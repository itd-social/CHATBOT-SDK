"""Bounded per-chat work and contiguous event checkpoints for opt-in polling."""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from .errors import ProtocolError, RateLimitError
from .polling import backoff_delay, error_status, is_redeliver, is_transient, pause
from .schemas import UpdateEvent, UpdatesResponse

_log = logging.getLogger(__name__)
_WAIT = 0.05


@dataclass
class _Entry:
    cursor: int
    done: bool = False


class _ChatQueues:
    def __init__(
        self,
        handler: Callable[[UpdateEvent], None],
        checkpoint: Callable[[int], None] | None,
        cursor: int,
        workers: int,
        capacity: int,
    ) -> None:
        self.cursor = cursor
        self._handler, self._checkpoint = handler, checkpoint
        self._capacity = capacity
        self._total_capacity = max(2, workers) * capacity
        self._executor = ThreadPoolExecutor(workers, "itd-chat")
        self._condition = threading.Condition()
        self._ledger: deque[_Entry] = deque()
        self._jobs: dict[str | None, deque[tuple[_Entry, UpdateEvent]]] = {}
        self._counts: dict[str | None, int] = {}
        self._active: set[str | None] = set()
        self._failure: BaseException | None = None
        self._closed = False

    def _run(self, key: str | None) -> None:
        with self._condition:
            if self._closed or self._failure is not None:
                return
            entry, event = self._jobs[key].popleft()
        try:
            self._handler(event)
        except BaseException as exc:
            with self._condition:
                if self._failure is None:
                    self._failure = exc
        else:
            with self._condition:
                entry.done = True
        finally:
            with self._condition:
                self._counts[key] -= 1
                if not self._counts[key]:
                    del self._counts[key]
                if not self._closed and self._failure is None and self._jobs[key]:
                    try:
                        self._executor.submit(self._run, key)
                    except BaseException as exc:
                        self._failure = exc
                else:
                    self._active.discard(key)
                    if not self._jobs[key]:
                        del self._jobs[key]
                self._condition.notify_all()

    def checkpoint(self, *, raise_errors: bool = True) -> None:
        cursor = self.cursor
        with self._condition:
            while self._ledger and self._ledger[0].done:
                cursor = self._ledger.popleft().cursor
            failure = self._failure
            self._condition.notify_all()
        if cursor > self.cursor:
            if self._checkpoint is not None:
                self._checkpoint(cursor)
            self.cursor = cursor
        if raise_errors and failure is not None:
            raise failure

    def submit(self, batch: UpdatesResponse, since: int) -> None:
        events, next_seq = batch["events"], batch["next_event_seq"]
        previous = since
        for event in events:
            seq = event.get("event_seq")
            if (
                isinstance(seq, bool)
                or not isinstance(seq, int)
                or not previous < seq <= next_seq
            ):
                raise ProtocolError(
                    "Chat queues require increasing per-event event_seq"
                )
            previous = seq
        if next_seq < since:
            raise ProtocolError("Updates cursor moved backwards without a reset")
        for event in events:
            chat_id = event.get("chat_id")
            key = chat_id if isinstance(chat_id, str) else None
            self._enqueue(_Entry(event["event_seq"]), event, key)
        if next_seq > since:
            self._enqueue(_Entry(next_seq, done=True), None, None)

    def _enqueue(
        self, entry: _Entry, event: UpdateEvent | None, key: str | None
    ) -> None:
        while True:
            self.checkpoint()
            with self._condition:
                if self._failure is not None:
                    continue
                if len(self._ledger) < self._total_capacity and (
                    event is None or self._counts.get(key, 0) < self._capacity
                ):
                    self._ledger.append(entry)
                    if event is not None:
                        self._jobs.setdefault(key, deque()).append((entry, event))
                        self._counts[key] = self._counts.get(key, 0) + 1
                        if key not in self._active:
                            self._active.add(key)
                            self._executor.submit(self._run, key)
                    return
                self._condition.wait(_WAIT)

    def wait(
        self,
        seconds: float,
        stop: threading.Event | None,
        wake: threading.Event | None = None,
    ) -> None:
        deadline = time.monotonic() + seconds
        while True:
            self.checkpoint()
            if (stop is not None and stop.is_set()) or (
                wake is not None and wake.is_set()
            ):
                return
            left = deadline - time.monotonic()
            if left <= 0:
                return
            with self._condition:
                if self._failure is not None or (self._ledger and self._ledger[0].done):
                    continue
                self._condition.wait(min(left, _WAIT))

    def finish(self) -> None:
        while True:
            self.checkpoint()
            with self._condition:
                if not self._ledger:
                    return
                if not self._ledger[0].done:
                    self._condition.wait(_WAIT)

    def close(self, *, wait: bool) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._executor.shutdown(wait=wait, cancel_futures=True)


def _poll_chat_queues(
    fetch: Callable[[int], UpdatesResponse],
    handler: Callable[[UpdateEvent], None],
    since: int,
    *,
    checkpoint: Callable[[int], None] | None,
    workers: int,
    capacity: int,
    interval: float,
    min_fetch_interval: float,
    websocket: bool,
    wake: threading.Event,
    stop: threading.Event | None,
    retry_initial: float,
    retry_max: float,
) -> None:
    queues = _ChatQueues(handler, checkpoint, since, workers, capacity)
    fetch_failures = read_failures = 0
    completed = since
    last_fetch: float | None = None
    clean = False
    try:
        while True:
            try:
                queues.checkpoint()
                if queues.cursor > completed:
                    completed = queues.cursor
                    read_failures = 0
                if stop is not None and stop.is_set():
                    queues.finish()
                    clean = True
                    return
                if websocket and last_fetch is not None:
                    queues.wait(
                        last_fetch + min_fetch_interval - time.monotonic(), stop
                    )
                    if stop is not None and stop.is_set():
                        continue
                wake.clear()
                last_fetch = time.monotonic()
                try:
                    batch = fetch(since)
                except RateLimitError as exc:
                    queues.wait(min(exc.retry_after_seconds, 60), stop)
                    continue
                except Exception as exc:
                    if not is_transient(exc):
                        raise
                    fetch_failures += 1
                    delay = backoff_delay(fetch_failures, retry_initial, retry_max)
                    _log.warning(
                        "Updates request failed (%s, status %s); retry %d in %.1f s",
                        type(exc).__name__,
                        error_status(exc),
                        fetch_failures,
                        delay,
                    )
                    queues.wait(delay, stop)
                    continue
                fetch_failures = 0
                queues.submit(batch, since)
                queues.checkpoint()
                advanced = batch["next_event_seq"] > since
                since = batch["next_event_seq"]
                if not batch["events"] or not advanced:
                    queues.wait(interval, stop, wake if websocket else None)
            except Exception as exc:
                if not is_redeliver(exc):
                    raise
                queues.close(wait=True)
                queues.checkpoint(raise_errors=False)
                since = queues.cursor
                if since > completed:
                    completed = since
                    read_failures = 0
                read_failures += 1
                delay = backoff_delay(read_failures, retry_initial, retry_max)
                _log.warning(
                    "Read mark failed (%s, status %s); fetching from checkpoint, "
                    "retry %d in %.1f s",
                    type(exc).__name__,
                    error_status(exc),
                    read_failures,
                    delay,
                )
                queues = _ChatQueues(handler, checkpoint, since, workers, capacity)
                if pause(stop, delay):
                    clean = True
                    return
    finally:
        queues.close(wait=clean)
