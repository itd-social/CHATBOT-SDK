"""Packet-scoped read targets; independent chats never share an HTTP lock."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from .schemas import UpdateEvent


class _ReadTarget:
    def __init__(self, message_id: str) -> None:
        self.message_id = message_id
        self.lock = threading.Lock()
        self.done = False


class _BatchReads:
    def __init__(
        self,
        mark: Callable[[str, str], None],
        events: list[UpdateEvent],
        self_user_id: str,
    ) -> None:
        self._mark = mark
        targets: dict[str, str] = {}
        for event in events:
            chat_id = event.get("chat_id")
            if not isinstance(chat_id, str):
                continue
            messages: Any
            if event.get("type") == "new_message":
                messages = [event.get("message")]
            elif event.get("type") == "new_messages":
                messages = event.get("messages")
            else:
                continue
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                sender, message_id = message.get("sender_id"), message.get("message_id")
                if (
                    isinstance(sender, str)
                    and sender != self_user_id
                    and isinstance(message_id, str)
                    and message_id
                ):
                    targets[chat_id] = message_id
        self._targets = {
            chat: _ReadTarget(message) for chat, message in targets.items()
        }

    def __call__(self, chat_id: str, message_id: str) -> None:
        target = self._targets.get(chat_id)
        if target is None:
            self._mark(chat_id, message_id)
            return
        with target.lock:
            if not target.done:
                self._mark(chat_id, target.message_id)
                target.done = True
