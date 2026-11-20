from datetime import datetime, timezone
from email.utils import format_datetime

import httpx
import pytest

from itd_bot import APIError, Bot, RateLimitError
from itd_bot.dispatcher import Dispatcher
from itd_bot.rate_limits import RateLimitState


def test_cooldown_blocks_same_method_and_path_until_deadline():
    now = [100.0]
    state = RateLimitState(monotonic=lambda: now[0])
    response = httpx.Response(
        429,
        headers={"Retry-After": "2.3", "X-RateLimit-Scope": "chat"},
        json={"error": {"code": "LIMIT", "errors": ["detail"]}},
    )
    with pytest.raises(RateLimitError) as error:
        state.observe(response, "POST", "/api/v1/messenger/chats/c/messages?x=1")
    assert isinstance(error.value, APIError)
    assert (
        error.value.retry_after,
        error.value.retry_after_seconds,
        error.value.code,
        error.value.errors,
    ) == ("2.3", 3, "LIMIT", ["detail"])
    with pytest.raises(RateLimitError) as blocked:
        state.check("POST", "/api/v1/messenger/chats/c/messages?x=2")
    assert blocked.value.locally_blocked and blocked.value.scope == "chat"
    state.check("GET", "/api/v1/messenger/chats/c/messages")
    state.check("POST", "/api/v1/messenger/chats/other/messages")
    now[0] = 103
    state.check("POST", "/api/v1/messenger/chats/c/messages")


@pytest.mark.parametrize("scope", ["ip_flood", "global_flood"])
def test_global_limit_blocks_other_routes(scope):
    state = RateLimitState(monotonic=lambda: 0)
    with pytest.raises(RateLimitError):
        state.observe(
            httpx.Response(429, headers={"X-RateLimit-Scope": scope}),
            "GET",
            "/api/users/me",
        )
    with pytest.raises(RateLimitError) as exc:
        state.check("POST", "/api/v1/messenger/chats")
    assert exc.value.retry_after_seconds == 30


def test_http_date_and_frontend_fallbacks():
    state = RateLimitState(monotonic=lambda: 0, wall_time=lambda: 1_000)
    date = format_datetime(datetime.fromtimestamp(1015, timezone.utc), usegmt=True)
    for headers, method, path, expected in [
        ({"Retry-After": date}, "GET", "/x", 15),
        ({"Retry-After": "invalid"}, "POST", "/api/v1/messenger/chats/c/messages", 2),
        ({}, "POST", "/api/v1/messenger/users/u/messages", 2),
        ({}, "GET", "/other", 30),
    ]:
        with pytest.raises(RateLimitError) as exc:
            state.observe(httpx.Response(429, headers=headers), method, path)
        assert exc.value.retry_after_seconds == expected


def test_write_not_retried_and_gate_prevents_second_request():
    calls = []

    def transport(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "10"})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        bot = Bot("fake", base_url="https://example.test", client=client)
        for local in (False, True):
            with pytest.raises(RateLimitError) as exc:
                bot.send_message("chat", "hello")
            assert exc.value.locally_blocked == local
    assert len(calls) == 1


def test_refresh_uses_same_global_gate():
    calls = []

    def transport(request):
        calls.append(request.url.path)
        return httpx.Response(
            429, headers={"X-RateLimit-Scope": "global_flood", "Retry-After": "10"}
        )

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        bot = Bot.from_refresh_token(
            "fake", base_url="https://example.test", client=client, session_path=None
        )
        with pytest.raises(RateLimitError):
            bot.get_chats()
        with pytest.raises(RateLimitError) as exc:
            bot.get_chats()
        assert exc.value.locally_blocked
    assert calls == ["/api/v1/auth/refresh"]


def test_poll_retries_fetch_but_never_replays_handler(monkeypatch):
    bot = Bot("fake")
    calls = []
    sleeps = []

    def updates(cursor):
        calls.append(cursor)
        if len(calls) == 1:
            raise RateLimitError(retry_after_seconds=3)
        return {"events": [{"id": 1}], "next_event_seq": 2}

    def handler(event):
        raise RateLimitError(retry_after_seconds=4)

    monkeypatch.setattr(bot, "get_updates", updates)
    monkeypatch.setattr("itd_bot.client.time.sleep", sleeps.append)
    with pytest.raises(RateLimitError) as exc:
        bot.poll(1, handler)
    assert exc.value.retry_after_seconds == 4
    assert calls == [1, 1] and sleeps == [3]
    bot.close()


def test_typing_throttled_per_chat():
    class FakeBot:
        def __init__(self):
            self.calls = []

        def send_action(self, chat, action):
            self.calls.append((chat, action))

    bot = FakeBot()
    router = Dispatcher(bot, self_user_id="self")
    for chat in ("a", "a", "b"):
        with router._typing(chat):
            pass
    assert bot.calls == [("a", "typing"), ("b", "typing")]
