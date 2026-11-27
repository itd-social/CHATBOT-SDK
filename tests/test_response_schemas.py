"""Response schemas are annotations only. All values are fictional."""

from __future__ import annotations

import json
import typing

import httpx
import pytest
from helpers import Transport

import itd_bot
from itd_bot import Bot, Dispatcher, MessageContext, Text, User, schemas
from itd_bot.context import Attachment, EventContext, SentMessage

SCHEMA_NAMES = [
    name
    for name, value in vars(schemas).items()
    if isinstance(value, type) and typing.is_typeddict(value)
]

# Some modules import these names only for type checkers.
NAMES = {**vars(schemas), "Bot": Bot, "User": User}


def hints(obj):
    return typing.get_type_hints(obj, localns=NAMES)


def test_every_schema_is_exported_total_false_and_disclaims_a_spec():
    assert len(SCHEMA_NAMES) == 16
    for name in SCHEMA_NAMES:
        cls = getattr(schemas, name)
        assert name in itd_bot.__all__ and getattr(itd_bot, name) is cls
        assert cls.__total__ is False
        assert "not a specification" in (cls.__doc__ or "")


@pytest.mark.parametrize(
    ("owner", "method", "expected"),
    [
        (Bot, "get_chats", schemas.ChatsResponse),
        (Bot, "get_chat", schemas.ChatPayload),
        (Bot, "create_direct_chat", schemas.ChatPayload),
        (Bot, "get_messages", schemas.MessagesResponse),
        (Bot, "get_messages_around", schemas.MessagesResponse),
        (Bot, "get_updates", schemas.UpdatesResponse),
        (Bot, "send_message", schemas.SendResponse),
        (Bot, "send_message_to_user", schemas.SendResponse),
        (Bot, "send_formatted", schemas.SendResponse),
        (Bot, "send_formatted_to_user", schemas.SendResponse),
        (Bot, "upload_file", schemas.UploadFinalizeResponse),
        (Bot, "get_reactions", schemas.ReactionsResponse),
        (Bot, "get_file_links", schemas.FileLinksResponse),
        (MessageContext, "reply", schemas.SendResponse),
        (MessageContext, "reply_formatted", schemas.SendResponse),
    ],
)
def test_public_methods_return_the_schema(owner, method, expected):
    assert hints(getattr(owner, method))["return"] is expected


def test_contexts_and_dispatcher_use_the_schemas():
    assert hints(MessageContext)["event"] is schemas.UpdateEvent
    assert hints(MessageContext)["message"] is schemas.MessagePayload
    assert hints(EventContext)["event"] is schemas.UpdateEvent
    assert hints(Attachment)["raw"] is schemas.AttachmentPayload
    assert hints(SentMessage)["raw"] is schemas.SendResponse
    assert hints(Dispatcher.handle)["event"] is schemas.UpdateEvent


def test_methods_still_return_plain_dicts():
    transport = Transport(default=httpx.Response(200, json={"message_id": "m-1"}))
    result = transport.bot().send_message("chat", "hi")
    assert type(result) is dict and result == {"message_id": "m-1"}


# --- send_formatted_to_user ----------------------------------------------------


@pytest.mark.parametrize("content", ["plain", None, ("a", [])])
def test_send_formatted_to_user_accepts_only_a_text_builder(content):
    transport = Transport()
    with pytest.raises(TypeError, match="Text builder"):
        transport.bot().send_formatted_to_user("user-1", content)
    assert transport.requests == []


def test_send_formatted_to_user_sends_text_entities_and_options():
    transport = Transport(default=httpx.Response(200, json={"message_id": "m-1"}))
    result = transport.bot().send_formatted_to_user(
        "user-1", Text().bold("Hi"), client_message_id="c-1"
    )
    (request,) = transport.requests
    assert request.method == "POST"
    assert request.url.path == "/api/v1/messenger/users/user-1/messages"
    body = json.loads(request.content)
    text, entities = Text().bold("Hi").build()
    assert body == {"text": text, "entities": entities, "client_message_id": "c-1"}
    assert result == {"message_id": "m-1"}


def test_on_message_warning_points_at_the_caller_not_at_the_sdk():
    # Python hides a DeprecationWarning attributed to a library module by default,
    # so the warning has to name the line that calls on_message.
    router = Dispatcher(Bot("fake"), self_user_id="self")
    with pytest.warns(DeprecationWarning, match="on_message") as caught:
        router.on_message(lambda event, message: None)
    ours = [item for item in caught if "on_message" in str(item.message)]
    assert ours[0].filename == __file__
