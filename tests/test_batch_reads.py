"""Packet read coalescing is opt-in and respects delivery and error policies."""

import inspect
import json
import threading

import httpx
import pytest
from helpers import FAR_FUTURE, Transport, jwt

from itd_bot import APIError, Bot, Dispatcher


def message(seq, chat="a", message_id=None, sender="other"):
    return {
        "type": "new_message",
        "event_seq": seq,
        "chat_id": chat,
        "message": {
            "message_id": message_id or str(seq),
            "sender_id": sender,
            "text": "hello",
        },
    }


def batch(events, cursor):
    return httpx.Response(
        200, json={"events": events, "next_event_seq": cursor, "reset_required": False}
    )


def setup(pages, *, mode="batch", read=None, stop_on_empty=False, **bot_kwargs):
    stop, trace, lock = threading.Event(), [], threading.Lock()
    by_cursor = {}
    cursor = 0
    for events, end in pages:
        by_cursor[cursor] = (events, end)
        cursor = end

    def answer(request):
        if request.url.path.endswith("/updates"):
            since = int(request.url.params["since"])
            if since in by_cursor:
                return batch(*by_cursor[since])
            for events, end in by_cursor.values():
                if end > since:
                    return batch(
                        [item for item in events if item.get("event_seq", 0) > since],
                        end,
                    )
            if stop_on_empty:
                stop.set()
            return batch([], since)
        if request.url.path.endswith("/read"):
            chat = request.url.path.split("/")[-2]
            target = json.loads(request.content)["last_read_message"]
            with lock:
                trace.append(("read", chat, target))
            return (
                read(request, chat, target)
                if read is not None
                else httpx.Response(200, json={})
            )
        if request.url.path.endswith("/auth/refresh"):
            return httpx.Response(
                200, json={"accessToken": jwt("self", FAR_FUTURE + 60)}
            )
        raise AssertionError("unexpected route")

    transport = Transport(default=answer)
    bot = transport.bot(**bot_kwargs)
    router = Dispatcher(bot, self_user_id="self", auto_typing=False, read_mode=mode)
    return router, transport, stop, trace


def run(router, stop, trace, count, *, queued=False, workers=1, hook=None, **options):
    seen, lock = [], threading.Lock()

    @router.message()
    def handle(ctx):
        if hook is not None:
            hook(ctx)
        with lock:
            seen.append(ctx.message_id)
            trace.append(("handler", ctx.chat_id, ctx.message_id))
            if len(seen) == count:
                stop.set()

    router.run_polling(
        0,
        handle_signals=False,
        stop=stop,
        interval=0.001,
        workers=workers,
        chat_queues=queued,
        **options,
    )
    return seen


@pytest.mark.parametrize("queued,workers", [(False, 1), (False, 3), (True, 3)])
def test_one_read_per_chat_per_packet_before_the_first_handler(queued, workers):
    items = [message(1, message_id="z"), message(2, "b"), message(3, message_id="a")]
    router, transport, stop, trace = setup([(items, 3)])
    assert sorted(run(router, stop, trace, 3, queued=queued, workers=workers)) == [
        "2",
        "a",
        "z",
    ]
    reads = [item for item in trace if item[0] == "read"]
    assert sorted(reads) == [("read", "a", "a"), ("read", "b", "2")]
    for chat in ("a", "b"):
        assert trace.index(next(item for item in reads if item[1] == chat)) < next(
            n for n, item in enumerate(trace) if item[:2] == ("handler", chat)
        )


def test_read_targets_survive_reading_ahead_into_the_next_packet():
    fetched = threading.Event()
    router, transport, stop, trace = setup(
        [
            ([message(1), message(2)], 2),
            ([message(3), message(4)], 4),
        ]
    )
    original = transport._default

    def answer(request):
        if request.method == "GET" and request.url.params["since"] == "2":
            fetched.set()
        if (
            request.url.path.endswith("/read")
            and json.loads(request.content)["last_read_message"] == "2"
        ):
            assert fetched.wait(2)
        return original(request)

    transport._default = answer
    assert run(router, stop, trace, 4, queued=True, workers=2) == ["1", "2", "3", "4"]
    assert [item[2] for item in trace if item[0] == "read"] == ["2", "4"]


def test_blocked_read_in_a_does_not_lock_read_or_handler_in_b():
    fast = threading.Event()

    def read(request, chat, target):
        if chat == "a":
            assert fast.wait(2)
        return httpx.Response(200, json={})

    router, transport, stop, trace = setup(
        [([message(1), message(2, "b")], 2)], read=read
    )
    seen = run(
        router,
        stop,
        trace,
        2,
        queued=True,
        workers=2,
        hook=lambda ctx: fast.set() if ctx.chat_id == "b" else None,
    )
    assert seen == ["2", "1"]


def test_manual_new_messages_is_one_batch_and_ignores_own_messages():
    item = {
        "type": "new_messages",
        "chat_id": "a",
        "messages": [
            message(1)["message"],
            message(2)["message"],
            message(3, sender="self")["message"],
        ],
    }
    router, transport, stop, trace = setup([])
    router.message()(lambda ctx: trace.append(("handler", ctx.chat_id, ctx.message_id)))
    router.handle(item)
    assert trace == [("read", "a", "2"), ("handler", "a", "1"), ("handler", "a", "2")]
    router.handle(item)
    assert len(transport.requests) == 1  # Successful duplicates need no read mark.


@pytest.mark.parametrize("mode,reads", [("blocking", 2), ("off", 0)])
def test_existing_read_modes_keep_their_request_count(mode, reads):
    router, transport, stop, trace = setup([([message(1), message(2)], 2)], mode=mode)
    assert run(router, stop, trace, 2) == ["1", "2"]
    assert sum(item[0] == "read" for item in trace) == reads


def test_malformed_and_non_message_events_do_not_poison_read_targets():
    items = [
        {"type": "future", "chat_id": "a"},
        {"type": "new_message"},
        {"type": "new_messages", "chat_id": "a", "messages": None},
        {"type": "new_messages", "chat_id": "a", "messages": [None]},
        message(1),
        message(2, sender="self"),
        {"type": "new_message", "chat_id": "a", "message": {"sender_id": 17}},
        {
            "type": "new_message",
            "chat_id": "a",
            "message": {"sender_id": "other", "message_id": ""},
        },
        message(3),
    ]
    router, transport, stop, trace = setup([(items, 9)])
    assert run(router, stop, trace, 2) == ["1", "3"]
    assert [item for item in trace if item[0] == "read"] == [("read", "a", "3")]


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_manual_read_error_prevents_every_handler_and_is_not_retried(status):
    router, transport, stop, trace = setup(
        [], read=lambda *args: httpx.Response(status, json={})
    )
    router.message()(lambda ctx: pytest.fail("handler ran before read succeeded"))
    with pytest.raises(APIError) as caught:
        router.handle(
            {
                "type": "new_messages",
                "chat_id": "a",
                "messages": [message(1)["message"], message(2)["message"]],
            }
        )
    assert caught.value.status == status
    assert trace == [("read", "a", "2")] and len(transport.requests) == 1


def test_network_read_error_is_not_retried_by_the_batch_reader():
    failure = httpx.ReadTimeout("fictional-private-error")

    def failed(*args):
        raise failure

    router, transport, stop, trace = setup([], read=failed)
    router.message()(lambda ctx: pytest.fail("handler ran"))
    with pytest.raises(httpx.ReadTimeout) as caught:
        router.handle(message(1))
    assert caught.value is failure and len(transport.requests) == 1


def test_401_uses_existing_refresh_and_preserves_last_read_target():
    attempts = []

    def read(*args):
        attempts.append(1)
        return httpx.Response(401 if len(attempts) == 1 else 200, json={})

    router, transport, stop, trace = setup(
        [],
        read=read,
        token=jwt("self"),
        refresh_token="fictional-refresh",
        session_path=None,
    )
    router.message()(lambda ctx: trace.append(("handler", ctx.chat_id, ctx.message_id)))
    router.handle(
        {
            "type": "new_messages",
            "chat_id": "a",
            "messages": [message(1)["message"], message(2)["message"]],
        }
    )
    assert trace == [
        ("read", "a", "2"),
        ("read", "a", "2"),
        ("handler", "a", "1"),
        ("handler", "a", "2"),
    ]
    assert router.bot.refresh_count == 1 and len(transport.requests) == 3


@pytest.mark.parametrize("queued", [False, True])
def test_failed_packet_is_replayed_without_repeating_successful_chat(
    monkeypatch, queued
):
    failed = threading.Event()

    def read(request, chat, target):
        if chat == "b" and not failed.is_set():
            failed.set()
            return httpx.Response(503, json={})
        return httpx.Response(200, json={})

    router, transport, stop, trace = setup(
        [([message(1), message(2, "b")], 2)], read=read
    )
    monkeypatch.setattr("itd_bot.client.pause", lambda stopped, delay: False)
    monkeypatch.setattr("itd_bot.chat_queues.pause", lambda stopped, delay: False)
    assert run(router, stop, trace, 2, queued=queued, workers=1) == ["1", "2"]
    assert sum(item[:2] == ("handler", "a") for item in trace) == 1
    assert sum(item[:2] == ("read", "b") for item in trace) == 2


def test_state_file_path_uses_the_same_batch_delivery(tmp_path):
    router, transport, stop, trace = setup([([message(1), message(2)], 2)])
    seen = run(router, stop, trace, 2, state_path=tmp_path / "cursor.json")
    assert seen == ["1", "2"]
    assert json.loads((tmp_path / "cursor.json").read_text())["cursor"] == 2
    assert sum(item[0] == "read" for item in trace) == 1


def test_defaults_remain_unchanged():
    assert inspect.signature(Dispatcher).parameters["read_mode"].default == "blocking"
    assert inspect.signature(Bot.poll).parameters["interval"].default == 2.0
    assert inspect.signature(Bot.poll).parameters["workers"].default == 1


@pytest.mark.parametrize("queued", [False, True])
def test_websocket_burst_during_get_is_coalesced_without_parallel_requests(
    monkeypatch, queued
):
    entered, burst, finished, stop = [threading.Event() for _ in range(4)]
    calls, active, maximum = [], [0], [0]
    lock = threading.Lock()

    def answer(request):
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
            calls.append(1)
        try:
            if len(calls) == 1:
                entered.set()
                assert burst.wait(2)
            else:
                stop.set()
            return batch([], 0)
        finally:
            with lock:
                active[0] -= 1

    bot = Transport(default=answer).bot()

    def wakeups(wake, stopped):
        assert entered.wait(2)
        for _ in range(1000):
            wake.set()
        burst.set()
        stopped.wait(2)
        finished.set()

    monkeypatch.setattr(bot, "_require_websocket", lambda: None)
    monkeypatch.setattr(bot, "_websocket_wakeups", wakeups)
    bot.poll(
        0,
        lambda item: None,
        websocket=True,
        min_fetch_interval=0,
        stop=stop,
        chat_queues=queued,
    )
    assert len(calls) == 2 and maximum == [1] and finished.is_set()
