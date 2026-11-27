"""Bounded message deduplication with an atomic storage contract."""

from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Protocol

DedupKey = tuple[str, str, str, str]


class DedupStore(Protocol):
    """Claim, complete or release a (server, account, chat, message) key.

    ``claim`` returns True only to the owner that may handle the message.
    Concurrent claims of an unfinished key wait for completion or release;
    completed keys return False. Implementations must make these operations
    thread-safe and bound the number of completed keys they retain.
    """

    def claim(self, key: DedupKey) -> bool: ...

    def complete(self, key: DedupKey) -> None: ...

    def release(self, key: DedupKey) -> None: ...


class MemoryDedupStore:
    """Remember successful messages in a thread-safe LRU cache.

    In-flight claims are separate from the bounded completed cache. A failed
    handler releases its claim, so a later attempt can process the message.
    Nothing survives a process restart.
    """

    def __init__(self, capacity: int = 4096) -> None:
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("dedup_capacity must be a positive integer")
        self._capacity = capacity
        self._completed: OrderedDict[DedupKey, None] = OrderedDict()
        self._inflight: dict[DedupKey, int] = {}
        self._condition = threading.Condition()

    def claim(self, key: DedupKey) -> bool:
        owner = threading.get_ident()
        with self._condition:
            while key in self._inflight:
                # A handler may pass its own event back to the dispatcher.
                if self._inflight[key] == owner:
                    return False
                self._condition.wait()
            if key in self._completed:
                self._completed.move_to_end(key)
                return False
            self._inflight[key] = owner
            return True

    def complete(self, key: DedupKey) -> None:
        with self._condition:
            self._inflight.pop(key, None)
            self._completed[key] = None
            self._completed.move_to_end(key)
            while len(self._completed) > self._capacity:
                self._completed.popitem(last=False)
            self._condition.notify_all()

    def release(self, key: DedupKey) -> None:
        with self._condition:
            self._inflight.pop(key, None)
            self._condition.notify_all()
