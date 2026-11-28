"""WS delivery uses fake sockets and MockTransport, never the live service."""

import json
import queue
import sys
import threading
import types

import httpx
import pytest
from helpers import FAR_FUTURE, Transport, install_websocket, jwt, strict_limiter

from itd_bot import APIError, ErrorAction, ProtocolError, ResetRequired, Router
from itd_bot._ws_source import WebSocketUpdates, _sequence


class SocketTimeout(Exception):
    pass


class Server:
    def __init__(self, monkeypatch):
        self.frames = queue.Queue()
        self.ready = threading.Event()
        self.consumed = threading.Event()
        self.connections = []
        self.calls = []
        server = self

        class Connection:
            handshake_response = types.SimpleNamespace(status=101)

            def __init__(self):
                self.closed = False

            def recv_data(self, control_frame=True):
                try:
                    frame = server.frames.get(timeout=0.01)
                except queue.Empty:
                    raise SocketTimeout from None
                server.consumed.set()
                return frame

            def settimeout(self, value):
                pass

            def send(self, value):
                assert value == "ping"

            def close(self):
                self.closed = True

        def connect(url, **kwargs):
            server.calls.append((url, kwargs))
            connection = Connection()
            server.connections.append(connection)
            server.ready.set()
            return connection

        install_websocket(
            monkeypatch,
            types.SimpleNamespace(
                create_connection=connect,
                WebSocketTimeoutException=SocketTimeout,
                ABNF=types.SimpleNamespace(
                    OPCODE_CLOSE=8, OPCODE_PING=9, OPCODE_PONG=10
                ),
            ),
        )

    def send(self, frame):
        self.frames.put((1, json.dumps(frame)))

    def close(self, code=1000):
        self.frames.put((8, code.to_bytes(2, "big")))


def event(seq, *, chat="chat-1", typ="new_message", count=None):
    result = {
        "type": typ,
        "chat_id": chat,
        "event_seq": seq,
        "message": {"message_id": str(seq), "sender_id": "user-1", "text": "fictional"},
        "future": {"x": 1},
    }
    if count is not None:
        result["event_seq_count"] = count
    return result


def response(events=(), cursor=0):
    return httpx.Response(
        200,
        json={
            "events": list(events),
            "next_event_seq": cursor,
            "reset_required": False,
        },
    )


def run(bot, handler, stop, *, queued=False, **options):
    guard = threading.Timer(4, stop.set)
    guard.start()
    try:
        bot.poll(
            0,
            handler,
            source="websocket",
            chat_queues=queued,
            stop=stop,
            interval=0.001,
            min_fetch_interval=0,
            retry_initial_delay=0.001,
            retry_max_delay=0.002,
            **options,
        )
    finally:
        stop.set()
        guard.cancel()


@pytest.mark.parametrize("legacy_flag", [False, True])
@pytest.mark.parametrize("queued", [False, True])
def test_contiguous_frames_use_one_socket_and_preserve_fields_without_get_per_frame(
    monkeypatch, queued, legacy_flag
):
    server, stop = Server(monkeypatch), threading.Event()
    reads, seen, checkpoints = [], [], []

    def answer(request):
        assert server.ready.wait(2)
        assert request.url.params["capabilities"] == "chat_settings"
        reads.append(int(request.url.params["since"]))
        if len(reads) == 2:
            for seq in range(1, 21):
                server.send(event(seq))
        return response()

    transport = Transport(default=answer)

    def handle(frame):
        seen.append(frame)
        if frame["event_seq"] == 20:
            stop.set()

    run(
        transport.bot(limiter=strict_limiter()),
        handle,
        stop,
        queued=queued,
        websocket=legacy_flag,
        on_checkpoint=checkpoints.append,
    )
    assert seen == [event(seq) for seq in range(1, 21)]
    assert 1 <= len(reads) <= 3 and set(reads) == {0}
    assert checkpoints[-1] == 20
    assert len(server.calls) == 1 and all(c.closed for c in server.connections)
    assert "capabilities=chat_settings_v2" in server.calls[0][0]
    assert all(request.method == "GET" for request in transport.requests)


@pytest.mark.parametrize("queued", [False, True])
def test_gap_fetches_from_cursor_then_delivers_pending_frame_in_order(
    monkeypatch, queued
):
    server, stop = Server(monkeypatch), threading.Event()
    reads, seen = [], []

    def answer(request):
        assert server.ready.wait(2)
        since = int(request.url.params["since"])
        reads.append(since)
        if len(reads) == 2:
            server.send(event(1))
        return response([event(2)], 2) if since == 1 else response()

    def handle(frame):
        seen.append(frame["event_seq"])
        if seen == [1]:
            server.send(event(3))
        if frame["event_seq"] == 3:
            stop.set()

    transport = Transport(default=answer)
    run(transport.bot(limiter=strict_limiter()), handle, stop, queued=queued)
    assert seen == [1, 2, 3] and 1 in reads


@pytest.mark.parametrize("queued", [False, True])
def test_count_covers_merged_events_and_duplicates_are_skipped(monkeypatch, queued):
    server, stop = Server(monkeypatch), threading.Event()
    calls, seen = [], []

    def answer(request):
        assert server.ready.wait(2)
        calls.append(1)
        if len(calls) == 2:
            server.send(event(2, count=2))
            server.send(event(2, count=2))
            server.send(event(1))
            server.send(event(3))
        return response()

    def handle(frame):
        seen.append(frame["event_seq"])
        if frame["event_seq"] == 3:
            stop.set()

    run(
        Transport(default=answer).bot(limiter=strict_limiter()),
        handle,
        stop,
        queued=queued,
    )
    assert seen == [2, 3] and len(calls) <= 3


@pytest.mark.parametrize("queued", [False, True])
def test_unsequenced_presence_and_typing_do_not_advance_cursor(monkeypatch, queued):
    server, stop = Server(monkeypatch), threading.Event()
    calls, seen, checkpoints = [], [], []
    raw = [
        {"type": "presence", "user_id": "user-1", "online": True, "future": 1},
        {"type": "typing", "chat_id": "chat-1", "user_id": "user-1", "event_seq": None},
    ]

    def answer(request):
        assert server.ready.wait(2)
        calls.append(1)
        if len(calls) == 2:
            for frame in raw:
                server.send(frame)
        return response()

    def handle(frame):
        seen.append(frame)
        if len(seen) == 2:
            stop.set()

    run(
        Transport(default=answer).bot(limiter=strict_limiter()),
        handle,
        stop,
        queued=queued,
        on_checkpoint=checkpoints.append,
    )
    assert seen == raw and all(cursor == 0 for cursor in checkpoints)


def test_unsequenced_queue_entry_blocks_later_checkpoint_until_completed(monkeypatch):
    server, stop = Server(monkeypatch), threading.Event()
    released, complete = threading.Event(), threading.Event()
    calls, seen = [], []

    def answer(request):
        assert server.ready.wait(2)
        calls.append(1)
        if len(calls) == 2:
            server.send({"type": "typing", "chat_id": "chat-1", "user_id": "user-1"})
            server.send(event(1, chat="chat-2"))
            server.send(event(2))
        return response()

    def handle(frame):
        if frame["type"] == "typing":
            assert released.wait(2)
            complete.set()
        elif frame["event_seq"] == 1:
            released.set()
        else:
            assert complete.is_set()
            stop.set()
        seen.append(frame["type"])

    def checkpoint(cursor):
        assert cursor == 0 or complete.is_set()

    run(
        Transport(default=answer).bot(limiter=strict_limiter()),
        handle,
        stop,
        queued=True,
        workers=2,
        on_checkpoint=checkpoint,
    )
    assert len(seen) == 3 and complete.is_set()


@pytest.mark.parametrize("http_has_event", [False, True])
@pytest.mark.parametrize("queued", [False, True])
def test_router_read_failure_recovers_from_http_without_duplicate_success(
    monkeypatch, queued, http_has_event
):
    server, stop = Server(monkeypatch), threading.Event()
    gets, reads, handled, checkpoints = [], [], [], []

    def answer(request):
        if request.method == "GET":
            assert server.ready.wait(2)
            since = int(request.url.params["since"])
            gets.append(since)
            if len(gets) == 2:
                server.send(event(1))
            return response([event(1)], 1) if reads and http_has_event else response()
        if request.url.path.endswith("/read"):
            reads.append(1)
            if len(reads) == 1:
                raise httpx.ReadError("fictional read failure")
            return httpx.Response(200, json={})
        raise AssertionError("unexpected write")

    transport = Transport(default=answer)
    router = Router(
        transport.bot(limiter=strict_limiter()), self_user_id="self", auto_typing=False
    )

    @router.message()
    def handle(ctx):
        handled.append(ctx.message_id)
        stop.set()

    guard = threading.Timer(4, stop.set)
    guard.start()
    try:
        router.run_polling(
            0,
            source="websocket",
            chat_queues=queued,
            stop=stop,
            interval=0.001,
            min_fetch_interval=0,
            retry_initial_delay=0.001,
            retry_max_delay=0.002,
            on_checkpoint=checkpoints.append,
            handle_signals=False,
        )
    finally:
        stop.set()
        guard.cancel()
    assert handled == ["1"] and len(reads) == 2 and len(gets) >= 3
    assert checkpoints[-1] == 1


@pytest.mark.parametrize("failure", ["reset", "forbidden", "checkpoint", "handler"])
def test_fatal_errors_close_source_and_do_not_hide_reset(monkeypatch, failure):
    server, stop = Server(monkeypatch), threading.Event()
    calls, checkpoints = [], []

    def answer(request):
        assert server.ready.wait(2)
        calls.append(1)
        if failure == "reset":
            return httpx.Response(200, json={"reset_required": True})
        if failure == "forbidden":
            return httpx.Response(403, json={})
        if len(calls) == 2:
            server.send(event(1))
        return response()

    def handle(frame):
        if failure == "handler":
            raise RuntimeError("fictional failure")

    def checkpoint(cursor):
        if cursor:
            checkpoints.append(cursor)
            raise RuntimeError("fictional checkpoint failure")

    expected = (
        ResetRequired
        if failure == "reset"
        else APIError
        if failure == "forbidden"
        else RuntimeError
    )
    with pytest.raises(expected):
        run(
            Transport(default=answer).bot(limiter=strict_limiter()),
            handle,
            stop,
            on_checkpoint=checkpoint,
            on_error=lambda exc, event: ErrorAction.STOP,
        )
    assert server.connections and all(c.closed for c in server.connections)


@pytest.mark.parametrize("field", ["event_seq", "event_seq_count"])
@pytest.mark.parametrize("value", [True, -1, 1.5, "1", 1 << 53])
def test_sequence_rejects_invalid_safe_integer_without_coercion(field, value):
    with pytest.raises(ProtocolError):
        _sequence({"type": "presence", field: value})


@pytest.mark.parametrize("value", [None, 0, 1, (1 << 53) - 1])
def test_sequence_accepts_optional_safe_integer_values(value):
    assert _sequence({"event_seq": value, "event_seq_count": value}) == (
        value,
        1 if value is None else value,
    )


@pytest.mark.parametrize("source", [None, "", "ws", True, []])
def test_invalid_source_fails_before_any_network(source):
    transport = Transport()
    with pytest.raises(ValueError, match="source"):
        transport.bot().poll(0, lambda event: None, source=source)
    assert not transport.requests


def test_disconnect_recovers_new_events_through_http(monkeypatch, caplog):
    server, stop = Server(monkeypatch), threading.Event()
    reads, seen = [], []

    def answer(request):
        assert server.ready.wait(2)
        since = int(request.url.params["since"])
        reads.append(since)
        if len(reads) == 2:
            server.send(event(1))
        return response([event(2)], 2) if since == 1 else response()

    def handle(frame):
        seen.append(frame["event_seq"])
        if frame["event_seq"] == 1:
            server.close()
        else:
            stop.set()

    run(Transport(default=answer).bot(limiter=strict_limiter()), handle, stop)
    assert seen == [1, 2] and 1 in reads
    assert "recovering with HTTP" in caplog.text
    assert all(connection.closed for connection in server.connections)


def test_buffer_backpressure_is_bounded_and_close_unblocks_reader(monkeypatch):
    import itd_bot._ws_source as module

    server = Server(monkeypatch)
    full = threading.Event()

    class ObservedQueue(queue.Queue):
        def put(self, value, **kwargs):
            if self.full():
                full.set()
            return super().put(value, **kwargs)

    monkeypatch.setattr(
        module,
        "queue",
        types.SimpleNamespace(Queue=ObservedQueue, Empty=queue.Empty, Full=queue.Full),
    )
    source = WebSocketUpdates(
        Transport().bot(limiter=strict_limiter()),
        threading.Event(),
        None,
        capacity=1,
        min_http_interval=0,
    )
    try:
        server.send(event(1))
        assert full.wait(2)
        assert source._buffer.qsize() == 1
    finally:
        source.close()
    assert not source._thread.is_alive() and all(
        connection.closed for connection in server.connections
    )


def test_http_recovery_rate_limit_wait_is_interruptible(monkeypatch):
    Server(monkeypatch)
    stop = threading.Event()
    transport = Transport(default=response())
    source = WebSocketUpdates(
        transport.bot(limiter=strict_limiter()),
        threading.Event(),
        stop,
        capacity=4,
        min_http_interval=10,
        monotonic=lambda: 0.0,
    )
    try:
        source.fetch(0)
        stop.set()
        assert source._http(0)["next_event_seq"] == 0
        assert len(transport.requests) == 1
    finally:
        source.close()


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "presence", "event_seq": True, "private": "fictional-private"},
        {"type": "new_message", "message": {"sender_id": "user-1", "message_id": "1"}},
    ],
)
def test_bad_sequence_or_unsequenced_message_uses_http_without_logging_payload(
    monkeypatch, frame, caplog
):

    monkeypatch.setattr(WebSocketUpdates, "_read", lambda self: None)
    transport = Transport(response(), response([event(1)], 1))
    source = WebSocketUpdates(
        transport.bot(limiter=strict_limiter()),
        threading.Event(),
        None,
        capacity=2,
        min_http_interval=0,
    )
    try:
        source.fetch(0)
        source._connected = True
        source._buffer.put(frame)
        assert source.fetch(0)["events"] == [event(1)]
        assert len(transport.requests) == 2
        assert "fictional-private" not in caplog.text
    finally:
        source.close()


@pytest.mark.parametrize("code", [4001, 4003])
def test_source_uses_existing_auth_refresh_and_rotated_cookie(monkeypatch, code):
    import itd_bot._ws_source as module

    server, stop = Server(monkeypatch), threading.Event()
    ws = sys.modules["websocket"]
    original = ws.create_connection
    pauses, seen = [], []

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        if len(server.connections) == 1:
            server.close(code)
        else:
            server.send(event(1))
        return connection

    def answer(request):
        if request.url.path.endswith("/auth/refresh"):
            assert request.headers["cookie"] == "refresh_token=fictional-seed"
            return httpx.Response(
                200,
                json={"accessToken": jwt("self", FAR_FUTURE + 60)},
                headers={
                    "set-cookie": "refresh_token=fictional-rotated; Path=/api/v1/auth; HttpOnly"
                },
            )
        return response()

    monkeypatch.setattr(ws, "create_connection", connect)
    monkeypatch.setattr(
        module, "pause", lambda stop, delay: pauses.append(delay) or False
    )
    transport = Transport(default=answer)
    bot = transport.bot(
        token=jwt("self"),
        refresh_token="fictional-seed",
        session_path=None,
        limiter=strict_limiter(),
    )

    def handle(frame):
        seen.append(frame)
        stop.set()

    run(bot, handle, stop)
    assert seen == [event(1)] and bot.refresh_count == 1
    assert len(server.connections) == 2 and all(c.closed for c in server.connections)
    assert pauses == [1.0]
    assert server.calls[1][1]["subprotocols"][-1] == "bearer." + jwt(
        "self", FAR_FUTURE + 60
    )
    assert [r.url.path for r in transport.requests if r.method == "POST"] == [
        "/api/v1/auth/refresh"
    ]


@pytest.mark.parametrize("queued", [False, True])
@pytest.mark.parametrize(
    "failure", [httpx.ReadTimeout("fictional"), httpx.Response(503, json={})]
)
def test_source_http_read_failure_retries_same_cursor(monkeypatch, queued, failure):
    server, stop = Server(monkeypatch), threading.Event()
    reads, seen = [], []

    def answer(request):
        assert server.ready.wait(2)
        reads.append(int(request.url.params["since"]))
        if len(reads) == 1:
            if isinstance(failure, Exception):
                raise failure
            return failure
        if len(reads) == 2:
            server.send(event(1))
        return response()

    def handle(frame):
        seen.append(frame)
        stop.set()

    transport = Transport(default=answer)
    run(
        transport.bot(limiter=strict_limiter(), get_retries=0),
        handle,
        stop,
        queued=queued,
    )
    assert seen == [event(1)] and set(reads) == {0} and len(reads) >= 2
    assert all(request.method == "GET" for request in transport.requests)


@pytest.mark.parametrize("queued", [False, True])
def test_source_http_429_pause_honors_stop(monkeypatch, queued):
    server, stop = Server(monkeypatch), threading.Event()

    def answer(request):
        assert server.ready.wait(2)
        stop.set()
        return httpx.Response(429, headers={"Retry-After": "30"}, json={})

    transport = Transport(default=answer)
    run(
        transport.bot(limiter=strict_limiter()),
        lambda frame: pytest.fail("no frame"),
        stop,
        queued=queued,
    )
    assert len(transport.requests) == 1 and all(c.closed for c in server.connections)


def test_already_stopped_source_does_not_connect_or_fetch(monkeypatch):
    server, stop = Server(monkeypatch), threading.Event()
    stop.set()
    transport = Transport()
    source = WebSocketUpdates(
        transport.bot(), threading.Event(), stop, capacity=2, min_http_interval=0
    )
    try:
        assert source.fetch(7) == {
            "events": [],
            "reset_required": False,
            "next_event_seq": 7,
        }
    finally:
        source.close()
    assert not server.calls and not transport.requests


def test_stop_interrupts_a_continuous_backlog_of_duplicates(monkeypatch):
    monkeypatch.setattr(WebSocketUpdates, "_read", lambda self: None)
    stop = threading.Event()
    source = WebSocketUpdates(
        Transport(default=response()).bot(),
        threading.Event(),
        stop,
        capacity=2,
        min_http_interval=0,
    )

    class Duplicates:
        calls = 0

        def get_nowait(self):
            self.calls += 1
            if self.calls == 20:
                stop.set()
            return event(0)

        def empty(self):
            return False

    try:
        source.fetch(0)
        source._connected = True
        source._buffer = Duplicates()
        assert source.fetch(0)["events"] == []
        assert source._buffer.calls == 20
    finally:
        source.close()


def test_source_requires_optional_websocket_before_http(monkeypatch):
    install_websocket(monkeypatch, None)
    transport = Transport()
    with pytest.raises(RuntimeError, match="websocket"):
        transport.bot().poll(0, lambda frame: None, source="websocket")
    assert not transport.requests


def test_source_keeps_http_fallback_running_then_reconnects(monkeypatch):
    server, stop = Server(monkeypatch), threading.Event()
    ws = sys.modules["websocket"]
    original = ws.create_connection
    seen = []

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        if len(server.connections) == 1:
            server.close()
        else:
            server.send(event(1))
        return connection

    monkeypatch.setattr(ws, "create_connection", connect)
    transport = Transport(default=response())

    def handle(frame):
        seen.append(frame)
        stop.set()

    guard = threading.Timer(4, stop.set)
    guard.start()
    try:
        transport.bot(limiter=strict_limiter()).poll(
            0,
            handle,
            source="websocket",
            stop=stop,
            interval=0.02,
            min_fetch_interval=0.01,
        )
    finally:
        stop.set()
        guard.cancel()
    assert seen == [event(1)] and len(server.connections) == 2
    assert len(transport.requests) > 3 and all(c.closed for c in server.connections)
