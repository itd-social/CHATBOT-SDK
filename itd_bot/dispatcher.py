from __future__ import annotations

import re
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from .client import Bot

_COMMAND = re.compile(r"^/([A-Za-z0-9_]{1,32})(?:@([A-Za-z0-9_]{1,64}))?(?=\s|$)")
_READ_MODES = frozenset({"background", "blocking", "off"})
_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CommandContext:
    bot: Bot
    event: dict[str, Any]
    message: dict[str, Any]
    command: str
    args: str

    @property
    def chat_id(self) -> str:
        return self.event["chat_id"]

    def reply(self, text: str, **kwargs: Any) -> Any:
        return self.bot.send_message(
            self.chat_id, text, reply_to_id=self.message["message_id"], **kwargs
        )

    def reply_formatted(self, content: Any, **kwargs: Any) -> Any:
        return self.bot.send_formatted(
            self.chat_id, content, reply_to_id=self.message["message_id"], **kwargs
        )


class Dispatcher:
    """Local command router. ITD does not expose a confirmed server-side command API.

    ``read_mode`` controls read marks: ``"background"`` (default) queues them in
    one background thread, coalesced per chat, so the reply is not delayed;
    ``"blocking"`` marks each message before its handler and skips the handler
    if that fails; ``"off"`` sends no read marks. ``typing_delay`` is how long a
    handler may run before ``typing`` is shown; ``0`` sends it before the
    handler starts. Call ``close()`` (or use the dispatcher as a context
    manager) to flush queued read marks when handling events manually.
    """

    def __init__(
        self,
        bot: Bot,
        *,
        self_user_id: str,
        username: str | None = None,
        auto_typing: bool = True,
        typing_interval: float = 4.0,
        typing_delay: float = 0.5,
        read_mode: str = "background",
    ):
        if not self_user_id:
            raise ValueError("self_user_id is required to prevent self-replies")
        if typing_interval <= 0:
            raise ValueError("typing_interval must be positive")
        if typing_delay < 0:
            raise ValueError("typing_delay must not be negative")
        if read_mode not in _READ_MODES:
            raise ValueError("read_mode must be 'background', 'blocking' or 'off'")
        self.bot, self.self_user_id = bot, self_user_id
        self.auto_typing, self.typing_interval = auto_typing, typing_interval
        self.typing_delay, self.read_mode = typing_delay, read_mode
        self.username = username.removeprefix("@").lower() if username else None
        self._commands: dict[str, Callable[[CommandContext], Any]] = {}
        self._fallback: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None
        self._event_handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}
        self._typing_lock = threading.Lock()
        self._typing_last: dict[str, float] = {}
        self._read_lock = threading.Lock()
        self._pending_reads: dict[str, str] = {}
        self._read_executor: ThreadPoolExecutor | None = None

    def __enter__(self) -> Dispatcher:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        """Wait for queued read marks and stop the background read thread."""
        with self._read_lock:
            executor, self._read_executor = self._read_executor, None
        if executor is not None:
            executor.shutdown(wait=True)

    def _mark_read(self, chat_id: str, message_id: str) -> None:
        if self.read_mode == "off":
            return
        if self.read_mode == "blocking":
            self.bot.mark_read(chat_id, message_id)
            return
        with self._read_lock:
            already_queued = chat_id in self._pending_reads
            self._pending_reads[chat_id] = message_id
            if already_queued:
                return
            if self._read_executor is None:
                self._read_executor = ThreadPoolExecutor(1, "itd-read")
            self._read_executor.submit(self._flush_read, chat_id)

    def _flush_read(self, chat_id: str) -> None:
        with self._read_lock:
            message_id = self._pending_reads.pop(chat_id, None)
        if message_id is None:
            return
        try:
            self.bot.mark_read(chat_id, message_id)
        except Exception as exc:
            _log.warning("Could not mark message as read: %s", type(exc).__name__)

    def _send_typing(self, chat_id: str, stopped: threading.Event) -> float:
        """Send typing unless throttled; return the delay until the next attempt."""
        with self._typing_lock:
            if stopped.is_set():
                return self.typing_interval
            now = time.monotonic()
            previous = self._typing_last.get(chat_id)
            if previous is not None and now - previous < self.typing_interval:
                return self.typing_interval - (now - previous)
            if len(self._typing_last) >= 4096:
                self._typing_last = {
                    k: v
                    for k, v in self._typing_last.items()
                    if now - v < self.typing_interval
                }
                if len(self._typing_last) >= 4096 and chat_id not in self._typing_last:
                    self._typing_last.pop(next(iter(self._typing_last)))
            self._typing_last[chat_id] = now
        try:
            self.bot.send_action(chat_id, "typing")
        except Exception as exc:
            _log.warning("Could not send typing action: %s", type(exc).__name__)
        return self.typing_interval

    @contextmanager
    def _typing(self, chat_id: str):
        if not self.auto_typing:
            yield
            return
        stopped = threading.Event()
        if self.typing_delay == 0:
            delay = self._send_typing(chat_id, stopped)
        else:
            delay = self.typing_delay

        def refresh():
            nonlocal delay
            while not stopped.wait(delay):
                delay = self._send_typing(chat_id, stopped)

        thread = threading.Thread(target=refresh, name="itd-typing", daemon=True)
        thread.start()
        try:
            yield
        finally:
            stopped.set()

    def on_event(self, event_type: str) -> Callable:
        """Register a callback for a non-message event, such as message_reactions."""
        if not event_type or event_type in {"new_message", "new_messages"}:
            raise ValueError("use command/on_message for incoming messages")

        def register(handler: Callable[[dict[str, Any]], Any]) -> Callable:
            if event_type in self._event_handlers:
                raise ValueError("event handler already registered")
            self._event_handlers[event_type] = handler
            return handler

        return register

    def command(self, name: str) -> Callable:
        name = name.removeprefix("/").lower()
        if not re.fullmatch(r"[a-z0-9_]{1,32}", name):
            raise ValueError("invalid command name")

        def register(handler: Callable[[CommandContext], Any]) -> Callable:
            if name in self._commands:
                raise ValueError("command already registered")
            self._commands[name] = handler
            return handler

        return register

    def on_message(
        self, handler: Callable[[dict[str, Any], dict[str, Any]], Any]
    ) -> Callable:
        self._fallback = handler
        return handler

    def handle(self, event: dict[str, Any]) -> None:
        typ = event.get("type")
        if typ == "new_message":
            messages = [event.get("message")]
        elif typ == "new_messages":
            messages = event.get("messages", [])
        else:
            handler = self._event_handlers.get(typ)
            if handler:
                chat_id = event.get("chat_id")
                if isinstance(chat_id, str) and chat_id:
                    with self._typing(chat_id):
                        handler(event)
                else:
                    handler(event)
            return
        if not isinstance(messages, list) or not isinstance(event.get("chat_id"), str):
            raise ValueError("invalid message event")
        for message in messages:
            if not isinstance(message, dict) or not isinstance(
                message.get("sender_id"), str
            ):
                raise ValueError("invalid message")
            if message["sender_id"] == self.self_user_id:
                continue
            message_id = message.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                raise ValueError("incoming message has no message_id")

            self._mark_read(event["chat_id"], message_id)
            text = message.get("text")
            match = _COMMAND.match(text) if isinstance(text, str) else None
            if match:
                target = match.group(2)
                if target and (not self.username or target.lower() != self.username):
                    continue
                name = match.group(1).lower()
                handler = self._commands.get(name)
                if handler:
                    with self._typing(event["chat_id"]):
                        handler(
                            CommandContext(
                                self.bot,
                                event,
                                message,
                                name,
                                text[match.end() :].strip(),
                            )
                        )
                    continue
            if self._fallback:
                with self._typing(event["chat_id"]):
                    self._fallback(event, message)

    def run_polling(self, since: int, **kwargs: Any) -> None:
        """Run ``Bot.poll`` with this dispatcher; kwargs are passed through."""
        try:
            self.bot.poll(since, self.handle, **kwargs)
        finally:
            self.close()
