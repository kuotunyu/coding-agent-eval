"""The adjudication ledger (design spec §6.6, §8.3, §8.3.1).

Every `verified_*` metric traces back to entries in this file, so the ledger is
the point where the benchmark is most easily faked and most easily broken.

Two properties defend it.

**Append-only, content-hashed.** A ruling cannot be rewritten in place, and a
silent edit is detectable, so a frozen ledger replays to the same numbers and
those numbers are falsifiable.

**Formal and synthetic are separate, enforced in code.** CI needs to compute
metrics without a human, and the machinery that allows that is exactly the
machinery that could manufacture a human ruling. The formal loader therefore
refuses any entry marked synthetic, the synthetic loader refuses any entry that
is not, and results scored against synthetic data are stamped unpublishable.

The guard is an exact `SYNTHETIC-` prefix on `adjudicator_id`. That is a
convention, not a cryptographic control: someone determined to forge a ruling
can write a plausible id. It stops the accident and the shortcut, which is what
it is for. Real assurance comes from the ledger being public, append-only, and
carrying a rationale per entry that a third party can challenge.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from coding_agent_eval import ADJUDICATION_PROTOCOL_VERSION

#: Marks an entry as test data. Exact, case-sensitive.
SYNTHETIC_PREFIX = "SYNTHETIC-"

_REQUIRED_FIELDS = (
    "key",
    "decision",
    "rationale",
    "adjudication_protocol_version",
    "adjudicator_id",
    "decided_at",
    "entry_hash",
)


class LedgerError(RuntimeError):
    """The ledger could not be read, or violates a rule that makes it trustworthy."""


class Decision(Enum):
    SAME_ROOT_CAUSE = "same_root_cause"
    DIFFERENT_ROOT_CAUSE = "different_root_cause"
    INSUFFICIENT = "insufficient"

    @property
    def verifies(self) -> bool:
        """Whether this ruling counts a bug as verified.

        `insufficient` does not. Treating uncertainty as a match would let an
        unclear finding score the same as a demonstrated one, and the whole
        point of the semantic stage is that the difference matters.
        """
        return self is Decision.SAME_ROOT_CAUSE


class LedgerKind(Enum):
    FORMAL = "formal"
    SYNTHETIC = "synthetic"


@dataclass(frozen=True)
class LedgerKey:
    """Identifies one ruling.

    The fixture version is part of the key so a ruling cannot silently carry over
    to a changed tree, and the finding hash covers evidence so it cannot carry
    over to fabricated support (spec §6.5).
    """

    fixture_version: str
    bug_id: str
    finding_hash: str

    def as_dict(self) -> dict[str, str]:
        return {
            "fixture_version": self.fixture_version,
            "bug_id": self.bug_id,
            "finding_hash": self.finding_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerKey:
        try:
            return cls(
                fixture_version=data["fixture_version"],
                bug_id=data["bug_id"],
                finding_hash=data["finding_hash"],
            )
        except (KeyError, TypeError) as exc:
            raise LedgerError(f"malformed ledger key: {data!r}") from exc


class DecisionSource(Protocol):
    """The only adjudication surface the metric engine may depend on."""

    @property
    def decision_source(self) -> str: ...

    @property
    def publishable(self) -> bool: ...

    @property
    def publication_reason(self) -> str: ...

    @property
    def publication_provenance(self) -> Mapping[str, str]: ...

    def decision(self, key: LedgerKey) -> Decision | None: ...


def entry_hash(entry: dict[str, Any]) -> str:
    """Hash every field except `entry_hash` itself, which is the value produced."""
    payload = {k: v for k, v in entry.items() if k != "entry_hash"}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_entry(
    *,
    key: LedgerKey,
    decision: str,
    rationale: str,
    adjudicator_id: str,
    decided_at: str,
) -> dict[str, Any]:
    """Assemble a well-formed entry, hash included."""
    entry: dict[str, Any] = {
        "key": key.as_dict(),
        "decision": decision,
        "rationale": rationale,
        "adjudication_protocol_version": ADJUDICATION_PROTOCOL_VERSION,
        "adjudicator_id": adjudicator_id,
        "decided_at": decided_at,
    }
    entry["entry_hash"] = entry_hash(entry)
    return entry


def write_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    """Write entries as JSONL, rejecting anything malformed before it lands."""
    for entry in entries:
        missing = [field for field in _REQUIRED_FIELDS if field not in entry]
        if missing:
            raise LedgerError(f"entry is missing {missing}")
        try:
            Decision(entry["decision"])
        except ValueError as exc:
            raise LedgerError(
                f"unknown decision {entry['decision']!r}; expected one of "
                f"{[d.value for d in Decision]}"
            ) from exc

    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for entry in entries
    ]
    # newline="\n" explicitly: the default translates to os.linesep, so the same
    # ledger would be written with different bytes on Windows and Linux. It is a
    # committed artifact that replay compares byte for byte.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("".join(f"{line}\n" for line in lines))


@dataclass(frozen=True)
class Ledger:
    kind: LedgerKind
    decisions: dict[LedgerKey, Decision]

    @property
    def publishable(self) -> bool:
        """Single-review and synthetic ledgers are historical or test evidence only."""
        return False

    @property
    def decision_source(self) -> str:
        return "legacy_formal" if self.kind is LedgerKind.FORMAL else "synthetic"

    @property
    def publication_reason(self) -> str:
        return (
            "single_adjudicator_legacy"
            if self.kind is LedgerKind.FORMAL
            else "synthetic_adjudication"
        )

    @property
    def publication_provenance(self) -> Mapping[str, str]:
        return {}

    def decision(self, key: LedgerKey) -> Decision | None:
        return self.decisions.get(key)

    def __len__(self) -> int:
        return len(self.decisions)


def _check_adjudicator(adjudicator_id: str, kind: LedgerKind, line_number: int) -> None:
    is_synthetic = adjudicator_id.startswith(SYNTHETIC_PREFIX)
    if kind is LedgerKind.FORMAL and is_synthetic:
        raise LedgerError(
            f"line {line_number}: adjudicator {adjudicator_id!r} is synthetic, and the formal "
            "ledger holds human rulings only"
        )
    if kind is LedgerKind.SYNTHETIC and not is_synthetic:
        raise LedgerError(
            f"line {line_number}: adjudicator {adjudicator_id!r} lacks the {SYNTHETIC_PREFIX} "
            "prefix, so it reads as a human ruling and cannot live in a test fixture"
        )


def read_entries(path: Path, *, kind: LedgerKind) -> list[dict[str, Any]]:
    """Every entry in the ledger, validated, as the raw dicts on disk, in file order.

    This is what `load_ledger` reads before collapsing into `Ledger.decisions` —
    pulled out on its own because collapsing loses information a writer needs
    and a reader does not: `rationale`, `adjudicator_id`, `decided_at`, and
    `entry_hash` all disappear once an entry becomes just a `Decision` value.
    Appending a new ruling to an existing ledger means starting from these full
    entries, not from `Ledger`, or every prior rationale would be discarded the
    moment a new one was added.

    A missing file is an empty ledger rather than an error: a benchmark with
    nothing adjudicated yet is a real state, and the evaluator refuses to publish
    from it anyway (spec §8.3).
    """
    if not path.is_file():
        return []

    entries: list[dict[str, Any]] = []
    seen: set[LedgerKey] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LedgerError(f"line {line_number}: not valid JSON: {exc}") from exc

        missing = [field for field in _REQUIRED_FIELDS if field not in entry]
        if missing:
            raise LedgerError(f"line {line_number}: missing {missing}")

        if entry_hash(entry) != entry["entry_hash"]:
            raise LedgerError(
                f"line {line_number}: entry hash does not match its contents, so the entry has "
                "been edited after it was recorded"
            )

        _check_adjudicator(entry["adjudicator_id"], kind, line_number)

        key = LedgerKey.from_dict(entry["key"])
        if key in seen:
            raise LedgerError(
                f"line {line_number}: {key.bug_id} / {key.finding_hash[:12]} has already been "
                "ruled on; the ledger is append-only, so a key appears at most once"
            )
        seen.add(key)
        try:
            Decision(entry["decision"])
        except ValueError as exc:
            raise LedgerError(f"line {line_number}: unknown decision") from exc

        entries.append(entry)

    return entries


def load_ledger(path: Path, *, kind: LedgerKind) -> Ledger:
    """Read a ledger, failing closed on anything that would make it untrustworthy.

    A missing file is an empty ledger rather than an error: a benchmark with
    nothing adjudicated yet is a real state, and the evaluator refuses to publish
    from it anyway (spec §8.3).
    """
    entries = read_entries(path, kind=kind)
    decisions = {
        LedgerKey.from_dict(entry["key"]): Decision(entry["decision"]) for entry in entries
    }
    return Ledger(kind=kind, decisions=decisions)
