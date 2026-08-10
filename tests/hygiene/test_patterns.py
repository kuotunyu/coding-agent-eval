"""Leak pattern coverage (design spec §10.5).

Samples live in `corpus.py` because they have to look exactly like real leaks,
and that file is the single audited exclusion in the tracked-file policy.
"""

from __future__ import annotations

import pytest

from coding_agent_eval.hygiene.patterns import RULE_NAMES, scan

from .corpus import (
    MARKER_LINE_SCOPED,
    MARKER_RULE_SCOPED,
    MARKER_WITH_EMPTY_REASON,
    MARKER_WITHOUT_REASON,
    NEGATIVE,
    POSITIVE,
    SAMPLE_API_KEY,
)


def test_every_named_rule_has_positive_coverage() -> None:
    covered = {rule for rule, _ in POSITIVE}
    assert covered == set(RULE_NAMES), f"uncovered rules: {set(RULE_NAMES) - covered}"


def test_every_rule_has_at_least_two_samples() -> None:
    """One sample tends to encode an accidental spelling of the rule, not the rule."""
    counts = {rule: sum(1 for r, _ in POSITIVE if r == rule) for rule in RULE_NAMES}
    assert all(n >= 2 for n in counts.values()), counts


@pytest.mark.parametrize(("rule", "sample"), POSITIVE)
def test_positive_sample_is_flagged(rule: str, sample: str) -> None:
    findings = scan(sample)
    assert any(f.rule == rule for f in findings), f"{rule} missed: {findings}"


@pytest.mark.parametrize("sample", NEGATIVE)
def test_negative_sample_is_clean(sample: str) -> None:
    assert scan(sample) == [], f"false positive on {sample!r}"


def test_finding_reports_rule_and_line() -> None:
    findings = scan(f"clean line\nkey is {SAMPLE_API_KEY}\n")
    assert len(findings) == 1
    assert findings[0].rule == "api_key"
    assert findings[0].line == 2


def test_suppression_marker_is_rule_scoped() -> None:
    """Allowing one rule on a line must not blanket-allow the others on it."""
    rules = {f.rule for f in scan(MARKER_RULE_SCOPED)}
    assert "absolute_path" not in rules
    assert "api_key" in rules


def test_suppression_marker_is_line_scoped() -> None:
    assert [f.line for f in scan(MARKER_LINE_SCOPED)] == [2]


def test_suppression_marker_requires_a_reason() -> None:
    """A bare marker with no justification must not suppress anything."""
    assert scan(MARKER_WITHOUT_REASON)
    assert scan(MARKER_WITH_EMPTY_REASON)


def test_scan_never_echoes_the_secret_value() -> None:
    """Findings reach CI logs, which are themselves published."""
    for finding in scan(f"token {SAMPLE_API_KEY}"):
        rendered = f"{finding.rule} {finding.line} {finding.excerpt} {finding.render()}"
        assert SAMPLE_API_KEY not in rendered
