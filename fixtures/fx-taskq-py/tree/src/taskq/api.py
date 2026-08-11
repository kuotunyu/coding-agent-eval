"""HTTP routing and request handling.

The router is a table of compiled patterns rather than string splitting, so a
path segment containing a slash or a traversal sequence simply fails to match
instead of being interpreted.

Handlers translate between JSON and the queue. All policy lives in `queue.py`;
anything decided here would be a rule the tests of the queue never see.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from taskq.auth import authenticate_admin
from taskq.config import Config
from taskq.deadletter import DeadLetterQueue
from taskq.errors import NotFound, PayloadTooLarge, TaskqError, ValidationError
from taskq.limits import validate_max_concurrency
from taskq.metrics import Metrics
from taskq.queue import TaskQueue

Handler = Callable[["Request", dict[str, str]], "Response"]

#: Queue and task segments are constrained in the pattern itself, so a name with
#: a slash or a traversal sequence never reaches a handler.
_QUEUE = r"(?P<queue>[a-z0-9][a-z0-9_-]{0,63})"
_TASK = r"(?P<task_id>[0-9a-f]{32})"


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    body: bytes
    headers: dict[str, str]

    def header(self, name: str) -> str | None:
        return self.headers.get(name.lower())

    def json(self, limit: int) -> Any:
        if len(self.body) > limit:
            raise PayloadTooLarge(f"request body exceeds {limit} bytes")
        if not self.body:
            return {}
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValidationError(f"body is not valid JSON: {exc}") from exc


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any]

    def encode(self) -> bytes:
        return json.dumps(self.body, ensure_ascii=False).encode("utf-8")


class Api:
    def __init__(self, queue: TaskQueue, config: Config) -> None:
        self.queue = queue
        self.config = config
        self.dead_letters = DeadLetterQueue(queue.storage)
        self.metrics = Metrics(queue.storage)
        self._routes: list[tuple[str, re.Pattern[str], Handler]] = [
            ("POST", re.compile(rf"^/queues/{_QUEUE}/tasks$"), self._enqueue),
            ("POST", re.compile(rf"^/queues/{_QUEUE}/lease$"), self._lease),
            ("POST", re.compile(rf"^/tasks/{_TASK}/ack$"), self._ack),
            ("POST", re.compile(rf"^/tasks/{_TASK}/fail$"), self._fail),
            ("GET", re.compile(rf"^/tasks/{_TASK}$"), self._status),
            ("GET", re.compile(r"^/admin/stats$"), self._stats),
            ("GET", re.compile(r"^/admin/dead$"), self._dead_list),
            ("POST", re.compile(rf"^/admin/dead/{_TASK}/requeue$"), self._dead_requeue),
            ("POST", re.compile(rf"^/admin/queues/{_QUEUE}/limit$"), self._set_limit),
            ("GET", re.compile(r"^/admin/limits$"), self._list_limits),
            ("GET", re.compile(r"^/admin/metrics$"), self._metrics),
            ("POST", re.compile(r"^/admin/purge$"), self._purge),
            ("GET", re.compile(r"^/health$"), self._health),
        ]

    def handle(self, request: Request) -> Response:
        """Route and run, converting known errors into their documented statuses."""
        try:
            for method, pattern, handler in self._routes:
                match = pattern.match(request.path)
                if match is None:
                    continue
                if method != request.method:
                    return Response(405, {"error": "method_not_allowed", "detail": method})
                return handler(request, match.groupdict())
            raise NotFound(f"no route for {request.method} {request.path}")
        except TaskqError as exc:
            return Response(exc.status, {"error": exc.code, "detail": str(exc)})

    # ----------------------------------------------------------- handlers

    def _enqueue(self, request: Request, params: dict[str, str]) -> Response:
        body = request.json(self.config.max_payload_bytes)
        if not isinstance(body, dict):
            raise ValidationError("request body must be a JSON object")

        # The header is the conventional place for this, but accepting it in the
        # body too means a client that cannot set headers is not locked out.
        idempotency_key = request.header("idempotency-key") or body.get("idempotency_key")

        task = self.queue.enqueue(
            params["queue"],
            body.get("payload"),
            priority=body.get("priority", 0),
            max_attempts=body.get("max_attempts"),
            delay_seconds=body.get("delay_seconds"),
            idempotency_key=idempotency_key,
        )
        return Response(201, task.as_dict())

    def _lease(self, request: Request, params: dict[str, str]) -> Response:
        task = self.queue.lease(params["queue"])
        if task is None:
            return Response(204, {})
        return Response(200, task.as_dict())

    def _ack(self, request: Request, params: dict[str, str]) -> Response:
        body = self._completion_body(request)
        return Response(
            200,
            self.queue.acknowledge(
                params["task_id"], body["lease_generation"]
            ).as_dict(),
        )

    def _fail(self, request: Request, params: dict[str, str]) -> Response:
        body = self._completion_body(request)
        error = body.get("error")
        return Response(
            200,
            self.queue.fail(
                params["task_id"], body["lease_generation"], error
            ).as_dict(),
        )

    def _completion_body(self, request: Request) -> dict[str, Any]:
        body = request.json(self.config.max_payload_bytes)
        if not isinstance(body, dict):
            raise ValidationError("request body must be a JSON object")
        generation = body.get("lease_generation")
        if (
            isinstance(generation, bool)
            or not isinstance(generation, int)
            or generation < 1
        ):
            raise ValidationError("lease_generation must be a positive integer")
        return body

    def _status(self, request: Request, params: dict[str, str]) -> Response:
        return Response(200, self.queue.get(params["task_id"]).as_dict())

    def _stats(self, request: Request, params: dict[str, str]) -> Response:
        authenticate_admin(request.header("authorization"), self.config.admin_token)
        return Response(200, {"counts": self.queue.stats()})

    def _purge(self, request: Request, params: dict[str, str]) -> Response:
        authenticate_admin(request.header("authorization"), self.config.admin_token)
        return Response(200, {"purged": self.queue.purge()})

    def _dead_list(self, request: Request, params: dict[str, str]) -> Response:
        authenticate_admin(request.header("authorization"), self.config.admin_token)
        body = request.json(self.config.max_payload_bytes)
        queue = body.get("queue") if isinstance(body, dict) else None
        return Response(200, self.dead_letters.list(queue).as_dict())

    def _dead_requeue(self, request: Request, params: dict[str, str]) -> Response:
        authenticate_admin(request.header("authorization"), self.config.admin_token)
        task = self.dead_letters.requeue(params["task_id"], self.queue.clock())
        return Response(200, task.as_dict())

    def _set_limit(self, request: Request, params: dict[str, str]) -> Response:
        authenticate_admin(request.header("authorization"), self.config.admin_token)
        body = request.json(self.config.max_payload_bytes)
        if not isinstance(body, dict) or "max_concurrency" not in body:
            raise ValidationError("body must be an object with max_concurrency")
        limit = self.queue.limits.set_limit(
            params["queue"], validate_max_concurrency(body["max_concurrency"])
        )
        return Response(200, {"queue": limit.queue, "max_concurrency": limit.max_concurrency})

    def _list_limits(self, request: Request, params: dict[str, str]) -> Response:
        authenticate_admin(request.header("authorization"), self.config.admin_token)
        return Response(200, {"limits": self.queue.limits.all_limits()})

    def _metrics(self, request: Request, params: dict[str, str]) -> Response:
        authenticate_admin(request.header("authorization"), self.config.admin_token)
        return Response(200, self.metrics.snapshot(self.queue.clock()))

    def _health(self, request: Request, params: dict[str, str]) -> Response:
        return Response(200, {"status": "ok"})
