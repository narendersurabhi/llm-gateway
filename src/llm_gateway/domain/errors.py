"""Domain errors, one per error `type` in the conventions' error envelope.

Inbound adapters translate them to their transport (an HTTP status, a queue retry, ...).
`public_message` is shown to callers, so it never holds secrets, user content or exception
text from a dependency.
"""

from __future__ import annotations


class DomainError(Exception):
    type = "internal"
    default_message = "internal error"

    def __init__(self, public_message: str = "", *, retry_after_s: float | None = None) -> None:
        self.public_message = public_message or self.default_message
        self.retry_after_s = retry_after_s
        super().__init__(self.public_message)


class InvalidRequest(DomainError):
    type = "invalid_request"
    default_message = "invalid request"


class Unauthenticated(DomainError):
    type = "unauthenticated"
    default_message = "missing or invalid credentials"


class Forbidden(DomainError):
    type = "forbidden"
    default_message = "not allowed"


class NotFound(DomainError):
    type = "not_found"
    default_message = "not found"


class Conflict(DomainError):
    type = "conflict"
    default_message = "conflict"


class RateLimited(DomainError):
    type = "rate_limited"
    default_message = "rate limited"


class UpstreamError(DomainError):
    type = "upstream_error"
    default_message = "a dependency failed"


class Unavailable(DomainError):
    type = "unavailable"
    default_message = "temporarily unavailable"


class DeadlineExceeded(DomainError):
    type = "timeout"
    default_message = "deadline exceeded"
