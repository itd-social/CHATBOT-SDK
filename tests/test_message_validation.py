"""Optional client limits leave raw requests and server error policy intact."""

import copy
import json
import runpy
from pathlib import Path

import httpx
import pytest
from helpers import FAR_FUTURE, Transport, jwt, sent_json

from itd_bot import (
    APIError,
    MessageContext,
    MessageValidationError,
    RateLimitError,
    SentMessage,
    Text,
)

OPERATIONS = ["send_message", "send_message_to_user", "edit_message"]


def invoke(bot, operation, text, **options):
    args = (
        ("target", "message-1", text)
        if operation == "edit_message"
        else ("target", text)
    )
    return getattr(bot, operation)(*args, **options)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("option", [{}, {"validate_limits": False}])
@pytest.mark.parametrize(
    "text,entities",
    [
        ("x" * 4097, None),
        ("hello", [{"type": "bold", "offset": 0, "length": 1}] * 51),
        ("hello", [{"type": "sticker", "sticker_id": "fictional", "future": True}]),
    ],
)
def test_default_and_false_preserve_raw_payloads(operation, option, text, entities):
    response = {"message_id": "sent-1", "future": {"kept": True}}
    transport = Transport(httpx.Response(200, json=response))
    result = invoke(transport.bot(), operation, text, entities=entities, **option)
    body = sent_json(transport.requests[0])
    assert type(result) is dict and result == response
    assert body["text"] == text
    if entities is not None:
        assert body["entities"] == entities
    assert "validate_limits" not in body and len(transport.requests) == 1


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "text",
    ["", "x" * 4096, "я" * 4096, "🙂" * 2048, "🙂" * 2047 + "ab", "e\u0301" * 2048],
)
def test_enabled_accepts_utf16_boundary_without_truncation(operation, text):
    transport = Transport(httpx.Response(200, json={}))
    invoke(transport.bot(), operation, text, validate_limits=True)
    request = transport.requests[0]
    assert sent_json(request)["text"] == text
    assert request.method == ("PATCH" if operation == "edit_message" else "POST")
    expected = (
        "/users/target/messages"
        if operation == "send_message_to_user"
        else "/chats/target/messages"
    )
    if operation == "edit_message":
        expected += "/message-1"
    assert request.url.path.endswith(expected)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "text", ["x" * 4097, "🙂" * 2048 + "x", "🙂" * 4096, "\ud800" * 4097]
)
def test_enabled_rejects_oversized_text_before_http_and_id_generation(
    monkeypatch, operation, text, caplog
):
    monkeypatch.setattr(
        "itd_bot.client._uuid7", lambda: pytest.fail("ID generated before validation")
    )
    transport = Transport()
    with pytest.raises(MessageValidationError, match="4096.*UTF-16") as caught:
        invoke(transport.bot(), operation, text, validate_limits=True)
    assert isinstance(caught.value, ValueError) and not transport.requests
    assert not caplog.text and text not in str(caught.value)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "entities",
    [
        None,
        [],
        [
            "future",
            None,
            {"type": "future", "sticker_id": "not-a-sticker", "extra": {"x": 1}},
        ],
        [{"type": "bold", "offset": 0, "length": 1, "future": True}] * 50,
    ],
)
def test_enabled_only_checks_limits_and_preserves_entities(operation, entities):
    original = copy.deepcopy(entities)
    transport = Transport(httpx.Response(200, json={}))
    invoke(
        transport.bot(),
        operation,
        "x",
        entities=entities,
        attachments=["file-1"],
        validate_limits=True,
    )
    body = sent_json(transport.requests[0])
    assert entities == original and body["attachments"] == ["file-1"]
    if entities is None or (not entities and operation != "edit_message"):
        assert "entities" not in body
    else:
        assert body["entities"] == entities


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize(
    "entities,match",
    [
        ([{"type": "bold"}] * 51, "50"),
        ([{"type": "sticker", "sticker_id": "fictional-private-id"}], "issues/17"),
    ],
)
def test_enabled_rejects_entities_without_http_or_payload_disclosure(
    operation, entities, match, caplog
):
    transport = Transport()
    with pytest.raises(ValueError, match=match) as caught:
        invoke(
            transport.bot(),
            operation,
            "fictional-private-text",
            entities=entities,
            validate_limits=True,
        )
    assert type(caught.value) is MessageValidationError
    assert not transport.requests and not caplog.text
    assert "fictional-private" not in str(caught.value)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_enabled_keeps_server_error_types_and_never_blindly_retries(operation, status):
    transport = Transport(httpx.Response(status, json={}))
    expected = RateLimitError if status == 429 else APIError
    with pytest.raises(expected):
        invoke(transport.bot(get_retries=10), operation, "hello", validate_limits=True)
    assert len(transport.requests) == 1


@pytest.mark.parametrize("operation", OPERATIONS)
def test_enabled_does_not_retry_transport_failure(operation):
    failure = httpx.ReadTimeout("fictional failure")
    transport = Transport(failure)
    with pytest.raises(httpx.ReadTimeout) as caught:
        invoke(transport.bot(get_retries=10), operation, "hello", validate_limits=True)
    assert caught.value is failure and len(transport.requests) == 1


@pytest.mark.parametrize("operation", OPERATIONS)
def test_enabled_keeps_existing_401_refresh_rotation_and_request_body(
    operation, tmp_path
):
    transport = Transport(
        httpx.Response(401, json={}),
        httpx.Response(
            200,
            json={"accessToken": jwt("self", FAR_FUTURE + 60)},
            headers={
                "set-cookie": "refresh_token=fictional-rotated; Path=/api/v1/auth; HttpOnly"
            },
        ),
        httpx.Response(200, json={"message_id": "sent-1"}),
    )
    session = tmp_path / "session.json"
    bot = transport.bot(
        token=jwt("self"), refresh_token="fictional-seed", session_path=session
    )
    invoke(bot, operation, "hello", validate_limits=True)
    assert len(transport.requests) == 3 and bot.refresh_count == 1
    assert sent_json(transport.requests[0]) == sent_json(transport.requests[2])
    assert transport.paths[1].endswith("/auth/refresh")
    assert (
        json.loads(session.read_text(encoding="utf-8"))["refresh_token"]
        == "fictional-rotated"
    )


@pytest.mark.parametrize(
    "operation", ["send_formatted", "send_formatted_to_user", "reply", "edit"]
)
def test_wrappers_pass_the_option_to_the_underlying_operation(operation):
    transport = Transport()
    bot = transport.bot()
    with pytest.raises(MessageValidationError):
        if operation.startswith("send_formatted"):
            getattr(bot, operation)("target", Text("x" * 4097), validate_limits=True)
        elif operation == "reply":
            MessageContext(
                bot, {"chat_id": "chat-1"}, {"message_id": "message-1"}
            ).reply("x" * 4097, validate_limits=True)
        else:
            SentMessage(bot, "chat-1", {"message_id": "message-1"}).edit(
                "x" * 4097, validate_limits=True
            )
    assert not transport.requests


def test_validation_does_not_copy_or_inspect_the_response(monkeypatch):
    bot = Transport().bot()
    response = {"unknown": object()}
    monkeypatch.setattr(bot, "request", lambda *args, **kwargs: response)
    assert bot.send_message("chat-1", "hello", validate_limits=True) is response


def test_error_has_the_documented_import_path_and_value_error_parent():
    from itd_bot.errors import MessageValidationError as Error

    assert Error is MessageValidationError and issubclass(Error, ValueError)


def test_example_sends_valid_text_and_explains_local_failure_without_private_text(
    capsys,
):
    example = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/message_limits.py")
    )
    transport = Transport(httpx.Response(201, json={"message_id": "sent-1"}))
    bot = transport.bot()
    assert example["send_checked"](bot, "chat-1", "hello") == {"message_id": "sent-1"}
    assert (
        example["send_checked"](bot, "chat-1", "fictional-private-text" * 4096) is None
    )
    assert len(transport.requests) == 1
    output = capsys.readouterr().out
    assert "4096" in output and "fictional-private-text" not in output
