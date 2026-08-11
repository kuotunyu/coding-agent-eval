"""The worker loop."""

from __future__ import annotations

from taskq.models import Task, TaskState
from taskq.queue import TaskQueue
from taskq.worker import Worker

from .conftest import FakeClock


def collecting_handler(seen: list[Task]):
    def handler(task: Task) -> None:
        seen.append(task)

    return handler


def failing_handler(message: str = "handler exploded"):
    def handler(task: Task) -> None:
        raise ValueError(message)

    return handler


def test_run_once_returns_none_on_an_empty_queue(queue: TaskQueue) -> None:
    worker = Worker(queue, collecting_handler([]), sleep=lambda _: None)
    assert worker.run_once("emails") is None
    assert worker.stats.idle_polls == 1


def test_a_successful_handler_acknowledges_the_task(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {})
    seen: list[Task] = []
    worker = Worker(queue, collecting_handler(seen), sleep=lambda _: None)

    worker.run_once("emails")
    assert [t.id for t in seen] == [task.id]
    assert queue.get(task.id).state is TaskState.DONE
    assert worker.stats.acknowledged == 1


def test_a_worker_cannot_acknowledge_a_replacement_lease(
    queue: TaskQueue, clock: FakeClock
) -> None:
    task = queue.enqueue("emails", {})

    def replace_lease(_: Task) -> None:
        clock.advance(30)
        assert queue.lease("emails") is not None

    worker = Worker(queue, replace_lease, sleep=lambda _: None)
    worker.run_once("emails")

    current = queue.get(task.id)
    assert current.state is TaskState.LEASED
    assert current.lease_generation == 2
    assert worker.stats.acknowledged == 0
    assert any("lease generation is no longer active" in error for error in worker.stats.errors)


def test_a_raising_handler_fails_the_task_rather_than_escaping(queue: TaskQueue) -> None:
    """An escaping exception would leave the task leased until the lease expired."""
    task = queue.enqueue("emails", {})
    worker = Worker(queue, failing_handler(), sleep=lambda _: None)

    worker.run_once("emails")
    updated = queue.get(task.id)
    assert updated.state is TaskState.PENDING
    assert updated.last_error is not None
    assert "handler exploded" in updated.last_error
    assert worker.stats.failed == 1


def test_the_error_message_records_the_exception_type(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {})
    Worker(queue, failing_handler(), sleep=lambda _: None).run_once("emails")
    last_error = queue.get(task.id).last_error
    assert last_error is not None
    assert last_error.startswith("ValueError:")


def test_repeated_failures_eventually_dead_letter(
    queue: TaskQueue, clock: FakeClock
) -> None:
    task = queue.enqueue("emails", {}, max_attempts=2)
    worker = Worker(queue, failing_handler(), sleep=lambda _: None)

    worker.run_once("emails")
    clock.advance(1000)
    worker.run_once("emails")

    assert queue.get(task.id).state is TaskState.DEAD
    assert worker.stats.failed == 2


def test_run_stops_at_the_iteration_budget(queue: TaskQueue) -> None:
    worker = Worker(queue, collecting_handler([]), sleep=lambda _: None)
    stats = worker.run("emails", max_iterations=3)
    assert stats.idle_polls == 3


def test_the_worker_sleeps_only_when_the_queue_was_empty(queue: TaskQueue) -> None:
    """A busy queue keeps moving without an artificial delay between tasks."""
    queue.enqueue("emails", {})
    slept: list[float] = []
    worker = Worker(queue, collecting_handler([]), sleep=slept.append)

    worker.run("emails", max_iterations=2)
    assert len(slept) == 1


def test_stats_are_reported_as_a_mapping(queue: TaskQueue) -> None:
    queue.enqueue("emails", {})
    worker = Worker(queue, collecting_handler([]), sleep=lambda _: None)
    worker.run_once("emails")
    assert worker.stats.as_dict() == {
        "leased": 1,
        "acknowledged": 1,
        "failed": 0,
        "idle_polls": 0,
    }
