import base64
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import itd_bot.auth as auth
from itd_bot import Bot


def test_refresh_bot_survives_access_expiry(monkeypatch, tmp_path, capsys):
    spec = importlib.util.spec_from_file_location(
        "refresh_bot_example",
        Path(__file__).parents[1] / "examples" / "refresh_test_bot.py",
    )
    example = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(example)
    clock = [1000]
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: clock[0]))
    refreshes = []
    updates = []

    def token(exp):
        part = (
            base64.urlsafe_b64encode(json.dumps({"sub": "bot", "exp": exp}).encode())
            .decode()
            .rstrip("=")
        )
        return f"header.{part}.sig"

    def handler(req):
        if req.url.path.endswith("/auth/refresh"):
            refreshes.append(req.headers["cookie"])
            value = "one" if len(refreshes) == 1 else "two"
            return httpx.Response(
                200,
                headers={"set-cookie": f"refresh_token={value}; Path=/api/v1/auth"},
                json={"accessToken": token(clock[0] + 900)},
            )
        if req.url.path == "/api/users/me":
            return httpx.Response(200, json={"id": "bot"})
        if req.url.path.endswith("/chats"):
            return httpx.Response(200, json={"event_seq": 3, "chats": []})
        if req.url.path.endswith("/updates"):
            updates.append(1)
            if len(updates) == 1:
                return httpx.Response(
                    200,
                    json={
                        "events": [
                            {
                                "type": "new_message",
                                "chat_id": "c",
                                "message": {
                                    "message_id": "m",
                                    "sender_id": "u",
                                    "text": "/ping",
                                },
                            }
                        ],
                        "reset_required": False,
                        "next_event_seq": 4,
                    },
                )
            return httpx.Response(
                200, json={"events": [], "reset_required": False, "next_event_seq": 4}
            )
        if req.url.path.endswith(("/read", "/action")):
            return httpx.Response(204)
        if req.url.path.endswith("/messages"):
            assert json.loads(req.content)["text"] == "pong"
            return httpx.Response(200, json={"message_id": "r"})
        raise AssertionError(req.url.path)

    def sleep(_):
        if len(updates) == 1:
            clock[0] = 1901
        else:
            raise KeyboardInterrupt()

    monkeypatch.setattr(example, "time", SimpleNamespace(sleep=sleep))
    monkeypatch.setenv("ITD_REFRESH_TOKEN", "seed")
    monkeypatch.setenv("ITD_SESSION_FILE", str(tmp_path / "session"))
    monkeypatch.setenv("ITD_CURSOR_FILE", str(tmp_path / "cursor"))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:

        class TestBot:
            @staticmethod
            def from_refresh_token(value, session_path):
                return Bot.from_refresh_token(
                    value,
                    session_path=session_path,
                    base_url="https://example.test",
                    client=client,
                )

        monkeypatch.setattr(example, "Bot", TestBot)
        with pytest.raises(KeyboardInterrupt):
            example.main()
    assert refreshes == ["refresh_token=seed", "refresh_token=one"]
    assert json.loads((tmp_path / "session").read_text())["refresh_token"] == "two"
    assert (tmp_path / "cursor").read_text() == "4"
    assert "Всего обновлений: 2" in capsys.readouterr().out
