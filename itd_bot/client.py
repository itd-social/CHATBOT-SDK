from __future__ import annotations

import logging
import mimetypes
import os
import hashlib
import json as jsonlib
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urljoin, urlsplit

import httpx

from .actions import ChatAction, ChatActionHandle, ProgressSource, _action_body
from .auth import RefreshSession, _claims
from .errors import APIError, ProtocolError, RateLimitError, ResetRequired
from .rate_limits import RateLimiter, default_limiter
from .post import Post
from .user import User

_log = logging.getLogger(__name__)


def _id(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError("ID must be a non-empty string")
    return quote(value, safe="")


class Bot:
    """Synchronous client for the observed ITD messenger API.

    token is a short-lived access token; refresh_token enables cookie-based refresh.
    All responses are raw JSON to preserve unknown fields.
    """

    def __init__(
        self,
        token: str | None = None,
        *,
        base_url: str = "https://xn--d1ah4a.com",
        timeout: float = 30.0,
        client: httpx.Client | None = None,
        refresh_token: str | None = None,
        session_path: str | os.PathLike | None = ".itd-session.json",
        limiter: RateLimiter | None = None,
    ):
        if token is None and refresh_token is None:
            raise ValueError("A bearer token or refresh token is required")
        if token is not None and (not token or "\n" in token or "\r" in token):
            raise ValueError("A valid bearer token is required")
        parts = urlsplit(base_url)
        if (
            parts.scheme not in {"https", "http"}
            or not parts.netloc
            or parts.query
            or parts.fragment
        ):
            raise ValueError("base_url must be an http(s) origin or base path")
        self._token = token
        self.base_url = base_url.rstrip("/")
        self._client = client or httpx.Client(
            timeout=timeout,
            follow_redirects=False,
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=60.0,
            ),
        )
        self._owns_client = client is None
        self._account_lock = threading.Lock()
        self._account_cache: tuple[str, str | None, str] | None = None
        self._fallback_account: str | None = None
        self._rate_limits = (
            limiter
            if limiter is not None
            else default_limiter(f"{parts.scheme.lower()}://{parts.netloc.lower()}")
        )
        try:
            self._refresh_session = (
                RefreshSession(
                    refresh_token,
                    client=self._client,
                    base_url=self.base_url,
                    session_path=session_path,
                    access_token=token,
                    rate_limits=self._rate_limits,
                )
                if refresh_token is not None
                else None
            )
        except Exception:
            if self._owns_client:
                self._client.close()
            raise

    @classmethod
    def from_refresh_token(cls, refresh_token: str, **kwargs: Any) -> Bot:
        return cls(refresh_token=refresh_token, **kwargs)

    def _access_token(self) -> str:
        if self._refresh_session is not None:
            return self._refresh_session.get_access_token()
        return self._token

    def _account_key(self, access_token: str) -> str:
        """Stable opaque account key for rate-limit buckets; never the token itself.

        Uses the account ID (JWT sub) the session or token carries, hashed;
        otherwise a random key unique to this instance.
        """
        session_subject = (
            self._refresh_session.subject if self._refresh_session else None
        )
        with self._account_lock:
            cached = self._account_cache
            if (
                cached is not None
                and cached[0] == access_token
                and cached[1] == session_subject
            ):
                return cached[2]
            subject = session_subject or _claims(access_token).get("sub")
            if isinstance(subject, str) and subject:
                digest = hashlib.sha256(b"itd-account:" + subject.encode("utf-8"))
                key = "sub:" + digest.hexdigest()[:24]
            else:
                if self._fallback_account is None:
                    self._fallback_account = "bot:" + uuid.uuid4().hex
                key = self._fallback_account
            self._account_cache = (access_token, session_subject, key)
            return key

    def _access_state(self) -> tuple[str, int]:
        if self._refresh_session is not None:
            return self._refresh_session.get_access_state()
        return self._token, 0

    @property
    def refresh_count(self) -> int:
        return self._refresh_session.refresh_count if self._refresh_session else 0

    def __enter__(self) -> Bot:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def set_token(self, token: str) -> None:
        if not token or "\n" in token or "\r" in token:
            raise ValueError("A valid bearer token is required")
        if self._refresh_session is not None:
            self._refresh_session.set_access_token(token)
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Call a relative messenger endpoint."""
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError(
                "path must be a relative messenger endpoint starting with /"
            )
        return self.api_request(
            method, "/api/v1/messenger" + path, params=params, json=json
        )

    def api_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        content: bytes | None = None,
        files: Any = None,
        headers: Mapping[str, str] | None = None,
        raw: bool = False,
    ) -> Any:
        """Call an API path on the configured origin, including experimental endpoints."""
        if (
            not path.startswith(("/api/", "/v1/"))
            or "?" in path
            or "#" in path
            or "\\" in path
            or any(part in {".", ".."} for part in path.split("/"))
            or "%" in path
            and any(
                part.lower() in {"%2e", "%2e%2e", ".%2e", "%2e."}
                for part in path.split("/")
            )
        ):
            raise ValueError(
                "path must be a safe /api/ or /v1/ path without query or fragment"
            )
        if sum(value is not None for value in (json, content, files)) > 1:
            raise ValueError("use only one of json, content or files")
        request_headers = dict(headers or {})
        if any(
            key.lower() in {"authorization", "host", "cookie"}
            for key in request_headers
        ):
            raise ValueError("authentication and host headers are managed by the SDK")
        cost = 1
        if (
            method.upper() == "POST"
            and path.endswith("/messages/forward")
            and isinstance(json, Mapping)
        ):
            message_ids = json.get("message_ids")
            if isinstance(message_ids, list):
                cost = max(1, len(message_ids))
        access_token, generation = self._access_state()
        account = self._account_key(access_token)
        self._rate_limits.acquire(method, path, cost=cost, account=account)
        request_headers.update(
            {
                "Authorization": "Bearer " + access_token,
                "X-Requested-With": "XMLHttpRequest",
            }
        )
        response = self._client.request(
            method,
            self.base_url + path,
            params=params,
            json=json,
            content=content,
            files=files,
            follow_redirects=False,
            headers=request_headers,
        )
        self._rate_limits.observe(response, method, path, account=account)
        if (
            response.status_code == 401
            and self._refresh_session is not None
            and files is None
            and not path.startswith("/api/v1/auth/")
        ):
            request_headers["Authorization"] = (
                "Bearer "
                + self._refresh_session.refresh_after_401(access_token, generation)
            )
            self._rate_limits.acquire(method, path, cost=cost, account=account)
            response = self._client.request(
                method,
                self.base_url + path,
                params=params,
                json=json,
                content=content,
                follow_redirects=False,
                headers=request_headers,
            )
            self._rate_limits.observe(response, method, path, account=account)
        if response.is_success and raw:
            return response
        try:
            data = response.json() if response.content else None
        except ValueError:
            if response.is_success:
                raise ProtocolError("Expected JSON response") from None
            data = None
        if not response.is_success:
            detail = data.get("error", data) if isinstance(data, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
            raise APIError(
                response.status_code,
                str(
                    detail.get("detail")
                    or detail.get("message")
                    or detail.get("title")
                    or "Request failed"
                ),
                code=detail.get("code"),
                errors=detail.get("errors") or detail.get("violations"),
                retry_after=response.headers.get("Retry-After"),
            )
        return data

    def upload_file(
        self,
        source: str | os.PathLike | bytes,
        *,
        filename: str | None = None,
        mime: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
        """Upload bytes or a local file using the messenger's multipart-by-chunk protocol."""
        import io

        stream = io.BytesIO(source) if isinstance(source, bytes) else open(source, "rb")
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
            result = self.request(
                "POST", f"/media/upload/{upload_id}/finalize", json=body
            )
            if (
                not isinstance(result, dict)
                or not isinstance(result.get("fileId"), str)
                or not result["fileId"]
            ):
                raise ProtocolError("Invalid upload finalization response")
            return result
        finally:
            stream.close()

    def send_file(
        self,
        chat_id: str,
        source: str | os.PathLike | bytes,
        *,
        text: str = "",
        filename: str | None = None,
        mime: str | None = None,
        reply_to_id: str | None = None,
        client_message_id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> dict:
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
        self,
        chat_id: str,
        source: str | os.PathLike | bytes,
        *,
        caption: str = "",
        compress: bool = False,
        filename: str | None = None,
        mime: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict:
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
                else (
                    Path(original_name).stem + ".jpg"
                    if original_name
                    else prepared_name
                )
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
        self,
        chat_id: str,
        source: str | os.PathLike | bytes,
        *,
        caption: str = "",
        compress: bool = False,
        **kwargs: Any,
    ) -> dict:
        """Send a video; optional compression requires the ffmpeg executable."""
        if not compress:
            return self.send_file(chat_id, source, text=caption, **kwargs)
        import subprocess
        import tempfile

        import shutil

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
        self,
        chat_id: str,
        source: str | os.PathLike | bytes,
        *,
        caption: str = "",
        **kwargs: Any,
    ) -> dict:
        """Send an audio file without transcoding."""
        return self.send_file(chat_id, source, text=caption, **kwargs)

    def send_audio(
        self,
        chat_id: str,
        source: str | os.PathLike | bytes,
        *,
        caption: str = "",
        **kwargs: Any,
    ) -> dict:
        return self.send_music(chat_id, source, caption=caption, **kwargs)

    def get_user(self, user_id_or_username: str) -> User:
        """Fetch a user profile by ID or username; optional @ is accepted."""
        if not isinstance(user_id_or_username, str):
            raise ValueError("user ID or username must be a non-empty string")
        identifier = user_id_or_username.removeprefix("@")
        return User.from_dict(
            self.api_request("GET", f"/api/users/{_id(identifier)}"), bot=self
        )

    def get_me(self) -> User:
        """Fetch the authenticated account's profile."""
        return User.from_dict(self.api_request("GET", "/api/users/me"), bot=self)

    def get_post(self, post_id: str) -> Post:
        """Fetch an accessible post by ID."""
        result = self.api_request("GET", f"/api/posts/{_id(post_id)}")
        data = result.get("data") if isinstance(result, dict) else None
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("id"), str)
            or not data["id"]
        ):
            raise ProtocolError("Invalid post response")
        if data["id"] != post_id:
            raise ProtocolError("Post response ID does not match request")
        return Post(id=data["id"], raw=data, _bot=self)

    def get_follow_status(self, user_ids: list[str]) -> dict:
        """Return whether this account follows each user ID."""
        if not user_ids:
            return {}
        for user_id in user_ids:
            _id(user_id)
        result = self.api_request(
            "POST", "/api/users/follow-status", json={"userIds": user_ids}
        )
        if not isinstance(result, dict) or not isinstance(result.get("data"), dict):
            raise ProtocolError("Invalid follow status response")
        return result["data"]

    def download_banner(self, url: str, destination: str | os.PathLike) -> Path:
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
        with httpx.Client(
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

    def get_chats(self, *, limit: int | None = None, before: str | None = None) -> dict:
        return self.request(
            "GET",
            "/chats",
            params={
                k: v
                for k, v in {
                    "limit": limit,
                    "before": before,
                    "manual_unread": 1,
                }.items()
                if v is not None
            },
        )

    def get_unread_count(self) -> dict:
        return self.request("GET", "/chats/unread", params={"manual_unread": 1})

    def get_chat(self, chat_id: str) -> dict:
        return self.request(
            "GET", f"/chats/{_id(chat_id)}", params={"manual_unread": 1}
        )

    def create_direct_chat(self, user_id: str) -> dict:
        return self.request("POST", "/chats/dm", json={"target_user_id": user_id})

    def get_messages(
        self, chat_id: str, *, limit: int | None = None, before: str | None = None
    ) -> dict:
        return self.request(
            "GET",
            f"/chats/{_id(chat_id)}/messages",
            params={
                k: v
                for k, v in {"limit": limit, "before": before}.items()
                if v is not None
            },
        )

    def get_messages_around(
        self, chat_id: str, message_id: str, *, limit: int = 40
    ) -> dict:
        return self.request(
            "GET",
            f"/chats/{_id(chat_id)}/messages/around/{_id(message_id)}",
            params={"limit": limit},
        )

    def send_message(
        self,
        chat_id: str,
        text: str,
        *,
        reply_to_id: str | None = None,
        attachments: list | None = None,
        entities: list | None = None,
        client_message_id: str | None = None,
    ) -> dict:
        body = {"text": text, "reply_to_id": reply_to_id}
        if attachments:
            body["attachments"] = attachments
        if entities:
            body["entities"] = entities
        if client_message_id:
            body["client_message_id"] = client_message_id
        return self.request("POST", f"/chats/{_id(chat_id)}/messages", json=body)

    def send_message_to_user(
        self,
        user_id: str,
        text: str,
        *,
        attachments: list | None = None,
        entities: list | None = None,
        client_message_id: str | None = None,
    ) -> dict:
        body = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if entities:
            body["entities"] = entities
        if client_message_id:
            body["client_message_id"] = client_message_id
        return self.request("POST", f"/users/{_id(user_id)}/messages", json=body)

    def send_formatted(self, chat_id: str, content: Any, **kwargs: Any) -> dict:
        from .formatting import Text

        if not isinstance(content, Text):
            raise TypeError("content must be a Text builder")
        text, entities = content.build()
        return self.send_message(chat_id, text, entities=entities, **kwargs)

    def edit_message(
        self,
        chat_id: str,
        message_id: str,
        text: str,
        *,
        attachments: list | None = None,
        entities: list | None = None,
    ) -> dict:
        body = {"text": text}
        if attachments:
            body["attachments"] = attachments
        if entities is not None:
            body["entities"] = entities
        return self.request(
            "PATCH", f"/chats/{_id(chat_id)}/messages/{_id(message_id)}", json=body
        )

    def delete_messages(
        self, chat_id: str, message_ids: list[str], *, for_everyone: bool = False
    ) -> Any:
        scope = "all" if for_everyone else "me"
        return self.request(
            "POST",
            f"/chats/{_id(chat_id)}/messages/delete-for-{scope}",
            json={"message_ids": message_ids},
        )

    def forward_messages(
        self,
        chat_id: str,
        source_chat_id: str,
        message_ids: list[str],
        *,
        client_message_ids: list[str] | None = None,
    ) -> Any:
        body = {"source_chat_id": source_chat_id, "message_ids": message_ids}
        if client_message_ids:
            body["client_message_ids"] = client_message_ids
        return self.request(
            "POST", f"/chats/{_id(chat_id)}/messages/forward", json=body
        )

    def mark_read(self, chat_id: str, message_id: str) -> Any:
        return self.request(
            "POST",
            f"/chats/{_id(chat_id)}/read",
            json={"last_read_message": message_id},
        )

    def mark_unread(self, chat_id: str, unread: bool = True) -> Any:
        return self.request(
            "PATCH", f"/chats/{_id(chat_id)}/unread", json={"unread": unread}
        )

    def accept_chat(self, chat_id: str) -> Any:
        return self.request("POST", f"/chats/{_id(chat_id)}/accept")

    def set_pinned(self, chat_id: str, pinned: bool) -> Any:
        return self.request(
            "PATCH", f"/chats/{_id(chat_id)}/pin", json={"is_pinned": pinned}
        )

    def set_muted(self, chat_id: str, muted: bool) -> Any:
        return self.request(
            "PATCH", f"/chats/{_id(chat_id)}/mute", json={"is_muted": muted}
        )

    def send_action(
        self,
        chat_id: str,
        action: ChatAction | str = ChatAction.TYPING,
        *,
        progress: float | None = None,
    ) -> Any:
        """Send a chat action status.

        ``action`` is a :class:`ChatAction` or any non-empty string (the server
        decides the result for unknown ones). ``progress`` is only allowed for
        ``upload_*`` actions and must be a number from 0 to 1 inclusive; invalid
        values raise ``ValueError`` before any request is made.
        """
        body = _action_body(action, progress)
        return self.request("POST", f"/chats/{_id(chat_id)}/action", json=body)

    def chat_action(
        self,
        chat_id: str,
        action: ChatAction | str = ChatAction.TYPING,
        *,
        interval: float = 3.0,
        progress: ProgressSource = None,
    ) -> ChatActionHandle:
        """Show a chat action for the duration of a ``with`` block.

        The first request is sent in ``__enter__`` and raises as usual; a daemon
        thread then repeats it every ``interval`` seconds (at least 1.0) with the
        current ``handle.progress`` until the block exits. ``progress`` may be a
        number, None or a callable evaluated on every tick. The first background
        failure stops the loop and is kept in ``handle.error``. Requests go
        through the shared rate limiter.
        """
        return ChatActionHandle(
            self, chat_id, action, interval=interval, progress=progress
        )

    def clear_history(self, chat_id: str, *, for_everyone: bool = False) -> Any:
        return self.request(
            "POST",
            f"/chats/{_id(chat_id)}/clear-history" + ("-all" if for_everyone else ""),
        )

    def delete_chat(self, chat_id: str, *, for_everyone: bool = False) -> Any:
        return self.request(
            "POST", f"/chats/{_id(chat_id)}/delete", json={"for_everyone": for_everyone}
        )

    def get_reaction_catalog(self) -> Any:
        return self.request("GET", "/reactions/available")

    def get_reactions(self, chat_id: str, message_id: str) -> Any:
        return self.request(
            "GET", f"/chats/{_id(chat_id)}/messages/{_id(message_id)}/reactions"
        )

    def set_reactions(
        self,
        chat_id: str,
        message_id: str,
        reactions: list,
        *,
        expected_version: str,
        operation_id: str,
    ) -> Any:
        return self.request(
            "PUT",
            f"/chats/{_id(chat_id)}/messages/{_id(message_id)}/reactions",
            json={
                "reactions": reactions,
                "expected_version": expected_version,
                "operation_id": operation_id,
            },
        )

    def add_reaction(self, chat_id: str, message_id: str, emoji: str) -> Any:
        """Add an emoji while preserving the account's existing selected reactions."""
        if not isinstance(emoji, str) or not emoji:
            raise ValueError("emoji must be a non-empty string")
        current = self.get_reactions(chat_id, message_id)
        if (
            not isinstance(current, dict)
            or not isinstance(current.get("reaction_version"), str)
            or not isinstance(current.get("reactions"), list)
        ):
            raise ProtocolError("Invalid reactions response")
        selected = []
        for item in current["reactions"]:
            if not isinstance(item, dict) or not isinstance(item.get("reaction"), dict):
                raise ProtocolError("Invalid reaction entry")
            if item.get("chosen") is True:
                selected.append(item["reaction"])
        reaction = {"type": "emoji", "emoji": emoji}
        if reaction not in selected:
            selected.append(reaction)
        return self.set_reactions(
            chat_id,
            message_id,
            selected,
            expected_version=current["reaction_version"],
            operation_id=str(uuid.uuid4()),
        )

    def remove_reaction(self, chat_id: str, message_id: str, emoji: str) -> Any:
        """Remove an emoji while preserving the account's other reactions."""
        if not isinstance(emoji, str) or not emoji:
            raise ValueError("emoji must be a non-empty string")
        current = self.get_reactions(chat_id, message_id)
        if (
            not isinstance(current, dict)
            or not isinstance(current.get("reaction_version"), str)
            or not isinstance(current.get("reactions"), list)
        ):
            raise ProtocolError("Invalid reactions response")
        selected = []
        for item in current["reactions"]:
            if not isinstance(item, dict) or not isinstance(item.get("reaction"), dict):
                raise ProtocolError("Invalid reaction entry")
            if item.get("chosen") is True and item["reaction"] != {
                "type": "emoji",
                "emoji": emoji,
            }:
                selected.append(item["reaction"])
        return self.set_reactions(
            chat_id,
            message_id,
            selected,
            expected_version=current["reaction_version"],
            operation_id=str(uuid.uuid4()),
        )

    def get_presence(self, user_id: str) -> Any:
        return self.request("GET", f"/presence/{_id(user_id)}")

    def get_presence_many(self, user_ids: list[str]) -> Any:
        return self.request("POST", "/presence", json={"user_ids": user_ids})

    def mark_voice_heard(self, chat_id: str, message_id: str) -> Any:
        return self.request(
            "POST", f"/chats/{_id(chat_id)}/messages/{_id(message_id)}/heard"
        )

    def get_heard_messages(self, chat_id: str, message_ids: list[str]) -> Any:
        return self.request(
            "POST", f"/chats/{_id(chat_id)}/heard", json={"message_ids": message_ids}
        )

    def get_media(
        self,
        chat_id: str,
        category: str,
        *,
        before: str | None = None,
        limit: int | None = None,
    ) -> Any:
        return self.request(
            "GET",
            f"/chats/{_id(chat_id)}/media",
            params={
                k: v
                for k, v in {
                    "category": category,
                    "before": before,
                    "limit": limit,
                }.items()
                if v is not None
            },
        )

    def get_file_links(self, chat_id: str, file_ids: list[str]) -> Any:
        return self.request(
            "POST", f"/chats/{_id(chat_id)}/file-links", json={"file_ids": file_ids}
        )

    def create_file_session(self) -> None:
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
        self,
        chat_id: str,
        file_id: str,
        *,
        destination: str | os.PathLike | None = None,
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
        self, response: httpx.Response, destination: str | os.PathLike | None
    ) -> bytes | Path:
        if not response.is_success:
            response.read()
            try:
                detail = response.json()
            except ValueError:
                detail = {}
            detail = detail.get("error", detail) if isinstance(detail, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
            raise APIError(
                response.status_code,
                str(
                    detail.get("detail")
                    or detail.get("message")
                    or "File download failed"
                ),
                code=detail.get("code"),
                errors=detail.get("errors"),
                retry_after=response.headers.get("Retry-After"),
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

    def get_updates(self, since: int, *, limit: int | None = None) -> dict:
        if isinstance(since, bool) or not isinstance(since, int) or since < 0:
            raise ValueError("since must be a non-negative integer")
        params = {"since": since, "capabilities": "chat_settings", "manual_unread": 1}
        if limit is not None:
            params["limit"] = limit
        data = self.request("GET", "/updates", params=params)
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("events"), list)
            or not isinstance(data.get("reset_required"), bool)
            or isinstance(data.get("next_event_seq"), bool)
            or not isinstance(data.get("next_event_seq"), int)
            or data["next_event_seq"] < 0
            or any(
                not isinstance(e, dict) or not isinstance(e.get("type"), str)
                for e in data["events"]
            )
        ):
            raise ProtocolError("Invalid updates response")
        if data["reset_required"]:
            raise ResetRequired(data)
        if data["next_event_seq"] < since:
            raise ProtocolError("Updates cursor moved backwards without a reset")
        return data

    def websocket_events(self, *, stop: threading.Event | None = None):
        """Yield live frames until disconnected; use get_updates to recover gaps.

        When ``stop`` is set, the generator closes the connection and returns
        within about a second.
        """
        try:
            import websocket
        except ImportError as exc:
            raise RuntimeError(
                'Install the websocket extra: pip install "itd-chatbot-sdk[websocket]"'
            ) from exc
        parts = urlsplit(self.base_url)
        scheme = "wss" if parts.scheme == "https" else "ws"
        url = f"{scheme}://{parts.netloc}{parts.path}/api/v1/messenger/ws?capabilities=chat_settings_v2"
        access_token = self._access_token()
        self._rate_limits.acquire("GET", "/api/v1/messenger/ws")
        try:
            conn = websocket.create_connection(
                url,
                timeout=5,
                redirect_limit=0,
                subprotocols=["itd.messenger.v1", "bearer." + access_token],
                origin=f"{parts.scheme}://{parts.netloc}",
            )
        except websocket.WebSocketBadStatusException as exc:
            if exc.status_code == 429:
                self._rate_limits.observe(
                    httpx.Response(
                        429,
                        headers=exc.resp_headers or {},
                        content=exc.resp_body or b"",
                    ),
                    "GET",
                    "/api/v1/messenger/ws",
                )
            raise
        try:
            if conn.handshake_response.status != 101:
                raise ProtocolError("WebSocket handshake failed")
            last_ping = last_frame = time.monotonic()
            while stop is None or not stop.is_set():
                now = time.monotonic()
                if now - last_frame >= 15:
                    raise ProtocolError("WebSocket heartbeat timed out")
                if now - last_ping >= 5:
                    conn.send("ping")
                    last_ping = now
                timeout = min(5, max(0.1, 5 - (now - last_ping)))
                conn.settimeout(timeout if stop is None else min(timeout, 1.0))
                try:
                    frame = conn.recv()
                except websocket.WebSocketTimeoutException:
                    continue
                if frame in (None, "", b""):
                    raise ProtocolError("WebSocket closed")
                last_frame = time.monotonic()
                if frame in ("pong", b"pong"):
                    continue
                try:
                    event = jsonlib.loads(frame)
                except (ValueError, UnicodeDecodeError, TypeError) as exc:
                    raise ProtocolError("Invalid WebSocket frame") from exc
                if not isinstance(event, dict) or not isinstance(
                    event.get("type"), str
                ):
                    raise ProtocolError("Invalid WebSocket event")
                yield event
        finally:
            conn.close()

    def poll(
        self,
        since: int,
        handler: Callable[[dict], None],
        *,
        interval: float = 2.0,
        on_checkpoint: Callable[[int], None] | None = None,
        websocket: bool = False,
        workers: int = 1,
        min_fetch_interval: float = 0.2,
    ) -> None:
        """Process batches; checkpoint only after all handlers of a batch succeed.

        The next batch is requested immediately only when the previous one had
        events and moved the cursor forward; otherwise the loop pauses for
        ``interval``. With ``websocket=True`` a background WebSocket connection
        wakes the loop as soon as the server pushes a frame, while events are
        still read from ``/updates`` with the same cursor; requests for updates
        then start at least ``min_fetch_interval`` seconds apart. ``workers > 1``
        runs handlers of different chats in parallel threads, keeping order
        inside each chat.
        """
        if interval <= 0:
            raise ValueError("interval must be positive")
        if min_fetch_interval < 0:
            raise ValueError("min_fetch_interval must not be negative")
        if isinstance(workers, bool) or not isinstance(workers, int) or workers < 1:
            raise ValueError("workers must be a positive integer")
        if websocket:
            self._require_websocket()
        wake = threading.Event()
        stop = threading.Event()
        if websocket:
            threading.Thread(
                target=self._websocket_wakeups,
                args=(wake, stop),
                name="itd-ws-wakeup",
                daemon=True,
            ).start()
        executor = ThreadPoolExecutor(workers, "itd-handler") if workers > 1 else None
        last_fetch = None
        try:
            while True:
                if websocket and last_fetch is not None:
                    gap = last_fetch + min_fetch_interval - time.monotonic()
                    if gap > 0:
                        time.sleep(gap)
                wake.clear()
                last_fetch = time.monotonic()
                try:
                    batch = self.get_updates(since)
                except RateLimitError as exc:
                    time.sleep(min(exc.retry_after_seconds, 60))
                    continue
                events = batch["events"]
                if executor is None:
                    for event in events:
                        handler(event)
                else:
                    _run_by_chat(executor, events, handler)
                next_seq = batch["next_event_seq"]
                if on_checkpoint is not None:
                    on_checkpoint(next_seq)
                advanced = next_seq > since
                since = next_seq
                if events and advanced:
                    continue
                if websocket:
                    wake.wait(interval)
                else:
                    time.sleep(interval)
        finally:
            stop.set()
            if executor is not None:
                executor.shutdown(wait=False, cancel_futures=True)

    @staticmethod
    def _require_websocket() -> None:
        try:
            import websocket  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                'Install the websocket extra: pip install "itd-chatbot-sdk[websocket]"'
            ) from exc

    def _websocket_wakeups(self, wake: threading.Event, stop: threading.Event) -> None:
        backoff = 1.0
        while not stop.is_set():
            try:
                for _ in self.websocket_events(stop=stop):
                    wake.set()
                    backoff = 1.0
            except Exception as exc:
                _log.warning("WebSocket wake-up stream failed: %s", type(exc).__name__)
            # A reconnect can miss frames, so let /updates look for a gap.
            wake.set()
            if stop.wait(backoff):
                return
            backoff = min(backoff * 2, 30.0)


def _run_by_chat(
    executor: ThreadPoolExecutor,
    events: list[dict],
    handler: Callable[[dict], None],
) -> None:
    """Run handlers in parallel across chats, in order within each chat."""
    groups: dict[str | None, list[dict]] = {}
    for event in events:
        chat_id = event.get("chat_id")
        key = chat_id if isinstance(chat_id, str) else None
        groups.setdefault(key, []).append(event)
    if len(groups) <= 1:
        for event in events:
            handler(event)
        return

    def run(group: list[dict]) -> None:
        for event in group:
            handler(event)

    futures = [executor.submit(run, group) for group in groups.values()]
    wait(futures)
    for future in futures:
        future.result()
