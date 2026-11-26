"""Bot.websocket_events against a fake ``websocket`` module: no network, no sleeping."""

from __future__ import annotations

import threading
import types

import pytest
from helpers import (
    Clock,
    Transport,
    install_websocket,
    patch_clock,
    strict_limiter,
)

import itd_bot.client as client_module
from itd_bot import Bot, ProtocolError, RateLimitError

TOKEN = "fictional-access-token"


class BadStatus(Exception):
    """Same attributes as ``websocket.WebSocketBadStatusException``."""

    def __init__(self, status_code, resp_headers=None, resp_body=None):
        super().__init__(f"handshake status {status_code}")
        self.status_code = status_code
        self.resp_headers = resp_headers
        self.resp_body = resp_body


class SocketTimeout(Exception):
    """Same role as ``websocket.WebSocketTimeoutException``."""


class DroppedConnection(Exception):
    """A failure the SDK does not translate; it must pass through unchanged."""


class FakeConnection:
    """Plays back ``frames``: text, bytes, ``None``, an exception, or a callable."""

    def __init__(self, frames=(), *, status=101):
        self.handshake_response = types.SimpleNamespace(status=status)
        self.frames = list(frames)
        self.sent: list[str] = []
        self.timeouts: list[float] = []
        self.closed = False

    def recv(self):
        if not self.frames:
            raise AssertionError("recv called more often than scripted")
        item = self.frames.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, BaseException):
            raise item
        return item

    def send(self, data):
        self.sent.append(data)

    def settimeout(self, value):
        self.timeouts.append(value)

    def close(self):
        self.closed = True


class Connector:
    def __init__(self, connection=None, error=None):
        self.connection, self.error, self.calls = connection, error, []

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            raise self.error
        return self.connection


def install(monkeypatch, connector):
    module = types.ModuleType("websocket")
    module.WebSocketBadStatusException = BadStatus
    module.WebSocketTimeoutException = SocketTimeout
    module.create_connection = connector
    install_websocket(monkeypatch, module)


def make_bot(**kwargs):
    kwargs.setdefault("limiter", strict_limiter())
    return Transport().bot(token=TOKEN, **kwargs)


def after(clock, seconds, result):
    """Frame that first moves the clock forward."""

    def run():
        clock.advance(seconds)
        return result

    return run


@pytest.fixture
def clock(monkeypatch):
    fake = Clock(0.0)
    patch_clock(monkeypatch, client_module, fake)
    return fake


# --- starting the connection ---------------------------------------------------------


def test_connection_uses_the_secure_url_and_keeps_the_token_out_of_it(monkeypatch):
    connection = FakeConnection([None])
    connector = Connector(connection)
    install(monkeypatch, connector)
    with pytest.raises(ProtocolError):
        next(make_bot().websocket_events())
    ((url, kwargs),) = connector.calls
    assert url == (
        "wss://example.test/api/v1/messenger/ws?capabilities=chat_settings_v2"
    )
    assert TOKEN not in url
    assert kwargs["subprotocols"] == ["itd.messenger.v1", "bearer." + TOKEN]
    assert kwargs["origin"] == "https://example.test"
    assert kwargs["redirect_limit"] == 0 and kwargs["timeout"] == 5


def test_plain_http_base_with_port_and_path_uses_ws(monkeypatch):
    connector = Connector(FakeConnection([None]))
    install(monkeypatch, connector)
    bot = make_bot(base_url="http://example.test:8080/prefix")
    with pytest.raises(ProtocolError):
        next(bot.websocket_events())
    url, kwargs = connector.calls[0]
    assert url == (
        "ws://example.test:8080/prefix/api/v1/messenger/ws?capabilities=chat_settings_v2"
    )
    assert kwargs["origin"] == "http://example.test:8080"


def test_missing_websocket_extra_is_a_clear_runtime_error(monkeypatch):
    install_websocket(monkeypatch, None)
    transport = Transport()
    bot = transport.bot(token=TOKEN)
    with pytest.raises(RuntimeError, match=r"itd-chatbot-sdk\[websocket\]"):
        next(bot.websocket_events())
    with pytest.raises(RuntimeError, match=r"itd-chatbot-sdk\[websocket\]"):
        Bot._require_websocket()
    assert transport.requests == []


def test_require_websocket_passes_when_the_extra_is_installed(monkeypatch):
    install(monkeypatch, Connector())
    assert Bot._require_websocket() is None


def test_failed_handshake_is_raised_as_it_is_and_sets_no_cooldown(monkeypatch):
    error = BadStatus(403, resp_headers={"Retry-After": "30"}, resp_body=b"{}")
    connector = Connector(error=error)
    install(monkeypatch, connector)
    bot = make_bot()
    for _ in range(2):
        with pytest.raises(BadStatus) as info:
            next(bot.websocket_events())
        assert info.value is error
    assert len(connector.calls) == 2


@pytest.mark.parametrize(
    ("headers", "body", "seconds"),
    [
        ({"Retry-After": "11"}, b"{}", 11),
        ({"Retry-After": "4"}, b'{"error": {"retry_after": 20}}', 20),
        (None, None, 30),
    ],
    ids=["header", "body-wins-when-longer", "nothing-given"],
)
def test_handshake_429_becomes_a_cooldown_that_blocks_the_next_attempt(
    monkeypatch, headers, body, seconds
):
    connector = Connector(error=BadStatus(429, resp_headers=headers, resp_body=body))
    install(monkeypatch, connector)
    bot = make_bot()
    with pytest.raises(RateLimitError) as first:
        next(bot.websocket_events())
    assert first.value.retry_after_seconds == seconds
    assert not first.value.locally_blocked
    assert isinstance(first.value.__context__, BadStatus)
    with pytest.raises(RateLimitError) as second:
        next(bot.websocket_events())
    assert second.value.locally_blocked
    assert len(connector.calls) == 1


def test_handshake_answer_other_than_101_is_a_protocol_error_and_closes(monkeypatch):
    connection = FakeConnection(status=200)
    install(monkeypatch, Connector(connection))
    with pytest.raises(ProtocolError, match="handshake failed"):
        next(make_bot().websocket_events())
    assert connection.closed


def test_missing_handshake_response_is_a_protocol_error_and_closes(monkeypatch):
    connection = FakeConnection()
    connection.handshake_response = None
    install(monkeypatch, Connector(connection))
    with pytest.raises(ProtocolError, match="handshake failed"):
        next(make_bot().websocket_events())
    assert connection.closed


# --- reading frames ----------------------------------------------------------------------


def test_events_are_yielded_in_order_from_text_and_bytes_frames(monkeypatch):
    connection = FakeConnection(
        ['{"type": "a", "n": 1}', b'{"type": "b", "extra": {"k": [1]}}', None]
    )
    install(monkeypatch, Connector(connection))
    events = []
    with pytest.raises(ProtocolError, match="closed"):
        events.extend(make_bot().websocket_events())
    assert events == [{"type": "a", "n": 1}, {"type": "b", "extra": {"k": [1]}}]
    assert connection.closed


@pytest.mark.parametrize("frame", [None, "", b""], ids=["none", "empty", "empty-bytes"])
def test_closed_connection_is_a_protocol_error(monkeypatch, frame):
    connection = FakeConnection([frame])
    install(monkeypatch, Connector(connection))
    with pytest.raises(ProtocolError, match="WebSocket closed"):
        next(make_bot().websocket_events())
    assert connection.closed


def test_failure_raised_by_the_socket_passes_through_and_closes(monkeypatch):
    connection = FakeConnection([DroppedConnection("reset")])
    install(monkeypatch, Connector(connection))
    with pytest.raises(DroppedConnection):
        next(make_bot().websocket_events())
    assert connection.closed


@pytest.mark.parametrize(
    "frame",
    ["{not json", "<html>", b"\xff\xfe\xfd"],
    ids=["truncated", "html", "binary"],
)
def test_frame_that_is_not_json_is_a_protocol_error(monkeypatch, frame):
    connection = FakeConnection([frame])
    install(monkeypatch, Connector(connection))
    with pytest.raises(ProtocolError, match="Invalid WebSocket frame"):
        next(make_bot().websocket_events())
    assert connection.closed


@pytest.mark.parametrize(
    "frame",
    [
        "[]",
        "123",
        '"text"',
        "null",
        "true",
        "{}",
        '{"type": 5, "text": "private-fictional-words"}',
        '{"type": null}',
        '{"kind": "message_created", "text": "private-fictional-words"}',
    ],
    ids=[
        "list",
        "number",
        "string",
        "null",
        "bool",
        "empty",
        "int-type",
        "null-type",
        "no-type",
    ],
)
def test_json_that_is_not_an_event_is_refused_without_echoing_it(monkeypatch, frame):
    connection = FakeConnection([frame])
    install(monkeypatch, Connector(connection))
    with pytest.raises(ProtocolError, match="Invalid WebSocket event") as info:
        next(make_bot().websocket_events())
    assert "private-fictional-words" not in str(info.value)
    assert connection.closed


def test_pong_is_never_yielded_and_counts_as_a_sign_of_life(monkeypatch, clock):
    connection = FakeConnection(
        [
            after(clock, 10, "pong"),
            after(clock, 10, b"pong"),
            after(clock, 10, '{"type": "event"}'),
        ]
    )
    install(monkeypatch, Connector(connection))
    events = make_bot().websocket_events()
    assert next(events) == {"type": "event"}
    assert clock.now == 30  # 30 s without an event, but never 15 s without a frame
    events.close()
    assert connection.closed


# --- heartbeat -----------------------------------------------------------------------------


def test_silence_for_fifteen_seconds_is_a_heartbeat_timeout(monkeypatch, clock):
    connection = FakeConnection([after(clock, 6, SocketTimeout()) for _ in range(3)])
    install(monkeypatch, Connector(connection))
    with pytest.raises(ProtocolError, match="heartbeat timed out"):
        next(make_bot().websocket_events())
    assert connection.sent == ["ping", "ping"]
    assert connection.closed and connection.frames == []


def test_ping_goes_out_before_the_next_read_after_five_quiet_seconds(
    monkeypatch, clock
):
    connection = FakeConnection([after(clock, 5, SocketTimeout()), '{"type": "event"}'])
    install(monkeypatch, Connector(connection))
    events = make_bot().websocket_events()
    assert next(events)["type"] == "event"
    assert connection.sent == ["ping"]
    events.close()


def test_without_stop_the_read_waits_up_to_five_seconds(monkeypatch, clock):
    connection = FakeConnection(['{"type": "event"}'])
    install(monkeypatch, Connector(connection))
    events = make_bot().websocket_events()
    next(events)
    assert connection.timeouts == [5]
    events.close()


def test_with_stop_each_read_waits_at_most_one_second(monkeypatch, clock):
    connection = FakeConnection(['{"type": "event"}'])
    install(monkeypatch, Connector(connection))
    events = make_bot().websocket_events(stop=threading.Event())
    next(events)
    assert connection.timeouts == [1.0]
    events.close()


# --- stopping ---------------------------------------------------------------------------------


def test_stop_set_before_starting_yields_nothing_and_closes(monkeypatch, clock):
    connection = FakeConnection()
    install(monkeypatch, Connector(connection))
    stop = threading.Event()
    stop.set()
    assert list(make_bot().websocket_events(stop=stop)) == []
    assert connection.closed


def test_stop_set_while_consuming_ends_the_stream_and_closes(monkeypatch, clock):
    connection = FakeConnection(['{"type": "one"}', '{"type": "two"}'])
    install(monkeypatch, Connector(connection))
    stop = threading.Event()
    events = make_bot().websocket_events(stop=stop)
    assert next(events)["type"] == "one"
    stop.set()
    with pytest.raises(StopIteration):
        next(events)
    assert connection.closed and len(connection.frames) == 1
