"""Task states and the task record.

States are a closed set with explicit transitions, so an operation on a task in
the wrong state fails loudly instead of producing a task nobody can account for.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    #: Waiting to be leased. `available_at` decides when.
    PENDING = "pending"
    #: Leased to a worker. `leased_until` decides when the lease expires.
    LEASED = "leased"
    #: Finished successfully. Terminal.
    DONE = "done"
    #: Attempts exhausted. Terminal, and never leased again.
    DEAD = "dead"

    @property
    def terminal(self) -> bool:
        return self in (TaskState.DONE, TaskState.DEAD)


#: Transitions the queue permits. Anything else is a conflict.
ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.LEASED}),
    TaskState.LEASED: frozenset({TaskState.DONE, TaskState.DEAD, TaskState.PENDING}),
    TaskState.DONE: frozenset(),
    TaskState.DEAD: frozenset(),
}


def can_transition(current: TaskState, target: TaskState) -> bool:
    return target in ALLOWED_TRANSITIONS[current]


@dataclass(frozen=True)
class Task:
    id: str
    queue: str
    payload: dict[str, Any]
    state: TaskState
    priority: int
    attempts: int
    lease_generation: int
    max_attempts: int
    created_at: float
    available_at: float
    leased_until: float | None
    last_error: str | None

    @property
    def attempts_remaining(self) -> int:
        """How many more times this task may run.

        `max_attempts` limits total attempts, not retries, so a task configured
        with three has three executions in total rather than four.
        """
        return max(0, self.max_attempts - self.attempts)

    @property
    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "queue": self.queue,
            "payload": self.payload,
            "state": self.state.value,
            "priority": self.priority,
            "attempts": self.attempts,
            "lease_generation": self.lease_generation,
            "max_attempts": self.max_attempts,
            "attempts_remaining": self.attempts_remaining,
            "created_at": self.created_at,
            "available_at": self.available_at,
            "leased_until": self.leased_until,
            "last_error": self.last_error,
        }
