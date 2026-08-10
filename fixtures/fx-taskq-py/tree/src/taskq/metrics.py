"""Queue observability.

The numbers an operator actually needs to answer "is this queue healthy", which
is not the same as the counts by state. A queue with ten pending tasks is fine
if they arrived a second ago and alarming if the oldest has been waiting an
hour, and only the second number distinguishes them.

Ages are computed against a clock passed in rather than read here, so a snapshot
is reproducible and the tests can place a task at an exact age instead of
sleeping.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from taskq.errors import ValidationError
from taskq.models import TaskState
from taskq.storage import Storage


@dataclass(frozen=True)
class QueueMetrics:
    """A point-in-time view of one queue."""

    queue: str
    pending: int
    leased: int
    done: int
    dead: int
    oldest_pending_age: float | None
    oldest_pending_id: str | None
    ready_now: int
    scheduled_later: int

    @property
    def backlog(self) -> int:
        """Work not yet finished. Leased tasks count: they can still come back."""
        return self.pending + self.leased

    def as_dict(self) -> dict[str, Any]:
        return {
            "queue": self.queue,
            "pending": self.pending,
            "leased": self.leased,
            "done": self.done,
            "dead": self.dead,
            "backlog": self.backlog,
            "oldest_pending_age": self.oldest_pending_age,
            "oldest_pending_id": self.oldest_pending_id,
            "ready_now": self.ready_now,
            "scheduled_later": self.scheduled_later,
        }


class Metrics:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def queue_names(self) -> list[str]:
        rows = self.storage.connection.execute(
            "SELECT DISTINCT queue FROM tasks ORDER BY queue"
        ).fetchall()
        return [row["queue"] for row in rows]

    def for_queue(self, queue: str, at: float) -> QueueMetrics:
        """Metrics for one queue at time `at`.

        A queue with no tasks is reported as empty rather than missing: asking
        about a queue that has not been used yet is a normal thing to do, and an
        error there would push the caller into treating absence as a failure.
        """
        if not isinstance(queue, str) or not queue:
            raise ValidationError("queue must be a non-empty string")

        connection = self.storage.connection
        counts = {state.value: 0 for state in TaskState}
        for row in connection.execute(
            "SELECT state, COUNT(*) AS n FROM tasks WHERE queue = ? GROUP BY state", (queue,)
        ).fetchall():
            counts[row["state"]] = int(row["n"])

        oldest = connection.execute(
            "SELECT id, created_at FROM tasks WHERE queue = ? AND state = ? "
            "ORDER BY created_at ASC, id ASC LIMIT 1",
            (queue, TaskState.PENDING.value),
        ).fetchone()

        ready = connection.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE queue = ? AND state = ? AND available_at <= ?",
            (queue, TaskState.PENDING.value, at),
        ).fetchone()["n"]

        return QueueMetrics(
            queue=queue,
            pending=counts[TaskState.PENDING.value],
            leased=counts[TaskState.LEASED.value],
            done=counts[TaskState.DONE.value],
            dead=counts[TaskState.DEAD.value],
            oldest_pending_age=None if oldest is None else at - float(oldest["created_at"]),
            oldest_pending_id=None if oldest is None else oldest["id"],
            ready_now=int(ready),
            scheduled_later=counts[TaskState.PENDING.value] - int(ready),
        )

    def snapshot(self, at: float) -> dict[str, Any]:
        """Every queue that has ever held a task, plus totals."""
        per_queue = [self.for_queue(name, at) for name in self.queue_names()]
        return {
            "at": at,
            "queues": [metrics.as_dict() for metrics in per_queue],
            "totals": {
                "pending": sum(m.pending for m in per_queue),
                "leased": sum(m.leased for m in per_queue),
                "done": sum(m.done for m in per_queue),
                "dead": sum(m.dead for m in per_queue),
                "backlog": sum(m.backlog for m in per_queue),
            },
        }
