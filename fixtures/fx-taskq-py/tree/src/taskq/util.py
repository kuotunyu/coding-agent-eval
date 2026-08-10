"""Small helpers with no dependencies of their own."""

from __future__ import annotations

import math
import re
import time
import uuid
from typing import Any

from taskq.config import QUEUE_NAME_PATTERN
from taskq.errors import ValidationError

_QUEUE_NAME = re.compile(QUEUE_NAME_PATTERN)


def now() -> float:
    """Wall clock, in seconds.

    Isolated behind a function so tests can substitute a clock without patching
    the standard library module everywhere it is used.
    """
    return time.time()


def new_task_id() -> str:
    return uuid.uuid4().hex


def validate_queue_name(name: str) -> str:
    """Return `name` if it is a legal queue name, else raise.

    Queue names appear in URLs and are stored as given, so the legal set is
    small and checked before the value reaches storage rather than after.
    """
    # `fullmatch`, not `match`. The pattern ends in `$`, and `$` also matches
    # immediately before a final newline — so with `match` a name ending in a
    # newline passes. That name is then a queue distinct from the one without
    # it, which no worker polling the intended queue ever reads: the enqueue
    # succeeds and the work is silently orphaned.
    if not isinstance(name, str) or not _QUEUE_NAME.fullmatch(name):
        raise ValidationError(
            f"queue name {name!r} must match {QUEUE_NAME_PATTERN}"
        )
    return name


def validate_payload(payload: Any) -> dict[str, Any]:
    """Payloads are JSON objects. A list or a scalar is a client mistake."""
    if not isinstance(payload, dict):
        raise ValidationError("payload must be a JSON object")
    return payload


def validate_priority(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError("priority must be an integer")
    return value


def validate_max_attempts(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError("max_attempts must be an integer of at least 1")
    return value


def validate_delay_seconds(value: Any) -> float:
    """Seconds to hold a task before it becomes runnable.

    Coercion is deliberately not a bare `float()`. That call raises `ValueError`
    on a string and `TypeError` on a container, and neither is a `TaskqError`, so
    both escape the API's error handling entirely and reach the client as a
    failed request rather than the documented 400.

    `nan` is the case a range check alone does not cover: `float("nan")`
    succeeds, and every comparison against it is false, so `delay < 0` lets it
    through to storage where `available_at` is NOT NULL and the insert fails
    partway through the request.
    """
    if value is None:
        return 0.0
    # `bool` first: it is a subclass of `int`, and `True` as a delay is a
    # client mistake rather than one second.
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValidationError("delay_seconds must be a number")
    delay = float(value)
    if not math.isfinite(delay):
        raise ValidationError("delay_seconds must be a finite number")
    if delay < 0:
        raise ValidationError("delay_seconds must not be negative")
    return delay


def retry_delay(attempts: int, base: float, maximum: float) -> float:
    """Exponential backoff for a task that has now been attempted `attempts` times.

    The first retry waits `base`, not `base * 2`: the exponent counts retries
    already taken, and after one attempt none have been.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1 to compute a retry delay")
    return min(base * (2 ** (attempts - 1)), maximum)
