"""Deduplication before scoring (design spec §8.1).

v0.1 collapses only *exact* semantic duplicates — findings whose `finding_hash`
matches. The original design used a Jaccard similarity threshold, which was
removed because it could merge two genuinely different defects reported in the
same region. If the absorbed one was unsupported, the precision denominator
quietly loses an error and the score goes up. A heuristic that can only ever
improve the headline has no business in the primary scoring path.

These tests therefore assert as much about what dedup must *not* do as what it
does.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from coding_agent_eval.evaluator import dedup as dedup_module
from coding_agent_eval.evaluator.dedup import deduplicate

BASE: dict[str, Any] = {
    "id": "F-002",
    "file": "src/taskq/auth.py",
    "line_start": 142,
    "line_end": 147,
    "category": "security",
    "severity": "high",
    "claim": "Session token comparison leaks timing information.",
    "root_cause": "The comparison short-circuits on the first differing byte.",
    "evidence": "auth.py:144 compares with == inside verify_token.",
    "suggested_verification": "Time verify_token against prefixes of a valid token.",
}


def finding(**overrides: Any) -> dict[str, Any]:
    doc = deepcopy(BASE)
    doc.update(overrides)
    return doc


def ids(findings: list[dict[str, Any]]) -> list[str]:
    return [f["id"] for f in findings]


def test_no_duplicates_leaves_the_list_alone() -> None:
    a = finding(id="F-001")
    b = finding(id="F-002", line_start=200, line_end=204, claim="Something else entirely here.")
    kept, removed = deduplicate([a, b])
    assert ids(kept) == ["F-001", "F-002"]
    assert removed == 0


def test_identical_findings_collapse_to_one() -> None:
    kept, removed = deduplicate([finding(id="F-003"), finding(id="F-001"), finding(id="F-002")])
    assert removed == 2
    assert len(kept) == 1


def test_the_smallest_id_survives() -> None:
    """Deterministic, so a rerun cannot change which finding is scored."""
    kept, _ = deduplicate([finding(id="F-010"), finding(id="F-002"), finding(id="F-007")])
    assert ids(kept) == ["F-002"]


def test_survivor_choice_is_independent_of_input_order() -> None:
    forward, _ = deduplicate([finding(id="F-002"), finding(id="F-010")])
    backward, _ = deduplicate([finding(id="F-010"), finding(id="F-002")])
    assert ids(forward) == ids(backward) == ["F-002"]


def test_findings_differing_only_in_evidence_both_survive() -> None:
    """Correction 1 seen from the dedup side: evidence is part of the identity."""
    a = finding(id="F-001")
    b = finding(id="F-002", evidence="auth.py:144 uses hmac.compare_digest here.")
    kept, removed = deduplicate([a, b])
    assert ids(kept) == ["F-001", "F-002"]
    assert removed == 0


def test_two_different_defects_in_the_same_region_both_survive() -> None:
    """Exactly the merge the removed Jaccard rule would have performed."""
    timing = finding(id="F-001")
    logging = finding(
        id="F-002",
        claim="The same handler logs the raw session token.",
        root_cause="The token is interpolated into a log line before comparison.",
        evidence="auth.py:141 logs token in full.",
    )
    kept, removed = deduplicate([timing, logging])
    assert ids(kept) == ["F-001", "F-002"]
    assert removed == 0


def test_reworded_duplicates_are_not_merged() -> None:
    """v0.1 does not attempt paraphrase detection; that is the adjudicator's job."""
    a = finding(id="F-001")
    b = finding(id="F-002", claim="Token comparison is vulnerable to a timing attack.")
    kept, _ = deduplicate([a, b])
    assert ids(kept) == ["F-001", "F-002"]


def test_findings_differing_only_in_unkeyed_fields_do_collapse() -> None:
    a = finding(id="F-001", severity="high", suggested_verification="Time it.")
    b = finding(id="F-002", severity="low", suggested_verification="Measure it.")
    kept, removed = deduplicate([a, b])
    assert ids(kept) == ["F-001"]
    assert removed == 1


def test_empty_input_is_handled() -> None:
    assert deduplicate([]) == ([], 0)


def test_input_list_is_not_mutated() -> None:
    original = [finding(id="F-002"), finding(id="F-001")]
    snapshot = deepcopy(original)
    deduplicate(original)
    assert original == snapshot


def test_module_exposes_no_similarity_based_removal() -> None:
    """Guard against the fuzzy rule creeping back into the scoring path.

    Similarity clustering is permitted later only as diagnostics that cannot
    delete a finding, so nothing here may offer a way to drop one by score.
    """
    forbidden = {"jaccard", "similarity", "cluster", "fuzzy", "near_duplicate", "token_set"}
    exported = {name.lower() for name in dir(dedup_module) if not name.startswith("_")}
    assert not (exported & forbidden), exported & forbidden
