"""Idempotency keys on enqueue."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from taskq.config import Config
from taskq.errors import ValidationError
from taskq.queue import TaskQueue
from taskq.storage import Storage

from .conftest import FakeClock


def test_the_same_key_returns_the_same_task(queue: TaskQueue) -> None:
    """A client retrying after a timeout must not duplicate the work."""
    first = queue.enqueue("emails", {"n": 1}, idempotency_key="order-42")
    second = queue.enqueue("emails", {"n": 2}, idempotency_key="order-42")
    assert first.id == second.id


def test_concurrent_enqueues_with_the_same_key_create_one_task(
    config: Config, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first key binding wins even while another connection is enqueueing."""
    first_storage = Storage(config.database)
    second_storage = Storage(config.database)
    first_queue = TaskQueue(first_storage, config, clock=clock)
    second_queue = TaskQueue(second_storage, config, clock=clock)
    first_at_remember = threading.Event()
    release_first = threading.Event()
    second_looked_up = threading.Event()
    original_remember = first_queue.idempotency.remember
    original_lookup = second_queue.idempotency.lookup

    def paused_remember(
        queue_name: str, key: str, task_id: str, at: float, ttl: float
    ) -> object:
        first_at_remember.set()
        assert release_first.wait(timeout=5)
        return original_remember(queue_name, key, task_id, at, ttl)

    def observed_lookup(queue_name: str, key: str, at: float) -> object:
        result = original_lookup(queue_name, key, at)
        second_looked_up.set()
        return result

    monkeypatch.setattr(first_queue.idempotency, "remember", paused_remember)
    monkeypatch.setattr(second_queue.idempotency, "lookup", observed_lookup)

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            first_future = executor.submit(
                first_queue.enqueue,
                "emails",
                {"worker": 1},
                idempotency_key="same-key",
            )
            assert first_at_remember.wait(timeout=5)
            second_future = executor.submit(
                second_queue.enqueue,
                "emails",
                {"worker": 2},
                idempotency_key="same-key",
            )
            second_looked_up.wait(timeout=2)
            release_first.set()
            first = first_future.result(timeout=5)
            second = second_future.result(timeout=5)

        assert first.id == second.id
        assert first_storage.counts_by_state()["pending"] == 1
    finally:
        release_first.set()
        second_storage.close()
        first_storage.close()


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
