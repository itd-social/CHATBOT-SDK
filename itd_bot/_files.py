"""Internal streamed uploads, downloads and media preparation."""

from __future__ import annotations

import mimetypes
import os
import re
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO, cast
from urllib.parse import urljoin, urlsplit

import httpx

from ._transport import _id
from .errors import ProtocolError, _api_error
from .schemas import SendResponse, UploadFinalizeResponse

if TYPE_CHECKING:
    from .client import Bot


def upload_file(
    self: Bot,
    source: str | os.PathLike[str] | bytes,
    *,
    filename: str | None = None,
    mime: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
    open_file: Callable[..., BinaryIO],
) -> UploadFinalizeResponse:
    """Upload bytes or a local file using the messenger's multipart-by-chunk protocol."""
    import io

    stream = (
        io.BytesIO(source) if isinstance(source, bytes) else open_file(source, "rb")
    )
    try:
        stream.seek(0, os.SEEK_END)
        size = stream.tell()
        stream.seek(0)
        if not 0 < size <= 2 * 1024**3:
            raise ValueError("file size must be between 1 byte and 2 GiB")
        if filename is None and not isinstance(source, bytes):
            filename = Path(source).name
        mime = (
            mime
            or mimetypes.guess_type(filename or "")[0]
            or "application/octet-stream"
        )
        part_size = 5 * 1024 * 1024
        count = (size + part_size - 1) // part_size
        initial = self.request(
            "POST",
            "/media/upload/init",
            json={"mime": mime, "size": size, "totalParts": count},
        )
        if (
            not isinstance(initial, dict)
            or not isinstance(initial.get("uploadId"), str)
            or not initial["uploadId"]
        ):
            raise ProtocolError("Invalid upload initialization response")
        upload_id = _id(initial["uploadId"])
        sent = 0
        for index in range(count):
            expected = min(part_size, size - sent)
            part = stream.read(expected)
            if len(part) != expected:
                raise OSError("file changed or could not be read during upload")
            self.api_request(
                "PUT",
                f"/api/v1/messenger/media/upload/{upload_id}/part/{index}",
                content=part,
                headers={"Content-Type": "application/octet-stream"},
                raw=True,
            )
            sent += len(part)
            if on_progress is not None:
                on_progress(sent, size)
        body = dict(metadata or {})
        if filename is not None:
            body["filename"] = filename
        result = self.request("POST", f"/media/upload/{upload_id}/finalize", json=body)
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("fileId"), str)
            or not result["fileId"]
        ):
            raise ProtocolError("Invalid upload finalization response")
        return cast(UploadFinalizeResponse, result)
    finally:
        stream.close()


def send_file(
    self: Bot,
    chat_id: str,
    source: str | os.PathLike[str] | bytes,
    *,
    text: str = "",
    filename: str | None = None,
    mime: str | None = None,
    reply_to_id: str | None = None,
    client_message_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    on_progress: Callable[[int, int], None] | None = None,
) -> SendResponse:
    uploaded = self.upload_file(
        source,
        filename=filename,
        mime=mime,
        metadata=metadata,
        on_progress=on_progress,
    )
    return self.send_message(
        chat_id,
        text,
        reply_to_id=reply_to_id,
        attachments=[uploaded["fileId"]],
        client_message_id=client_message_id,
    )


def send_photo(
    self: Bot,
    chat_id: str,
    source: str | os.PathLike[str] | bytes,
    *,
    caption: str = "",
    compress: bool = False,
    filename: str | None = None,
    mime: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    **kwargs: Any,
) -> SendResponse:
    """Send an image, optionally preparing a JPEG like the web client."""
    if compress:
        from .media import prepare_photo

        original_name = filename or (
            Path(source).name if not isinstance(source, bytes) else None
        )
        data, prepared_name, dimensions = prepare_photo(source)
        is_gif = data.startswith((b"GIF87a", b"GIF89a"))
        filename = (
            original_name
            if is_gif and original_name
            else (Path(original_name).stem + ".jpg" if original_name else prepared_name)
        )
        mime = "image/gif" if is_gif else "image/jpeg"
        source = data
        metadata = {**dimensions, **(metadata or {})}
    return self.send_file(
        chat_id,
        source,
        text=caption,
        filename=filename,
        mime=mime,
        metadata=metadata,
        **kwargs,
    )


def send_video(
    self: Bot,
    chat_id: str,
    source: str | os.PathLike[str] | bytes,
    *,
    caption: str = "",
    compress: bool = False,
    **kwargs: Any,
) -> SendResponse:
    """Send a video; optional compression requires the ffmpeg executable."""
    if not compress:
        return self.send_file(chat_id, source, text=caption, **kwargs)
    import shutil
    import subprocess
    import tempfile

    executable = shutil.which("ffmpeg")
    if executable is None:
        raise RuntimeError("Video compression requires ffmpeg in PATH")
    if not isinstance(source, bytes) and not Path(source).is_file():
        raise FileNotFoundError(source)
    try:
        with tempfile.TemporaryDirectory(prefix="itd-video-") as directory:
            if isinstance(source, bytes):
                input_path = Path(directory) / "input"
                input_path.write_bytes(source)
            else:
                input_path = Path(source).resolve()
            output_path = Path(directory) / "video.mp4"
            subprocess.run(
                [
                    executable,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(input_path),
                    "-c:v",
                    "libx264",
                    "-crf",
                    "28",
                    "-preset",
                    "medium",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    str(output_path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
            )
            name = kwargs.pop("filename", None)
            name = Path(name or input_path.name).stem + ".mp4"
            kwargs.pop("mime", None)
            return self.send_file(
                chat_id,
                output_path,
                text=caption,
                filename=name,
                mime="video/mp4",
                **kwargs,
            )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError("Video compression failed") from exc


def send_music(
    self: Bot,
    chat_id: str,
    source: str | os.PathLike[str] | bytes,
    *,
    caption: str = "",
    **kwargs: Any,
) -> SendResponse:
    """Send an audio file without transcoding."""
    return self.send_file(chat_id, source, text=caption, **kwargs)


def send_audio(
    self: Bot,
    chat_id: str,
    source: str | os.PathLike[str] | bytes,
    *,
    caption: str = "",
    **kwargs: Any,
) -> SendResponse:
    return self.send_music(chat_id, source, caption=caption, **kwargs)


def download_banner(
    self: Bot,
    url: str,
    destination: str | os.PathLike[str],
    *,
    client_factory: Callable[..., httpx.Client],
) -> Path:
    """Download a profile banner from ITD's CDN without sending authentication."""
    parsed = urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname != "cdn.xn--d1ah4a.com"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
        or parsed.fragment
    ):
        raise ValueError("Banner URL must use the ITD CDN over HTTPS")
    target = Path(destination)
    with client_factory(
        follow_redirects=False, timeout=30.0, trust_env=False
    ) as client:
        self._rate_limits.acquire("GET", parsed.path)
        with client.stream("GET", url) as response:
            if response.status_code == 429:
                response.read()
                self._rate_limits.observe(response, "GET", parsed.path)
            response.raise_for_status()
            with target.open("xb") as output:
                try:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
                except BaseException:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise
    return target


def create_file_session(self: Bot) -> None:
    """Create the short-lived browser file cookie in this client's cookie jar."""
    origin = urlsplit(self.base_url)
    response = self.api_request(
        "POST",
        "/api/v1/messenger/browser-file-session",
        raw=True,
        headers={"Origin": f"{origin.scheme}://{origin.netloc}"},
    )
    if response.status_code != 204:
        raise ProtocolError("Unexpected file session response")


def download_file(
    self: Bot,
    chat_id: str,
    file_id: str,
    *,
    destination: str | os.PathLike[str] | None = None,
) -> bytes | Path:
    """Resolve a chat attachment and download it; destination streams to a new file."""
    links = self.get_file_links(chat_id, [file_id])
    if not isinstance(links, dict) or not isinstance(links.get("links"), list):
        raise ProtocolError("Invalid file links response")
    link = next(
        (
            entry.get("url")
            for entry in links["links"]
            if isinstance(entry, dict) and entry.get("fileId") == file_id
        ),
        None,
    )
    if not isinstance(link, str):
        raise ProtocolError("No link for requested file")
    base = urlsplit(self.base_url)
    resolved = urlsplit(urljoin(self.base_url + "/", link))
    if (
        resolved.scheme != base.scheme
        or resolved.netloc != base.netloc
        or resolved.username
        or resolved.password
        or resolved.fragment
        or not re.fullmatch(
            r"/v1/chat-files/[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}",
            resolved.path,
        )
    ):
        raise ProtocolError("Unsupported or unexpected file URL")
    self.create_file_session()
    access_token, generation = self._access_state()
    headers = {
        "Authorization": "Bearer " + access_token,
        "X-Requested-With": "XMLHttpRequest",
    }
    for attempt in range(2):
        self._rate_limits.acquire("GET", resolved.path)
        with self._client.stream(
            "GET", resolved.geturl(), headers=headers, follow_redirects=False
        ) as response:
            if (
                response.status_code == 401
                and attempt == 0
                and self._refresh_session is not None
            ):
                response.close()
                self._refresh_session.refresh_after_401(access_token, generation)
                self.create_file_session()
                headers["Authorization"] = "Bearer " + self._access_token()
                continue
            if response.status_code == 429:
                response.read()
                self._rate_limits.observe(response, "GET", resolved.path)
            return self._read_download(response, destination)
    raise ProtocolError("File download failed")


def _read_download(
    self: Bot, response: httpx.Response, destination: str | os.PathLike[str] | None
) -> bytes | Path:
    if not response.is_success:
        response.read()
        try:
            data = response.json()
        except ValueError:
            data = None
        detail = data.get("error", data) if isinstance(data, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        raise _api_error(
            response.status_code,
            str(
                detail.get("detail") or detail.get("message") or "File download failed"
            ),
            code=detail.get("code"),
            errors=detail.get("errors"),
            retry_after=response.headers.get("Retry-After"),
            response=data,
        )
    if destination is None:
        return response.read()
    target = Path(destination)

    with target.open("xb") as output:
        try:
            for chunk in response.iter_bytes():
                output.write(chunk)
        except BaseException:
            output.close()
            target.unlink(missing_ok=True)
            raise
    return target
