"""Gate G5 — the sanitizer fails closed (design spec §10.5).

The sanitizer is the only route from private evidence to a published artifact,
so its failure mode decides what a leak costs. Best-effort redaction fails open:
the file still gets written, and whatever the rules missed is now public. This
one refuses instead — non-zero exit, no output path, no partial file.

The "no partial file" part matters more than it looks. A half-written artifact
left behind after a rejection is exactly what a later step picks up and
publishes, so every test here asserts the output directory is empty afterwards,
not merely that an exception was raised.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from coding_agent_eval.hygiene.policy import OFFICIAL_PUBLIC_EMAIL
from coding_agent_eval.trace.sanitizer import SanitizerError, sanitize_events

from ..hygiene.corpus import (
    POSITIVE,
    SAMPLE_API_KEY,
    SAMPLE_POSIX_PATH,
)

CLEAN_HEADER: dict[str, Any] = {
    "run_id": "run-1",
    "benchmark_version": "0.1.0",
    "fixture_id": "fx-taskq-py",
    "fixture_version": "1.0.0",
    "fixture_tree_checksum": "sha256:" + "a" * 64,
    "snapshot": "mutated",
    "bug_set_hash": "b" * 64,
    "agent_adapter": "deterministic",
    "agent_adapter_version": "0.1.0",
    "provider": None,
    "model": None,
    "prompt_hash": "c" * 64,
    "system_prompt_version": "0.1.0",
    "params_hash": "d" * 64,
    "seed": 7,
    "image_ref": ("ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@sha256:" + "e" * 64),
    "image_manifest_digest": "sha256:" + "e" * 64,
    "image_config_digest": "sha256:" + "1" * 64,
    "env_fingerprint": "sha256:" + "f" * 64,
    "sandbox_profile": "measure",
    "tool_backend": "measure_container:sha256:" + "e" * 64,
    "budget": {"max_tokens": 200000},
    "redaction_manifest_version": "0.1.0",
}


def event(seq: int, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    return {"seq": seq, "ts": "2026-08-05T00:00:00+00:00", "event": name, "payload": payload}


def clean_events() -> list[dict[str, Any]]:
    return [
        event(0, "run_header", dict(CLEAN_HEADER)),
        event(
            1,
            "tool_result",
            {
                "is_error": False,
                "content_sha256": "a" * 64,
                "content_bytes": 128,
                "excerpt": "exit code: 0",
                "excerpt_policy": "harness",
            },
        ),
        event(
            2,
            "termination",
            {"reason": "completed", "steps": 4, "tool_calls": 3, "wall_clock_ms": 900},
        ),
    ]


def poisoned(excerpt: str) -> list[dict[str, Any]]:
    events = clean_events()
    events[1]["payload"]["excerpt"] = excerpt
    return events


def assert_nothing_written(out: Path) -> None:
    assert not out.exists(), "a rejected artifact must leave no output file"
    leftovers = [p.name for p in out.parent.iterdir()] if out.parent.exists() else []
    assert leftovers == [], f"partial files left behind: {leftovers}"


# ------------------------------------------------------------- happy path


def test_a_clean_artifact_is_written(tmp_path: Path) -> None:
    out = tmp_path / "out" / "trace.jsonl"
    sanitize_events(clean_events(), out)
    assert out.is_file()
    assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 3


def test_output_is_deterministic(tmp_path: Path) -> None:
    """Replay compares byte for byte, so two runs of the sanitizer must agree."""
    first = tmp_path / "a" / "trace.jsonl"
    second = tmp_path / "b" / "trace.jsonl"
    sanitize_events(clean_events(), first)
    sanitize_events(clean_events(), second)
    assert first.read_bytes() == second.read_bytes()


def test_a_known_private_field_is_dropped_not_rejected(tmp_path: Path) -> None:
    """Dropping is the decision; only unclassified fields are a failure."""
    events = clean_events()
    events[1]["payload"]["content"] = "the full file content, private by design"
    out = tmp_path / "out" / "trace.jsonl"
    sanitize_events(events, out)
    assert "the full file content" not in out.read_text(encoding="utf-8")


# ----------------------------------------------------- §10.5 rules, 2+ each

#: Reused from the hygiene corpus, which is the one path the tracked-file scanner
#: skips. Restating the samples here would make this file trip gate G11, and
#: keeping one copy also means the sanitizer and the scanner are tested against
#: exactly the same inputs.
POISON_SAMPLES: list[tuple[str, str]] = POSITIVE


@pytest.mark.parametrize(("rule", "sample"), POISON_SAMPLES)
def test_poisoned_content_is_rejected_and_writes_nothing(
    tmp_path: Path, rule: str, sample: str
) -> None:
    out = tmp_path / "out" / "trace.jsonl"
    with pytest.raises(SanitizerError) as exc:
        sanitize_events(poisoned(sample), out)
    assert rule in str(exc.value)
    assert_nothing_written(out)


def test_every_named_rule_has_at_least_two_samples() -> None:
    from coding_agent_eval.hygiene.patterns import RULE_NAMES

    counts = {rule: sum(1 for r, _ in POISON_SAMPLES if r == rule) for rule in RULE_NAMES}
    assert all(n >= 2 for n in counts.values()), counts


def test_the_official_email_is_rejected_here(tmp_path: Path) -> None:
    """Allowed in tracked files, never in a run artifact (correction 7, spec §10.8)."""
    out = tmp_path / "out" / "trace.jsonl"
    with pytest.raises(SanitizerError, match="email"):
        sanitize_events(poisoned(f"authored by {OFFICIAL_PUBLIC_EMAIL}"), out)
    assert_nothing_written(out)


def test_a_leak_in_any_event_rejects_the_whole_artifact(tmp_path: Path) -> None:
    """Partial publication is not a safe fallback."""
    events = clean_events()
    events[0]["payload"]["sandbox_profile"] = SAMPLE_POSIX_PATH
    out = tmp_path / "out" / "trace.jsonl"
    with pytest.raises(SanitizerError):
        sanitize_events(events, out)
    assert_nothing_written(out)


# ------------------------------------------------------- unknown raw fields


def test_an_unknown_raw_field_rejects_the_artifact(tmp_path: Path) -> None:
    events = clean_events()
    events[1]["payload"]["newly_added_field"] = "harmless looking"
    out = tmp_path / "out" / "trace.jsonl"
    with pytest.raises(SanitizerError, match="newly_added_field"):
        sanitize_events(events, out)
    assert_nothing_written(out)


def test_an_unknown_event_type_rejects_the_artifact(tmp_path: Path) -> None:
    out = tmp_path / "out" / "trace.jsonl"
    with pytest.raises(SanitizerError):
        sanitize_events([*clean_events(), event(3, "brand_new_event", {})], out)
    assert_nothing_written(out)


# ----------------------------------------------------------------- atomicity


def test_an_existing_output_is_not_clobbered_by_a_rejected_run(tmp_path: Path) -> None:
    """A failed re-sanitise must not destroy the artifact already published."""
    out = tmp_path / "out" / "trace.jsonl"
    sanitize_events(clean_events(), out)
    original = out.read_bytes()

    with pytest.raises(SanitizerError):
        sanitize_events(poisoned(SAMPLE_POSIX_PATH), out)
    assert out.read_bytes() == original


def test_no_temporary_file_survives_a_rejection(tmp_path: Path) -> None:
    out = tmp_path / "out" / "trace.jsonl"
    with pytest.raises(SanitizerError):
        sanitize_events(poisoned(SAMPLE_API_KEY), out)
    assert_nothing_written(out)
