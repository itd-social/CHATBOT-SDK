"""Helpers shared by the error-path tests. Every value here is fictional."""

from __future__ import annotations

import base64
import json
import os
import sys
import types
from collections.abc import Callable
from typing import Any

import httpx

from itd_bot import Bot
from itd_bot.rate_limits import RateLimitConfig, RateLimiter

BASE = "https://example.test"
API = "/api/v1/messenger"
FAR_FUTURE = 4_102_444_800  # year 2100: such a token never looks expired


def jwt(subject: str | None, expiry: float | None = FAR_FUTURE) -> str:
    """Build an unsigned, fictional JWT; ``subject=None`` leaves out ``sub``."""
    claims: dict[str, Any] = {}
    if subject is not None:
        claims["sub"] = subject
    if expiry is not None:
        claims["exp"] = expiry
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode()
    return f"header.{payload.rstrip('=')}.signature"


def sent_json(request: httpx.Request) -> Any:
    return json.loads(request.content)


class Transport:
    """Scripted ``httpx.MockTransport`` handler that records every request.

    Each item is consumed once, in order: a ``Response``, an exception to raise,
    or a callable ``request -> Response``. ``default`` answers when the script is
    exhausted; without it an extra request fails the test.
    """

    def __init__(self, *script: Any, default: Any = None):
        self.requests: list[httpx.Request] = []
        self._script = list(script)
        self._default = default

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._script:
            item = self._script.pop(0)
        elif self._default is not None:
            item = self._default
        else:
            raise AssertionError(f"unexpected request {request.method} {request.url}")
        if callable(item):
            item = item(request)
        if isinstance(item, BaseException):
            raise item
        return item

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def bot(self, token: str = "fictional-token", **kwargs: Any) -> Bot:
        kwargs.setdefault("base_url", BASE)
        return Bot(token, client=self.client(), **kwargs)

    @property
    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


def strict_limiter(clock: Callable[[], float] = lambda: 1000.0) -> RateLimiter:
    """Limiter that never sleeps: cooldowns after a 429 raise at once."""
    return RateLimiter(
        RateLimitConfig(enabled=False, wait=False),
        monotonic=clock,
        wall_time=lambda: 0.0,
    )


class _OSProxy:
    """Stand-in for the ``os`` module of one SDK module, with chosen overrides."""

    def __init__(self, **overrides: Any):
        self._overrides = overrides

    def __getattr__(self, name: str) -> Any:
        if name in self._overrides:
            return self._overrides[name]
        return getattr(os, name)


def patch_os(monkeypatch: Any, module: types.ModuleType, **overrides: Any) -> None:
    """Replace ``module.os`` so only this module sees the failing function."""
    monkeypatch.setattr(module, "os", _OSProxy(**overrides))


class Clock:
    """Manual monotonic clock; install it as ``module.time``."""

    def __init__(self, now: float = 0.0):
        self.now = now

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def patch_clock(monkeypatch: Any, module: types.ModuleType, clock: Clock) -> None:
    monkeypatch.setattr(
        module, "time", types.SimpleNamespace(monotonic=clock.monotonic)
    )


def httpx_namespace(**overrides: Any) -> types.SimpleNamespace:
    """A copy of the ``httpx`` namespace with some names replaced."""
    names = {
        name: getattr(httpx, name) for name in dir(httpx) if not name.startswith("_")
    }
    names.update(overrides)
    return types.SimpleNamespace(**names)


def install_websocket(monkeypatch: Any, module: types.ModuleType | None) -> None:
    """Make ``import websocket`` return ``module`` (``None`` means not installed)."""
    monkeypatch.setitem(sys.modules, "websocket", module)
