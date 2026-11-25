import json
import logging
import signal
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from itd_bot import (
    APIError,
    Bot,
    Dispatcher,
    ErrorAction,
    ProtocolError,
    RateLimitError,
    ResetRequired,
    SessionStorageError,
    UploadQuotaError,
    polling,
)
from itd_bot.client import _run_by_chat


class Stop(Exception):
    pass


class HookBoom(Exception):
    pass


class RecordingStop(threading.Event):
    """A stop event whose waits are recorded and end the wait at once."""

    def __init__(self, stop_on_wait=True):
        super().__init__()
        self.waits = []
        self._stop_on_wait = stop_on_wait

    def wait(self, timeout=None):
        self.waits.append(timeout)
        if self._stop_on_wait:
            self.set()
        return self.is_set()


@pytest.fixture(autouse=True)
def fixed_jitter(monkeypatch):
    monkeypatch.setattr("itd_bot.polling.random.random", lambda: 0.0)


@pytest.fixture
def sleeps(monkeypatch):
    calls = []
    monkeypatch.setattr("itd_bot.polling.time.sleep", calls.append)
    return calls


def batch(events, seq):
    return {"events": events, "reset_required": False, "next_event_seq": seq}


def feed(monkeypatch, bot, script):
    """Make get_updates replay script (batches or exceptions), then raise Stop."""
    cursors, items = [], list(script)

    def updates(cursor):
        cursors.append(cursor)
        if not items:
            raise Stop
        item = items.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    monkeypatch.setattr(bot, "get_updates", updates)
    return cursors


def new_message(chat="a", n=0, text="private-text"):
    return {
        "type": "new_message",
        "chat_id": chat,
        "n": n,
        "message": {"sender_id": "u", "message_id": str(n), "text": text},
    }


# --- network failures while polling ---------------------------------------


def test_backoff_doubles_resets_after_success_and_keeps_cursor(monkeypatch, sleeps):
    bot = Bot("fake")
    cursors = feed(
        monkeypatch,
        bot,
        [
            httpx.ConnectError("down"),
            APIError(502, "bad gateway"),
            APIError(503, "unavailable"),
            batch([{"type": "x"}], 1),
            httpx.ReadTimeout("slow"),
        ],
    )
    checkpoints = []
    with pytest.raises(Stop):
        bot.poll(0, lambda event: None, on_checkpoint=checkpoints.append)
    assert cursors == [0, 0, 0, 0, 1, 1]
    assert sleeps == [1.0, 2.0, 4.0, 1.0]
    assert checkpoints == [1]
    bot.close()


def test_backoff_is_capped_and_uses_custom_delays(monkeypatch, sleeps):
    bot = Bot("fake")
    feed(monkeypatch, bot, [httpx.ConnectError("down")] * 5)
    with pytest.raises(Stop):
        bot.poll(0, lambda e: None, retry_initial_delay=0.5, retry_max_delay=3.0)
    assert sleeps == [0.5, 1.0, 2.0, 3.0, 3.0]
    bot.close()


def test_backoff_jitter_stays_below_the_ceiling(monkeypatch):
    monkeypatch.setattr("itd_bot.polling.random.random", lambda: 0.999)
    for attempt in range(1, 100):
        assert 0 < polling.backoff_delay(attempt, 1.0, 60.0) <= 60.0
    assert polling.backoff_delay(1, 1.0, 60.0) < 1.0


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("down"),
        httpx.ReadTimeout("slow"),
        APIError(500, "boom"),
        APIError(502, "boom"),
        APIError(503, "boom"),
        APIError(504, "boom"),
    ],
)
def test_transient_poll_errors_are_retried(monkeypatch, sleeps, error):
    bot = Bot("fake")
    cursors = feed(monkeypatch, bot, [error, batch([], 3)])
    with pytest.raises(Stop):
        bot.poll(3, lambda e: None)
    assert cursors == [3, 3, 3] and sleeps == [1.0, 2.0]
    bot.close()


@pytest.mark.parametrize(
    "error",
    [
        APIError(400, "bad"),
        APIError(401, "bad"),
        APIError(403, "bad"),
        APIError(404, "bad"),
        APIError(501, "bad"),
        UploadQuotaError(503, "quota"),
        ProtocolError("bad"),
        ResetRequired({}),
        SessionStorageError("bad"),
        ValueError("bad"),
        TypeError("bad"),
    ],
)
def test_other_poll_errors_are_raised_at_once(monkeypatch, sleeps, error):
    bot = Bot("fake")
    cursors = feed(monkeypatch, bot, [error])
    with pytest.raises(type(error)):
        bot.poll(0, lambda e: None)
    assert cursors == [0] and sleeps == []
    bot.close()


def test_rate_limit_keeps_its_own_pause(monkeypatch, sleeps):
    bot = Bot("fake")
    limited = RateLimitError(retry_after_seconds=7)
    feed(monkeypatch, bot, [limited, httpx.ConnectError("down")])
    with pytest.raises(Stop):
        bot.poll(0, lambda e: None)
    assert sleeps == [7, 1.0]
    bot.close()


def test_poll_retry_log_has_class_status_attempt_and_no_secrets(
    monkeypatch, sleeps, caplog
):
    bot = Bot("fake")
    feed(
        monkeypatch,
        bot,
        [
            httpx.ConnectError("https://example.test/updates?token=SECRET-TOKEN"),
            APIError(502, "SECRET-BODY"),
        ],
    )
    with caplog.at_level(logging.WARNING, logger="itd_bot"):
        with pytest.raises(Stop):
            bot.poll(0, lambda e: None)
    text = caplog.text
    assert "ConnectError" in text and "APIError" in text and "502" in text
    assert "retry 1" in text and "retry 2" in text and "2.0 s" in text
    assert "SECRET" not in text and "example.test" not in text
    bot.close()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"retry_initial_delay": 0},
        {"retry_initial_delay": -1.0},
        {"retry_initial_delay": 5.0, "retry_max_delay": 1.0},
        {"handler_retries": -1},
        {"handler_retries": True},
        {"handler_retries": 1.5},
    ],
)
def test_poll_validates_new_parameters(kwargs):
    bot = Bot("fake")
    with pytest.raises(ValueError):
        bot.poll(0, lambda e: None, **kwargs)
    bot.close()


# --- handler errors --------------------------------------------------------


def test_failed_handler_is_skipped_by_default_without_leaking_text(monkeypatch, caplog):
    bot = Bot("fake")
    events = [new_message(n=1), new_message(n=2), new_message(n=3)]
    feed(monkeypatch, bot, [batch(events, 3)])
    handled, checkpoints = [], []

    def handler(event):
        handled.append(event["n"])
        if event["n"] == 2:
            raise RuntimeError("private-text token-abc")

    with caplog.at_level(logging.ERROR, logger="itd_bot"):
        with pytest.raises(Stop):
            bot.poll(0, handler, on_checkpoint=checkpoints.append)
    assert handled == [1, 2, 3] and checkpoints == [3]
    assert "RuntimeError" in caplog.text and "new_message" in caplog.text
    assert "private-text" not in caplog.text and "token-abc" not in caplog.text
    assert [r.levelno for r in caplog.records] == [logging.ERROR]
    bot.close()


def test_on_error_stop_raises_and_skips_checkpoint(monkeypatch):
    bot = Bot("fake")
    events = [new_message(n=1), new_message(n=2), new_message(n=3)]
    feed(monkeypatch, bot, [batch(events, 3)])
    handled, checkpoints, seen = [], [], []

    def handler(event):
        handled.append(event["n"])
        if event["n"] == 2:
            raise RuntimeError("failed")

    def hook(exc, event):
        seen.append((type(exc), event["n"]))
        return ErrorAction.STOP

    with pytest.raises(RuntimeError):
        bot.poll(0, handler, on_error=hook, on_checkpoint=checkpoints.append)
    assert handled == [1, 2] and checkpoints == [] and seen == [(RuntimeError, 2)]
    bot.close()


def test_on_error_retry_repeats_then_skips(monkeypatch, sleeps, caplog):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch([new_message(n=1), new_message(n=2)], 2)])
    calls, hooks, checkpoints = [], [], []

    def handler(event):
        calls.append(event["n"])
        if event["n"] == 1:
            raise RuntimeError("failed")

    def hook(exc, event):
        hooks.append(event["n"])
        return ErrorAction.RETRY

    with caplog.at_level(logging.WARNING, logger="itd_bot"):
        with pytest.raises(Stop):
            bot.poll(
                0,
                handler,
                on_error=hook,
                handler_retries=2,
                on_checkpoint=checkpoints.append,
            )
    assert calls == [1, 1, 1, 2] and hooks == [1, 1, 1]
    assert sleeps == [1.0, 2.0] and checkpoints == [2]
    levels = [r.levelno for r in caplog.records]
    assert levels == [logging.WARNING, logging.WARNING, logging.ERROR]
    assert "private-text" not in caplog.text
    bot.close()


def test_on_error_retry_stops_when_handler_succeeds(monkeypatch, sleeps):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch([new_message(n=1)], 1)])
    calls = []

    def handler(event):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("failed")

    with pytest.raises(Stop):
        bot.poll(0, handler, on_error=lambda exc, event: ErrorAction.RETRY)
    assert len(calls) == 2 and sleeps == [1.0]
    bot.close()


def test_retry_with_zero_handler_retries_skips_at_once(monkeypatch, sleeps):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch([new_message(n=1)], 1)])
    calls = []

    def handler(event):
        calls.append(1)
        raise RuntimeError("failed")

    with pytest.raises(Stop):
        bot.poll(
            0, handler, on_error=lambda e, ev: ErrorAction.RETRY, handler_retries=0
        )
    assert calls == [1] and sleeps == []
    bot.close()


@pytest.mark.parametrize("answer", [None, ErrorAction.SKIP])
def test_on_error_none_or_skip_skips_the_event(monkeypatch, answer):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch([new_message(n=1), new_message(n=2)], 2)])
    handled, checkpoints = [], []

    def handler(event):
        handled.append(event["n"])
        raise ValueError("failed")

    with pytest.raises(Stop):
        bot.poll(
            0,
            handler,
            on_error=lambda exc, event: answer,
            on_checkpoint=checkpoints.append,
        )
    assert handled == [1, 2] and checkpoints == [2]
    bot.close()


def test_hook_error_and_bad_answer_propagate(monkeypatch):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch([new_message(n=1)], 1)] * 2)
    checkpoints = []

    def failing(event):
        raise RuntimeError("failed")

    def boom(exc, event):
        raise HookBoom

    with pytest.raises(HookBoom) as info:
        bot.poll(0, failing, on_error=boom, on_checkpoint=checkpoints.append)
    assert isinstance(info.value.__context__, RuntimeError) and checkpoints == []
    with pytest.raises(TypeError):
        bot.poll(0, failing, on_error=lambda exc, event: "skip")
    bot.close()


@pytest.mark.parametrize("signal_exc", [KeyboardInterrupt, SystemExit])
def test_base_exceptions_bypass_the_hook(monkeypatch, signal_exc):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch([new_message(n=1)], 1)])
    hooks, checkpoints = [], []

    def handler(event):
        raise signal_exc

    with pytest.raises(signal_exc):
        bot.poll(
            0,
            handler,
            on_error=lambda exc, event: hooks.append(exc),
            on_checkpoint=checkpoints.append,
        )
    assert hooks == [] and checkpoints == []
    bot.close()


def test_stop_request_shortens_retry_pause_but_not_retries(monkeypatch):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch([new_message(n=1)], 1)])
    stop, calls, checkpoints = RecordingStop(), [], []

    def handler(event):
        calls.append(1)
        raise RuntimeError("failed")

    bot.poll(
        0,
        handler,
        on_error=lambda e, ev: ErrorAction.RETRY,
        handler_retries=2,
        stop=stop,
        on_checkpoint=checkpoints.append,
    )
    assert len(calls) == 3 and checkpoints == [1] and len(stop.waits) == 2
    bot.close()


# --- several workers -------------------------------------------------------


def chat_events():
    return [
        new_message("a", 1),
        new_message("a", 2),
        new_message("b", 3),
        new_message("b", 4),
    ]


def test_workers_failure_in_one_chat_does_not_touch_the_other(monkeypatch):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch(chat_events(), 4)])
    handled, checkpoints, lock = [], [], threading.Lock()

    def handler(event):
        with lock:
            handled.append(event["n"])
        if event["n"] == 1:
            raise RuntimeError("failed")

    with pytest.raises(Stop):
        bot.poll(0, handler, workers=2, on_checkpoint=checkpoints.append)
    assert sorted(handled) == [1, 2, 3, 4] and checkpoints == [4]
    assert handled.index(1) < handled.index(2) and handled.index(3) < handled.index(4)
    bot.close()


def test_workers_retry_in_one_chat_does_not_rerun_the_other(monkeypatch, sleeps):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch(chat_events(), 4)])
    counts, lock = {}, threading.Lock()

    def handler(event):
        with lock:
            counts[event["n"]] = counts.get(event["n"], 0) + 1
        if event["n"] == 1:
            raise RuntimeError("failed")

    with pytest.raises(Stop):
        bot.poll(
            0,
            handler,
            workers=2,
            on_error=lambda e, ev: ErrorAction.RETRY,
            handler_retries=2,
        )
    assert counts == {1: 3, 2: 1, 3: 1, 4: 1}
    bot.close()


def test_workers_stop_lets_started_handlers_finish_and_skips_checkpoint(
    monkeypatch,
):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch(chat_events(), 4)])
    both_started = threading.Barrier(2, timeout=5)
    finished, checkpoints = [], []

    def handler(event):
        if event["n"] in (1, 3):
            both_started.wait()
        if event["n"] == 1:
            raise RuntimeError("failed")
        finished.append(event["n"])

    with pytest.raises(RuntimeError):
        bot.poll(
            0,
            handler,
            workers=2,
            on_error=lambda e, ev: ErrorAction.STOP,
            on_checkpoint=checkpoints.append,
        )
    assert 3 in finished and checkpoints == []
    bot.close()


def test_run_by_chat_starts_nothing_new_after_a_failure():
    handled = []

    def handler(event):
        handled.append(event["chat_id"])
        raise RuntimeError("failed")

    with ThreadPoolExecutor(1) as executor:
        with pytest.raises(RuntimeError):
            _run_by_chat(executor, [new_message("a", 1), new_message("b", 2)], handler)
    assert handled == ["a"]


# --- soft stop -------------------------------------------------------------


def test_stop_during_pause_checkpoints_and_returns(monkeypatch):
    bot = Bot("fake")
    feed(monkeypatch, bot, [batch([], 4)])
    stop, checkpoints = RecordingStop(), []
    assert (
        bot.poll(0, lambda e: None, stop=stop, on_checkpoint=checkpoints.append) is None
    )
    assert stop.waits == [polling.WAIT_SLICE] and checkpoints == [4]
    bot.close()


def test_stop_during_error_backoff_returns_without_checkpoint(monkeypatch):
    bot = Bot("fake")
    cursors = feed(monkeypatch, bot, [httpx.ConnectError("down")])
    stop, checkpoints = RecordingStop(), []
    bot.poll(0, lambda e: None, stop=stop, on_checkpoint=checkpoints.append)
    assert cursors == [0] and stop.waits == [polling.WAIT_SLICE] and checkpoints == []
    bot.close()


def test_stop_set_before_start_fetches_nothing(monkeypatch):
    bot = Bot("fake")
    cursors = feed(monkeypatch, bot, [])
    stop = threading.Event()
    stop.set()
    bot.poll(0, lambda e: None, stop=stop)
    assert cursors == []
    bot.close()


@pytest.mark.parametrize("workers", [1, 3])
def test_stop_during_batch_finishes_it_and_leaks_no_threads(monkeypatch, workers):
    bot = Bot("fake")
    cursors = feed(monkeypatch, bot, [batch(chat_events(), 4)])
    stop, handled, checkpoints = threading.Event(), [], []
    lock = threading.Lock()

    def handler(event):
        stop.set()
        with lock:
            handled.append(event["n"])

    bot.poll(0, handler, workers=workers, stop=stop, on_checkpoint=checkpoints.append)
    assert sorted(handled) == [1, 2, 3, 4] and checkpoints == [4] and cursors == [0]
    assert not [t for t in threading.enumerate() if t.name.startswith("itd-handler")]
    bot.close()


def test_stop_joins_websocket_wakeup_thread(monkeypatch):
    bot = Bot("fake")
    stop = threading.Event()
    calls = []

    def updates(cursor):
        calls.append(cursor)
        stop.set()  # request the stop while the loop would wait for a frame
        return batch([], 2)

    def frames(*, stop):
        stop.wait(10)
        return
        yield

    monkeypatch.setattr(bot, "_require_websocket", lambda: None)
    monkeypatch.setattr(bot, "get_updates", updates)
    monkeypatch.setattr(bot, "websocket_events", frames)
    bot.poll(0, lambda e: None, websocket=True, interval=30, stop=stop)
    assert calls == [0]
    assert not [t for t in threading.enumerate() if t.name == "itd-ws-wakeup"]
    bot.close()


def make_router(monkeypatch, bot):
    monkeypatch.setattr(bot, "get_chats", lambda **kwargs: {"event_seq": 5})
    return Dispatcher(bot, self_user_id="self", auto_typing=False, read_mode="off")


def serve_one_message(monkeypatch, bot):
    def updates(since):
        if since == 5:
            return batch([new_message("a", 1)], 6)
        return batch([], since)

    monkeypatch.setattr(bot, "get_updates", updates)


def test_run_polling_returns_after_stop_and_state_has_last_cursor(
    monkeypatch, tmp_path
):
    bot = Bot("fake", base_url="https://example.test")
    router = make_router(monkeypatch, bot)
    stop = threading.Event()
    serve_one_message(monkeypatch, bot)

    @router.message()
    def handle(ctx):
        stop.set()

    path = tmp_path / "state.json"
    router.run_polling(state_path=path, stop=stop, handle_signals=False)
    assert json.loads(path.read_text())["cursor"] == 6
    bot.close()


def test_default_handler_failure_in_dispatcher_moves_the_cursor(monkeypatch, tmp_path):
    bot = Bot("fake", base_url="https://example.test")
    router = make_router(monkeypatch, bot)
    stop = threading.Event()
    serve_one_message(monkeypatch, bot)

    @router.message()
    def broken(ctx):
        stop.set()
        raise RuntimeError("failed")

    path = tmp_path / "state.json"
    router.run_polling(state_path=path, stop=stop, handle_signals=False)
    assert json.loads(path.read_text())["cursor"] == 6
    bot.close()


# --- Dispatcher hook and signals ------------------------------------------


class FakeSignals:
    """Record signal.signal calls without touching the real process."""

    def __init__(self, monkeypatch):
        self.handlers = {}
        self.calls = []
        self.originals = {}
        monkeypatch.setattr("itd_bot.polling.signal.signal", self.signal)

    def signal(self, signum, handler):
        self.calls.append((signum, handler))
        original = self.originals.setdefault(signum, object())
        previous = self.handlers.get(signum, original)
        self.handlers[signum] = handler
        return previous


def poll_probe(captured, body=None):
    def poll(since, handler, **kwargs):
        captured.update(kwargs)
        if body is not None:
            body(kwargs)

    return poll


def test_on_error_decorator_registers_once_and_reaches_poll(monkeypatch):
    bot = Bot("fake")
    router = Dispatcher(bot, self_user_id="self")

    @router.on_error
    def hook(exc, event):
        return ErrorAction.STOP

    with pytest.raises(ValueError):
        router.on_error(lambda exc, event: None)
    captured = {}
    monkeypatch.setattr(bot, "poll", poll_probe(captured))
    router.run_polling(0, handle_signals=False)
    assert captured["on_error"] is hook
    other = {}
    monkeypatch.setattr(bot, "poll", poll_probe(other))
    router.run_polling(0, handle_signals=False, on_error=None)
    assert other["on_error"] is None
    bot.close()


def test_signals_are_installed_and_restored(monkeypatch):
    fake = FakeSignals(monkeypatch)
    bot = Bot("fake")
    router = Dispatcher(bot, self_user_id="self")
    names = [n for n in ("SIGINT", "SIGTERM", "SIGBREAK") if hasattr(signal, n)]
    expected = {getattr(signal, n) for n in names}
    captured = {}

    def body(kwargs):
        assert set(fake.handlers) == expected
        assert all(callable(h) for h in fake.handlers.values())
        assert not kwargs["stop"].is_set()
        fake.handlers[signal.SIGTERM](signal.SIGTERM, None)
        assert kwargs["stop"].is_set()

    monkeypatch.setattr(bot, "poll", poll_probe(captured, body))
    router.run_polling(0)
    assert fake.handlers == fake.originals
    assert len(fake.calls) == 2 * len(expected)
    bot.close()


def test_second_sigint_raises_keyboard_interrupt_and_handlers_are_restored(
    monkeypatch,
):
    fake = FakeSignals(monkeypatch)
    bot = Bot("fake")
    router = Dispatcher(bot, self_user_id="self")

    def body(kwargs):
        handle = fake.handlers[signal.SIGINT]
        handle(signal.SIGINT, None)
        assert kwargs["stop"].is_set()
        handle(signal.SIGINT, None)

    monkeypatch.setattr(bot, "poll", poll_probe({}, body))
    with pytest.raises(KeyboardInterrupt):
        router.run_polling(0)
    assert fake.handlers == fake.originals
    bot.close()


def test_second_sigterm_does_not_raise(monkeypatch):
    fake = FakeSignals(monkeypatch)
    bot = Bot("fake")
    router = Dispatcher(bot, self_user_id="self")

    def body(kwargs):
        handle = fake.handlers[signal.SIGTERM]
        handle(signal.SIGTERM, None)
        handle(signal.SIGTERM, None)
        assert kwargs["stop"].is_set()

    monkeypatch.setattr(bot, "poll", poll_probe({}, body))
    router.run_polling(0)
    bot.close()


def test_user_stop_is_used_for_signals(monkeypatch):
    fake = FakeSignals(monkeypatch)
    bot = Bot("fake")
    router = Dispatcher(bot, self_user_id="self")
    mine, captured = threading.Event(), {}

    def body(kwargs):
        fake.handlers[signal.SIGTERM](signal.SIGTERM, None)

    monkeypatch.setattr(bot, "poll", poll_probe(captured, body))
    router.run_polling(0, stop=mine)
    assert captured["stop"] is mine and mine.is_set()
    bot.close()


def test_handle_signals_false_installs_nothing(monkeypatch):
    fake = FakeSignals(monkeypatch)
    bot = Bot("fake")
    router = Dispatcher(bot, self_user_id="self")
    captured = {}
    monkeypatch.setattr(bot, "poll", poll_probe(captured))
    router.run_polling(0, handle_signals=False)
    assert fake.calls == [] and "stop" not in captured
    bot.close()


def test_no_signal_handlers_outside_the_main_thread(monkeypatch):
    fake = FakeSignals(monkeypatch)
    bot = Bot("fake")
    router = Dispatcher(bot, self_user_id="self")
    monkeypatch.setattr(bot, "poll", poll_probe({}))
    errors = []

    def run():
        try:
            router.run_polling(0)
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=run)
    thread.start()
    thread.join(5)
    assert errors == [] and fake.calls == []
    bot.close()


# --- repeated GET ----------------------------------------------------------


class CountingLimiter:
    def __init__(self):
        self.acquired = []

    def acquire(self, method, path, *, cost=1, account=None):
        self.acquired.append(method)

    def observe(self, response, method, path, *, account=None):
        pass


def make_bot(handler, **kwargs):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    kwargs.setdefault("limiter", CountingLimiter())
    return Bot("token", base_url="https://example.test", client=client, **kwargs)


def test_get_is_retried_after_transport_error_and_5xx(sleeps):
    answers = [
        httpx.ConnectError("down"),
        httpx.Response(503),
        httpx.Response(200, json={"ok": 1}),
    ]
    seen = []

    def handler(req):
        seen.append(req.method)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    bot = make_bot(handler)
    assert bot.api_request("GET", "/api/users/me") == {"ok": 1}
    assert seen == ["GET"] * 3 and sleeps == [0.5, 1.0]
    assert bot._rate_limits.acquired == ["GET"] * 3
    bot.close()


def test_get_raises_original_error_when_retries_run_out(sleeps):
    count = []

    def handler(req):
        count.append(1)
        raise httpx.ReadTimeout("slow")

    bot = make_bot(handler)
    with pytest.raises(httpx.ReadTimeout):
        bot.api_request("GET", "/api/users/me")
    assert len(count) == 3 and sleeps == [0.5, 1.0]
    bot.close()


def test_get_retries_can_be_disabled_or_changed(sleeps):
    count = []

    def handler(req):
        count.append(1)
        return httpx.Response(502, json={"message": "bad"})

    bot = make_bot(handler, get_retries=0)
    with pytest.raises(APIError) as info:
        bot.api_request("GET", "/api/users/me")
    assert info.value.status == 502 and len(count) == 1 and sleeps == []
    bot.close()
    count.clear()
    bot = make_bot(handler, get_retries=3)
    with pytest.raises(APIError):
        bot.api_request("GET", "/api/users/me")
    assert len(count) == 4 and sleeps == [0.5, 1.0, 2.0]
    bot.close()


@pytest.mark.parametrize("value", [-1, True, 1.5, "2"])
def test_get_retries_is_validated(value):
    with pytest.raises(ValueError):
        Bot("fake", get_retries=value)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 501])
def test_get_does_not_retry_other_statuses(sleeps, status):
    count = []

    def handler(req):
        count.append(1)
        return httpx.Response(status, json={"message": "no"})

    bot = make_bot(handler)
    with pytest.raises(APIError):
        bot.api_request("GET", "/api/users/me")
    assert len(count) == 1 and sleeps == []
    bot.close()


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("failure", ["connect", "503"])
def test_writes_are_never_repeated(sleeps, method, failure):
    count = []

    def handler(req):
        count.append(req.method)
        if failure == "connect":
            raise httpx.ConnectError("down")
        return httpx.Response(503, json={"message": "no"})

    bot = make_bot(handler)
    expected = httpx.ConnectError if failure == "connect" else APIError
    with pytest.raises(expected):
        bot.api_request(method, "/api/things", json={"a": 1})
    assert count == [method] and sleeps == []
    bot.close()


def test_poll_backs_off_after_get_retries_run_out(monkeypatch, sleeps):
    count = []

    def handler(req):
        count.append(1)
        raise httpx.ConnectError("down")

    def pause(stop, seconds):
        raise Stop

    monkeypatch.setattr("itd_bot.client.pause", pause)
    bot = make_bot(handler)
    with pytest.raises(Stop):
        bot.poll(0, lambda e: None)
    assert len(count) == 3 and sleeps == [0.5, 1.0]
    bot.close()


def test_refresh_does_not_use_up_get_attempts(sleeps):
    statuses = [401, 503, 200]
    seen = []

    def handler(req):
        seen.append(req.headers["Authorization"])
        status = statuses.pop(0)
        return httpx.Response(status, json={"ok": status})

    bot = make_bot(
        handler, refresh_token="refresh-value", session_path=None, get_retries=1
    )
    session = bot._refresh_session

    def refresh(rejected, generation):
        session._access_token, session._generation = "fresh", generation + 1
        return "fresh"

    session.refresh_after_401 = refresh
    assert bot.api_request("GET", "/api/users/me") == {"ok": 200}
    # The 401 retry is free: the one allowed GET retry is still left for the 503.
    assert seen == ["Bearer token", "Bearer fresh", "Bearer fresh"]
    assert sleeps == [0.5]
    bot.close()


def test_failed_refresh_is_not_repeated(sleeps):
    count, refreshes = [], []

    def handler(req):
        count.append(1)
        return httpx.Response(401, json={"message": "expired"})

    def refresh(rejected, generation):
        refreshes.append(1)
        raise APIError(503, "Session refresh failed")

    bot = make_bot(handler, refresh_token="refresh-value", session_path=None)
    bot._refresh_session.refresh_after_401 = refresh
    with pytest.raises(APIError) as info:
        bot.api_request("GET", "/api/users/me")
    assert info.value.status == 503 and len(count) == 1 and refreshes == [1]
    assert sleeps == []
    bot.close()


def test_downloads_are_not_repeated(monkeypatch, sleeps, tmp_path):
    file_id = "11111111-2222-3333-4444-555555555555"
    stream_calls = []

    def handler(req):
        if req.url.path.endswith("/file-links"):
            link = f"/v1/chat-files/{file_id}"
            return httpx.Response(
                200, json={"links": [{"fileId": file_id, "url": link}]}
            )
        if req.url.path.endswith("/browser-file-session"):
            return httpx.Response(204)
        stream_calls.append(req.url.path)
        raise httpx.ConnectError("down")

    bot = make_bot(handler)
    with pytest.raises(httpx.ConnectError):
        bot.download_file("chat", file_id)
    assert len(stream_calls) == 1

    banner_calls = []

    def banner_handler(req):
        banner_calls.append(1)
        raise httpx.ConnectError("down")

    real = httpx.Client
    monkeypatch.setattr(
        "itd_bot.client.httpx.Client",
        lambda **kwargs: real(transport=httpx.MockTransport(banner_handler)),
    )
    with pytest.raises(httpx.ConnectError):
        bot.download_banner("https://cdn.xn--d1ah4a.com/b.png", tmp_path / "b.png")
    assert len(banner_calls) == 1 and sleeps == []
    bot.close()


# --- review additions ------------------------------------------------------


def test_unsupported_protocol_is_a_setup_error_not_transient():
    assert polling.is_transient(httpx.ConnectError("down"))
    assert not polling.is_transient(httpx.UnsupportedProtocol("no scheme"))


def test_long_pauses_are_cut_into_short_slices():
    stop = RecordingStop(stop_on_wait=False)
    assert polling.pause(stop, 1.25) is False
    assert stop.waits == [0.5, 0.5, 0.25]
    stop = RecordingStop(stop_on_wait=False)
    assert polling.pause(stop, 0.5) is False and stop.waits == [0.5]
    stop = RecordingStop()
    assert polling.pause(stop, 60.0) is True and stop.waits == [0.5]


def test_on_error_decorator_works_with_and_without_brackets():
    bot = Bot("fake")
    router = Dispatcher(bot, self_user_id="self")

    @router.on_error()
    def hook(exc, event):
        return None

    assert hook(RuntimeError(), {}) is None
    with pytest.raises(ValueError):
        router.on_error()(lambda exc, event: None)
    bot.close()


def test_scheduled_refresh_failure_is_not_repeated_by_get_retries(monkeypatch, sleeps):
    bot = Bot("fake", get_retries=2)
    attempts = []

    def failing_refresh():
        attempts.append(1)
        raise httpx.ConnectError("refresh down")

    monkeypatch.setattr(bot, "_access_state", failing_refresh)
    with pytest.raises(httpx.ConnectError):
        bot.get_chats()
    assert attempts == [1] and sleeps == []
    bot.close()


def blocking_router(monkeypatch, bot):
    monkeypatch.setattr(bot, "get_chats", lambda **kwargs: {"event_seq": 5})
    return Dispatcher(bot, self_user_id="self", auto_typing=False, read_mode="blocking")


def test_transient_read_mark_failure_fetches_the_batch_again(
    monkeypatch, sleeps, caplog
):
    bot = Bot("fake")
    cursors = feed(
        monkeypatch,
        bot,
        [
            batch([new_message("a", 1), new_message("a", 2)], 2),
            batch([new_message("a", 1), new_message("a", 2)], 2),
        ],
    )
    marks, handled, checkpoints = [], [], []

    def mark_read(chat_id, message_id):
        marks.append(message_id)
        if len(marks) == 2:
            raise httpx.ReadTimeout("slow")

    monkeypatch.setattr(bot, "mark_read", mark_read)
    router = blocking_router(monkeypatch, bot)
    router.message()(lambda ctx: handled.append(ctx.message["message_id"]))
    with caplog.at_level(logging.WARNING, logger="itd_bot"), pytest.raises(Stop):
        bot.poll(0, router.handle, on_checkpoint=checkpoints.append)
    # The same cursor was asked for again and nothing was lost or checkpointed early.
    assert cursors == [0, 0, 2]
    assert handled == ["1", "1", "2"] and checkpoints == [2]
    assert sleeps == [1.0]
    assert "Read mark failed" in caplog.text and "private-text" not in caplog.text
    bot.close()


def test_read_mark_rate_limit_also_fetches_the_batch_again(monkeypatch, sleeps):
    bot = Bot("fake")
    cursors = feed(
        monkeypatch,
        bot,
        [batch([new_message("a", 1)], 1), batch([new_message("a", 1)], 1)],
    )
    marks, handled = [], []

    def mark_read(chat_id, message_id):
        marks.append(1)
        if len(marks) == 1:
            raise RateLimitError(retry_after_seconds=1)

    monkeypatch.setattr(bot, "mark_read", mark_read)
    router = blocking_router(monkeypatch, bot)
    router.message()(lambda ctx: handled.append(1))
    with pytest.raises(Stop):
        bot.poll(0, router.handle)
    assert cursors == [0, 0, 1] and handled == [1]
    bot.close()


def test_permanent_read_mark_failure_skips_only_that_event(monkeypatch, sleeps):
    bot = Bot("fake")
    cursors = feed(
        monkeypatch, bot, [batch([new_message("a", 1), new_message("b", 2)], 2)]
    )
    handled = []

    def mark_read(chat_id, message_id):
        if chat_id == "a":
            raise APIError(404, "chat is gone")

    monkeypatch.setattr(bot, "mark_read", mark_read)
    router = blocking_router(monkeypatch, bot)
    router.message()(lambda ctx: handled.append(ctx.message["message_id"]))
    with pytest.raises(Stop):
        bot.poll(0, router.handle)
    assert cursors == [0, 2] and handled == ["2"] and sleeps == []
    bot.close()


def test_manual_handle_still_raises_the_original_read_mark_error(monkeypatch):
    bot = Bot("fake")

    def mark_read(chat_id, message_id):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(bot, "mark_read", mark_read)
    router = blocking_router(monkeypatch, bot)
    with pytest.raises(httpx.ConnectError):
        router.handle(new_message("a", 1))
    bot.close()


def test_read_mark_redelivery_with_workers_keeps_other_chats_safe(monkeypatch, sleeps):
    bot = Bot("fake")
    cursors = feed(
        monkeypatch,
        bot,
        [
            batch([new_message("a", 1), new_message("b", 2)], 2),
            batch([new_message("a", 1), new_message("b", 2)], 2),
        ],
    )
    lock, handled, calls = threading.Lock(), [], []

    def mark_read(chat_id, message_id):
        with lock:
            calls.append(chat_id)
            first_for_a = chat_id == "a" and calls.count("a") == 1
        if first_for_a:
            raise httpx.ConnectError("down")

    monkeypatch.setattr(bot, "mark_read", mark_read)
    router = blocking_router(monkeypatch, bot)
    router.message()(lambda ctx: handled.append(ctx.message["message_id"]))
    with pytest.raises(Stop):
        bot.poll(0, router.handle, workers=2)
    assert cursors == [0, 0, 2] and "1" in handled and sleeps == [1.0]
    bot.close()


def test_stop_during_read_mark_redelivery_pause_returns_without_checkpoint(
    monkeypatch,
):
    bot = Bot("fake")
    cursors = feed(monkeypatch, bot, [batch([new_message("a", 1)], 1)])

    def mark_read(chat_id, message_id):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(bot, "mark_read", mark_read)
    router = blocking_router(monkeypatch, bot)
    stop, checkpoints = RecordingStop(), []
    bot.poll(0, router.handle, stop=stop, on_checkpoint=checkpoints.append)
    assert cursors == [0] and checkpoints == [] and stop.waits == [0.5]
    bot.close()
