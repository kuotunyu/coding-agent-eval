"""Queue policy: what may be leased, when a failure retries, when it dies.

This is where the README's guarantees are actually implemented, so each one has
a comment naming the guarantee it satisfies. A change here that contradicts the
README is a defect even if every test still passes.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from taskq.config import Config
from taskq.errors import Conflict, NotFound, PayloadTooLarge, ValidationError
from taskq.idempotency import (
    DEFAULT_KEY_TTL_SECONDS,
    IdempotencyStore,
    validate_idempotency_key,
)
from taskq.limits import LimitStore
from taskq.models import Task, TaskState, can_transition
from taskq.storage import Storage
from taskq.util import (
    new_task_id,
    now,
    retry_delay,
    validate_delay_seconds,
    validate_max_attempts,
    validate_payload,
    validate_priority,
    validate_queue_name,
)


class TaskQueue:
    def __init__(
        self,
        storage: Storage,
        config: Config,
        clock: Callable[[], float] = now,
    ) -> None:
        self.storage = storage
        self.config = config
        self._clock = clock
        self.idempotency = IdempotencyStore(storage.connection)
        self.limits = LimitStore(storage.connection)

    def clock(self) -> float:
        """Current time as the queue sees it, so callers share one clock."""
        return self._clock()

    # ------------------------------------------------------------ enqueue

    def enqueue(
        self,
        queue: str,
        payload: Any,
        *,
        priority: Any = 0,
        max_attempts: Any = None,
        delay_seconds: Any = None,
        idempotency_key: Any = None,
    ) -> Task:
        """Add a task, or return the existing one for a live idempotency key.

        The key is checked after validation, so a malformed request is rejected
        rather than being answered with whatever the previous good request
        produced.
        """
        queue = validate_queue_name(queue)
        payload = validate_payload(payload)
        priority = validate_priority(priority)
        attempts_limit = validate_max_attempts(max_attempts, self.config.max_attempts)
        delay = validate_delay_seconds(delay_seconds)

        encoded_size = len(repr(payload).encode("utf-8"))
        if encoded_size > self.config.max_payload_bytes:
            raise PayloadTooLarge(
                f"payload is {encoded_size} bytes, limit is {self.config.max_payload_bytes}"
            )

        moment = self._clock()

        key = None
        if idempotency_key is not None:
            key = validate_idempotency_key(idempotency_key)
            existing = self.idempotency.lookup(queue, key, moment)
            if existing is not None:
                previous = self.storage.get(existing.task_id)
                if previous is not None:
                    return previous

        task = Task(
            id=new_task_id(),
            queue=queue,
            payload=payload,
            state=TaskState.PENDING,
            priority=priority,
            attempts=0,
            lease_generation=0,
            max_attempts=attempts_limit,
            created_at=moment,
            available_at=moment + delay,
            leased_until=None,
            last_error=None,
        )
        self.storage.insert(task)
        if key is not None:
            self.idempotency.remember(
                queue, key, task.id, moment, DEFAULT_KEY_TTL_SECONDS
            )
        return task

    # -------------------------------------------------------------- lease

    def lease(self, queue: str) -> Task | None:
        """Lease the next runnable task, or return None if there is nothing to do.

        Selection and claim share one write transaction, so a task is leased to
        at most one worker at a time even when several ask at once.
        """
        queue = validate_queue_name(queue)
        moment = self._clock()

        with self.storage.write_transaction() as connection:
            # Capacity is checked inside the transaction, so the count cannot
            # move between the check and the claim below it.
            if not self.limits.has_capacity(connection, queue, moment):
                return None

            candidate = self.storage.next_runnable(connection, queue, moment)
            if candidate is None:
                return None

            # A task whose attempts are already spent must never be handed out.
            # Reaching here means it was leased and the lease expired before the
            # worker reported anything.
            if candidate.exhausted:
                self.storage.mark_state(
                    candidate.id,
                    TaskState.DEAD,
                    leased_until=None,
                    last_error=candidate.last_error or "attempts exhausted",
                )
                return None

            attempts = candidate.attempts + 1
            lease_generation = candidate.lease_generation + 1
            leased_until = moment + self.config.lease_seconds
            self.storage.mark_leased(
                connection,
                candidate.id,
                leased_until,
                attempts,
                lease_generation,
            )

        leased = self.storage.get(candidate.id)
        assert leased is not None  # noqa: S101 - just written inside the transaction
        return leased

    # --------------------------------------------------- completion paths

    def acknowledge(self, task_id: str) -> Task:
        """Mark a leased task done."""
        task = self._require(task_id)
        if not can_transition(task.state, TaskState.DONE):
            raise Conflict(f"task in state {task.state.value} cannot be acknowledged")

        self.storage.mark_state(task.id, TaskState.DONE, leased_until=None)
        return self._require(task_id)

    def fail(self, task_id: str, error: str | None = None) -> Task:
        """Report a failed attempt: retry if attempts remain, otherwise dead-letter.

        `max_attempts` counts attempts rather than retries, so a task with three
        runs three times in total. The attempt being reported has already been
        counted by `lease`.
        """
        task = self._require(task_id)
        if task.state is not TaskState.LEASED:
            raise Conflict(f"task in state {task.state.value} is not leased")

        message = (error or "").strip() or None

        if task.attempts >= task.max_attempts:
            self.storage.mark_state(
                task.id, TaskState.DEAD, leased_until=None, last_error=message
            )
            return self._require(task_id)

        delay = retry_delay(
            task.attempts, self.config.base_retry_delay, self.config.max_retry_delay
        )
        self.storage.mark_state(
            task.id,
            TaskState.PENDING,
            available_at=self._clock() + delay,
            leased_until=None,
            last_error=message,
        )
        return self._require(task_id)

    # ------------------------------------------------------------ queries

    def get(self, task_id: str) -> Task:
        return self._require(task_id)

    def stats(self) -> dict[str, int]:
        return self.storage.counts_by_state()

    def purge(self) -> int:
        return self.storage.purge_terminal()

    def _require(self, task_id: str) -> Task:
        if not isinstance(task_id, str) or not task_id:
            raise ValidationError("task id must be a non-empty string")
        task = self.storage.get(task_id)
        if task is None:
            raise NotFound(f"no task with id {task_id}")
        return task
