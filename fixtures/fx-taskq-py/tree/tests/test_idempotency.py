"""Idempotency keys on enqueue."""

from __future__ import annotations

import pytest

from taskq.errors import ValidationError
from taskq.queue import TaskQueue

from .conftest import FakeClock


def test_the_same_key_returns_the_same_task(queue: TaskQueue) -> None:
    """A client retrying after a timeout must not duplicate the work."""
    first = queue.enqueue("emails", {"n": 1}, idempotency_key="order-42")
    second = queue.enqueue("emails", {"n": 2}, idempotency_key="order-42")
    assert first.id == second.id


def test_the_replayed_task_keeps_the_original_payload(queue: TaskQueue) -> None:
    """The first request won; the second is a replay, not an update."""
    queue.enqueue("emails", {"n": 1}, idempotency_key="order-42")
    second = queue.enqueue("emails", {"n": 2}, idempotency_key="order-42")
    assert second.payload == {"n": 1}


def test_different_keys_create_different_tasks(queue: TaskQueue) -> None:
    first = queue.enqueue("emails", {}, idempotency_key="order-1")
    second = queue.enqueue("emails", {}, idempotency_key="order-2")
    assert first.id != second.id


def test_keys_are_scoped_to_their_queue(queue: TaskQueue) -> None:
    """The same key in two queues is two pieces of work, not one."""
    first = queue.enqueue("emails", {}, idempotency_key="order-42")
    second = queue.enqueue("reports", {}, idempotency_key="order-42")
    assert first.id != second.id


def test_no_key_means_no_deduplication(queue: TaskQueue) -> None:
    first = queue.enqueue("emails", {})
    second = queue.enqueue("emails", {})
    assert first.id != second.id


def test_a_key_expires(queue: TaskQueue, clock: FakeClock) -> None:
    """Holding keys forever would make a natural key unusable a second time."""
    first = queue.enqueue("emails", {}, idempotency_key="order-42")
    clock.advance(24 * 60 * 60 + 1)
    second = queue.enqueue("emails", {}, idempotency_key="order-42")
    assert first.id != second.id


def test_a_key_is_still_live_just_before_it_expires(
    queue: TaskQueue, clock: FakeClock
) -> None:
    first = queue.enqueue("emails", {}, idempotency_key="order-42")
    clock.advance(24 * 60 * 60 - 1)
    assert queue.enqueue("emails", {}, idempotency_key="order-42").id == first.id


@pytest.mark.parametrize("key", ["", "-leading-dash", "with space", "x" * 129, 42, None_ := object()])
def test_a_malformed_key_is_rejected(queue: TaskQueue, key: object) -> None:
    with pytest.raises(ValidationError):
        queue.enqueue("emails", {}, idempotency_key=key)


def test_validation_runs_before_the_key_is_consulted(queue: TaskQueue) -> None:
    """A bad request must be rejected, not answered from a previous good one."""
    queue.enqueue("emails", {"n": 1}, idempotency_key="order-42")
    with pytest.raises(ValidationError):
        queue.enqueue("emails", ["not-an-object"], idempotency_key="order-42")


def test_purging_expired_keys_leaves_live_ones(queue: TaskQueue, clock: FakeClock) -> None:
    queue.enqueue("emails", {}, idempotency_key="old")
    clock.advance(24 * 60 * 60 + 1)
    queue.enqueue("emails", {}, idempotency_key="new")

    assert queue.idempotency.purge_expired(clock.value) == 1
    assert queue.idempotency.count() == 1


def test_a_replayed_enqueue_does_not_create_a_second_row(queue: TaskQueue) -> None:
    queue.enqueue("emails", {}, idempotency_key="order-42")
    queue.enqueue("emails", {}, idempotency_key="order-42")
    assert queue.stats()["pending"] == 1
