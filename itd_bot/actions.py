"""Chat action statuses (typing, upload progress, ...) and the repeating handle."""

from __future__ import annotations

import math
import numbers
import threading
from collections.abc import Callable
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import Bot


class ChatAction(str, Enum):
    """Chat action statuses that can be sent with ``Bot.send_action``.

    Members compare equal to their plain string values. ``RECORD_VIDEO`` is
    only displayed by the ITD client, which never sends it itself; the SDK does
    not forbid sending it and the server decides the result.
    """

    TYPING = "typing"
    RECORD_VOICE = "record_voice"
    RECORD_VIDEO = "record_video"
    UPLOAD_PHOTO = "upload_photo"
    UPLOAD_VIDEO = "upload_video"
    UPLOAD_VOICE = "upload_voice"
    UPLOAD_DOCUMENT = "upload_document"


ProgressValue = float | int | None
ProgressSource = ProgressValue | Callable[[], ProgressValue]


def _action_name(action: Any) -> str:
    if isinstance(action, ChatAction):
        return action.value
    if not isinstance(action, str):
        raise ValueError("action must be a ChatAction or a non-empty string")
    if not action:
        raise ValueError("action must not be empty")
    return str(action)


def _is_upload(action_name: str) -> bool:
    return action_name.startswith("upload_")


def _check_progress(value: Any) -> ProgressValue:
    """Return ``value`` if it is None or a finite number in 0..1, else raise."""
    if value is None:
        return None
    if (
        isinstance(value, bool)
        or not isinstance(value, numbers.Real)
        or not math.isfinite(value)
        or not 0 <= value <= 1
    ):
        raise ValueError("progress must be a number from 0 to 1 inclusive")
    return value if type(value) in (int, float) else float(value)


def _action_body(action: Any, progress: Any) -> dict[str, Any]:
    """Validate and build the request body before anything is sent."""
    name = _action_name(action)
    progress = _check_progress(progress)
    body: dict[str, Any] = {"type": name}
    if progress is not None:
        if not _is_upload(name):
            raise ValueError("progress is only allowed for upload_* actions")
        body["progress"] = progress
    return body


class ChatActionHandle:
    """Context manager repeating a chat action until the ``with`` block ends.

    Returned by ``Bot.chat_action``. The first request is sent synchronously in
    ``__enter__`` and raises as usual; afterwards a daemon thread repeats it
    every ``interval`` seconds with the current :attr:`progress`. The first
    failure of a background request (rate limit, 401, network, invalid
    progress) stops the loop and is kept in :attr:`error`; nothing is retried.
    Leaving the ``with`` block stops and joins the thread without waiting for
    the interval and never replaces an exception raised by the body.
    """

    def __init__(
        self,
        bot: Bot,
        chat_id: str,
        action: ChatAction | str = ChatAction.TYPING,
        *,
        interval: float = 3.0,
        progress: ProgressSource = None,
    ) -> None:
        self._name = _action_name(action)
        if (
            isinstance(interval, bool)
            or not isinstance(interval, numbers.Real)
            or not math.isfinite(interval)
            or interval < 1.0
        ):
            raise ValueError("interval must be a number of at least 1.0 seconds")
        self._bot = bot
        self._chat_id = chat_id
        self._interval = float(interval)
        self._lock = threading.Lock()
        self._source: ProgressSource = None
        self._last_progress: ProgressValue = None
        self._error: BaseException | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._used = False
        self._wait: Callable[[threading.Event, float], bool] = lambda ev, t: ev.wait(t)
        self.progress = progress  # type: ignore[assignment]

    @property
    def progress(self) -> ProgressValue:
        """Last progress value (number or None); writable, thread-safe.

        Assigning a number or None replaces the source; assigning a callable
        without arguments makes it evaluated on every tick.
        """
        with self._lock:
            if callable(self._source):
                return self._last_progress
            return self._source

    @progress.setter
    def progress(self, value: ProgressSource) -> None:
        if callable(value):
            source: ProgressSource = value
        else:
            source = _check_progress(value)
            if source is not None and not _is_upload(self._name):
                raise ValueError("progress is only allowed for upload_* actions")
        with self._lock:
            self._source = source
            self._last_progress = None if callable(source) else source

    @property
    def error(self) -> BaseException | None:
        """The last error of the background thread, or None."""
        with self._lock:
            return self._error

    def _current_body(self) -> dict[str, Any]:
        with self._lock:
            source = self._source
        value = source() if callable(source) else source
        if callable(source):
            value = _check_progress(value)
            with self._lock:
                if self._source is source:
                    self._last_progress = value
        return _action_body(self._name, value)

    def _send(self) -> None:
        body = self._current_body()
        self._bot.send_action(
            self._chat_id, body["type"], progress=body.get("progress")
        )

    def _run(self) -> None:
        while not self._wait(self._stop, self._interval):
            try:
                self._send()
            except Exception as exc:
                with self._lock:
                    self._error = exc
                return

    def __enter__(self) -> ChatActionHandle:
        if self._used:
            raise RuntimeError("chat_action context manager cannot be reused")
        self._used = True
        self._send()
        self._thread = threading.Thread(
            target=self._run, name="itd-chat-action", daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join()
        return None
