"""HTTP error type for the local API.

Handlers raise ApiError with the exact status the client should see;
the transport layer serializes it into the standard ApplicationResult
failure envelope. Typed service exceptions are mapped to ApiError in
LocalApi.handle, so handlers only raise ApiError for API-level
decisions (bad input, state conflicts, missing records).
"""
from __future__ import annotations

from typing import Any, Mapping


class ApiError(Exception):
    """An error with an HTTP status and a client-safe message."""

    def __init__(
        self,
        status: int,
        message: str,
        *,
        code: str = "",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        if not 400 <= status <= 599:
            raise ValueError("API error status must be 4xx or 5xx.")
        self.status = status
        self.message = str(message).strip()[:500] or "Request failed."
        self.code = str(code).strip()[:80]
        self.details = dict(details) if details else {}
        super().__init__(self.message)

    @classmethod
    def bad_request(cls, message: str, **kwargs: Any) -> "ApiError":
        return cls(400, message, code="bad_request", **kwargs)

    @classmethod
    def not_found(cls, message: str, **kwargs: Any) -> "ApiError":
        return cls(404, message, code="not_found", **kwargs)

    @classmethod
    def conflict(cls, message: str, **kwargs: Any) -> "ApiError":
        return cls(409, message, code="conflict", **kwargs)

    @classmethod
    def unprocessable(cls, message: str, **kwargs: Any) -> "ApiError":
        return cls(422, message, code="unprocessable", **kwargs)

    @classmethod
    def too_large(cls, message: str, **kwargs: Any) -> "ApiError":
        return cls(413, message, code="payload_too_large", **kwargs)

    @classmethod
    def too_many_requests(cls, message: str, **kwargs: Any) -> "ApiError":
        return cls(429, message, code="too_many_requests", **kwargs)

    @classmethod
    def timeout(cls, message: str, **kwargs: Any) -> "ApiError":
        return cls(504, message, code="request_timeout", **kwargs)

    @classmethod
    def internal(cls, message: str, **kwargs: Any) -> "ApiError":
        return cls(500, message, code="internal_error", **kwargs)

    def to_dict(self) -> dict[str, Any]:
        """Serialize into the ApplicationResult failure envelope."""
        data: dict[str, Any] = {}
        if self.code:
            data["error_code"] = self.code
        data.update(self.details)
        return {
            "status": "failure",
            "message": self.message,
            "data": data,
            "warnings": [],
            "errors": [self.message],
            "next_actions": [],
        }
