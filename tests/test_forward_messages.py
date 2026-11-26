"""Forwarding keeps both client ID forms and never guesses a safe retry."""

from copy import deepcopy

import httpx
import pytest
from helpers import API, BASE, Transport, sent_json

from itd_bot import APIError, Bot, ProtocolError


@pytest.fixture(
    params=[
        ["client-1", "client-2"],
        {"msg-2": "client-2", "msg-1": "client-1"},
    ],
    ids=["list", "mapping"],
)
def client_ids(request):
    return deepcopy(request.param)


def test_forward_preserves_client_ids_and_raw_response(client_ids):
    response = {"messages": [{"message_id": "forwarded-1", "unknown": True}]}
    transport = Transport(httpx.Response(200, json=response))
    original = deepcopy(client_ids)
    message_ids = ["msg-1", "msg-2"]
    with transport.client() as client:
        bot = Bot("fictional-token", base_url=BASE, client=client)
        result = bot.forward_messages(
            "target", "source", message_ids, client_message_ids=client_ids
        )
    assert isinstance(result, dict) and result == response
    (request,) = transport.requests
    assert request.method == "POST"
    assert request.url.path == API + "/chats/target/messages/forward"
    assert sent_json(request) == {
        "source_chat_id": "source",
        "message_ids": ["msg-1", "msg-2"],
        "client_message_ids": original,
    }
    assert client_ids == original and message_ids == ["msg-1", "msg-2"]


def test_forward_empty_mapping_is_omitted():
    transport = Transport(httpx.Response(200, json={}))
    with transport.client() as client:
        bot = Bot("fictional-token", base_url=BASE, client=client)
        assert (
            bot.forward_messages("target", "source", ["msg-1"], client_message_ids={})
            == {}
        )
    (request,) = transport.requests
    assert sent_json(request) == {
        "source_chat_id": "source",
        "message_ids": ["msg-1"],
    }


@pytest.mark.parametrize("status", [401, 403, 429, 500, 502, 503, 504])
def test_forward_http_failure_does_not_retry_or_change_format(client_ids, status):
    transport = Transport(
        httpx.Response(status, json={"error": {"code": "FORWARD_FAILED"}})
    )
    with transport.client() as client:
        bot = Bot("fictional-token", base_url=BASE, client=client, get_retries=4)
        with pytest.raises(APIError) as caught:
            bot.forward_messages(
                "target", "source", ["msg-1", "msg-2"], client_message_ids=client_ids
            )
    assert caught.value.status == status and caught.value.code == "FORWARD_FAILED"
    (request,) = transport.requests
    assert sent_json(request)["client_message_ids"] == client_ids


@pytest.mark.parametrize("error_type", [httpx.ConnectError, httpx.ReadTimeout])
def test_forward_network_failure_does_not_retry_or_change_format(
    client_ids, error_type
):
    failure = error_type("fictional network failure")
    transport = Transport(failure)
    with transport.client() as client:
        bot = Bot("fictional-token", base_url=BASE, client=client, get_retries=4)
        with pytest.raises(error_type) as caught:
            bot.forward_messages(
                "target", "source", ["msg-1", "msg-2"], client_message_ids=client_ids
            )
    assert caught.value is failure
    (request,) = transport.requests
    assert sent_json(request)["client_message_ids"] == client_ids


def test_forward_invalid_response_does_not_retry(client_ids):
    transport = Transport(httpx.Response(200, content=b"not-json"))
    with transport.client() as client:
        bot = Bot("fictional-token", base_url=BASE, client=client)
        with pytest.raises(ProtocolError, match="Expected JSON"):
            bot.forward_messages(
                "target", "source", ["msg-1", "msg-2"], client_message_ids=client_ids
            )
    assert len(transport.requests) == 1
