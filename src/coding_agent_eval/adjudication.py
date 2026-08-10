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
import re
import shutil
import tempfile
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal

import yaml

from coding_agent_eval.e2e import Workspace
from coding_agent_eval.evaluator.blinded_export import (
    BlindedBatch,
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
from coding_agent_eval.evaluator.review_set import candidate_set_sha256
from coding_agent_eval.evaluator.worksheet import (
    DECISION_MARKER,
    RATIONALE_MARKER,
    parse_worksheet,
    render_worksheet,
)
from coding_agent_eval.fixtures.patcher import apply_patch, materialise
from coding_agent_eval.schemas.validate import validate_document

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
    if keymap.review_set_id is not None:
        payload["review_set_id"] = keymap.review_set_id
    if keymap.slot is not None:
        payload["slot"] = keymap.slot
    if keymap.candidate_set_sha256 is not None:
        payload["candidate_set_sha256"] = keymap.candidate_set_sha256
    if keymap.worksheet_template_sha256 is not None:
        payload["worksheet_template_sha256"] = keymap.worksheet_template_sha256
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _read_keymap(path: Path) -> KeyMap:
    if not path.is_file():
        raise AdjudicationError(f"no key map at {path}; export writes one beside the worksheet")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise TypeError("top level is not an object")
        entries = {int(seq): LedgerKey.from_dict(key) for seq, key in payload["entries"].items()}
        return KeyMap(
            fixture_version=payload["fixture_version"],
            entries=entries,
            review_set_id=payload.get("review_set_id"),
            slot=payload.get("slot"),
            candidate_set_sha256=payload.get("candidate_set_sha256"),
            worksheet_template_sha256=payload.get("worksheet_template_sha256"),
        )
    except (OSError, json.JSONDecodeError, AttributeError, KeyError, TypeError, ValueError) as exc:
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


# ---------------------------------------------------------- dual-review flow

ReviewSlot = Literal["primary", "independent"]
_MATERIALS = "candidates.json"


def _sha256_bytes(payload: bytes) -> str:
    return f"sha256:{sha256(payload).hexdigest()}"


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


def _worksheet_template_sha256(text: str) -> str:
    """Hash every worksheet byte except the two human-authored answer values."""
    normalized: list[str] = []
    for line in text.rstrip("\r\n").splitlines():
        if line.startswith(DECISION_MARKER):
            line = f"{DECISION_MARKER} "
        elif line.startswith(RATIONALE_MARKER):
            line = f"{RATIONALE_MARKER} "
        normalized.append(line)
    return _sha256_bytes(("\n".join(normalized) + "\n").encode())


def _atomic_text(path: Path, text: str) -> None:
    """Replace one text file only after its complete next version exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            delete=False,
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(text)
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_entries(path: Path, entries: list[dict[str, Any]]) -> None:
    temporary = path.with_name(f".{path.name}.next")
    try:
        write_entries(temporary, entries)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_trace_for_review(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if not path.is_file():
        raise AdjudicationError(f"no public trace at {path}")
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise AdjudicationError(f"trace line {number} is not valid JSON: {exc}") from exc
        problems = validate_document("trace-record", loaded)
        if problems:
            rendered = "; ".join(problem.render() for problem in problems)
            raise AdjudicationError(f"trace line {number} is invalid: {rendered}")
        records.append(loaded)
    headers = [record["payload"] for record in records if record["event"] == "run_header"]
    if len(headers) != 1:
        raise AdjudicationError(f"trace must contain one run_header; found {len(headers)}")
    if any(record["schema_version"] != "0.2.0" for record in records):
        raise AdjudicationError("review-set init requires trace schema 0.2.0 throughout")
    return records, headers[0]


def _trace_findings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for record in records:
        if record["event"] == "findings_submitted":
            findings.extend(record["payload"]["findings"])
    return findings


@dataclass(frozen=True)
class ReviewSetInitResult:
    review_set_dir: Path
    review_set_id: str
    candidate_count: int


def init_review_set(
    *,
    trace_path: Path,
    bugs_path: Path,
    fixture_dir: Path,
    review_set_dir: Path,
    fixture_author_ids: tuple[str, ...],
    run_operator_id: str,
    primary_id: str,
    independent_id: str,
) -> ReviewSetInitResult:
    """Freeze review inputs without creating or suggesting a human ruling."""
    if review_set_dir.exists():
        raise AdjudicationError(f"review-set directory already exists: {review_set_dir}")
    if not fixture_author_ids:
        raise AdjudicationError("at least one fixture author ID is required")
    if primary_id == independent_id:
        raise AdjudicationError("primary and independent reviewer IDs must be distinct")
    if independent_id in fixture_author_ids or independent_id == run_operator_id:
        raise AdjudicationError(
            "independent reviewer must differ from fixture authors and the run operator"
        )

    records, header = _load_trace_for_review(trace_path)
    findings = _trace_findings(records)
    if not bugs_path.is_file():
        raise AdjudicationError(f"no bug set at {bugs_path}")
    bugs_loaded = json.loads(bugs_path.read_text(encoding="utf-8"))
    if not isinstance(bugs_loaded, list):
        raise AdjudicationError("bug set must be a JSON array")
    bugs: list[dict[str, Any]] = []
    for index, loaded_bug in enumerate(bugs_loaded):
        if not isinstance(loaded_bug, dict) or not isinstance(loaded_bug.get("bug_id"), str):
            raise AdjudicationError(f"bug set item {index} must be an object with bug_id")
        bugs.append(loaded_bug)
    bug_ids = [str(bug["bug_id"]) for bug in bugs]
    expected_bug_set_hash = sha256(_canonical_bytes(bug_ids)).hexdigest()
    if header.get("bug_set_hash") != expected_bug_set_hash:
        raise AdjudicationError("trace bug-set hash does not match the supplied bug set")
    if header.get("snapshot") != "mutated":
        raise AdjudicationError("review-set init requires a mutated snapshot")
    if header.get("sandbox_profile") != "measure":
        raise AdjudicationError("review-set init requires the measure sandbox")

    manifest_path = fixture_dir / "fixture.yaml"
    manifest, _ = _load_fixture(fixture_dir)
    if str(manifest["fixture_id"]) != str(header["fixture_id"]):
        raise AdjudicationError("fixture_id in the trace does not match the fixture manifest")
    if str(manifest["fixture_version"]) != str(header["fixture_version"]):
        raise AdjudicationError("fixture_version in the trace does not match the fixture manifest")
    try:
        manifest_tree_checksum = str(manifest["clean_control"]["tree_checksum"])
        manifest_environment = str(manifest["environment"]["fingerprint"])
        manifest_bug_ids = {str(bug_id) for bug_id in manifest["bugs"]}
    except (KeyError, TypeError) as exc:
        raise AdjudicationError(f"fixture manifest lacks publication identity: {exc}") from exc
    if header.get("fixture_tree_checksum") != manifest_tree_checksum:
        raise AdjudicationError("trace tree checksum does not match the fixture manifest")
    if header.get("env_fingerprint") != manifest_environment:
        raise AdjudicationError("trace environment fingerprint does not match the fixture manifest")
    if not set(bug_ids).issubset(manifest_bug_ids):
        raise AdjudicationError("supplied bug set contains IDs absent from the fixture manifest")

    pairs = candidate_pairs(findings, bugs)
    with Workspace() as workspace:
        bugs_by_id = {str(bug["bug_id"]): bug for bug in bugs}
        tree = _mutated_tree(fixture_dir, list(bugs_by_id), bugs_by_id, workspace)
        excerpts = {
            finding["file"]: _excerpt(
                tree, finding["file"], finding["line_start"], finding["line_end"]
            )
            for finding, _ in pairs
        }
        batch, keymap = export_batch(
            pairs,
            fixture_version=str(manifest["fixture_version"]),
            language=str(manifest["language"]),
            excerpts=excerpts,
        )

    keys = tuple(keymap.entries[seq] for seq in sorted(keymap.entries))
    candidate_hash = candidate_set_sha256(keys)
    slug = re.sub(r"[^a-z0-9-]+", "-", str(header["run_id"]).lower()).strip("-")
    review_set_id = f"rs-{slug}"
    review_manifest: dict[str, Any] = {
        "schema_version": "1.0.0",
        "review_set_id": review_set_id,
        "run_id": str(header["run_id"]),
        "fixture_id": str(header["fixture_id"]),
        "fixture_version": str(header["fixture_version"]),
        "tree_checksum": str(header["fixture_tree_checksum"]),
        "trace_sha256": _sha256_bytes(trace_path.read_bytes()),
        "findings_sha256": _sha256_bytes(_canonical_bytes(findings)),
        "fixture_manifest_sha256": _sha256_bytes(manifest_path.read_bytes()),
        "candidate_set_sha256": candidate_hash,
        "trace_schema_version": "0.2.0",
        "environment_fingerprint": str(header["env_fingerprint"]),
        "fixture_author_ids": list(fixture_author_ids),
        "run_operator_id": run_operator_id,
        "primary": {
            "reviewer_id": primary_id,
            "independent_of_fixture_authors": primary_id not in fixture_author_ids,
            "independent_of_run_operator": primary_id != run_operator_id,
            "independent_of_other_reviewers": False,
        },
        "independent": {
            "reviewer_id": independent_id,
            "independent_of_fixture_authors": True,
            "independent_of_run_operator": True,
            "independent_of_other_reviewers": True,
        },
        "resolver": None,
        "ledgers": {
            "primary": "primary.jsonl",
            "independent": "independent.jsonl",
            "resolutions": "resolutions.jsonl",
        },
    }
    materials = {
        "review_set_id": review_set_id,
        "candidate_set_sha256": candidate_hash,
        "fixture_version": str(manifest["fixture_version"]),
        "items": [
            {
                "key": keymap.entries[int(item["seq"])].as_dict(),
                "item": {field: value for field, value in item.items() if field != "seq"},
            }
            for item in batch.items
        ],
    }
    review_manifest["candidate_materials_sha256"] = _sha256_bytes(_canonical_bytes(materials))
    problems = validate_document("review-set", review_manifest)
    if problems:
        raise AdjudicationError(
            "review-set manifest would be invalid: "
            + "; ".join(problem.render() for problem in problems)
        )

    review_set_dir.parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = Path(
        tempfile.mkdtemp(prefix=f".{review_set_dir.name}.", dir=review_set_dir.parent)
    )
    try:
        (temporary_dir / "manifest.json").write_text(
            json.dumps(review_manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (temporary_dir / _MATERIALS).write_text(
            json.dumps(materials, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        for name in ("primary.jsonl", "independent.jsonl", "resolutions.jsonl"):
            (temporary_dir / name).write_bytes(b"")
        temporary_dir.replace(review_set_dir)
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return ReviewSetInitResult(review_set_dir, review_set_id, len(keys))


def _load_review_files(directory: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = directory / "manifest.json"
    materials_path = directory / _MATERIALS
    if not manifest_path.is_file() or not materials_path.is_file():
        raise AdjudicationError(f"{directory} is missing manifest.json or {_MATERIALS}")
    try:
        manifest_loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        materials_loaded = json.loads(materials_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdjudicationError(f"review-set files are not readable JSON: {exc}") from exc
    if not isinstance(manifest_loaded, dict) or not isinstance(materials_loaded, dict):
        raise AdjudicationError("review-set manifest and candidate materials must be objects")
    manifest: dict[str, Any] = manifest_loaded
    materials: dict[str, Any] = materials_loaded
    problems = validate_document("review-set", manifest)
    if problems:
        raise AdjudicationError(
            "review-set manifest is invalid: " + "; ".join(problem.render() for problem in problems)
        )
    if materials.get("review_set_id") != manifest["review_set_id"]:
        raise AdjudicationError("candidate materials belong to a different review set")
    if materials.get("candidate_set_sha256") != manifest["candidate_set_sha256"]:
        raise AdjudicationError("candidate materials hash binding does not match the manifest")
    materials_hash = _sha256_bytes(_canonical_bytes(materials))
    if materials_hash != manifest["candidate_materials_sha256"]:
        raise AdjudicationError("candidate materials have drifted from their manifest hash")
    keys = tuple(LedgerKey.from_dict(item["key"]) for item in materials["items"])
    if candidate_set_sha256(keys) != manifest["candidate_set_sha256"]:
        raise AdjudicationError("candidate materials have drifted from candidate_set_sha256")
    return manifest, materials


def _ordered_materials(
    manifest: dict[str, Any], materials: dict[str, Any], slot: str
) -> list[dict[str, Any]]:
    items = sorted(
        materials["items"],
        key=lambda item: json.dumps(item["key"], sort_keys=True, separators=(",", ":")),
    )

    def shuffled(name: str) -> list[dict[str, Any]]:
        copied = list(items)
        seed = int.from_bytes(
            sha256(f"{manifest['review_set_id']}:{name}".encode()).digest(), "big"
        )
        random.Random(seed).shuffle(copied)
        return copied

    ordered = shuffled(slot)
    if slot == "independent" and len(ordered) > 1 and ordered == shuffled("primary"):
        ordered = ordered[1:] + ordered[:1]
    return ordered


def _export_bound_items(
    directory: Path,
    *,
    slot: str,
    selected: list[dict[str, Any]],
    worksheet_path: Path,
    keymap_path: Path,
) -> ExportResult:
    manifest, _ = _load_review_files(directory)
    items: list[dict[str, Any]] = []
    entries: dict[int, LedgerKey] = {}
    for seq, material in enumerate(selected, start=1):
        items.append({"seq": seq, **material["item"]})
        entries[seq] = LedgerKey.from_dict(material["key"])
    batch = BlindedBatch(items)
    worksheet = render_worksheet(batch)
    keymap = KeyMap(
        fixture_version=str(manifest["fixture_version"]),
        entries=entries,
        review_set_id=str(manifest["review_set_id"]),
        slot=slot,
        candidate_set_sha256=str(manifest["candidate_set_sha256"]),
        worksheet_template_sha256=_worksheet_template_sha256(worksheet),
    )
    _atomic_text(worksheet_path, worksheet)
    _write_keymap(keymap_path, keymap)
    return ExportResult(worksheet_path, keymap_path, len(items), 0)


def export_review_slot(
    review_set_dir: Path,
    *,
    slot: ReviewSlot,
    worksheet_path: Path,
    keymap_path: Path,
) -> ExportResult:
    """Export one independently shuffled first-pass worksheet."""
    manifest, materials = _load_review_files(review_set_dir)
    ledger_path = review_set_dir / f"{slot}.jsonl"
    if read_entries(ledger_path, kind=LedgerKind.FORMAL):
        raise AdjudicationError(f"{slot} review already has recorded rulings")
    selected = _ordered_materials(manifest, materials, slot)
    return _export_bound_items(
        review_set_dir,
        slot=slot,
        selected=selected,
        worksheet_path=worksheet_path,
        keymap_path=keymap_path,
    )


def _verify_bound_keymap(
    keymap: KeyMap, manifest: dict[str, Any], *, slot: str, expected: set[LedgerKey]
) -> None:
    if keymap.review_set_id != manifest["review_set_id"]:
        raise AdjudicationError("key map belongs to a different review set")
    if keymap.slot != slot:
        raise AdjudicationError(
            f"key map is bound to slot {keymap.slot!r}, not requested slot {slot!r}"
        )
    if keymap.candidate_set_sha256 != manifest["candidate_set_sha256"]:
        raise AdjudicationError("key map candidate-set hash does not match the manifest")
    if set(keymap.entries.values()) != expected:
        raise AdjudicationError("key map does not cover the expected candidate keys")


def _verify_worksheet_content(keymap: KeyMap, text: str) -> None:
    if keymap.worksheet_template_sha256 is None:
        raise AdjudicationError("key map lacks the worksheet content binding")
    if _worksheet_template_sha256(text) != keymap.worksheet_template_sha256:
        raise AdjudicationError("worksheet content changed outside DECISION or RATIONALE")


def apply_review_slot(
    review_set_dir: Path,
    *,
    slot: ReviewSlot,
    worksheet_path: Path,
    keymap_path: Path,
    decided_at: str,
) -> ImportResult:
    """Atomically import one human review into its bound slot."""
    manifest, materials = _load_review_files(review_set_dir)
    reviewer_id = str(manifest[slot]["reviewer_id"])
    keymap = _read_keymap(keymap_path)
    expected = {LedgerKey.from_dict(item["key"]) for item in materials["items"]}
    _verify_bound_keymap(keymap, manifest, slot=slot, expected=expected)
    if not worksheet_path.is_file():
        raise AdjudicationError(f"no worksheet at {worksheet_path}")
    worksheet = worksheet_path.read_text(encoding="utf-8")
    _verify_worksheet_content(keymap, worksheet)
    decisions = parse_worksheet(worksheet)
    entries = import_decisions(keymap, decisions, adjudicator_id=reviewer_id, decided_at=decided_at)
    ledger_path = review_set_dir / f"{slot}.jsonl"
    if read_entries(ledger_path, kind=LedgerKind.FORMAL):
        raise AdjudicationError(f"{slot} review already has recorded rulings")
    _atomic_entries(ledger_path, entries)
    return ImportResult(len(entries), ledger_path)


def _review_decisions(path: Path) -> dict[LedgerKey, str]:
    return {
        LedgerKey.from_dict(entry["key"]): str(entry["decision"])
        for entry in read_entries(path, kind=LedgerKind.FORMAL)
    }


def _disagreement_keys(review_set_dir: Path, materials: dict[str, Any]) -> set[LedgerKey]:
    expected = {LedgerKey.from_dict(item["key"]) for item in materials["items"]}
    primary = _review_decisions(review_set_dir / "primary.jsonl")
    independent = _review_decisions(review_set_dir / "independent.jsonl")
    if set(primary) != expected or set(independent) != expected:
        raise AdjudicationError("primary and independent review must have 100% coverage first")
    return {key for key in expected if primary[key] != independent[key]}


def export_resolver_review(
    review_set_dir: Path,
    *,
    worksheet_path: Path,
    keymap_path: Path,
) -> ExportResult:
    manifest, materials = _load_review_files(review_set_dir)
    disagreements = _disagreement_keys(review_set_dir, materials)
    selected = [
        item
        for item in _ordered_materials(manifest, materials, "resolver")
        if LedgerKey.from_dict(item["key"]) in disagreements
    ]
    return _export_bound_items(
        review_set_dir,
        slot="resolver",
        selected=selected,
        worksheet_path=worksheet_path,
        keymap_path=keymap_path,
    )


def apply_resolver_review(
    review_set_dir: Path,
    *,
    resolver_id: str,
    worksheet_path: Path,
    keymap_path: Path,
    decided_at: str,
) -> ImportResult:
    manifest, materials = _load_review_files(review_set_dir)
    excluded = {
        str(manifest["primary"]["reviewer_id"]),
        str(manifest["independent"]["reviewer_id"]),
        str(manifest["run_operator_id"]),
        *{str(author) for author in manifest["fixture_author_ids"]},
    }
    if resolver_id in excluded:
        raise AdjudicationError(
            "resolver must differ from reviewers, fixture authors, and operator"
        )
    disagreements = _disagreement_keys(review_set_dir, materials)
    keymap = _read_keymap(keymap_path)
    _verify_bound_keymap(keymap, manifest, slot="resolver", expected=disagreements)
    if not worksheet_path.is_file():
        raise AdjudicationError(f"no worksheet at {worksheet_path}")
    worksheet = worksheet_path.read_text(encoding="utf-8")
    _verify_worksheet_content(keymap, worksheet)
    decisions = parse_worksheet(worksheet)
    entries = import_decisions(keymap, decisions, adjudicator_id=resolver_id, decided_at=decided_at)
    ledger_path = review_set_dir / "resolutions.jsonl"
    if read_entries(ledger_path, kind=LedgerKind.FORMAL):
        raise AdjudicationError("resolver rulings are already recorded")
    updated_manifest = dict(manifest)
    updated_manifest["resolver"] = {
        "reviewer_id": resolver_id,
        "independent_of_fixture_authors": True,
        "independent_of_run_operator": True,
        "independent_of_other_reviewers": True,
    }
    problems = validate_document("review-set", updated_manifest)
    if problems:
        raise AdjudicationError("resolver would make the review-set manifest invalid")
    _atomic_entries(ledger_path, entries)
    _atomic_text(
        review_set_dir / "manifest.json",
        json.dumps(updated_manifest, indent=2, sort_keys=True) + "\n",
    )
    return ImportResult(len(entries), ledger_path)
