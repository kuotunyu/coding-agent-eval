"""Stage A — deterministic candidate matching (design spec §8.2).

Three mechanical conditions, no human and no model: the file matches exactly,
the line ranges overlap within the bug's tolerance, and the category agrees.

**Naming discipline.** The metric produced here is `localization_recall` and
must not be called bug recall, correctness recall, or anything else implying the
agent explained the defect. All that has been established is that it pointed at
the right place with the right label — which is precisely why a semantic stage
exists downstream. A test asserts this module exports no name suggesting more.

Candidate pairs are the only input to human adjudication (spec §8.3), so this
stage decides what a person is ever asked to look at. Being too strict silently
withholds real matches; being too loose wastes adjudication effort on noise. The
tolerance is per-bug so that judgement stays with whoever authored the fixture.
"""

from __future__ import annotations

from typing import Any

Finding = dict[str, Any]
Bug = dict[str, Any]


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start <= b_end and b_start <= a_end


def _locations(bug: Bug) -> list[dict[str, Any]]:
    """The primary location plus any acceptable alternates.

    Alternates exist because a defect can legitimately be reported at its call
    site rather than its definition, and refusing that would penalise a correct
    review for choosing a different vantage point.
    """
    localization = bug["localization"]
    return [localization["primary"], *localization["acceptable_alternates"]]


def is_candidate(finding: Finding, bug: Bug) -> bool:
    """Whether `finding` localises `bug` well enough to be worth adjudicating."""
    if finding["category"] != bug["category"]:
        return False

    tolerance = bug["localization"]["line_tolerance"]
    for location in _locations(bug):
        if finding["file"] != location["file"]:
            continue
        if _ranges_overlap(
            finding["line_start"],
            finding["line_end"],
            location["line_start"] - tolerance,
            location["line_end"] + tolerance,
        ):
            return True
    return False


def candidate_pairs(findings: list[Finding], bugs: list[Bug]) -> list[tuple[Finding, Bug]]:
    """Every (finding, bug) pair worth adjudicating, ordered deterministically.

    Sorted by finding id then bug id so a rerun presents the adjudicator with
    the same sequence; a frozen ledger depends on the ordering being stable.
    """
    pairs = [
        (finding, bug)
        for finding in sorted(findings, key=lambda f: f["id"])
        for bug in sorted(bugs, key=lambda b: b["bug_id"])
        if is_candidate(finding, bug)
    ]
    return pairs


def localized_bug_ids(findings: list[Finding], bugs: list[Bug]) -> set[str]:
    """Bugs with at least one candidate. Several findings on one bug still count once."""
    return {bug["bug_id"] for _, bug in candidate_pairs(findings, bugs)}


def localization_recall(findings: list[Finding], bugs: list[Bug]) -> float | None:
    """Fraction of bugs with at least one candidate finding.

    Returns `None` when there are no bugs — a clean-control snapshot has no
    denominator, and reporting 0.0 there would read as total failure rather than
    as a question that was never asked (spec §8.5).
    """
    if not bugs:
        return None
    return len(localized_bug_ids(findings, bugs)) / len(bugs)
