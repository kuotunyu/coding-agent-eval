"""Error types.

Each carries the HTTP status it should produce, so the API layer never has to
map exception classes to status codes and cannot drift out of step with them.
"""

from __future__ import annotations


class TaskqError(Exception):
    """Base class. `status` is what the API returns."""

    status = 500
    code = "internal_error"


class ValidationError(TaskqError):
    """The request was malformed or violated a documented constraint."""

    status = 400
    code = "invalid_request"


class PayloadTooLarge(TaskqError):
    status = 413
    code = "payload_too_large"


class NotFound(TaskqError):
    status = 404
    code = "not_found"


class Unauthorized(TaskqError):
    """Authentication failed.

    The message is deliberately identical for a missing token and a wrong one:
    a different response would tell an unauthenticated caller which of the two
    happened, which is information they have no use for except to probe.
    """

    status = 401
    code = "unauthorized"


class Conflict(TaskqError):
    """The task is not in a state where this operation makes sense."""

    status = 409
    code = "conflict"
