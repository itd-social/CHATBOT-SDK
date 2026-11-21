import io
import json
import shutil
import subprocess

import httpx
import pytest

from itd_bot import Bot, Dispatcher


def make_client(captured):
    def handler(req):
        if req.url.path.endswith("/init"):
            captured["init"] = json.loads(req.content)
            return httpx.Response(200, json={"uploadId": "upload"})
        if "/part/" in req.url.path:
            captured.setdefault("parts", []).append(req.content)
            return httpx.Response(204)
        if req.url.path.endswith("/finalize"):
            captured["finalize"] = json.loads(req.content)
            return httpx.Response(200, json={"fileId": "file"})
        if req.url.path.endswith("/messages"):
            captured["message"] = json.loads(req.content)
            return httpx.Response(200, json={"message_id": "sent"})
        if req.url.path.endswith("/read"):
            return httpx.Response(204)
        raise AssertionError(req.url.path)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_photo_compression_and_uncompressed(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "picture.png"
    Image.new("RGBA", (2000, 1000), (255, 0, 0, 100)).save(path)
    original = path.read_bytes()
    for compress in (False, True):
        sent = {}
        with make_client(sent) as client:
            bot = Bot("token", base_url="https://example.test", client=client)
            bot.send_photo("chat", path, caption="hello", compress=compress)
        data = b"".join(sent["parts"])
        assert sent["message"]["text"] == "hello"
        if compress:
            assert sent["init"]["mime"] == "image/jpeg"
            assert sent["finalize"] == {
                "filename": "picture.jpg",
                "width": 1920,
                "height": 960,
            }
            assert Image.open(io.BytesIO(data)).format == "JPEG"
        else:
            assert sent["init"]["mime"] == "image/png"
            assert data == original
            assert sent["finalize"] == {"filename": "picture.png"}


def test_compressed_gif_preserves_animation_bytes(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    path = tmp_path / "animated.gif"
    Image.new("RGB", (2, 3), "red").save(
        path, save_all=True, append_images=[Image.new("RGB", (2, 3), "blue")]
    )
    sent = {}
    with make_client(sent) as client:
        Bot("token", base_url="https://example.test", client=client).send_photo(
            "chat", path, compress=True
        )
    assert b"".join(sent["parts"]) == path.read_bytes()
    assert sent["init"]["mime"] == "image/gif"
    assert sent["finalize"] == {"filename": "animated.gif", "width": 2, "height": 3}


def test_music_video_and_reply_preserve_bytes():
    sent = {}
    with make_client(sent) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        bot.send_video("chat", b"video", filename="v.mp4", caption="video")
        assert b"".join(sent["parts"]) == b"video"
        assert sent["init"]["mime"] == "video/mp4"
        sent.clear()
        bot.send_music("chat", b"music", filename="a.mp3", caption="music")
        assert b"".join(sent["parts"]) == b"music"
        assert sent["init"]["mime"] == "audio/mpeg"
        sent.clear()
        router = Dispatcher(bot, self_user_id="self", auto_typing=False)

        @router.command("song")
        def song(ctx):
            ctx.reply_music(b"music", filename="a.mp3", caption="music")

        router.handle(
            {
                "type": "new_message",
                "chat_id": "chat",
                "message": {"sender_id": "u", "message_id": "m", "text": "/song"},
            }
        )
        assert sent["message"]["reply_to_id"] == "m"


def test_video_compression_with_ffmpeg(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("ffmpeg not installed")
    video = tmp_path / "input.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=32x32:d=0.2",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )
    sent = {}
    with make_client(sent) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        bot.send_video("chat", video, compress=True, caption="clip")
    assert sent["init"]["mime"] == "video/mp4"
    assert sent["finalize"]["filename"] == "input.mp4"
    assert sent["message"]["text"] == "clip"
    assert b"ftyp" in b"".join(sent["parts"])[:32]


def test_video_compression_failure_does_not_upload(tmp_path):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not a video")
    sent = {}
    with make_client(sent) as client:
        bot = Bot("token", base_url="https://example.test", client=client)
        with pytest.raises(RuntimeError, match="Video compression failed"):
            bot.send_video("chat", path, compress=True)
    assert sent == {}
