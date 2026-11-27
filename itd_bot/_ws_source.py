"""Bounded WebSocket frames with HTTP bootstrap, gap recovery and replay."""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from contextlib import closing
from typing import TYPE_CHECKING, cast

from ._websocket import websocket_events
from .errors import ProtocolError
from .polling import pause
from .schemas import UpdateEvent, UpdatesResponse

if TYPE_CHECKING:
    from .client import Bot

_CONNECTED = object()
_DISCONNECTED = object()
_log = logging.getLogger(__name__)
_MAX_INTEGER = (1 << 53) - 1


def _sequence(frame: UpdateEvent) -> tuple[int | None, int]:
    for name in ("event_seq", "event_seq_count"):
        value = frame.get(name)
        if value is not None and (
            isinstance(value, bool)
            or not isinstance(value, int)
            or not 0 <= value <= _MAX_INTEGER
        ):
            raise ProtocolError("Invalid WebSocket sequence")
    seq = frame.get("event_seq")
    count = frame.get("event_seq_count")
    return seq, 1 if count is None else count


class WebSocketUpdates:
    """Only the polling thread owns cursor/replay state; the reader enqueues.

    Unacknowledged durable frames are retained until checkpoint succeeds, so
    read-mark failures can recover after queues rewind. Unsequenced signals
    have no durable replay guarantee and never advance the cursor.
    """

    def __init__(
        self,
        bot: Bot,
        wake: threading.Event,
        stop: threading.Event | None,
        *,
        capacity: int,
        min_http_interval: float,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._bot, self._wake, self._stop = bot, wake, stop
        self._closing = threading.Event()
        self._buffer: queue.Queue[object] = queue.Queue(capacity)
        self._pending: UpdateEvent | None = None
        self._replay: deque[UpdateEvent] = deque()
        self._history: dict[int, UpdateEvent] = {}
        self._position: int | None = None
        self._connected = False
        self._need_sync = True
        self._http_at: float | None = None
        self._minimum, self._monotonic = min_http_interval, monotonic
        self._thread = threading.Thread(
            target=self._read, name="itd-ws-source", daemon=True
        )
        if stop is not None and stop.is_set():
            self._closing.set()
        self._thread.start()

    def _put(self, value: object) -> bool:
        while not self._closing.is_set():
            try:
                self._buffer.put(value, timeout=0.05)
            except queue.Full:
                continue
            self._wake.set()
            return True
        return False

    def _read(self) -> None:
        backoff = 1.0
        while not self._closing.is_set():
            try:
                with closing(
                    websocket_events(
                        self._bot,
                        stop=self._closing,
                        pause_wait=pause,
                        on_connect=lambda: self._put(_CONNECTED),
                    )
                ) as stream:
                    for frame in stream:
                        if not self._put(frame):
                            return
                        backoff = 1.0
            except Exception as exc:
                _log.warning(
                    "WebSocket source disconnected (%s); recovering with HTTP",
                    type(exc).__name__,
                )
            if not self._put(_DISCONNECTED) or self._closing.wait(backoff):
                return
            backoff = min(30.0, backoff * 2)

    def _http(self, since: int) -> UpdatesResponse:
        if self._http_at is not None:
            delay = self._http_at + self._minimum - self._monotonic()
            if delay > 0 and pause(self._stop, delay):
                return {"events": [], "reset_required": False, "next_event_seq": since}
        self._http_at = self._monotonic()
        result = self._bot.get_updates(since)
        self._need_sync = False
        return result

    def _result(self, result: UpdatesResponse) -> UpdatesResponse:
        self._position = result["next_event_seq"]
        if self._replay or self._pending is not None or not self._buffer.empty():
            self._wake.set()
        return result

    def fetch(self, since: int) -> UpdatesResponse:
        if self._stop is not None and self._stop.is_set():
            return self._result(
                {"events": [], "reset_required": False, "next_event_seq": since}
            )
        if self._position is not None and since < self._position:
            self._replay = deque(
                value for seq, value in sorted(self._history.items()) if seq > since
            )
            self._need_sync = True
        if self._need_sync:
            return self._result(self._http(since))
        while True:
            if self._stop is not None and self._stop.is_set():
                return self._result(
                    {"events": [], "reset_required": False, "next_event_seq": since}
                )
            if self._replay:
                item: object = self._replay.popleft()
            elif self._pending is not None:
                item, self._pending = self._pending, None
            else:
                try:
                    item = self._buffer.get_nowait()
                except queue.Empty:
                    if not self._connected:
                        return self._result(self._http(since))
                    return self._result(
                        {"events": [], "reset_required": False, "next_event_seq": since}
                    )
            if item is _CONNECTED or item is _DISCONNECTED:
                self._connected = item is _CONNECTED
                self._need_sync = True
                return self._result(self._http(since))
            frame = cast(UpdateEvent, item)
            try:
                seq, count = _sequence(frame)
            except ProtocolError:
                _log.warning("Invalid WebSocket sequence; recovering with HTTP")
                self._need_sync = True
                return self._result(self._http(since))
            if seq is not None:
                if seq <= since:
                    continue
                if since + count != seq:
                    self._pending = frame
                    self._need_sync = True
                    return self._result(self._http(since))
                self._history[seq] = frame
            elif frame.get("type") in {"new_message", "new_messages"}:
                self._need_sync = True
                return self._result(self._http(since))
            return self._result(
                {
                    "events": [frame],
                    "reset_required": False,
                    "next_event_seq": since if seq is None else seq,
                }
            )

    def acknowledge(self, cursor: int) -> None:
        for seq in list(self._history):
            if seq <= cursor:
                del self._history[seq]

    def close(self) -> None:
        self._closing.set()
        self._wake.set()
        self._thread.join()
