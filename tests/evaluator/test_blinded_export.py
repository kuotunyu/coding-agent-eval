"""Blinded adjudication export (design spec §8.3).

The adjudicator is asked one question: does this finding describe this defect,
with support the code actually provides. Anything else in view is a route to a
biased answer — knowing the model invites brand expectations, knowing the cost
invites effort-justification, seeing neighbouring rulings invites consistency
with oneself rather than with the evidence.

So the export is an allowlist, not a redaction pass. Each forbidden field is
asserted absent individually, because a blanket "no leakage" assertion passes
just as well when the export is empty as when it is correct.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from coding_agent_eval.evaluator.blinded_export import (
    BlindingError,
    export_batch,
    import_decisions,
)
from coding_agent_eval.evaluator.ledger import SYNTHETIC_PREFIX, Decision, LedgerKind, load_ledger

BUG_A: dict[str, Any] = {
    "bug_id": "fx-taskq-py/B-001",
    "category": "security",
    "canonical_claim": "Session token comparison is not constant time.",
    "canonical_root_cause": "Uses == on secrets, so timing depends on the prefix.",
    "localization": {
        "primary": {"file": "src/taskq/auth.py", "line_start": 100, "line_end": 102},
        "line_tolerance": 8,
        "acceptable_alternates": [],
    },
}

BUG_B: dict[str, Any] = {
    **BUG_A,
    "bug_id": "fx-taskq-py/B-002",
    "canonical_claim": "Queue depth is read before the lock is taken.",
    "canonical_root_cause": "Check-then-act across a lock boundary.",
}

FINDING_A: dict[str, Any] = {
    "id": "F-001",
    "file": "src/taskq/auth.py",
    "line_start": 100,
    "line_end": 102,
    "category": "security",
    "severity": "high",
    "claim": "Token comparison leaks timing information.",
    "root_cause": "Short-circuits on the first differing byte.",
    "evidence": "auth.py:101 uses == on the token.",
    "suggested_verification": "Time it against prefixes.",
}

FINDING_B: dict[str, Any] = {**FINDING_A, "id": "F-002", "claim": "A second, different claim."}

RUN_CONTEXT: dict[str, Any] = {
    "run_id": "run-2026-08-05-abc",
    "provider": "openai",
    "model": "gpt-5-mini",
    "agent_adapter": "provider_loop",
    "agent_adapter_version": "0.1.0",
    "budget": {"max_tokens": 200000},
    "estimated_cost_usd": 0.031,
    "input_tokens": 120000,
    "latency_ms": 48000,
}

EXCERPTS = {"src/taskq/auth.py": "100: if token == expected:\n101:     return True\n"}


def build(pairs: list[tuple[dict, dict]] | None = None) -> tuple[Any, Any]:
    return export_batch(
        pairs if pairs is not None else [(FINDING_A, BUG_A)],
        fixture_version="1.0.0",
        language="python",
        excerpts=EXCERPTS,
        run_context=RUN_CONTEXT,
    )


FORBIDDEN_SUBSTRINGS = [
    "run-2026-08-05-abc",
    "openai",
    "gpt-5-mini",
    "provider_loop",
    "0.031",
    "120000",
    "48000",
    "fx-taskq-py/B-001",
]


def test_export_produces_one_item_per_pair() -> None:
    batch, _ = build([(FINDING_A, BUG_A), (FINDING_B, BUG_B)])
    assert len(batch.items) == 2


def test_items_are_numbered_opaquely_from_one() -> None:
    batch, _ = build([(FINDING_A, BUG_A), (FINDING_B, BUG_B)])
    assert [item["seq"] for item in batch.items] == [1, 2]


def test_the_adjudicator_sees_what_is_needed_to_rule() -> None:
    batch, _ = build()
    item = batch.items[0]
    assert item["language"] == "python"
    assert item["bug_claim"] == BUG_A["canonical_claim"]
    assert item["bug_root_cause"] == BUG_A["canonical_root_cause"]
    assert item["finding_claim"] == FINDING_A["claim"]
    assert item["finding_root_cause"] == FINDING_A["root_cause"]
    assert item["finding_evidence"] == FINDING_A["evidence"]
    assert "token ==" in item["code_excerpt"]


@pytest.mark.parametrize("field", ["run_id", "provider", "model", "agent_adapter", "budget"])
def test_run_identity_is_absent_from_every_item(field: str) -> None:
    batch, _ = build()
    assert field not in batch.items[0]


@pytest.mark.parametrize(
    "field", ["estimated_cost_usd", "input_tokens", "output_tokens", "latency_ms"]
)
def test_resource_usage_is_absent_from_every_item(field: str) -> None:
    """Knowing a run was expensive invites justifying the expense."""
    batch, _ = build()
    assert field not in batch.items[0]


@pytest.mark.parametrize("field", ["bug_id", "finding_hash", "finding_id", "decision"])
def test_identifiers_and_prior_rulings_are_absent(field: str) -> None:
    batch, _ = build()
    assert field not in batch.items[0]


@pytest.mark.parametrize("needle", FORBIDDEN_SUBSTRINGS)
def test_no_forbidden_value_appears_anywhere_in_the_serialised_batch(needle: str) -> None:
    """Belt and braces: the whole payload is searched, not just top-level keys."""
    batch, _ = build([(FINDING_A, BUG_A), (FINDING_B, BUG_B)])
    assert needle not in json.dumps(batch.as_dict(), ensure_ascii=False)


def test_the_batch_is_not_ordered_by_bug_so_grouping_cannot_be_inferred() -> None:
    """Adjacent items sharing a bug would let an adjudicator infer the answer set."""
    batch, _ = build([(FINDING_A, BUG_A), (FINDING_B, BUG_A), (FINDING_A, BUG_B)])
    assert len(batch.items) == 3
    assert all("bug_id" not in item for item in batch.items)


# ------------------------------------------------------------------- import


def test_import_maps_sequence_numbers_back_to_keys() -> None:
    _, keymap = build([(FINDING_A, BUG_A), (FINDING_B, BUG_B)])
    entries = import_decisions(
        keymap,
        {
            1: ("same_root_cause", "Same mechanism, evidence matches the line."),
            2: ("different_root_cause", "Describes a different defect."),
        },
        adjudicator_id="A1",
        decided_at="2026-08-05",
    )
    assert {e["key"]["bug_id"] for e in entries} == {"fx-taskq-py/B-001", "fx-taskq-py/B-002"}
    assert {e["decision"] for e in entries} == {"same_root_cause", "different_root_cause"}


def test_import_round_trips_through_the_ledger(tmp_path: Any) -> None:
    from coding_agent_eval.evaluator.ledger import write_entries

    _, keymap = build()
    entries = import_decisions(
        keymap,
        {1: ("insufficient", "Cannot tell from the excerpt shown.")},
        adjudicator_id=f"{SYNTHETIC_PREFIX}test",
        decided_at="2026-08-05",
    )
    path = tmp_path / "synthetic_adjudications.jsonl"
    write_entries(path, entries)
    ledger = load_ledger(path, kind=LedgerKind.SYNTHETIC)
    assert len(ledger) == 1
    assert next(iter(ledger.decisions.values())) is Decision.INSUFFICIENT


def test_an_unknown_sequence_number_is_rejected() -> None:
    _, keymap = build()
    with pytest.raises(BlindingError, match="99"):
        import_decisions(
            keymap, {99: ("same_root_cause", "x")}, adjudicator_id="A1", decided_at="2026-08-05"
        )


def test_an_unruled_item_is_rejected() -> None:
    """A partial return would silently produce a partially adjudicated ledger."""
    _, keymap = build([(FINDING_A, BUG_A), (FINDING_B, BUG_B)])
    with pytest.raises(BlindingError, match="2"):
        import_decisions(
            keymap,
            {1: ("same_root_cause", "ok")},
            adjudicator_id="A1",
            decided_at="2026-08-05",
        )


def test_a_blank_rationale_is_rejected() -> None:
    """Rationale is what lets a third party re-examine the ruling."""
    _, keymap = build()
    with pytest.raises(BlindingError, match="rationale"):
        import_decisions(
            keymap, {1: ("same_root_cause", "   ")}, adjudicator_id="A1", decided_at="2026-08-05"
        )
