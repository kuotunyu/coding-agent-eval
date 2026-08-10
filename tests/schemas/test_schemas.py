"""Schema contracts (design spec §6, §7).

These tests guard the properties that make a schema load-bearing rather than
decorative: that it rejects the specific shapes the protocol forbids. A schema
which accepts everything passes meta-validation just as happily as one that
does its job, so meta-validation alone proves nothing.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from coding_agent_eval.schemas.loader import SCHEMA_DIR, load_schema, schema_names
from coding_agent_eval.schemas.validate import is_valid, validate_document

EXPECTED_SCHEMAS = {
    "fixture",
    "bug",
    "finding",
    "trace-record",
    "ledger-entry",
    "results",
    "known-residual-defects",
    "review-set",
    "suite-registration",
    "task",
}


def validator(name: str) -> Draft202012Validator:
    return Draft202012Validator(load_schema(name))


# --------------------------------------------------------------------- generic


def test_every_expected_schema_exists() -> None:
    assert set(schema_names()) == EXPECTED_SCHEMAS


@pytest.mark.parametrize("name", sorted(EXPECTED_SCHEMAS))
def test_schema_is_valid_draft_2020_12(name: str) -> None:
    Draft202012Validator.check_schema(load_schema(name))


@pytest.mark.parametrize("name", sorted(EXPECTED_SCHEMAS))
def test_schema_declares_identity_and_is_closed(name: str) -> None:
    schema = load_schema(name)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["$id"].endswith(f"{name}.schema.json")
    # An open object schema silently accepts typos in field names.
    assert schema.get("additionalProperties") is False


@pytest.mark.parametrize("path", sorted(SCHEMA_DIR.glob("*.json")))
def test_schema_file_is_utf8_json(path: Path) -> None:
    json.loads(path.read_text(encoding="utf-8"))


# --------------------------------------------------------------------- fixture

VALID_FIXTURE: dict[str, Any] = {
    "schema_version": "0.1",
    "fixture_id": "fx-demo-py",
    "name": "demo",
    "fixture_version": "1.0.0",
    "provenance": "first_party",
    "language": "python",
    "license": "MIT",
    "license_file": "LICENSE",
    "authored_at": "2026-08-05",
    "scope": {
        "in_scope_paths": ["src/**"],
        "out_of_scope_paths": ["tests/**"],
        "in_scope_categories": ["security", "correctness"],
        "in_scope_loc": 1800,
        "loc_tool": "cae-loc 0.1.0",
    },
    "clean_control": {
        "tree_checksum": "sha256:" + "a" * 64,
        "witness_suite": "witness/clean_suite.yaml",
    },
    "known_residual_defects": "known_residual_defects.yaml",
    "environment": {
        "base_image_digest": "sha256:" + "b" * 64,
        "prepared_image_repository": "ghcr.io/kuotunyu/coding-agent-eval-fx-demo-py",
        "prepared_image_tag": "1.0.4",
        "prepared_image_manifest_digest": "sha256:" + "d" * 64,
        "prepared_image_config_digest": "sha256:" + "e" * 64,
        "lock_manifest": "env/env.lock.json",
        "rebuild_recipe": "env/Dockerfile",
        "fingerprint": "sha256:" + "c" * 64,
    },
    "bugs": ["fx-demo-py/B-001"],
}

VALID_CURRENT_FIXTURE = deepcopy(VALID_FIXTURE)


def test_valid_fixture_passes() -> None:
    assert validate_document("fixture", VALID_FIXTURE) == []


def test_valid_current_oci_fixture_passes() -> None:
    assert validate_document("fixture", VALID_CURRENT_FIXTURE) == []


def test_fixture_rejects_mixed_legacy_and_current_image_identity() -> None:
    doc = deepcopy(VALID_CURRENT_FIXTURE)
    doc["environment"]["prepared_image_digest"] = "sha256:" + "f" * 64

    assert not validator("fixture").is_valid(doc)


def test_fixture_rejects_the_legacy_local_image_identity() -> None:
    doc = deepcopy(VALID_FIXTURE)
    doc["environment"] = {
        "base_image_digest": "sha256:" + "b" * 64,
        "prepared_image_tag": "cae/fx-demo-py:1.0.0",
        "prepared_image_digest": "sha256:" + "d" * 64,
        "lock_manifest": "env/env.lock.json",
        "rebuild_recipe": "env/Dockerfile",
        "fingerprint": "sha256:" + "c" * 64,
    }

    assert not validator("fixture").is_valid(doc)


@pytest.mark.parametrize(
    "field",
    [
        "prepared_image_repository",
        "prepared_image_tag",
        "prepared_image_manifest_digest",
        "prepared_image_config_digest",
    ],
)
def test_current_oci_fixture_requires_every_identity_field(field: str) -> None:
    doc = deepcopy(VALID_CURRENT_FIXTURE)
    del doc["environment"][field]

    assert not validator("fixture").is_valid(doc)


def test_fixture_rejects_non_first_party_provenance_at_v0_1() -> None:
    """v0.1 ships only first-party fixtures; upstream provenance arrives in v0.2."""
    for provenance in ("upstream_injected", "upstream_historical"):
        doc = deepcopy(VALID_FIXTURE)
        doc["provenance"] = provenance
        assert not validator("fixture").is_valid(doc), provenance


def test_fixture_rejects_non_mit_license_at_v0_1() -> None:
    doc = deepcopy(VALID_FIXTURE)
    doc["license"] = "GPL-2.0-only"
    assert not validator("fixture").is_valid(doc)


def test_fixture_requires_a_positive_loc_denominator() -> None:
    """in_scope_loc divides a headline metric; zero would produce a division by zero."""
    doc = deepcopy(VALID_FIXTURE)
    doc["scope"]["in_scope_loc"] = 0
    assert not validator("fixture").is_valid(doc)


def test_fixture_rejects_unknown_field() -> None:
    doc = deepcopy(VALID_FIXTURE)
    doc["extra"] = "surprise"
    assert not validator("fixture").is_valid(doc)


def test_fixture_id_must_be_a_slug() -> None:
    doc = deepcopy(VALID_FIXTURE)
    doc["fixture_id"] = "Fx Demo"
    assert not validator("fixture").is_valid(doc)


# ------------------------------------------------------------------------- bug

VALID_BUG: dict[str, Any] = {
    "schema_version": "0.1",
    "bug_id": "fx-demo-py/B-001",
    "fixture_id": "fx-demo-py",
    "fixture_version": "1.0.0",
    "category": "security",
    "subcategory": "non-constant-time-compare",
    "severity": "high",
    "provenance": "injected",
    "authored_at": "2026-08-05",
    "patch": "bugs/B-001.patch",
    "compound_group": None,
    "localization": {
        "primary": {"file": "src/demo/auth.py", "line_start": 42, "line_end": 47},
        "line_tolerance": 8,
        "acceptable_alternates": [],
    },
    "canonical_claim": "Token comparison is not constant time.",
    "canonical_root_cause": "Uses == on secrets, so comparison time depends on the prefix.",
    "witness": {
        "contract_version": "0.1",
        "prepare": ["python", "-m", "pip", "install", "-e", "."],
        "command": ["python", "-m", "pytest", "-q", "witness_B_001.py"],
        "workdir": ".",
        "timeout_seconds": 120,
        "environment": {"TZ": "UTC", "LC_ALL": "C.UTF-8", "PYTHONHASHSEED": "0"},
        "expected_clean": {
            "exit_code": 0,
            "stdout_contains": ["1 passed"],
            "stdout_not_contains": [],
        },
        "expected_mutated": {
            "exit_code": 1,
            "stdout_contains": ["1 failed"],
            "stdout_not_contains": [],
        },
        "artifacts": [{"path": "witness/B-001/witness_B_001.py", "sha256": "d" * 64}],
        "deterministic": True,
        "overlay_target": "/workspace/witness",
    },
}


def test_valid_bug_passes() -> None:
    assert validate_document("bug", VALID_BUG) == []


def test_bug_rejects_witness_whose_expectations_are_identical() -> None:
    """A witness expecting the same result on both trees proves nothing (spec §7).

    This is a sibling comparison, which Draft 2020-12 cannot express, so it is
    enforced as a named structural rule rather than left as a comment.
    """
    doc = deepcopy(VALID_BUG)
    doc["witness"]["expected_mutated"] = deepcopy(doc["witness"]["expected_clean"])
    problems = validate_document("bug", doc)
    assert [p.rule for p in problems] == ["WITNESS_DISTINGUISHES"]
    assert problems[0].pointer == "/witness/expected_mutated"


def test_witness_distinguishing_rule_is_not_fooled_by_key_order() -> None:
    doc = deepcopy(VALID_BUG)
    clean = doc["witness"]["expected_clean"]
    doc["witness"]["expected_mutated"] = {
        "stdout_not_contains": list(clean["stdout_not_contains"]),
        "exit_code": clean["exit_code"],
        "stdout_contains": list(clean["stdout_contains"]),
    }
    assert [p.rule for p in validate_document("bug", doc)] == ["WITNESS_DISTINGUISHES"]


def test_witness_differing_only_in_exit_code_is_accepted() -> None:
    doc = deepcopy(VALID_BUG)
    doc["witness"]["expected_mutated"] = deepcopy(doc["witness"]["expected_clean"])
    doc["witness"]["expected_mutated"]["exit_code"] = 7
    assert validate_document("bug", doc) == []


def test_bug_accepts_non_pytest_witness_shape() -> None:
    """expected_mutated may be an exit code plus a marker, not a test-framework line."""
    doc = deepcopy(VALID_BUG)
    doc["witness"]["command"] = ["node", "--test", "harness.mjs"]
    doc["witness"]["expected_mutated"] = {
        "exit_code": 7,
        "stdout_contains": ["LOST_UPDATE_DETECTED"],
        "stdout_not_contains": [],
    }
    assert validate_document("bug", doc) == []


def test_bug_requires_deterministic_witness_at_v0_1() -> None:
    doc = deepcopy(VALID_BUG)
    doc["witness"]["deterministic"] = False
    assert not validator("bug").is_valid(doc)


def test_bug_rejects_injected_provenance_other_than_injected_at_v0_1() -> None:
    doc = deepcopy(VALID_BUG)
    doc["provenance"] = "historical"
    assert not validator("bug").is_valid(doc)


def test_bug_rejects_inverted_line_range() -> None:
    """An inverted range would overlap nothing, so every finding would miss it."""
    doc = deepcopy(VALID_BUG)
    doc["localization"]["primary"]["line_end"] = 1
    problems = validate_document("bug", doc)
    assert [p.rule for p in problems] == ["LINE_RANGE_ORDER"]
    assert problems[0].pointer == "/localization/primary/line_end"


def test_bug_rejects_inverted_range_in_an_alternate() -> None:
    doc = deepcopy(VALID_BUG)
    doc["localization"]["acceptable_alternates"] = [
        {"file": "src/demo/api.py", "line_start": 90, "line_end": 12}
    ]
    problems = validate_document("bug", doc)
    assert [p.pointer for p in problems] == ["/localization/acceptable_alternates/0/line_end"]


def test_finding_rejects_inverted_line_range() -> None:
    doc = deepcopy(VALID_FINDING)
    doc["line_end"] = 1
    assert [p.rule for p in validate_document("finding", doc)] == ["LINE_RANGE_ORDER"]


def test_bug_id_must_be_namespaced_by_fixture() -> None:
    doc = deepcopy(VALID_BUG)
    doc["bug_id"] = "B-001"
    assert not validator("bug").is_valid(doc)


def test_bug_requires_a_nonempty_canonical_root_cause() -> None:
    """The adjudicator compares against this text; an empty one makes the ruling arbitrary."""
    doc = deepcopy(VALID_BUG)
    doc["canonical_root_cause"] = ""
    assert not validator("bug").is_valid(doc)


# --------------------------------------------------------------------- finding

VALID_FINDING: dict[str, Any] = {
    "id": "F-001",
    "file": "src/demo/auth.py",
    "line_start": 42,
    "line_end": 47,
    "category": "security",
    "severity": "high",
    "claim": "Session token comparison leaks timing information.",
    "root_cause": "The comparison short-circuits on the first differing byte.",
    "evidence": "auth.py:44 compares with == inside verify_token.",
    "suggested_verification": "Time verify_token against prefixes of a valid token.",
}

FINDING_FIELDS = tuple(VALID_FINDING)


def test_valid_finding_passes() -> None:
    assert validate_document("finding", VALID_FINDING) == []


@pytest.mark.parametrize("field", FINDING_FIELDS)
def test_finding_requires_every_field(field: str) -> None:
    doc = deepcopy(VALID_FINDING)
    del doc[field]
    assert not validator("finding").is_valid(doc), field


def test_finding_is_strict_for_provider_tool_calling() -> None:
    schema = load_schema("finding")
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(FINDING_FIELDS)


def test_finding_rejects_unknown_field() -> None:
    doc = deepcopy(VALID_FINDING)
    doc["confidence"] = 0.9
    assert not validator("finding").is_valid(doc)


def test_finding_rejects_empty_evidence() -> None:
    """Evidence is part of finding_hash and of the ruling; blank evidence is not a finding."""
    doc = deepcopy(VALID_FINDING)
    doc["evidence"] = "   "
    assert not validator("finding").is_valid(doc)


def test_finding_windows_path_is_rejected() -> None:
    """Paths are repo-relative POSIX so matching is platform independent."""
    doc = deepcopy(VALID_FINDING)
    doc["file"] = "src\\demo\\auth.py"
    assert not validator("finding").is_valid(doc)


def test_finding_absolute_path_is_rejected() -> None:
    doc = deepcopy(VALID_FINDING)
    doc["file"] = "/src/demo/auth.py"
    assert not validator("finding").is_valid(doc)


# ---------------------------------------------------------------- trace record

TRACE_MANIFEST_DIGEST = "sha256:" + "a" * 64
TRACE_CONFIG_DIGEST = "sha256:" + "b" * 64
VALID_TRACE_0_2_HEADER: dict[str, Any] = {
    "schema_version": "0.2.0",
    "seq": 0,
    "ts": "2026-08-11T00:00:00+00:00",
    "event": "run_header",
    "payload": {
        "image_ref": ("ghcr.io/kuotunyu/coding-agent-eval-fx-demo-py@" + TRACE_MANIFEST_DIGEST),
        "image_manifest_digest": TRACE_MANIFEST_DIGEST,
        "image_config_digest": TRACE_CONFIG_DIGEST,
        "sandbox_profile": "measure",
        "tool_backend": "measure_container:" + TRACE_MANIFEST_DIGEST,
    },
}


def test_trace_0_2_measure_header_carries_a_complete_oci_identity() -> None:
    assert validate_document("trace-record", VALID_TRACE_0_2_HEADER) == []


def test_trace_0_2_host_header_is_explicitly_non_oci() -> None:
    doc = deepcopy(VALID_TRACE_0_2_HEADER)
    doc["payload"].update(
        {
            "image_ref": None,
            "image_manifest_digest": None,
            "image_config_digest": None,
            "sandbox_profile": "host_process",
            "tool_backend": "host_process",
        }
    )

    assert validate_document("trace-record", doc) == []


@pytest.mark.parametrize("field", ["image_ref", "image_manifest_digest", "image_config_digest"])
def test_trace_0_2_header_requires_every_oci_identity_field(field: str) -> None:
    doc = deepcopy(VALID_TRACE_0_2_HEADER)
    del doc["payload"][field]

    assert not validator("trace-record").is_valid(doc)


def test_trace_0_2_rejects_a_tag_only_image_reference() -> None:
    doc = deepcopy(VALID_TRACE_0_2_HEADER)
    doc["payload"]["image_ref"] = "ghcr.io/kuotunyu/coding-agent-eval-fx-demo-py:1.0.0"

    assert not validator("trace-record").is_valid(doc)


def test_trace_0_2_rejects_a_backend_for_a_different_manifest() -> None:
    doc = deepcopy(VALID_TRACE_0_2_HEADER)
    doc["payload"]["tool_backend"] = "measure_container:sha256:" + "c" * 64

    problems = validate_document("trace-record", doc)

    assert [problem.rule for problem in problems] == ["TRACE_OCI_IDENTITY_MATCH"]


def test_trace_0_1_header_remains_readable() -> None:
    doc = deepcopy(VALID_TRACE_0_2_HEADER)
    doc["schema_version"] = "0.1.0"
    doc["payload"] = {"image_digest": TRACE_MANIFEST_DIGEST}

    assert validate_document("trace-record", doc) == []


# --------------------------------------------------- known residual defects


def test_empty_residual_defects_is_the_only_valid_shape_at_v0_1() -> None:
    """v0.1 requires clean fixtures to be actually clean (spec §6.8)."""
    doc = {
        "schema_version": "0.1",
        "fixture_id": "fx-demo-py",
        "fixture_version": "1.0.0",
        "defects": [],
    }
    validator("known-residual-defects").validate(doc)

    doc["defects"] = [{"residual_id": "R-001"}]
    assert not validator("known-residual-defects").is_valid(doc)


# ------------------------------------------------------------------ ledger


VALID_LEDGER_ENTRY: dict[str, Any] = {
    "key": {
        "fixture_version": "1.0.0",
        "bug_id": "fx-demo-py/B-001",
        "finding_hash": "e" * 64,
    },
    "decision": "same_root_cause",
    "rationale": "Same mechanism and the cited line does contain the comparison.",
    "adjudication_protocol_version": "0.1.0",
    "adjudicator_id": "A1",
    "decided_at": "2026-08-05",
    "entry_hash": "f" * 64,
}


def test_valid_ledger_entry_passes() -> None:
    validator("ledger-entry").validate(VALID_LEDGER_ENTRY)


def test_ledger_entry_accepts_the_three_decisions_only() -> None:
    for decision in ("same_root_cause", "different_root_cause", "insufficient"):
        doc = deepcopy(VALID_LEDGER_ENTRY)
        doc["decision"] = decision
        validator("ledger-entry").validate(doc)
    doc = deepcopy(VALID_LEDGER_ENTRY)
    doc["decision"] = "probably"
    assert not validator("ledger-entry").is_valid(doc)


def test_ledger_entry_requires_a_rationale() -> None:
    """Rationale is what lets a third party re-examine a ruling."""
    doc = deepcopy(VALID_LEDGER_ENTRY)
    doc["rationale"] = ""
    assert not validator("ledger-entry").is_valid(doc)


def test_synthetic_adjudicator_id_is_schema_valid() -> None:
    """The schema permits it; the formal-ledger loader is what refuses it (EV3)."""
    doc = deepcopy(VALID_LEDGER_ENTRY)
    doc["adjudicator_id"] = "SYNTHETIC-fixture-author"
    validator("ledger-entry").validate(doc)


def test_ledger_entry_rejects_a_timestamp_finer_than_a_day() -> None:
    doc = deepcopy(VALID_LEDGER_ENTRY)
    doc["decided_at"] = "2026-08-05T12:34:56Z"
    assert not validator("ledger-entry").is_valid(doc)


# ---------------------------------------------------- validator reporting


def test_problems_are_addressed_by_json_pointer() -> None:
    doc = deepcopy(VALID_BUG)
    doc["localization"]["primary"]["line_start"] = 0
    problems = validate_document("bug", doc)
    assert any(p.pointer == "/localization/primary/line_start" for p in problems)


def test_structural_rules_are_suppressed_while_the_shape_is_wrong() -> None:
    """Reporting a sibling comparison against a malformed document is noise."""
    doc = deepcopy(VALID_BUG)
    del doc["witness"]["expected_mutated"]
    doc["localization"]["primary"]["line_end"] = 1
    rules = {p.rule for p in validate_document("bug", doc)}
    assert rules == {"schema"}


def test_is_valid_agrees_with_validate_document() -> None:
    assert is_valid("finding", VALID_FINDING)
    broken = deepcopy(VALID_FINDING)
    broken["line_end"] = 1
    assert not is_valid("finding", broken)


def test_unknown_schema_name_raises() -> None:
    from coding_agent_eval.schemas.loader import SchemaNotFoundError

    with pytest.raises(SchemaNotFoundError):
        load_schema("no-such-schema")


def test_schema_directory_candidates_prefer_the_packaged_copy() -> None:
    """An installed wheel must not depend on a repository root being present."""
    from coding_agent_eval.schemas.loader import SCHEMA_DIR_CANDIDATES

    assert len(SCHEMA_DIR_CANDIDATES) == 2
    packaged, repo_root = SCHEMA_DIR_CANDIDATES
    assert packaged.name == "_schemas"
    assert packaged.parent.name == "coding_agent_eval"
    assert repo_root.name == "schemas"


def test_missing_schema_directory_raises_rather_than_reporting_none() -> None:
    from coding_agent_eval.schemas import loader

    original = loader.SCHEMA_DIR_CANDIDATES
    loader.SCHEMA_DIR_CANDIDATES = (Path("/nonexistent-a"), Path("/nonexistent-b"))
    try:
        with pytest.raises(loader.SchemaNotFoundError):
            loader.schema_dir()
    finally:
        loader.SCHEMA_DIR_CANDIDATES = original


def test_every_committed_result_conforms_to_the_public_schema(repo_root: Path) -> None:
    result_paths = sorted((repo_root / "runs").glob("baseline-*/results.json"))
    assert len(result_paths) == 4
    result_validator = validator("results")
    for path in result_paths:
        document = json.loads(path.read_text(encoding="utf-8"))
        errors = sorted(error.message for error in result_validator.iter_errors(document))
        assert errors == [], f"{path.relative_to(repo_root)}: {errors}"


def test_synthetic_result_cannot_claim_publishable(repo_root: Path) -> None:
    path = next((repo_root / "runs").glob("baseline-*/results.json"))
    document = json.loads(path.read_text(encoding="utf-8"))
    document["publishable"] = True

    assert not validator("results").is_valid(document)
