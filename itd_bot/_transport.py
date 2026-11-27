"""Internal HTTP request validation, read retries and response handling."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .errors import _api_error, _InvalidJSON
from .polling import backoff_delay, error_status, is_transient

if TYPE_CHECKING:
    from .client import Bot
_log = logging.getLogger("itd_bot.client")
_NO_RETRY = "_itd_no_retry"


def _id(value: str) -> str:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise ValueError("ID must be a non-empty string")
    return quote(value, safe="")


def api_request(
    self: Bot,
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None = None,
    json: Any = None,
    content: bytes | None = None,
    files: Any = None,
    headers: Mapping[str, str] | None = None,
    raw: bool = False,
    sleep: Callable[[float], None],
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
        key.lower() in {"authorization", "host", "cookie"} for key in request_headers
    ):
        raise ValueError("authentication and host headers are managed by the SDK")
    retries = self._get_retries if method.upper() == "GET" else 0
    attempt = 0
    while True:
        try:
            return self._api_request_once(
                method,
                path,
                params=params,
                json=json,
                content=content,
                files=files,
                headers=dict(request_headers),
                raw=raw,
            )
        except Exception as exc:
            if (
                attempt >= retries
                or getattr(exc, _NO_RETRY, False)
                or not is_transient(exc)
            ):
                raise
            attempt += 1
            delay = backoff_delay(attempt, 0.5, 2.0)
            _log.warning(
                "GET failed (%s, status %s); retry %d of %d in %.1f s",
                type(exc).__name__,
                error_status(exc),
                attempt,
                retries,
                delay,
            )
            sleep(delay)


def _api_request_once(
    self: Bot,
    method: str,
    path: str,
    *,
    params: Mapping[str, Any] | None,
    json: Any,
    content: bytes | None,
    files: Any,
    headers: dict[str, str],
    raw: bool,
) -> Any:
    cost = 1
    if (
        method.upper() == "POST"
        and path.endswith("/messages/forward")
        and isinstance(json, Mapping)
    ):
        message_ids = json.get("message_ids")
        if isinstance(message_ids, list):
            cost = max(1, len(message_ids))
    try:
        access_token, generation = self._access_state()
    except Exception as exc:
        # A scheduled refresh is a write too: do not repeat it with the GET.
        setattr(exc, _NO_RETRY, True)
        raise
    account = self._account_key(access_token)
    self._rate_limits.acquire(method, path, cost=cost, account=account)
    headers.update(
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
        headers=headers,
    )
    self._rate_limits.observe(response, method, path, account=account)
    if (
        response.status_code == 401
        and self._refresh_session is not None
        and files is None
        and not path.startswith("/api/v1/auth/")
    ):
        headers["Authorization"] = "Bearer " + self._refresh_after_401(
            access_token, generation
        )
        self._rate_limits.acquire(method, path, cost=cost, account=account)
        response = self._client.request(
            method,
            self.base_url + path,
            params=params,
            json=json,
            content=content,
            follow_redirects=False,
            headers=headers,
        )
        self._rate_limits.observe(response, method, path, account=account)
    if response.is_success and raw:
        return response
    try:
        data = response.json() if response.content else None
    except ValueError:
        if response.is_success:
            raise _InvalidJSON(response.content) from None
        data = None
    if not response.is_success:
        detail = data.get("error", data) if isinstance(data, dict) else {}
        if not isinstance(detail, dict):
            detail = {}
        raise _api_error(
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
            response=data,
        )
    return data
