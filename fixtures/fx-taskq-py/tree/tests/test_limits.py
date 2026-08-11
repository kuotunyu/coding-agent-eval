"""Per-queue concurrency caps."""

from __future__ import annotations

import pytest

from taskq.errors import ValidationError
from taskq.queue import TaskQueue

from .conftest import FakeClock


def test_an_uncapped_queue_leases_freely(queue: TaskQueue) -> None:
    for _ in range(3):
        queue.enqueue("emails", {})
    assert all(queue.lease("emails") is not None for _ in range(3))


def test_a_cap_of_one_allows_a_single_lease(queue: TaskQueue) -> None:
    queue.limits.set_limit("emails", 1)
    queue.enqueue("emails", {})
    queue.enqueue("emails", {})

    assert queue.lease("emails") is not None
    assert queue.lease("emails") is None


def test_capacity_returns_when_a_task_completes(
    queue: TaskQueue, clock: FakeClock
) -> None:
    queue.limits.set_limit("emails", 1)
    first = queue.enqueue("emails", {})
    clock.advance(1)  # distinct created_at, so which one leases first is defined
    queue.enqueue("emails", {})

    leased = queue.lease("emails")
    assert leased is not None
    assert leased.id == first.id

    queue.acknowledge(first.id, leased.lease_generation)
    assert queue.lease("emails") is not None


def test_an_expired_lease_does_not_hold_a_slot(queue: TaskQueue, clock: FakeClock) -> None:
    """One crashed worker must not hold capacity until someone notices."""
    queue.limits.set_limit("emails", 1)
    queue.enqueue("emails", {})
    queue.enqueue("emails", {})

    queue.lease("emails")
    assert queue.lease("emails") is None

    clock.advance(31)
    assert queue.lease("emails") is not None


def test_a_cap_applies_only_to_its_own_queue(queue: TaskQueue) -> None:
    queue.limits.set_limit("emails", 1)
    queue.enqueue("emails", {})
    queue.enqueue("reports", {})

    queue.lease("emails")
    assert queue.lease("reports") is not None


def test_clearing_a_cap_restores_free_leasing(queue: TaskQueue) -> None:
    queue.limits.set_limit("emails", 1)
    queue.enqueue("emails", {})
    queue.enqueue("emails", {})
    queue.lease("emails")
    assert queue.lease("emails") is None

    assert queue.limits.clear_limit("emails") is True
    assert queue.lease("emails") is not None


def test_clearing_an_absent_cap_reports_nothing_changed(queue: TaskQueue) -> None:
    assert queue.limits.clear_limit("emails") is False


def test_a_cap_can_be_raised(queue: TaskQueue) -> None:
    queue.limits.set_limit("emails", 1)
    queue.limits.set_limit("emails", 2)
    for _ in range(3):
        queue.enqueue("emails", {})

    assert queue.lease("emails") is not None
    assert queue.lease("emails") is not None
    assert queue.lease("emails") is None


@pytest.mark.parametrize("value", [0, -1, 1.5, True, "2", None])
def test_an_illegal_cap_is_rejected(queue: TaskQueue, value: object) -> None:
    with pytest.raises(ValidationError):
        queue.limits.set_limit("emails", value)  # type: ignore[arg-type]


def test_a_cap_on_an_illegal_queue_name_is_rejected(queue: TaskQueue) -> None:
    with pytest.raises(ValidationError):
        queue.limits.set_limit("Not A Queue", 1)


def test_limits_are_listable(queue: TaskQueue) -> None:
    queue.limits.set_limit("emails", 2)
    queue.limits.set_limit("reports", 5)
    assert queue.limits.all_limits() == {"emails": 2, "reports": 5}
