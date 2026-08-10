"""Private raw evidence store (design spec §10.1).

This holds the full record of a run: every tool call, every response, in the
form it actually took. It is the thing that makes a disputed result checkable,
and simultaneously the thing that must never be published — it can contain
third-party source, host paths, and provider responses.

So the store has exactly two jobs. Keep everything, append-only, so the record
cannot be quietly rewritten. And stay out of version control, so the decision to
publish is always an explicit pass through the sanitizer rather than a default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent_eval.agent.loop import Recorder
from coding_agent_eval.trace.raw_store import RawStore, RawStoreError

RUN_ID = "run-2026-08-05-001"


@pytest.fixture
def store(tmp_path: Path) -> RawStore:
    return RawStore(tmp_path / ".run-store", run_id=RUN_ID)


# ------------------------------------------------------------- append-only


def test_events_are_recorded_in_order(store: RawStore) -> None:
    store.append(0, "run_header", {"fixture_id": "fx-taskq-py"})
    store.append(1, "tool_call", {"tool_name": "read_file"})
    assert [e["event"] for e in store.read_events()] == ["run_header", "tool_call"]


def test_a_repeated_sequence_number_is_rejected(store: RawStore) -> None:
    """Append-only means a record cannot be replaced by a later one."""
    store.append(0, "run_header", {})
    with pytest.raises(RawStoreError, match="0"):
        store.append(0, "tool_call", {})


def test_a_sequence_number_going_backwards_is_rejected(store: RawStore) -> None:
    store.append(0, "run_header", {})
    store.append(1, "tool_call", {})
    with pytest.raises(RawStoreError):
        store.append(1, "tool_result", {})


def test_events_survive_a_reopened_store(tmp_path: Path) -> None:
    """A crash mid-run must not lose what came before it."""
    root = tmp_path / ".run-store"
    RawStore(root, run_id=RUN_ID).append(0, "run_header", {"a": 1})
    reopened = RawStore(root, run_id=RUN_ID)
    reopened.append(1, "tool_call", {"b": 2})
    assert len(reopened.read_events()) == 2


def test_reopening_still_refuses_an_already_used_sequence_number(tmp_path: Path) -> None:
    root = tmp_path / ".run-store"
    RawStore(root, run_id=RUN_ID).append(0, "run_header", {})
    with pytest.raises(RawStoreError):
        RawStore(root, run_id=RUN_ID).append(0, "tool_call", {})


def test_runs_are_isolated_from_each_other(tmp_path: Path) -> None:
    root = tmp_path / ".run-store"
    RawStore(root, run_id="run-a").append(0, "run_header", {})
    other = RawStore(root, run_id="run-b")
    other.append(0, "run_header", {})
    assert len(other.read_events()) == 1


def test_recorder_sink_preserves_the_exact_record(store: RawStore) -> None:
    recorder = Recorder(timestamp=lambda: "2026-08-10T00:00:00.000+00:00", sink=store.append_record)

    recorder.emit("run_header", {"fixture_id": "fx-taskq-py"})

    assert store.read_events() == recorder.events


# --------------------------------------------------------- content addressing


def test_a_blob_round_trips(store: RawStore) -> None:
    digest = store.put_blob(b"exit code: 0\nall tests passed\n")
    assert store.get_blob(digest) == b"exit code: 0\nall tests passed\n"


def test_identical_content_yields_one_stored_copy(store: RawStore) -> None:
    """Tool outputs repeat constantly; storing each copy would waste the disk."""
    first = store.put_blob(b"same bytes")
    second = store.put_blob(b"same bytes")
    assert first == second
    assert store.blob_count() == 1


def test_different_content_yields_different_digests(store: RawStore) -> None:
    assert store.put_blob(b"a") != store.put_blob(b"b")


def test_digest_is_the_sha256_of_the_content(store: RawStore) -> None:
    """The digest also appears in the public trace, so it must be verifiable."""
    import hashlib

    payload = b"deterministic content"
    assert store.put_blob(payload) == hashlib.sha256(payload).hexdigest()


def test_an_unknown_digest_raises(store: RawStore) -> None:
    with pytest.raises(RawStoreError):
        store.get_blob("0" * 64)


def test_empty_content_is_storable(store: RawStore) -> None:
    """An empty tool output is a real observation, not a missing one."""
    digest = store.put_blob(b"")
    assert store.get_blob(digest) == b""


# ------------------------------------------------------------------ retention


def test_prune_removes_runs_older_than_the_window(tmp_path: Path) -> None:
    import os
    import time

    root = tmp_path / ".run-store"
    old = RawStore(root, run_id="run-old")
    old.append(0, "run_header", {})
    fresh = RawStore(root, run_id="run-new")
    fresh.append(0, "run_header", {})

    ancient = time.time() - (40 * 86400)
    os.utime(old.run_dir, (ancient, ancient))
    os.utime(old.events_path, (ancient, ancient))

    removed = RawStore.prune(root, retention_days=30)
    assert removed == ["run-old"]
    assert not old.run_dir.exists()
    assert fresh.run_dir.exists()


def test_prune_keeps_everything_inside_the_window(tmp_path: Path) -> None:
    root = tmp_path / ".run-store"
    RawStore(root, run_id="run-new").append(0, "run_header", {})
    assert RawStore.prune(root, retention_days=30) == []


def test_prune_on_a_missing_store_is_not_an_error(tmp_path: Path) -> None:
    assert RawStore.prune(tmp_path / "absent", retention_days=30) == []


# --------------------------------------------------------- never published


def test_the_store_directory_is_gitignored(repo_root: Path) -> None:
    """The whole privacy boundary rests on this one line staying present."""
    ignored = (repo_root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".run-store/" in [line.strip() for line in ignored]


def test_the_store_is_not_tracked_by_git(repo_root: Path) -> None:
    import subprocess

    tracked = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", ".run-store"],
        capture_output=True,
        text=True,
    )
    assert tracked.stdout.strip() == ""
