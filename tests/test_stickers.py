"""Read-only sticker views remain tolerant and preserve the original JSON."""

import copy
import json
import runpy
import threading
import types
from pathlib import Path
from typing import get_type_hints

import httpx
import pytest
from helpers import Transport, install_websocket

from itd_bot import (
    Dispatcher,
    EntityType,
    EventContext,
    MessageContext,
    MessageValidationError,
    Router,
    StickerEntity,
    filters,
    message_stickers,
)


def message(entity):
    return {
        "message_id": "message-1",
        "sender_id": "user-1",
        "text": None,
        "entities": [entity],
        "future": {"kept": True},
    }


def test_enum_and_schema_are_optional_views_not_runtime_converters():
    assert (
        EntityType.STICKER == "sticker" and EntityType("sticker") is EntityType.STICKER
    )
    assert json.loads(json.dumps({"type": EntityType.STICKER})) == {"type": "sticker"}
    assert str(EntityType("future")) == "future"
    assert get_type_hints(StickerEntity)["sticker_id"] is str
    assert StickerEntity.__total__ is False


@pytest.mark.parametrize(
    "entities",
    [
        None,
        "sticker",
        {},
        1,
        (),
        [None, "sticker", 1, [], {"type": "future"}, {"sticker_id": "id"}],
    ],
)
def test_missing_and_malformed_entities_do_not_break_reading(entities):
    assert message_stickers({}) == []
    assert message_stickers({"entities": entities, "future": True}) == []


@pytest.mark.parametrize(
    "entity",
    [
        {"type": "sticker", "sticker_id": "fictional"},
        {"type": EntityType.STICKER, "sticker_id": ""},
        {"type": "sticker"},
        {"type": "sticker", "sticker_id": 17},
        {"type": "sticker", "sticker_id": "x" * 65, "future": {"nested": [1]}},
    ],
)
def test_helper_selects_original_dict_without_validating_ids(entity):
    raw = message(entity)
    original = copy.deepcopy(raw)
    result = message_stickers(raw)
    assert result == [entity] and result[0] is entity
    assert raw == original and result is not raw["entities"]


def test_helper_preserves_multiple_entity_order_and_unknown_fields():
    first = {"type": "sticker", "sticker_id": "one", "future": 1}
    second = {"type": "sticker", "sticker_id": "two", "future": [2]}
    raw = {"entities": [first, {"type": "future", "sticker_id": "other"}, None, second]}
    result = message_stickers(raw)
    assert result[0] is first and result[1] is second


@pytest.mark.parametrize("dispatcher", [Dispatcher, Router])
@pytest.mark.parametrize("event_type", ["new_message", "new_messages"])
@pytest.mark.parametrize(
    "entity",
    [
        {"type": "sticker", "sticker_id": "fictional", "future": {"x": 1}},
        {"type": "sticker", "unknown": True},
    ],
)
def test_http_update_reaches_handler_whole_and_unchanged(
    dispatcher, event_type, entity
):
    raw = {
        "type": event_type,
        "chat_id": "chat-1",
        "event_seq": 1,
        "future": {"packet": True},
    }
    raw["message" if event_type == "new_message" else "messages"] = (
        message(entity) if event_type == "new_message" else [message(entity)]
    )
    transport = Transport(
        httpx.Response(
            200, json={"events": [raw], "next_event_seq": 1, "reset_required": False}
        )
    )
    bot = transport.bot()
    router = dispatcher(bot, self_user_id="self", auto_typing=False, read_mode="off")
    seen = []
    router.message()(seen.append)
    received = bot.get_updates(0)["events"][0]
    router.handle(received)
    assert received == raw and len(seen) == 1
    ctx = seen[0]
    assert ctx.event is received
    assert ctx.message is (
        received["message"] if event_type == "new_message" else received["messages"][0]
    )
    assert message_stickers(ctx.message)[0] is ctx.message["entities"][0]
    assert type(ctx.message["entities"][0]) is dict
    assert len(transport.requests) == 1


def test_sticker_filter_combines_and_ignores_non_message_events():
    bot = Transport().bot()
    router = Router(bot, self_user_id="self", read_mode="off", auto_typing=False)
    seen = []
    router.message(
        filters.sticker() & filters.chat("chat-1") & ~filters.user("blocked")
    )(seen.append)
    raw = {
        "type": "new_message",
        "chat_id": "chat-1",
        "message": message({"type": "sticker", "future": True}),
    }
    router.handle(raw)
    assert len(seen) == 1 and seen[0].event is raw
    assert not filters.sticker()(
        EventContext(bot, {"type": "presence", "chat_id": "chat-1"})
    )
    assert not filters.sticker()(
        MessageContext(bot, {"chat_id": "chat-1"}, {"text": "hello"})
    )


def test_websocket_frame_keeps_sticker_entities_without_validation(monkeypatch):
    stop = threading.Event()
    raw = {
        "type": "new_message",
        "chat_id": "chat-1",
        "message": message(
            {"type": "sticker", "sticker_id": "fictional", "future": True}
        ),
    }

    class Connection:
        handshake_response = types.SimpleNamespace(status=101)
        closed = False

        def recv(self):
            stop.set()
            return json.dumps(raw)

        def settimeout(self, value):
            pass

        def close(self):
            self.closed = True

    connection = Connection()
    install_websocket(
        monkeypatch,
        types.SimpleNamespace(
            create_connection=lambda *args, **kwargs: connection,
            WebSocketTimeoutException=TimeoutError,
        ),
    )
    transport = Transport()
    frames = list(transport.bot().websocket_events(stop=stop))
    assert frames == [raw] and connection.closed
    assert message_stickers(frames[0]["message"])[0]["future"] is True
    assert not transport.requests


@pytest.mark.parametrize(
    "operation", ["send_message", "send_message_to_user", "edit_message"]
)
def test_known_sticker_enum_is_still_rejected_by_opt_in_validation(operation):
    transport = Transport()
    args = (
        ("chat-1", "message-1", "") if operation == "edit_message" else ("target", "")
    )
    with pytest.raises(MessageValidationError, match="issues/17"):
        getattr(transport.bot(), operation)(
            *args,
            entities=[{"type": EntityType.STICKER, "sticker_id": "fictional"}],
            validate_limits=True,
        )
    assert not transport.requests


def test_reading_example_runs_without_account_or_network(capsys):
    sample = runpy.run_path(
        str(Path(__file__).resolve().parents[1] / "examples/sticker_reading.py")
    )
    sample["main"]()
    assert "1" in capsys.readouterr().out
