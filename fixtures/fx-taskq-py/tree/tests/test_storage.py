"""Persistence: schema, durability, and the write transaction."""

from __future__ import annotations

import sqlite3

import pytest

from taskq.config import Config
from taskq.models import Task, TaskState
from taskq.storage import SCHEMA_VERSION, Storage


def make_task(task_id: str = "a" * 32, **overrides: object) -> Task:
    fields: dict[str, object] = {
        "id": task_id,
        "queue": "emails",
        "payload": {"k": "v"},
        "state": TaskState.PENDING,
        "priority": 0,
        "attempts": 0,
        "max_attempts": 3,
        "created_at": 1_000_000.0,
        "available_at": 1_000_000.0,
        "leased_until": None,
        "last_error": None,
    }
    fields.update(overrides)
    return Task(**fields)  # type: ignore[arg-type]


def test_the_schema_is_created_on_first_use(storage: Storage) -> None:
    assert storage.schema_version() == SCHEMA_VERSION


def test_reopening_does_not_reset_the_schema(config: Config, storage: Storage) -> None:
    storage.insert(make_task())
    storage.close()

    reopened = Storage(config.database)
    try:
        assert reopened.schema_version() == SCHEMA_VERSION
        assert reopened.get("a" * 32) is not None
    finally:
        reopened.close()


def test_an_enqueued_task_survives_a_restart(config: Config, storage: Storage) -> None:
    """Durability: an acknowledged enqueue is on disk."""
    storage.insert(make_task(payload={"durable": True}))
    storage.close()

    reopened = Storage(config.database)
    try:
        task = reopened.get("a" * 32)
        assert task is not None
        assert task.payload == {"durable": True}
    finally:
        reopened.close()


def test_a_duplicate_id_is_rejected(storage: Storage) -> None:
    storage.insert(make_task())
    with pytest.raises(sqlite3.IntegrityError):
        storage.insert(make_task())


def test_get_returns_none_for_an_unknown_id(storage: Storage) -> None:
    assert storage.get("f" * 32) is None


def test_payload_round_trips_through_json(storage: Storage) -> None:
    payload = {"nested": {"list": [1, 2, {"deep": None}]}, "unicode": "值"}
    storage.insert(make_task(payload=payload))
    stored = storage.get("a" * 32)
    assert stored is not None
    assert stored.payload == payload


def test_a_quote_in_a_queue_name_is_stored_not_interpreted(storage: Storage) -> None:
    """Parameterised statements: a value cannot become part of the query.

    Storage does not validate names — that is the queue's job — so this checks
    the statement layer directly.
    """
    hostile = "'; DROP TABLE tasks; --"
    storage.insert(make_task(queue=hostile))
    stored = storage.get("a" * 32)
    assert stored is not None
    assert stored.queue == hostile
    assert storage.counts_by_state()["pending"] == 1


def test_counts_include_every_state_even_when_empty(storage: Storage) -> None:
    assert storage.counts_by_state() == {"pending": 0, "leased": 0, "done": 0, "dead": 0}


def test_purge_removes_terminal_states_only(storage: Storage) -> None:
    storage.insert(make_task("a" * 32, state=TaskState.DONE))
    storage.insert(make_task("b" * 32, state=TaskState.DEAD))
    storage.insert(make_task("c" * 32, state=TaskState.PENDING))

    assert storage.purge_terminal() == 2
    assert storage.get("c" * 32) is not None


def test_the_write_transaction_rolls_back_on_error(storage: Storage) -> None:
    storage.insert(make_task())
    with pytest.raises(RuntimeError):
        with storage.write_transaction() as connection:
            connection.execute("DELETE FROM tasks")
            raise RuntimeError("abort")
    assert storage.get("a" * 32) is not None


def test_the_write_transaction_commits_on_success(storage: Storage) -> None:
    storage.insert(make_task())
    with storage.write_transaction() as connection:
        connection.execute("DELETE FROM tasks")
    assert storage.get("a" * 32) is None


def test_next_runnable_prefers_priority_then_age(storage: Storage) -> None:
    storage.insert(make_task("a" * 32, priority=0, created_at=1.0))
    storage.insert(make_task("b" * 32, priority=5, created_at=2.0))
    storage.insert(make_task("c" * 32, priority=5, created_at=1.5))

    with storage.write_transaction() as connection:
        candidate = storage.next_runnable(connection, "emails", 1_000_000.0)
    assert candidate is not None
    assert candidate.id == "c" * 32


def test_next_runnable_ignores_tasks_that_are_not_yet_available(storage: Storage) -> None:
    storage.insert(make_task(available_at=2_000_000.0))
    with storage.write_transaction() as connection:
        assert storage.next_runnable(connection, "emails", 1_000_000.0) is None
