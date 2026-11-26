from __future__ import annotations

from typing import Any


class APIError(Exception):
    def __init__(
        self,
        status: int,
        message: str,
        *,
        code: str | None = None,
        errors: Any = None,
        retry_after: str | None = None,
        response: Any = None,
    ):
        super().__init__(message)
        self.status, self.code, self.errors, self.retry_after = (
            status,
            code,
            errors,
            retry_after,
        )
        self.response = response


class NukstaRequiredError(APIError):
    """Sending requires an ITD NUKSTA subscription."""


class BlockedError(APIError):
    """The server rejected the operation because of a block."""


class MessagesClosedError(APIError):
    """The recipient's messages are closed."""


_ERROR_TYPES: dict[str, type[APIError]] = {
    "nuksta_required": NukstaRequiredError,
    "blocked": BlockedError,
    "messages_closed": MessagesClosedError,
}


def _api_error(
    status: int,
    message: str,
    *,
    code: str | None = None,
    errors: Any = None,
    retry_after: str | None = None,
    response: Any = None,
) -> APIError:
    error_type = _ERROR_TYPES.get(code, APIError) if isinstance(code, str) else APIError
    return error_type(
        status,
        message,
        code=code,
        errors=errors,
        retry_after=retry_after,
        response=response,
    )


class RateLimitError(APIError):
    """A request exceeded a server limit or is blocked by its cooldown."""

    def __init__(
        self,
        *,
        retry_after_seconds: int,
        retry_after: str | None = None,
        scope: str | None = None,
        locally_blocked: bool = False,
        code: str | None = "RATE_LIMIT_EXCEEDED",
        message: str = "Rate limit exceeded",
        errors: Any = None,
    ):
        super().__init__(
            429, message, code=code, errors=errors, retry_after=retry_after
        )
        self.retry_after_seconds = retry_after_seconds
        self.scope = scope
        self.locally_blocked = locally_blocked


class ProtocolError(Exception):
    """The server returned a response incompatible with the observed protocol."""


class ResetRequired(ProtocolError):
    """The server cannot replay all requested events; reconcile application state."""

    def __init__(self, response: dict[str, Any]):
        super().__init__(
            "Event history reset required; reconcile state before resuming"
        )
        self.response = response


class SessionStorageError(Exception):
    """The persistent refresh session could not be read or safely saved."""


class UploadQuotaError(APIError):
    """Upload budget is exhausted; a timed retry may not free it."""
