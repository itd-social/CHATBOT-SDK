import json
import threading
import time

import httpx
import pytest

from itd_bot import Bot, Dispatcher


class Stop(Exception):
    pass


def batch(events, seq):
    return {"events": events, "reset_required": False, "next_event_seq": seq}


def message_event(chat, message_id, text="hi"):
    return {
        "type": "new_message",
        "chat_id": chat,
        "message": {"sender_id": "u", "message_id": message_id, "text": text},
    }


def test_poll_pauses_only_after_empty_batch(monkeypatch):
    bot = Bot("fake")
    batches = [batch([{"type": "x"}], 1), batch([{"type": "x"}], 2), batch([], 2)]
    calls, sleeps, checkpoints = [], [], []

    def updates(cursor):
        calls.append(cursor)
        return batches.pop(0)

    def sleep(seconds):
        sleeps.append((seconds, len(calls)))
        raise Stop

    monkeypatch.setattr(bot, "get_updates", updates)
    monkeypatch.setattr("itd_bot.client.time.sleep", sleep)
    with pytest.raises(Stop):
        bot.poll(0, lambda event: None, on_checkpoint=checkpoints.append)
    assert calls == [0, 1, 2]
    assert checkpoints == [1, 2, 2]
    assert sleeps == [(2.0, 3)]
    bot.close()


def test_poll_websocket_frame_wakes_loop_early(monkeypatch):
    bot = Bot("fake")
    calls = []
    fetched_once = threading.Event()
    stops = []

    def updates(cursor):
        calls.append(time.monotonic())
        if len(calls) == 1:
            fetched_once.set()
            return batch([], 5)
        raise Stop

    def frames(*, stop):
        stops.append(stop)
        assert fetched_once.wait(2)
        time.sleep(0.05)
        yield {"type": "new_message"}
        stop.wait(2)

    monkeypatch.setattr(bot, "_require_websocket", lambda: None)
    monkeypatch.setattr(bot, "get_updates", updates)
    monkeypatch.setattr(bot, "websocket_events", frames)
    with pytest.raises(Stop):
        bot.poll(0, lambda event: None, interval=30, websocket=True)
    assert len(calls) == 2 and calls[1] - calls[0] < 5
    assert stops[0].is_set()
    bot.close()


def test_poll_websocket_requires_extra(monkeypatch):
    bot = Bot("fake")

    def missing():
        raise RuntimeError("Install the websocket extra")

    monkeypatch.setattr(bot, "_require_websocket", missing)
    with pytest.raises(RuntimeError):
        bot.poll(0, lambda event: None, websocket=True)
    with pytest.raises(ValueError):
        bot.poll(0, lambda event: None, workers=0)
    bot.close()


def test_poll_workers_parallel_across_chats_and_ordered_within_chat(monkeypatch):
    bot = Bot("fake")
    events = [
        {"type": "e", "chat_id": "a", "n": 1},
        {"type": "e", "chat_id": "a", "n": 2},
        {"type": "e", "chat_id": "b", "n": 3},
    ]
    batches = [batch(events, 3)]
    b_done = threading.Event()
    seen, checkpoints = [], []
    lock = threading.Lock()

    def updates(cursor):
        if not batches:
            raise Stop
        return batches.pop(0)

    def handler(event):
        if event["n"] == 1:
            assert b_done.wait(2), "chat b was blocked by slow chat a"
        with lock:
            seen.append(event["n"])
        if event["n"] == 3:
            b_done.set()

    monkeypatch.setattr(bot, "get_updates", updates)
    with pytest.raises(Stop):
        bot.poll(0, handler, workers=4, on_checkpoint=checkpoints.append)
    assert seen == [3, 1, 2]
    assert checkpoints == [3]
    bot.close()


def test_poll_workers_failure_is_not_checkpointed(monkeypatch):
    bot = Bot("fake")
    events = [{"type": "e", "chat_id": "a"}, {"type": "e", "chat_id": "b"}]
    monkeypatch.setattr(bot, "get_updates", lambda cursor: batch(events, 2))
    checkpoints, handled = [], []

    def handler(event):
        handled.append(event["chat_id"])
        if event["chat_id"] == "a":
            raise RuntimeError("failed")

    with pytest.raises(RuntimeError):
        bot.poll(0, handler, workers=2, on_checkpoint=checkpoints.append)
    assert sorted(handled) == ["a", "b"] and checkpoints == []
    bot.close()


def test_background_read_does_not_block_and_coalesces():
    gate = threading.Event()
    reads, replies = [], []

    def transport(req):
        body = json.loads(req.content)
        if req.url.path.endswith("/read"):
            reads.append(body["last_read_message"])
            gate.wait(2)
            return httpx.Response(403, json={"message": "denied"})
        replies.append(body["text"])
        return httpx.Response(200, json={"message_id": "r"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        with Dispatcher(bot, self_user_id="self") as router:

            @router.on_message
            def reply(event, message):
                bot.send_message(event["chat_id"], "re:" + message["text"])

            router.handle(
                {
                    "type": "new_messages",
                    "chat_id": "c",
                    "messages": [
                        {"sender_id": "u", "message_id": "1", "text": "a"},
                        {"sender_id": "u", "message_id": "2", "text": "b"},
                        {"sender_id": "u", "message_id": "3", "text": "c"},
                    ],
                }
            )
            assert replies == ["re:a", "re:b", "re:c"]
            gate.set()
    assert reads[-1] == "3" and len(reads) <= 2


def test_read_mode_off_and_validation():
    calls = []

    def transport(req):
        calls.append(req.url.path)
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        router = Dispatcher(bot, self_user_id="self", read_mode="off")
        router.on_message(lambda event, message: None)
        router.handle(message_event("c", "m"))
        router.close()
        assert calls == []
        with pytest.raises(ValueError):
            Dispatcher(bot, self_user_id="self", read_mode="later")
        with pytest.raises(ValueError):
            Dispatcher(bot, self_user_id="self", typing_delay=-1)


def test_typing_only_for_handlers_slower_than_delay():
    actions = []
    typed = threading.Event()

    def transport(req):
        if req.url.path.endswith("/action"):
            actions.append(req.url.path)
            typed.set()
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        with Dispatcher(
            bot, self_user_id="self", read_mode="off", typing_delay=0.05
        ) as router:

            @router.command("fast")
            def fast(ctx):
                pass

            @router.command("slow")
            def slow(ctx):
                assert typed.wait(2)

            router.handle(message_event("c", "1", "/fast"))
            time.sleep(0.1)
            assert actions == []
            router.handle(message_event("c", "2", "/slow"))
            assert actions == ["/api/v1/messenger/chats/c/action"]


def test_websocket_events_stop(monkeypatch):
    websocket = pytest.importorskip("websocket")
    stop = threading.Event()

    class FakeSocket:
        handshake_response = type("Handshake", (), {"status": 101})()
        closed = False
        timeouts = []

        def settimeout(self, timeout):
            self.timeouts.append(timeout)

        def recv(self):
            stop.set()
            raise websocket.WebSocketTimeoutException()

        def close(self):
            self.closed = True

    conn = FakeSocket()
    monkeypatch.setattr(websocket, "create_connection", lambda url, **kw: conn)
    bot = Bot("token", base_url="https://example.test")
    assert list(bot.websocket_events(stop=stop)) == []
    assert conn.closed and max(conn.timeouts) <= 1.0
    bot.close()
