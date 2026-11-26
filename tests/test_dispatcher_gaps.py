"""Dispatcher registration, event validation, typing, read marks and state files."""

from __future__ import annotations

import json
import logging
import threading

import pytest
from helpers import BASE, Clock, patch_clock, patch_os

import itd_bot.dispatcher as dispatcher_module
from itd_bot import APIError, Dispatcher, EventContext, ProtocolError

USER = "user-me"
CHAT = "chat-1"


class FakeBot:
    """Records what the dispatcher asks the bot to do; no HTTP involved."""

    base_url = BASE

    def __init__(self, chats=None):
        self.chats = chats
        self.chats_error = None
        self.chat_calls = []
        self.marked = []
        self.actions = []
        self.mark_error = None
        self.action_error = None
        self.polls = []
        self.on_poll = None

    def mark_read(self, chat_id, message_id):
        if self.mark_error is not None:
            raise self.mark_error
        self.marked.append((chat_id, message_id))

    def send_action(self, chat_id, action):
        if self.action_error is not None:
            raise self.action_error
        self.actions.append((chat_id, action))

    def get_chats(self, *, limit=None):
        self.chat_calls.append(limit)
        if self.chats_error is not None:
            raise self.chats_error
        return self.chats

    def poll(self, since, handler, **kwargs):
        self.polls.append((since, handler, kwargs))
        if self.on_poll is not None:
            self.on_poll(since, kwargs)


def make(bot=None, **kwargs):
    kwargs.setdefault("auto_typing", False)
    kwargs.setdefault("read_mode", "off")
    return Dispatcher(bot or FakeBot(), self_user_id=USER, **kwargs)


def message_event(**changes):
    message = {"message_id": "msg-1", "sender_id": "someone-else", "text": "hello"}
    message.update(changes)
    return {"type": "new_message", "chat_id": CHAT, "message": message}


def recorder():
    calls = []

    def handler(*args):
        calls.append(args)

    return calls, handler


# --- construction ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"self_user_id": ""}, "self_user_id"),
        ({"typing_interval": 0}, "typing_interval"),
        ({"typing_interval": -1}, "typing_interval"),
        ({"typing_delay": -0.1}, "typing_delay"),
        ({"read_mode": "sometimes"}, "read_mode"),
        ({"read_mode": None}, "read_mode"),
    ],
)
def test_constructor_rejects_unusable_settings(kwargs, message):
    kwargs.setdefault("self_user_id", USER)
    with pytest.raises(ValueError, match=message):
        Dispatcher(FakeBot(), **kwargs)


def test_username_is_stored_without_at_sign_and_lowercase():
    assert make(username="@MyBot").username == "mybot"
    assert make().username is None


# --- registration ------------------------------------------------------------------


@pytest.mark.parametrize("event_type", ["", "new_message", "new_messages"])
def test_on_event_refuses_message_events_and_empty_names(event_type):
    with pytest.raises(ValueError, match="command/on_message"):
        make().on_event(event_type)


def test_on_event_refuses_a_second_handler_for_the_same_type():
    dispatcher = make()
    dispatcher.on_event("custom")(lambda event: None)
    with pytest.raises(ValueError, match="already registered"):
        dispatcher.on_event("custom")(lambda event: None)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (lambda d: d.reaction(), lambda d: d.on_event("message_reactions")),
        (lambda d: d.on_event("message_reaction"), lambda d: d.reaction()),
        (lambda d: d.edited(), lambda d: d.edited()),
        (lambda d: d.edited(), lambda d: d.on_event("message_edited")),
        (lambda d: d.deleted(), lambda d: d.deleted()),
        (lambda d: d.deleted(), lambda d: d.on_event("messages_deleted")),
        (lambda d: d.on_event("message_deleted"), lambda d: d.deleted()),
    ],
    ids=[
        "reaction-then-event",
        "event-then-reaction",
        "edited-twice",
        "edited-then-event",
        "deleted-twice",
        "deleted-then-event",
        "event-then-deleted",
    ],
)
def test_event_decorators_do_not_share_event_names(first, second):
    dispatcher = make()
    first(dispatcher)(lambda event: None)
    with pytest.raises(ValueError, match="already registered"):
        second(dispatcher)(lambda event: None)


@pytest.mark.parametrize(
    "name", ["", "/", "a b", "a-b", "x" * 33, "ünï", "/a/b", "with\nline"]
)
def test_invalid_command_names_are_refused(name):
    with pytest.raises(ValueError, match="invalid command name"):
        make().command(name)


def test_command_name_is_case_insensitive_and_unique():
    dispatcher = make()
    calls, handler = recorder()
    dispatcher.command("/Echo")(handler)
    with pytest.raises(ValueError, match="already registered"):
        dispatcher.command("echo")(handler)
    dispatcher.handle(message_event(text="/ECHO  some words "))
    ((context,),) = calls
    assert (context.command, context.args) == ("echo", "some words")


def test_message_and_photo_handlers_are_exclusive():
    dispatcher = make()
    dispatcher.message()(lambda context: None)
    dispatcher.photo()(lambda context: None)
    with pytest.raises(ValueError, match="message handler already registered"):
        dispatcher.message()(lambda context: None)
    with pytest.raises(ValueError, match="photo handler already registered"):
        dispatcher.photo()(lambda context: None)
    with pytest.raises(ValueError, match="message handler already registered"):
        dispatcher.on_message(lambda event, message: None)


def test_message_refuses_to_replace_a_legacy_handler():
    dispatcher = make()
    with pytest.warns(DeprecationWarning, match=r"message\(\)"):
        dispatcher.on_message(lambda event, message: None)
    with pytest.raises(ValueError, match="message handler already registered"):
        dispatcher.message()(lambda context: None)


# --- edited(), deleted() and plain events ------------------------------------------


@pytest.mark.parametrize(
    ("register", "types"),
    [
        (lambda d: d.edited(), ["message_edited"]),
        (lambda d: d.deleted(), ["message_deleted", "messages_deleted"]),
        (lambda d: d.reaction(), ["message_reaction", "message_reactions"]),
    ],
    ids=["edited", "deleted", "reaction"],
)
def test_decorated_event_handlers_receive_an_event_context(register, types):
    bot = FakeBot()
    dispatcher = make(bot)
    calls, handler = recorder()
    register(dispatcher)(handler)
    for event_type in types:
        dispatcher.handle({"type": event_type, "chat_id": CHAT, "message_id": "m-1"})
    assert len(calls) == len(types)
    for (context,), event_type in zip(calls, types, strict=True):
        assert isinstance(context, EventContext) and context.bot is bot
        assert (context.chat_id, context.message_id) == (CHAT, "m-1")
        assert context.event["type"] == event_type
    assert bot.marked == []


def test_edited_and_deleted_handlers_only_get_their_own_events():
    dispatcher = make()
    edited, on_edit = recorder()
    deleted, on_delete = recorder()
    dispatcher.edited()(on_edit)
    dispatcher.deleted()(on_delete)
    dispatcher.handle({"type": "message_deleted", "chat_id": CHAT})
    assert edited == [] and len(deleted) == 1


def test_plain_event_handler_gets_the_raw_event():
    dispatcher = make()
    calls, handler = recorder()
    dispatcher.on_event("custom_event")(handler)
    event = {"type": "custom_event", "chat_id": CHAT, "x": 1}
    dispatcher.handle(event)
    assert calls == [(event,)]


@pytest.mark.parametrize("chat_id", [None, "", 5, ["chat"]])
def test_event_without_a_usable_chat_id_still_reaches_its_handler_without_typing(
    chat_id,
):
    bot = FakeBot()
    dispatcher = make(bot, auto_typing=True, typing_delay=0)
    calls, handler = recorder()
    dispatcher.on_event("custom_event")(handler)
    dispatcher.handle({"type": "custom_event", "chat_id": chat_id})
    assert len(calls) == 1 and bot.actions == []


@pytest.mark.parametrize("event", [{"type": "unknown_event"}, {}, {"type": None}])
def test_events_without_a_handler_are_ignored(event):
    bot = FakeBot()
    make(bot, read_mode="blocking").handle(event)
    assert bot.marked == [] and bot.actions == []


# --- invalid events and messages ---------------------------------------------------


def valid_message(**changes):
    return {"message_id": "msg-1", "sender_id": "someone-else", **changes}


@pytest.mark.parametrize(
    ("event", "match"),
    [
        (
            {"type": "new_message", "chat_id": CHAT, "message": None},
            "^invalid message$",
        ),
        ({"type": "new_message", "chat_id": CHAT}, "^invalid message$"),
        ({"type": "new_message", "chat_id": CHAT, "message": "x"}, "^invalid message$"),
        (
            {"type": "new_message", "chat_id": CHAT, "message": ["x"]},
            "^invalid message$",
        ),
        (
            {"type": "new_message", "chat_id": CHAT, "message": {"message_id": "m"}},
            "^invalid message$",
        ),
        (
            {
                "type": "new_message",
                "chat_id": CHAT,
                "message": valid_message(sender_id=7),
            },
            "^invalid message$",
        ),
        (
            {"type": "new_message", "message": valid_message()},
            "invalid message event",
        ),
        (
            {"type": "new_message", "chat_id": 5, "message": valid_message()},
            "invalid message event",
        ),
        (
            {"type": "new_messages", "chat_id": CHAT, "messages": "x"},
            "invalid message event",
        ),
        (
            {"type": "new_messages", "chat_id": CHAT, "messages": None},
            "invalid message event",
        ),
        (
            {"type": "new_messages", "chat_id": CHAT, "messages": {}},
            "invalid message event",
        ),
        (
            {"type": "new_messages", "messages": [valid_message()]},
            "invalid message event",
        ),
        (
            {
                "type": "new_messages",
                "chat_id": CHAT,
                "messages": [valid_message(), "x"],
            },
            "^invalid message$",
        ),
    ],
    ids=[
        "message-null",
        "message-missing",
        "message-string",
        "message-list",
        "no-sender",
        "sender-int",
        "no-chat",
        "chat-int",
        "messages-string",
        "messages-null",
        "messages-dict",
        "batch-no-chat",
        "batch-item-string",
    ],
)
def test_invalid_message_events_are_value_errors(event, match):
    with pytest.raises(ValueError, match=match):
        make().handle(event)


@pytest.mark.parametrize("message_id", [None, "", 5, ["m"]])
def test_incoming_message_without_an_id_is_refused_before_read_mark_and_handler(
    message_id,
):
    bot = FakeBot()
    dispatcher = make(bot, read_mode="blocking")
    calls, handler = recorder()
    dispatcher.message()(handler)
    with pytest.raises(ValueError, match="no message_id"):
        dispatcher.handle(message_event(message_id=message_id))
    assert bot.marked == [] and calls == []


def test_missing_message_id_is_also_refused():
    event = message_event()
    del event["message"]["message_id"]
    with pytest.raises(ValueError, match="no message_id"):
        make().handle(event)


def test_own_messages_are_skipped_even_when_they_are_malformed():
    bot = FakeBot()
    dispatcher = make(bot, read_mode="blocking")
    calls, handler = recorder()
    dispatcher.message()(handler)
    own = {"sender_id": USER}
    dispatcher.handle({"type": "new_message", "chat_id": CHAT, "message": own})
    dispatcher.handle(message_event(sender_id=USER))
    assert bot.marked == [] and calls == []


def test_batch_skips_own_messages_and_handles_the_rest_in_order():
    bot = FakeBot()
    dispatcher = make(bot, read_mode="blocking")
    calls, handler = recorder()
    dispatcher.message()(handler)
    messages = [
        valid_message(message_id="m-1"),
        valid_message(message_id="m-2", sender_id=USER),
        valid_message(message_id="m-3"),
    ]
    dispatcher.handle({"type": "new_messages", "chat_id": CHAT, "messages": messages})
    assert [c[0].message_id for c in calls] == ["m-1", "m-3"]
    assert bot.marked == [(CHAT, "m-1"), (CHAT, "m-3")]


def test_failed_blocking_read_mark_skips_the_handler_and_raises():
    bot = FakeBot()
    bot.mark_error = APIError(500, "Request failed")
    dispatcher = make(bot, read_mode="blocking")
    calls, handler = recorder()
    dispatcher.message()(handler)
    with pytest.raises(APIError):
        dispatcher.handle(message_event())
    assert calls == []


def test_read_mode_off_sends_no_marks_but_still_handles():
    bot = FakeBot()
    dispatcher = make(bot, read_mode="off")
    calls, handler = recorder()
    dispatcher.message()(handler)
    dispatcher.handle(message_event())
    assert bot.marked == [] and len(calls) == 1


# --- typing ------------------------------------------------------------------------


@pytest.fixture
def clock(monkeypatch):
    fake = Clock(100.0)
    patch_clock(monkeypatch, dispatcher_module, fake)
    return fake


def test_typing_failure_is_logged_without_details_and_not_raised(clock, caplog):
    bot = FakeBot()
    bot.action_error = RuntimeError("token=fictional-secret text=private-words")
    dispatcher = make(bot, typing_interval=4.0)
    with caplog.at_level(logging.WARNING, logger="itd_bot.dispatcher"):
        delay = dispatcher._send_typing(CHAT, threading.Event())
    assert delay == 4.0
    assert "Could not send typing action: RuntimeError" in caplog.text
    assert "fictional-secret" not in caplog.text and "private-words" not in caplog.text


def test_typing_is_throttled_per_chat_and_resumes_after_the_interval(clock):
    bot = FakeBot()
    dispatcher = make(bot, typing_interval=4.0)
    stopped = threading.Event()
    assert dispatcher._send_typing(CHAT, stopped) == 4.0
    clock.advance(1.5)
    assert dispatcher._send_typing(CHAT, stopped) == pytest.approx(2.5)
    assert dispatcher._send_typing("chat-2", stopped) == 4.0
    assert len(bot.actions) == 2
    clock.advance(2.5)
    dispatcher._send_typing(CHAT, stopped)
    assert bot.actions == [(CHAT, "typing"), ("chat-2", "typing"), (CHAT, "typing")]


def test_typing_is_not_sent_once_the_handler_has_finished(clock):
    bot = FakeBot()
    dispatcher = make(bot, typing_interval=4.0)
    stopped = threading.Event()
    stopped.set()
    assert dispatcher._send_typing(CHAT, stopped) == 4.0
    assert bot.actions == []
    stopped.clear()
    dispatcher._send_typing(CHAT, stopped)
    assert len(bot.actions) == 1


def test_oldest_chat_is_forgotten_when_the_typing_cache_is_full(clock):
    bot = FakeBot()
    dispatcher = make(bot, typing_interval=4.0)
    stopped = threading.Event()
    for number in range(4097):
        dispatcher._send_typing(f"chat-{number}", stopped)
    assert len(bot.actions) == 4097

    dispatcher._send_typing("chat-4096", stopped)
    dispatcher._send_typing("chat-2000", stopped)
    assert len(bot.actions) == 4097  # still remembered, so throttled

    dispatcher._send_typing("chat-0", stopped)
    assert bot.actions[-1] == ("chat-0", "typing")  # was forgotten, sent again
    assert len(bot.actions) == 4098
    assert len(dispatcher._typing_last) == 4096


def test_expired_chats_are_dropped_when_the_typing_cache_is_full(clock):
    dispatcher = make(FakeBot(), typing_interval=4.0)
    stopped = threading.Event()
    for number in range(4096):
        dispatcher._send_typing(f"chat-{number}", stopped)
    clock.advance(10)
    dispatcher._send_typing("fresh-chat", stopped)
    assert list(dispatcher._typing_last) == ["fresh-chat"]


# --- background read marks -----------------------------------------------------------


def test_failed_background_read_mark_is_logged_and_later_marks_still_work(caplog):
    bot = FakeBot()
    bot.mark_error = RuntimeError("token=fictional-secret text=private-words")
    dispatcher = make(bot, read_mode="background")
    calls, handler = recorder()
    dispatcher.message()(handler)
    with caplog.at_level(logging.WARNING, logger="itd_bot.dispatcher"):
        dispatcher.handle(message_event(message_id="m-1"))
        dispatcher.close()
    assert len(calls) == 1 and bot.marked == []
    assert "Could not mark message as read: RuntimeError" in caplog.text
    assert "fictional-secret" not in caplog.text and "private-words" not in caplog.text

    bot.mark_error = None
    dispatcher.handle(message_event(message_id="m-2"))
    dispatcher.close()
    dispatcher.close()
    assert bot.marked == [(CHAT, "m-2")]


def test_flush_without_a_pending_mark_does_nothing():
    bot = FakeBot()
    make(bot, read_mode="background")._flush_read(CHAT)
    assert bot.marked == []


# --- run_polling as a black box --------------------------------------------------------


def state(**changes):
    return {"version": 1, "user_id": USER, "base_url": BASE, "cursor": 5, **changes}


def write_state(path, **changes):
    path.write_text(json.dumps(state(**changes)), encoding="utf-8")


def read_state(path):
    return json.loads(path.read_text(encoding="utf-8"))


def files(directory):
    return sorted(p.name for p in directory.iterdir())


def disk_full(*args, **kwargs):
    raise OSError("disk full")


def test_polling_needs_a_cursor_or_a_state_file():
    bot = FakeBot()
    with pytest.raises(ValueError, match="since or state_path"):
        make(bot).run_polling()
    assert bot.polls == []


def test_polling_with_only_since_passes_it_and_the_options_on():
    bot = FakeBot()
    dispatcher = make(bot)
    dispatcher.run_polling(7, interval=0.5, handle_signals=False)
    assert bot.polls == [(7, dispatcher.handle, {"interval": 0.5})]


@pytest.mark.parametrize(
    "raw",
    [b"{", b"", b"\xff\xfe\x00", b"[]", b"null", b'"text"'],
    ids=["json", "empty", "binary", "list", "null", "string"],
)
def test_unreadable_state_file_is_a_protocol_error_and_stays_as_it_was(tmp_path, raw):
    path = tmp_path / "state.json"
    path.write_bytes(raw)
    bot = FakeBot()
    with pytest.raises(ProtocolError, match="(?i)polling state"):
        make(bot).run_polling(state_path=path)
    assert path.read_bytes() == raw
    assert bot.polls == [] and bot.chat_calls == []


def test_state_path_that_is_a_directory_is_a_protocol_error(tmp_path):
    bot = FakeBot()
    with pytest.raises(ProtocolError, match="Cannot read polling state") as info:
        make(bot).run_polling(state_path=tmp_path)
    assert isinstance(info.value.__cause__, OSError)
    assert bot.polls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"user_id": "someone-else"},
        {"user_id": None},
        {"base_url": "https://other.test"},
        {"version": 2},
        {"version": None},
    ],
    ids=["other-account", "no-account", "other-site", "other-version", "no-version"],
)
def test_state_of_another_account_or_site_is_refused_and_not_rewritten(
    tmp_path, changes
):
    path = tmp_path / "state.json"
    write_state(path, **changes)
    before = path.read_bytes()
    bot = FakeBot()
    with pytest.raises(ProtocolError, match="another account"):
        make(bot).run_polling(state_path=path)
    assert path.read_bytes() == before and bot.polls == []


@pytest.mark.parametrize("cursor", [None, -1, True, False, "5", 1.5, [5]])
def test_invalid_stored_cursor_is_refused_and_not_rewritten(tmp_path, cursor):
    path = tmp_path / "state.json"
    write_state(path, cursor=cursor)
    before = path.read_bytes()
    bot = FakeBot()
    with pytest.raises(ProtocolError, match="invalid cursor"):
        make(bot).run_polling(state_path=path)
    assert path.read_bytes() == before and bot.polls == []


def test_stored_cursor_without_the_key_is_refused(tmp_path):
    path = tmp_path / "state.json"
    raw = state()
    del raw["cursor"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ProtocolError, match="invalid cursor"):
        make().run_polling(state_path=path)


def test_since_cannot_override_an_existing_state_file(tmp_path):
    path = tmp_path / "state.json"
    write_state(path, cursor=5)
    before = path.read_bytes()
    bot = FakeBot()
    with pytest.raises(ValueError, match="since cannot be combined"):
        make(bot).run_polling(3, state_path=path)
    assert path.read_bytes() == before
    assert bot.polls == [] and bot.chat_calls == []


def test_existing_state_resumes_from_the_stored_cursor(tmp_path):
    path = tmp_path / "state.json"
    write_state(path, cursor=5)
    bot = FakeBot()
    make(bot).run_polling(state_path=path)
    assert [poll[0] for poll in bot.polls] == [5]
    assert bot.chat_calls == []


@pytest.mark.parametrize("chats", [None, ["x"], "x"], ids=["null", "list", "string"])
def test_chat_list_that_is_not_an_object_has_no_cursor(tmp_path, chats):
    path = tmp_path / "state.json"
    bot = FakeBot(chats=chats)
    with pytest.raises(ProtocolError, match="no valid event cursor"):
        make(bot).run_polling(state_path=path)
    assert files(tmp_path) == [] and bot.polls == []


@pytest.mark.parametrize(
    "chats",
    [
        {},
        {"event_seq": None},
        {"event_seq": -3},
        {"event_seq": True},
        {"event_seq": "4"},
    ],
    ids=["missing", "null", "negative", "bool", "string"],
)
def test_invalid_event_seq_in_the_chat_list_creates_no_state(tmp_path, chats):
    path = tmp_path / "state.json"
    bot = FakeBot(chats=chats)
    with pytest.raises(ProtocolError, match="invalid cursor"):
        make(bot).run_polling(state_path=path)
    assert files(tmp_path) == [] and bot.polls == []


def test_failing_chat_list_leaves_no_state_behind(tmp_path):
    bot = FakeBot()
    bot.chats_error = APIError(503, "Request failed")
    with pytest.raises(APIError):
        make(bot).run_polling(state_path=tmp_path / "state.json")
    assert files(tmp_path) == [] and bot.polls == []


def test_first_run_starts_from_the_server_cursor_and_saves_checkpoints(tmp_path):
    path = tmp_path / "state.json"
    bot = FakeBot(chats={"event_seq": 12})
    seen = []
    snapshots = []

    def checkpoint(cursor, kwargs):
        snapshots.append(read_state(path))
        kwargs["on_checkpoint"](20)
        snapshots.append(read_state(path))

    bot.on_poll = checkpoint
    make(bot).run_polling(state_path=path, on_checkpoint=seen.append)
    assert bot.chat_calls == [1]
    assert [poll[0] for poll in bot.polls] == [12]
    assert snapshots == [state(cursor=12), state(cursor=20)]
    assert seen == [20]
    assert files(tmp_path) == ["state.json"]


def test_first_run_with_since_saves_it_before_polling(tmp_path):
    path = tmp_path / "state.json"
    bot = FakeBot()
    at_poll = []
    bot.on_poll = lambda cursor, kwargs: at_poll.append(read_state(path))
    make(bot).run_polling(3, state_path=path)
    assert at_poll == [state(cursor=3)]
    assert bot.chat_calls == []


def test_first_save_failure_leaves_no_files_and_never_polls(tmp_path, monkeypatch):
    patch_os(monkeypatch, dispatcher_module, replace=disk_full)
    bot = FakeBot()
    with pytest.raises(OSError, match="disk full"):
        make(bot).run_polling(3, state_path=tmp_path / "state.json")
    assert files(tmp_path) == [] and bot.polls == []


def test_checkpoint_save_failure_keeps_the_old_cursor_and_removes_the_temp_file(
    tmp_path, monkeypatch
):
    path = tmp_path / "state.json"
    bot = FakeBot()

    def checkpoint(cursor, kwargs):
        patch_os(monkeypatch, dispatcher_module, replace=disk_full)
        kwargs["on_checkpoint"](9)

    bot.on_poll = checkpoint
    with pytest.raises(OSError, match="disk full"):
        make(bot).run_polling(3, state_path=path)
    assert read_state(path)["cursor"] == 3
    assert files(tmp_path) == ["state.json"]
