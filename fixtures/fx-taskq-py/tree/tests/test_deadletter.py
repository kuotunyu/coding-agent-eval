"""Dead-letter inspection and requeue."""

from __future__ import annotations

import pytest

from taskq.deadletter import DeadLetterQueue
from taskq.errors import Conflict, NotFound, ValidationError
from taskq.models import TaskState
from taskq.queue import TaskQueue
from taskq.storage import Storage

from .conftest import FakeClock


@pytest.fixture
def dead_letters(storage: Storage) -> DeadLetterQueue:
    return DeadLetterQueue(storage)


def kill(queue: TaskQueue, clock: FakeClock, name: str = "emails") -> str:
    """Drive one task all the way to dead and return its id."""
    task = queue.enqueue(name, {}, max_attempts=1)
    leased = queue.lease(name)
    assert leased is not None
    queue.fail(task.id, leased.lease_generation, "boom")
    assert queue.get(task.id).state is TaskState.DEAD
    return task.id


def test_an_empty_dead_letter_queue_lists_nothing(dead_letters: DeadLetterQueue) -> None:
    page = dead_letters.list()
    assert page.tasks == []
    assert page.total == 0


def test_a_dead_task_is_listed(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    task_id = kill(queue, clock)
    page = dead_letters.list()
    assert [t.id for t in page.tasks] == [task_id]
    assert page.total == 1


def test_listing_can_be_scoped_to_a_queue(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    kill(queue, clock, "emails")
    kill(queue, clock, "reports")
    assert dead_letters.list("emails").total == 1
    assert dead_letters.list().total == 2


def test_live_tasks_are_not_listed(
    queue: TaskQueue, dead_letters: DeadLetterQueue
) -> None:
    queue.enqueue("emails", {})
    assert dead_letters.list().total == 0


def test_paging_reports_the_full_total(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    for _ in range(3):
        kill(queue, clock)
        clock.advance(1)

    page = dead_letters.list(limit=2)
    assert len(page.tasks) == 2
    assert page.total == 3


@pytest.mark.parametrize(("limit", "offset"), [(0, 0), (501, 0), (10, -1)])
def test_illegal_paging_is_rejected(
    dead_letters: DeadLetterQueue, limit: int, offset: int
) -> None:
    with pytest.raises(ValidationError):
        dead_letters.list(limit=limit, offset=offset)


# ---------------------------------------------------------------- requeue


def test_requeue_returns_a_task_to_its_queue(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    task_id = kill(queue, clock)
    requeued = dead_letters.requeue(task_id, clock.value)
    assert requeued.state is TaskState.PENDING


def test_requeue_resets_the_attempt_counter(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    """Without this the task would be leased once and immediately die again."""
    task_id = kill(queue, clock)
    requeued = dead_letters.requeue(task_id, clock.value)
    assert requeued.attempts == 0
    assert requeued.attempts_remaining == requeued.max_attempts


def test_a_requeued_task_can_actually_run_again(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    task_id = kill(queue, clock)
    dead_letters.requeue(task_id, clock.value)

    leased = queue.lease("emails")
    assert leased is not None
    assert leased.id == task_id
    assert (
        queue.acknowledge(task_id, leased.lease_generation).state is TaskState.DONE
    )


def test_requeue_keeps_the_recorded_error(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    """The reason it died is usually more useful than the task itself."""
    task_id = kill(queue, clock)
    assert dead_letters.requeue(task_id, clock.value).last_error == "boom"


def test_requeuing_a_live_task_is_a_conflict(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    task = queue.enqueue("emails", {})
    with pytest.raises(Conflict):
        dead_letters.requeue(task.id, clock.value)


def test_requeuing_an_unknown_task_raises(
    dead_letters: DeadLetterQueue, clock: FakeClock
) -> None:
    with pytest.raises(NotFound):
        dead_letters.requeue("0" * 32, clock.value)


def test_requeue_queue_moves_every_dead_task(
    queue: TaskQueue, clock: FakeClock, dead_letters: DeadLetterQueue
) -> None:
    for _ in range(3):
        kill(queue, clock)
        clock.advance(1)
    kill(queue, clock, "reports")

    assert dead_letters.requeue_queue("emails", clock.value) == 3
    assert dead_letters.list("emails").total == 0
    assert dead_letters.list("reports").total == 1
