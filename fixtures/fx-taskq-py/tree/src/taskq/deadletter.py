"""Dead-letter inspection and requeue.

A task that exhausted its attempts is kept rather than deleted, because the
reason it failed is usually more valuable than the task itself.

Requeuing resets the attempt counter. A task returned to the queue with its
attempts already spent would be leased once and immediately die again, which
looks like the requeue silently failed. Resetting is the only behaviour that
matches what "requeue" means to the person asking for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from taskq.errors import Conflict, ValidationError
from taskq.models import Task, TaskState
from taskq.storage import Storage


@dataclass(frozen=True)
class DeadLetterPage:
    tasks: list[Task]
    total: int

    def as_dict(self) -> dict[str, Any]:
        return {"tasks": [task.as_dict() for task in self.tasks], "total": self.total}


class DeadLetterQueue:
    def __init__(self, storage: Storage) -> None:
        self.storage = storage

    def list(self, queue: str | None = None, *, limit: int = 50, offset: int = 0) -> DeadLetterPage:
        """Page through dead tasks, newest first."""
        if limit < 1 or limit > 500:
            raise ValidationError("limit must be between 1 and 500")
        if offset < 0:
            raise ValidationError("offset must not be negative")

        connection = self.storage.connection
        if queue is None:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE state = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (TaskState.DEAD.value, limit, offset),
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE state = ?", (TaskState.DEAD.value,)
            ).fetchone()["n"]
        else:
            rows = connection.execute(
                "SELECT * FROM tasks WHERE state = ? AND queue = ? "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (TaskState.DEAD.value, queue, limit, offset),
            ).fetchall()
            total = connection.execute(
                "SELECT COUNT(*) AS n FROM tasks WHERE state = ? AND queue = ?",
                (TaskState.DEAD.value, queue),
            ).fetchone()["n"]

        from taskq.storage import row_to_task

        return DeadLetterPage(tasks=[row_to_task(row) for row in rows], total=int(total))

    def requeue(self, task_id: str, at: float) -> Task:
        """Return a dead task to its queue with a fresh attempt budget."""
        task = self.storage.get(task_id)
        if task is None:
            from taskq.errors import NotFound

            raise NotFound(f"no task with id {task_id}")
        if task.state is not TaskState.DEAD:
            raise Conflict(f"task in state {task.state.value} is not dead-lettered")

        self.storage.reset_attempts(task_id)
        self.storage.mark_state(
            task_id,
            TaskState.PENDING,
            available_at=at,
            leased_until=None,
            last_error=task.last_error,
        )
        requeued = self.storage.get(task_id)
        assert requeued is not None  # noqa: S101 - just written
        return requeued

    def requeue_queue(self, queue: str, at: float) -> int:
        """Requeue every dead task in one queue. Returns how many moved."""
        page = self.list(queue, limit=500)
        for task in page.tasks:
            self.requeue(task.id, at)
        return len(page.tasks)
