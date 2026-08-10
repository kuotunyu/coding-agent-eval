"""Witness for fx-taskq-py/B-001.

Overlaid at run time. This file is never part of `tree/`, so it is not visible
to an agent under measurement — if it were, it would state the answer.

Self-contained on purpose: it imports only from `taskq`, never from the
fixture's own `tests/` package. A witness that shared fixtures with the suite it
is meant to be independent of would fail for reasons that have nothing to do
with the defect.

What it pins is the one property the fixture's own suite does not: that a
presented token is rejected when it merely *begins* with the configured one.
The suite covers a wrong token, a token differing in its last byte, a prefix of
the token, and a missing token — every case where the presented value is the
same length or shorter. Nothing covers a longer one, which is exactly the gap
this bug lives in.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from taskq.api import Api, Request
from taskq.auth import verify_admin_token
from taskq.config import Config
from taskq.errors import Unauthorized
from taskq.queue import TaskQueue
from taskq.storage import Storage

TOKEN = "s3cret-admin-token"


@pytest.fixture
def api(tmp_path: Path):
    config = Config(database=str(tmp_path / "witness.db"), admin_token=TOKEN)
    storage = Storage(config.database)
    try:
        yield Api(TaskQueue(storage, config), config)
    finally:
        storage.close()


def test_a_token_that_only_starts_with_the_real_one_is_rejected() -> None:
    with pytest.raises(Unauthorized):
        verify_admin_token(TOKEN + "-and-then-some", TOKEN)


def test_an_extended_token_does_not_reach_an_admin_route(api: Api) -> None:
    response = api.handle(
        Request(
            method="GET",
            path="/admin/stats",
            body=b"",
            headers={"authorization": f"Bearer {TOKEN}0000"},
        )
    )
    assert response.status == 401, json.dumps(response.body)
