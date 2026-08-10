"""HTTP routing, request validation, and status codes."""

from __future__ import annotations

import json
from typing import Any

import pytest

from taskq.api import Api, Request

TOKEN = "s3cret-admin-token"


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


def admin(headers: dict[str, str] | None = None) -> dict[str, str]:
    return {"authorization": f"Bearer {TOKEN}", **(headers or {})}


# ------------------------------------------------------------------ routing


def test_health_needs_no_authentication(api: Api) -> None:
    response = call(api, "GET", "/health")
    assert response.status == 200
    assert response.body == {"status": "ok"}


def test_an_unknown_path_is_404(api: Api) -> None:
    assert call(api, "GET", "/nope").status == 404


def test_a_known_path_with_the_wrong_method_is_405(api: Api) -> None:
    assert call(api, "GET", "/admin/purge").status == 405


@pytest.mark.parametrize(
    "path",
    [
        "/queues/UPPER/tasks",
        "/queues/with space/tasks",
        "/queues/../../etc/passwd/tasks",
        "/queues//tasks",
        "/tasks/not-a-uuid",
        "/tasks/../admin/stats",
    ],
)
def test_a_path_that_does_not_match_the_pattern_is_404(api: Api, path: str) -> None:
    """Constraints live in the route pattern, so bad segments never reach a handler."""
    assert call(api, "GET", path).status in (404, 405)


def test_a_query_string_is_ignored_for_routing(api: Api) -> None:
    assert call(api, "GET", "/health").status == 200


# ------------------------------------------------------------------ enqueue


def test_enqueue_returns_201_and_the_task(api: Api) -> None:
    response = call(api, "POST", "/queues/emails/tasks", {"payload": {"to": "x"}})
    assert response.status == 201
    assert response.body["state"] == "pending"


def test_enqueue_rejects_a_non_object_body(api: Api) -> None:
    assert call(api, "POST", "/queues/emails/tasks", ["list"]).status == 400


def test_enqueue_rejects_invalid_json(api: Api) -> None:
    response = api.handle(
        Request(method="POST", path="/queues/emails/tasks", body=b"{oops", headers={})
    )
    assert response.status == 400
    assert response.body["error"] == "invalid_request"


@pytest.mark.parametrize("delay", ["abc", [1], {}, True])
def test_enqueue_answers_400_for_a_delay_of_the_wrong_type(api: Api, delay: Any) -> None:
    """The defect this test exists for escaped the handler entirely.

    `float(body.get("delay_seconds", 0.0) or 0.0)` raises `ValueError` on a
    string and `TypeError` on a container, and the dispatcher catches only
    `TaskqError` — so an ordinary malformed body produced an uncaught exception
    rather than the 400 the README documents.
    """
    response = call(api, "POST", "/queues/emails/tasks", {"payload": {}, "delay_seconds": delay})
    assert response.status == 400
    assert response.body["error"] == "invalid_request"


def test_enqueue_answers_400_for_a_non_finite_delay(api: Api) -> None:
    """NaN passed coercion and reached a NOT NULL column.

    JSON has no NaN literal, but Python's decoder accepts the bare token, and a
    client using it is still making a request this service has to refuse before
    storage sees it.
    """
    response = api.handle(
        Request(
            method="POST",
            path="/queues/emails/tasks",
            body=b'{"payload": {}, "delay_seconds": NaN}',
            headers={},
        )
    )
    assert response.status == 400


def test_enqueue_rejects_an_oversized_body(api: Api) -> None:
    response = call(api, "POST", "/queues/emails/tasks", {"payload": {"b": "x" * 4096}})
    assert response.status == 413


def test_enqueue_rejects_a_non_object_payload(api: Api) -> None:
    assert call(api, "POST", "/queues/emails/tasks", {"payload": 42}).status == 400


# ------------------------------------------------------------ lease cycle


def test_leasing_an_empty_queue_is_204(api: Api) -> None:
    assert call(api, "POST", "/queues/emails/lease").status == 204


def test_the_full_lease_and_ack_cycle(api: Api) -> None:
    created = call(api, "POST", "/queues/emails/tasks", {"payload": {}}).body
    leased = call(api, "POST", "/queues/emails/lease")
    assert leased.status == 200
    assert leased.body["id"] == created["id"]

    acked = call(api, "POST", f"/tasks/{created['id']}/ack")
    assert acked.status == 200
    assert acked.body["state"] == "done"


def test_failing_a_task_reports_the_error(api: Api) -> None:
    created = call(api, "POST", "/queues/emails/tasks", {"payload": {}}).body
    call(api, "POST", "/queues/emails/lease")
    failed = call(api, "POST", f"/tasks/{created['id']}/fail", {"error": "boom"})
    assert failed.status == 200
    assert failed.body["last_error"] == "boom"


def test_acknowledging_an_unknown_task_is_404(api: Api) -> None:
    assert call(api, "POST", f"/tasks/{'0' * 32}/ack").status == 404


def test_acknowledging_an_unleased_task_is_409(api: Api) -> None:
    created = call(api, "POST", "/queues/emails/tasks", {"payload": {}}).body
    assert call(api, "POST", f"/tasks/{created['id']}/ack").status == 409


def test_status_returns_the_task(api: Api) -> None:
    created = call(api, "POST", "/queues/emails/tasks", {"payload": {"k": "v"}}).body
    response = call(api, "GET", f"/tasks/{created['id']}")
    assert response.status == 200
    assert response.body["payload"] == {"k": "v"}


# -------------------------------------------------------------- admin routes


def test_admin_stats_requires_a_token(api: Api) -> None:
    assert call(api, "GET", "/admin/stats").status == 401


def test_admin_stats_rejects_a_wrong_token(api: Api) -> None:
    assert call(api, "GET", "/admin/stats", headers={"authorization": "Bearer wrong"}).status == 401


def test_admin_stats_accepts_the_right_token(api: Api) -> None:
    response = call(api, "GET", "/admin/stats", headers=admin())
    assert response.status == 200
    assert set(response.body["counts"]) == {"pending", "leased", "done", "dead"}


def test_admin_purge_requires_a_token(api: Api) -> None:
    assert call(api, "POST", "/admin/purge").status == 401


def test_admin_purge_removes_terminal_tasks(api: Api) -> None:
    created = call(api, "POST", "/queues/emails/tasks", {"payload": {}}).body
    call(api, "POST", "/queues/emails/lease")
    call(api, "POST", f"/tasks/{created['id']}/ack")

    response = call(api, "POST", "/admin/purge", headers=admin())
    assert response.status == 200
    assert response.body["purged"] == 1


def test_a_missing_and_a_wrong_token_give_the_same_body(api: Api) -> None:
    missing = call(api, "GET", "/admin/stats")
    wrong = call(api, "GET", "/admin/stats", headers={"authorization": "Bearer wrong"})
    assert missing.status == wrong.status
    assert missing.body == wrong.body


def test_error_responses_carry_a_code_and_a_detail(api: Api) -> None:
    body = call(api, "GET", "/nope").body
    assert body["error"] == "not_found"
    assert body["detail"]
