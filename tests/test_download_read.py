import json

import httpx
import pytest

from itd_bot import APIError, Bot, Dispatcher, ProtocolError

FILE_ID = "01234567-89ab-cdef-0123-456789abcdef"


def test_download_uses_link_session_cookie_and_stream(tmp_path):
    calls = []

    def handler(req):
        calls.append(req.url.path)
        if req.url.path.endswith("/file-links"):
            return httpx.Response(
                200,
                json={
                    "links": [
                        {
                            "fileId": FILE_ID,
                            "url": f"/v1/chat-files/{FILE_ID}?version=1",
                        }
                    ]
                },
            )
        if req.url.path.endswith("/browser-file-session"):
            assert req.headers["origin"] == "https://example.test"
            return httpx.Response(
                204, headers={"set-cookie": "file_session=abc; Path=/; Secure"}
            )
        assert req.url.params["version"] == "1"
        assert "file_session=abc" in req.headers["cookie"]
        return httpx.Response(200, content=b"file contents")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("secret", base_url="https://example.test", client=client)
        target = tmp_path / "file.bin"
        assert bot.download_file("chat", FILE_ID, destination=target) == target
        assert target.read_bytes() == b"file contents"
        with pytest.raises(FileExistsError):
            bot.download_file("chat", FILE_ID, destination=target)
        assert bot.download_file("chat", FILE_ID) == b"file contents"
        assert calls[:3] == [
            "/api/v1/messenger/chats/chat/file-links",
            "/api/v1/messenger/browser-file-session",
            f"/v1/chat-files/{FILE_ID}",
        ]


def test_download_rejects_external_urls_before_session():
    calls = []

    def handler(req):
        calls.append(req.url)
        return httpx.Response(
            200,
            json={
                "links": [
                    {
                        "fileId": FILE_ID,
                        "url": f"https://evil.test/v1/chat-files/{FILE_ID}",
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("secret", base_url="https://example.test", client=client)
        with pytest.raises(ProtocolError):
            bot.download_file("chat", FILE_ID)
    assert len(calls) == 1


def test_read_before_dispatch_including_nontext_and_other_bot_command():
    calls = []

    def handler(req):
        calls.append((req.url.path, json.loads(req.content)))
        return httpx.Response(204)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        router = Dispatcher(bot, self_user_id="self", username="mine")
        observed = []

        @router.command("start")
        def start(ctx):
            observed.append(len(calls))

        router.handle(
            {
                "type": "new_messages",
                "chat_id": "c",
                "messages": [
                    {"sender_id": "u", "message_id": "1", "text": "/start"},
                    {"sender_id": "u", "message_id": "2", "text": None},
                    {"sender_id": "u", "message_id": "3", "text": "/start@other"},
                    {"sender_id": "self", "message_id": "4", "text": "ignored"},
                ],
            }
        )
        assert observed == [2]
        assert [x[1]["last_read_message"] for x in calls if x[0].endswith("/read")] == [
            "1",
            "2",
            "3",
        ]


def test_failed_read_stops_before_handler():
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(403, json={"message": "denied"})
        )
    ) as client:
        router = Dispatcher(
            Bot("token", base_url="https://example.test", client=client),
            self_user_id="self",
        )
        observed = []

        @router.command("start")
        def start(ctx):
            observed.append(True)

        with pytest.raises(APIError):
            router.handle(
                {
                    "type": "new_message",
                    "chat_id": "c",
                    "message": {"sender_id": "u", "message_id": "m", "text": "/start"},
                }
            )
        assert not observed
