"""Headline metrics (design spec §8.4, §8.5).

Expected values are worked out by hand in each docstring. A test that recomputes
the implementation's own arithmetic checks only that the code is consistent with
itself.

Two decisions get particular attention because they are the ones that could
quietly flatter an agent. Out-of-scope findings count in the precision
denominator, since off-target noise is a real cost to a reviewer. And a zero
denominator produces `None` with a stated reason, never `0` — reporting zero
cost per verified bug for a run that verified nothing would read as perfect
efficiency.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from coding_agent_eval import TRACE_SCHEMA_VERSION
from coding_agent_eval.evaluator.hashing import finding_hash
from coding_agent_eval.evaluator.ledger import (
    SYNTHETIC_PREFIX,
    Ledger,
    LedgerKey,
    LedgerKind,
)
from coding_agent_eval.evaluator.metrics import (
    EvaluationError,
    FixtureSpec,
    RunContext,
    Usage,
    score_run,
)

FIXTURE_VERSION = "1.0.0"
TREE_CHECKSUM = "sha256:" + "a" * 64

FIXTURE = FixtureSpec(
    fixture_id="fx-taskq-py",
    fixture_version=FIXTURE_VERSION,
    tree_checksum=TREE_CHECKSUM,
    in_scope_paths=["src/**"],
    out_of_scope_paths=["tests/**"],
    in_scope_loc=2000,
)

CONTEXT = RunContext(
    run_id="run-1",
    fixture_version=FIXTURE_VERSION,
    tree_checksum=TREE_CHECKSUM,
    trace_schema_version=TRACE_SCHEMA_VERSION,
    snapshot="mutated",
    tool_backend="host_process",
    pricing_table_version="none-offline",
)

USAGE = Usage(estimated_cost_usd=0.10, input_tokens=90_000, output_tokens=10_000)


def bug(
    bug_id: str,
    *,
    file: str,
    start: int,
    end: int,
    category: str = "security",
    group: str | None = None,
) -> dict[str, Any]:
    return {
        "bug_id": bug_id,
        "category": category,
        "compound_group": group,
        "canonical_claim": f"claim for {bug_id}",
        "canonical_root_cause": f"root cause for {bug_id}",
        "localization": {
            "primary": {"file": file, "line_start": start, "line_end": end},
            "line_tolerance": 8,
            "acceptable_alternates": [],
        },
    }


def finding(
    fid: str, *, file: str, start: int, end: int, category: str = "security"
) -> dict[str, Any]:
    return {
        "id": fid,
        "file": file,
        "line_start": start,
        "line_end": end,
        "category": category,
        "severity": "high",
        "claim": f"claim {fid}",
        "root_cause": f"root cause {fid}",
        "evidence": f"evidence {fid}",
        "suggested_verification": "check it",
    }


def ledger_of(
    rulings: list[tuple[dict[str, Any], dict[str, Any], str]],
    kind: LedgerKind = LedgerKind.SYNTHETIC,
) -> Ledger:
    from coding_agent_eval.evaluator.ledger import Decision

    return Ledger(
        kind=kind,
        decisions={
            LedgerKey(
                fixture_version=FIXTURE_VERSION,
                bug_id=b["bug_id"],
                finding_hash=finding_hash(f),
            ): Decision(d)
            for f, b, d in rulings
        },
    )


def score(findings, bugs, ledger, *, context=CONTEXT, fixture=FIXTURE, usage=USAGE):
    return score_run(
        findings=findings,
        bugs=bugs,
        ledger=ledger,
        fixture=fixture,
        context=context,
        usage=usage,
    )


# ------------------------------------------------------------ full scenario


def test_hand_worked_scenario() -> None:
    """Three bugs, four in-scope findings, one out-of-scope. Worked by hand.

    | bug   | location        | candidate findings | ruling             |
    |-------|-----------------|--------------------|--------------------|
    | B-001 | auth.py 100-110 | F-001              | same_root_cause    |
    | B-002 | auth.py 300-310 | F-002              | different_root_cause |
    | B-003 | queue.py 40-44  | (none)             | -                  |

    F-004 is in src/ but matches no bug. F-005 is in tests/, out of scope.

    localization_recall  = |{B-001, B-002}| / 3          = 2/3
    verified_bug_recall  = |{B-001}| / 3                 = 1/3
    F (scored)           = F-001, F-002, F-004, F-005    = 4
    M (matched)          = F-001                          = 1
    verified_finding_precision = 1/4                      = 0.25
    unsupported_findings = 4 - 1                          = 3
    per_kloc             = 3 / (2000/1000)                = 1.5
    cost_per_verified    = 0.10 / 1                       = 0.10
    tokens_per_verified  = 100000 / 1                     = 100000
    """
    bugs = [
        bug("B-001", file="src/auth.py", start=100, end=110),
        bug("B-002", file="src/auth.py", start=300, end=310),
        bug("B-003", file="src/queue.py", start=40, end=44),
    ]
    f1 = finding("F-001", file="src/auth.py", start=104, end=104)
    f2 = finding("F-002", file="src/auth.py", start=305, end=305)
    f4 = finding("F-004", file="src/worker.py", start=7, end=7)
    f5 = finding("F-005", file="tests/test_auth.py", start=3, end=3)

    ledger = ledger_of([(f1, bugs[0], "same_root_cause"), (f2, bugs[1], "different_root_cause")])
    result = score([f1, f2, f4, f5], bugs, ledger)

    m = result.metrics
    assert m["localization_recall"] == pytest.approx(2 / 3)
    assert m["verified_bug_recall"] == pytest.approx(1 / 3)
    assert m["verified_finding_precision"] == pytest.approx(0.25)
    assert m["unsupported_findings"] == 3
    assert m["benchmark_unsupported_findings_per_kloc"] == pytest.approx(1.5)
    assert m["cost_per_verified_bug"] == pytest.approx(0.10)
    assert m["tokens_per_verified_bug"] == pytest.approx(100_000)
    assert m["out_of_scope_findings"] == 1
    assert m["exact_duplicates_removed"] == 0


def test_out_of_scope_findings_count_against_precision_and_are_reported() -> None:
    """Off-target noise is a cost to a reviewer, so it is not free."""
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    hit = finding("F-001", file="src/auth.py", start=104, end=104)
    noise = finding("F-002", file="tests/test_auth.py", start=1, end=1)
    ledger = ledger_of([(hit, b, "same_root_cause")])

    result = score([hit, noise], [b], ledger)
    assert result.metrics["verified_finding_precision"] == pytest.approx(0.5)
    assert result.metrics["out_of_scope_findings"] == 1


def test_duplicates_are_collapsed_before_scoring() -> None:
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    hit = finding("F-001", file="src/auth.py", start=104, end=104)
    same = deepcopy(hit)
    same["id"] = "F-002"
    ledger = ledger_of([(hit, b, "same_root_cause")])

    result = score([hit, same], [b], ledger)
    assert result.metrics["exact_duplicates_removed"] == 1
    assert result.metrics["verified_finding_precision"] == pytest.approx(1.0)


# ---------------------------------------------------------- no residual set


def test_there_is_no_residual_defect_exclusion() -> None:
    """v0.1 removed it: proximity alone would exempt a nearby wrong finding."""
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    miss = finding("F-001", file="src/auth.py", start=500, end=500)
    result = score([miss], [b], ledger_of([]))
    assert "residual_defect_findings" not in result.metrics
    assert result.metrics["unsupported_findings"] == 1


# --------------------------------------------------------- zero denominators


def test_no_verified_bugs_gives_null_cost_not_zero() -> None:
    """Zero cost per verified bug would read as perfect efficiency."""
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    miss = finding("F-001", file="src/auth.py", start=500, end=500)
    result = score([miss], [b], ledger_of([]))

    assert result.metrics["cost_per_verified_bug"] is None
    assert result.metrics["tokens_per_verified_bug"] is None
    assert result.undefined_reasons["cost_per_verified_bug"] == "no_verified_bugs"
    assert result.undefined_reasons["tokens_per_verified_bug"] == "no_verified_bugs"


def test_no_findings_gives_null_precision() -> None:
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    result = score([], [b], ledger_of([]))
    assert result.metrics["verified_finding_precision"] is None
    assert result.undefined_reasons["verified_finding_precision"] == "no_findings"
    assert result.metrics["verified_bug_recall"] == pytest.approx(0.0)


def test_clean_control_has_no_recall_denominator() -> None:
    """No bugs in the snapshot, so recall was never a question that could be asked."""
    noise = finding("F-001", file="src/auth.py", start=10, end=10)
    context = deepcopy(CONTEXT)
    context.snapshot = "clean"
    result = score([noise], [], ledger_of([]), context=context)

    assert result.metrics["verified_bug_recall"] is None
    assert result.metrics["localization_recall"] is None
    assert result.undefined_reasons["verified_bug_recall"] == "no_bugs_in_snapshot"
    assert result.metrics["unsupported_findings"] == 1
    assert result.metrics["benchmark_unsupported_findings_per_kloc"] == pytest.approx(0.5)


def test_no_metric_is_ever_infinity() -> None:
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    result = score([], [b], ledger_of([]))
    for name, value in result.metrics.items():
        assert value != float("inf"), name


def test_every_null_metric_states_a_reason() -> None:
    """A null with no reason is a defect, not a value."""
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    result = score([], [b], ledger_of([]))
    for name, value in result.metrics.items():
        if value is None:
            assert name in result.undefined_reasons, name


# ------------------------------------------------------- one-to-one assignment


def test_a_finding_verifies_at_most_one_bug_by_default() -> None:
    """Two separate bugs must not both be credited to one finding."""
    b1 = bug("B-001", file="src/auth.py", start=100, end=110)
    b2 = bug("B-002", file="src/auth.py", start=105, end=115)
    f = finding("F-001", file="src/auth.py", start=108, end=108)
    ledger = ledger_of([(f, b1, "same_root_cause"), (f, b2, "same_root_cause")])

    result = score([f], [b1, b2], ledger)
    assert result.counts["verified_bugs"] == 1
    assert result.metrics["verified_bug_recall"] == pytest.approx(0.5)


def test_assignment_is_deterministic_across_input_order() -> None:
    b1 = bug("B-001", file="src/auth.py", start=100, end=110)
    b2 = bug("B-002", file="src/auth.py", start=105, end=115)
    f = finding("F-001", file="src/auth.py", start=108, end=108)
    ledger = ledger_of([(f, b1, "same_root_cause"), (f, b2, "same_root_cause")])

    forward = score([f], [b1, b2], ledger)
    backward = score([f], [b2, b1], ledger)
    assert forward.verified_bug_ids == backward.verified_bug_ids


def test_a_compound_group_lets_one_finding_verify_several_bugs() -> None:
    """Only when the manifest says so explicitly."""
    b1 = bug("B-001", file="src/auth.py", start=100, end=110, group="G1")
    b2 = bug("B-002", file="src/auth.py", start=105, end=115, group="G1")
    f = finding("F-001", file="src/auth.py", start=108, end=108)
    ledger = ledger_of([(f, b1, "same_root_cause"), (f, b2, "same_root_cause")])

    result = score([f], [b1, b2], ledger)
    assert result.counts["verified_bugs"] == 2
    assert result.metrics["verified_bug_recall"] == pytest.approx(1.0)


def test_different_compound_groups_do_not_combine() -> None:
    b1 = bug("B-001", file="src/auth.py", start=100, end=110, group="G1")
    b2 = bug("B-002", file="src/auth.py", start=105, end=115, group="G2")
    f = finding("F-001", file="src/auth.py", start=108, end=108)
    ledger = ledger_of([(f, b1, "same_root_cause"), (f, b2, "same_root_cause")])
    assert score([f], [b1, b2], ledger).counts["verified_bugs"] == 1


def test_one_bug_verified_by_several_findings_counts_once() -> None:
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    f1 = finding("F-001", file="src/auth.py", start=104, end=104)
    f2 = finding("F-002", file="src/auth.py", start=106, end=106)
    ledger = ledger_of([(f1, b, "same_root_cause"), (f2, b, "same_root_cause")])

    result = score([f1, f2], [b], ledger)
    assert result.counts["verified_bugs"] == 1
    assert result.metrics["verified_finding_precision"] == pytest.approx(1.0)


def test_insufficient_does_not_verify() -> None:
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    f = finding("F-001", file="src/auth.py", start=104, end=104)
    result = score([f], [b], ledger_of([(f, b, "insufficient")]))
    assert result.counts["verified_bugs"] == 0
    assert result.metrics["unsupported_findings"] == 1


# ------------------------------------------------------ gate G8: fail closed


def test_an_unadjudicated_candidate_pair_refuses_to_score() -> None:
    """Partial adjudication must not yield a headline."""
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    f = finding("F-001", file="src/auth.py", start=104, end=104)
    with pytest.raises(EvaluationError, match="unadjudicated"):
        score([f], [b], ledger_of([]))


def test_fixture_version_mismatch_refuses_to_score() -> None:
    context = deepcopy(CONTEXT)
    context.fixture_version = "9.9.9"
    with pytest.raises(EvaluationError, match="fixture_version"):
        score([], [], ledger_of([]), context=context)


def test_tree_checksum_mismatch_refuses_to_score() -> None:
    context = deepcopy(CONTEXT)
    context.tree_checksum = "sha256:" + "f" * 64
    with pytest.raises(EvaluationError, match="checksum"):
        score([], [], ledger_of([]), context=context)


def test_unsupported_trace_schema_version_refuses_to_score() -> None:
    context = deepcopy(CONTEXT)
    context.trace_schema_version = "99.0.0"
    with pytest.raises(EvaluationError, match="trace_schema_version"):
        score([], [], ledger_of([]), context=context)


def test_legacy_trace_contract_remains_readable_but_is_not_publishable() -> None:
    context = deepcopy(CONTEXT)
    context.trace_schema_version = "0.1.0"
    ledger = ledger_of([], kind=LedgerKind.FORMAL)

    result = score([], [], ledger, context=context)

    assert result.context.trace_schema_version == "0.1.0"
    assert result.publishable is False


def test_unadjudicated_count_is_reported_in_the_error() -> None:
    b1 = bug("B-001", file="src/auth.py", start=100, end=110)
    b2 = bug("B-002", file="src/auth.py", start=105, end=115)
    f = finding("F-001", file="src/auth.py", start=108, end=108)
    with pytest.raises(EvaluationError, match="2"):
        score([f], [b1, b2], ledger_of([]))


# ------------------------------------------------------------ publishability


def test_a_synthetic_ledger_produces_an_unpublishable_result() -> None:
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    f = finding("F-001", file="src/auth.py", start=104, end=104)
    result = score([f], [b], ledger_of([(f, b, "same_root_cause")]))
    assert result.ledger_kind == "synthetic"
    assert result.publishable is False


def test_a_formal_ledger_produces_a_publishable_result() -> None:
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    f = finding("F-001", file="src/auth.py", start=104, end=104)
    ledger = ledger_of([(f, b, "same_root_cause")], kind=LedgerKind.FORMAL)
    context = deepcopy(CONTEXT)
    context.tool_backend = "measure_container:sha256:" + "b" * 64
    result = score([f], [b], ledger, context=context)
    assert result.ledger_kind == "formal"
    assert result.publishable is True


def test_host_process_trace_0_2_is_not_publication_evidence() -> None:
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    f = finding("F-001", file="src/auth.py", start=104, end=104)
    ledger = ledger_of([(f, b, "same_root_cause")], kind=LedgerKind.FORMAL)

    result = score([f], [b], ledger)

    assert result.context.trace_schema_version == TRACE_SCHEMA_VERSION
    assert result.context.tool_backend == "host_process"
    assert result.publishable is False


def test_results_document_carries_every_version_field() -> None:
    result = score([], [], ledger_of([]))
    document = result.as_dict()
    for field in (
        "benchmark_version",
        "adjudication_protocol_version",
        "trace_schema_version",
        "pricing_table_version",
        "redaction_manifest_version",
    ):
        assert field in document, field


def test_results_document_validates_against_the_schema() -> None:
    from coding_agent_eval.schemas.validate import validate_document

    b = bug("B-001", file="src/auth.py", start=100, end=110)
    f = finding("F-001", file="src/auth.py", start=104, end=104)
    result = score([f], [b], ledger_of([(f, b, "same_root_cause")]))
    assert validate_document("results", result.as_dict()) == []


def test_synthetic_prefix_is_visible_in_the_result_for_audit() -> None:
    """Anyone reading a committed result must see it was not human-adjudicated."""
    b = bug("B-001", file="src/auth.py", start=100, end=110)
    f = finding("F-001", file="src/auth.py", start=104, end=104)
    result = score([f], [b], ledger_of([(f, b, "same_root_cause")]))
    assert SYNTHETIC_PREFIX.lower().rstrip("-") in result.as_dict()["ledger_kind"]
