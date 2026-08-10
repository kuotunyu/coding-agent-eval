"""Runtime configuration.

Defaults live here rather than being scattered as literals, so the documented
guarantees in the README and the values the code uses can be compared side by
side.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

#: Queue names are used in URLs and stored as-is, so the set of legal characters
#: is deliberately small.
QUEUE_NAME_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"

DEFAULT_MAX_PAYLOAD_BYTES = 64 * 1024
DEFAULT_LEASE_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_RETRY_DELAY = 2.0
DEFAULT_MAX_RETRY_DELAY = 300.0


@dataclass(frozen=True)
class Config:
    database: str = "taskq.db"
    host: str = "127.0.0.1"
    port: int = 8080
    admin_token: str | None = None
    max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_BYTES
    lease_seconds: int = DEFAULT_LEASE_SECONDS
    max_attempts: int = DEFAULT_MAX_ATTEMPTS
    base_retry_delay: float = DEFAULT_BASE_RETRY_DELAY
    max_retry_delay: float = DEFAULT_MAX_RETRY_DELAY

    @classmethod
    def from_environment(cls, environ: dict[str, str] | None = None) -> Config:
        source = os.environ if environ is None else environ
        return cls(
            database=source.get("TASKQ_DATABASE", cls.database),
            host=source.get("TASKQ_HOST", cls.host),
            port=int(source.get("TASKQ_PORT", cls.port)),
            admin_token=source.get("TASKQ_ADMIN_TOKEN") or None,
            max_payload_bytes=int(
                source.get("TASKQ_MAX_PAYLOAD_BYTES", cls.max_payload_bytes)
            ),
            lease_seconds=int(source.get("TASKQ_LEASE_SECONDS", cls.lease_seconds)),
            max_attempts=int(source.get("TASKQ_MAX_ATTEMPTS", cls.max_attempts)),
            base_retry_delay=float(
                source.get("TASKQ_BASE_RETRY_DELAY", cls.base_retry_delay)
            ),
            max_retry_delay=float(source.get("TASKQ_MAX_RETRY_DELAY", cls.max_retry_delay)),
        )
