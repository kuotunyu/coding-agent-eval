"""Idempotency keys for enqueue.

A client that retries an enqueue after a timeout has no way to know whether the
first attempt landed. Without a key it either drops work or duplicates it, and
for a task queue duplicating is usually the worse of the two.

A key is scoped to its queue. The same key in two queues is two different pieces
of work, and treating them as one would silently drop the second.

Keys expire. Holding them forever turns a retry window into a permanent
constraint, so a client reusing a natural key — an order id, say — could never
enqueue that work again.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from taskq.errors import ValidationError

#: Keys appear in stored rows and in client code, so the legal set is small.
IDEMPOTENCY_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"

DEFAULT_KEY_TTL_SECONDS = 24 * 60 * 60

_KEY = re.compile(IDEMPOTENCY_KEY_PATTERN)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    queue      TEXT NOT NULL,
    key        TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    PRIMARY KEY (queue, key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expiry ON idempotency_keys (expires_at);
"""


def validate_idempotency_key(key: object) -> str:
    if not isinstance(key, str) or not _KEY.match(key):
        raise ValidationError(f"idempotency key must match {IDEMPOTENCY_KEY_PATTERN}")
    return key


@dataclass(frozen=True)
class KeyRecord:
    queue: str
    key: str
    task_id: str
    created_at: float
    expires_at: float


class IdempotencyStore:
    """Maps `(queue, key)` to the task the first request created."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection
        self._connection.executescript(_SCHEMA)

    def lookup(self, queue: str, key: str, at: float) -> KeyRecord | None:
        """Return the live record for this key, or None.

        An expired record is treated as absent rather than being returned with a
        flag: a caller that forgot to check the expiry would otherwise reuse a
        task id from an arbitrarily long time ago.
        """
        row = self._connection.execute(
            "SELECT * FROM idempotency_keys WHERE queue = ? AND key = ? AND expires_at > ?",
            (queue, key, at),
        ).fetchone()
        if row is None:
            return None
        return KeyRecord(
            queue=row["queue"],
            key=row["key"],
            task_id=row["task_id"],
            created_at=row["created_at"],
            expires_at=row["expires_at"],
        )

    def remember(
        self, queue: str, key: str, task_id: str, at: float, ttl: float
    ) -> KeyRecord:
        """Record a key, replacing an expired one for the same pair."""
        record = KeyRecord(
            queue=queue, key=key, task_id=task_id, created_at=at, expires_at=at + ttl
        )
        self._connection.execute(
            "INSERT INTO idempotency_keys (queue, key, task_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(queue, key) DO UPDATE SET "
            "  task_id = excluded.task_id, "
            "  created_at = excluded.created_at, "
            "  expires_at = excluded.expires_at",
            (queue, key, task_id, record.created_at, record.expires_at),
        )
        return record

    def purge_expired(self, at: float) -> int:
        cursor = self._connection.execute(
            "DELETE FROM idempotency_keys WHERE expires_at <= ?", (at,)
        )
        return cursor.rowcount

    def count(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) AS n FROM idempotency_keys").fetchone()
        return int(row["n"])
