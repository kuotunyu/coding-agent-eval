"""Blinded human adjudication, end to end: export a worksheet, apply it back.

Everything a `verified_*` metric traces to starts here. The pieces already
existed and were already tested in isolation — `evaluator.matcher` finds the
candidate pairs, `evaluator.blinded_export` builds the blind view and turns
returned rulings into ledger entries, `evaluator.worksheet` renders and parses
the document a person actually reads — but nothing connected a live run's
`findings.json` to any of them. This is that connection.

**No AI may author an adjudication.** This module builds the document and
reads the two lines a human filled in; it never chooses a decision or writes a
rationale. `apply_review` refuses an `adjudicator_id` carrying the
`SYNTHETIC-` prefix outright — that prefix exists precisely to mark rulings
that were not made by a person, and this path only ever writes to the formal
ledger.

**The code shown is the tree the run actually measured, not the clean tree.**
A finding was made against a mutated snapshot, so the excerpt has to come from
that same tree — the fixture's `tree/` with exactly the bug(s) named in the
run's own `bugs_in_snapshot` patched in, reconstructed the same way
`e2e.run_snapshot` and `live.execute` built it in the first place, not assumed.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from coding_agent_eval.e2e import Workspace
from coding_agent_eval.evaluator.blinded_export import (
    KeyMap,
    export_batch,
    import_decisions,
)
from coding_agent_eval.evaluator.hashing import finding_hash
from coding_agent_eval.evaluator.ledger import (
    SYNTHETIC_PREFIX,
    LedgerKey,
    LedgerKind,
    read_entries,
    write_entries,
)
from coding_agent_eval.evaluator.matcher import candidate_pairs
from coding_agent_eval.evaluator.worksheet import parse_worksheet, render_worksheet
from coding_agent_eval.fixtures.patcher import apply_patch, materialise

#: Lines of surrounding code shown on each side of a finding's cited range.
#: Enough to see the statement in context; not so much that unrelated code
#: crowds out the two or three lines the ruling actually depends on.
EXCERPT_CONTEXT_LINES = 3


class AdjudicationError(RuntimeError):
    """Export or import could not proceed."""


# --------------------------------------------------------------------- export


@dataclass(frozen=True)
class ExportResult:
    """What `export_for_review` produced, or the fact that there was nothing to."""

    worksheet_path: Path | None
    keymap_path: Path | None
    pending: int
    already_ruled: int


def _load_fixture(fixture_dir: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """The manifest, and every bug it lists, read in full."""
    manifest_path = fixture_dir / "fixture.yaml"
    if not manifest_path.is_file():
        raise AdjudicationError(f"no fixture manifest at {manifest_path}")
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    bugs = []
    for bug_ref in manifest["bugs"]:
        name = bug_ref.split("/")[-1]
        bug_path = fixture_dir / "bugs" / f"{name}.yaml"
        bugs.append(yaml.safe_load(bug_path.read_text(encoding="utf-8")))
    return manifest, bugs


def _excerpt(tree: Path, file: str, line_start: int, line_end: int) -> str:
    """Numbered source lines around a finding's location, from the tree the run measured."""
    path = tree / file
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise AdjudicationError(f"could not read {file} for its excerpt: {exc}") from exc
    lo = max(1, line_start - EXCERPT_CONTEXT_LINES)
    hi = min(len(lines), line_end + EXCERPT_CONTEXT_LINES)
    if lo > len(lines):
        raise AdjudicationError(
            f"{file} is only {len(lines)} lines; a finding citing line {line_start} does not "
            "match the tree this export is reading against"
        )
    return "\n".join(f"{n:>6}: {lines[n - 1]}" for n in range(lo, hi + 1))


def _mutated_tree(
    fixture_dir: Path, bug_ids: list[str], bugs_by_id: dict[str, dict[str, Any]], workspace: Path
) -> Path:
    """Reconstruct the tree a mutated run actually measured: clean plus exactly
    the named bugs' patches, applied the same way the run itself was built."""
    tree = materialise(fixture_dir / "tree", workspace / "tree")
    for bug_id in bug_ids:
        bug = bugs_by_id[bug_id]
        apply_patch(tree, fixture_dir / bug["patch"])
    return tree


def _write_keymap(path: Path, keymap: KeyMap) -> None:
    """The private half of an export. Never hand this file to whoever adjudicates."""
    payload = {
        "fixture_version": keymap.fixture_version,
        "entries": {str(seq): key.as_dict() for seq, key in keymap.entries.items()},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_keymap(path: Path) -> KeyMap:
    if not path.is_file():
        raise AdjudicationError(f"no key map at {path}; export writes one beside the worksheet")
    payload = json.loads(path.read_text(encoding="utf-8"))
    try:
        entries = {int(seq): LedgerKey.from_dict(key) for seq, key in payload["entries"].items()}
        return KeyMap(fixture_version=payload["fixture_version"], entries=entries)
    except (KeyError, ValueError) as exc:
        raise AdjudicationError(f"{path} is not a well-formed key map: {exc}") from exc


def export_for_review(
    run_dir: Path,
    *,
    fixture_dir: Path,
    ledger_path: Path,
    out_path: Path,
    keymap_path: Path | None = None,
    rng: random.Random | None = None,
) -> ExportResult:
    """Build a blinded worksheet of one run's unruled candidate pairs.

    Only a mutated-snapshot run has anything to export: a clean control's
    findings are unsupported by definition (spec §8.5), and there is no bug to
    adjudicate them against.
    """
    header_path = run_dir / "run.json"
    findings_path = run_dir / "findings.json"
    if not header_path.is_file() or not findings_path.is_file():
        raise AdjudicationError(
            f"{run_dir} does not look like run evidence (run.json/findings.json)"
        )

    header = json.loads(header_path.read_text(encoding="utf-8"))
    findings = json.loads(findings_path.read_text(encoding="utf-8"))["findings"]

    bug_ids: list[str] = list(header.get("bugs_in_snapshot") or [])
    if not bug_ids:
        raise AdjudicationError(
            f"{run_dir} has no bugs_in_snapshot — this is a clean-control run (or carries no "
            "record of one), and benchmark_unsupported_findings_per_kloc does not need a "
            "human ruling, so there is nothing here to adjudicate"
        )

    manifest, all_bugs = _load_fixture(fixture_dir)
    fixture_version = str(manifest["fixture_version"])
    bugs_by_id = {str(bug["bug_id"]): bug for bug in all_bugs}
    unknown_bugs = [b for b in bug_ids if b not in bugs_by_id]
    if unknown_bugs:
        raise AdjudicationError(
            f"{run_dir} names bugs not in {fixture_dir}'s manifest: {unknown_bugs}"
        )
    bugs = [bugs_by_id[b] for b in bug_ids]

    pairs = candidate_pairs(findings, bugs)
    already_ruled = {
        LedgerKey.from_dict(entry["key"])
        for entry in read_entries(ledger_path, kind=LedgerKind.FORMAL)
    }

    def already_has(finding: dict[str, Any], bug: dict[str, Any]) -> bool:
        key = LedgerKey(
            fixture_version=fixture_version,
            bug_id=str(bug["bug_id"]),
            finding_hash=finding_hash(finding),
        )
        return key in already_ruled

    pending_pairs = [(f, b) for f, b in pairs if not already_has(f, b)]
    already_count = len(pairs) - len(pending_pairs)

    if not pending_pairs:
        return ExportResult(
            worksheet_path=None, keymap_path=None, pending=0, already_ruled=already_count
        )

    (rng or random).shuffle(pending_pairs)

    with Workspace() as workspace:
        tree = _mutated_tree(fixture_dir, bug_ids, bugs_by_id, workspace)
        excerpts = {
            finding["file"]: _excerpt(
                tree, finding["file"], finding["line_start"], finding["line_end"]
            )
            for finding, _ in pending_pairs
        }

        batch, keymap = export_batch(
            pending_pairs,
            fixture_version=fixture_version,
            language=str(manifest["language"]),
            excerpts=excerpts,
        )

    worksheet_text = render_worksheet(batch)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(worksheet_text, encoding="utf-8", newline="\n")

    resolved_keymap_path = keymap_path or out_path.with_suffix(".keymap.json")
    _write_keymap(resolved_keymap_path, keymap)

    return ExportResult(
        worksheet_path=out_path,
        keymap_path=resolved_keymap_path,
        pending=len(pending_pairs),
        already_ruled=already_count,
    )


# --------------------------------------------------------------------- import


@dataclass(frozen=True)
class ImportResult:
    ruled: int
    ledger_path: Path


def apply_review(
    *,
    worksheet_path: Path,
    keymap_path: Path,
    ledger_path: Path,
    adjudicator_id: str,
    decided_at: str,
) -> ImportResult:
    """Read a filled-in worksheet and append its rulings to the formal ledger.

    Refuses outright rather than partially: `blinded_export.import_decisions`
    already refuses an unruled item, and this refuses a ruling that would
    collide with one already on record — the ledger is append-only, and
    re-importing the same worksheet twice must not silently produce a second,
    identical-looking entry the next `load_ledger` call would then reject far
    from here, with no memory of which import caused it.
    """
    if adjudicator_id.startswith(SYNTHETIC_PREFIX):
        raise AdjudicationError(
            f"{adjudicator_id!r} carries the {SYNTHETIC_PREFIX} prefix, which marks a ruling "
            "as test data. The formal ledger holds human rulings only."
        )

    if not worksheet_path.is_file():
        raise AdjudicationError(f"no worksheet at {worksheet_path}")
    keymap = _read_keymap(keymap_path)
    decisions = parse_worksheet(worksheet_path.read_text(encoding="utf-8"))

    new_entries = import_decisions(
        keymap, decisions, adjudicator_id=adjudicator_id, decided_at=decided_at
    )

    existing = read_entries(ledger_path, kind=LedgerKind.FORMAL)
    existing_keys = {LedgerKey.from_dict(entry["key"]) for entry in existing}
    colliding = [e for e in new_entries if LedgerKey.from_dict(e["key"]) in existing_keys]
    if colliding:
        raise AdjudicationError(
            f"{len(colliding)} of these rulings already exist in the ledger. It is "
            "append-only; re-importing the same worksheet twice is refused rather than "
            "silently duplicated or left to fail confusingly on the next read."
        )

    write_entries(ledger_path, existing + new_entries)
    return ImportResult(ruled=len(new_entries), ledger_path=ledger_path)
