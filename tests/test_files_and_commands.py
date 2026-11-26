import json

import httpx
import pytest

from itd_bot import APIError, Bot, Dispatcher


def test_chunked_file_and_send():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/init"):
            assert json.loads(request.content) == {
                "mime": "application/octet-stream",
                "size": 5242883,
                "totalParts": 2,
            }
            return httpx.Response(200, json={"uploadId": "up"})
        if "/part/" in request.url.path:
            return httpx.Response(204)
        if request.url.path.endswith("/finalize"):
            assert json.loads(request.content) == {"filename": "test.bin"}
            return httpx.Response(200, json={"fileId": "file-1"})
        if request.url.path.endswith("/messages"):
            assert json.loads(request.content)["attachments"] == ["file-1"]
            return httpx.Response(200, json={"message_id": "sent"})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        progress = []
        assert bot.send_file(
            "chat",
            b"x" * 5242883,
            filename="test.bin",
            on_progress=lambda a, b: progress.append((a, b)),
        ) == {"message_id": "sent"}
        assert [len(x[2]) for x in seen if "/part/" in x[1]] == [5242880, 3]
        assert progress == [(5242880, 5242883), (5242883, 5242883)]


@pytest.mark.parametrize("send", [False, True])
def test_experimental_file_preserves_bytes_and_independent_metadata(tmp_path, send):
    payload = b"ID3\x00\x01audio data"
    source = tmp_path / "track.mp3"
    source.write_bytes(payload)
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path.endswith("/init"):
            assert json.loads(request.content) == {
                "mime": "video/mp4",
                "size": len(payload),
                "totalParts": 1,
            }
            return httpx.Response(200, json={"uploadId": "up"})
        if request.url.path.endswith("/part/0"):
            assert request.content == payload
            return httpx.Response(204)
        if request.url.path.endswith("/finalize"):
            assert json.loads(request.content) == {"filename": "photo.png"}
            return httpx.Response(200, json={"fileId": "file-1"})
        if request.url.path.endswith("/messages"):
            assert json.loads(request.content)["attachments"] == ["file-1"]
            return httpx.Response(200, json={"message_id": "sent"})
        raise AssertionError(request.url)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        if send:
            result = bot.send_file(
                "chat", source, filename="photo.png", mime="video/mp4"
            )
            assert result == {"message_id": "sent"}
        else:
            result = bot.upload_file(source, filename="photo.png", mime="video/mp4")
            assert result == {"fileId": "file-1"}
    assert any(path.endswith("/messages") for path in seen) is send


def test_failed_part_does_not_finalize():
    paths = []

    def handler(req):
        paths.append(req.url.path)
        return (
            httpx.Response(200, json={"uploadId": "up"})
            if req.url.path.endswith("/init")
            else httpx.Response(500, json={"error": {"code": "FAILED"}})
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        with pytest.raises(APIError):
            bot.upload_file(b"abc")
    assert len(paths) == 2


def test_general_api_and_path_validation():
    def handler(req):
        assert req.url.path == "/api/users/me"
        return httpx.Response(200, json={"id": "x"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        assert bot.api_request("GET", "/api/users/me")["id"] == "x"
        for bad in (
            "//evil.test",
            "/api/../secret",
            "/api/%2e%2e/secret",
            "/api/a?x=1",
        ):
            with pytest.raises(ValueError):
                bot.api_request("GET", bad)
        with pytest.raises(ValueError):
            bot.api_request("GET", "/api/users/me", headers={"Authorization": "bad"})


def test_command_routing_and_self_filter():
    replies = []

    def handler(req):
        if req.url.path.endswith(("/read", "/action")):
            return httpx.Response(204)
        replies.append(json.loads(req.content))
        return httpx.Response(200, json={"message_id": "sent"})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        router = Dispatcher(bot, self_user_id="self", username="mybot")
        seen = []

        @router.command("/start")
        def start(ctx):
            seen.append(ctx.args)
            ctx.reply("Hi")

        def event(text, sender="other", message_id="m"):
            return {
                "type": "new_message",
                "chat_id": "chat",
                "message": {
                    "message_id": message_id,
                    "sender_id": sender,
                    "text": text,
                },
            }

        router.handle(event("/start hello world"))
        router.handle(event("/start@mybot arg", message_id="m-2"))
        router.handle(event("/start@someone_else", message_id="m-3"))
        router.handle(event("/start", "self", message_id="m-4"))
        router.handle(event("/startx", message_id="m-5"))
        assert seen == ["hello world", "arg"]
        assert len(replies) == 2 and replies[0]["reply_to_id"] == "m"


def test_command_batch_and_unknown_fallback():
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json={}))
    ) as client:
        router = Dispatcher(
            Bot("token", base_url="https://example.test", client=client),
            self_user_id="self",
        )
        seen = []

        with pytest.warns(DeprecationWarning, match=r"message\(\)"):

            @router.on_message
            def fallback(event, message):
                seen.append(message["text"])

        router.handle(
            {
                "type": "new_messages",
                "chat_id": "c",
                "messages": [
                    {"message_id": "1", "sender_id": "u", "text": "/unknown"},
                    {"message_id": "2", "sender_id": "self", "text": "no"},
                    {"message_id": "3", "sender_id": "u", "text": "hello"},
                ],
            }
        )
        assert seen == ["/unknown", "hello"]


def test_websocket_event_and_cleanup(monkeypatch):
    websocket = pytest.importorskip("websocket")

    class FakeSocket:
        handshake_response = type("Handshake", (), {"status": 101})()
        closed = False

        def settimeout(self, timeout):
            pass

        def recv(self):
            return '{"type":"presence","user_id":"u","online":true}'

        def close(self):
            self.closed = True

    conn = FakeSocket()

    def connect(url, **kwargs):
        assert (
            url
            == "wss://example.test/api/v1/messenger/ws?capabilities=chat_settings_v2"
        )
        assert kwargs["redirect_limit"] == 0
        assert kwargs["subprotocols"] == ["itd.messenger.v1", "bearer.token"]
        return conn

    monkeypatch.setattr(websocket, "create_connection", connect)
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200))
    ) as client:
        events = Bot(
            "token", base_url="https://example.test", client=client
        ).websocket_events()
        assert next(events)["type"] == "presence"
        events.close()
        assert conn.closed
