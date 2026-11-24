from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import unquote

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


_EPSILON = 1e-9
_PRUNE_INTERVAL = 5.0
_MAX_BUCKETS = 4096
_CHAT_PATH = re.compile(r"/api/v1/messenger/chats/([^/]+)/")
_USER_PATH = re.compile(r"/api/v1/messenger/users/([^/]+)/")

BucketScope = tuple[str, ...]


def _bucket_scope(group: str, method: str, path: str) -> BucketScope:
    """Return the account-relative token-bucket scope for a limited group.

    Sending, forwarding, reactions, editing and deleting for oneself are counted
    per chat (the chat ID comes from the request path); sending to a user is a
    separate scope per user ID.
    """
    path = path.split("?", 1)[0]
    if group in {"send", "reaction", "edit", "delete_for_me"}:
        chat = _CHAT_PATH.match(path)
        if chat:
            return group, "chat", unquote(chat.group(1))
        user = _USER_PATH.match(path)
        if user:
            return group, "user", unquote(user.group(1))
    return (group,)


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

    def _bucket_key(
        self, method: str, path: str, account: str | None = None
    ) -> tuple[str, ...]:
        return self._key(method, path)

    def acquire(
        self, method: str, path: str, *, cost: int = 1, account: str | None = None
    ) -> None:
        self.check(method, path, account)

    def _on_group_cooldown(self, key: tuple[str, ...], deadline: float) -> None:
        """Called under the lock after a 429 set a cooldown for a limited group."""

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

    def check(self, method: str, path: str, account: str | None = None) -> None:
        keys = (
            self._bucket_key(method, path, account),
            self._key(method, path),
            ("*", "*"),
        )
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

    def observe(
        self,
        response: httpx.Response,
        method: str,
        path: str,
        account: str | None = None,
    ) -> None:
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
        coarse = max(self._seconds(raw, 0), self._seconds(str(body_retry), 0))
        seconds = coarse
        precise: float | None = None
        try:
            milliseconds = float(
                response.headers.get("X-RateLimit-Retry-After-Ms", "0")
            )
        except ValueError:
            milliseconds = 0
        if math.isfinite(milliseconds) and milliseconds > 0:
            seconds = max(seconds, math.ceil(milliseconds / 1000))
            if coarse <= math.ceil(milliseconds / 1000):
                precise = milliseconds / 1000
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
            key = self._bucket_key(method, path, account)
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
            is_group = key[0] == "group"
            wait = precise if is_group and precise is not None else seconds
            deadline = now + wait
            existing = self._deadlines.get(key)
            if existing is None or deadline > existing[0]:
                self._deadlines[key] = (deadline, scope)
            if is_group:
                self._on_group_cooldown(key, self._deadlines[key][0])
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
    requests_per_minute: int | None = 80
    min_interval: float = 0.0
    send_interval: float = 2.1
    reaction_interval: float = 1.1
    edit_interval: float = 6.2
    delete_for_me_interval: float = 3.2
    send_burst: int = 4
    edit_burst: int = 4
    reaction_burst: int = 12
    delete_for_me_burst: int = 8

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
        for name in (
            "send_burst",
            "edit_burst",
            "reaction_burst",
            "delete_for_me_burst",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be an integer >= 1")


class RateLimiter(RateLimitState):
    """Coordinate request starts and observed cooldowns across threads.

    Message groups use a token bucket (GCRA): ``*_interval`` is the refill period
    of one token and ``*_burst`` the bucket size, so ``burst=1`` keeps a fixed
    interval. Buckets are kept per ``account`` passed to ``acquire`` and
    ``observe`` (``None`` is one shared account): sending, forwarding and
    reactions, editing and deleting for oneself per chat, sending to a user per
    user ID. The request budget and IP cooldowns stay shared by the
    whole instance. Waiting never reserves capacity ahead of other callers. An
    already dispatched request cannot be recalled after a concurrent 429.
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
        self._buckets: dict[tuple[str | None, BucketScope], float] = {}
        self._last_prune = 0.0

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

    def _bucket_key(
        self, method: str, path: str, account: str | None = None
    ) -> tuple[str, ...]:
        group = self._group(method, path)
        if group:
            return ("group", account, *_bucket_scope(group, method, path))
        method, path = self._key(method, path)
        for candidate_method, pattern, template in _ROUTE_PATTERNS:
            if method == candidate_method and pattern.fullmatch(path):
                return method, template
        return method, path

    def _bucket_params(self, group: str) -> tuple[float, int]:
        return (
            getattr(self.config, group + "_interval"),
            getattr(self.config, group + "_burst"),
        )

    def _bucket_delay(
        self, key: tuple[str | None, BucketScope], now: float, cost: int
    ) -> float:
        interval, burst = self._bucket_params(key[1][0])
        tat = self._buckets.get(key, 0.0)
        if cost > burst:
            return max(0.0, tat - now)
        return max(0.0, tat - now + (cost - burst) * interval)

    def _bucket_spend(
        self, key: tuple[str | None, BucketScope], now: float, cost: int
    ) -> None:
        interval, _ = self._bucket_params(key[1][0])
        self._buckets[key] = max(self._buckets.get(key, 0.0), now) + cost * interval
        self._trim_buckets(now, force=len(self._buckets) > _MAX_BUCKETS, keep=key)

    def _trim_buckets(
        self,
        now: float,
        *,
        force: bool = False,
        keep: tuple[str | None, BucketScope] | None = None,
    ) -> None:
        """Drop fully recovered buckets; cap the number of remaining ones.

        A bucket with ``tat <= now`` behaves exactly like a missing one, so it is
        safe to forget. Called under the lock.
        """
        if not force and now - self._last_prune < _PRUNE_INTERVAL:
            return
        self._last_prune = now
        for key in [k for k, tat in self._buckets.items() if tat <= now]:
            del self._buckets[key]
        excess = len(self._buckets) - _MAX_BUCKETS
        if excess > 0:
            oldest = sorted(
                (key for key in self._buckets if key != keep),
                key=self._buckets.__getitem__,
            )
            for key in oldest[: excess + _MAX_BUCKETS // 4]:
                del self._buckets[key]

    def _on_group_cooldown(self, key: tuple[str, ...], deadline: float) -> None:
        bucket = (key[1], tuple(key[2:]))
        interval, burst = self._bucket_params(bucket[1][0])
        self._buckets[bucket] = max(
            self._buckets.get(bucket, 0.0), deadline + (burst - 1) * interval
        )
        self._trim_buckets(
            self._monotonic(), force=len(self._buckets) > _MAX_BUCKETS, keep=bucket
        )

    def acquire(
        self, method: str, path: str, *, cost: int = 1, account: str | None = None
    ) -> None:
        if type(cost) is not int or cost < 1:
            raise ValueError("cost must be a positive integer")
        keys = (
            self._bucket_key(method, path, account),
            self._key(method, path),
            ("*", "*"),
        )
        group = self._group(method, path)
        bucket = (account, _bucket_scope(group, method, path)) if group else None
        if not self.config.wait:
            self.check(method, path, account)
        while True:
            with self._lock:
                now = self._monotonic()
                for old in list(self._deadlines):
                    if self._deadlines[old][0] <= now:
                        del self._deadlines[old]
                self._trim_buckets(now)
                delay = max(
                    (self._deadlines[k][0] - now for k in keys if k in self._deadlines),
                    default=0.0,
                )
                if self.config.enabled:
                    while self._starts and self._starts[0] + 60 <= now:
                        self._starts.popleft()
                    delay = max(delay, self._next_start - now)
                    if bucket:
                        delay = max(delay, self._bucket_delay(bucket, now, cost))
                    if (
                        self.config.requests_per_minute is not None
                        and len(self._starts) >= self.config.requests_per_minute
                    ):
                        delay = max(delay, self._starts[0] + 60 - now)
                if delay <= _EPSILON:
                    if self.config.enabled:
                        if self.config.requests_per_minute is not None:
                            self._starts.append(now)
                        self._next_start = now + self.config.min_interval
                        if bucket:
                            self._bucket_spend(bucket, now, cost)
                    return
            if not self.config.wait:
                self.check(method, path, account)
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
