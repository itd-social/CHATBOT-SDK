from __future__ import annotations

import enum
import random
import signal
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any

import httpx

from .errors import APIError, UploadQuotaError
from .schemas import UpdateEvent

TRANSIENT_STATUSES = frozenset({500, 502, 503, 504})
WAIT_SLICE = 0.5
_JITTER = 0.25
_MAX_EXPONENT = 32
_REDELIVER = "_itd_redeliver"


class ErrorAction(enum.Enum):
    """What ``Bot.poll`` does with an event whose handler raised."""

    SKIP = "skip"
    RETRY = "retry"
    STOP = "stop"


class _BatchHandler:
    """Internal adapter for dispatcher behavior scoped to one fetched packet."""

    def __init__(
        self,
        handler: Callable[[UpdateEvent], None],
        prepare: Callable[[list[UpdateEvent]], Callable[[UpdateEvent], None]],
    ) -> None:
        self._handler, self._prepare = handler, prepare

    def __call__(self, event: UpdateEvent) -> None:
        self._handler(event)

    def for_batch(self, events: list[UpdateEvent]) -> Callable[[UpdateEvent], None]:
        return self._prepare(events)


def is_transient(exc: BaseException) -> bool:
    """Network failures and 5xx answers that are worth repeating a read for."""
    if isinstance(exc, httpx.TransportError):
        # A wrong URL scheme is a setup mistake: repeating it only hides it.
        return not isinstance(exc, httpx.UnsupportedProtocol)
    return (
        isinstance(exc, APIError)
        and not isinstance(exc, UploadQuotaError)
        and exc.status in TRANSIENT_STATUSES
    )


def backoff_delay(attempt: int, initial: float, maximum: float) -> float:
    """Pause before retry number ``attempt`` (1, 2, ...); never above ``maximum``."""
    base = min(maximum, initial * 2 ** min(attempt - 1, _MAX_EXPONENT))
    return base * (1 - _JITTER * random.random())


def pause(stop: threading.Event | None, seconds: float) -> bool:
    """Wait ``seconds``; return True if ``stop`` was set meanwhile.

    The wait is cut into short slices: on Windows a long ``Event.wait`` does not
    react to Ctrl+C until it ends.
    """
    if stop is None:
        time.sleep(seconds)
        return False
    whole, rest = divmod(seconds, WAIT_SLICE)
    for _ in range(int(whole)):
        if stop.wait(WAIT_SLICE):
            return True
    if rest > 0:
        return stop.wait(rest)
    return stop.is_set()


def mark_redeliver(exc: BaseException) -> None:
    """Flag an error raised before a handler started: the batch is fetched again."""
    setattr(exc, _REDELIVER, True)


def is_redeliver(exc: BaseException) -> bool:
    return bool(getattr(exc, _REDELIVER, False))


def wait_for_wake(
    wake: threading.Event, stop: threading.Event | None, seconds: float
) -> None:
    """Wait for ``wake`` up to ``seconds``, noticing ``stop`` within half a second."""
    if stop is None:
        wake.wait(seconds)
        return
    deadline = time.monotonic() + seconds
    while not wake.is_set() and not stop.is_set():
        left = deadline - time.monotonic()
        if left <= 0:
            return
        wake.wait(min(left, WAIT_SLICE))


def error_status(exc: BaseException) -> str:
    return str(exc.status) if isinstance(exc, APIError) else "-"


@contextmanager
def stop_on_signals(stop: threading.Event) -> Iterator[None]:
    """Turn SIGINT/SIGTERM (and SIGBREAK where it exists) into ``stop.set()``.

    A second SIGINT raises ``KeyboardInterrupt``. Does nothing outside the main
    thread. Previous handlers are restored on exit.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    received = False

    def handle(signum: int, frame: object) -> None:
        nonlocal received
        if received and signum == signal.SIGINT:
            raise KeyboardInterrupt
        received = True
        stop.set()

    previous: dict[int, Any] = {}
    try:
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            signum = getattr(signal, name, None)
            if signum is not None:
                previous[signum] = signal.signal(signum, handle)
        yield
    finally:
        for signum, old in previous.items():
            if old is not None:
                signal.signal(signum, old)
