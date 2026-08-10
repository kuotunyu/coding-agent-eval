"""A small HTTP client for the API.

Written against `urllib` so the client has no dependency the server does not.

The client sends an idempotency key on every enqueue unless told otherwise. A
retry after a network timeout is the case this exists for, and a client that
only sends a key when the caller remembers to is a client that duplicates work
exactly when it matters most.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any


class ClientError(RuntimeError):
    """The server answered with an error status."""

    def __init__(self, status: int, body: dict[str, Any]) -> None:
        self.status = status
        self.body = body
        detail = body.get("detail") or body.get("error") or "no detail"
        super().__init__(f"HTTP {status}: {detail}")


@dataclass
class Response:
    status: int
    body: dict[str, Any]


class TaskqClient:
    def __init__(
        self,
        base_url: str,
        *,
        admin_token: str | None = None,
        timeout: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.admin_token = admin_token
        self.timeout = timeout

    # -------------------------------------------------------------- plumbing

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - base_url is caller-supplied
            f"{self.base_url}{path}",
            data=payload,
            method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                raw = response.read()
                return Response(response.status, json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            body_out = json.loads(raw) if raw else {}
            raise ClientError(exc.code, body_out) from exc

    def _admin_headers(self) -> dict[str, str]:
        if not self.admin_token:
            raise ClientError(401, {"error": "unauthorized", "detail": "no admin token set"})
        return {"Authorization": f"Bearer {self.admin_token}"}

    # ---------------------------------------------------------------- public

    def enqueue(
        self,
        queue: str,
        payload: dict[str, Any],
        *,
        priority: int = 0,
        max_attempts: int | None = None,
        delay_seconds: float = 0.0,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Enqueue a task.

        A key is generated when none is supplied, so a retried request cannot
        duplicate work. Pass an empty string to opt out deliberately.
        """
        key = uuid.uuid4().hex if idempotency_key is None else idempotency_key
        headers = {"Idempotency-Key": key} if key else {}

        body: dict[str, Any] = {
            "payload": payload,
            "priority": priority,
            "delay_seconds": delay_seconds,
        }
        if max_attempts is not None:
            body["max_attempts"] = max_attempts

        return self._request("POST", f"/queues/{queue}/tasks", body, headers).body

    def lease(self, queue: str) -> dict[str, Any] | None:
        """Lease the next task, or None when the queue is empty."""
        response = self._request("POST", f"/queues/{queue}/lease")
        return None if response.status == 204 else response.body

    def acknowledge(self, task_id: str) -> dict[str, Any]:
        return self._request("POST", f"/tasks/{task_id}/ack").body

    def fail(self, task_id: str, error: str) -> dict[str, Any]:
        return self._request("POST", f"/tasks/{task_id}/fail", {"error": error}).body

    def status(self, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/tasks/{task_id}").body

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health").body

    # ----------------------------------------------------------------- admin

    def stats(self) -> dict[str, int]:
        body = self._request("GET", "/admin/stats", headers=self._admin_headers()).body
        return dict(body["counts"])

    def purge(self) -> int:
        body = self._request("POST", "/admin/purge", headers=self._admin_headers()).body
        return int(body["purged"])

    def dead_letters(self, queue: str | None = None) -> dict[str, Any]:
        body = {"queue": queue} if queue else {}
        return self._request("GET", "/admin/dead", body, self._admin_headers()).body

    def requeue(self, task_id: str) -> dict[str, Any]:
        return self._request(
            "POST", f"/admin/dead/{task_id}/requeue", headers=self._admin_headers()
        ).body

    def set_limit(self, queue: str, max_concurrency: int) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/admin/queues/{queue}/limit",
            {"max_concurrency": max_concurrency},
            self._admin_headers(),
        ).body

    def limits(self) -> dict[str, int]:
        body = self._request("GET", "/admin/limits", headers=self._admin_headers()).body
        return dict(body["limits"])
