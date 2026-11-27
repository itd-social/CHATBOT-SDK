"""Per-chat queues through public polling, with fictional updates and HTTP."""

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import httpx
import pytest
from helpers import Transport

from itd_bot import Dispatcher, ErrorAction, ProtocolError, ResetRequired
from itd_bot.chat_queues import _ChatQueues


def event(seq, chat="a", message_id=None):
    return {
        "type": "new_message",
        "event_seq": seq,
        "chat_id": chat,
        "message": {
            "message_id": message_id or str(seq),
            "sender_id": "other",
            "text": "hello",
        },
    }


def response(events=(), seq=0, **fields):
    return httpx.Response(
        200,
        json={
            "events": list(events),
            "next_event_seq": seq,
            "reset_required": False,
            **fields,
        },
    )


def polling_bot(*script):
    transport = Transport(
        *script, default=lambda request: response(seq=int(request.url.params["since"]))
    )
    return transport, transport.bot(get_retries=0)


@contextmanager
def running(bot, handler, *, stop=None, **kwargs):
    stop = stop or threading.Event()
    failures = []

    def poll():
        try:
            bot.poll(
                0,
                handler,
                chat_queues=True,
                workers=3,
                interval=0.01,
                stop=stop,
                **kwargs,
            )
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    try:
        yield stop, thread, failures
    finally:
        stop.set()
        thread.join(3)
        assert not thread.is_alive(), "poll did not stop"


def test_slow_chat_does_not_block_another_chat_from_the_next_batch():
    started, release, fast = threading.Event(), threading.Event(), threading.Event()
    checkpoints = []

    def second(request):
        assert started.wait(2)
        return response([event(2, "b")], 2)

    transport, bot = polling_bot(response([event(1)], 1), second)

    def handle(item):
        if item["chat_id"] == "a":
            started.set()
            assert release.wait(2)
        else:
            fast.set()

    with running(bot, handle, on_checkpoint=checkpoints.append) as (
        stop,
        thread,
        failures,
    ):
        try:
            assert fast.wait(2)
            assert not release.is_set() and checkpoints == []
            assert [r.url.params["since"] for r in transport.requests[:2]] == ["0", "1"]
        finally:
            release.set()
            stop.set()
        thread.join(2)
        assert not failures and checkpoints[-1] == 2


def test_watermark_stops_before_a_gap_and_uses_event_seq_not_list_position():
    middle, release, last, prefix = [threading.Event() for _ in range(4)]
    checkpoints, callback_threads = [], []
    _, bot = polling_bot(response([event(1), event(3, "b"), event(7, "c")], 9))

    def handle(item):
        if item["event_seq"] == 3:
            middle.set()
            assert release.wait(2)
        if item["event_seq"] == 7:
            last.set()

    def checkpoint(cursor):
        callback_threads.append(threading.get_ident())
        checkpoints.append(cursor)
        if cursor == 1:
            prefix.set()

    with running(bot, handle, on_checkpoint=checkpoint) as (stop, thread, failures):
        try:
            assert middle.wait(2) and last.wait(2) and prefix.wait(2)
            assert checkpoints == [1]
        finally:
            release.set()
            stop.set()
        thread.join(2)
        assert not failures and checkpoints[-1] == 9
        assert set(callback_threads) == {thread.ident}


def test_one_chat_keeps_order_across_batches_while_other_chat_runs():
    started, release, fast = [threading.Event() for _ in range(3)]
    seen = []

    def second(request):
        assert started.wait(2)
        return response([event(2), event(3, "b")], 3)

    _, bot = polling_bot(response([event(1)], 1), second)

    def handle(item):
        if item["event_seq"] == 1:
            started.set()
            assert release.wait(2)
        seen.append(item["event_seq"])
        if item["event_seq"] == 3:
            fast.set()

    with running(bot, handle) as (stop, thread, failures):
        try:
            assert fast.wait(2) and seen == [3]
        finally:
            release.set()
            stop.set()
        thread.join(2)
        assert not failures and seen == [3, 1, 2]


def test_full_chat_queue_pauses_fetching_without_dropping_events():
    started, release, second_fetched = [threading.Event() for _ in range(3)]
    seen, checkpoints = [], []

    def first(request):
        return response([event(1), event(2), event(3)], 3)

    def later(request):
        second_fetched.set()
        return response(seq=3)

    transport, bot = polling_bot(first, later)

    def handle(item):
        if item["event_seq"] == 1:
            started.set()
            assert release.wait(2)
        seen.append(item["event_seq"])

    with running(bot, handle, chat_queue_size=1, on_checkpoint=checkpoints.append) as (
        stop,
        thread,
        failures,
    ):
        try:
            assert started.wait(2)
            assert not second_fetched.wait(0.05)
            assert len(transport.requests) == 1 and checkpoints == []
        finally:
            stop.set()
            release.set()
        thread.join(2)
        assert not failures and seen == [1, 2, 3] and checkpoints[-1] == 3


def test_completed_events_behind_a_gap_also_have_a_global_bound():
    started, release, full, extra_fetch = [threading.Event() for _ in range(4)]
    seen = []
    script = [response([event(1)], 1)]
    for seq in range(2, 8):

        def answer(request, seq=seq):
            if seq == 7:
                extra_fetch.set()
            return response([event(seq, "b")], seq)

        script.append(answer)
    _, bot = polling_bot(*script)

    def handle(item):
        if item["event_seq"] == 1:
            started.set()
            assert release.wait(2)
        else:
            seen.append(item["event_seq"])
            full.set()

    with running(bot, handle, chat_queue_size=2) as (stop, thread, failures):
        try:
            assert started.wait(2) and full.wait(2)
            assert not extra_fetch.wait(0.05)
        finally:
            stop.set()
            release.set()
        thread.join(2)
        assert not failures and seen


def test_handler_error_is_isolated_and_later_events_continue(caplog):
    stop = threading.Event()
    seen, errors, checkpoints = [], [], []
    _, bot = polling_bot(response([event(1), event(2), event(3, "b")], 3))

    def handle(item):
        seen.append(item["event_seq"])
        if item["event_seq"] == 1:
            raise RuntimeError("fictional-private-error")
        if len(seen) == 3:
            stop.set()

    bot.poll(
        0,
        handle,
        chat_queues=True,
        workers=2,
        stop=stop,
        interval=0.01,
        on_error=lambda exc, item: errors.append(item["event_seq"]),
        on_checkpoint=checkpoints.append,
    )
    assert sorted(seen) == [1, 2, 3] and errors == [1] and checkpoints[-1] == 3
    assert seen.index(1) < seen.index(2)
    assert "fictional-private-error" not in caplog.text


def test_stop_error_does_not_checkpoint_the_failed_or_following_event():
    failure = RuntimeError("fictional failure")
    _, bot = polling_bot(response([event(1), event(2)], 2))
    seen, checkpoints = [], []

    def handle(item):
        seen.append(item["event_seq"])
        raise failure

    with pytest.raises(RuntimeError) as caught:
        bot.poll(
            0,
            handle,
            chat_queues=True,
            workers=2,
            on_error=lambda exc, item: ErrorAction.STOP,
            on_checkpoint=checkpoints.append,
        )
    assert caught.value is failure and seen == [1] and checkpoints == []


@pytest.mark.parametrize("failure_type", [KeyboardInterrupt, SystemExit])
def test_base_exception_reaches_the_polling_thread(failure_type):
    _, bot = polling_bot(response([event(1)], 1))
    failure = failure_type()

    def handle(item):
        raise failure

    with pytest.raises(failure_type) as caught:
        bot.poll(0, handle, chat_queues=True)
    assert caught.value is failure


@pytest.mark.parametrize("bad", [None, True, "1", -1, 0, 2.5])
def test_missing_or_invalid_event_sequence_is_rejected_before_handlers(bad):
    item = event(1)
    if bad is None:
        del item["event_seq"]
    else:
        item["event_seq"] = bad
    transport, bot = polling_bot(response([event(1), item], 2))
    seen = []
    with pytest.raises(ProtocolError, match="per-event event_seq"):
        bot.poll(0, seen.append, chat_queues=True)
    assert seen == [] and len(transport.requests) == 1


@pytest.mark.parametrize("sequences", [[2, 1], [1, 1], [1, 3]])
def test_out_of_order_duplicate_or_out_of_batch_sequence_is_rejected(sequences):
    _, bot = polling_bot(response([event(seq) for seq in sequences], 2))
    seen = []
    with pytest.raises(ProtocolError):
        bot.poll(0, seen.append, chat_queues=True)
    assert seen == []


def test_mark_read_failure_refetches_from_watermark_and_dedup_skips_completed_messages(
    monkeypatch,
):
    stop = threading.Event()
    calls, handled, checkpoints = [], [], []
    failed = threading.Event()
    combined = {
        "type": "new_messages",
        "chat_id": "a",
        "event_seq": 1,
        "messages": [event(1)["message"], event(2)["message"]],
    }

    def answer(request):
        if request.method == "GET":
            cursor = int(request.url.params["since"])
            calls.append(cursor)
            return response(seq=1) if cursor == 1 else response([combined], 1)
        if request.url.path.endswith("/read"):
            import json

            message_id = json.loads(request.content)["last_read_message"]
            if message_id == "2" and not failed.is_set():
                failed.set()
                return httpx.Response(503, json={})
            return httpx.Response(200, json={})
        raise AssertionError("unexpected route")

    transport = Transport(default=answer)
    bot = transport.bot(get_retries=0)
    router = Dispatcher(bot, self_user_id="self", auto_typing=False)

    @router.message()
    def handle(ctx):
        handled.append(ctx.message_id)
        if ctx.message_id == "2":
            stop.set()

    monkeypatch.setattr("itd_bot.chat_queues.pause", lambda stopped, delay: False)
    bot.poll(
        0,
        router.handle,
        chat_queues=True,
        chat_queue_size=1,
        workers=2,
        interval=0.001,
        stop=stop,
        on_checkpoint=checkpoints.append,
    )
    assert handled == ["1", "2"] and calls.count(0) == 2
    assert checkpoints[-1] == 1


@pytest.mark.parametrize(
    "failure",
    [
        ResetRequired({}),
        ProtocolError("bad schema"),
        httpx.UnsupportedProtocol("bad scheme"),
    ],
)
def test_permanent_fetch_error_is_not_hidden(failure):
    bot = Transport(failure).bot(get_retries=0)
    with pytest.raises(type(failure)) as caught:
        bot.poll(0, lambda item: None, chat_queues=True)
    assert caught.value is failure


def test_stop_before_first_fetch_sends_no_request():
    transport, bot = polling_bot()
    stop = threading.Event()
    stop.set()
    bot.poll(0, lambda item: None, chat_queues=True, stop=stop)
    assert transport.requests == []


@pytest.mark.parametrize(
    "name,value",
    [
        ("chat_queues", "yes"),
        ("chat_queue_size", 0),
        ("chat_queue_size", True),
        ("chat_queue_size", 1.5),
    ],
)
def test_invalid_queue_options_fail_before_fetch(name, value):
    transport, bot = polling_bot()
    with pytest.raises(ValueError, match=name):
        bot.poll(0, lambda item: None, **{name: value})
    assert transport.requests == []


def test_checkpoint_failure_propagates_without_advancing_again():
    failure = OSError("fictional storage failure")
    _, bot = polling_bot(response([event(1)], 1))
    calls = []

    def checkpoint(cursor):
        calls.append(cursor)
        raise failure

    with pytest.raises(OSError) as caught:
        bot.poll(
            0,
            lambda item: None,
            chat_queues=True,
            on_checkpoint=checkpoint,
            interval=0.001,
        )
    assert caught.value is failure and calls == [1]


def test_transient_fetch_errors_repeat_the_fetch_cursor_with_backoff(monkeypatch):
    stop = threading.Event()
    transport, bot = polling_bot(
        httpx.ReadTimeout("fictional-private-error"),
        httpx.Response(503, json={}),
        response([event(1)], 1),
    )
    attempts = []

    def delay(attempt, initial, maximum):
        attempts.append((attempt, initial, maximum))
        return 0.001

    monkeypatch.setattr("itd_bot.chat_queues.backoff_delay", delay)
    bot.poll(
        0,
        lambda item: stop.set(),
        chat_queues=True,
        stop=stop,
        interval=0.001,
        retry_initial_delay=0.01,
        retry_max_delay=0.1,
    )
    assert attempts == [(1, 0.01, 0.1), (2, 0.01, 0.1)]
    assert [r.url.params["since"] for r in transport.requests[:3]] == ["0"] * 3


def test_rate_limit_wait_is_capped_and_interruptible(monkeypatch):
    stop, waits = threading.Event(), []
    transport, bot = polling_bot(
        httpx.Response(429, headers={"Retry-After": "3600"}, json={})
    )

    def wait(self, seconds, stopped, wake=None):
        waits.append(seconds)
        stop.set()

    monkeypatch.setattr(_ChatQueues, "wait", wait)
    bot.poll(0, lambda item: pytest.fail("no event"), chat_queues=True, stop=stop)
    assert len(transport.requests) == 1 and waits == [60]


def test_websocket_wakes_keep_minimum_fetch_interval_and_stop_thread(monkeypatch):
    import types

    stop, ws_finished = threading.Event(), threading.Event()
    transport, bot = polling_bot(response(seq=0), response(seq=0))
    now, waits = [0.0], []
    monkeypatch.setattr(
        "itd_bot.chat_queues.time", types.SimpleNamespace(monotonic=lambda: now[0])
    )
    monkeypatch.setattr(bot, "_require_websocket", lambda: None)

    def wakeups(wake, stopped):
        for _ in range(1000):
            wake.set()
        stopped.wait(2)
        ws_finished.set()

    def wait(self, seconds, stopped, wake=None):
        waits.append(seconds)
        now[0] += max(0, seconds)
        if len(transport.requests) == 2:
            stop.set()

    monkeypatch.setattr(bot, "_websocket_wakeups", wakeups)
    monkeypatch.setattr(_ChatQueues, "wait", wait)
    bot.poll(
        0,
        lambda item: None,
        chat_queues=True,
        websocket=True,
        min_fetch_interval=0.2,
        interval=0.1,
        stop=stop,
    )
    assert len(transport.requests) == 2 and waits == [0.1, 0.1, 0.1]
    assert ws_finished.is_set()


def test_handler_retry_does_not_repeat_another_chat(monkeypatch):
    stop = threading.Event()
    _, bot = polling_bot(response([event(1), event(2, "b")], 2))
    seen = []
    lock = threading.Lock()

    def handle(item):
        with lock:
            seen.append(item["event_seq"])
            first_count = seen.count(1)
            if len(seen) == 3:
                stop.set()
        if item["event_seq"] == 1 and first_count == 1:
            raise RuntimeError("fictional failure")

    monkeypatch.setattr("itd_bot.client.pause", lambda stopped, delay: False)
    bot.poll(
        0,
        handle,
        chat_queues=True,
        workers=2,
        stop=stop,
        interval=0.001,
        on_error=lambda exc, item: ErrorAction.RETRY,
    )
    assert seen.count(1) == 2 and seen.count(2) == 1


def test_repeated_read_failures_back_off_until_stop(monkeypatch):
    stop, attempts, pauses = threading.Event(), [], []
    combined = {
        "type": "new_messages",
        "event_seq": 1,
        "chat_id": "a",
        "messages": [event(1)["message"]],
    }
    transport = Transport(
        default=lambda request: (
            response([combined], 1)
            if request.method == "GET" and request.url.params["since"] == "0"
            else response(seq=1)
            if request.method == "GET"
            else httpx.Response(503, json={})
        )
    )
    bot = transport.bot(get_retries=0)
    router = Dispatcher(bot, self_user_id="self", auto_typing=False)

    def delay(attempt, initial, maximum):
        attempts.append(attempt)
        return 0.001

    def pause(stopped, seconds):
        pauses.append(seconds)
        if len(pauses) == 3:
            stop.set()
            return True
        return False

    monkeypatch.setattr("itd_bot.chat_queues.backoff_delay", delay)
    monkeypatch.setattr("itd_bot.chat_queues.pause", pause)
    router.message()(lambda ctx: pytest.fail("read did not succeed"))
    bot.poll(0, router.handle, chat_queues=True, stop=stop, interval=0.001)
    assert attempts == [1, 2, 3] and pauses == [0.001] * 3


def test_event_without_chat_id_keeps_order_in_shared_queue():
    stop, seen = threading.Event(), []
    items = [{"type": "future_event", "event_seq": n} for n in range(1, 4)]
    _, bot = polling_bot(response(items, 3))

    def handle(item):
        seen.append(item["event_seq"])
        if len(seen) == 3:
            stop.set()

    bot.poll(0, handle, chat_queues=True, workers=3, stop=stop, interval=0.001)
    assert seen == [1, 2, 3]


def test_failure_to_schedule_next_handler_is_propagated(monkeypatch):
    started, release, fetched = [threading.Event() for _ in range(3)]
    failure = RuntimeError("fictional executor failure")
    original = ThreadPoolExecutor.submit
    submitted = []

    def submit(self, *args, **kwargs):
        submitted.append(1)
        if len(submitted) == 2:
            raise failure
        return original(self, *args, **kwargs)

    def answer(request):
        fetched.set()
        return response(seq=2)

    transport = Transport(response([event(1), event(2)], 2), default=answer)
    bot = transport.bot()

    def handle(item):
        started.set()
        assert release.wait(2)

    monkeypatch.setattr(ThreadPoolExecutor, "submit", submit)
    with running(bot, handle) as (stop, thread, failures):
        try:
            assert started.wait(2) and fetched.wait(2)
        finally:
            release.set()
        thread.join(2)
        assert failures == [failure]
