"""Regenerate the golden replay inputs.

Run from the repository root:

    uv run python tests/evaluator/golden/build_golden.py

The trace is produced by the real sanitizer rather than written by hand, so the
golden artifact is one the pipeline could actually emit. A hand-written trace
would drift from the projection rules and quietly stop testing them.

The ledger here is synthetic, and everything scored from it carries
`publishable: false`. These numbers validate the evaluator's arithmetic; they
say nothing about any model.
"""

from __future__ import annotations

import json
from pathlib import Path

from coding_agent_eval.evaluator.hashing import finding_hash
from coding_agent_eval.evaluator.ledger import (
    SYNTHETIC_PREFIX,
    LedgerKey,
    build_entry,
    write_entries,
)
from coding_agent_eval.evaluator.replay import replay_run

GOLDEN = Path(__file__).parent
FIXTURE_VERSION = "1.0.0"
TREE_CHECKSUM = "sha256:" + "1a" * 32

FIXTURE = {
    "fixture_id": "fx-demo-py",
    "fixture_version": FIXTURE_VERSION,
    "tree_checksum": TREE_CHECKSUM,
    "in_scope_paths": ["src/**"],
    "out_of_scope_paths": ["tests/**"],
    "in_scope_loc": 2000,
}

BUGS = [
    {
        "bug_id": "fx-demo-py/B-001",
        "category": "security",
        "compound_group": None,
        "canonical_claim": "Session token comparison is not constant time.",
        "canonical_root_cause": "Uses == on secrets, so timing depends on the prefix.",
        "localization": {
            "primary": {"file": "src/demo/auth.py", "line_start": 100, "line_end": 110},
            "line_tolerance": 8,
            "acceptable_alternates": [],
        },
    },
    {
        "bug_id": "fx-demo-py/B-002",
        "category": "correctness",
        "compound_group": None,
        "canonical_claim": "The retry counter is compared with the wrong operator.",
        "canonical_root_cause": "Uses <= where < is required, allowing one extra attempt.",
        "localization": {
            "primary": {"file": "src/demo/queue.py", "line_start": 40, "line_end": 44},
            "line_tolerance": 8,
            "acceptable_alternates": [],
        },
    },
]

FINDINGS = [
    {
        "id": "F-001",
        "file": "src/demo/auth.py",
        "line_start": 104,
        "line_end": 104,
        "category": "security",
        "severity": "high",
        "claim": "Token comparison leaks timing information.",
        "root_cause": "The comparison short-circuits on the first differing byte.",
        "evidence": "auth.py:104 compares the token with ==.",
        "suggested_verification": "Time the call against prefixes of a valid token.",
    },
    {
        "id": "F-002",
        "file": "src/demo/worker.py",
        "line_start": 12,
        "line_end": 12,
        "category": "correctness",
        "severity": "low",
        "claim": "The worker loop has no backoff.",
        "root_cause": "Retries immediately on failure.",
        "evidence": "worker.py:12 loops without sleeping.",
        "suggested_verification": "Observe the retry interval under a forced failure.",
    },
]

RAW_EVENTS = [
    {
        "seq": 0,
        "ts": "2026-08-05T00:00:00+00:00",
        "event": "run_header",
        "payload": {
            "run_id": "golden-deterministic-001",
            "benchmark_version": "0.1.0",
            "fixture_id": FIXTURE["fixture_id"],
            "fixture_version": FIXTURE_VERSION,
            "fixture_tree_checksum": TREE_CHECKSUM,
            "snapshot": "mutated",
            "bug_set_hash": "2b" * 32,
            "agent_adapter": "deterministic",
            "agent_adapter_version": "0.1.0",
            "provider": None,
            "model": None,
            "prompt_hash": "3c" * 32,
            "system_prompt_version": "0.1.0",
            "params_hash": "4d" * 32,
            "seed": 0,
            "image_digest": "sha256:" + "5e" * 32,
            "env_fingerprint": "sha256:" + "6f" * 32,
            "sandbox_profile": "measure",
            "tool_backend": "measure_container:sha256:" + "5e" * 32,
            "budget": {
                "max_tokens": None,
                "max_tool_calls": 25,
                "max_wallclock_seconds": None,
                "max_estimated_cost_usd": None,
            },
            "redaction_manifest_version": "0.1.0",
            "system_prompt": "private: never published",
        },
    },
    {
        "seq": 1,
        "ts": "2026-08-05T00:00:01+00:00",
        "event": "llm_call",
        "payload": {
            "request_hash": "7a" * 32,
            "latency_ms": 0,
            "finish_reason": "stop",
            "usage": {
                "input_tokens": 40000,
                "cached_input_tokens": None,
                "output_tokens": 10000,
                "reasoning_tokens": None,
            },
            "response_body": "private: never published",
        },
    },
    {
        "seq": 2,
        "ts": "2026-08-05T00:00:02+00:00",
        "event": "tool_result",
        "payload": {
            "is_error": False,
            "content_sha256": "8b" * 32,
            "content_bytes": 512,
            "excerpt": "exit code: 0",
            "excerpt_policy": "harness",
            "content": "private: the whole tool output",
        },
    },
    {
        "seq": 3,
        "ts": "2026-08-05T00:00:03+00:00",
        "event": "findings_submitted",
        "payload": {"findings": FINDINGS},
    },
    {
        "seq": 4,
        "ts": "2026-08-05T00:00:04+00:00",
        "event": "cost",
        "payload": {
            "estimated_cost_usd": None,
            "completeness": "partial",
            "unknown_fields": ["estimated_cost_usd"],
            "pricing_table_version": "none-offline",
            "pricing_effective_date": "2026-08-05",
            "pricing_source": "not applicable: deterministic offline golden",
            "estimator_limitations": ["the deterministic golden uses no priced provider"],
        },
    },
    {
        "seq": 5,
        "ts": "2026-08-05T00:00:05+00:00",
        "event": "termination",
        "payload": {"reason": "completed", "steps": 6, "tool_calls": 5, "wall_clock_ms": 0},
    },
]

#: F-001 matches B-001; F-002 matches nothing. One ruling is therefore enough.
RULINGS = [(FINDINGS[0], BUGS[0], "same_root_cause")]


def _write(path: Path, text: str) -> None:
    """Write with LF endings on every platform.

    The default translates to `os.linesep`, so these committed artifacts would
    otherwise carry different bytes on Windows and Linux — and replay compares
    them byte for byte.
    """
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)


def main() -> None:
    from coding_agent_eval.trace.allowlist import PUBLIC_FIELDS

    GOLDEN.mkdir(parents=True, exist_ok=True)

    _write(GOLDEN / "fixture.json", json.dumps(FIXTURE, indent=2, sort_keys=True) + "\n")
    _write(GOLDEN / "bugs.json", json.dumps(BUGS, indent=2, sort_keys=True) + "\n")

    write_entries(
        GOLDEN / "synthetic_adjudications.jsonl",
        [
            build_entry(
                key=LedgerKey(
                    fixture_version=FIXTURE_VERSION,
                    bug_id=bug["bug_id"],
                    finding_hash=finding_hash(finding),
                ),
                decision=decision,
                rationale="Synthetic fixture: evaluator arithmetic only, not a human ruling.",
                adjudicator_id=f"{SYNTHETIC_PREFIX}golden",
                decided_at="2026-08-05",
            )
            for finding, bug, decision in RULINGS
        ],
    )

    # This fixture is deliberately the frozen 0.1 read-compatibility artifact.
    # Current sanitizers emit 0.2 only; routing these historical raw fields
    # through that writer would either migrate the golden or make regeneration
    # impossible. Keep the old envelope explicit and byte-deterministic.
    _write(
        GOLDEN / "trace.jsonl",
        "".join(
            json.dumps(
                {
                    "schema_version": "0.1.0",
                    **record,
                    "payload": {
                        field: value
                        for field, value in record["payload"].items()
                        if field in PUBLIC_FIELDS[record["event"]]
                        or (record["event"] == "run_header" and field == "image_digest")
                    },
                },
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
            for record in RAW_EVENTS
        ),
    )

    result = replay_run(
        trace_path=GOLDEN / "trace.jsonl",
        fixture_path=GOLDEN / "fixture.json",
        bugs_path=GOLDEN / "bugs.json",
        ledger_path=GOLDEN / "synthetic_adjudications.jsonl",
    )
    _write(
        GOLDEN / "expected_results.json",
        json.dumps(result.as_dict(), indent=2, sort_keys=True) + "\n",
    )
    print("golden artifacts regenerated")


if __name__ == "__main__":
    main()
