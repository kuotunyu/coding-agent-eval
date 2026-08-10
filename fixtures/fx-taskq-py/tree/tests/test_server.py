"""The HTTP server itself.

Everywhere else drives `Api` directly, which is the right way to test routing
and policy but says nothing about the server wrapped around it. Two things are
only visible from the socket: whether a request that touches storage works at
all, and whether a response frames itself correctly on a keep-alive connection.

Both were broken while this file did not exist.
"""

from __future__ import annotations

import json
import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from taskq.config import Config
from taskq.server import build_server


@pytest.fixture
def server(tmp_path: Path) -> Iterator[tuple[str, int]]:
    """A real server on a real port, shut down at the end of the test."""
    config = Config(
        database=str(tmp_path / "taskq.db"),
        host="127.0.0.1",
        port=0,  # let the OS choose, so tests never collide on a fixed port
        admin_token="s3cret-admin-token",
    )
    httpd = build_server(config)
    address = (str(httpd.server_address[0]), int(httpd.server_address[1]))
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield address
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def send(sock: socket.socket, raw: bytes) -> str:
    """Send one request and read the whole response, headers and body.

    Stopping at the blank line would leave the body in the socket, and the next
    call would parse it as a status line — the very confusion the framing tests
    exist to detect.
    """
    sock.sendall(raw)
    sock.settimeout(5)
    data = b""
    try:
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                return data.decode("latin1")
            data += chunk

        head, _, body = data.partition(b"\r\n\r\n")
        length = 0
        for line in head.decode("latin1").splitlines()[1:]:
            name, _, value = line.partition(":")
            if name.strip().lower() == "content-length":
                length = int(value.strip())
        while len(body) < length:
            chunk = sock.recv(4096)
            if not chunk:
                break
            body += chunk
    except socket.timeout:
        return ""
    return (head + b"\r\n\r\n" + body).decode("latin1")


def status_of(response: str) -> int:
    return int(response.split()[1]) if response else 0


def post(path: str, payload: object) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return (
        f"POST {path} HTTP/1.1\r\nHost: x\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode("latin1") + body


def test_health_answers(server: tuple[str, int]) -> None:
    with socket.create_connection(server, timeout=5) as sock:
        assert status_of(send(sock, b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")) == 200


def test_a_request_that_touches_storage_works(server: tuple[str, int]) -> None:
    """Every route except /health goes through SQLite.

    A SQLite connection may only be used from the thread that opened it unless
    it is opened otherwise, so a server that serves on a different thread from
    the one that built `Storage` fails on every one of those routes while
    /health stays green — which is exactly how this went unnoticed.
    """
    with socket.create_connection(server, timeout=5) as sock:
        response = send(sock, post("/queues/emails/tasks", {"payload": {"n": 1}}))
        assert status_of(response) == 201


def test_every_storage_route_survives_being_served(server: tuple[str, int]) -> None:
    """Not one route: the failure was in how requests are dispatched."""
    with socket.create_connection(server, timeout=5) as sock:
        for path in ("/admin/stats", "/tasks/" + "0" * 32):
            response = send(sock, f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode("latin1"))
            # 401 and 404 are fine. 500 would mean the request never got served.
            assert status_of(response) != 500, path


def test_a_204_does_not_announce_a_body_it_will_not_send(server: tuple[str, int]) -> None:
    """Leasing an empty queue returns 204.

    A Content-Length larger than the body leaves the client waiting for bytes
    that never arrive.
    """
    with socket.create_connection(server, timeout=5) as sock:
        response = send(sock, b"POST /queues/empty/lease HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n")
        assert status_of(response) == 204
        assert "Content-Length: 0" in response


def test_a_connection_survives_a_204(server: tuple[str, int]) -> None:
    """The framing fault only shows on the *next* request over the same socket."""
    lease = b"POST /queues/empty/lease HTTP/1.1\r\nHost: x\r\nContent-Length: 0\r\n\r\n"
    with socket.create_connection(server, timeout=5) as sock:
        assert status_of(send(sock, lease)) == 204
        assert status_of(send(sock, b"GET /health HTTP/1.1\r\nHost: x\r\n\r\n")) == 200


def test_two_requests_share_one_connection(server: tuple[str, int]) -> None:
    """Keep-alive with bodies: the second response must not be read as the first's."""
    with socket.create_connection(server, timeout=5) as sock:
        first = send(sock, post("/queues/emails/tasks", {"payload": {"n": 1}}))
        second = send(sock, post("/queues/emails/tasks", {"payload": {"n": 2}}))
        assert status_of(first) == 201
        assert status_of(second) == 201


def test_a_payload_over_the_cap_is_rejected_with_413(server: tuple[str, int]) -> None:
    """The README states this explicitly, and nothing else checked it."""
    with socket.create_connection(server, timeout=10) as sock:
        response = send(sock, post("/queues/emails/tasks", {"payload": {"pad": "x" * 70_000}}))
        assert status_of(response) == 413
