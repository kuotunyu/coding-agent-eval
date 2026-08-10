"""Shared fixtures.

Time is injected everywhere rather than slept through, so lease expiry and retry
backoff can be tested at their exact boundaries instead of approximately.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from taskq.api import Api
from taskq.config import Config
from taskq.queue import TaskQueue
from taskq.storage import Storage


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, start: float = 1_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += seconds
        return self.value


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        database=str(tmp_path / "taskq.db"),
        admin_token="s3cret-admin-token",
        max_payload_bytes=1024,
        lease_seconds=30,
        max_attempts=3,
        base_retry_delay=2.0,
        max_retry_delay=60.0,
    )


@pytest.fixture
def storage(config: Config) -> Iterator[Storage]:
    store = Storage(config.database)
    yield store
    store.close()


@pytest.fixture
def queue(storage: Storage, config: Config, clock: FakeClock) -> TaskQueue:
    return TaskQueue(storage, config, clock=clock)


@pytest.fixture
def api(queue: TaskQueue, config: Config) -> Api:
    return Api(queue, config)
