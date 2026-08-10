"""Per-queue concurrency caps.

A queue can be capped so that at most N of its tasks are leased at once. This is
what stops a queue whose tasks call a rate-limited third party from leasing
fifty at a time and having forty-nine of them fail.

The cap is evaluated inside the same write transaction that leases, so the count
it sees cannot change between the check and the claim. Reading the count in a
separate statement would let two workers each observe N-1 and both proceed.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from taskq.errors import ValidationError
from taskq.models import TaskState

#: Absent from this table means uncapped, which is the default.
_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue_limits (
    queue           TEXT PRIMARY KEY,
    max_concurrency INTEGER NOT NULL
);
"""

_QUEUE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


@dataclass(frozen=True)
class QueueLimit:
    queue: str
    max_concurrency: int


def validate_max_concurrency(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValidationError("max_concurrency must be an integer of at least 1")
    return value


class LimitStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.executescript(_SCHEMA)

    def set_limit(self, queue: str, max_concurrency: int) -> QueueLimit:
        if not _QUEUE.match(queue):
            raise ValidationError(f"queue name {queue!r} is not legal")
        limit = QueueLimit(queue=queue, max_concurrency=validate_max_concurrency(max_concurrency))
        self._connection.execute(
            "INSERT INTO queue_limits (queue, max_concurrency) VALUES (?, ?) "
            "ON CONFLICT(queue) DO UPDATE SET max_concurrency = excluded.max_concurrency",
            (limit.queue, limit.max_concurrency),
        )
        return limit

    def clear_limit(self, queue: str) -> bool:
        cursor = self._connection.execute("DELETE FROM queue_limits WHERE queue = ?", (queue,))
        return cursor.rowcount > 0

    def get_limit(self, queue: str) -> int | None:
        row = self._connection.execute(
            "SELECT max_concurrency FROM queue_limits WHERE queue = ?", (queue,)
        ).fetchone()
        return int(row["max_concurrency"]) if row else None

    def all_limits(self) -> dict[str, int]:
        rows = self._connection.execute("SELECT queue, max_concurrency FROM queue_limits").fetchall()
        return {row["queue"]: int(row["max_concurrency"]) for row in rows}

    def active_leases(
        self, connection: sqlite3.Connection, queue: str, at: float
    ) -> int:
        """Leases that have not expired.

        An expired lease does not occupy a slot. Counting it would let one
        crashed worker hold capacity until someone noticed.
        """
        row = connection.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE queue = ? AND state = ? "
            "AND leased_until IS NOT NULL AND leased_until > ?",
            (queue, TaskState.LEASED.value, at),
        ).fetchone()
        return int(row["n"])

    def has_capacity(
        self, connection: sqlite3.Connection, queue: str, at: float
    ) -> bool:
        """Whether another task may be leased from `queue` right now.

        Called inside the leasing transaction, so the count cannot move between
        this check and the claim that follows it.
        """
        limit = self.get_limit(queue)
        if limit is None:
            return True
        return self.active_leases(connection, queue, at) < limit
