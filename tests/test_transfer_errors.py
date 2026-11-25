"""Uploads, ffmpeg compression, downloads and banners: failures and what remains."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest
from helpers import (
    API,
    FAR_FUTURE,
    Transport,
    httpx_namespace,
    jwt,
    sent_json,
    strict_limiter,
)

import itd_bot.client as client_module
from itd_bot import APIError, ProtocolError, RateLimitError

PART = 5 * 1024 * 1024
FILE_ID = "01234567-89ab-cdef-0123-456789abcdef"
FFMPEG = "/fictional/bin/ffmpeg"


def upload_handler(*, init=None, part=None, finalize=None, message=None):
    """Answer every step of the multipart upload and the final message."""

    def handler(request):
        path = request.url.path
        if path.endswith("/media/upload/init"):
            return init or httpx.Response(200, json={"uploadId": "upload-1"})
        if "/part/" in path:
            return part or httpx.Response(204)
        if path.endswith("/finalize"):
            return finalize or httpx.Response(200, json={"fileId": "file-1"})
        if path.endswith("/messages"):
            return message or httpx.Response(200, json={"message_id": "msg-1"})
        raise AssertionError(f"unexpected {request.method} {path}")

    return handler


def upload_bot(**handlers):
    transport = Transport(default=upload_handler(**handlers))
    return transport, transport.bot()


def only(transport, suffix):
    return [r for r in transport.requests if r.url.path.endswith(suffix)]


class FakeStream:
    """File object that reports ``size`` bytes but can end early (``limit``)."""

    def __init__(self, size, *, limit=None):
        self.size, self.limit = size, size if limit is None else limit
        self.pos = 0
        self.closed = False

    def seek(self, offset, whence=os.SEEK_SET):
        self.pos = self.size + offset if whence == os.SEEK_END else offset

    def tell(self):
        return self.pos

    def read(self, count):
        count = max(0, min(count, self.limit - self.pos))
        self.pos += count
        return b"x" * count

    def close(self):
        self.closed = True


def use_stream(monkeypatch, stream):
    monkeypatch.setattr(client_module, "open", lambda *a, **k: stream, raising=False)


# --- upload_file -----------------------------------------------------------------


def test_empty_bytes_are_rejected_before_any_request():
    transport, bot = upload_bot()
    with pytest.raises(ValueError, match="between 1 byte and 2 GiB"):
        bot.upload_file(b"")
    assert transport.requests == []


def test_empty_file_is_rejected_before_any_request(tmp_path):
    empty = tmp_path / "empty.bin"
    empty.write_bytes(b"")
    transport, bot = upload_bot()
    with pytest.raises(ValueError, match="between 1 byte and 2 GiB"):
        bot.upload_file(empty)
    assert transport.requests == []


def test_file_over_two_gib_is_rejected_without_reading_it(monkeypatch, tmp_path):
    stream = FakeStream(2 * 1024**3 + 1)
    use_stream(monkeypatch, stream)
    transport, bot = upload_bot()
    with pytest.raises(ValueError, match="2 GiB"):
        bot.upload_file(tmp_path / "huge.bin")
    assert transport.requests == [] and stream.closed and stream.pos == 0


def test_file_of_exactly_two_gib_is_announced_in_parts(monkeypatch, tmp_path):
    size = 2 * 1024**3
    use_stream(monkeypatch, FakeStream(size))
    transport, bot = upload_bot(init=httpx.Response(200, json={}))
    with pytest.raises(ProtocolError):
        bot.upload_file(tmp_path / "big.bin", mime="video/mp4")
    body = sent_json(transport.requests[0])
    assert body == {"mime": "video/mp4", "size": size, "totalParts": size // PART + 1}
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "payload",
    [None, ["upload-1"], {}, {"uploadId": ""}, {"uploadId": 5}, {"uploadId": None}],
    ids=["null", "list", "empty", "empty-id", "int-id", "null-id"],
)
def test_invalid_upload_init_response_sends_no_data(payload):
    transport, bot = upload_bot(init=httpx.Response(200, json=payload))
    with pytest.raises(ProtocolError, match="initialization"):
        bot.upload_file(b"data")
    assert len(transport.requests) == 1


def test_failed_upload_init_is_an_api_error_without_parts():
    transport, bot = upload_bot(init=httpx.Response(500, json={"error": {}}))
    with pytest.raises(APIError) as info:
        bot.upload_file(b"data")
    assert info.value.status == 500 and len(transport.requests) == 1


@pytest.mark.parametrize("upload_id", ["..", "."])
def test_dot_upload_id_is_refused_before_any_part(upload_id):
    transport, bot = upload_bot(init=httpx.Response(200, json={"uploadId": upload_id}))
    with pytest.raises(ValueError, match="ID"):
        bot.upload_file(b"data")
    assert only(transport, "/init") and not only(transport, "/part/0")


def test_upload_id_is_quoted_in_the_part_and_finalize_paths():
    transport, bot = upload_bot(
        init=httpx.Response(200, json={"uploadId": "a/b?c"}),
    )
    bot.upload_file(b"data")
    raw_paths = [r.url.raw_path.split(b"?")[0] for r in transport.requests]
    assert raw_paths[1:] == [
        (API + "/media/upload/a%2Fb%3Fc/part/0").encode(),
        (API + "/media/upload/a%2Fb%3Fc/finalize").encode(),
    ]


@pytest.mark.parametrize(
    "payload",
    [None, ["file-1"], {}, {"fileId": ""}, {"fileId": 5}, {"fileId": None}],
    ids=["null", "list", "empty", "empty-id", "int-id", "null-id"],
)
def test_invalid_finalize_response_is_a_protocol_error_and_nothing_is_sent(payload):
    transport, bot = upload_bot(finalize=httpx.Response(200, json=payload))
    with pytest.raises(ProtocolError, match="finalization"):
        bot.send_file("chat-1", b"data")
    assert len(only(transport, "/part/0")) == 1
    assert only(transport, "/messages") == []


def test_failed_finalize_is_an_api_error_and_nothing_is_sent():
    transport, bot = upload_bot(finalize=httpx.Response(500, json={}))
    with pytest.raises(APIError):
        bot.send_file("chat-1", b"data")
    assert only(transport, "/messages") == []


def test_failed_part_stops_the_upload_and_closes_the_file(monkeypatch, tmp_path):
    stream = FakeStream(10)
    use_stream(monkeypatch, stream)
    transport, bot = upload_bot(part=httpx.Response(500, json={}))
    with pytest.raises(APIError):
        bot.upload_file(tmp_path / "a.bin")
    assert only(transport, "/finalize") == [] and stream.closed


def test_file_that_ends_early_stops_before_the_part_and_is_closed(
    monkeypatch, tmp_path
):
    stream = FakeStream(10, limit=7)
    use_stream(monkeypatch, stream)
    transport, bot = upload_bot()
    with pytest.raises(OSError, match="changed"):
        bot.upload_file(tmp_path / "a.bin")
    assert only(transport, "/part/0") == [] and only(transport, "/finalize") == []
    assert stream.closed


def test_file_that_shrinks_after_the_first_part_is_not_finalized(monkeypatch, tmp_path):
    stream = FakeStream(PART + 10, limit=PART)
    use_stream(monkeypatch, stream)
    transport, bot = upload_bot()
    with pytest.raises(OSError, match="changed"):
        bot.upload_file(tmp_path / "a.bin")
    assert len(only(transport, "/part/0")) == 1
    assert only(transport, "/part/1") == [] and only(transport, "/finalize") == []
    assert stream.closed


def test_progress_is_reported_per_part_and_parts_are_sent_in_order():
    data = b"a" * PART + b"bcd"
    transport, bot = upload_bot()
    seen = []
    bot.upload_file(data, on_progress=lambda sent, size: seen.append((sent, size)))
    assert seen == [(PART, PART + 3), (PART + 3, PART + 3)]
    init, first, second, finalize = transport.requests
    assert sent_json(init) == {
        "mime": "application/octet-stream",
        "size": PART + 3,
        "totalParts": 2,
    }
    assert first.url.path.endswith("/part/0") and len(first.content) == PART
    assert second.url.path.endswith("/part/1") and second.content == b"bcd"
    assert first.method == second.method == "PUT"
    assert first.headers["content-type"] == "application/octet-stream"
    assert sent_json(finalize) == {}


def test_failing_progress_callback_stops_the_upload():
    transport, bot = upload_bot()

    def stop(sent, size):
        raise RuntimeError("stop here")

    with pytest.raises(RuntimeError, match="stop here"):
        bot.upload_file(b"a" * PART + b"b", on_progress=stop)
    assert len(only(transport, "/part/0")) == 1
    assert only(transport, "/part/1") == [] and only(transport, "/finalize") == []


def test_filename_and_metadata_reach_finalize_without_changing_the_input():
    transport, bot = upload_bot()
    metadata = {"width": 3}
    bot.upload_file(b"data", filename="a.bin", mime="image/png", metadata=metadata)
    assert sent_json(only(transport, "/finalize")[0]) == {
        "width": 3,
        "filename": "a.bin",
    }
    assert metadata == {"width": 3}


def test_send_audio_uploads_then_sends_one_message():
    transport, bot = upload_bot()
    bot.send_audio(
        "chat-1",
        b"data",
        caption="listen",
        filename="song.mp3",
        mime="audio/mpeg",
        reply_to_id="msg-0",
        client_message_id="client-1",
    )
    assert [r.url.path.removeprefix(API) for r in transport.requests] == [
        "/media/upload/init",
        "/media/upload/upload-1/part/0",
        "/media/upload/upload-1/finalize",
        "/chats/chat-1/messages",
    ]
    assert sent_json(transport.requests[0])["mime"] == "audio/mpeg"
    assert sent_json(transport.requests[-1]) == {
        "text": "listen",
        "reply_to_id": "msg-0",
        "attachments": ["file-1"],
        "client_message_id": "client-1",
    }


# --- send_video(compress=True) ---------------------------------------------------


class FakeFfmpeg:
    """Replacement for ``subprocess.run`` that never starts a process."""

    def __init__(self, *, error=None):
        self.error, self.calls, self.input_bytes = error, [], None

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        self.input_bytes = Path(argv[argv.index("-i") + 1]).read_bytes()
        if self.error is not None:
            raise self.error
        Path(argv[-1]).write_bytes(b"compressed")

    @property
    def work_dir(self):
        return Path(self.calls[0][0][-1]).parent


@pytest.fixture
def ffmpeg(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: FFMPEG)
    fake = FakeFfmpeg()
    monkeypatch.setattr(subprocess, "run", fake)
    return fake


def test_video_without_compression_never_looks_for_ffmpeg(monkeypatch):
    def forbidden(name):
        raise AssertionError("ffmpeg must not be looked up")

    monkeypatch.setattr(shutil, "which", forbidden)
    transport, bot = upload_bot()
    bot.send_video("chat-1", b"video", caption="plain")
    assert sent_json(only(transport, "/messages")[0])["text"] == "plain"


def test_missing_ffmpeg_is_reported_before_anything_is_read_or_sent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    called = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: called.append(a))
    transport, bot = upload_bot()
    with pytest.raises(RuntimeError, match="requires ffmpeg"):
        bot.send_video("chat-1", b"video", compress=True)
    assert called == [] and transport.requests == []


@pytest.mark.parametrize("kind", ["missing", "directory"])
def test_unreadable_video_source_is_file_not_found(ffmpeg, tmp_path, kind):
    source = tmp_path / "nothing.mp4" if kind == "missing" else tmp_path
    transport, bot = upload_bot()
    with pytest.raises(FileNotFoundError):
        bot.send_video("chat-1", source, compress=True)
    assert ffmpeg.calls == [] and transport.requests == []


def test_video_bytes_are_compressed_from_a_temp_file_and_sent_as_mp4(ffmpeg):
    transport, bot = upload_bot()
    bot.send_video(
        "chat-1",
        b"raw-video",
        caption="clip",
        compress=True,
        filename="clip.mov",
        mime="video/x-ignored",
        reply_to_id="msg-0",
    )
    ((argv, kwargs),) = ffmpeg.calls
    assert argv[0] == FFMPEG
    assert {"-nostdin", "-y", "libx264", "aac", "+faststart"} <= set(argv)
    assert kwargs["check"] is True
    assert ffmpeg.input_bytes == b"raw-video"
    assert not ffmpeg.work_dir.exists()
    assert sent_json(only(transport, "/init")[0])["mime"] == "video/mp4"
    assert sent_json(only(transport, "/init")[0])["size"] == len(b"compressed")
    assert sent_json(only(transport, "/finalize")[0])["filename"] == "clip.mp4"
    assert only(transport, "/part/0")[0].content == b"compressed"
    assert sent_json(only(transport, "/messages")[0]) == {
        "text": "clip",
        "reply_to_id": "msg-0",
        "attachments": ["file-1"],
    }


def test_video_bytes_without_a_name_get_a_generic_mp4_name(ffmpeg):
    transport, bot = upload_bot()
    bot.send_video("chat-1", b"raw-video", compress=True)
    assert sent_json(only(transport, "/finalize")[0])["filename"] == "input.mp4"


def test_video_file_is_read_in_place_and_left_alone(ffmpeg, tmp_path):
    source = tmp_path / "movie.avi"
    source.write_bytes(b"raw-video")
    transport, bot = upload_bot()
    bot.send_video("chat-1", source, compress=True)
    argv = ffmpeg.calls[0][0]
    assert argv[argv.index("-i") + 1] == str(source.resolve())
    assert source.read_bytes() == b"raw-video"
    assert sent_json(only(transport, "/finalize")[0])["filename"] == "movie.mp4"


def test_ffmpeg_failure_is_a_runtime_error_without_its_output(ffmpeg):
    ffmpeg.error = subprocess.CalledProcessError(
        1, ["ffmpeg"], stderr=b"fictional private path /home/someone/clip.mov"
    )
    transport, bot = upload_bot()
    with pytest.raises(RuntimeError) as info:
        bot.send_video("chat-1", b"raw-video", compress=True)
    assert str(info.value) == "Video compression failed"
    assert info.value.__cause__ is ffmpeg.error
    assert transport.requests == []
    assert not ffmpeg.work_dir.exists()


def test_upload_error_after_compression_is_not_hidden_and_cleans_up(ffmpeg):
    transport, bot = upload_bot(init=httpx.Response(500, json={}))
    with pytest.raises(APIError):
        bot.send_video("chat-1", b"raw-video", compress=True)
    assert not ffmpeg.work_dir.exists()


# --- download_file -----------------------------------------------------------------


def links(url=f"/v1/chat-files/{FILE_ID}", file_id=FILE_ID):
    return httpx.Response(200, json={"links": [{"fileId": file_id, "url": url}]})


def session_cookie():
    return httpx.Response(204)


def download_script(*file_responses, url=f"/v1/chat-files/{FILE_ID}"):
    return Transport(links(url), session_cookie(), *file_responses)


def file_requests(transport):
    return [r for r in transport.requests if r.url.path.startswith("/v1/chat-files/")]


@pytest.mark.parametrize(
    ("response", "message", "code", "errors", "retry_after"),
    [
        (
            httpx.Response(
                403,
                json={
                    "error": {
                        "detail": "Not yours",
                        "code": "FORBIDDEN",
                        "errors": ["e"],
                    }
                },
            ),
            "Not yours",
            "FORBIDDEN",
            ["e"],
            None,
        ),
        (httpx.Response(404, json={"message": "Gone"}), "Gone", None, None, None),
        (
            httpx.Response(
                503, headers={"Retry-After": "3"}, content=b"<html>x</html>"
            ),
            "File download failed",
            None,
            None,
            "3",
        ),
        (httpx.Response(500), "File download failed", None, None, None),
        (httpx.Response(400, json=["x"]), "File download failed", None, None, None),
        (
            httpx.Response(400, json={"error": "text"}),
            "File download failed",
            None,
            None,
            None,
        ),
    ],
    ids=["wrapped", "flat", "html-retry-after", "empty", "list", "error-string"],
)
@pytest.mark.parametrize("to_file", [False, True], ids=["bytes", "file"])
def test_download_http_error_is_an_api_error_and_creates_no_file(
    tmp_path, response, message, code, errors, retry_after, to_file
):
    transport = download_script(response)
    target = tmp_path / "out.bin"
    with pytest.raises(APIError) as info:
        transport.bot().download_file(
            "chat-1", FILE_ID, destination=target if to_file else None
        )
    error = info.value
    assert str(error) == message
    assert (error.status, error.code, error.errors, error.retry_after) == (
        response.status_code,
        code,
        errors,
        retry_after,
    )
    assert not target.exists()


def test_429_inside_the_download_sets_a_cooldown(tmp_path):
    transport = Transport(
        links(),
        session_cookie(),
        httpx.Response(429, headers={"Retry-After": "7"}, json={}),
        links(),
        session_cookie(),
    )
    bot = transport.bot(limiter=strict_limiter())
    target = tmp_path / "out.bin"
    with pytest.raises(RateLimitError) as first:
        bot.download_file("chat-1", FILE_ID, destination=target)
    assert first.value.retry_after_seconds == 7 and not first.value.locally_blocked
    assert not target.exists()
    with pytest.raises(RateLimitError) as second:
        bot.download_file("chat-1", FILE_ID, destination=target)
    assert second.value.locally_blocked
    assert len(file_requests(transport)) == 1
    assert not target.exists()


def refresh_response(access):
    return httpx.Response(200, json={"accessToken": access})


def test_expired_download_session_refreshes_once_and_retries():
    old, new = jwt("alice"), jwt("alice", FAR_FUTURE + 1)
    transport = download_script(
        httpx.Response(401, json={}),
        refresh_response(new),
        session_cookie(),
        httpx.Response(200, content=b"file-data"),
    )
    bot = transport.bot(token=old, refresh_token="fictional-seed", session_path=None)
    assert bot.download_file("chat-1", FILE_ID) == b"file-data"
    assert transport.paths == [
        f"{API}/chats/chat-1/file-links",
        f"{API}/browser-file-session",
        f"/v1/chat-files/{FILE_ID}",
        "/api/v1/auth/refresh",
        f"{API}/browser-file-session",
        f"/v1/chat-files/{FILE_ID}",
    ]
    first, second = file_requests(transport)
    assert first.headers["authorization"] == "Bearer " + old
    assert second.headers["authorization"] == "Bearer " + new


def test_second_401_after_refresh_is_reported_without_another_refresh():
    new = jwt("alice", FAR_FUTURE + 1)
    transport = download_script(
        httpx.Response(401, json={}),
        refresh_response(new),
        session_cookie(),
        httpx.Response(401, json={"error": {"code": "NO_AUTH"}}),
    )
    bot = transport.bot(
        token=jwt("alice"), refresh_token="fictional-seed", session_path=None
    )
    with pytest.raises(APIError) as info:
        bot.download_file("chat-1", FILE_ID)
    assert (info.value.status, info.value.code) == (401, "NO_AUTH")
    assert transport.paths.count("/api/v1/auth/refresh") == 1


def test_401_without_a_refresh_session_is_reported_at_once():
    transport = download_script(httpx.Response(401, json={}))
    with pytest.raises(APIError) as info:
        transport.bot().download_file("chat-1", FILE_ID)
    assert info.value.status == 401
    assert len(transport.requests) == 3


def test_dropped_connection_removes_the_partial_file(tmp_path):
    def broken():
        yield b"partial"
        raise httpx.ReadError("connection reset")

    transport = download_script(httpx.Response(200, content=broken()))
    target = tmp_path / "out.bin"
    with pytest.raises(httpx.ReadError):
        transport.bot().download_file("chat-1", FILE_ID, destination=target)
    assert not target.exists()
    assert list(tmp_path.iterdir()) == []


def test_dropped_connection_without_destination_raises_the_network_error():
    def broken():
        yield b"partial"
        raise httpx.ReadError("connection reset")

    transport = download_script(httpx.Response(200, content=broken()))
    with pytest.raises(httpx.ReadError):
        transport.bot().download_file("chat-1", FILE_ID)


def test_existing_destination_is_never_overwritten_or_removed(tmp_path):
    target = tmp_path / "out.bin"
    target.write_bytes(b"precious")
    transport = download_script(httpx.Response(200, content=b"new-data"))
    with pytest.raises(FileExistsError):
        transport.bot().download_file("chat-1", FILE_ID, destination=target)
    assert target.read_bytes() == b"precious"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (["x"], "Invalid file links"),
        (None, "Invalid file links"),
        ({}, "Invalid file links"),
        ({"links": None}, "Invalid file links"),
        ({"links": {}}, "Invalid file links"),
        ({"links": []}, "No link"),
        ({"links": ["x", None]}, "No link"),
        (
            {"links": [{"fileId": "other", "url": f"/v1/chat-files/{FILE_ID}"}]},
            "No link",
        ),
        ({"links": [{"fileId": FILE_ID}]}, "No link"),
        ({"links": [{"fileId": FILE_ID, "url": None}]}, "No link"),
        ({"links": [{"fileId": FILE_ID, "url": 5}]}, "No link"),
    ],
    ids=[
        "list",
        "null",
        "empty",
        "links-null",
        "links-dict",
        "no-links",
        "garbage-entries",
        "other-file",
        "no-url",
        "null-url",
        "int-url",
    ],
)
def test_unusable_file_links_stop_before_the_file_session(body, message):
    transport = Transport(httpx.Response(200, json=body))
    with pytest.raises(ProtocolError, match=message):
        transport.bot().download_file("chat-1", FILE_ID)
    assert len(transport.requests) == 1


@pytest.mark.parametrize(
    "url",
    [
        f"https://evil.test/v1/chat-files/{FILE_ID}",
        f"http://example.test/v1/chat-files/{FILE_ID}",
        f"https://example.test:8443/v1/chat-files/{FILE_ID}",
        f"https://user:pass@example.test/v1/chat-files/{FILE_ID}",
        f"https://example.test/v1/chat-files/{FILE_ID}#fragment",
        f"//evil.test/v1/chat-files/{FILE_ID}",
        "/v1/chat-files/not-a-uuid",
        f"/v1/chat-files/{FILE_ID}/extra",
        f"/v1/chat-files/{FILE_ID}%2F..%2Fsecret",
        "/v1/chat-files/",
        f"{API}/chats/chat-1/file-links",
    ],
    ids=[
        "other-host",
        "other-scheme",
        "other-port",
        "userinfo",
        "fragment",
        "protocol-relative",
        "not-a-uuid",
        "extra-segment",
        "encoded-traversal",
        "no-id",
        "other-route",
    ],
)
def test_unexpected_file_url_is_refused_before_any_credentials_are_sent(url):
    transport = Transport(links(url))
    with pytest.raises(ProtocolError, match="Unsupported or unexpected"):
        transport.bot().download_file("chat-1", FILE_ID)
    assert len(transport.requests) == 1


# --- download_banner ---------------------------------------------------------------

BANNER = "https://cdn.xn--d1ah4a.com/banners/banner-1.jpg"


def use_cdn(monkeypatch, cdn):
    """Route the banner downloader's own HTTP client to ``cdn``."""
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return httpx.Client(transport=httpx.MockTransport(cdn), **kwargs)

    monkeypatch.setattr(client_module, "httpx", httpx_namespace(Client=factory))
    return created


@pytest.mark.parametrize(
    "url",
    [
        "https://evil.test/banners/banner-1.jpg",
        "https://cdn.xn--d1ah4a.com.evil.test/banners/banner-1.jpg",
        "https://xn--d1ah4a.com/banners/banner-1.jpg",
        "http://cdn.xn--d1ah4a.com/banners/banner-1.jpg",
        "ftp://cdn.xn--d1ah4a.com/banners/banner-1.jpg",
        "https://cdn.xn--d1ah4a.com:8443/banners/banner-1.jpg",
        "https://user@cdn.xn--d1ah4a.com/banners/banner-1.jpg",
        "https://user:pass@cdn.xn--d1ah4a.com/banners/banner-1.jpg",
        BANNER + "#fragment",
        "banners/banner-1.jpg",
        "",
    ],
    ids=[
        "other-host",
        "suffix-trick",
        "main-site",
        "http",
        "ftp",
        "port",
        "user",
        "user-password",
        "fragment",
        "relative",
        "empty",
    ],
)
def test_banner_from_anywhere_but_the_cdn_is_refused(monkeypatch, tmp_path, url):
    cdn = Transport()
    created = use_cdn(monkeypatch, cdn)
    own = Transport()
    target = tmp_path / "banner.jpg"
    with pytest.raises(ValueError, match="ITD CDN"):
        own.bot().download_banner(url, target)
    assert created == [] and cdn.requests == [] and own.requests == []
    assert not target.exists()


def test_banner_is_fetched_without_credentials_proxies_or_redirects(
    monkeypatch, tmp_path
):
    cdn = Transport(httpx.Response(200, content=b"image-bytes"))
    created = use_cdn(monkeypatch, cdn)
    own = Transport()
    target = tmp_path / "banner.jpg"
    url = "https://cdn.xn--d1ah4a.com:443/banners/banner-1.jpg"
    assert own.bot().download_banner(url, target) == target
    assert target.read_bytes() == b"image-bytes"
    (request,) = cdn.requests
    assert "authorization" not in request.headers and "cookie" not in request.headers
    assert created[0]["trust_env"] is False and created[0]["follow_redirects"] is False
    assert own.requests == []


def test_banner_429_sets_a_cooldown_and_creates_no_file(monkeypatch, tmp_path):
    cdn = Transport(httpx.Response(429, headers={"Retry-After": "9"}, json={}))
    use_cdn(monkeypatch, cdn)
    bot = Transport().bot(limiter=strict_limiter())
    target = tmp_path / "banner.jpg"
    with pytest.raises(RateLimitError) as first:
        bot.download_banner(BANNER, target)
    assert first.value.retry_after_seconds == 9 and not first.value.locally_blocked
    with pytest.raises(RateLimitError) as second:
        bot.download_banner(BANNER, target)
    assert second.value.locally_blocked
    assert len(cdn.requests) == 1 and not target.exists()


def test_banner_http_error_creates_no_file(monkeypatch, tmp_path):
    cdn = Transport(httpx.Response(404, content=b"<html>not found</html>"))
    use_cdn(monkeypatch, cdn)
    target = tmp_path / "banner.jpg"
    with pytest.raises((httpx.HTTPStatusError, APIError)):
        Transport().bot().download_banner(BANNER, target)
    assert not target.exists()


def test_banner_redirect_is_not_followed(monkeypatch, tmp_path):
    cdn = Transport(
        httpx.Response(302, headers={"Location": "https://evil.test/banner.jpg"})
    )
    use_cdn(monkeypatch, cdn)
    target = tmp_path / "banner.jpg"
    with pytest.raises((httpx.HTTPStatusError, APIError)):
        Transport().bot().download_banner(BANNER, target)
    assert len(cdn.requests) == 1 and not target.exists()


def test_banner_dropped_connection_removes_the_partial_file(monkeypatch, tmp_path):
    def broken():
        yield b"partial"
        raise httpx.ReadError("connection reset")

    use_cdn(monkeypatch, Transport(httpx.Response(200, content=broken())))
    target = tmp_path / "banner.jpg"
    with pytest.raises(httpx.ReadError):
        Transport().bot().download_banner(BANNER, target)
    assert list(tmp_path.iterdir()) == []


def test_existing_banner_destination_is_kept(monkeypatch, tmp_path):
    use_cdn(monkeypatch, Transport(httpx.Response(200, content=b"new")))
    target = tmp_path / "banner.jpg"
    target.write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        Transport().bot().download_banner(BANNER, target)
    assert target.read_bytes() == b"precious"
