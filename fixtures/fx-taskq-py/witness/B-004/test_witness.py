"""Witness for fx-taskq-py/B-004.

Overlaid at run time, never part of `tree/`.

The README states the rule this pins: "A payload larger than
`max_payload_bytes` is rejected with 413." A payload of exactly that size is
not larger than it. The fixture's own suite only ever sends one comfortably
over the limit, so the boundary itself is unchecked.
"""

from __future__ import annotations

import json

import pytest
from taskq.api import Request
from taskq.errors import PayloadTooLarge

LIMIT = 1024


def body_of(size: int) -> bytes:
    raw = json.dumps({"payload": {"pad": "x" * size}}).encode("utf-8")
    return raw[:size] if len(raw) >= size else raw + b" " * (size - len(raw))


def request_with(body: bytes) -> Request:
    return Request(method="POST", path="/queues/emails/tasks", body=body, headers={})


def test_a_payload_exactly_at_the_limit_is_accepted() -> None:
    # Exactly at the limit is not "larger than" the limit.
    request = request_with(b'{"payload":{}}'.ljust(LIMIT, b" "))
    assert len(request.body) == LIMIT
    request.json(LIMIT)


def test_a_payload_one_byte_over_the_limit_is_rejected() -> None:
    request = request_with(b'{"payload":{}}'.ljust(LIMIT + 1, b" "))
    with pytest.raises(PayloadTooLarge):
        request.json(LIMIT)


def test_a_small_payload_is_still_accepted() -> None:
    assert request_with(b'{"payload":{"n":1}}').json(LIMIT) == {"payload": {"n": 1}}
