"""Server error codes keep the old APIError contract and the raw JSON."""

import httpx
import pytest
from helpers import BASE, Transport

from itd_bot import (
    APIError,
    BlockedError,
    Bot,
    MessagesClosedError,
    NukstaRequiredError,
    RateLimitError,
)

ERRORS = [
    ("nuksta_required", NukstaRequiredError),
    ("blocked", BlockedError),
    ("messages_closed", MessagesClosedError),
]
FILE_ID = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize(("code", "expected"), ERRORS)
@pytest.mark.parametrize("wrapped", [False, True], ids=["flat", "wrapped"])
@pytest.mark.parametrize("operation", ["send", "get", "download", "refresh"])
def test_known_code_preserves_api_error_and_response(
    tmp_path, caplog, code, expected, wrapped, operation
):
    detail = {"code": code, "message": "Denied", "errors": ["field"]}
    payload = {"error": detail, "private": "FICTIONAL-PRIVATE"} if wrapped else detail
    response = httpx.Response(403, headers={"Retry-After": "7"}, json=payload)
    script = []
    if operation == "download":
        script = [
            httpx.Response(
                200,
                json={
                    "links": [{"fileId": FILE_ID, "url": f"/v1/chat-files/{FILE_ID}"}]
                },
            ),
            httpx.Response(204),
        ]
    transport = Transport(*script, response)
    target = tmp_path / "download.bin"
    with transport.client() as client:
        if operation == "refresh":
            bot = Bot.from_refresh_token(
                "fictional-refresh", base_url=BASE, client=client, session_path=None
            )
        else:
            bot = Bot("fictional-access", base_url=BASE, client=client, get_retries=4)
        with pytest.raises(APIError) as caught:
            if operation == "send":
                bot.send_message("chat-1", "hello")
            elif operation == "download":
                bot.download_file("chat-1", FILE_ID, destination=target)
            else:
                bot.get_unread_count()
    error = caught.value
    assert type(error) is expected
    assert (error.status, error.code, error.errors, error.retry_after) == (
        403,
        code,
        ["field"],
        "7",
    )
    assert error.response == payload
    assert str(error) == (
        "Session refresh failed" if operation == "refresh" else "Denied"
    )
    assert len(transport.requests) == (3 if operation == "download" else 1)
    assert not target.exists()
    assert "FICTIONAL-PRIVATE" not in caplog.text


@pytest.mark.parametrize("code", ["future_error", None, 42, {}, [], True])
def test_unknown_or_unexpected_code_keeps_plain_api_error(code):
    payload = {"error": {"code": code, "message": "Denied"}, "unknown": [1, 2]}
    transport = Transport(httpx.Response(403, json=payload))
    with transport.client() as client:
        bot = Bot("fictional-access", base_url=BASE, client=client)
        with pytest.raises(APIError) as caught:
            bot.send_message("chat-1", "hello")
    assert type(caught.value) is APIError
    assert caught.value.code == code
    assert caught.value.response == payload
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "payload", [None, ["unexpected"], {"error": []}, {"error": "text"}]
)
def test_unexpected_json_error_body_is_preserved(payload):
    transport = Transport(httpx.Response(400, json=payload))
    with transport.client() as client:
        bot = Bot("fictional-access", base_url=BASE, client=client)
        with pytest.raises(APIError) as caught:
            bot.send_message("chat-1", "hello")
    assert type(caught.value) is APIError
    assert caught.value.code is None
    assert caught.value.response == payload


def test_non_json_error_response_has_no_raw_json():
    transport = Transport(httpx.Response(403, content=b"not-json"))
    with transport.client() as client:
        bot = Bot("fictional-access", base_url=BASE, client=client)
        with pytest.raises(APIError) as caught:
            bot.send_message("chat-1", "hello")
    assert type(caught.value) is APIError and caught.value.response is None


@pytest.mark.parametrize(("code", "unused"), ERRORS)
def test_429_keeps_rate_limit_error_priority(code, unused):
    transport = Transport(httpx.Response(429, json={"error": {"code": code}}))
    with transport.client() as client:
        bot = Bot("fictional-access", base_url=BASE, client=client)
        with pytest.raises(RateLimitError) as caught:
            bot.send_message("chat-1", "hello")
    assert caught.value.code == code
    assert len(transport.requests) == 1


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_transport_failure_is_not_converted_or_retried(error_type):
    failure = error_type("fictional failure")
    transport = Transport(failure)
    with transport.client() as client:
        bot = Bot("fictional-access", base_url=BASE, client=client, get_retries=4)
        with pytest.raises(error_type) as caught:
            bot.send_message("chat-1", "hello")
    assert caught.value is failure and len(transport.requests) == 1


def test_success_with_an_error_named_field_is_untouched():
    payload = {"error": {"code": "nuksta_required"}, "unknown": True}
    transport = Transport(httpx.Response(200, json=payload))
    with transport.client() as client:
        bot = Bot("fictional-access", base_url=BASE, client=client)
        assert bot.send_message("chat-1", "hello") == payload


def test_existing_api_error_import_paths_are_preserved():
    from itd_bot.auth import APIError as AuthAPIError
    from itd_bot.client import APIError as ClientAPIError

    assert AuthAPIError is ClientAPIError is APIError
