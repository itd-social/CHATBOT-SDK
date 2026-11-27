"""Internal update validation, polling and handler scheduling."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, wait
from copy import deepcopy
from typing import TYPE_CHECKING, Any, cast

from .chat_queues import _poll_chat_queues
from .errors import ProtocolError, RateLimitError, ResetRequired, _InvalidJSON
from .polling import (
    ErrorAction,
    _BatchHandler,
    backoff_delay,
    error_status,
    is_redeliver,
    is_transient,
    pause,
    wait_for_wake,
)
from .schemas import UpdateEvent, UpdatesResponse

if TYPE_CHECKING:
    from .client import Bot
_log = logging.getLogger("itd_bot.client")


def get_updates(self: Bot, since: int, *, limit: int | None = None) -> UpdatesResponse:
    if isinstance(since, bool) or not isinstance(since, int) or since < 0:
        raise ValueError("since must be a non-negative integer")
    params = {"since": since, "capabilities": "chat_settings", "manual_unread": 1}
    if limit is not None:
        params["limit"] = limit
    try:
        data = self.request("GET", "/updates", params=params)
    except _InvalidJSON as exc:
        if self._on_protocol_error == "raise":
            raise
        self._remember_protocol_error(exc.raw)
        return self._empty_updates(since)
    if isinstance(data, dict) and data.get("reset_required") is True:
        raise ResetRequired(data)
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("events"), list)
        or not isinstance(data.get("reset_required"), bool)
        or isinstance(data.get("next_event_seq"), bool)
        or not isinstance(data.get("next_event_seq"), int)
        or data["next_event_seq"] < 0
    ):
        if self._on_protocol_error == "skip":
            self._remember_protocol_error(data)
            return self._empty_updates(since)
        raise ProtocolError("Invalid updates response")
    if data["next_event_seq"] < since:
        if self._on_protocol_error == "skip":
            self._remember_protocol_error(data)
            return self._empty_updates(since)
        raise ProtocolError("Updates cursor moved backwards without a reset")
    events = []
    for event in data["events"]:
        if isinstance(event, dict) and isinstance(event.get("type"), str):
            events.append(event)
        elif self._on_protocol_error == "skip":
            self._remember_protocol_error(event)
        else:
            raise ProtocolError("Invalid updates response")
    if len(events) != len(data["events"]):
        data = {**data, "events": events}
    return cast(UpdatesResponse, data)


def _empty_updates(since: int) -> UpdatesResponse:
    return {"events": [], "reset_required": False, "next_event_seq": since}


def _remember_protocol_error(self: Bot, fragment: Any) -> None:
    snapshot = deepcopy(fragment)
    with self._protocol_lock:
        self._protocol_errors.append(snapshot)
    _log.warning("Skipped invalid updates fragment; inspect Bot.protocol_errors")


def protocol_errors(self: Bot) -> tuple[Any, ...]:
    """Copies of the last 32 skipped raw fragments; may contain private data."""
    with self._protocol_lock:
        return deepcopy(tuple(self._protocol_errors))


def poll(
    self: Bot,
    since: int,
    handler: Callable[[UpdateEvent], None],
    *,
    interval: float = 2.0,
    on_checkpoint: Callable[[int], None] | None = None,
    websocket: bool = False,
    workers: int = 1,
    min_fetch_interval: float = 0.2,
    retry_initial_delay: float = 1.0,
    retry_max_delay: float = 60.0,
    on_error: Callable[[Exception, UpdateEvent], ErrorAction | None] | None = None,
    handler_retries: int = 3,
    stop: threading.Event | None = None,
    chat_queues: bool = False,
    chat_queue_size: int = 64,
    pause_wait: Callable[[threading.Event | None, float], bool],
    monotonic: Callable[[], float],
) -> None:
    """Process batches; checkpoint after every batch whose handlers finished.

    The next batch is requested immediately only when the previous one had
    events and moved the cursor forward; otherwise the loop pauses for
    ``interval``. With ``websocket=True`` a background WebSocket connection
    wakes the loop as soon as the server pushes a frame, while events are
    still read from ``/updates`` with the same cursor; requests for updates
    then start at least ``min_fetch_interval`` seconds apart. ``workers > 1``
    runs handlers of different chats in parallel threads, keeping order
    inside each chat.

    ``chat_queues=True`` reads ahead through bounded per-chat queues and
    checkpoints the contiguous completed prefix, using each event's
    ``event_seq``. ``chat_queue_size`` includes the running event. The
    default remains the batch barrier; see docs/CHAT_QUEUES.md.

    A network failure or HTTP 500/502/503/504 from ``/updates`` is repeated
    with the same cursor after a pause that doubles from
    ``retry_initial_delay`` up to ``retry_max_delay``. Other errors,
    including ``ResetRequired``, are raised.

    When a handler raises ``Exception``, ``on_error(exc, event)`` may return
    an ``ErrorAction``. Without a hook, or when it returns ``None``, the event
    is skipped and logged. ``STOP`` raises the error and leaves the batch
    without a checkpoint. ``RETRY`` runs the handler again up to
    ``handler_retries`` times, then skips; it can duplicate side effects. An
    error raised by the hook itself propagates. The hook may be called from
    worker threads.

    Set ``stop`` to finish the current batch, checkpoint and return.
    """
    if interval <= 0:
        raise ValueError("interval must be positive")
    if not isinstance(chat_queues, bool):
        raise ValueError("chat_queues must be a boolean")
    if (
        isinstance(chat_queue_size, bool)
        or not isinstance(chat_queue_size, int)
        or chat_queue_size < 1
    ):
        raise ValueError("chat_queue_size must be a positive integer")
    if min_fetch_interval < 0:
        raise ValueError("min_fetch_interval must not be negative")
    if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
        raise ValueError("workers must be a positive integer")
    if retry_initial_delay <= 0:
        raise ValueError("retry_initial_delay must be positive")
    if retry_max_delay < retry_initial_delay:
        raise ValueError("retry_max_delay must not be below retry_initial_delay")
    if (
        isinstance(handler_retries, bool)
        or not isinstance(handler_retries, int)
        or handler_retries < 0
    ):
        raise ValueError("handler_retries must be a non-negative integer")
    if websocket:
        self._require_websocket()
    run = _EventRunner(
        handler,
        on_error,
        handler_retries,
        retry_initial_delay,
        retry_max_delay,
        stop,
        pause_wait,
    )
    wake = threading.Event()
    ws_stop = threading.Event()
    ws_thread = None
    if websocket:
        ws_thread = threading.Thread(
            target=self._websocket_wakeups,
            args=(wake, ws_stop),
            name="itd-ws-wakeup",
            daemon=True,
        )
        ws_thread.start()
    executor = (
        ThreadPoolExecutor(workers, "itd-handler")
        if workers > 1 and not chat_queues
        else None
    )
    last_fetch = None
    failures = 0
    clean = False
    try:
        if chat_queues:
            _poll_chat_queues(
                self.get_updates,
                run,
                since,
                checkpoint=on_checkpoint,
                workers=workers,
                capacity=chat_queue_size,
                interval=interval,
                min_fetch_interval=min_fetch_interval,
                websocket=websocket,
                wake=wake,
                stop=stop,
                retry_initial=retry_initial_delay,
                retry_max=retry_max_delay,
                prepare=run.for_batch,
            )
            clean = True
            return
        while True:
            if stop is not None and stop.is_set():
                clean = True
                return
            if websocket and last_fetch is not None:
                gap = last_fetch + min_fetch_interval - monotonic()
                if gap > 0 and pause_wait(stop, gap):
                    clean = True
                    return
            wake.clear()
            last_fetch = monotonic()
            try:
                batch = self.get_updates(since)
            except RateLimitError as exc:
                if pause_wait(stop, min(exc.retry_after_seconds, 60)):
                    clean = True
                    return
                continue
            except Exception as exc:
                if not is_transient(exc):
                    raise
                failures += 1
                delay = backoff_delay(failures, retry_initial_delay, retry_max_delay)
                _log.warning(
                    "Updates request failed (%s, status %s); retry %d in %.1f s",
                    type(exc).__name__,
                    error_status(exc),
                    failures,
                    delay,
                )
                if pause_wait(stop, delay):
                    clean = True
                    return
                continue
            events = batch["events"]
            batch_run = run.for_batch(events)
            try:
                if executor is None:
                    for event in events:
                        batch_run(event)
                else:
                    _run_by_chat(executor, events, batch_run)
            except Exception as exc:
                if not is_redeliver(exc):
                    raise
                # Failed before a handler started (read mark): ask for the
                # same batch again instead of losing the event.
                failures += 1
                delay = backoff_delay(failures, retry_initial_delay, retry_max_delay)
                _log.warning(
                    "Read mark failed (%s, status %s); fetching the batch "
                    "again, retry %d in %.1f s",
                    type(exc).__name__,
                    error_status(exc),
                    failures,
                    delay,
                )
                if pause_wait(stop, delay):
                    clean = True
                    return
                continue
            failures = 0
            next_seq = batch["next_event_seq"]
            if on_checkpoint is not None:
                on_checkpoint(next_seq)
            advanced = next_seq > since
            since = next_seq
            if events and advanced:
                continue
            if websocket:
                wait_for_wake(wake, stop, interval)
            elif pause_wait(stop, interval):
                clean = True
                return
    finally:
        ws_stop.set()
        if executor is not None:
            executor.shutdown(wait=clean, cancel_futures=not clean)
        if clean and ws_thread is not None:
            ws_thread.join(timeout=5.0)


class _EventRunner:
    """Apply the handler-error policy to one event at a time."""

    def __init__(
        self,
        handler: Callable[[UpdateEvent], None],
        on_error: Callable[[Exception, UpdateEvent], ErrorAction | None] | None,
        retries: int,
        initial: float,
        maximum: float,
        stop: threading.Event | None,
        pause_wait: Callable[[threading.Event | None, float], bool] = pause,
    ) -> None:
        self._handler, self._on_error, self._retries = handler, on_error, retries
        self._pause = pause_wait
        self._initial, self._maximum, self._stop = initial, maximum, stop

    def for_batch(self, events: list[UpdateEvent]) -> _EventRunner:
        if not isinstance(self._handler, _BatchHandler):
            return self
        return _EventRunner(
            self._handler.for_batch(events),
            self._on_error,
            self._retries,
            self._initial,
            self._maximum,
            self._stop,
            self._pause,
        )

    def __call__(self, event: UpdateEvent) -> None:
        attempt = 0
        while True:
            try:
                self._handler(event)
                return
            except Exception as exc:
                if is_redeliver(exc):
                    raise
                action = self._decide(exc, event)
                if action is ErrorAction.STOP:
                    raise
                if action is ErrorAction.RETRY and attempt < self._retries:
                    attempt += 1
                    delay = backoff_delay(attempt, self._initial, self._maximum)
                    _log.warning(
                        "Handler failed (%s) for event type %s; retry %d of %d in %.1f s",
                        type(exc).__name__,
                        _event_type(event),
                        attempt,
                        self._retries,
                        delay,
                    )
                    # A stop request only shortens the pause; the retry still runs.
                    self._pause(self._stop, delay)
                    continue
                _log.error(
                    "Handler failed (%s) for event type %s; event skipped",
                    type(exc).__name__,
                    _event_type(event),
                )
                return

    def _decide(self, exc: Exception, event: UpdateEvent) -> ErrorAction | None:
        if self._on_error is None:
            return None
        action = self._on_error(exc, event)
        if action is not None and not isinstance(action, ErrorAction):
            raise TypeError("on_error must return an ErrorAction or None")
        return action


def _event_type(event: UpdateEvent) -> str:
    kind = event.get("type")
    return kind[:64] if isinstance(kind, str) else "?"


def _run_by_chat(
    executor: ThreadPoolExecutor,
    events: list[UpdateEvent],
    handler: Callable[[UpdateEvent], None],
) -> None:
    """Run handlers in parallel across chats, in order within each chat.

    If one handler raises, handlers that already started finish, no new event
    starts, and the first error is raised.
    """
    groups: dict[str | None, list[UpdateEvent]] = {}
    for event in events:
        chat_id = event.get("chat_id")
        key = chat_id if isinstance(chat_id, str) else None
        groups.setdefault(key, []).append(event)
    if len(groups) <= 1:
        for event in events:
            handler(event)
        return
    failed = threading.Event()

    def run(group: list[UpdateEvent]) -> None:
        for event in group:
            if failed.is_set():
                return
            try:
                handler(event)
            except BaseException:
                failed.set()
                raise

    futures = [executor.submit(run, group) for group in groups.values()]
    wait(futures)
    for future in futures:
        future.result()
