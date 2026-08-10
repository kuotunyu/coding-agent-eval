"""Blinded export for human adjudication (design spec §8.3).

The adjudicator answers one question: does this finding describe this defect,
with support the code actually provides. Everything else in view is a route to a
biased answer. Knowing the model invites brand expectations. Knowing the run cost
invites justifying the expense. Seeing neighbouring rulings invites consistency
with oneself rather than with the evidence.

The export is therefore built as an **allowlist**: items are assembled from named
fields rather than copied and stripped. A redaction pass fails open — a field
added upstream leaks until someone notices — whereas this fails closed, because a
new field simply never appears until it is added here deliberately.

The mapping from sequence number back to `(bug_id, finding_hash)` is kept out of
the exported payload entirely, so the file handed to a person cannot be reversed
into the answer key.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from coding_agent_eval.evaluator.hashing import finding_hash
from coding_agent_eval.evaluator.ledger import LedgerKey, build_entry


class BlindingError(RuntimeError):
    """The export or its returned decisions are not in a usable state."""


@dataclass(frozen=True)
class BlindedBatch:
    """What a person is given. Contains no run identity and no answer key."""

    items: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {"items": self.items}


@dataclass(frozen=True)
class KeyMap:
    """Sequence number to ledger key. Private: never exported."""

    fixture_version: str
    entries: dict[int, LedgerKey]


def export_batch(
    pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    *,
    fixture_version: str,
    language: str,
    excerpts: dict[str, str],
    run_context: dict[str, Any] | None = None,
) -> tuple[BlindedBatch, KeyMap]:
    """Build the adjudicator-facing batch and the private key map.

    `run_context` is accepted so callers need not remember to withhold it, and is
    deliberately never read. Taking it and ignoring it is safer than trusting
    every call site to leave it out.
    """
    del run_context  # never enters the export; see module docstring

    items: list[dict[str, Any]] = []
    keys: dict[int, LedgerKey] = {}

    for seq, (finding, bug) in enumerate(pairs, start=1):
        items.append(
            {
                "seq": seq,
                "language": language,
                "code_excerpt": excerpts.get(finding["file"], ""),
                "bug_claim": bug["canonical_claim"],
                "bug_root_cause": bug["canonical_root_cause"],
                "finding_claim": finding["claim"],
                "finding_root_cause": finding["root_cause"],
                "finding_evidence": finding["evidence"],
            }
        )
        keys[seq] = LedgerKey(
            fixture_version=fixture_version,
            bug_id=bug["bug_id"],
            finding_hash=finding_hash(finding),
        )

    return BlindedBatch(items=items), KeyMap(fixture_version=fixture_version, entries=keys)


def import_decisions(
    keymap: KeyMap,
    decisions: dict[int, tuple[str, str]],
    *,
    adjudicator_id: str,
    decided_at: str,
) -> list[dict[str, Any]]:
    """Turn returned `{seq: (decision, rationale)}` into ledger entries.

    Every item must be ruled on. A partial return would produce a partially
    adjudicated ledger, and the evaluator would then refuse to publish anything —
    failing here says why, instead of failing later without a reason.
    """
    unknown = sorted(set(decisions) - set(keymap.entries))
    if unknown:
        raise BlindingError(f"decisions reference unknown sequence numbers: {unknown}")

    unruled = sorted(set(keymap.entries) - set(decisions))
    if unruled:
        raise BlindingError(f"no decision returned for sequence numbers: {unruled}")

    entries: list[dict[str, Any]] = []
    for seq in sorted(keymap.entries):
        decision, rationale = decisions[seq]
        if not rationale.strip():
            raise BlindingError(
                f"sequence {seq}: rationale is blank, and a ruling nobody can re-examine "
                "is not auditable"
            )
        entries.append(
            build_entry(
                key=keymap.entries[seq],
                decision=decision,
                rationale=rationale.strip(),
                adjudicator_id=adjudicator_id,
                decided_at=decided_at,
            )
        )
    return entries
