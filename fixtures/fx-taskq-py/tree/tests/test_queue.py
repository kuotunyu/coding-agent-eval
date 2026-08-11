"""Queue policy: leasing, ordering, retries, and the attempt limit.

Each README guarantee that can be tested here has a test named after it.
"""

from __future__ import annotations

import pytest

from taskq.deadletter import DeadLetterQueue
from taskq.errors import Conflict, NotFound, PayloadTooLarge, ValidationError
from taskq.models import TaskState
from taskq.queue import TaskQueue

from .conftest import FakeClock


# ------------------------------------------------------------------ enqueue


def test_enqueue_returns_a_pending_task(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {"to": "someone"})
    assert task.state is TaskState.PENDING
    assert task.attempts == 0
    assert len(task.id) == 32


def test_enqueue_stores_the_payload_verbatim(queue: TaskQueue) -> None:
    payload = {"b": 2, "a": [1, {"nested": True}]}
    task = queue.enqueue("emails", payload)
    assert queue.get(task.id).payload == payload


def test_enqueue_rejects_a_non_object_payload(queue: TaskQueue) -> None:
    with pytest.raises(ValidationError):
        queue.enqueue("emails", ["not", "an", "object"])


def test_enqueue_rejects_an_illegal_queue_name(queue: TaskQueue) -> None:
    for name in ("Emails", "with space", "../escape", "", "x" * 65):
        with pytest.raises(ValidationError):
            queue.enqueue(name, {})


def test_enqueue_rejects_a_queue_name_with_a_trailing_newline(queue: TaskQueue) -> None:
    r"""`$` also matches just before a final newline, so `match` is not enough.

    Accepting "emails\n" would create a queue distinct from "emails" that no
    worker polling "emails" ever reads, so the enqueue succeeds and the work is
    then silently orphaned.
    """
    for name in ("emails\n", "emails\r\n", "emails\n\n"):
        with pytest.raises(ValidationError):
            queue.enqueue(name, {})


def test_enqueue_rejects_an_oversized_payload(queue: TaskQueue) -> None:
    with pytest.raises(PayloadTooLarge):
        queue.enqueue("emails", {"blob": "x" * 4096})


def test_enqueue_rejects_a_negative_delay(queue: TaskQueue) -> None:
    with pytest.raises(ValidationError):
        queue.enqueue("emails", {}, delay_seconds=-1)


@pytest.mark.parametrize("delay", ["abc", "10", [1], {}, True])
def test_enqueue_rejects_a_delay_that_is_not_a_number(queue: TaskQueue, delay: object) -> None:
    """A delay of the wrong type is a client mistake, not a coercion problem.

    Coercing with `float()` raises `ValueError` on a string and `TypeError` on a
    container. Neither is a `TaskqError`, so both escaped the API's error
    handling and never became the documented 400. `"10"` is rejected too, for
    the same reason `priority` rejects a string: the API takes JSON, and a
    number arrives as a number.
    """
    with pytest.raises(ValidationError):
        queue.enqueue("emails", {}, delay_seconds=delay)


@pytest.mark.parametrize("delay", [float("nan"), float("inf"), float("-inf")])
def test_enqueue_rejects_a_non_finite_delay(queue: TaskQueue, delay: float) -> None:
    """The case a range check alone does not cover.

    Every comparison against `nan` is false, `-1 < 0` included, so a bare lower
    bound let it through to storage — where `available_at` is NOT NULL and the
    insert failed partway through the request.
    """
    with pytest.raises(ValidationError):
        queue.enqueue("emails", {}, delay_seconds=delay)


def test_an_absent_delay_means_no_delay(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {})
    assert task.available_at == task.created_at


def test_a_delayed_task_is_not_immediately_leasable(
    queue: TaskQueue, clock: FakeClock
) -> None:
    queue.enqueue("emails", {}, delay_seconds=10)
    assert queue.lease("emails") is None
    clock.advance(10)
    assert queue.lease("emails") is not None


# -------------------------------------------------------------------- lease


def test_lease_returns_none_on_an_empty_queue(queue: TaskQueue) -> None:
    assert queue.lease("emails") is None


def test_lease_marks_the_task_leased_and_counts_the_attempt(queue: TaskQueue) -> None:
    queue.enqueue("emails", {})
    leased = queue.lease("emails")
    assert leased is not None
    assert leased.state is TaskState.LEASED
    assert leased.attempts == 1


def test_a_leased_task_is_not_leased_again(queue: TaskQueue) -> None:
    """At most one worker holds a task at a time."""
    queue.enqueue("emails", {})
    assert queue.lease("emails") is not None
    assert queue.lease("emails") is None


def test_an_expired_lease_becomes_available_again(
    queue: TaskQueue, clock: FakeClock
) -> None:
    """At-least-once delivery: a worker that vanishes does not lose the task."""
    queue.enqueue("emails", {})
    first = queue.lease("emails")
    assert first is not None

    clock.advance(29)
    assert queue.lease("emails") is None

    clock.advance(1)
    second = queue.lease("emails")
    assert second is not None
    assert second.id == first.id
    assert second.attempts == 2


def test_leases_are_scoped_to_their_queue(queue: TaskQueue) -> None:
    queue.enqueue("emails", {})
    assert queue.lease("reports") is None
    assert queue.lease("emails") is not None


# ----------------------------------------------------------------- ordering


def test_higher_priority_runs_first(queue: TaskQueue) -> None:
    low = queue.enqueue("emails", {"n": 1}, priority=0)
    high = queue.enqueue("emails", {"n": 2}, priority=10)
    leased = queue.lease("emails")
    assert leased is not None
    assert leased.id == high.id
    assert leased.id != low.id


def test_equal_priority_runs_oldest_first(queue: TaskQueue, clock: FakeClock) -> None:
    first = queue.enqueue("emails", {"n": 1})
    clock.advance(1)
    queue.enqueue("emails", {"n": 2})
    leased = queue.lease("emails")
    assert leased is not None
    assert leased.id == first.id


# ------------------------------------------------------------------ ack/fail


def test_acknowledge_completes_a_leased_task(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {})
    leased = queue.lease("emails")
    assert leased is not None
    assert (
        queue.acknowledge(task.id, leased.lease_generation).state is TaskState.DONE
    )


def test_an_expired_lease_cannot_acknowledge_before_release(
    queue: TaskQueue, clock: FakeClock
) -> None:
    task = queue.enqueue("emails", {})
    leased = queue.lease("emails")
    assert leased is not None
    clock.advance(30)

    with pytest.raises(Conflict):
        queue.acknowledge(task.id, leased.lease_generation)
    current = queue.get(task.id)
    assert current.state is TaskState.LEASED
    assert current.lease_generation == leased.lease_generation


def test_an_old_generation_cannot_acknowledge_a_new_lease(
    queue: TaskQueue, clock: FakeClock
) -> None:
    task = queue.enqueue("emails", {})
    first = queue.lease("emails")
    assert first is not None
    clock.advance(30)
    second = queue.lease("emails")
    assert second is not None

    with pytest.raises(Conflict):
        queue.acknowledge(task.id, first.lease_generation)
    current = queue.get(task.id)
    assert current.state is TaskState.LEASED
    assert current.lease_generation == second.lease_generation


def test_an_old_generation_cannot_fail_a_new_lease(
    queue: TaskQueue, clock: FakeClock
) -> None:
    task = queue.enqueue("emails", {})
    first = queue.lease("emails")
    assert first is not None
    clock.advance(30)
    second = queue.lease("emails")
    assert second is not None

    with pytest.raises(Conflict):
        queue.fail(task.id, first.lease_generation, "stale")
    current = queue.get(task.id)
    assert current.state is TaskState.LEASED
    assert current.lease_generation == second.lease_generation
    assert current.last_error is None


def test_dead_letter_requeue_does_not_reuse_a_lease_generation(
    queue: TaskQueue, clock: FakeClock
) -> None:
    task = queue.enqueue("emails", {}, max_attempts=1)
    first = queue.lease("emails")
    assert first is not None
    queue.fail(task.id, first.lease_generation, "boom")
    DeadLetterQueue(queue.storage).requeue(task.id, clock.value)
    second = queue.lease("emails")
    assert second is not None

    assert second.lease_generation > first.lease_generation
    with pytest.raises(Conflict):
        queue.acknowledge(task.id, first.lease_generation)


def test_acknowledging_a_pending_task_is_a_conflict(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {})
    with pytest.raises(Conflict):
        queue.acknowledge(task.id, 1)


def test_acknowledging_twice_is_a_conflict(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {})
    leased = queue.lease("emails")
    assert leased is not None
    queue.acknowledge(task.id, leased.lease_generation)
    with pytest.raises(Conflict):
        queue.acknowledge(task.id, leased.lease_generation)


def test_failing_an_unleased_task_is_a_conflict(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {})
    with pytest.raises(Conflict):
        queue.fail(task.id, 1, "nope")


def test_fail_schedules_a_retry_while_attempts_remain(
    queue: TaskQueue, clock: FakeClock
) -> None:
    task = queue.enqueue("emails", {})
    leased = queue.lease("emails")
    assert leased is not None
    failed = queue.fail(task.id, leased.lease_generation, "boom")

    assert failed.state is TaskState.PENDING
    assert failed.last_error == "boom"
    assert failed.available_at == pytest.approx(clock.value + 2.0)


def test_retry_delay_doubles(queue: TaskQueue, clock: FakeClock) -> None:
    task = queue.enqueue("emails", {})

    first_lease = queue.lease("emails")
    assert first_lease is not None
    first = queue.fail(task.id, first_lease.lease_generation, "boom")
    assert first.available_at == pytest.approx(clock.value + 2.0)

    clock.advance(2)
    second_lease = queue.lease("emails")
    assert second_lease is not None
    second = queue.fail(task.id, second_lease.lease_generation, "boom")
    assert second.available_at == pytest.approx(clock.value + 4.0)


def test_retry_delay_is_capped(queue: TaskQueue, clock: FakeClock) -> None:
    from taskq.util import retry_delay

    assert retry_delay(20, 2.0, 60.0) == 60.0


# -------------------------------------------------- the attempt limit


def test_max_attempts_counts_attempts_not_retries(
    queue: TaskQueue, clock: FakeClock
) -> None:
    """A task with max_attempts=3 runs exactly three times, then dies."""
    task = queue.enqueue("emails", {}, max_attempts=3)

    executions = 0
    for _ in range(10):
        leased = queue.lease("emails")
        if leased is None:
            clock.advance(1000)
            leased = queue.lease("emails")
            if leased is None:
                break
        executions += 1
        queue.fail(task.id, leased.lease_generation, "boom")

    assert executions == 3
    assert queue.get(task.id).state is TaskState.DEAD


def test_a_dead_task_is_never_leased_again(queue: TaskQueue, clock: FakeClock) -> None:
    task = queue.enqueue("emails", {}, max_attempts=1)
    leased = queue.lease("emails")
    assert leased is not None
    queue.fail(task.id, leased.lease_generation, "boom")
    assert queue.get(task.id).state is TaskState.DEAD

    clock.advance(10_000)
    assert queue.lease("emails") is None


def test_attempts_remaining_reaches_zero_and_stops(queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {}, max_attempts=2)
    assert queue.get(task.id).attempts_remaining == 2
    leased = queue.lease("emails")
    assert leased is not None
    assert queue.get(task.id).attempts_remaining == 1
    queue.fail(task.id, leased.lease_generation, "boom")
    assert queue.get(task.id).attempts_remaining == 1


def test_max_attempts_must_be_at_least_one(queue: TaskQueue) -> None:
    with pytest.raises(ValidationError):
        queue.enqueue("emails", {}, max_attempts=0)


# ------------------------------------------------------------------ lookups


def test_get_on_an_unknown_id_raises(queue: TaskQueue) -> None:
    with pytest.raises(NotFound):
        queue.get("0" * 32)


def test_stats_counts_every_state(queue: TaskQueue) -> None:
    queue.enqueue("emails", {})
    done = queue.enqueue("emails", {})
    queue.lease("emails")
    queue.lease("emails")
    leased_done = queue.get(done.id)
    queue.acknowledge(done.id, leased_done.lease_generation)

    counts = queue.stats()
    assert counts["done"] == 1
    assert counts["leased"] == 1
    assert set(counts) == {"pending", "leased", "done", "dead"}


def test_purge_removes_only_terminal_tasks(queue: TaskQueue) -> None:
    keep = queue.enqueue("emails", {})
    done = queue.enqueue("emails", {})
    queue.lease("emails")
    queue.lease("emails")
    leased_done = queue.get(done.id)
    queue.acknowledge(done.id, leased_done.lease_generation)

    assert queue.purge() == 1
    assert queue.get(keep.id) is not None
