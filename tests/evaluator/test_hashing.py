"""`finding_hash` (design spec §6.5).

This hash is the adjudication ledger key, so what it covers decides what a
ruling carries over to. Evidence is included on purpose: without it, a finding
with the right claim and root cause but *fabricated* evidence would inherit an
earlier `same_root_cause` ruling, and inventing evidence would be free.

Id, severity, and suggested_verification are excluded, because none of them
changes whether two findings describe the same defect with the same support.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from coding_agent_eval.evaluator.hashing import finding_hash, normalize_text

FINDING: dict[str, Any] = {
    "id": "F-001",
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

#: Changing any of these must produce a different hash.
KEYED_FIELDS = ("file", "line_start", "line_end", "category", "claim", "root_cause", "evidence")

#: Changing any of these must not.
UNKEYED_FIELDS = ("id", "severity", "suggested_verification")


def test_hash_is_hex_sha256() -> None:
    value = finding_hash(FINDING)
    assert len(value) == 64
    assert all(c in "0123456789abcdef" for c in value)


def test_hash_is_stable_across_calls() -> None:
    assert finding_hash(FINDING) == finding_hash(deepcopy(FINDING))


@pytest.mark.parametrize("field", KEYED_FIELDS)
def test_changing_a_keyed_field_changes_the_hash(field: str) -> None:
    other = deepcopy(FINDING)
    other[field] = other[field] + 1 if isinstance(other[field], int) else other[field] + " (edited)"
    assert finding_hash(other) != finding_hash(FINDING), field


@pytest.mark.parametrize("field", UNKEYED_FIELDS)
def test_changing_an_unkeyed_field_leaves_the_hash_alone(field: str) -> None:
    other = deepcopy(FINDING)
    other[field] = "F-999" if field == "id" else "low" if field == "severity" else "something else"
    assert finding_hash(other) == finding_hash(FINDING), field


def test_evidence_is_keyed_so_fabricated_support_cannot_inherit_a_ruling() -> None:
    """The whole point of correction 1: same claim, invented evidence, new key."""
    fabricated = deepcopy(FINDING)
    fabricated["evidence"] = "auth.py:144 calls hmac.compare_digest, which does not exist here."
    assert fabricated["claim"] == FINDING["claim"]
    assert fabricated["root_cause"] == FINDING["root_cause"]
    assert finding_hash(fabricated) != finding_hash(FINDING)


# ------------------------------------------------------------- normalisation


def test_normalisation_collapses_whitespace_runs() -> None:
    spaced = deepcopy(FINDING)
    spaced["claim"] = "Session   token\tcomparison\nleaks timing information."
    assert finding_hash(spaced) == finding_hash(FINDING)


def test_normalisation_strips_surrounding_whitespace() -> None:
    padded = deepcopy(FINDING)
    padded["root_cause"] = f"  {FINDING['root_cause']}\n"
    assert finding_hash(padded) == finding_hash(FINDING)


def test_normalisation_is_case_insensitive() -> None:
    shouted = deepcopy(FINDING)
    shouted["claim"] = FINDING["claim"].upper()
    assert finding_hash(shouted) == finding_hash(FINDING)


def test_normalisation_applies_nfkc() -> None:
    """Full-width forms must not key differently from their ASCII equivalents.

    Built from codepoints rather than written as literals: the characters are
    the subject of the test, so naming them explicitly keeps that visible rather
    than relying on a reader to notice they are not ordinary letters.
    """
    fullwidth_abc = "".join(chr(cp) for cp in (0xFF21, 0xFF22, 0xFF23))
    assert normalize_text(fullwidth_abc) == normalize_text("abc")


def test_normalisation_does_not_erase_meaning() -> None:
    """Different wording is a different finding, whatever the normalisation."""
    reworded = deepcopy(FINDING)
    reworded["claim"] = "Session token comparison is constant time."
    assert finding_hash(reworded) != finding_hash(FINDING)


def test_key_order_does_not_affect_the_hash() -> None:
    reordered = {key: FINDING[key] for key in sorted(FINDING, reverse=True)}
    assert finding_hash(reordered) == finding_hash(FINDING)


def test_missing_keyed_field_raises() -> None:
    """A partial finding must not silently hash to something plausible."""
    incomplete = deepcopy(FINDING)
    del incomplete["evidence"]
    with pytest.raises(KeyError):
        finding_hash(incomplete)


def test_line_numbers_are_hashed_as_numbers_not_strings() -> None:
    """142 and '142' must not collide, or a type error would change a ledger key."""
    stringy = deepcopy(FINDING)
    stringy["line_start"] = "142"
    assert finding_hash(stringy) != finding_hash(FINDING)
