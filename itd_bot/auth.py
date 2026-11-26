from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import tempfile
import threading
import time
import uuid
from http.cookies import SimpleCookie
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import httpx

from .errors import APIError, ProtocolError, SessionStorageError
from .rate_limits import RateLimitState


def _validate_refresh(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 8192
        or not re.fullmatch(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]+", value)
    ):
        raise ValueError("refresh token must be a single cookie value")
    return value


def _claims(token: str) -> dict:
    try:
        part = token.split(".")[1]
        if len(part) > 16384:
            return {}
        result = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return result if isinstance(result, dict) else {}
    except (IndexError, ValueError, UnicodeDecodeError):
        return {}


class RefreshSession:
    """Refresh a browser cookie session; serialize rotations inside this instance."""

    def __init__(
        self,
        refresh_token: str,
        *,
        client: httpx.Client,
        base_url: str,
        session_path: str | os.PathLike | None,
        access_token: str | None = None,
        rate_limits: RateLimitState | None = None,
    ):
        seed = _validate_refresh(refresh_token)
        self._seed_fingerprint = hashlib.sha256(seed.encode("ascii")).hexdigest()
        self._client = client
        self._rate_limits = rate_limits if rate_limits is not None else RateLimitState()
        self._base_url = base_url.rstrip("/")
        self._path = Path(session_path) if session_path is not None else None
        self._lock = threading.RLock()
        self._access_token = access_token
        self._refresh_count = 0
        self._generation = 0
        self._subject = _claims(access_token).get("sub") if access_token else None
        self._device_id = str(uuid.uuid4())
        self._refresh_token = seed
        self._storage_dirty = False
        if self._path is not None:
            if self._path.exists():
                self._load(self._path)
            else:
                self._save()

    def _load(self, path: Path) -> None:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if (
                not isinstance(state, dict)
                or state.get("version") != 1
                or state.get("seed_fingerprint") != self._seed_fingerprint
                or state.get("base_url") != self._base_url
            ):
                raise ValueError("session belongs to a different initial token")
            self._refresh_token = _validate_refresh(state["refresh_token"])
            stored_subject = state.get("subject")
            if stored_subject is not None and not isinstance(stored_subject, str):
                raise ValueError("invalid stored account")
            if self._subject and stored_subject and self._subject != stored_subject:
                raise ValueError("session belongs to a different account")
            self._subject = stored_subject or self._subject
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise SessionStorageError(
                "Cannot read refresh session or initial token does not match"
            ) from exc

    def _save(self) -> None:
        if self._path is None:
            return
        state = {
            "version": 1,
            "seed_fingerprint": self._seed_fingerprint,
            "base_url": self._base_url,
            "refresh_token": self._refresh_token,
            "subject": self._subject,
        }
        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=".itd-session-", dir=self._path.parent
            )
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, self._path)
        except OSError as exc:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass
            raise SessionStorageError("Cannot save refresh session") from exc

    def set_access_token(self, token: str) -> None:
        with self._lock:
            subject = _claims(token).get("sub")
            if self._subject and subject != self._subject:
                raise ProtocolError("Access token belongs to a different account")
            if not self._subject and isinstance(subject, str) and subject:
                self._subject = subject
                self._storage_dirty = True
                self._save()
                self._storage_dirty = False
            self._access_token = token
            self._generation += 1

    @property
    def subject(self) -> str | None:
        """Account ID (JWT ``sub``) once known; stays the same across refreshes."""
        with self._lock:
            return self._subject

    @property
    def refresh_count(self) -> int:
        with self._lock:
            return self._refresh_count

    def get_access_token(self) -> str:
        with self._lock:
            if self._storage_dirty:
                self._save()
                self._storage_dirty = False
            claims = _claims(self._access_token) if self._access_token else {}
            expiry = claims.get("exp")
            if not self._access_token or (
                isinstance(expiry, (int, float))
                and not isinstance(expiry, bool)
                and expiry <= time.time() + 30
            ):
                self._refresh()
            return self._fresh_token()

    def get_access_state(self) -> tuple[str, int]:
        with self._lock:
            return self.get_access_token(), self._generation

    def refresh_after_401(self, rejected_token: str, generation: int) -> str:
        with self._lock:
            if self._storage_dirty:
                self._save()
                self._storage_dirty = False
            if self._access_token and (
                self._generation != generation or self._access_token != rejected_token
            ):
                return self._access_token
            self._refresh()
            return self._fresh_token()

    def _fresh_token(self) -> str:
        # _refresh stores a token or raises, so it is never None here.
        return cast(str, self._access_token)

    def _refresh(self) -> None:
        if self._storage_dirty:
            self._save()
            self._storage_dirty = False
        origin = urlsplit(self._base_url)
        headers = {
            "Cookie": "refresh_token=" + self._refresh_token,
            "Origin": f"{origin.scheme}://{origin.netloc}",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/json",
            "X-Device-Id": self._device_id,
        }
        if self._access_token:
            headers["Authorization"] = "Bearer " + self._access_token
        self._rate_limits.acquire("POST", "/api/v1/auth/refresh")
        response = self._client.post(
            self._base_url + "/api/v1/auth/refresh",
            headers=headers,
            follow_redirects=False,
        )
        self._rate_limits.observe(response, "POST", "/api/v1/auth/refresh")
        if not response.is_success:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            detail = payload.get("error", payload) if isinstance(payload, dict) else {}
            if not isinstance(detail, dict):
                detail = {}
            raise APIError(
                response.status_code,
                "Session refresh failed",
                code=detail.get("code"),
                errors=detail.get("errors"),
                retry_after=response.headers.get("Retry-After"),
            )
        new_values = []
        removed = False
        for header in response.headers.get_list("set-cookie"):
            cookie = SimpleCookie()
            try:
                cookie.load(header)
            except Exception as exc:
                raise ProtocolError("Invalid refresh cookie response") from exc
            morsel = cookie.get("refresh_token")
            if morsel is not None and (not morsel.value or morsel["max-age"] == "0"):
                removed = True
            if morsel is not None and morsel.value and morsel["max-age"] != "0":
                try:
                    new_values.append(_validate_refresh(morsel.value))
                except ValueError as exc:
                    raise ProtocolError("Invalid refresh cookie response") from exc
        if len(set(new_values)) > 1:
            raise ProtocolError("Conflicting refresh cookies in response")
        if removed and not new_values:
            raise ProtocolError("Server removed the refresh cookie")
        if new_values and new_values[0] != self._refresh_token:
            self._refresh_token = new_values[0]
            self._storage_dirty = True
            self._save()
            self._storage_dirty = False
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProtocolError("Invalid refresh response") from exc
        token = payload.get("accessToken") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token or "\n" in token or "\r" in token:
            raise ProtocolError("Refresh response has no valid accessToken")
        subject = _claims(token).get("sub")
        if self._subject and subject != self._subject:
            raise ProtocolError("Refreshed session belongs to a different account")
        if not self._subject and isinstance(subject, str) and subject:
            self._subject = subject
            self._storage_dirty = True
            self._save()
            self._storage_dirty = False
        self._access_token = token
        self._refresh_count += 1
        self._generation += 1
