"""Admin routes for dead letters and queue limits.

Every one of these needs a token, and each is checked individually. Adding a
route and forgetting to authenticate it is a realistic mistake and would not be
caught by testing the two original admin routes.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from taskq.api import Api, Request
from taskq.queue import TaskQueue

from .conftest import FakeClock

TOKEN = "s3cret-admin-token"

ADMIN_ROUTES = [
    ("GET", "/admin/stats"),
    ("GET", "/admin/dead"),
    ("GET", "/admin/limits"),
    ("GET", "/admin/metrics"),
    ("POST", "/admin/purge"),
    ("POST", f"/admin/dead/{'0' * 32}/requeue"),
    ("POST", "/admin/queues/emails/limit"),
]


def call(
    api: Api,
    method: str,
    path: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
):
    encoded = b"" if body is None else json.dumps(body).encode("utf-8")
    return api.handle(
        Request(method=method, path=path, body=encoded, headers=headers or {})
    )


def admin() -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}"}


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_every_admin_route_requires_a_token(api: Api, method: str, path: str) -> None:
    assert call(api, method, path, {"max_concurrency": 1}).status == 401


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_every_admin_route_rejects_a_wrong_token(api: Api, method: str, path: str) -> None:
    response = call(
        api, method, path, {"max_concurrency": 1}, {"authorization": "Bearer wrong"}
    )
    assert response.status == 401


# ------------------------------------------------------------ dead letters


def test_dead_letters_are_listed(api: Api, queue: TaskQueue, clock: FakeClock) -> None:
    task = queue.enqueue("emails", {}, max_attempts=1)
    queue.lease("emails")
    queue.fail(task.id, "boom")

    response = call(api, "GET", "/admin/dead", headers=admin())
    assert response.status == 200
    assert response.body["total"] == 1


def test_a_dead_task_can_be_requeued(api: Api, queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {}, max_attempts=1)
    queue.lease("emails")
    queue.fail(task.id, "boom")

    response = call(api, "POST", f"/admin/dead/{task.id}/requeue", headers=admin())
    assert response.status == 200
    assert response.body["state"] == "pending"
    assert response.body["attempts"] == 0


def test_requeuing_a_live_task_is_409(api: Api, queue: TaskQueue) -> None:
    task = queue.enqueue("emails", {})
    response = call(api, "POST", f"/admin/dead/{task.id}/requeue", headers=admin())
    assert response.status == 409


# ------------------------------------------------------------------ limits


def test_a_limit_can_be_set_and_listed(api: Api) -> None:
    created = call(
        api, "POST", "/admin/queues/emails/limit", {"max_concurrency": 3}, admin()
    )
    assert created.status == 200
    assert created.body == {"queue": "emails", "max_concurrency": 3}

    listed = call(api, "GET", "/admin/limits", headers=admin())
    assert listed.body["limits"] == {"emails": 3}


def test_setting_a_limit_without_a_value_is_400(api: Api) -> None:
    assert call(api, "POST", "/admin/queues/emails/limit", {}, admin()).status == 400


@pytest.mark.parametrize("value", [0, -1, "three", True])
def test_an_illegal_limit_is_400(api: Api, value: object) -> None:
    response = call(
        api, "POST", "/admin/queues/emails/limit", {"max_concurrency": value}, admin()
    )
    assert response.status == 400


def test_a_limit_actually_caps_leasing(api: Api) -> None:
    call(api, "POST", "/admin/queues/emails/limit", {"max_concurrency": 1}, admin())
    call(api, "POST", "/queues/emails/tasks", {"payload": {}})
    call(api, "POST", "/queues/emails/tasks", {"payload": {}})

    assert call(api, "POST", "/queues/emails/lease").status == 200
    assert call(api, "POST", "/queues/emails/lease").status == 204


# ------------------------------------------------------- idempotency header


def test_the_idempotency_key_header_deduplicates(api: Api) -> None:
    headers = {"idempotency-key": "order-42"}
    first = call(api, "POST", "/queues/emails/tasks", {"payload": {"n": 1}}, headers)
    second = call(api, "POST", "/queues/emails/tasks", {"payload": {"n": 2}}, headers)
    assert first.body["id"] == second.body["id"]


def test_the_key_may_also_be_given_in_the_body(api: Api) -> None:
    body = {"payload": {}, "idempotency_key": "order-42"}
    first = call(api, "POST", "/queues/emails/tasks", body)
    second = call(api, "POST", "/queues/emails/tasks", body)
    assert first.body["id"] == second.body["id"]


def test_the_header_wins_over_the_body(api: Api) -> None:
    """One request cannot claim two identities; the conventional place decides."""
    body = {"payload": {}, "idempotency_key": "from-body"}
    first = call(api, "POST", "/queues/emails/tasks", body, {"idempotency-key": "from-header"})
    second = call(api, "POST", "/queues/emails/tasks", {"payload": {}}, {"idempotency-key": "from-header"})
    assert first.body["id"] == second.body["id"]


def test_a_malformed_key_is_400(api: Api) -> None:
    response = call(
        api, "POST", "/queues/emails/tasks", {"payload": {}}, {"idempotency-key": "has space"}
    )
    assert response.status == 400


def test_metrics_are_exposed_to_admins(api: Api, queue: TaskQueue) -> None:
    queue.enqueue("emails", {})
    response = call(api, "GET", "/admin/metrics", headers=admin())
    assert response.status == 200
    assert response.body["totals"]["backlog"] == 1
    assert [q["queue"] for q in response.body["queues"]] == ["emails"]
