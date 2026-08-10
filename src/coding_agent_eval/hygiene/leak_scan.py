"""Gate G11 — scan version-controlled content for leaks.

The subject is **what a commit would contain**: files Git already tracks, plus
files it does not yet track and would not ignore. Not the whole working tree —
ignored build output and a real `.env` would only generate noise that trains
people to ignore the gate.

Untracked-but-not-ignored files were originally excluded on that same reasoning,
and that was wrong. A file Git would add on the next `git add -A` is a file about
to be published, and a **new** file is the most likely place for a pasted
credential to be sitting. Excluding them left the gate blind precisely where it
needed to see: a new test file with a key-shaped constant passed this scan while
untracked and only failed once the commit adding it had already been made.
"""

from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

from coding_agent_eval.hygiene.patterns import Finding
from coding_agent_eval.hygiene.policy import TRACKED_FILE_POLICY, HygienePolicy

_BINARY_SNIFF_BYTES = 8192


class LeakScanError(RuntimeError):
    """The scan could not be performed (so its result must not be read as 'clean')."""


def _tracked_files(root: Path) -> list[Path]:
    """Every file a commit would contain, tracked or merely about to be.

    `--exclude-standard` keeps `.gitignore` in force, so a real `.env` is never
    read: it cannot be committed, so it is not this gate's business.
    """
    try:
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:  # pragma: no cover - env guard
        raise LeakScanError(f"could not list tracked files in {root}: {exc}") from exc
    names = proc.stdout.decode("utf-8", "replace").split("\0")
    return [root / name for name in names if name]


def _read_text(path: Path) -> str | None:
    """Return decoded text, or None when the file is binary or unreadable."""
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:_BINARY_SNIFF_BYTES]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_paths(
    paths: list[Path],
    *,
    root: Path,
    policy: HygienePolicy = TRACKED_FILE_POLICY,
) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        rel = path.relative_to(root).as_posix()
        if policy.excludes(rel):
            continue
        text = _read_text(path)
        if text is None:
            continue
        findings.extend(replace(f, path=rel) for f in policy.findings(text))
    return findings


def scan_tracked_files(
    root: Path,
    *,
    policy: HygienePolicy = TRACKED_FILE_POLICY,
) -> list[Finding]:
    return scan_paths(_tracked_files(root), root=root, policy=policy)
