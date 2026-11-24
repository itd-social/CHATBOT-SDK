import httpx
import pytest

from itd_bot import APIError, Bot, Dispatcher, ProtocolError, User

PROFILE = {
    "id": "user-123",
    "username": "example",
    "displayName": "Example",
    "avatar": "avatar",
    "banner": "banner",
    "bio": "about",
    "verified": False,
    "isPrivate": True,
    "pin": {"slug": "badge", "name": "Badge"},
    "hasNuksta": True,
    "pinnedPostId": "post-123",
    "wallAccess": "nobody",
    "likesVisibility": "mutual",
    "followersCount": 11192,
    "followingCount": 19,
    "postsCount": 573,
    "isFollowing": False,
    "isFollowedBy": False,
    "createdAt": "2025-12-01T12:00:00.000Z",
    "canMessage": False,
    "online": True,
    "lastSeen": {"unit": "this_month"},
    "newField": {"value": 5},
}


def test_user_fields_and_full_response():
    calls = []

    def handler(request):
        calls.append(request.url.raw_path)
        return httpx.Response(200, json=PROFILE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        user = bot.get_user("@example")
        assert isinstance(user, User)
        assert (
            user.id,
            user.username,
            user.display_name,
            user.avatar,
            user.banner,
            user.bio,
        ) == ("user-123", "example", "Example", "avatar", "banner", "about")
        assert (user.is_verified, user.pin, user.has_nuksta, user.pinned_post_id) == (
            False,
            PROFILE["pin"],
            True,
            "post-123",
        )
        assert (
            user.wall_access,
            user.likes_visibility,
            user.followers_count,
            user.following_count,
            user.posts_count,
        ) == ("nobody", "mutual", 11192, 19, 573)
        assert (
            user.is_following,
            user.is_followed_by,
            user.created_at,
            user.can_message,
            user.online,
            user.last_seen,
        ) == (False, False, PROFILE["createdAt"], False, True, {"unit": "this_month"})
        assert user.raw == PROFILE and user.raw["newField"] == {"value": 5}
        assert bot.get_user("user-123").id == "user-123"
        assert bot.get_me().id == "user-123"
    assert calls == [b"/api/users/example", b"/api/users/user-123", b"/api/users/me"]


def test_sender_uses_message_sender_and_fetches_only_when_requested():
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/read"):
            return httpx.Response(204)
        return httpx.Response(200, json=PROFILE)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router = Dispatcher(
            Bot("token", base_url="https://example.test", client=client),
            self_user_id="bot",
            auto_typing=False,
        )
        users = []

        @router.command("start")
        def start(ctx):
            users.append(ctx.get_sender())

        router.handle(
            {
                "type": "new_message",
                "chat_id": "group",
                "message": {
                    "sender_id": "user-123",
                    "message_id": "m",
                    "text": "/start",
                },
            }
        )
        assert users[0].display_name == "Example"
    assert requests == ["/api/v1/messenger/chats/group/read", "/api/users/user-123"]


def test_partial_profile_invalid_response_and_http_errors():
    user = User.from_dict({"id": "u", "isVerified": False, "verified": True})
    assert user.is_verified is False and user.username is None and user.online is None
    for data in ({}, {"id": ""}, {"id": 7}, []):
        with pytest.raises(ProtocolError):
            User.from_dict(data)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    ) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        with pytest.raises(APIError) as exc:
            bot.get_user("unknown")
        assert exc.value.status == 404
        for invalid in ("", "@", ".", "..", None):
            with pytest.raises(ValueError):
                bot.get_user(invalid)


def test_profile_identifier_is_encoded_as_one_path_segment():
    def handler(request):
        assert request.url.raw_path == b"/api/users/name%2Fother"
        return httpx.Response(200, json={"id": "u"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        assert bot.get_user("name/other").id == "u"
