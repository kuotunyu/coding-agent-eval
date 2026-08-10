"""Execute one command under a sandbox profile (design spec §9.2).

`profiles.py` builds arguments. This runs them, which is a different claim: a
flag in an argv is a request, and only running it shows whether the kernel
agreed. Gate H2 exists to make that distinction observable, and this module is
what it observes through.

**The timeout is two layers, deliberately.** Inside the container, `timeout
--signal=KILL` bounds the command. Outside it, a subprocess timeout bounds the
container. The inner one is the one that reports cleanly; the outer one exists
because the inner one cannot fire if the container never starts, or if the
process that should enforce it is itself what wedged. One layer would leave a
class of hang unbounded.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Any

from coding_agent_eval.sandbox.profiles import SandboxProfile, build_run_argv

#: How much longer the host waits than the container. Enough that the inner
#: timeout normally wins and reports, not so much that a wedged container holds
#: a test run for long.
_HOST_GRACE_SECONDS = 15

#: Exit code `timeout` uses when it had to send the signal.
TIMEOUT_EXIT_CODE = 137


@dataclass(frozen=True)
class SandboxResult:
    """What running one command produced."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    argv: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "stdout": self.stdout.strip(),
            "stderr": self.stderr.strip(),
        }


def run_in_sandbox(
    profile: SandboxProfile,
    *,
    image: str,
    command: list[str],
    timeout_seconds: int = 30,
    environment: dict[str, str] | None = None,
    workdir: str = "/workspace",
) -> SandboxResult:
    """Run `command` under `profile` and report what happened.

    Returns rather than raises for a non-zero exit: a command that fails is the
    normal case here, because most of what H2 checks is that things which
    should be refused are refused.
    """
    bounded = ["timeout", "--signal=KILL", str(timeout_seconds), *command]
    argv = build_run_argv(
        profile,
        image=image,
        command=bounded,
        workdir=workdir,
        environment=environment,
    )

    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_seconds + _HOST_GRACE_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # The container outlived both bounds. Reported, not raised: a wedged
        # container is an observation about the sandbox, which is the subject.
        return SandboxResult(
            exit_code=-1,
            stdout="",
            stderr=f"host timeout after {timeout_seconds + _HOST_GRACE_SECONDS}s",
            timed_out=True,
            argv=tuple(argv),
        )

    return SandboxResult(
        exit_code=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        timed_out=proc.returncode == TIMEOUT_EXIT_CODE,
        argv=tuple(argv),
    )


def docker_available() -> bool:
    """Whether a daemon is reachable. Used to skip, never to pass."""
    try:
        return (
            subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def docker_facts() -> dict[str, str]:
    """Daemon version and platform, for the verification record."""
    fields = {
        "docker_server_version": "{{.Server.Version}}",
        "docker_api_version": "{{.Server.APIVersion}}",
        "platform": "{{.Server.Os}}/{{.Server.Arch}}",
        "kernel": "{{.Server.KernelVersion}}",
    }
    facts: dict[str, str] = {}
    for name, template in fields.items():
        proc = subprocess.run(
            ["docker", "version", "--format", template],
            capture_output=True,
            text=True,
            timeout=30,
        )
        facts[name] = proc.stdout.strip() if proc.returncode == 0 else "unknown"
    return facts


def resolve_digest(tag: str) -> str:
    """Resolve a local image tag to its digest, so a run can be pinned to it."""
    proc = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"image {tag!r} is not present locally: {proc.stderr.strip()}")
    return proc.stdout.strip()
