"""Gate H2 — the sandbox observed, not asserted (design spec §9.2, §9.5).

Every test here runs a real container and checks what actually happened. That is
the entire point of the gate: `profiles.py` builds arguments, and an argument is
a request. Whether the kernel granted it is a separate question, and until these
tests pass no document may describe §9.2 as verified.

The distinction is not pedantic. Writing these found that `/workspace/scratch`
— the one place a run is supposed to be able to write — was not writable at all,
because the tmpfs mounted over the image's `chown` and came up root-owned. The
flag had been correct since H1. The behaviour never was.

Marked `docker`, so the default suite stays runnable without a daemon.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coding_agent_eval.sandbox.profiles import MEASURE, ProfileError, build_run_argv
from coding_agent_eval.sandbox.run import (
    docker_available,
    resolve_digest,
    run_in_sandbox,
)

IMAGE_REF = (
    "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@"
    "sha256:db6a0afabe3acfd9c704e020b27a5b55ccef430b4864d8e565711b0b9cbc8966"
)
IMAGE_TAG = "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py:1.0.5"

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="no Docker daemon"),
]


@pytest.fixture(scope="module")
def image() -> str:
    try:
        return resolve_digest(IMAGE_REF)
    except RuntimeError as exc:  # pragma: no cover - environment dependent
        pytest.skip(str(exc))


def run(image: str, *command: str, timeout_seconds: int = 25) -> object:
    return run_in_sandbox(
        MEASURE, image=image, command=list(command), timeout_seconds=timeout_seconds
    )


# ------------------------------------------------------------------ network


def test_outbound_connections_fail(image: str) -> None:
    """`--network none` is requested; this checks it was granted.

    A container that can reach the network is one where the code under analysis
    can exfiltrate what it read, or fetch something to run.
    """
    result = run(
        image,
        "python3",
        "-c",
        "import socket\n"
        "socket.setdefaulttimeout(4)\n"
        "try:\n"
        "    socket.create_connection(('1.1.1.1', 53))\n"
        "    print('CONNECTED')\n"
        "except OSError as exc:\n"
        "    print('REFUSED', type(exc).__name__)\n",
    )
    assert "REFUSED" in result.stdout, result.as_dict()
    assert "CONNECTED" not in result.stdout


def test_dns_resolution_fails(image: str) -> None:
    """The other half: no route and no resolver."""
    result = run(
        image,
        "python3",
        "-c",
        "import socket\n"
        "try:\n"
        "    print('RESOLVED', socket.gethostbyname('example.com'))\n"
        "except OSError:\n"
        "    print('NO_RESOLVER')\n",
    )
    assert "NO_RESOLVER" in result.stdout, result.as_dict()


# --------------------------------------------------------------- filesystem


def test_the_root_filesystem_is_read_only(image: str) -> None:
    result = run(image, "sh", "-c", "touch /nope 2>&1 || echo REFUSED")
    assert "REFUSED" in result.stdout
    assert "Read-only file system" in result.stdout


def test_the_measured_tree_is_read_only(image: str) -> None:
    """A run must not be able to modify the tree it is being scored against."""
    result = run(image, "sh", "-c", "touch /workspace/evidence.txt 2>&1 || echo REFUSED")
    assert "REFUSED" in result.stdout


def test_tmp_is_writable(image: str) -> None:
    result = run(image, "sh", "-c", "echo x > /tmp/probe && cat /tmp/probe")
    assert result.ok, result.as_dict()
    assert result.stdout.strip() == "x"


def test_the_scratch_area_is_writable(image: str) -> None:
    """The place work is supposed to happen has to actually accept writes.

    This is the assertion that failed when the gate was first written: the
    tmpfs mounted over the image's `chown` and came up owned by root, so a
    container running as uid 1000 could not write to its own scratch area.
    """
    result = run(
        image, "sh", "-c", "echo x > /workspace/scratch/probe && cat /workspace/scratch/probe"
    )
    assert result.ok, result.as_dict()
    assert result.stdout.strip() == "x"


def test_no_host_path_is_visible(image: str, tmp_path: Path) -> None:
    """Nothing mounts, so a file on the host is not reachable by its host path."""
    marker = tmp_path / "host-marker.txt"
    marker.write_text("host side only\n", encoding="utf-8")
    posix = marker.as_posix()

    result = run(image, "sh", "-c", f"cat '{posix}' 2>&1 || echo NOT_VISIBLE")
    assert "NOT_VISIBLE" in result.stdout, result.as_dict()
    assert "host side only" not in result.stdout


def test_the_container_filesystem_has_no_mounted_host_directory(image: str) -> None:
    """Checked from inside: no bind mount appears in the mount table."""
    result = run(image, "sh", "-c", "cat /proc/mounts")
    for line in result.stdout.splitlines():
        assert "/mnt/host" not in line
        assert "/run/desktop" not in line


# --------------------------------------------------------------- privileges


def test_the_container_does_not_run_as_root(image: str) -> None:
    result = run(image, "id", "-u")
    assert result.ok, result.as_dict()
    assert result.stdout.strip() != "0"
    assert result.stdout.strip() == "1000"


def test_a_capability_requiring_operation_fails(image: str) -> None:
    """`--cap-drop ALL` requested; this observes CAP_CHOWN actually gone.

    Binding a privileged port was tried first and turned out to prove nothing:
    this kernel sets `ip_unprivileged_port_start=0` inside the namespace, so the
    bind succeeds without any capability at all. `chown` to another uid is a
    real capability check.
    """
    result = run(
        image,
        "python3",
        "-c",
        "import os\n"
        "try:\n"
        "    os.chown('/tmp', 0, 0)\n"
        "    print('CHOWNED')\n"
        "except PermissionError:\n"
        "    print('DENIED')\n",
    )
    assert "DENIED" in result.stdout, result.as_dict()


def test_privileges_cannot_be_regained(image: str) -> None:
    """`no-new-privileges`: a setuid binary must not raise the effective uid."""
    result = run(
        image,
        "sh",
        "-c",
        "su root -c 'id -u' 2>&1 || echo REFUSED",
    )
    assert "REFUSED" in result.stdout or "0" not in result.stdout.split()


# -------------------------------------------------------------------- limits


def test_the_pid_limit_contains_runaway_process_creation(image: str) -> None:
    """A fork bomb is contained by the container, not by the host noticing.

    Bounded at 2000 attempts so this test cannot itself become the fork bomb if
    the limit is ever removed — it would report NOLIMIT and fail, rather than
    running until something else stopped it.
    """
    result = run(
        image,
        "python3",
        "-c",
        "import os, threading, time\n"
        "n = 0\n"
        "try:\n"
        "    while n < 2000:\n"
        "        threading.Thread(target=lambda: time.sleep(50), daemon=True).start()\n"
        "        n += 1\n"
        "    print('NOLIMIT', n)\n"
        "except RuntimeError:\n"
        "    print('BLOCKED', n)\n"
        "os._exit(0)\n",
    )
    assert "BLOCKED" in result.stdout, result.as_dict()
    blocked_at = int(result.stdout.split()[1])
    assert blocked_at < MEASURE.pids_limit + 50, (
        f"stopped at {blocked_at}, limit {MEASURE.pids_limit}"
    )


def test_an_over_timeout_command_is_killed_and_reported(image: str) -> None:
    """The inner `timeout --signal=KILL` fires and the result says so."""
    result = run(image, "sleep", "60", timeout_seconds=3)
    assert result.timed_out, result.as_dict()
    assert result.exit_code == 137


def test_a_command_within_its_timeout_is_not_reported_as_timed_out(image: str) -> None:
    """The converse, so the timeout flag means something."""
    result = run(image, "sleep", "1", timeout_seconds=20)
    assert result.ok
    assert not result.timed_out


# ------------------------------------------------------------- housekeeping


def test_the_image_must_be_referenced_by_digest() -> None:
    """A tag can be repointed, so a result taken against one is not reproducible."""
    with pytest.raises(ProfileError, match="not pinned by digest"):
        build_run_argv(MEASURE, image=IMAGE_TAG, command=["true"])


def test_the_run_actually_used_a_digest(image: str) -> None:
    result = run(image, "true")
    assert image in result.argv
    assert IMAGE_REF not in result.argv


def test_no_container_is_left_behind(image: str) -> None:
    """`--rm` requested; this observes that nothing accumulated."""

    def container_count() -> int:
        proc = subprocess.run(
            ["docker", "ps", "--all", "--quiet"], capture_output=True, text=True, timeout=60
        )
        return len([line for line in proc.stdout.splitlines() if line.strip()])

    before = container_count()
    run(image, "true")
    run(image, "sh", "-c", "exit 3")
    assert container_count() == before


def test_a_failing_command_reports_its_exit_code(image: str) -> None:
    """Failures have to be distinguishable from timeouts and from success."""
    result = run(image, "sh", "-c", "exit 3")
    assert result.exit_code == 3
    assert not result.timed_out
    assert not result.ok
