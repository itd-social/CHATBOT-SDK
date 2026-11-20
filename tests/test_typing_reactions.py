import json
import threading
import time

import httpx

from itd_bot import Bot, Dispatcher


def test_typing_repeats_and_stops_on_handler_failure():
    actions = []
    twice = threading.Event()

    def handler(req):
        if req.url.path.endswith("/action"):
            actions.append(time.monotonic())
            if len(actions) >= 2:
                twice.set()
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        router = Dispatcher(
            Bot("token", base_url="https://example.test", client=client),
            self_user_id="self",
            typing_interval=0.01,
        )

        @router.command("slow")
        def slow(ctx):
            assert twice.wait(1)
            raise RuntimeError("failed")

        try:
            router.handle(
                {
                    "type": "new_message",
                    "chat_id": "c",
                    "message": {"sender_id": "u", "message_id": "m", "text": "/slow"},
                }
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("handler error did not propagate")
        count = len(actions)
        time.sleep(0.04)
        assert len(actions) == count


def test_reactions_handler_and_add_preserve_existing():
    calls = []

    def handler(req):
        calls.append(req)
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "reaction_version": "9",
                    "reactions": [
                        {"chosen": True, "reaction": {"type": "emoji", "emoji": "❤"}},
                        {"chosen": False, "reaction": {"type": "emoji", "emoji": "😂"}},
                    ],
                },
            )
        if req.method == "PUT":
            body = json.loads(req.content)
            assert body["expected_version"] == "9"
            assert body["reactions"] == [
                {"type": "emoji", "emoji": "❤"},
                {"type": "emoji", "emoji": "👍"},
            ]
            assert body["operation_id"]
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        assert bot.add_reaction("c", "m", "👍") == {"ok": True}
        router = Dispatcher(bot, self_user_id="self", auto_typing=False)
        events = []

        @router.on_event("message_reactions")
        def on_reactions(event):
            events.append(event["message_id"])

        router.handle(
            {
                "type": "message_reactions",
                "chat_id": "c",
                "message_id": "m",
                "reactions": [],
            }
        )
        assert events == ["m"]
