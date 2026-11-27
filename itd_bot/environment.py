"""Explicit environment parsing; this module never opens .env or logs values."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import fields
from typing import Any

from .rate_limits import RateLimitConfig, RateLimiter


def _integer(name: str, value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise ValueError(f"{name} must be a positive integer") from None
    if result < 1:
        raise ValueError(f"{name} must be a positive integer")
    return result


def _number(name: str, value: str, *, positive: bool = False) -> float:
    try:
        result = float(value)
    except ValueError:
        raise ValueError(f"{name} must be a finite number") from None
    if not math.isfinite(result) or result < 0 or (positive and result == 0):
        requirement = "positive" if positive else "nonnegative"
        raise ValueError(f"{name} must be finite and {requirement}")
    return result


def _boolean(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return normalized == "true"


def _rate_options(environ: Mapping[str, str]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for field in fields(RateLimitConfig):
        name = "ITD_RATE_" + field.name.upper()
        if name not in environ:
            continue
        value = environ[name]
        if field.name in {"enabled", "wait"}:
            options[field.name] = _boolean(name, value)
        elif field.name == "requests_per_minute" and value.strip().lower() == "none":
            options[field.name] = None
        elif field.name.endswith("_interval"):
            options[field.name] = _number(name, value)
        else:
            options[field.name] = _integer(name, value)
    return options


def _environment_options(
    environ: Mapping[str, str], overrides: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    client = dict(overrides)
    for name, parameter in (
        ("ITD_TOKEN", "token"),
        ("ITD_REFRESH_TOKEN", "refresh_token"),
        ("ITD_SESSION_PATH", "session_path"),
    ):
        if parameter not in client and name in environ:
            client[parameter] = environ[name] or None
    if client.get("token") is None and client.get("refresh_token") is None:
        raise ValueError("Set ITD_TOKEN or ITD_REFRESH_TOKEN")
    polling: dict[str, Any] = {}
    if "ITD_WORKERS" in environ:
        polling["workers"] = _integer("ITD_WORKERS", environ["ITD_WORKERS"])
    if "ITD_POLL_INTERVAL" in environ:
        polling["interval"] = _number(
            "ITD_POLL_INTERVAL", environ["ITD_POLL_INTERVAL"], positive=True
        )
    dispatcher: dict[str, Any] = {}
    if "ITD_READ_MODE" in environ:
        mode = environ["ITD_READ_MODE"]
        if mode not in {"blocking", "background", "batch", "off"}:
            raise ValueError("ITD_READ_MODE must be blocking, background, batch or off")
        dispatcher["read_mode"] = mode
    if "limiter" not in client:
        rates = _rate_options(environ)
        if rates:
            client["limiter"] = RateLimiter(RateLimitConfig(**rates))
    return client, polling, dispatcher
