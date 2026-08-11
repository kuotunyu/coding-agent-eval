"""Queue metrics."""

from __future__ import annotations

import pytest

from taskq.errors import ValidationError
from taskq.metrics import Metrics
from taskq.queue import TaskQueue
from taskq.storage import Storage

from .conftest import FakeClock


@pytest.fixture
def metrics(storage: Storage) -> Metrics:
    return Metrics(storage)


def test_an_unused_queue_reports_empty_rather_than_failing(
    metrics: Metrics, clock: FakeClock
) -> None:
    """Asking about a queue that has not been used yet is normal."""
    snapshot = metrics.for_queue("never-used", clock.value)
    assert snapshot.pending == 0
    assert snapshot.backlog == 0
    assert snapshot.oldest_pending_age is None


def test_counts_reflect_each_state(
    metrics: Metrics, queue: TaskQueue, clock: FakeClock
) -> None:
    queue.enqueue("emails", {})
    done = queue.enqueue("emails", {})
    queue.lease("emails")
    queue.lease("emails")
    leased_done = queue.get(done.id)
    queue.acknowledge(done.id, leased_done.lease_generation)

    snapshot = metrics.for_queue("emails", clock.value)
    assert snapshot.done == 1
    assert snapshot.leased == 1


def test_backlog_includes_leased_tasks(
    metrics: Metrics, queue: TaskQueue, clock: FakeClock
) -> None:
    """A leased task can still come back, so it is not finished work."""
    queue.enqueue("emails", {})
    queue.enqueue("emails", {})
    queue.lease("emails")

    assert metrics.for_queue("emails", clock.value).backlog == 2


def test_the_oldest_pending_age_is_measured_from_creation(
    metrics: Metrics, queue: TaskQueue, clock: FakeClock
) -> None:
    """Ten pending tasks are fine if new and alarming if old; this is the difference."""
    oldest = queue.enqueue("emails", {})
    clock.advance(60)
    queue.enqueue("emails", {})
    clock.advance(30)

    snapshot = metrics.for_queue("emails", clock.value)
    assert snapshot.oldest_pending_age == pytest.approx(90.0)
    assert snapshot.oldest_pending_id == oldest.id


def test_a_leased_task_is_not_the_oldest_pending(
    metrics: Metrics, queue: TaskQueue, clock: FakeClock
) -> None:
    queue.enqueue("emails", {})
    clock.advance(10)
    second = queue.enqueue("emails", {})
    queue.lease("emails")  # takes the older one

    snapshot = metrics.for_queue("emails", clock.value)
    assert snapshot.oldest_pending_id == second.id


def test_delayed_tasks_are_counted_as_scheduled_not_ready(
    metrics: Metrics, queue: TaskQueue, clock: FakeClock
) -> None:
    queue.enqueue("emails", {})
    queue.enqueue("emails", {}, delay_seconds=300)

    snapshot = metrics.for_queue("emails", clock.value)
    assert snapshot.ready_now == 1
    assert snapshot.scheduled_later == 1
    assert snapshot.pending == 2


def test_a_delayed_task_becomes_ready_when_its_time_arrives(
    metrics: Metrics, queue: TaskQueue, clock: FakeClock
) -> None:
    queue.enqueue("emails", {}, delay_seconds=300)
    assert metrics.for_queue("emails", clock.value).ready_now == 0

    clock.advance(300)
    assert metrics.for_queue("emails", clock.value).ready_now == 1


def test_queues_are_reported_separately(
    metrics: Metrics, queue: TaskQueue, clock: FakeClock
) -> None:
    queue.enqueue("emails", {})
    queue.enqueue("reports", {})
    queue.enqueue("reports", {})

    assert metrics.for_queue("emails", clock.value).pending == 1
    assert metrics.for_queue("reports", clock.value).pending == 2


def test_queue_names_lists_every_queue_seen(
    metrics: Metrics, queue: TaskQueue
) -> None:
    queue.enqueue("reports", {})
    queue.enqueue("emails", {})
    assert metrics.queue_names() == ["emails", "reports"]


def test_the_snapshot_totals_every_queue(
    metrics: Metrics, queue: TaskQueue, clock: FakeClock
) -> None:
    queue.enqueue("emails", {})
    queue.enqueue("reports", {})
    queue.lease("emails")

    snapshot = metrics.snapshot(clock.value)
    assert snapshot["totals"]["backlog"] == 2
    assert len(snapshot["queues"]) == 2
    assert snapshot["at"] == clock.value


def test_an_empty_queue_name_is_rejected(metrics: Metrics, clock: FakeClock) -> None:
    with pytest.raises(ValidationError):
        metrics.for_queue("", clock.value)


def test_the_snapshot_of_an_empty_database_is_empty(
    metrics: Metrics, clock: FakeClock
) -> None:
    snapshot = metrics.snapshot(clock.value)
    assert snapshot["queues"] == []
    assert snapshot["totals"]["backlog"] == 0
