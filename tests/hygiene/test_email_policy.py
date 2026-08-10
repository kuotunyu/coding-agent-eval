"""The two scanners share patterns but not policy (design spec §10.8).

The tracked-file scanner must accept exactly one official address, because the
repository unavoidably contains it in commit metadata and documentation. A rule
that rejected the repository's own identity would simply be switched off, which
is worse than a rule with one audited exception.

The public-artifact scanner has no such pressure: a run artifact has no
legitimate reason to contain any address, so it keeps zero tolerance — including
for the official one.
"""

from __future__ import annotations

import pytest

from coding_agent_eval import HYGIENE_POLICY_VERSION
from coding_agent_eval.hygiene.policy import (
    LEAK_CORPUS_EXCLUSIONS,
    OFFICIAL_PUBLIC_EMAIL,
    PUBLIC_ARTIFACT_POLICY,
    TRACKED_FILE_POLICY,
)

from .corpus import EMAIL_NEAR_MISSES, SAME_HOST_OTHER_ACCOUNT


def test_policy_is_versioned() -> None:
    assert HYGIENE_POLICY_VERSION
    assert TRACKED_FILE_POLICY.version == HYGIENE_POLICY_VERSION
    assert PUBLIC_ARTIFACT_POLICY.version == HYGIENE_POLICY_VERSION


def test_official_email_passes_tracked_file_scanner() -> None:
    assert TRACKED_FILE_POLICY.findings(f"Author: kuotunyu <{OFFICIAL_PUBLIC_EMAIL}>") == []


@pytest.mark.parametrize("address", EMAIL_NEAR_MISSES)
def test_near_misses_fail_tracked_file_scanner(address: str) -> None:
    findings = TRACKED_FILE_POLICY.findings(f"contact {address} today")
    assert any(f.rule == "email" for f in findings), f"{address} should not be allowlisted"


def test_official_email_still_fails_public_artifact_scanner() -> None:
    """Zero tolerance in run artifacts, official address included."""
    findings = PUBLIC_ARTIFACT_POLICY.findings(f"trace mentions {OFFICIAL_PUBLIC_EMAIL}")
    assert any(f.rule == "email" for f in findings)


def test_allowlist_holds_exactly_one_entry() -> None:
    assert TRACKED_FILE_POLICY.email_allowlist == frozenset({OFFICIAL_PUBLIC_EMAIL})


def test_allowlist_is_compared_literally_not_as_a_regex() -> None:
    """If the entry were compiled as a pattern, '.' and '+' would become wildcards."""
    dot_as_wildcard = OFFICIAL_PUBLIC_EMAIL.replace(".noreply", "Xnoreply")
    assert TRACKED_FILE_POLICY.findings(dot_as_wildcard)

    plus_as_quantifier = OFFICIAL_PUBLIC_EMAIL.replace("5+k", "55k")
    assert TRACKED_FILE_POLICY.findings(plus_as_quantifier)


def test_allowlist_is_not_domain_scoped() -> None:
    assert TRACKED_FILE_POLICY.findings(SAME_HOST_OTHER_ACCOUNT)


def test_allowlist_match_is_case_insensitive_but_otherwise_exact() -> None:
    assert TRACKED_FILE_POLICY.findings(OFFICIAL_PUBLIC_EMAIL.upper()) == []
    assert TRACKED_FILE_POLICY.findings(OFFICIAL_PUBLIC_EMAIL.replace("+", "-"))


# ------------------------------------------------------ corpus path exclusion


def test_exclusion_set_holds_exactly_one_path() -> None:
    """A hole in the gate stays one reviewable file wide."""
    assert frozenset({"tests/hygiene/corpus.py"}) == LEAK_CORPUS_EXCLUSIONS
    assert TRACKED_FILE_POLICY.path_exclusions == LEAK_CORPUS_EXCLUSIONS


def test_public_artifact_policy_has_no_exclusions() -> None:
    assert PUBLIC_ARTIFACT_POLICY.path_exclusions == frozenset()


def test_exclusion_is_an_exact_path_not_a_prefix_or_directory() -> None:
    assert TRACKED_FILE_POLICY.excludes("tests/hygiene/corpus.py")
    for near in (
        "tests/hygiene/corpus.py.bak",
        "tests/hygiene/corpus_extra.py",
        "tests/hygiene/",
        "tests/hygiene/other.py",
        "corpus.py",
        "src/coding_agent_eval/corpus.py",
    ):
        assert not TRACKED_FILE_POLICY.excludes(near), near
