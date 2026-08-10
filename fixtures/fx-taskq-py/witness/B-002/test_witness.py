"""Witness for fx-taskq-py/B-002.

Overlaid at run time, never part of `tree/`.

The fixture's own suite checks that per-queue *counts* are separate
(`test_queues_are_reported_separately`) but never checks which task the
"oldest pending" fields describe. That is the gap this defect lives in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from taskq.config import Config
from taskq.metrics import Metrics
from taskq.queue import TaskQueue
from taskq.storage import Storage

AT = 1_000_000.0


@pytest.fixture
def parts(tmp_path: Path):
    config = Config(database=str(tmp_path / "witness.db"), admin_token="s3cret-admin-token")
    storage = Storage(config.database)
    try:
        yield TaskQueue(storage, config, clock=lambda: AT), Metrics(storage)
    finally:
        storage.close()


def test_the_oldest_pending_task_belongs_to_the_queue_asked_about(parts) -> None:
    queue, metrics = parts
    # Give the rows different times. With the fixture's default fixed clock the
    # SQL tie-breaks on random UUIDs, making this witness pass or fail by chance.
    clock = iter((AT - 10, AT))
    queue = TaskQueue(queue.storage, queue.config, clock=clock.__next__)
    older = queue.enqueue("reports", {"n": 1})
    queue.enqueue("emails", {"n": 2})

    snapshot = metrics.for_queue("emails", AT)
    assert snapshot.oldest_pending_id != older.id, (
        "metrics for 'emails' named a task belonging to 'reports'"
    )


def test_a_queue_with_nothing_pending_reports_no_oldest(parts) -> None:
    queue, metrics = parts
    queue.enqueue("reports", {"n": 1})

    snapshot = metrics.for_queue("emails", AT)
    assert snapshot.pending == 0
    assert snapshot.oldest_pending_id is None
    assert snapshot.oldest_pending_age is None
