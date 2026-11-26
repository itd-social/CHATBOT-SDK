"""Client methods and error branches that no other test reaches."""

from __future__ import annotations

from unittest.mock import ANY

import httpx
import pytest
from helpers import API, BASE, Transport, httpx_namespace, jwt, sent_json

import itd_bot.client as client_module
from itd_bot import APIError, Bot, ProtocolError, ResetRequired, Text

OK = {"ok": True}


def ok_bot(**kwargs):
    transport = Transport(default=httpx.Response(200, json=OK))
    return transport, transport.bot(**kwargs)


# --- one request per method: method, path, query and body ----------------------

CASES = [
    (
        "send_message_to_user",
        lambda b: b.send_message_to_user("user-1", "hello"),
        "POST",
        "/users/user-1/messages",
        {},
        {"text": "hello", "client_message_id": ANY},
    ),
    (
        "send_message_to_user_all_fields",
        lambda b: b.send_message_to_user(
            "user-1",
            "hello",
            attachments=["file-1"],
            entities=[{"type": "bold"}],
            client_message_id="client-1",
        ),
        "POST",
        "/users/user-1/messages",
        {},
        {
            "text": "hello",
            "attachments": ["file-1"],
            "entities": [{"type": "bold"}],
            "client_message_id": "client-1",
        },
    ),
    (
        "send_message_to_user_empty_lists_are_left_out",
        lambda b: b.send_message_to_user(
            "user-1", "hello", attachments=[], entities=[], client_message_id=""
        ),
        "POST",
        "/users/user-1/messages",
        {},
        {"text": "hello"},
    ),
    (
        "send_message_to_user_encodes_the_id",
        lambda b: b.send_message_to_user("a/b c", "hi"),
        "POST",
        "/users/a%2Fb%20c/messages",
        {},
        {"text": "hi", "client_message_id": ANY},
    ),
    (
        "edit_message_plain",
        lambda b: b.edit_message("chat-1", "msg-1", "new"),
        "PATCH",
        "/chats/chat-1/messages/msg-1",
        {},
        {"text": "new"},
    ),
    (
        "edit_message_entities_none_is_omitted",
        lambda b: b.edit_message("chat-1", "msg-1", "new", entities=None),
        "PATCH",
        "/chats/chat-1/messages/msg-1",
        {},
        {"text": "new"},
    ),
    (
        "edit_message_empty_entities_clear_formatting",
        lambda b: b.edit_message("chat-1", "msg-1", "new", entities=[]),
        "PATCH",
        "/chats/chat-1/messages/msg-1",
        {},
        {"text": "new", "entities": []},
    ),
    (
        "edit_message_attachments",
        lambda b: b.edit_message(
            "chat-1", "msg-1", "new", attachments=["file-1"], entities=[{"t": 1}]
        ),
        "PATCH",
        "/chats/chat-1/messages/msg-1",
        {},
        {"text": "new", "attachments": ["file-1"], "entities": [{"t": 1}]},
    ),
    (
        "edit_message_empty_attachments_are_left_out",
        lambda b: b.edit_message("chat-1", "msg-1", "new", attachments=[]),
        "PATCH",
        "/chats/chat-1/messages/msg-1",
        {},
        {"text": "new"},
    ),
    (
        "create_direct_chat",
        lambda b: b.create_direct_chat("user-1"),
        "POST",
        "/chats/dm",
        {},
        {"target_user_id": "user-1"},
    ),
    (
        "accept_chat",
        lambda b: b.accept_chat("chat-1"),
        "POST",
        "/chats/chat-1/accept",
        {},
        None,
    ),
    (
        "get_unread_count",
        lambda b: b.get_unread_count(),
        "GET",
        "/chats/unread",
        {"manual_unread": "1"},
        None,
    ),
    (
        "get_reaction_catalog",
        lambda b: b.get_reaction_catalog(),
        "GET",
        "/reactions/available",
        {},
        None,
    ),
    (
        "get_presence",
        lambda b: b.get_presence("user-1"),
        "GET",
        "/presence/user-1",
        {},
        None,
    ),
    (
        "get_presence_many",
        lambda b: b.get_presence_many(["user-1", "user-2"]),
        "POST",
        "/presence",
        {},
        {"user_ids": ["user-1", "user-2"]},
    ),
    (
        "mark_voice_heard",
        lambda b: b.mark_voice_heard("chat-1", "msg-1"),
        "POST",
        "/chats/chat-1/messages/msg-1/heard",
        {},
        None,
    ),
    (
        "get_heard_messages",
        lambda b: b.get_heard_messages("chat-1", ["msg-1", "msg-2"]),
        "POST",
        "/chats/chat-1/heard",
        {},
        {"message_ids": ["msg-1", "msg-2"]},
    ),
    (
        "get_media_category_only",
        lambda b: b.get_media("chat-1", "image"),
        "GET",
        "/chats/chat-1/media",
        {"category": "image"},
        None,
    ),
    (
        "get_media_paging",
        lambda b: b.get_media("chat-1", "file", before="msg-9", limit=20),
        "GET",
        "/chats/chat-1/media",
        {"category": "file", "before": "msg-9", "limit": "20"},
        None,
    ),
    (
        "get_messages_around_default_limit",
        lambda b: b.get_messages_around("chat-1", "msg-1"),
        "GET",
        "/chats/chat-1/messages/around/msg-1",
        {"limit": "40"},
        None,
    ),
    (
        "get_messages_around_custom_limit",
        lambda b: b.get_messages_around("chat-1", "msg-1", limit=5),
        "GET",
        "/chats/chat-1/messages/around/msg-1",
        {"limit": "5"},
        None,
    ),
    (
        "set_pinned",
        lambda b: b.set_pinned("chat-1", True),
        "PATCH",
        "/chats/chat-1/pin",
        {},
        {"is_pinned": True},
    ),
    (
        "set_pinned_off",
        lambda b: b.set_pinned("chat-1", False),
        "PATCH",
        "/chats/chat-1/pin",
        {},
        {"is_pinned": False},
    ),
    (
        "set_muted",
        lambda b: b.set_muted("chat-1", True),
        "PATCH",
        "/chats/chat-1/mute",
        {},
        {"is_muted": True},
    ),
    (
        "set_muted_off",
        lambda b: b.set_muted("chat-1", False),
        "PATCH",
        "/chats/chat-1/mute",
        {},
        {"is_muted": False},
    ),
    (
        "mark_unread_default",
        lambda b: b.mark_unread("chat-1"),
        "PATCH",
        "/chats/chat-1/unread",
        {},
        {"unread": True},
    ),
    (
        "mark_unread_off",
        lambda b: b.mark_unread("chat-1", False),
        "PATCH",
        "/chats/chat-1/unread",
        {},
        {"unread": False},
    ),
    (
        "delete_chat_for_me",
        lambda b: b.delete_chat("chat-1"),
        "POST",
        "/chats/chat-1/delete",
        {},
        {"for_everyone": False},
    ),
    (
        "delete_chat_for_everyone",
        lambda b: b.delete_chat("chat-1", for_everyone=True),
        "POST",
        "/chats/chat-1/delete",
        {},
        {"for_everyone": True},
    ),
    (
        "clear_history_for_me",
        lambda b: b.clear_history("chat-1"),
        "POST",
        "/chats/chat-1/clear-history",
        {},
        None,
    ),
    (
        "clear_history_for_everyone",
        lambda b: b.clear_history("chat-1", for_everyone=True),
        "POST",
        "/chats/chat-1/clear-history-all",
        {},
        None,
    ),
    (
        "forward_with_client_ids",
        lambda b: b.forward_messages(
            "chat-2",
            "chat-1",
            ["msg-1", "msg-2"],
            client_message_ids=["c-1", "c-2"],
        ),
        "POST",
        "/chats/chat-2/messages/forward",
        {},
        {
            "source_chat_id": "chat-1",
            "message_ids": ["msg-1", "msg-2"],
            "client_message_ids": {"msg-1": "c-1", "msg-2": "c-2"},
        },
    ),
    (
        "forward_without_client_ids",
        lambda b: b.forward_messages("chat-2", "chat-1", ["msg-1"]),
        "POST",
        "/chats/chat-2/messages/forward",
        {},
        {"source_chat_id": "chat-1", "message_ids": ["msg-1"]},
    ),
    (
        "forward_empty_client_ids_are_left_out",
        lambda b: b.forward_messages(
            "chat-2", "chat-1", ["msg-1"], client_message_ids=[]
        ),
        "POST",
        "/chats/chat-2/messages/forward",
        {},
        {"source_chat_id": "chat-1", "message_ids": ["msg-1"]},
    ),
]


@pytest.mark.parametrize(
    ("call", "method", "path", "query", "body"),
    [case[1:] for case in CASES],
    ids=[case[0] for case in CASES],
)
def test_method_sends_the_expected_request(call, method, path, query, body):
    transport, bot = ok_bot()
    assert call(bot) == OK
    (request,) = transport.requests
    assert request.method == method
    assert request.url.raw_path.split(b"?")[0].decode() == API + path
    assert dict(request.url.params) == query
    if body is None:
        assert request.content == b""
    else:
        assert sent_json(request) == body
    assert request.headers["authorization"] == "Bearer fictional-token"


@pytest.mark.parametrize(
    "call",
    [
        lambda b: b.send_message_to_user("", "hi"),
        lambda b: b.send_message_to_user(".", "hi"),
        lambda b: b.get_presence(".."),
        lambda b: b.accept_chat(""),
        lambda b: b.mark_voice_heard("chat-1", ""),
        lambda b: b.get_messages_around("chat-1", ".."),
        lambda b: b.set_pinned("", True),
        lambda b: b.delete_chat(None),
    ],
    ids=[
        "empty-user",
        "dot-user",
        "dotdot-user",
        "empty-chat",
        "empty-message",
        "dotdot-message",
        "empty-chat-pin",
        "none-chat",
    ],
)
def test_bad_ids_are_rejected_before_any_request(call):
    transport, bot = ok_bot()
    with pytest.raises(ValueError):
        call(bot)
    assert transport.requests == []


# --- send_formatted ------------------------------------------------------------


@pytest.mark.parametrize("content", ["plain string", None, {"text": "x"}])
def test_send_formatted_accepts_only_a_text_builder(content):
    transport, bot = ok_bot()
    with pytest.raises(TypeError, match="Text builder"):
        bot.send_formatted("chat-1", content)
    assert transport.requests == []


def test_send_formatted_sends_text_entities_and_options():
    transport, bot = ok_bot()
    bot.send_formatted("chat-1", Text().bold("Hi"), reply_to_id="msg-1")
    body = sent_json(transport.requests[0])
    assert body["text"] == "Hi" and body["reply_to_id"] == "msg-1"
    assert body["entities"]


# --- reactions -----------------------------------------------------------------


@pytest.mark.parametrize("method", ["add_reaction", "remove_reaction"])
@pytest.mark.parametrize(
    "emoji", ["", None, 5, b"x"], ids=["empty", "none", "int", "bytes"]
)
def test_reaction_with_invalid_emoji_makes_no_request(method, emoji):
    transport, bot = ok_bot()
    with pytest.raises(ValueError, match="emoji"):
        getattr(bot, method)("chat-1", "msg-1", emoji)
    assert transport.requests == []


BAD_REACTIONS = [
    pytest.param(["not", "an", "object"], id="list"),
    pytest.param(None, id="null"),
    pytest.param({"reactions": []}, id="no-version"),
    pytest.param({"reaction_version": 3, "reactions": []}, id="version-not-string"),
    pytest.param({"reaction_version": "v1"}, id="no-reactions"),
    pytest.param({"reaction_version": "v1", "reactions": {}}, id="reactions-dict"),
    pytest.param({"reaction_version": "v1", "reactions": ["x"]}, id="item-string"),
    pytest.param({"reaction_version": "v1", "reactions": [{}]}, id="item-no-reaction"),
    pytest.param(
        {"reaction_version": "v1", "reactions": [{"reaction": "x"}]},
        id="reaction-string",
    ),
    pytest.param(
        {
            "reaction_version": "v1",
            "reactions": [
                {"reaction": {"type": "emoji", "emoji": "a"}, "chosen": True},
                None,
            ],
        },
        id="broken-second-item",
    ),
]


@pytest.mark.parametrize("method", ["add_reaction", "remove_reaction"])
@pytest.mark.parametrize("payload", BAD_REACTIONS)
def test_invalid_reactions_response_stops_before_the_put(method, payload):
    transport = Transport(httpx.Response(200, json=payload))
    bot = transport.bot()
    with pytest.raises(ProtocolError):
        getattr(bot, method)("chat-1", "msg-1", "👍")
    assert [r.method for r in transport.requests] == ["GET"]


def reaction(emoji):
    return {"type": "emoji", "emoji": emoji}


def current_reactions():
    return {
        "reaction_version": "v7",
        "reactions": [
            {"reaction": reaction("a"), "chosen": True},
            {"reaction": reaction("b"), "chosen": False},
            {"reaction": reaction("c"), "chosen": True},
        ],
    }


def test_add_reaction_keeps_chosen_ones_and_does_not_duplicate():
    transport = Transport(
        httpx.Response(200, json=current_reactions()),
        httpx.Response(200, json=OK),
        httpx.Response(200, json=current_reactions()),
        httpx.Response(200, json=OK),
    )
    bot = transport.bot()
    bot.add_reaction("chat-1", "msg-1", "z")
    bot.add_reaction("chat-1", "msg-1", "a")
    first, second = sent_json(transport.requests[1]), sent_json(transport.requests[3])
    assert first["reactions"] == [reaction("a"), reaction("c"), reaction("z")]
    assert second["reactions"] == [reaction("a"), reaction("c")]
    assert first["expected_version"] == "v7" and first["operation_id"]
    assert transport.requests[1].method == "PUT"


def test_remove_reaction_keeps_the_others_and_ignores_unchosen():
    transport = Transport(
        httpx.Response(200, json=current_reactions()),
        httpx.Response(200, json=OK),
        httpx.Response(200, json=current_reactions()),
        httpx.Response(200, json=OK),
    )
    bot = transport.bot()
    bot.remove_reaction("chat-1", "msg-1", "a")
    bot.remove_reaction("chat-1", "msg-1", "b")
    assert sent_json(transport.requests[1])["reactions"] == [reaction("c")]
    assert sent_json(transport.requests[3])["reactions"] == [
        reaction("a"),
        reaction("c"),
    ]
    assert sent_json(transport.requests[1])["expected_version"] == "v7"


def test_each_reaction_change_gets_its_own_operation_id():
    transport = Transport(
        httpx.Response(200, json=current_reactions()),
        httpx.Response(200, json=OK),
        httpx.Response(200, json=current_reactions()),
        httpx.Response(200, json=OK),
    )
    bot = transport.bot()
    bot.add_reaction("chat-1", "msg-1", "z")
    bot.add_reaction("chat-1", "msg-1", "y")
    ids = {sent_json(transport.requests[i])["operation_id"] for i in (1, 3)}
    assert len(ids) == 2


# --- constructor and set_token -------------------------------------------------


def test_a_token_or_refresh_token_is_required():
    with pytest.raises(ValueError, match="required"):
        Bot()


@pytest.mark.parametrize("token", ["", "line\nbreak", "carriage\rreturn"])
def test_constructor_rejects_invalid_bearer_tokens(token):
    with pytest.raises(ValueError, match="valid bearer token"):
        Bot(token)


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "example.test",
        "ftp://example.test",
        "file:///tmp/x",
        "https://",
        "https://example.test?x=1",
        "https://example.test/base#fragment",
    ],
)
def test_constructor_rejects_unusable_base_urls(base_url):
    with pytest.raises(ValueError, match="base_url"):
        Bot("fictional-token", base_url=base_url)


def test_base_url_keeps_its_path_and_drops_the_trailing_slash():
    transport = Transport(default=httpx.Response(200, json=OK))
    bot = transport.bot(base_url="http://example.test:8080/prefix/")
    assert bot.base_url == "http://example.test:8080/prefix"
    bot.get_unread_count()
    assert str(transport.requests[0].url).startswith(
        "http://example.test:8080/prefix/api/v1/messenger/chats/unread"
    )


@pytest.mark.parametrize("token", ["", "line\nbreak", "carriage\rreturn"])
def test_set_token_rejects_invalid_tokens_and_keeps_the_old_one(token):
    transport, bot = ok_bot()
    with pytest.raises(ValueError, match="valid bearer token"):
        bot.set_token(token)
    bot.get_unread_count()
    assert transport.requests[0].headers["authorization"] == "Bearer fictional-token"


def test_set_token_switches_the_token_used_for_requests():
    transport, bot = ok_bot()
    bot.set_token("fictional-replacement")
    bot.get_unread_count()
    assert transport.requests[0].headers["authorization"] == (
        "Bearer fictional-replacement"
    )


class SpyClient(httpx.Client):
    instances: list[SpyClient] = []

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        SpyClient.instances.append(self)


@pytest.fixture
def spy_clients(monkeypatch):
    SpyClient.instances = []
    monkeypatch.setattr(client_module, "httpx", httpx_namespace(Client=SpyClient))
    return SpyClient.instances


def test_own_http_client_is_closed_when_the_refresh_session_cannot_start(
    spy_clients, tmp_path
):
    with pytest.raises(ValueError):
        Bot(refresh_token="has space", session_path=tmp_path / "s.json")
    (created,) = spy_clients
    assert created.is_closed


def test_own_http_client_is_closed_when_the_session_file_is_unusable(
    spy_clients, tmp_path
):
    (tmp_path / "s.json").write_text("{")
    with pytest.raises(Exception, match="refresh session"):
        Bot(refresh_token="fictional-seed", session_path=tmp_path / "s.json")
    assert spy_clients[0].is_closed


def test_borrowed_http_client_stays_open_when_the_refresh_session_fails(tmp_path):
    borrowed = Transport().client()
    with pytest.raises(ValueError):
        Bot(refresh_token="has space", client=borrowed, session_path=None)
    assert not borrowed.is_closed


def test_context_manager_closes_only_an_own_client(spy_clients):
    with Bot("fictional-token") as bot:
        assert not bot._client.is_closed
    assert spy_clients[0].is_closed

    borrowed = Transport().client()
    with Bot("fictional-token", client=borrowed):
        pass
    assert not borrowed.is_closed


# --- api_request ---------------------------------------------------------------


def test_successful_non_json_body_is_a_protocol_error():
    transport = Transport(httpx.Response(200, content=b"<html>fictional</html>"))
    with pytest.raises(ProtocolError, match="Expected JSON"):
        transport.bot().api_request("GET", "/api/anything")


def test_successful_empty_body_is_none():
    transport = Transport(httpx.Response(204))
    assert transport.bot().api_request("POST", "/api/anything") is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"json": {}, "content": b"x"},
        {"json": {}, "files": {"f": b"x"}},
        {"content": b"x", "files": {"f": b"x"}},
    ],
    ids=["json+content", "json+files", "content+files"],
)
def test_only_one_body_kind_is_allowed(kwargs):
    transport, bot = ok_bot()
    with pytest.raises(ValueError, match="only one"):
        bot.api_request("POST", "/api/anything", **kwargs)
    assert transport.requests == []


@pytest.mark.parametrize(
    "path",
    [
        "api/no-slash",
        "/other/path",
        "/api/x?query=1",
        "/api/x#fragment",
        "/api/x\\y",
        "/api/../secret",
        "/api/./x",
        "/api/%2e%2e/secret",
        "/api/%2E/x",
        "/v1/%2e%2E/x",
    ],
)
def test_unsafe_api_paths_are_refused(path):
    transport, bot = ok_bot()
    with pytest.raises(ValueError, match="safe"):
        bot.api_request("GET", path)
    assert transport.requests == []


@pytest.mark.parametrize("path", ["chats", "//evil.test/x", ""])
def test_messenger_request_needs_a_single_leading_slash(path):
    transport, bot = ok_bot()
    with pytest.raises(ValueError, match="relative messenger endpoint"):
        bot.request("GET", path)
    assert transport.requests == []


@pytest.mark.parametrize("header", ["Authorization", "COOKIE", "host"])
def test_managed_headers_cannot_be_overridden(header):
    transport, bot = ok_bot()
    with pytest.raises(ValueError, match="managed by the SDK"):
        bot.api_request("GET", "/api/x", headers={header: "value"})
    assert transport.requests == []


def test_raw_success_returns_the_response_and_error_still_raises():
    transport = Transport(
        httpx.Response(204),
        httpx.Response(500, json={"error": {"message": "broken"}}),
    )
    bot = transport.bot()
    response = bot.api_request("POST", "/api/x", raw=True)
    assert isinstance(response, httpx.Response) and response.status_code == 204
    with pytest.raises(APIError) as info:
        bot.api_request("POST", "/api/x", raw=True)
    assert info.value.status == 500


@pytest.mark.parametrize(
    ("response", "message", "code", "errors", "retry_after"),
    [
        (
            httpx.Response(500, content=b"<html>oops</html>"),
            "Request failed",
            None,
            None,
            None,
        ),
        (httpx.Response(502), "Request failed", None, None, None),
        (httpx.Response(400, json=["x"]), "Request failed", None, None, None),
        (
            httpx.Response(400, json={"error": "text"}),
            "Request failed",
            None,
            None,
            None,
        ),
        (
            httpx.Response(
                422,
                json={"error": {"detail": "Bad", "code": "INVALID", "errors": [1]}},
            ),
            "Bad",
            "INVALID",
            [1],
            None,
        ),
        (
            httpx.Response(409, json={"title": "Conflict", "violations": ["v"]}),
            "Conflict",
            None,
            ["v"],
            None,
        ),
        (
            httpx.Response(
                503, headers={"Retry-After": "12"}, json={"message": "Later"}
            ),
            "Later",
            None,
            None,
            "12",
        ),
    ],
    ids=["html", "empty", "list", "error-string", "wrapped", "flat", "retry-after"],
)
def test_error_responses_become_api_errors(
    response, message, code, errors, retry_after
):
    transport = Transport(response)
    with pytest.raises(APIError) as info:
        # Retries of 5xx GETs have their own tests; here one answer is enough.
        transport.bot(get_retries=0).api_request("GET", "/api/x")
    error = info.value
    assert str(error) == message
    assert (error.status, error.code, error.errors, error.retry_after) == (
        response.status_code,
        code,
        errors,
        retry_after,
    )


def test_unauthorised_without_a_refresh_session_is_not_retried():
    transport = Transport(httpx.Response(401, json={"error": {"code": "NO_AUTH"}}))
    with pytest.raises(APIError) as info:
        transport.bot().get_unread_count()
    assert info.value.status == 401 and info.value.code == "NO_AUTH"
    assert len(transport.requests) == 1


def test_forward_cost_follows_the_number_of_messages():
    costs = []

    class Recorder:
        def acquire(self, method, path, *, cost=1, account=None):
            costs.append((method, cost))

        def observe(self, *args, **kwargs):
            pass

    transport = Transport(default=httpx.Response(200, json=OK))
    bot = transport.bot(limiter=Recorder())
    bot.forward_messages("chat-2", "chat-1", ["m1", "m2", "m3"])
    bot.forward_messages("chat-2", "chat-1", [])
    bot.send_message("chat-2", "hello")
    assert costs == [("POST", 3), ("POST", 1), ("POST", 1)]


# --- get_post, get_follow_status, create_file_session ----------------------------


@pytest.mark.parametrize(
    "payload",
    [
        None,
        ["x"],
        {},
        {"data": None},
        {"data": ["x"]},
        {"data": {}},
        {"data": {"id": ""}},
        {"data": {"id": 5}},
    ],
    ids=[
        "null",
        "list",
        "empty",
        "data-null",
        "data-list",
        "no-id",
        "empty-id",
        "int-id",
    ],
)
def test_get_post_rejects_malformed_responses(payload):
    transport = Transport(httpx.Response(200, json=payload))
    with pytest.raises(ProtocolError, match="Invalid post response"):
        transport.bot().get_post("post-1")


def test_get_post_rejects_a_different_post():
    transport = Transport(httpx.Response(200, json={"data": {"id": "post-2"}}))
    with pytest.raises(ProtocolError, match="does not match"):
        transport.bot().get_post("post-1")


def test_get_post_returns_the_post_and_encodes_the_id():
    transport = Transport(httpx.Response(200, json={"data": {"id": "a/b", "x": 1}}))
    post = transport.bot().get_post("a/b")
    assert post.id == "a/b" and post.raw == {"id": "a/b", "x": 1}
    assert transport.requests[0].url.raw_path == b"/api/posts/a%2Fb"


def test_get_follow_status_of_nobody_makes_no_request():
    transport, bot = ok_bot()
    assert bot.get_follow_status([]) == {}
    assert transport.requests == []


@pytest.mark.parametrize("user_ids", [["user-1", ""], [".."], [None]])
def test_get_follow_status_validates_every_id_first(user_ids):
    transport, bot = ok_bot()
    with pytest.raises(ValueError):
        bot.get_follow_status(user_ids)
    assert transport.requests == []


@pytest.mark.parametrize(
    "payload",
    [None, ["x"], {}, {"data": None}, {"data": ["x"]}, {"data": "x"}],
    ids=["null", "list", "empty", "data-null", "data-list", "data-string"],
)
def test_get_follow_status_rejects_malformed_responses(payload):
    transport = Transport(httpx.Response(200, json=payload))
    with pytest.raises(ProtocolError, match="follow status"):
        transport.bot().get_follow_status(["user-1"])


def test_get_follow_status_posts_ids_and_returns_the_data():
    transport = Transport(
        httpx.Response(200, json={"data": {"user-1": True, "user-2": False}})
    )
    result = transport.bot().get_follow_status(["user-1", "user-2"])
    assert result == {"user-1": True, "user-2": False}
    request = transport.requests[0]
    assert (request.method, request.url.path) == ("POST", "/api/users/follow-status")
    assert sent_json(request) == {"userIds": ["user-1", "user-2"]}


@pytest.mark.parametrize("status", [200, 201, 202])
def test_create_file_session_expects_exactly_204(status):
    transport = Transport(httpx.Response(status, json={}))
    with pytest.raises(ProtocolError, match="file session"):
        transport.bot().create_file_session()


def test_create_file_session_sends_the_origin_of_the_site_only():
    transport = Transport(httpx.Response(204))
    transport.bot(base_url=BASE + "/prefix").create_file_session()
    request = transport.requests[0]
    assert request.method == "POST"
    assert request.url.path == "/prefix/api/v1/messenger/browser-file-session"
    assert request.headers["origin"] == BASE


# --- get_updates ---------------------------------------------------------------


def updates(**changes):
    body = {"events": [], "reset_required": False, "next_event_seq": 5}
    body.update(changes)
    return httpx.Response(200, json=body)


@pytest.mark.parametrize("since", [-1, True, False, "1", 1.5, None])
def test_get_updates_rejects_a_bad_cursor_before_any_request(since):
    transport, bot = ok_bot()
    with pytest.raises(ValueError, match="since"):
        bot.get_updates(since)
    assert transport.requests == []


def test_get_updates_sends_cursor_capabilities_and_optional_limit():
    transport = Transport(updates(), updates())
    bot = transport.bot()
    bot.get_updates(3)
    bot.get_updates(3, limit=10)
    first, second = (dict(r.url.params) for r in transport.requests)
    assert first == {
        "since": "3",
        "capabilities": "chat_settings",
        "manual_unread": "1",
    }
    assert second == {**first, "limit": "10"}


@pytest.mark.parametrize(
    "changes",
    [
        {"events": None},
        {"events": {}},
        {"reset_required": "no"},
        {"reset_required": None},
        {"next_event_seq": True},
        {"next_event_seq": "5"},
        {"next_event_seq": -1},
        {"next_event_seq": 1.5},
        {"events": ["x"]},
        {"events": [{"type": 5}]},
        {"events": [{}]},
        {"events": [{"type": "ok"}, None]},
    ],
    ids=[
        "events-null",
        "events-dict",
        "reset-string",
        "reset-null",
        "seq-bool",
        "seq-string",
        "seq-negative",
        "seq-float",
        "event-string",
        "type-int",
        "no-type",
        "second-event-null",
    ],
)
def test_get_updates_rejects_malformed_batches(changes):
    transport = Transport(updates(**changes))
    with pytest.raises(ProtocolError, match="Invalid updates response"):
        transport.bot().get_updates(0)


@pytest.mark.parametrize("body", [None, ["x"], {}])
def test_get_updates_rejects_a_body_that_is_not_a_batch(body):
    transport = Transport(httpx.Response(200, json=body))
    with pytest.raises(ProtocolError, match="Invalid updates response"):
        transport.bot().get_updates(0)


def test_cursor_that_moves_backwards_is_a_protocol_error():
    transport = Transport(updates(next_event_seq=4))
    with pytest.raises(ProtocolError, match="backwards"):
        transport.bot().get_updates(5)


def test_cursor_that_stays_in_place_is_fine():
    transport = Transport(updates(next_event_seq=5))
    assert transport.bot().get_updates(5)["next_event_seq"] == 5


def test_reset_required_carries_the_whole_response_and_is_checked_first():
    transport = Transport(updates(reset_required=True, next_event_seq=1))
    with pytest.raises(ResetRequired) as info:
        transport.bot().get_updates(9)
    assert info.value.response["next_event_seq"] == 1
    assert isinstance(info.value, ProtocolError)


def test_account_key_is_a_hash_and_never_the_token():
    transport = Transport(default=httpx.Response(200, json=OK))
    bot = transport.bot(token=jwt("alice-account"))
    key = bot._account_key(jwt("alice-account"))
    assert key.startswith("sub:") and "alice-account" not in key
    assert bot._account_key(jwt("alice-account")) == key
    other = bot._account_key(jwt("bob-account"))
    assert other != key
