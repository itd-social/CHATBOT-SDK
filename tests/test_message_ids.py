"""UUIDv7 send IDs and server replay metadata, without live requests."""

import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from helpers import FAR_FUTURE, Transport, jwt, sent_json, strict_limiter

from itd_bot import APIError, StaleClientMessageIdError, Text
from itd_bot.message_ids import _uuid7

STALE = "client_message_id is stale or clock is incorrect"


def test_uuid7_matches_rfc9562_appendix_a6(monkeypatch):
    expected = uuid.UUID("017f22e2-79b0-7cc3-98c4-dc0c0c07398f")
    timestamp = 0x017F22E279B0
    random = (0xCC3 << 62) | 0x18C4DC0C0C07398F
    monkeypatch.setattr(
        "itd_bot.message_ids.time.time_ns", lambda: timestamp * 1_000_000
    )
    monkeypatch.setattr(
        "itd_bot.message_ids.secrets.randbits",
        lambda n: random if n == 74 else pytest.fail("wrong entropy size"),
    )
    assert _uuid7() == str(expected)


@pytest.mark.parametrize("timestamp", [0, 1, (1 << 48) - 1])
def test_uuid7_preserves_milliseconds_version_and_variant(monkeypatch, timestamp):
    monkeypatch.setattr(
        "itd_bot.message_ids.time.time_ns", lambda: timestamp * 1_000_000 + 999999
    )
    value = uuid.UUID(_uuid7())
    assert value.int >> 80 == timestamp
    assert value.version == 7 and value.variant == uuid.RFC_4122


@pytest.mark.parametrize("timestamp", [-1, 1 << 48])
def test_unrepresentable_clock_fails_before_sending_but_explicit_id_still_works(
    monkeypatch, timestamp
):
    monkeypatch.setattr(
        "itd_bot.message_ids.time.time_ns", lambda: timestamp * 1_000_000
    )
    transport = Transport(httpx.Response(200, json={"message_id": "m"}))
    bot = transport.bot()
    with pytest.raises(ValueError, match="System time"):
        bot.send_message("chat", "hello")
    assert transport.requests == []
    bot.send_message("chat", "hello", client_message_id="legacy-explicit")
    assert sent_json(transport.requests[0])["client_message_id"] == "legacy-explicit"


def test_ids_are_distinct_across_threads_in_one_millisecond(monkeypatch):
    monkeypatch.setattr("itd_bot.message_ids.time.time_ns", lambda: 1700000000000000000)
    with ThreadPoolExecutor(8) as pool:
        values = list(pool.map(lambda _: _uuid7(), range(1000)))
    assert len(set(values)) == 1000
    assert {uuid.UUID(value).int >> 80 for value in values} == {1700000000000}


def test_clock_moving_backwards_does_not_get_hidden(monkeypatch):
    times = iter([1700000000000000000, 1699999999000000000])
    monkeypatch.setattr("itd_bot.message_ids.time.time_ns", lambda: next(times))
    first, second = uuid.UUID(_uuid7()), uuid.UUID(_uuid7())
    assert first.int >> 80 == 1700000000000 and second.int >> 80 == 1699999999000


@pytest.mark.parametrize("method", ["send_message", "send_message_to_user"])
def test_missing_id_is_generated_once_per_send(method):
    transport = Transport(default=httpx.Response(201, json={"message_id": "m"}))
    bot = transport.bot()
    assert getattr(bot, method)("target", "hello") == {"message_id": "m"}
    getattr(bot, method)("target", "hello")
    values = [sent_json(request)["client_message_id"] for request in transport.requests]
    assert values[0] != values[1]
    assert all(uuid.UUID(value).version == 7 for value in values)


@pytest.mark.parametrize("method", ["send_message", "send_message_to_user"])
@pytest.mark.parametrize(
    "explicit", ["legacy-non-uuid", "", "5b570a0d-17d3-42b3-88cf-ce41b2af9d46"]
)
def test_explicit_ids_keep_their_previous_wire_form(monkeypatch, method, explicit):
    monkeypatch.setattr(
        "itd_bot.client._uuid7", lambda: pytest.fail("explicit ID was replaced")
    )
    transport = Transport(httpx.Response(201, json={"message_id": "m"}))
    getattr(transport.bot(), method)("target", "hello", client_message_id=explicit)
    expected = (
        {"text": "hello", "client_message_id": explicit}
        if explicit
        else {"text": "hello"}
    )
    if method == "send_message":
        expected["reply_to_id"] = None
    assert sent_json(transport.requests[0]) == expected


@pytest.mark.parametrize("replayed", [True, False])
def test_replayed_and_unknown_response_fields_remain_in_raw_dict(replayed):
    response = {"message_id": "m", "replayed": replayed, "future": {"kept": True}}
    result = (
        Transport(httpx.Response(201, json=response))
        .bot()
        .send_message("chat", "hello")
    )
    assert type(result) is dict and result == response


def test_401_refresh_reuses_the_same_generated_id():
    transport = Transport(
        httpx.Response(401, json={}),
        httpx.Response(200, json={"accessToken": jwt("self", FAR_FUTURE + 60)}),
        httpx.Response(201, json={"message_id": "m"}),
    )
    bot = transport.bot(
        token=jwt("self"), refresh_token="fictional-refresh", session_path=None
    )
    bot.send_message("chat", "hello")
    assert sent_json(transport.requests[0]) == sent_json(transport.requests[2])
    assert uuid.UUID(sent_json(transport.requests[0])["client_message_id"]).version == 7
    assert bot.refresh_count == 1


@pytest.mark.parametrize("status", [400, 429, 500, 503])
def test_write_errors_are_not_retried_even_with_generated_id(status):
    transport = Transport(httpx.Response(status, json={}))
    with pytest.raises(APIError):
        transport.bot(get_retries=10, limiter=strict_limiter()).send_message(
            "chat", "hello"
        )
    assert len(transport.requests) == 1


def test_network_failure_does_not_repeat_the_write():
    failure = httpx.ReadTimeout("fictional failure")
    transport = Transport(failure)
    with pytest.raises(httpx.ReadTimeout) as caught:
        transport.bot(get_retries=10).send_message_to_user("target", "hello")
    assert caught.value is failure and len(transport.requests) == 1


def test_file_id_is_generated_after_upload_has_finished(monkeypatch):
    transport = Transport(
        httpx.Response(200, json={"uploadId": "upload"}),
        httpx.Response(200, json={}),
        httpx.Response(200, json={"fileId": "file"}),
        httpx.Response(201, json={"message_id": "m"}),
    )

    def generated():
        assert len(transport.requests) == 3
        return "018bcfe5-6800-7000-8000-000000000001"

    monkeypatch.setattr("itd_bot.client._uuid7", generated)
    transport.bot().send_file("chat", b"data", filename="example.txt")
    assert sent_json(transport.requests[-1]) == {
        "text": "",
        "reply_to_id": None,
        "attachments": ["file"],
        "client_message_id": "018bcfe5-6800-7000-8000-000000000001",
    }


@pytest.mark.parametrize("method", ["send_formatted", "send_formatted_to_user"])
def test_formatted_wrappers_use_automatic_ids(method):
    transport = Transport(httpx.Response(201, json={"message_id": "m"}))
    getattr(transport.bot(), method)("target", Text().bold("hello"))
    body = sent_json(transport.requests[0])
    assert uuid.UUID(body["client_message_id"]).version == 7
    assert body["entities"] == [{"type": "bold", "offset": 0, "length": 5}]


@pytest.mark.parametrize("field", ["message", "detail"])
@pytest.mark.parametrize("wrapped", [False, True])
def test_stale_error_is_apierror_with_original_metadata(field, wrapped):
    detail = {
        field: STALE,
        "code": "BAD_REQUEST",
        "errors": {"field": "client_message_id"},
    }
    response = {"error": detail} if wrapped else detail
    transport = Transport(httpx.Response(400, json=response))
    with pytest.raises(StaleClientMessageIdError) as caught:
        transport.bot().send_message("chat", "hello")
    error = caught.value
    assert isinstance(error, APIError) and error.status == 400
    assert str(error) == STALE and error.code == "BAD_REQUEST"
    assert error.response == response and error.errors == detail["errors"]
    assert len(transport.requests) == 1


@pytest.mark.parametrize("message", ["other error", STALE + " (future detail)"])
def test_other_errors_are_not_guessed(message):
    transport = Transport(httpx.Response(400, json={"message": message}))
    with pytest.raises(APIError) as caught:
        transport.bot().send_message("chat", "hello")
    assert type(caught.value) is APIError
