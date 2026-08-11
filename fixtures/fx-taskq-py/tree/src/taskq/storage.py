"""SQLite persistence.

Every statement is parameterised. Task and queue identifiers arrive from HTTP
requests, and string-formatting them into SQL would put request data into the
query itself.

Leasing runs inside an IMMEDIATE transaction. Selecting a candidate and then
claiming it are two statements, and without a write lock held across both, two
workers can select the same row and both believe they own it.

One connection, usable from any thread. `check_same_thread=False` lifts the
Python-level guard that would otherwise refuse a connection used from a thread
other than the one that built it — a guard that makes `Storage` unusable in any
server that constructs it on one thread and serves on another. SQLite itself is
built serialized, so sharing the handle is safe; what is not safe is two threads
interleaving `BEGIN IMMEDIATE ... COMMIT` on it, which is what `_write_lock`
prevents.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from taskq.models import Task, TaskState

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id            TEXT PRIMARY KEY,
    queue         TEXT NOT NULL,
    payload       TEXT NOT NULL,
    state         TEXT NOT NULL,
    priority      INTEGER NOT NULL DEFAULT 0,
    attempts      INTEGER NOT NULL DEFAULT 0,
    max_attempts  INTEGER NOT NULL,
    created_at    REAL NOT NULL,
    available_at  REAL NOT NULL,
    leased_until  REAL,
    last_error    TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_runnable
    ON tasks (queue, state, available_at, priority DESC, created_at);

CREATE TABLE IF NOT EXISTS schema_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def row_to_task(row: sqlite3.Row) -> Task:
    """Build a Task from a row. Public so sibling modules can share the mapping."""
    return Task(
        id=row["id"],
        queue=row["queue"],
        payload=json.loads(row["payload"]),
        state=TaskState(row["state"]),
        priority=row["priority"],
        attempts=row["attempts"],
        lease_generation=row["lease_generation"],
        max_attempts=row["max_attempts"],
        created_at=row["created_at"],
        available_at=row["available_at"],
        leased_until=row["leased_until"],
        last_error=row["last_error"],
    )


class Storage:
    """Owns the connection and the schema. Knows nothing about queue policy."""

    def __init__(self, database: str) -> None:
        self.database = database
        self._write_lock = threading.Lock()
        self._connection = sqlite3.connect(
            database, isolation_level=None, check_same_thread=False
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    @property
    def connection(self) -> sqlite3.Connection:
        """The live connection, for modules that own their own tables."""
        return self._connection

    def close(self) -> None:
        self._connection.close()

    # ------------------------------------------------------------- schema

    def _migrate(self) -> None:
        self._connection.executescript(_SCHEMA)
        with self.write_transaction() as connection:
            current = self.schema_version()
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
            }
            if current < 2 and "lease_generation" not in columns:
                connection.execute(
                    "ALTER TABLE tasks ADD COLUMN "
                    "lease_generation INTEGER NOT NULL DEFAULT 0"
                )
            if current != SCHEMA_VERSION:
                connection.execute(
                    "INSERT INTO schema_meta (key, value) VALUES ('version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(SCHEMA_VERSION),),
                )

    def schema_version(self) -> int:
        row = self._connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    @contextmanager
    def write_transaction(self) -> Iterator[sqlite3.Connection]:
        """A transaction that takes the write lock immediately.

        BEGIN IMMEDIATE rather than a deferred BEGIN: leasing reads a candidate
        and then claims it, and a deferred transaction would let two workers
        read the same row before either wrote.
        """
        # Held for the whole transaction. Two threads issuing BEGIN IMMEDIATE on
        # one connection is not a race over rows, it is an error: SQLite refuses
        # to start a transaction inside a transaction.
        with self._write_lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            self._connection.execute("COMMIT")

    # -------------------------------------------------------------- tasks

    def insert(self, task: Task) -> None:
        self._connection.execute(
            "INSERT INTO tasks (id, queue, payload, state, priority, attempts, "
            "lease_generation, max_attempts, created_at, available_at, leased_until, "
            "last_error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.id,
                task.queue,
                json.dumps(task.payload, ensure_ascii=False, sort_keys=True),
                task.state.value,
                task.priority,
                task.attempts,
                task.lease_generation,
                task.max_attempts,
                task.created_at,
                task.available_at,
                task.leased_until,
                task.last_error,
            ),
        )

    def get(self, task_id: str) -> Task | None:
        row = self._connection.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        return row_to_task(row) if row else None

    def next_runnable(
        self, connection: sqlite3.Connection, queue: str, at: float
    ) -> Task | None:
        """The task that should run next, or None.

        Ordering is priority descending then oldest-first, matching the README.
        Expired leases are runnable again, which is what makes delivery
        at-least-once rather than at-most-once.
        """
        row = connection.execute(
            "SELECT * FROM tasks WHERE queue = ? AND available_at <= ? AND ("
            "  state = ? OR (state = ? AND leased_until IS NOT NULL AND leased_until <= ?)"
            ") ORDER BY priority DESC, created_at ASC, id ASC LIMIT 1",
            (queue, at, TaskState.PENDING.value, TaskState.LEASED.value, at),
        ).fetchone()
        return row_to_task(row) if row else None

    def mark_leased(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        leased_until: float,
        attempts: int,
        lease_generation: int,
    ) -> None:
        connection.execute(
            "UPDATE tasks SET state = ?, leased_until = ?, attempts = ?, "
            "lease_generation = ? WHERE id = ?",
            (
                TaskState.LEASED.value,
                leased_until,
                attempts,
                lease_generation,
                task_id,
            ),
        )

    def mark_state(
        self,
        task_id: str,
        state: TaskState,
        *,
        available_at: float | None = None,
        leased_until: float | None = None,
        last_error: str | None = None,
    ) -> None:
        self._connection.execute(
            "UPDATE tasks SET state = ?, available_at = COALESCE(?, available_at), "
            "leased_until = ?, last_error = ? WHERE id = ?",
            (state.value, available_at, leased_until, last_error, task_id),
        )

    def reset_attempts(self, task_id: str) -> None:
        """Give a task a fresh attempt budget, used when requeuing from dead."""
        self._connection.execute("UPDATE tasks SET attempts = 0 WHERE id = ?", (task_id,))

    def counts_by_state(self) -> dict[str, int]:
        rows = self._connection.execute(
            "SELECT state, COUNT(*) AS n FROM tasks GROUP BY state"
        ).fetchall()
        counts = {state.value: 0 for state in TaskState}
        for row in rows:
            counts[row["state"]] = row["n"]
        return counts

    def purge_terminal(self) -> int:
        cursor = self._connection.execute(
            "DELETE FROM tasks WHERE state IN (?, ?)",
            (TaskState.DONE.value, TaskState.DEAD.value),
        )
        return cursor.rowcount

    def all_tasks(self) -> list[dict[str, Any]]:
        rows = self._connection.execute("SELECT * FROM tasks ORDER BY created_at").fetchall()
        return [row_to_task(row).as_dict() for row in rows]
