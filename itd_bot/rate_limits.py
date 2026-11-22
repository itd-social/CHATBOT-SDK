from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Callable

import httpx

from .errors import RateLimitError, UploadQuotaError


_ROUTE_TEMPLATES = (
    ("POST", "/api/v1/messenger/chats/{chat}/messages/delete-for-all"),
    ("POST", "/api/v1/messenger/chats/{chat}/messages/delete-for-me"),
    ("GET", "/api/v1/messenger/chats"),
    ("GET", "/api/v1/messenger/chats/{chat}"),
    ("GET", "/api/v1/messenger/chats/{chat}/messages"),
    ("GET", "/api/v1/messenger/chats/{chat}/messages/around/{message}"),
    ("GET", "/api/v1/messenger/chats/unread"),
    ("GET", "/api/v1/messenger/updates"),
    ("PATCH", "/api/v1/messenger/chats/{chat}/pin"),
    ("PATCH", "/api/v1/messenger/chats/{chat}/mute"),
    ("PATCH", "/api/v1/messenger/chats/{chat}/unread"),
    ("POST", "/api/v1/messenger/chats/{chat}/read"),
    ("POST", "/api/v1/messenger/chats/{chat}/action"),
    ("GET", "/api/v1/messenger/reactions/available"),
    ("GET", "/api/v1/messenger/chats/{chat}/messages/{message}/reactions"),
    ("GET", "/api/v1/messenger/presence/{user}"),
    ("POST", "/api/v1/messenger/presence"),
    ("POST", "/api/v1/messenger/chats/{chat}/messages/{message}/heard"),
    ("POST", "/api/v1/messenger/chats/{chat}/heard"),
    ("GET", "/api/v1/messenger/chats/{chat}/media"),
    ("POST", "/api/v1/messenger/media/upload/init"),
    ("PUT", "/api/v1/messenger/media/upload/{upload}/part/{part}"),
    ("POST", "/api/v1/messenger/media/upload/{upload}/finalize"),
    ("POST", "/api/v1/messenger/chats/{chat}/file-links"),
    ("POST", "/api/v1/messenger/browser-file-session"),
    ("GET", "/v1/chat-files/{id}"),
    ("POST", "/api/v1/messenger/chats/dm"),
    ("POST", "/api/v1/messenger/chats/{chat}/accept"),
)
_ROUTE_PATTERNS = tuple(
    (
        method,
        re.compile(
            "".join(
                "[^/]+" if part.startswith("{") else re.escape(part)
                for part in re.split(r"(\{[^}]+\})", template)
            )
        ),
        template,
    )
    for method, template in sorted(
        _ROUTE_TEMPLATES, key=lambda item: item[1].count("{")
    )
)


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

    def _bucket_key(self, method: str, path: str) -> tuple[str, str]:
        return self._key(method, path)

    def acquire(self, method: str, path: str, *, cost: int = 1) -> None:
        self.check(method, path)

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
        keys = (self._bucket_key(method, path), self._key(method, path), ("*", "*"))
        with self._lock:
            now = self._monotonic()
            candidates = [self._deadlines.get(key) for key in keys]
            active = [item for item in candidates if item is not None and item[0] > now]
            if not active:
                for key in keys:
                    self._deadlines.pop(key, None)
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
        try:
            body = response.json()
        except ValueError:
            body = {}
        detail = body.get("error", body) if isinstance(body, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        method, path = self._key(method, path)
        is_message = method == "POST" and re.search(
            r"/messenger/(?:chats|users)/[^/]+/messages$", path
        )
        raw = response.headers.get("Retry-After")
        fallback = 2 if is_message else 30
        body_retry = detail.get("retryAfter", detail.get("retry_after"))
        seconds = max(self._seconds(raw, 0), self._seconds(str(body_retry), 0))
        try:
            milliseconds = float(
                response.headers.get("X-RateLimit-Retry-After-Ms", "0")
            )
        except ValueError:
            milliseconds = 0
        if math.isfinite(milliseconds) and milliseconds > 0:
            seconds = max(seconds, math.ceil(milliseconds / 1000))
        seconds = seconds or fallback
        if detail.get("code") == "UPLOAD_QUOTA_EXCEEDED":
            raise UploadQuotaError(
                429,
                str(detail.get("message") or "Upload budget exhausted"),
                code="UPLOAD_QUOTA_EXCEEDED",
                retry_after=raw,
                errors=detail.get("errors"),
            )
        scope = response.headers.get("X-RateLimit-Scope")
        if scope in {"ip_flood", "global_flood"}:
            key = ("*", "*")
        elif scope in {"new_recipients_hour", "new_recipients_day"}:
            key = self._key(method, path)
        else:
            key = self._bucket_key(method, path)
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


@dataclass(frozen=True)
class RateLimitConfig:
    """Client safety settings, not a server quota specification."""

    enabled: bool = True
    wait: bool = True
    requests_per_minute: int | None = 50
    min_interval: float = 0.0
    send_interval: float = 2.1
    reaction_interval: float = 1.1
    edit_interval: float = 6.2
    delete_for_me_interval: float = 3.2

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.wait) is not bool:
            raise ValueError("enabled and wait must be bool")
        if self.requests_per_minute is not None and (
            type(self.requests_per_minute) is not int or self.requests_per_minute < 1
        ):
            raise ValueError("requests_per_minute must be a positive integer or None")
        for name in (
            "min_interval",
            "send_interval",
            "reaction_interval",
            "edit_interval",
            "delete_for_me_interval",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError(f"{name} must be a finite nonnegative number")


class RateLimiter(RateLimitState):
    """Coordinate request starts and observed cooldowns across threads.

    Message rules conservatively share capacity across all chats and accounts
    using this instance. Waiting never reserves capacity ahead of other callers.
    An already dispatched request cannot be recalled after a concurrent 429.
    """

    def __init__(
        self,
        config: RateLimitConfig | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        wall_time: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(monotonic=monotonic, wall_time=wall_time)
        self.config = config if config is not None else RateLimitConfig()
        self._sleep = sleep
        self._starts: deque[float] = deque()
        self._next_start = 0.0
        self._groups: dict[str, float] = {}

    @staticmethod
    def _group(method: str, path: str) -> str | None:
        method, path = RateLimitState._key(method, path)
        prefix = r"/api/v1/messenger/"
        if method == "POST" and re.fullmatch(
            prefix + r"(?:chats/[^/]+/messages(?:/forward)?|users/[^/]+/messages)", path
        ):
            return "send"
        if method == "PATCH" and re.fullmatch(
            prefix + r"chats/[^/]+/messages/[^/]+", path
        ):
            return "edit"
        if method == "PUT" and re.fullmatch(
            prefix + r"chats/[^/]+/messages/[^/]+/reactions", path
        ):
            return "reaction"
        if method == "POST" and re.fullmatch(
            prefix + r"chats/[^/]+/messages/delete-for-me", path
        ):
            return "delete_for_me"
        return None

    def _bucket_key(self, method: str, path: str) -> tuple[str, str]:
        group = self._group(method, path)
        if group:
            return "group", group
        method, path = self._key(method, path)
        for candidate_method, pattern, template in _ROUTE_PATTERNS:
            if method == candidate_method and pattern.fullmatch(path):
                return method, template
        return method, path

    def acquire(self, method: str, path: str, *, cost: int = 1) -> None:
        if type(cost) is not int or cost < 1:
            raise ValueError("cost must be a positive integer")
        keys = (self._bucket_key(method, path), self._key(method, path), ("*", "*"))
        group = self._group(method, path)
        if not self.config.wait:
            self.check(method, path)
        while True:
            with self._lock:
                now = self._monotonic()
                for old in list(self._deadlines):
                    if self._deadlines[old][0] <= now:
                        del self._deadlines[old]
                delay = max(
                    (self._deadlines[k][0] - now for k in keys if k in self._deadlines),
                    default=0.0,
                )
                if self.config.enabled:
                    while self._starts and self._starts[0] + 60 <= now:
                        self._starts.popleft()
                    delay = max(delay, self._next_start - now)
                    if group:
                        delay = max(delay, self._groups.get(group, 0) - now)
                    if (
                        self.config.requests_per_minute is not None
                        and len(self._starts) >= self.config.requests_per_minute
                    ):
                        delay = max(delay, self._starts[0] + 60 - now)
                if delay <= 0:
                    if self.config.enabled:
                        if self.config.requests_per_minute is not None:
                            self._starts.append(now)
                        self._next_start = now + self.config.min_interval
                        if group:
                            self._groups[group] = now + cost * getattr(
                                self.config, group + "_interval"
                            )
                    return
            if not self.config.wait:
                self.check(method, path)
                raise RateLimitError(
                    retry_after_seconds=max(1, math.ceil(delay)), locally_blocked=True
                )
            self._sleep(delay)


_default_lock = threading.Lock()
_default_limiters: dict[str, RateLimiter] = {}


def default_limiter(origin: str) -> RateLimiter:
    """Share default pacing for clients targeting the same origin in this process."""
    with _default_lock:
        if origin not in _default_limiters:
            _default_limiters[origin] = RateLimiter()
        return _default_limiters[origin]
