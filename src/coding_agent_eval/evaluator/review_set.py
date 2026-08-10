"""Evidence-bound consensus from two blinded human reviews."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coding_agent_eval import PUBLICATION_TRACE_SCHEMA_VERSION
from coding_agent_eval.evaluator.ledger import (
    Decision,
    LedgerKey,
    LedgerKind,
    read_entries,
)
from coding_agent_eval.schemas.validate import validate_document


class ReviewSetError(RuntimeError):
    """A review set cannot support a deterministic, independent decision."""


@dataclass(frozen=True)
class ReviewSetEvidence:
    """Immutable run facts a review-set manifest must reproduce exactly."""

    run_id: str
    fixture_id: str
    fixture_version: str
    tree_checksum: str
    trace_sha256: str
    findings_sha256: str
    trace_schema_version: str
    environment_fingerprint: str
    candidate_keys: tuple[LedgerKey, ...]


@dataclass(frozen=True)
class Ruling:
    decision: Decision
    rationale: str
    entry_hash: str


@dataclass(frozen=True)
class ReviewSet:
    review_set_id: str
    manifest: Mapping[str, object]
    primary: Mapping[LedgerKey, Ruling]
    independent: Mapping[LedgerKey, Ruling]
    resolutions: Mapping[LedgerKey, Ruling]
    decisions: Mapping[LedgerKey, Decision]
    publication_provenance: Mapping[str, str]

    @property
    def decision_source(self) -> str:
        return "dual_review"

    @property
    def publishable(self) -> bool:
        return True

    @property
    def publication_reason(self) -> str:
        return "dual_review_complete"

    def decision(self, key: LedgerKey) -> Decision | None:
        return self.decisions.get(key)

    def __len__(self) -> int:
        return len(self.decisions)


def _key_order(key: LedgerKey) -> tuple[str, str, str]:
    return key.fixture_version, key.bug_id, key.finding_hash


def candidate_set_sha256(keys: Iterable[LedgerKey]) -> str:
    """Hash candidate keys in protocol order, independent of worksheet shuffle."""
    payload = [key.as_dict() for key in sorted(keys, key=_key_order)]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _file_sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _load_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ReviewSetError(f"missing review-set manifest at {path}")
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReviewSetError(f"review-set manifest is not valid JSON: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ReviewSetError("review-set manifest must be a JSON object")
    document: dict[str, Any] = loaded
    problems = validate_document("review-set", document)
    if problems:
        rendered = "; ".join(problem.render() for problem in problems)
        raise ReviewSetError(f"review-set manifest is invalid: {rendered}")
    return document


def _check_evidence(manifest: Mapping[str, Any], evidence: ReviewSetEvidence) -> None:
    expected = {
        "run_id": evidence.run_id,
        "fixture_id": evidence.fixture_id,
        "fixture_version": evidence.fixture_version,
        "tree_checksum": evidence.tree_checksum,
        "trace_sha256": evidence.trace_sha256,
        "findings_sha256": evidence.findings_sha256,
        "trace_schema_version": evidence.trace_schema_version,
        "environment_fingerprint": evidence.environment_fingerprint,
        "candidate_set_sha256": candidate_set_sha256(evidence.candidate_keys),
    }
    for field, value in expected.items():
        if manifest.get(field) != value:
            raise ReviewSetError(
                f"{field} drift: manifest records {manifest.get(field)!r}, expected {value!r}"
            )
    if manifest["trace_schema_version"] != PUBLICATION_TRACE_SCHEMA_VERSION:
        raise ReviewSetError(
            f"trace_schema_version must be {PUBLICATION_TRACE_SCHEMA_VERSION} for dual review"
        )


def _role_id(manifest: Mapping[str, Any], role: str) -> str | None:
    record = manifest[role]
    if record is None:
        return None
    return str(record["reviewer_id"])


def _require_attestations(record: Mapping[str, Any], role: str) -> None:
    for field in (
        "independent_of_fixture_authors",
        "independent_of_run_operator",
        "independent_of_other_reviewers",
    ):
        if record[field] is not True:
            raise ReviewSetError(f"{role} reviewer must attest {field}")


def _check_reviewers(manifest: Mapping[str, Any]) -> None:
    primary_id = _role_id(manifest, "primary")
    independent_id = _role_id(manifest, "independent")
    if primary_id == independent_id:
        raise ReviewSetError("primary and independent reviewer IDs must be distinct")

    independent = manifest["independent"]
    assert isinstance(independent, Mapping)
    _require_attestations(independent, "independent")
    fixture_authors = set(manifest["fixture_author_ids"])
    if independent_id in fixture_authors:
        raise ReviewSetError("independent reviewer cannot be a fixture author")
    if independent_id == manifest["run_operator_id"]:
        raise ReviewSetError("independent reviewer cannot be the run operator")


def _check_resolver(manifest: Mapping[str, Any]) -> str:
    resolver = manifest["resolver"]
    if not isinstance(resolver, Mapping):
        raise ReviewSetError("unresolved disagreements require a third human resolver")
    _require_attestations(resolver, "resolver")
    resolver_id = str(resolver["reviewer_id"])
    excluded = {
        _role_id(manifest, "primary"),
        _role_id(manifest, "independent"),
        str(manifest["run_operator_id"]),
        *{str(author) for author in manifest["fixture_author_ids"]},
    }
    if resolver_id in excluded:
        raise ReviewSetError("resolver must differ from reviewers, fixture authors, and operator")
    return resolver_id


def _load_rulings(path: Path, reviewer_id: str) -> dict[LedgerKey, Ruling]:
    if not path.is_file():
        raise ReviewSetError(f"missing review ledger {path.name}")
    entries = read_entries(path, kind=LedgerKind.FORMAL)
    rulings: dict[LedgerKey, Ruling] = {}
    for entry in entries:
        if entry["adjudicator_id"] != reviewer_id:
            raise ReviewSetError(
                f"{path.name} contains reviewer {entry['adjudicator_id']!r}, "
                f"expected {reviewer_id!r}"
            )
        key = LedgerKey.from_dict(entry["key"])
        rulings[key] = Ruling(
            decision=Decision(entry["decision"]),
            rationale=str(entry["rationale"]),
            entry_hash=str(entry["entry_hash"]),
        )
    return rulings


def _check_coverage(
    role: str, rulings: Mapping[LedgerKey, Ruling], expected: set[LedgerKey]
) -> None:
    actual = set(rulings)
    if actual != expected:
        missing = sorted(expected - actual, key=_key_order)
        extra = sorted(actual - expected, key=_key_order)
        raise ReviewSetError(
            f"{role} coverage mismatch: {len(missing)} missing, {len(extra)} unexpected"
        )


def load_review_set(directory: Path, *, evidence: ReviewSetEvidence) -> ReviewSet:
    """Load and resolve a complete review set, refusing every partial state."""
    keys = evidence.candidate_keys
    if len(set(keys)) != len(keys):
        raise ReviewSetError("candidate set contains duplicate keys")

    manifest_path = directory / "manifest.json"
    manifest = _load_manifest(manifest_path)
    _check_evidence(manifest, evidence)
    _check_reviewers(manifest)

    primary_id = _role_id(manifest, "primary")
    independent_id = _role_id(manifest, "independent")
    assert primary_id is not None and independent_id is not None
    primary = _load_rulings(directory / "primary.jsonl", primary_id)
    independent = _load_rulings(directory / "independent.jsonl", independent_id)
    expected = set(keys)
    _check_coverage("primary", primary, expected)
    _check_coverage("independent", independent, expected)

    disagreements = {
        key for key in expected if primary[key].decision is not independent[key].decision
    }
    resolver_record = manifest["resolver"]
    if resolver_record is None:
        resolutions = _load_rulings(directory / "resolutions.jsonl", "resolver-missing")
        if disagreements or resolutions:
            raise ReviewSetError("unresolved disagreements require a third human resolver")
    else:
        resolver_id = _check_resolver(manifest)
        resolutions = _load_rulings(directory / "resolutions.jsonl", resolver_id)

    if resolver_record is not None and not disagreements and not resolutions:
        raise ReviewSetError("resolver role is present even though there are no disagreements")

    unexpected_resolutions = set(resolutions) - disagreements
    if unexpected_resolutions:
        raise ReviewSetError("resolution ledger contains a ruling for an agreed pair")
    unresolved = disagreements - set(resolutions)
    if unresolved:
        raise ReviewSetError(f"{len(unresolved)} disagreement pairs remain unresolved")

    decisions: dict[LedgerKey, Decision] = {}
    for key in sorted(expected, key=_key_order):
        decisions[key] = (
            resolutions[key].decision if key in disagreements else primary[key].decision
        )

    provenance = {
        "review_set_id": str(manifest["review_set_id"]),
        "review_set_manifest_sha256": _file_sha256(manifest_path),
        "primary_review_sha256": _file_sha256(directory / "primary.jsonl"),
        "independent_review_sha256": _file_sha256(directory / "independent.jsonl"),
        "resolutions_sha256": _file_sha256(directory / "resolutions.jsonl"),
    }
    return ReviewSet(
        review_set_id=str(manifest["review_set_id"]),
        manifest=manifest,
        primary=primary,
        independent=independent,
        resolutions=resolutions,
        decisions=decisions,
        publication_provenance=provenance,
    )


__all__ = [
    "ReviewSet",
    "ReviewSetError",
    "ReviewSetEvidence",
    "Ruling",
    "candidate_set_sha256",
    "load_review_set",
]
