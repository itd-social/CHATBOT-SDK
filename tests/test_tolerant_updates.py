"""Opt-in schema tolerance keeps raw data and never invents a cursor."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from helpers import BASE, FAR_FUTURE, Transport, jwt, strict_limiter

from itd_bot import APIError, Bot, ChatType, EntityType, ProtocolError, ResetRequired


def batch(events=(), seq=8, **fields):
    return {
        "events": list(events),
        "reset_required": False,
        "next_event_seq": seq,
        **fields,
    }


def test_unknown_events_fields_and_chat_type_are_not_changed():
    event = {"type": "future_event", "chat_type": 73, "future": {"items": [1]}}
    payload = batch([event], future_field="kept")
    transport = Transport(httpx.Response(200, json=payload))
    result = transport.bot(on_protocol_error="skip").get_updates(7)
    assert result == payload and type(result) is dict
    assert type(result["events"][0]["chat_type"]) is int
    assert ChatType(result["events"][0]["chat_type"]).name == "UNKNOWN"


def test_skip_retains_valid_events_and_only_saves_bad_fragments(caplog):
    event = {"type": "future", "message": {"private": "fictional-private-text"}}
    invalid = {"type": 17, "private": "fictional-secret"}
    payload = batch([None, event, invalid, "unexpected"], extension={"kept": True})
    transport = Transport(httpx.Response(200, json=payload))
    bot = transport.bot(on_protocol_error="skip")
    assert bot.get_updates(7) == batch([event], extension={"kept": True})
    assert bot.protocol_errors == (None, invalid, "unexpected")
    assert len(caplog.records) == 3
    assert "fictional-private-text" not in caplog.text
    assert "fictional-secret" not in caplog.text


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "unexpected",
        {},
        batch(events=(), events_changed=True) | {"events": {}},
        batch() | {"reset_required": "false"},
        batch() | {"next_event_seq": True},
        batch() | {"next_event_seq": -1},
        batch() | {"next_event_seq": "8"},
        batch() | {"next_event_seq": 8.5},
        batch(seq=6),
    ],
)
def test_bad_envelope_keeps_cursor_and_raw_fragment(payload):
    transport = Transport(httpx.Response(200, json=payload))
    bot = transport.bot(on_protocol_error="skip")
    assert bot.get_updates(7, limit=10) == batch(seq=7)
    assert bot.protocol_errors == (payload,)
    assert transport.requests[0].url.params["since"] == "7"
    assert transport.requests[0].url.params["limit"] == "10"


@pytest.mark.parametrize("mode", ["raise", "skip"])
@pytest.mark.parametrize(
    "payload",
    [batch(seq=1, reset_required=True), {"reset_required": True, "events": None}],
)
def test_reset_required_is_never_hidden(mode, payload):
    transport = Transport(httpx.Response(200, json=payload))
    bot = transport.bot(on_protocol_error=mode)
    with pytest.raises(ResetRequired) as caught:
        bot.get_updates(7)
    assert caught.value.response == payload and bot.protocol_errors == ()


def test_default_still_raises_on_a_bad_event():
    bot = Transport(httpx.Response(200, json=batch([None]))).bot()
    with pytest.raises(ProtocolError, match="Invalid updates response"):
        bot.get_updates(7)
    assert bot.protocol_errors == ()


@pytest.mark.parametrize("mode", ["raise", "skip"])
def test_invalid_json_is_kept_as_bytes_only_when_opted_in(mode, caplog):
    raw = b"not-json-fictional-private-text"
    bot = Transport(httpx.Response(200, content=raw)).bot(on_protocol_error=mode)
    if mode == "skip":
        assert bot.get_updates(7) == batch(seq=7)
        assert bot.protocol_errors == (raw,)
    else:
        with pytest.raises(ProtocolError, match="Expected JSON response"):
            bot.get_updates(7)
        assert bot.protocol_errors == ()
    assert raw.decode() not in caplog.text


def test_buffer_is_bounded_and_cannot_be_mutated_through_its_snapshots(monkeypatch):
    bot = Transport().bot(on_protocol_error="skip")
    payload = {"private": [0]}
    monkeypatch.setattr(bot, "request", lambda *a, **kw: payload)
    for number in range(35):
        payload["private"][0] = number
        bot.get_updates(7)
    snapshots = bot.protocol_errors
    assert len(snapshots) == 32
    assert snapshots[0] == {"private": [3]} and snapshots[-1] == {"private": [34]}
    snapshots[0]["private"].clear()
    payload.clear()
    assert bot.protocol_errors[0] == {"private": [3]}


def test_buffer_supports_concurrent_update_readers():
    def answer(request):
        return httpx.Response(200, json={"bad": int(request.url.params["since"])})

    bot = Transport(default=answer).bot(on_protocol_error="skip")
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(bot.get_updates, range(50)))
    assert [item["next_event_seq"] for item in results] == list(range(50))
    assert len(bot.protocol_errors) == 32
    assert len({item["bad"] for item in bot.protocol_errors}) == 32


def test_poll_continues_after_bad_response_without_advancing_past_it(monkeypatch):
    event = {"type": "future_event", "chat_type": 73}
    transport = Transport(
        httpx.Response(200, json=[]), httpx.Response(200, json=batch([event]))
    )
    bot = transport.bot(on_protocol_error="skip")
    stop = threading.Event()
    seen, checkpoints, pauses = [], [], []

    def handle(item):
        seen.append(item)
        stop.set()

    monkeypatch.setattr(
        "itd_bot.client.pause", lambda stopped, delay: pauses.append(delay) or False
    )
    bot.poll(7, handle, on_checkpoint=checkpoints.append, stop=stop)
    assert seen == [event] and checkpoints == [7, 8] and pauses == [2.0]
    assert [request.url.params["since"] for request in transport.requests] == ["7", "7"]


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_http_errors_are_not_swallowed(status):
    bot = Transport(httpx.Response(status, json={})).bot(
        on_protocol_error="skip", get_retries=0, limiter=strict_limiter()
    )
    with pytest.raises(APIError):
        bot.get_updates(7)
    assert bot.protocol_errors == ()


def test_network_error_is_not_a_protocol_error():
    failure = httpx.ReadError("fictional failure")
    transport = Transport(failure)
    bot = transport.bot(on_protocol_error="skip", get_retries=0)
    with pytest.raises(httpx.ReadError) as caught:
        bot.get_updates(7)
    assert caught.value is failure and bot.protocol_errors == ()


def test_refresh_after_401_still_precedes_protocol_parsing():
    payload = batch([{"type": "future"}])
    transport = Transport(
        httpx.Response(401, json={}),
        httpx.Response(200, json={"accessToken": jwt("self", FAR_FUTURE + 60)}),
        httpx.Response(200, json=payload),
    )
    with transport.client() as client:
        bot = Bot(
            jwt("self"),
            refresh_token="fictional-refresh",
            session_path=None,
            client=client,
            base_url=BASE,
            on_protocol_error="skip",
        )
        assert bot.get_updates(7) == payload and bot.refresh_count == 1
    assert transport.paths == [
        "/api/v1/messenger/updates",
        "/api/v1/auth/refresh",
        "/api/v1/messenger/updates",
    ]


@pytest.mark.parametrize("value", ["ignore", "", None, 17])
def test_invalid_policy_is_rejected_before_network(value):
    with pytest.raises(ValueError, match="on_protocol_error"):
        Transport().bot(on_protocol_error=value)


@pytest.mark.parametrize("value", [73, 999999, -7])
def test_unknown_chat_enum_retains_integer_without_caching(value):
    size = len(ChatType._value2member_map_)
    item = ChatType(value)
    assert item == value and item.value == value and item.name == "UNKNOWN"
    assert json.dumps({"chat_type": item}) == json.dumps({"chat_type": value})
    assert len(ChatType._value2member_map_) == size


@pytest.mark.parametrize("value", ["future_entity", "", "CUSTOM"])
def test_unknown_entity_enum_retains_string_without_caching(value):
    size = len(EntityType._value2member_map_)
    item = EntityType(value)
    assert item == value and str(item) == value and item.name == "UNKNOWN"
    assert json.dumps({"type": item}) == json.dumps({"type": value})
    assert len(EntityType._value2member_map_) == size


def test_known_enums_compare_with_existing_raw_values():
    assert ChatType(0) is ChatType.DIRECT and ChatType.DIRECT == 0
    assert EntityType("bold") is EntityType.BOLD and EntityType.BOLD == "bold"
    for cls, value in ((ChatType, "73"), (EntityType, 17)):
        with pytest.raises(ValueError):
            cls(value)
