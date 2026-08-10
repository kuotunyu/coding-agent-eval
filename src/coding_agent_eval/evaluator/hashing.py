"""`finding_hash` — the adjudication ledger key (design spec §6.5).

What this hash covers decides what a human ruling carries over to, so the field
set is a protocol decision rather than an implementation detail. It is frozen by
`ADJUDICATION_PROTOCOL_VERSION`; changing it invalidates every existing entry.

**Evidence is included.** Without it, a finding with the right claim and root
cause but fabricated evidence would inherit an earlier `same_root_cause` ruling,
which makes inventing supporting detail free. Since adjudication asks whether the
evidence is actually supported by the code (spec §8.3), the evidence has to be
part of what is being ruled on.

Id, severity, and suggested_verification are excluded: none of them changes
whether two findings describe the same defect with the same support.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any

#: Fields that form the identity of a finding, in the order they are documented.
KEYED_FIELDS: tuple[str, ...] = (
    "file",
    "line_start",
    "line_end",
    "category",
    "claim",
    "root_cause",
    "evidence",
)

#: Fields deliberately outside the identity.
UNKEYED_FIELDS: tuple[str, ...] = ("id", "severity", "suggested_verification")

#: Free-text fields, which are normalised before hashing.
_TEXT_FIELDS: frozenset[str] = frozenset({"claim", "root_cause", "evidence"})

_WHITESPACE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """NFKC, collapse whitespace runs, strip, casefold.

    Cosmetic differences — a re-wrapped line, a double space, capitalisation —
    must not create a new ledger key and force a re-ruling of the same finding.
    Anything beyond that is a genuine difference and is left alone.
    """
    folded = unicodedata.normalize("NFKC", value)
    return _WHITESPACE.sub(" ", folded).strip().casefold()


def canonical_payload(finding: dict[str, Any]) -> dict[str, Any]:
    """The exact subset that is hashed. Raises `KeyError` if a keyed field is absent.

    A partial finding must not hash to something plausible; that would key a
    ruling to a document that never existed.
    """
    payload: dict[str, Any] = {}
    for field in KEYED_FIELDS:
        value = finding[field]
        payload[field] = normalize_text(value) if field in _TEXT_FIELDS else value
    return payload


def finding_hash(finding: dict[str, Any]) -> str:
    """Return the hex SHA-256 identity of `finding`."""
    encoded = json.dumps(
        canonical_payload(finding),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
