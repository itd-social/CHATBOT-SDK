import json

import httpx
import pytest

from itd_bot import APIError, Bot, ErrorAction, ProtocolError, ResetRequired


def make_bot(handler):
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return Bot("secret", base_url="https://example.test", client=client), client


def test_send_and_encoding():
    def handler(req):
        assert req.url.path == "/api/v1/messenger/chats/chat/messages"
        assert req.headers["Authorization"] == "Bearer secret"
        assert json.loads(req.content) == {
            "text": "hello",
            "reply_to_id": "m",
            "client_message_id": "c",
        }
        return httpx.Response(200, json={"message_id": "m2"})

    bot, client = make_bot(handler)
    assert (
        bot.send_message("chat", "hello", reply_to_id="m", client_message_id="c")[
            "message_id"
        ]
        == "m2"
    )
    client.close()


def test_updates_and_reset():
    def handler(req):
        assert req.url.params["since"] == "7"
        assert req.url.params["capabilities"] == "chat_settings"
        return httpx.Response(
            200, json={"events": [], "reset_required": True, "next_event_seq": 8}
        )

    bot, client = make_bot(handler)
    with pytest.raises(ResetRequired) as exc:
        bot.get_updates(7)
    assert exc.value.response["next_event_seq"] == 8
    client.close()

    bot, client = make_bot(
        lambda req: httpx.Response(
            200, json={"events": [], "reset_required": True, "next_event_seq": 1}
        )
    )
    with pytest.raises(ResetRequired):
        bot.get_updates(7)
    client.close()


def test_http_error_and_redirection():
    bot, client = make_bot(
        lambda req: httpx.Response(
            429,
            json={"error": {"code": "RATE_LIMIT", "message": "wait"}},
            headers={"Retry-After": "12"},
        )
    )
    with pytest.raises(APIError) as exc:
        bot.get_chat("a")
    assert (exc.value.status, exc.value.code, exc.value.retry_after) == (
        429,
        "RATE_LIMIT",
        "12",
    )
    client.close()
    bot, client = make_bot(
        lambda req: httpx.Response(302, headers={"Location": "https://other.test"})
    )
    client.follow_redirects = True
    with pytest.raises(APIError):
        bot.get_chat("a")
    client.close()


def test_invalid_response_and_ids():
    bot, client = make_bot(
        lambda req: httpx.Response(
            200, json={"events": [], "reset_required": False, "next_event_seq": True}
        )
    )
    with pytest.raises(ProtocolError):
        bot.get_updates(0)
    with pytest.raises(ValueError):
        bot.get_chat("..")
    with pytest.raises(ValueError):
        bot.get_updates(True)
    client.close()


def test_poll_does_not_checkpoint_failed_batch(monkeypatch):
    bot, client = make_bot(
        lambda req: httpx.Response(
            200,
            json={
                "events": [{"type": "new_message"}],
                "reset_required": False,
                "next_event_seq": 1,
            },
        )
    )
    checkpoints = []

    def fail(event):
        raise RuntimeError("handler failed")

    with pytest.raises(RuntimeError):
        bot.poll(
            0,
            fail,
            on_error=lambda exc, event: ErrorAction.STOP,
            on_checkpoint=checkpoints.append,
        )
    assert checkpoints == []
    client.close()
