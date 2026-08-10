"""Dual-review consensus is complete, independent, and evidence-bound."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from coding_agent_eval.evaluator.ledger import Decision, LedgerKey, build_entry, write_entries
from coding_agent_eval.evaluator.review_set import (
    ReviewSetError,
    ReviewSetEvidence,
    candidate_set_sha256,
    load_review_set,
)

KEY = LedgerKey("1.0.0", "fx-taskq-py/B-001", "a" * 64)
OTHER_KEY = LedgerKey("1.0.0", "fx-taskq-py/B-002", "b" * 64)
SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64


def evidence(*, keys: tuple[LedgerKey, ...] = (KEY, OTHER_KEY)) -> ReviewSetEvidence:
    return ReviewSetEvidence(
        run_id="reference-fx-taskq-py-mutated",
        fixture_id="fx-taskq-py",
        fixture_version="1.0.0",
        tree_checksum=SHA_A,
        trace_sha256=SHA_B,
        findings_sha256=SHA_C,
        bugs_sha256="sha256:" + "f" * 64,
        fixture_manifest_sha256="sha256:" + "e" * 64,
        trace_schema_version="0.2.0",
        environment_fingerprint="sha256:" + "d" * 64,
        candidate_keys=keys,
    )


def role(
    reviewer_id: str,
    *,
    fixture: bool,
    operator: bool,
    reviewers: bool,
) -> dict[str, object]:
    return {
        "reviewer_id": reviewer_id,
        "independent_of_fixture_authors": fixture,
        "independent_of_run_operator": operator,
        "independent_of_other_reviewers": reviewers,
    }


def entry(key: LedgerKey, decision: str, reviewer_id: str) -> dict[str, object]:
    return build_entry(
        key=key,
        decision=decision,
        rationale=f"{reviewer_id} reviewed the blinded candidate.",
        adjudicator_id=reviewer_id,
        decided_at="2026-08-11",
    )


def write_review_set(
    root: Path,
    *,
    expected: ReviewSetEvidence | None = None,
    primary: dict[LedgerKey, str] | None = None,
    independent: dict[LedgerKey, str] | None = None,
    resolutions: dict[LedgerKey, str] | None = None,
    primary_id: str = "kuotunyu",
    independent_id: str = "reviewer-b",
    resolver_id: str | None = None,
) -> tuple[Path, ReviewSetEvidence]:
    bound = expected or evidence()
    primary = primary or {key: "same_root_cause" for key in bound.candidate_keys}
    independent = independent or dict(primary)
    resolutions = resolutions or {}
    root.mkdir(parents=True)

    manifest = {
        "schema_version": "1.0.0",
        "review_set_id": "rs-reference-fx-taskq-py-mutated",
        "run_id": bound.run_id,
        "fixture_id": bound.fixture_id,
        "fixture_version": bound.fixture_version,
        "tree_checksum": bound.tree_checksum,
        "trace_sha256": bound.trace_sha256,
        "findings_sha256": bound.findings_sha256,
        "bugs_sha256": bound.bugs_sha256,
        "fixture_manifest_sha256": bound.fixture_manifest_sha256,
        "candidate_set_sha256": candidate_set_sha256(bound.candidate_keys),
        "trace_schema_version": bound.trace_schema_version,
        "environment_fingerprint": bound.environment_fingerprint,
        "fixture_author_ids": ["kuotunyu"],
        "run_operator_id": "kuotunyu",
        "primary": role(primary_id, fixture=False, operator=False, reviewers=False),
        "independent": role(independent_id, fixture=True, operator=True, reviewers=True),
        "resolver": (
            None
            if resolver_id is None
            else role(resolver_id, fixture=True, operator=True, reviewers=True)
        ),
        "ledgers": {
            "primary": "primary.jsonl",
            "independent": "independent.jsonl",
            "resolutions": "resolutions.jsonl",
        },
    }
    materials = {
        "review_set_id": manifest["review_set_id"],
        "candidate_set_sha256": manifest["candidate_set_sha256"],
        "fixture_version": bound.fixture_version,
        "items": [
            {"key": key.as_dict(), "item": {"blinded_context": f"candidate-{index}"}}
            for index, key in enumerate(bound.candidate_keys, start=1)
        ],
    }
    encoded_materials = json.dumps(
        materials, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    manifest["candidate_materials_sha256"] = f"sha256:{sha256(encoded_materials).hexdigest()}"
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "candidates.json").write_text(
        json.dumps(materials, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_entries(
        root / "primary.jsonl",
        [entry(key, decision, primary_id) for key, decision in primary.items()],
    )
    write_entries(
        root / "independent.jsonl",
        [entry(key, decision, independent_id) for key, decision in independent.items()],
    )
    write_entries(
        root / "resolutions.jsonl",
        [
            entry(key, decision, resolver_id or "resolver-missing")
            for key, decision in resolutions.items()
        ],
    )
    return root, bound


def read_manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "manifest.json").read_text(encoding="utf-8"))


def replace_manifest(root: Path, manifest: dict[str, object]) -> None:
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_agreement_resolves_deterministically(tmp_path: Path) -> None:
    root, bound = write_review_set(tmp_path / "review-set")

    review_set = load_review_set(root, evidence=bound)

    assert review_set.decision(KEY) is Decision.SAME_ROOT_CAUSE
    assert review_set.decision(OTHER_KEY) is Decision.SAME_ROOT_CAUSE
    assert review_set.publishable is True
    assert review_set.publication_reason == "dual_review_complete"


def test_disagreement_without_resolution_is_refused(tmp_path: Path) -> None:
    root, bound = write_review_set(
        tmp_path / "review-set",
        independent={KEY: "different_root_cause", OTHER_KEY: "same_root_cause"},
    )

    with pytest.raises(ReviewSetError, match="unresolved"):
        load_review_set(root, evidence=bound)


def test_third_human_resolves_only_the_disagreement(tmp_path: Path) -> None:
    root, bound = write_review_set(
        tmp_path / "review-set",
        independent={KEY: "different_root_cause", OTHER_KEY: "same_root_cause"},
        resolutions={KEY: "insufficient"},
        resolver_id="reviewer-c",
    )

    review_set = load_review_set(root, evidence=bound)

    assert review_set.decision(KEY) is Decision.INSUFFICIENT
    assert review_set.decision(OTHER_KEY) is Decision.SAME_ROOT_CAUSE


def test_partial_primary_or_independent_coverage_is_refused(tmp_path: Path) -> None:
    root, bound = write_review_set(
        tmp_path / "review-set",
        independent={KEY: "same_root_cause"},
    )

    with pytest.raises(ReviewSetError, match="coverage"):
        load_review_set(root, evidence=bound)


def test_duplicate_reviewers_are_refused(tmp_path: Path) -> None:
    root, bound = write_review_set(
        tmp_path / "review-set", primary_id="reviewer-a", independent_id="reviewer-a"
    )

    with pytest.raises(ReviewSetError, match="distinct"):
        load_review_set(root, evidence=bound)


def test_independent_reviewer_cannot_be_a_fixture_author(tmp_path: Path) -> None:
    root, bound = write_review_set(
        tmp_path / "review-set", primary_id="reviewer-a", independent_id="kuotunyu"
    )

    with pytest.raises(ReviewSetError, match="fixture author"):
        load_review_set(root, evidence=bound)


@pytest.mark.parametrize("resolver_id", ["reviewer-a", "reviewer-b", "kuotunyu"])
def test_resolver_must_be_independent_of_every_participant(
    resolver_id: str, tmp_path: Path
) -> None:
    root, bound = write_review_set(
        tmp_path / "review-set",
        primary_id="reviewer-a",
        independent_id="reviewer-b",
        independent={KEY: "different_root_cause", OTHER_KEY: "same_root_cause"},
        resolutions={KEY: "same_root_cause"},
        resolver_id=resolver_id,
    )

    with pytest.raises(ReviewSetError, match="resolver"):
        load_review_set(root, evidence=bound)


def test_candidate_set_hash_drift_is_refused(tmp_path: Path) -> None:
    root, bound = write_review_set(tmp_path / "review-set")
    manifest = read_manifest(root)
    manifest["candidate_set_sha256"] = "sha256:" + "f" * 64
    replace_manifest(root, manifest)

    with pytest.raises(ReviewSetError, match="candidate"):
        load_review_set(root, evidence=bound)


def test_candidate_material_drift_is_refused(tmp_path: Path) -> None:
    root, bound = write_review_set(tmp_path / "review-set")
    materials_path = root / "candidates.json"
    materials = json.loads(materials_path.read_text(encoding="utf-8"))
    materials["items"][0]["item"]["blinded_context"] = "tampered"
    materials_path.write_text(json.dumps(materials), encoding="utf-8")

    with pytest.raises(ReviewSetError, match="candidate materials"):
        load_review_set(root, evidence=bound)


def test_trace_0_1_is_readable_history_not_a_review_set_input(tmp_path: Path) -> None:
    root, bound = write_review_set(tmp_path / "review-set")
    manifest = read_manifest(root)
    manifest["trace_schema_version"] = "0.1.0"
    replace_manifest(root, manifest)

    with pytest.raises(ReviewSetError, match="trace_schema_version"):
        load_review_set(root, evidence=bound)


def test_environment_fingerprint_drift_is_refused(tmp_path: Path) -> None:
    root, bound = write_review_set(tmp_path / "review-set")
    manifest = read_manifest(root)
    manifest["environment_fingerprint"] = "sha256:" + "e" * 64
    replace_manifest(root, manifest)

    with pytest.raises(ReviewSetError, match="environment_fingerprint"):
        load_review_set(root, evidence=bound)


def test_resolution_for_an_agreed_pair_is_refused(tmp_path: Path) -> None:
    root, bound = write_review_set(
        tmp_path / "review-set",
        resolutions={KEY: "same_root_cause"},
        resolver_id="reviewer-c",
    )

    with pytest.raises(ReviewSetError, match="agreed"):
        load_review_set(root, evidence=bound)


def test_resolver_role_is_not_activated_without_a_disagreement(tmp_path: Path) -> None:
    root, bound = write_review_set(tmp_path / "review-set", resolver_id="reviewer-c")

    with pytest.raises(ReviewSetError, match="no disagreements"):
        load_review_set(root, evidence=bound)


def test_publication_provenance_hashes_every_review_input(tmp_path: Path) -> None:
    root, bound = write_review_set(tmp_path / "review-set")

    provenance = load_review_set(root, evidence=bound).publication_provenance

    assert provenance["review_set_id"] == "rs-reference-fx-taskq-py-mutated"
    for field in (
        "review_set_manifest_sha256",
        "primary_review_sha256",
        "independent_review_sha256",
        "resolutions_sha256",
    ):
        assert provenance[field].startswith("sha256:")
        assert len(provenance[field]) == 71
