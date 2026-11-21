import httpx
import pytest

from itd_bot import Bot, ProtocolError, User


def test_pinned_post_fetch_and_share():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/api/users/test":
            return httpx.Response(
                200,
                json={
                    "id": "u",
                    "pinnedPostId": "p",
                    "isPrivate": True,
                    "lastSeen": {"unit": "this_month"},
                },
            )
        if req.url.path == "/api/posts/p":
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": "p",
                        "content": "hello",
                        "author": {"id": "u"},
                        "other": 2,
                    }
                },
            )
        assert req.url.path == "/api/v1/messenger/chats/c/messages"
        import json

        assert json.loads(req.content)["entities"] == [{"type": "post", "post_id": "p"}]
        return httpx.Response(200, json={"message_id": "m"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        user = bot.get_user("test")
        assert calls == ["/api/users/test"]
        assert user.is_private is True and user.last_seen == {"unit": "this_month"}
        post = user.pinned_post
        assert (
            post.id == "p"
            and post.content == "hello"
            and post.author == {"id": "u"}
            and post.raw["other"] == 2
        )
        assert post.share("c", text="hello")["message_id"] == "m"
    assert calls == [
        "/api/users/test",
        "/api/posts/p",
        "/api/v1/messenger/chats/c/messages",
    ]


def test_no_post_or_private_field_and_post_errors():
    user = User.from_dict({"id": "u"})
    assert user.pinned_post is None and user.is_private is None
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": {"id": "wrong"}})
        )
    ) as client:
        with pytest.raises(ProtocolError):
            Bot("token", base_url="https://example.test", client=client).get_post(
                "expected"
            )


def test_follow_status_is_bot_following_users():
    import json

    def handler(req):
        assert req.url.path == "/api/users/follow-status"
        assert json.loads(req.content) == {"userIds": ["a", "b"]}
        return httpx.Response(200, json={"data": {"a": True, "b": False}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        assert bot.get_follow_status(["a", "b"]) == {"a": True, "b": False}
        assert bot.get_follow_status([]) == {}


def test_banner_download_does_not_send_authorization(monkeypatch, tmp_path):
    seen = []
    original = httpx.Client

    def client_factory(*args, **kwargs):
        assert kwargs["trust_env"] is False and kwargs["follow_redirects"] is False

        def handle(req):
            seen.append(req)
            assert "authorization" not in req.headers and "cookie" not in req.headers
            return httpx.Response(200, content=b"banner")

        return original(transport=httpx.MockTransport(handle))

    monkeypatch.setattr("itd_bot.client.httpx.Client", client_factory)
    bot = Bot(
        "secret",
        client=original(transport=httpx.MockTransport(lambda req: httpx.Response(200))),
    )
    user = User.from_dict(
        {"id": "u", "banner": {"url": "https://cdn.xn--d1ah4a.com/images/banner.png"}},
        bot=bot,
    )
    target = tmp_path / "banner.png"
    assert user.download_banner(target) == target and target.read_bytes() == b"banner"
    with pytest.raises(FileExistsError):
        user.download_banner(target)
    for url in (
        "http://cdn.xn--d1ah4a.com/x",
        "https://evil.test/x",
        "https://cdn.xn--d1ah4a.com.evil.test/x",
        "https://user:password@cdn.xn--d1ah4a.com/x",
    ):
        with pytest.raises(ValueError):
            bot.download_banner(url, tmp_path / "bad")
    assert len(seen) == 2
