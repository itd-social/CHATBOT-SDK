from __future__ import annotations

import math
import re
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import httpx

from .errors import RateLimitError


class RateLimitState:
    """Track observed server cooldowns for one client instance."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
    ):
        self._monotonic = monotonic
        self._wall_time = wall_time
        self._lock = threading.Lock()
        self._deadlines: dict[tuple[str, str], tuple[float, str | None]] = {}

    @staticmethod
    def _key(method: str, path: str) -> tuple[str, str]:
        return method.upper(), path.split("?", 1)[0]

    def _seconds(self, value: str | None, fallback: int) -> int:
        if value is not None:
            value = value.strip()
            if re.fullmatch(r"\d+(?:\.\d+)?", value):
                number = float(value)
                if math.isfinite(number):
                    return max(1, math.ceil(number))
            try:
                date = parsedate_to_datetime(value)
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                delta = date.timestamp() - self._wall_time()
                if math.isfinite(delta):
                    return max(1, math.ceil(delta))
            except (TypeError, ValueError, OverflowError):
                pass
        return fallback

    def check(self, method: str, path: str) -> None:
        key = self._key(method, path)
        with self._lock:
            now = self._monotonic()
            candidates = [self._deadlines.get(key), self._deadlines.get(("*", "*"))]
            active = [item for item in candidates if item is not None and item[0] > now]
            if not active:
                self._deadlines.pop(key, None)
                self._deadlines.pop(("*", "*"), None)
                return
            deadline, scope = max(active, key=lambda item: item[0])
            seconds = max(1, math.ceil(deadline - now))
        raise RateLimitError(
            retry_after_seconds=seconds,
            retry_after=str(seconds),
            scope=scope,
            locally_blocked=True,
        )

    def observe(self, response: httpx.Response, method: str, path: str) -> None:
        if response.status_code != 429:
            return
        method, path = self._key(method, path)
        is_message = method == "POST" and re.search(
            r"/messenger/(?:chats|users)/[^/]+/messages$", path
        )
        raw = response.headers.get("Retry-After")
        seconds = self._seconds(raw, 2 if is_message else 30)
        scope = response.headers.get("X-RateLimit-Scope")
        key = ("*", "*") if scope in {"ip_flood", "global_flood"} else (method, path)
        with self._lock:
            now = self._monotonic()
            for old_key, (deadline, _) in list(self._deadlines.items()):
                if deadline <= now:
                    self._deadlines.pop(old_key, None)
            if key not in self._deadlines and len(self._deadlines) >= 256:
                removable = next(
                    (item for item in self._deadlines if item != ("*", "*")), None
                )
                if removable is not None:
                    self._deadlines.pop(removable, None)
            deadline = now + seconds
            existing = self._deadlines.get(key)
            if existing is None or deadline > existing[0]:
                self._deadlines[key] = (deadline, scope)
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("error", body) if isinstance(body, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        code = detail.get("code") or "RATE_LIMIT_EXCEEDED"
        message = str(
            detail.get("detail")
            or detail.get("message")
            or detail.get("title")
            or "Rate limit exceeded"
        )
        raise RateLimitError(
            retry_after_seconds=seconds,
            retry_after=raw,
            scope=scope,
            locally_blocked=False,
            code=code,
            message=message,
            errors=detail.get("errors") or detail.get("violations"),
        )
