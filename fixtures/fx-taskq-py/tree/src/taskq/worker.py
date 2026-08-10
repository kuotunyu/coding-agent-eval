"""The worker loop.

A worker leases a task, runs a handler, and reports the outcome. It reports
failure for any exception the handler raises rather than letting it escape,
because an escaping exception would leave the task leased until the lease
expired — turning a fast failure into a slow one.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from taskq.models import Task
from taskq.queue import TaskQueue

TaskHandler = Callable[[Task], None]


@dataclass
class WorkerStats:
    leased: int = 0
    acknowledged: int = 0
    failed: int = 0
    idle_polls: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "leased": self.leased,
            "acknowledged": self.acknowledged,
            "failed": self.failed,
            "idle_polls": self.idle_polls,
        }


class Worker:
    def __init__(
        self,
        queue: TaskQueue,
        handler: TaskHandler,
        *,
        poll_interval: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.queue = queue
        self.handler = handler
        self.poll_interval = poll_interval
        self.stats = WorkerStats()
        self._sleep = sleep

    def run_once(self, queue_name: str) -> Task | None:
        """Lease one task and run it. Returns the task, or None if the queue was empty."""
        task = self.queue.lease(queue_name)
        if task is None:
            self.stats.idle_polls += 1
            return None

        self.stats.leased += 1
        try:
            self.handler(task)
        except Exception as exc:  # noqa: BLE001 - the whole point is not to escape
            self.stats.failed += 1
            self.stats.errors.append(f"{type(exc).__name__}: {exc}")
            self.queue.fail(task.id, f"{type(exc).__name__}: {exc}")
            return task

        self.stats.acknowledged += 1
        self.queue.acknowledge(task.id)
        return task

    def run(self, queue_name: str, *, max_iterations: int | None = None) -> WorkerStats:
        """Poll until the iteration budget runs out.

        Sleeping only when the queue was empty keeps a busy queue moving without
        an artificial delay between tasks.
        """
        iterations = 0
        while max_iterations is None or iterations < max_iterations:
            iterations += 1
            if self.run_once(queue_name) is None:
                self._sleep(self.poll_interval)
        return self.stats
