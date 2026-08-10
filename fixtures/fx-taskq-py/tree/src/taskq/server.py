"""HTTP server entry point.

`http.server` from the standard library, so the fixture has no third-party
dependency at all and its prepared image needs no package resolution.

Requests are served on worker threads, so `Storage` has to be usable from a
thread other than the one that opened it — see the note there. Writes serialise
on the storage write lock rather than here, because the transaction boundary is
where serialising actually matters.
"""

from __future__ import annotations

import argparse
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from taskq.api import Api, Request
from taskq.config import Config
from taskq.queue import TaskQueue
from taskq.storage import Storage

MAX_REQUEST_BYTES = 1 * 1024 * 1024


def build_api(config: Config) -> Api:
    storage = Storage(config.database)
    return Api(TaskQueue(storage, config), config)


class _Handler(BaseHTTPRequestHandler):
    api: Api

    protocol_version = "HTTP/1.1"
    server_version = "taskq"
    sys_version = ""

    #: A stalled request must not hold its worker thread indefinitely.
    #:
    #: The body read below is capped, which bounds how much memory one request
    #: can take. Nothing there bounds how long it waits for bytes a client
    #: promised and never sent; this does.
    timeout = 15

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        """Log to stderr, never stdout, which callers may be parsing."""
        sys.stderr.write(f"{self.address_string()} {format % args}\n")

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return b""
        # Bounded rather than trusting the header. The cap limits how much can
        # be read into memory; it does not by itself limit how long the read
        # waits, which is what `timeout` above is for.
        return self.rfile.read(min(max(length, 0), MAX_REQUEST_BYTES))

    def _dispatch(self, method: str) -> None:
        request = Request(
            method=method,
            path=self.path.split("?", 1)[0],
            body=self._read_body(),
            headers={key.lower(): value for key, value in self.headers.items()},
        )
        response = self.api.handle(request)

        # 204 means "no content", so there is no body and no Content-Type. The
        # length header has to go too: announcing bytes that are never written
        # leaves the client waiting for them, and on a keep-alive connection the
        # next response is then read as the tail of this one.
        if response.status == 204:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        encoded = response.encode()
        self.send_response(response.status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:  # noqa: N802 - http.server naming
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server naming
        self._dispatch("POST")


def build_server(config: Config) -> ThreadingHTTPServer:
    """Build a bound server without serving it, so a test can drive one."""
    handler = type("_BoundHandler", (_Handler,), {"api": build_api(config)})
    return ThreadingHTTPServer((config.host, config.port), handler)


def serve(config: Config) -> None:  # pragma: no cover - exercised by running it
    server = build_server(config)
    sys.stderr.write(f"taskq listening on {config.host}:{config.port}\n")
    server.serve_forever()


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin shell
    parser = argparse.ArgumentParser(prog="taskq")
    parser.add_argument("--database", default=None)
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args(argv)

    config = Config.from_environment()
    if args.database:
        config = Config(**{**config.__dict__, "database": args.database})
    if args.host:
        config = Config(**{**config.__dict__, "host": args.host})
    if args.port:
        config = Config(**{**config.__dict__, "port": args.port})

    serve(config)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
