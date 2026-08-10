"""Shared pytest configuration.

`REPO_ROOT` is resolved from this file rather than the working directory so tests
behave identically under pytest, CI, and an isolated install from a clean export.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        proc = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


DOCKER_AVAILABLE = _docker_available()

requires_docker = pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon is not reachable")


def _in_a_checkout() -> bool:
    """Whether the shipped fixtures have commit history to be rebuilt from.

    Gate G3 rebuilds a fixture tree from `HEAD`, so it needs a repository. An
    sdist, a wheel, and the clean export that `scripts/verify_release.sh` tests
    in all carry the fixtures without one. G3's *failure* cases build their own
    throwaway repositories and still run there; only the assertions about the
    shipped fixtures are skipped, and they are covered wherever history exists.
    """
    if shutil.which("git") is None:
        return False
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=REPO_ROOT,
            capture_output=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


IN_A_CHECKOUT = _in_a_checkout()

requires_checkout = pytest.mark.skipif(
    not IN_A_CHECKOUT, reason="the shipped fixtures have no commit history here"
)
