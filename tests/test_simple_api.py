import json
from pathlib import Path

import httpx
import pytest

from itd_bot import Bot, Dispatcher, ErrorAction, ProtocolError


class Stop(Exception):
    pass


def event(text="hello", attachments=None, message_id="m"):
    return {
        "type": "new_message",
        "chat_id": "chat",
        "message": {
            "message_id": message_id,
            "sender_id": "u",
            "text": text,
            "attachments": attachments or [],
        },
    }


def test_context_commands_photo_fallback_and_legacy():
    calls = []

    def transport(req):
        calls.append((req.method, req.url.path))
        if req.url.path.endswith("/read"):
            return httpx.Response(204)
        if req.url.path == "/api/users/u":
            return httpx.Response(200, json={"id": "u", "isFollowedBy": False})
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"message_id": "sent"})
        raise AssertionError(req.url)

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        bot = Bot("fake", base_url="https://example.test", client=client)
        router = Dispatcher(bot, self_user_id="self", auto_typing=False)
        seen = []

        @router.command("start")
        def command(ctx):
            seen.append((ctx.sender_id, ctx.text, ctx.args, ctx.is_subscribed()))
            ctx.reply("hi")

        @router.photo()
        def photo(ctx):
            seen.append((len(ctx.photo), ctx.photo[0].filename, ctx.photo[0].size))

        @router.message()
        def message(ctx):
            seen.append((ctx.chat_id, ctx.message_id, ctx.text))

        router.handle(event("/start args", message_id="command-1"))
        router.handle(
            event(
                "look",
                [
                    {
                        "fileId": "f",
                        "mime": "image/png",
                        "filename": "f.png",
                        "sizePlain": 3,
                    }
                ],
                message_id="photo-1",
            )
        )
        router.handle(event("normal"))
        assert seen == [
            ("u", "/start args", "args", False),
            (1, "f.png", 3),
            ("chat", "m", "normal"),
        ]
        assert calls[0][1].endswith("/read")
        with pytest.raises(ValueError):
            router.on_message(lambda e, m: None)
        legacy = Dispatcher(bot, self_user_id="self", auto_typing=False)
        old = []

        with pytest.warns(DeprecationWarning, match=r"message\(\)"):

            @legacy.on_message
            def old_handler(e, m):
                old.append((e["chat_id"], m["text"]))

        legacy.handle(event())
        assert old == [("chat", "hello")]


def test_attachment_download_and_message_actions(monkeypatch):
    bot = Bot("fake")
    seen = []
    monkeypatch.setattr(bot, "mark_read", lambda *args: None)
    monkeypatch.setattr(
        bot, "download_file", lambda *a, **kw: seen.append((a, kw)) or b"file"
    )
    monkeypatch.setattr(bot, "send_message", lambda *a, **kw: {"message_id": "sent"})
    monkeypatch.setattr(bot, "edit_message", lambda *a, **kw: seen.append((a, kw)))
    monkeypatch.setattr(bot, "delete_messages", lambda *a, **kw: seen.append((a, kw)))
    monkeypatch.setattr(bot, "forward_messages", lambda *a, **kw: seen.append((a, kw)))
    router = Dispatcher(bot, self_user_id="self", auto_typing=False)

    @router.message()
    def handle(ctx):
        assert ctx.attachments[0].download() == b"file"
        ctx.reply_message("before").edit("after")
        ctx.delete_message(for_everyone=True)
        ctx.forward_to("other")

    router.handle(event(attachments=[{"fileId": "f", "mime": "text/plain"}]))
    assert seen == [
        (("chat", "f"), {"destination": None}),
        (("chat", "sent", "after"), {}),
        (("chat", ["m"]), {"for_everyone": True}),
        (("other", "chat", ["m"]), {}),
    ]
    bot.close()


def test_reaction_and_event_handlers():
    requests = []

    def transport(req):
        requests.append(req)
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "reaction_version": "2",
                    "reactions": [
                        {"chosen": True, "reaction": {"type": "emoji", "emoji": "a"}},
                        {"chosen": True, "reaction": {"type": "emoji", "emoji": "b"}},
                    ],
                },
            )
        assert json.loads(req.content)["reactions"] == [{"type": "emoji", "emoji": "b"}]
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(transport)) as client:
        bot = Bot("fake", base_url="https://example.test", client=client)
        assert bot.remove_reaction("c", "m", "a") == {"ok": True}
        router = Dispatcher(bot, self_user_id="self", auto_typing=False)
        seen = []

        @router.reaction()
        def on_reaction(ctx):
            seen.append((ctx.chat_id, ctx.message_id, ctx.reactions))

        router.handle(
            {
                "type": "message_reactions",
                "chat_id": "c",
                "message_id": "m",
                "reactions": [],
            }
        )
        router.handle(
            {
                "type": "message_reaction",
                "chat_id": "c",
                "message_id": "n",
                "reactions": [],
            }
        )
        assert seen == [("c", "m", []), ("c", "n", [])]
        assert len(requests) == 2


def test_state_initialization_restart_and_account_binding(monkeypatch, tmp_path):
    bot = Bot("fake", base_url="https://example.test")
    calls, checkpoints = [], []
    monkeypatch.setattr(bot, "get_chats", lambda **kwargs: {"event_seq": 7})

    def poll(since, handler, *, on_checkpoint, **kwargs):
        calls.append(since)
        on_checkpoint(since + 1)
        raise Stop

    monkeypatch.setattr(bot, "poll", poll)
    path = tmp_path / "state.json"
    router = Dispatcher(bot, self_user_id="self")
    with pytest.raises(Stop):
        router.run_polling(state_path=path, on_checkpoint=checkpoints.append)
    assert json.loads(path.read_text()) == {
        "version": 1,
        "user_id": "self",
        "base_url": "https://example.test",
        "cursor": 8,
    }
    assert checkpoints == [8]
    with pytest.raises(Stop):
        router.run_polling(state_path=path)
    assert calls == [7, 8]
    with pytest.raises(ProtocolError):
        Dispatcher(bot, self_user_id="another").run_polling(state_path=path)
    with pytest.raises(ValueError):
        router.run_polling(0, state_path=path)
    bot.close()


def test_failed_handler_does_not_checkpoint(monkeypatch, tmp_path):
    bot = Bot("fake", base_url="https://example.test")
    monkeypatch.setattr(bot, "get_chats", lambda **kwargs: {"event_seq": 5})
    monkeypatch.setattr(
        bot, "get_updates", lambda since: {"events": [event()], "next_event_seq": 6}
    )
    monkeypatch.setattr(bot, "mark_read", lambda *args: None)
    router = Dispatcher(bot, self_user_id="self", auto_typing=False)

    @router.message()
    def broken(ctx):
        raise Stop

    @router.on_error
    def stop_on_error(exc, event):
        return ErrorAction.STOP

    path = tmp_path / "state.json"
    with pytest.raises(Stop):
        router.run_polling(state_path=path)
    assert json.loads(path.read_text())["cursor"] == 5
    bot.close()


def test_from_bot_and_invalid_attachment(monkeypatch):
    bot = Bot("fake")
    monkeypatch.setattr(
        bot, "get_me", lambda: type("Profile", (), {"id": "self", "username": "bot"})()
    )
    router = Dispatcher.from_bot(bot, auto_typing=False)
    assert router.self_user_id == "self" and router.username == "bot"
    assert Dispatcher.from_bot(bot, username="override").username == "override"
    monkeypatch.setattr(bot, "mark_read", lambda *args: None)

    @router.message()
    def handle(ctx):
        ctx.attachments[0].download(Path("x"))

    with pytest.raises(ProtocolError):
        router.handle(event(attachments=[{"mime": "image/png"}]))
    bot.close()
