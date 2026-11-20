import importlib.util
import json
from pathlib import Path

import httpx

from itd_bot import Bot, Dispatcher


def test_test_bot_ping_reads_before_answer(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location(
        "test_bot_example", Path(__file__).parents[1] / "examples" / "test_bot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/chats"):
            return httpx.Response(200, json={"chats": [], "event_seq": 5})
        if req.url.path.endswith("/read"):
            assert json.loads(req.content)["last_read_message"] == "m1"
            return httpx.Response(204)
        if req.url.path.endswith("/action"):
            assert json.loads(req.content) == {"type": "typing"}
            return httpx.Response(204)
        if req.url.path.endswith("/messages"):
            assert json.loads(req.content)["text"] == "pong"
            return httpx.Response(200, json={"message_id": "reply"})
        raise AssertionError(req.url)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(
            module,
            "Bot",
            lambda token, base_url: Bot(token, base_url=base_url, client=client),
        )

        def one_event(self, since, **kwargs):
            assert since == 5
            self.handle(
                {
                    "type": "new_message",
                    "chat_id": "c",
                    "message": {
                        "message_id": "m1",
                        "sender_id": "tester",
                        "text": "/ping",
                    },
                }
            )
            kwargs["on_checkpoint"](6)

        monkeypatch.setattr(Dispatcher, "run_polling", one_event)
        monkeypatch.setenv("ITD_TOKEN", "test")
        monkeypatch.setenv("ITD_SELF_USER_ID", "self")
        monkeypatch.setenv("ITD_TESTER_ID", "tester")
        cursor = tmp_path / "cursor"
        monkeypatch.setenv("ITD_CURSOR_FILE", str(cursor))
        module.main()
        assert calls == [
            "/api/v1/messenger/chats",
            "/api/v1/messenger/chats/c/read",
            "/api/v1/messenger/chats/c/messages",
        ]
        assert cursor.read_text() == "6"
