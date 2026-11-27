"""Auth close frames refresh a session without hiding other failures."""

import threading
import types

import httpx
import pytest
from helpers import BASE, FAR_FUTURE, Transport, install_websocket, jwt

from itd_bot import APIError, Bot, ProtocolError


def close_frame(code):
    return 8, code.to_bytes(2, "big") + b"private-fictional-reason"


class Socket:
    def __init__(self, frames):
        self.frames = list(frames)
        self.handshake_response = types.SimpleNamespace(status=101)
        self.closed = False

    def recv(self):
        raise AssertionError("recv loses the close code")

    def recv_data(self, control_frame=False):
        assert control_frame is True
        item = self.frames.pop(0)
        return item() if callable(item) else item

    def settimeout(self, value):
        pass

    def send(self, value):
        pass

    def close(self):
        self.closed = True


def install(monkeypatch, sockets):
    sockets = iter(sockets)
    calls = []

    def connect(url, **kwargs):
        calls.append((url, kwargs))
        return next(sockets)

    module = types.ModuleType("websocket")
    module.create_connection = connect
    module.WebSocketBadStatusException = type("BadStatus", (Exception,), {})
    module.WebSocketTimeoutException = type("SocketTimeout", (Exception,), {})
    module.ABNF = types.SimpleNamespace(OPCODE_CLOSE=8, OPCODE_PING=9, OPCODE_PONG=10)
    install_websocket(monkeypatch, module)
    waits = []
    monkeypatch.setattr(
        "itd_bot.client.pause", lambda stop, delay: waits.append(delay) or False
    )
    return calls, waits


def bot(client, **kwargs):
    return Bot(
        jwt("self"),
        refresh_token="fictional-refresh",
        session_path=None,
        base_url=BASE,
        client=client,
        **kwargs,
    )


@pytest.mark.parametrize("code", [4001, 4003])
def test_auth_close_refreshes_and_reconnects_with_new_token(monkeypatch, caplog, code):
    first = Socket([close_frame(code)])
    second = Socket([(1, b'{"type":"presence","unknown":true}')])
    calls, waits = install(monkeypatch, [first, second])
    fresh = jwt("self", FAR_FUTURE + 60)

    def refreshed(request):
        assert first.closed
        assert request.method == "POST"
        assert request.url.path == "/api/v1/auth/refresh"
        return httpx.Response(200, json={"accessToken": fresh})

    transport = Transport(refreshed)
    with transport.client() as client:
        current = bot(client)
        stream = current.websocket_events()
        assert next(stream) == {"type": "presence", "unknown": True}
        stream.close()
        assert current.refresh_count == 1
    assert waits == [1.0]
    assert len(transport.requests) == 1 and len(calls) == 2
    assert calls[0][1]["subprotocols"] == ["itd.messenger.v1", "bearer." + jwt("self")]
    assert calls[1][1]["subprotocols"] == ["itd.messenger.v1", "bearer." + fresh]
    assert first.closed and second.closed
    assert "private-fictional-reason" not in caplog.text
    assert jwt("self") not in caplog.text and fresh not in caplog.text


def test_auth_refreshes_are_bounded_and_back_off(monkeypatch):
    sockets = [Socket([close_frame(4001)]) for _ in range(4)]
    calls, waits = install(monkeypatch, sockets)
    transport = Transport(
        *[
            httpx.Response(200, json={"accessToken": jwt("self", FAR_FUTURE + n)})
            for n in range(1, 4)
        ]
    )
    with transport.client() as client:
        current = bot(client)
        with pytest.raises(ProtocolError, match="WebSocket closed"):
            next(current.websocket_events())
        assert current.refresh_count == 3
    assert len(calls) == 4 and len(transport.requests) == 3
    assert waits == [1.0, 2.0, 4.0]
    assert all(socket.closed for socket in sockets)


@pytest.mark.parametrize("code", [1000, 4000, 4002])
def test_other_close_codes_do_not_refresh_or_reconnect(monkeypatch, code):
    socket = Socket([close_frame(code)])
    calls, waits = install(monkeypatch, [socket])
    transport = Transport()
    with transport.client() as client:
        with pytest.raises(ProtocolError, match="WebSocket closed"):
            next(bot(client).websocket_events())
    assert len(calls) == 1 and socket.closed
    assert transport.requests == [] and waits == []


@pytest.mark.parametrize("payload", [b"", b"\x0f", "invalid-close"])
def test_missing_or_invalid_close_code_is_not_guessed(monkeypatch, payload):
    socket = Socket([(8, payload)])
    calls, waits = install(monkeypatch, [socket])
    transport = Transport()
    with transport.client() as client:
        with pytest.raises(ProtocolError, match="WebSocket closed"):
            next(bot(client).websocket_events())
    assert len(calls) == 1 and socket.closed
    assert transport.requests == [] and waits == []


@pytest.mark.parametrize("code", [4001, 4003])
def test_auth_close_without_refresh_session_keeps_the_error(monkeypatch, code):
    socket = Socket([close_frame(code)])
    calls, waits = install(monkeypatch, [socket])
    transport = Transport()
    with transport.client() as client:
        current = Bot("fictional-token", base_url=BASE, client=client)
        with pytest.raises(ProtocolError):
            next(current.websocket_events())
    assert len(calls) == 1 and transport.requests == [] and waits == []
    assert socket.closed


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_failed_refresh_is_not_repeated(monkeypatch, status):
    socket = Socket([close_frame(4001)])
    calls, waits = install(monkeypatch, [socket])
    transport = Transport(httpx.Response(status, json={"error": {"code": "FAILED"}}))
    with transport.client() as client:
        with pytest.raises(APIError) as caught:
            next(bot(client).websocket_events())
    assert caught.value.status == status
    assert len(transport.requests) == 1 and len(calls) == 1 and waits == [1.0]
    assert socket.closed


def test_refresh_network_error_is_not_repeated(monkeypatch):
    socket = Socket([close_frame(4003)])
    calls, waits = install(monkeypatch, [socket])
    failure = httpx.ReadTimeout("fictional refresh failure")
    transport = Transport(failure)
    with transport.client() as client:
        with pytest.raises(httpx.ReadTimeout) as caught:
            next(bot(client).websocket_events())
    assert caught.value is failure
    assert len(transport.requests) == 1 and len(calls) == 1 and waits == [1.0]


@pytest.mark.parametrize("when", ["frame", "backoff"])
def test_stop_prevents_auth_refresh_and_reconnect(monkeypatch, when):
    stop = threading.Event()

    def frame():
        if when == "frame":
            stop.set()
        return close_frame(4001)

    socket = Socket([frame])
    calls, waits = install(monkeypatch, [socket])

    def pause(stopped, delay):
        waits.append(delay)
        stop.set()
        return True

    monkeypatch.setattr("itd_bot.client.pause", pause)
    transport = Transport()
    with transport.client() as client:
        assert list(bot(client).websocket_events(stop=stop)) == []
    assert len(calls) == 1 and socket.closed and transport.requests == []
    assert waits == ([] if when == "frame" else [1.0])


def test_token_rotated_elsewhere_is_reused_without_another_refresh(monkeypatch):
    fresh = jwt("self", FAR_FUTURE + 60)
    transport = Transport()
    with transport.client() as client:
        current = bot(client)

        def rotated():
            current.set_token(fresh)
            return close_frame(4001)

        first = Socket([rotated])
        second = Socket([(1, b'{"type":"presence"}')])
        calls, waits = install(monkeypatch, [first, second])
        stream = current.websocket_events()
        assert next(stream) == {"type": "presence"}
        stream.close()
        assert current.refresh_count == 0
    assert transport.requests == [] and len(calls) == 2 and waits == [1.0]
    assert calls[1][1]["subprotocols"][-1] == "bearer." + fresh


def test_control_ping_and_pong_are_not_json_events(monkeypatch):
    socket = Socket([(9, b"ping"), (10, b"pong"), (1, b'{"type":"presence"}')])
    calls, waits = install(monkeypatch, [socket])
    transport = Transport()
    with transport.client() as client:
        current = Bot("fictional-token", base_url=BASE, client=client)
        stream = current.websocket_events()
        assert next(stream) == {"type": "presence"}
        stream.close()
    assert len(calls) == 1 and waits == [] and socket.closed


def test_reconnect_and_refresh_both_consume_request_budget(monkeypatch):
    first = Socket([close_frame(4001)])
    second = Socket([(1, b'{"type":"presence"}')])
    install(monkeypatch, [first, second])
    acquired = []

    class Limiter:
        def acquire(self, method, path, **kwargs):
            acquired.append((method, path))

        def observe(self, *args, **kwargs):
            pass

    transport = Transport(
        httpx.Response(200, json={"accessToken": jwt("self", FAR_FUTURE + 60)})
    )
    with transport.client() as client:
        stream = bot(client, limiter=Limiter()).websocket_events()
        next(stream)
        stream.close()
    assert acquired == [
        ("GET", "/api/v1/messenger/ws"),
        ("POST", "/api/v1/auth/refresh"),
        ("GET", "/api/v1/messenger/ws"),
    ]


def test_polling_wake_stream_backs_off_after_failed_auth_refresh(monkeypatch, caplog):
    sockets = [Socket([close_frame(code)]) for code in (4001, 4003)]
    calls, _ = install(monkeypatch, sockets)
    delays = []

    class Stop(threading.Event):
        def wait(self, timeout=None):
            delays.append(timeout)
            if len(delays) == 2:
                self.set()
            return self.is_set()

    stop, wake = Stop(), threading.Event()
    transport = Transport(*[httpx.Response(403, json={}) for _ in range(2)])
    with transport.client() as client:
        bot(client)._websocket_wakeups(wake, stop)
    assert delays == [1.0, 2.0] and wake.is_set()
    assert len(calls) == 2 and len(transport.requests) == 2
    assert all(socket.closed for socket in sockets)
    assert "private-fictional-reason" not in caplog.text
