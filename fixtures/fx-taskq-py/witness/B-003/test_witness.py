"""Witness for fx-taskq-py/B-003.

Overlaid at run time, never part of `tree/`.

The fixture's own suite pages with a `limit` but never with an `offset`
(`test_paging_reports_the_full_total`), so nothing checks that the second page
differs from the first.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from taskq.config import Config
from taskq.deadletter import DeadLetterQueue
from taskq.queue import TaskQueue
from taskq.storage import Storage


class Clock:
    def __init__(self) -> None:
        self.value = 1_000_000.0

    def __call__(self) -> float:
        return self.value


@pytest.fixture
def parts(tmp_path: Path):
    config = Config(
        database=str(tmp_path / "witness.db"),
        admin_token="s3cret-admin-token",
        max_attempts=1,
    )
    storage = Storage(config.database)
    clock = Clock()
    try:
        yield TaskQueue(storage, config, clock=clock), DeadLetterQueue(storage), clock
    finally:
        storage.close()


def kill(queue: TaskQueue, clock: Clock) -> str:
    task = queue.enqueue("emails", {}, max_attempts=1)
    queue.lease("emails")
    queue.fail(task.id, "boom")
    clock.value += 1
    return task.id


def test_an_offset_returns_a_different_page(parts) -> None:
    queue, dead_letters, clock = parts
    for _ in range(3):
        kill(queue, clock)

    first = [t.id for t in dead_letters.list(limit=2, offset=0).tasks]
    second = [t.id for t in dead_letters.list(limit=2, offset=2).tasks]

    assert len(first) == 2
    assert len(second) == 1
    assert not set(first) & set(second), "paging past the first page repeated it"


def test_paging_reaches_every_dead_task(parts) -> None:
    queue, dead_letters, clock = parts
    expected = {kill(queue, clock) for _ in range(5)}

    seen: set[str] = set()
    for offset in range(0, 6, 2):
        seen.update(t.id for t in dead_letters.list(limit=2, offset=offset).tasks)

    assert seen == expected
