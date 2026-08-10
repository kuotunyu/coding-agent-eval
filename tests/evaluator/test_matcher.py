"""Gate G7 — the deterministic candidate matcher (design spec §8.2).

This stage is fully mechanical: file equality, line-range overlap within the
bug's tolerance, and category equality. No human, no model.

Its output is named `localization_recall` and nothing else. Calling it bug
recall would claim the agent explained the defect, when all that has been shown
is that it pointed at the right place with the right label. That distinction is
the reason the semantic stage exists, so the naming is asserted here rather than
left to convention.

The expected values in the scenario test are worked out by hand in the
docstring: a matcher that computes its own expectation proves nothing.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from coding_agent_eval.evaluator import matcher as matcher_module
from coding_agent_eval.evaluator.matcher import candidate_pairs, is_candidate, localization_recall

BUG: dict[str, Any] = {
    "bug_id": "fx-taskq-py/B-001",
    "category": "security",
    "localization": {
        "primary": {"file": "src/taskq/auth.py", "line_start": 100, "line_end": 110},
        "line_tolerance": 8,
        "acceptable_alternates": [],
    },
}

FINDING: dict[str, Any] = {
    "id": "F-001",
    "file": "src/taskq/auth.py",
    "line_start": 100,
    "line_end": 110,
    "category": "security",
    "severity": "high",
    "claim": "Token comparison leaks timing information.",
    "root_cause": "Short-circuits on the first differing byte.",
    "evidence": "auth.py:104 uses ==.",
    "suggested_verification": "Time it.",
}


def finding(**overrides: Any) -> dict[str, Any]:
    doc = deepcopy(FINDING)
    doc.update(overrides)
    return doc


def bug(**overrides: Any) -> dict[str, Any]:
    doc = deepcopy(BUG)
    doc.update(overrides)
    return doc


def at(line_start: int, line_end: int, **overrides: Any) -> dict[str, Any]:
    return finding(line_start=line_start, line_end=line_end, **overrides)


# ------------------------------------------------------------------- basics


def test_exact_overlap_is_a_candidate() -> None:
    assert is_candidate(FINDING, BUG)


def test_different_file_is_not_a_candidate() -> None:
    assert not is_candidate(finding(file="src/taskq/api.py"), BUG)


def test_file_comparison_is_exact_not_by_basename() -> None:
    """Two files can share a name; matching on it would credit the wrong one."""
    assert not is_candidate(finding(file="src/taskq/admin/auth.py"), BUG)


def test_category_mismatch_blocks_a_candidate_despite_perfect_overlap() -> None:
    """Right place, wrong kind of defect, is not a localisation of this bug."""
    assert not is_candidate(finding(category="correctness"), BUG)


# ------------------------------------------------------- tolerance boundary


def test_overlap_exactly_at_the_lower_tolerance_edge_matches() -> None:
    """tolerance 8 on a bug starting at 100 reaches down to line 92."""
    assert is_candidate(at(80, 92), BUG)


def test_one_line_below_the_lower_edge_does_not_match() -> None:
    assert not is_candidate(at(80, 91), BUG)


def test_overlap_exactly_at_the_upper_tolerance_edge_matches() -> None:
    """A bug ending at 110 reaches up to line 118."""
    assert is_candidate(at(118, 130), BUG)


def test_one_line_above_the_upper_edge_does_not_match() -> None:
    assert not is_candidate(at(119, 130), BUG)


def test_a_finding_enclosing_the_bug_matches() -> None:
    assert is_candidate(at(1, 500), BUG)


def test_a_finding_inside_the_bug_matches() -> None:
    assert is_candidate(at(104, 104), BUG)


def test_zero_tolerance_requires_real_overlap() -> None:
    strict = deepcopy(BUG)
    strict["localization"]["line_tolerance"] = 0
    assert is_candidate(at(110, 120), strict)
    assert not is_candidate(at(111, 120), strict)


# --------------------------------------------------------------- alternates


def test_a_finding_at_an_acceptable_alternate_matches() -> None:
    """A defect can legitimately be reported at its call site."""
    with_alternate = deepcopy(BUG)
    with_alternate["localization"]["acceptable_alternates"] = [
        {"file": "src/taskq/api/session.py", "line_start": 61, "line_end": 66}
    ]
    assert is_candidate(
        finding(file="src/taskq/api/session.py", line_start=61, line_end=66), with_alternate
    )


def test_alternates_use_the_same_tolerance_as_the_primary() -> None:
    with_alternate = deepcopy(BUG)
    with_alternate["localization"]["acceptable_alternates"] = [
        {"file": "src/taskq/api/session.py", "line_start": 61, "line_end": 66}
    ]
    assert is_candidate(
        finding(file="src/taskq/api/session.py", line_start=74, line_end=80), with_alternate
    )
    assert not is_candidate(
        finding(file="src/taskq/api/session.py", line_start=75, line_end=80), with_alternate
    )


def test_an_alternate_does_not_relax_the_category_requirement() -> None:
    with_alternate = deepcopy(BUG)
    with_alternate["localization"]["acceptable_alternates"] = [
        {"file": "src/taskq/api/session.py", "line_start": 61, "line_end": 66}
    ]
    assert not is_candidate(
        finding(
            file="src/taskq/api/session.py", line_start=61, line_end=66, category="correctness"
        ),
        with_alternate,
    )


# ------------------------------------------------------------------- pairing


def test_candidate_pairs_are_returned_in_a_deterministic_order() -> None:
    bugs = [bug(bug_id="fx-taskq-py/B-002"), bug(bug_id="fx-taskq-py/B-001")]
    findings = [finding(id="F-002"), finding(id="F-001")]
    pairs = candidate_pairs(findings, bugs)
    assert [(f["id"], b["bug_id"]) for f, b in pairs] == [
        ("F-001", "fx-taskq-py/B-001"),
        ("F-001", "fx-taskq-py/B-002"),
        ("F-002", "fx-taskq-py/B-001"),
        ("F-002", "fx-taskq-py/B-002"),
    ]


def test_one_finding_may_be_a_candidate_for_several_bugs() -> None:
    """Assignment is the metrics stage's job; the matcher only proposes."""
    overlapping = bug(bug_id="fx-taskq-py/B-002")
    pairs = candidate_pairs([FINDING], [BUG, overlapping])
    assert len(pairs) == 2


# --------------------------------------------------- localization_recall


def test_localization_recall_on_a_hand_worked_scenario() -> None:
    """Four bugs, five findings, expected recall 2/4 = 0.5.

    Worked out by hand; a matcher that computes its own expectation proves
    nothing.

    | bug   | category    | location          | best finding                  | result |
    |-------|-------------|-------------------|-------------------------------|--------|
    | B-001 | security    | auth.py 100-110   | F-001 auth.py 104 security    | HIT    |
    | B-002 | security    | auth.py 300-310   | F-002 auth.py 200 security    | miss   |
    | B-003 | correctness | queue.py 40-44    | F-003 queue.py 42 correctness | HIT    |
    | B-004 | concurrency | queue.py 80-90    | F-004 queue.py 85 security    | miss   |

    B-002 misses because 200 is nowhere near 300-310 even with tolerance 8.
    B-004 misses on category: F-004 is in the right place but labelled security,
    and F-005 is concurrency but in worker.py. Neither is a candidate.

    Hit set is {B-001, B-003}, so 2 of 4.
    """
    bugs = [
        {
            "bug_id": "B-001",
            "category": "security",
            "localization": {
                "primary": {"file": "src/auth.py", "line_start": 100, "line_end": 110},
                "line_tolerance": 8,
                "acceptable_alternates": [],
            },
        },
        {
            "bug_id": "B-002",
            "category": "security",
            "localization": {
                "primary": {"file": "src/auth.py", "line_start": 300, "line_end": 310},
                "line_tolerance": 8,
                "acceptable_alternates": [],
            },
        },
        {
            "bug_id": "B-003",
            "category": "correctness",
            "localization": {
                "primary": {"file": "src/queue.py", "line_start": 40, "line_end": 44},
                "line_tolerance": 8,
                "acceptable_alternates": [],
            },
        },
        {
            "bug_id": "B-004",
            "category": "concurrency",
            "localization": {
                "primary": {"file": "src/queue.py", "line_start": 80, "line_end": 90},
                "line_tolerance": 8,
                "acceptable_alternates": [],
            },
        },
    ]
    findings = [
        finding(id="F-001", file="src/auth.py", line_start=104, line_end=104, category="security"),
        finding(id="F-002", file="src/auth.py", line_start=200, line_end=200, category="security"),
        finding(
            id="F-003", file="src/queue.py", line_start=42, line_end=42, category="correctness"
        ),
        finding(id="F-004", file="src/queue.py", line_start=85, line_end=85, category="security"),
        finding(id="F-005", file="src/worker.py", line_start=5, line_end=5, category="concurrency"),
    ]
    assert localization_recall(findings, bugs) == pytest.approx(0.5)


def test_localization_recall_is_one_when_every_bug_is_localised() -> None:
    assert localization_recall([FINDING], [BUG]) == pytest.approx(1.0)


def test_localization_recall_is_zero_when_nothing_matches() -> None:
    assert localization_recall([finding(file="src/other.py")], [BUG]) == pytest.approx(0.0)


def test_localization_recall_with_no_findings_is_zero_not_undefined() -> None:
    """The denominator is the bug set, which still exists."""
    assert localization_recall([], [BUG]) == pytest.approx(0.0)


def test_localization_recall_with_no_bugs_is_none() -> None:
    """A clean control has no bugs, so recall has no denominator (spec §8.5)."""
    assert localization_recall([FINDING], []) is None


def test_duplicate_candidates_do_not_inflate_recall() -> None:
    """Three findings on one bug is still one bug localised."""
    findings = [finding(id="F-001"), finding(id="F-002"), finding(id="F-003")]
    assert localization_recall(findings, [BUG]) == pytest.approx(1.0)


# ------------------------------------------------------------ naming rules


def test_module_names_the_metric_localization_recall_only() -> None:
    """Spec §8.2: this stage proves localisation, and must not claim more."""
    exported = {name.lower() for name in dir(matcher_module) if not name.startswith("_")}
    assert "localization_recall" in exported
    assert not any("bug_recall" in name for name in exported)
    assert not any("correctness" in name for name in exported)
    assert not any("verified" in name for name in exported)
