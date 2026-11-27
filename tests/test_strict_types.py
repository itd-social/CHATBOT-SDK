"""Type hints improve checked callers without converting runtime JSON."""

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from helpers import Transport


@pytest.mark.parametrize(
    ("method", "args", "payload"),
    [
        (
            "get_chats",
            (),
            {
                "chats": [{"chat_id": "chat-1", "future": {"x": 1}}],
                "event_seq": 7,
                "future": True,
            },
        ),
        ("get_chat", ("chat-1",), {"chat_id": "chat-1", "future": [1]}),
        ("create_direct_chat", ("user-1",), {"chat_id": "chat-1", "future": None}),
        (
            "get_messages",
            ("chat-1",),
            {"messages": [{"message_id": "message-1", "future": True}], "future": 7},
        ),
        (
            "get_messages_around",
            ("chat-1", "message-1"),
            {"messages": [], "future": "value"},
        ),
    ],
)
def test_new_response_annotations_keep_raw_json_and_unknown_fields(
    method, args, payload
):
    transport = Transport(httpx.Response(200, json=payload))
    result = getattr(transport.bot(), method)(*args)
    assert type(result) is dict
    assert result == payload
    assert len(transport.requests) == 1


def test_casts_do_not_copy_or_validate_responses(monkeypatch):
    transport = Transport()
    bot = transport.bot()
    payload = {"future": object()}
    monkeypatch.setattr(bot, "request", lambda *args, **kwargs: payload)
    assert bot.get_chats() is payload
    assert bot.get_chat("chat-1") is payload
    assert bot.get_messages("chat-1") is payload
    assert not transport.requests


def test_checked_callers_get_schemas_and_preserved_decorator_signatures(tmp_path):
    pytest.importorskip("mypy")
    code = """from collections.abc import Callable
from itd_bot import Bot, ChatPayload, ChatsResponse, CommandContext, Dispatcher, ErrorAction, EventContext, MessageContext, MessagesResponse, UpdateEvent

def register(bot: Bot) -> None:
    chat: ChatPayload = bot.get_chat("chat-1")
    page: ChatsResponse = bot.get_chats()
    history: MessagesResponse = bot.get_messages("chat-1")
    chat_id: str = chat["chat_id"]
    first: str = page["chats"][0]["chat_id"]
    message_id: str = history["messages"][0]["message_id"]
    router = Dispatcher(bot, self_user_id="self")

    @router.command("start")
    def start(ctx: CommandContext) -> str:
        return ctx.args

    @router.message()
    def message(ctx: MessageContext) -> str | None:
        return ctx.text

    @router.reaction()
    def reaction(ctx: EventContext) -> str | None:
        return ctx.message_id

    @router.on_event("future")
    def event(data: UpdateEvent) -> str | None:
        return data.get("type")

    @router.on_error()
    def error(exc: Exception, data: UpdateEvent) -> ErrorAction:
        return ErrorAction.STOP

    command_callback: Callable[[CommandContext], str] = start
    message_callback: Callable[[MessageContext], str | None] = message
    reaction_callback: Callable[[EventContext], str | None] = reaction
    event_callback: Callable[[UpdateEvent], str | None] = event
    direct_callback: Callable[[Exception, UpdateEvent], ErrorAction] = router.on_error(error)
"""
    root = Path(__file__).resolve().parents[1]
    env = {**os.environ, "PYTHONUTF8": "1"}

    def check(source):
        caller = tmp_path / "caller.py"
        caller.write_text(source, encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "mypy",
                "--strict",
                "--python-version",
                "3.10",
                "--cache-dir",
                str(tmp_path / "mypy-cache"),
                str(caller),
            ],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

    positive = check(code)
    assert positive.returncode == 0, positive.stdout + positive.stderr
    negative = check(
        code
        + '\n    bad: ChatPayload = {"chat_id": 17}\n    wrong: int = message_callback\n'
    )
    assert negative.returncode == 1
    assert "typeddict-item" in negative.stdout
    assert "assignment" in negative.stdout
