"""Fixture-directory validation (design spec §6.1, §6.2, §6.8).

Schema validation checks one document at a time, which cannot catch the failures
that actually happen when a fixture is edited: a bug left behind after a version
bump, a manifest file nobody listed, a listed bug with no manifest. Those are
cross-file properties, so they are checked as such.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from coding_agent_eval.cli import main
from coding_agent_eval.schemas.fixture_dir import validate_fixture_dir, validate_fixture_root

from .test_schemas import VALID_BUG, VALID_FIXTURE


def write_fixture(
    root: Path,
    *,
    fixture: dict[str, Any] | None = None,
    bugs: list[dict[str, Any]] | None = None,
    residual: dict[str, Any] | None = None,
) -> Path:
    """Materialise a fixture directory. Defaults produce a valid one."""
    manifest = deepcopy(fixture if fixture is not None else VALID_FIXTURE)
    bug_docs = deepcopy(bugs if bugs is not None else [VALID_BUG])

    fixture_dir = root / manifest["fixture_id"]
    (fixture_dir / "bugs").mkdir(parents=True, exist_ok=True)

    (fixture_dir / "fixture.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=True), encoding="utf-8"
    )
    for bug in bug_docs:
        name = bug["bug_id"].split("/")[-1]
        (fixture_dir / "bugs" / f"{name}.yaml").write_text(
            yaml.safe_dump(bug, sort_keys=True), encoding="utf-8"
        )

    residual_doc = deepcopy(
        residual
        if residual is not None
        else {
            "schema_version": "0.1",
            "fixture_id": manifest["fixture_id"],
            "fixture_version": manifest["fixture_version"],
            "defects": [],
        }
    )
    (fixture_dir / manifest["known_residual_defects"]).write_text(
        yaml.safe_dump(residual_doc, sort_keys=True), encoding="utf-8"
    )
    return fixture_dir


def rules(problems: list[Any]) -> list[str]:
    return [p.rule for p in problems]


def test_valid_fixture_directory_passes(tmp_path: Path) -> None:
    assert validate_fixture_dir(write_fixture(tmp_path)) == []


def test_missing_fixture_manifest_is_reported(tmp_path: Path) -> None:
    fixture_dir = write_fixture(tmp_path)
    (fixture_dir / "fixture.yaml").unlink()
    assert rules(validate_fixture_dir(fixture_dir)) == ["MISSING_MANIFEST"]


def test_bug_declaring_a_different_fixture_is_reported(tmp_path: Path) -> None:
    bug = deepcopy(VALID_BUG)
    bug["fixture_id"] = "fx-other-py"
    problems = validate_fixture_dir(write_fixture(tmp_path, bugs=[bug]))
    assert "BUG_FIXTURE_MISMATCH" in rules(problems)


def test_bug_left_behind_after_a_version_bump_is_reported(tmp_path: Path) -> None:
    """The failure that actually happens: the fixture is bumped, a bug is not."""
    fixture = deepcopy(VALID_FIXTURE)
    fixture["fixture_version"] = "1.1.0"
    problems = validate_fixture_dir(write_fixture(tmp_path, fixture=fixture))
    assert "BUG_VERSION_MISMATCH" in rules(problems)


def test_listed_bug_without_a_manifest_is_reported(tmp_path: Path) -> None:
    fixture = deepcopy(VALID_FIXTURE)
    fixture["bugs"] = ["fx-demo-py/B-001", "fx-demo-py/B-002"]
    problems = validate_fixture_dir(write_fixture(tmp_path, fixture=fixture))
    assert rules(problems) == ["BUG_MANIFEST_MISSING"]
    assert "B-002" in problems[0].message


def test_manifest_not_listed_in_the_fixture_is_reported(tmp_path: Path) -> None:
    """An unlisted bug would never be scored, and nothing else would notice."""
    extra = deepcopy(VALID_BUG)
    extra["bug_id"] = "fx-demo-py/B-002"
    problems = validate_fixture_dir(write_fixture(tmp_path, bugs=[VALID_BUG, extra]))
    assert rules(problems) == ["BUG_NOT_LISTED"]
    assert "B-002" in problems[0].message


def test_bug_id_disagreeing_with_its_filename_is_reported(tmp_path: Path) -> None:
    fixture_dir = write_fixture(tmp_path)
    (fixture_dir / "bugs" / "B-001.yaml").rename(fixture_dir / "bugs" / "B-009.yaml")
    assert "BUG_FILENAME_MISMATCH" in rules(validate_fixture_dir(fixture_dir))


def test_schema_failure_inside_a_bug_is_surfaced_with_its_pointer(tmp_path: Path) -> None:
    bug = deepcopy(VALID_BUG)
    bug["severity"] = "catastrophic"
    problems = validate_fixture_dir(write_fixture(tmp_path, bugs=[bug]))
    assert any(p.pointer.endswith("/severity") for p in problems)


def test_structural_rule_inside_a_bug_is_surfaced(tmp_path: Path) -> None:
    bug = deepcopy(VALID_BUG)
    bug["witness"]["expected_mutated"] = deepcopy(bug["witness"]["expected_clean"])
    assert "WITNESS_DISTINGUISHES" in rules(
        validate_fixture_dir(write_fixture(tmp_path, bugs=[bug]))
    )


# ------------------------------------------------- residual defects (v0.1)


def test_missing_residual_defects_file_is_reported(tmp_path: Path) -> None:
    fixture_dir = write_fixture(tmp_path)
    (fixture_dir / "known_residual_defects.yaml").unlink()
    assert rules(validate_fixture_dir(fixture_dir)) == ["RESIDUAL_FILE_MISSING"]


def test_non_empty_residual_defects_makes_the_fixture_release_ineligible(tmp_path: Path) -> None:
    """v0.1 requires clean fixtures to be genuinely clean (spec §6.8)."""
    residual = {
        "schema_version": "0.1",
        "fixture_id": "fx-demo-py",
        "fixture_version": "1.0.0",
        "defects": [{"residual_id": "R-001"}],
    }
    problems = validate_fixture_dir(write_fixture(tmp_path, residual=residual))
    assert problems, "a non-empty residual list must not validate"
    assert any("release" in p.message for p in problems)


def test_residual_file_for_a_different_fixture_is_reported(tmp_path: Path) -> None:
    residual = {
        "schema_version": "0.1",
        "fixture_id": "fx-other-py",
        "fixture_version": "1.0.0",
        "defects": [],
    }
    problems = validate_fixture_dir(write_fixture(tmp_path, residual=residual))
    assert "RESIDUAL_FIXTURE_MISMATCH" in rules(problems)


# ------------------------------------------------------------------ root + CLI


def test_validate_root_walks_every_fixture(tmp_path: Path) -> None:
    write_fixture(tmp_path)
    second = deepcopy(VALID_FIXTURE)
    second["fixture_id"] = "fx-second-ts"
    second["language"] = "typescript"
    second["bugs"] = []
    write_fixture(tmp_path, fixture=second, bugs=[])
    assert validate_fixture_root(tmp_path) == []


def test_validate_root_reports_the_offending_fixture(tmp_path: Path) -> None:
    bug = deepcopy(VALID_BUG)
    bug["fixture_id"] = "fx-other-py"
    write_fixture(tmp_path, bugs=[bug])
    problems = validate_fixture_root(tmp_path)
    assert problems
    assert all(p.source.startswith("fx-demo-py/") for p in problems)


def test_validate_root_on_an_empty_directory_is_an_error(tmp_path: Path) -> None:
    """Silently passing on zero fixtures would make the gate meaningless."""
    assert rules(validate_fixture_root(tmp_path)) == ["NO_FIXTURES"]


def test_cli_validate_returns_zero_on_a_valid_tree(tmp_path: Path) -> None:
    write_fixture(tmp_path)
    assert main(["validate", str(tmp_path)]) == 0


def test_cli_validate_returns_one_on_problems(tmp_path: Path) -> None:
    bug = deepcopy(VALID_BUG)
    bug["fixture_id"] = "fx-other-py"
    write_fixture(tmp_path, bugs=[bug])
    assert main(["validate", str(tmp_path)]) == 1


def test_cli_validate_returns_two_for_a_missing_path(tmp_path: Path) -> None:
    assert main(["validate", str(tmp_path / "nope")]) == 2


def test_cli_validate_accepts_a_single_fixture_directory(tmp_path: Path) -> None:
    """Validating one fixture is the common case, and the plan's own command.

    Pointed at a fixture rather than at a root of them, the walk finds no
    `*/fixture.yaml` below and reports NO_FIXTURES — correct, and useless. A
    path holding a manifest is one fixture, not a root.
    """
    fixture_dir = write_fixture(tmp_path)
    assert main(["validate", str(fixture_dir)]) == 0


def test_cli_validate_on_one_fixture_still_reports_its_problems(tmp_path: Path) -> None:
    """The single-fixture path must not become a way to pass by accident."""
    bug = deepcopy(VALID_BUG)
    bug["fixture_id"] = "fx-other-py"
    fixture_dir = write_fixture(tmp_path, bugs=[bug])
    assert main(["validate", str(fixture_dir)]) == 1


@pytest.mark.parametrize("subject", ["fixture", "bug"])
def test_problem_renders_source_pointer_and_rule(tmp_path: Path, subject: str) -> None:
    doc = deepcopy(VALID_BUG if subject == "bug" else VALID_FIXTURE)
    doc["schema_version"] = "9.9"
    fixture_dir = write_fixture(
        tmp_path,
        fixture=None if subject == "bug" else doc,
        bugs=[doc] if subject == "bug" else None,
    )
    problems = validate_fixture_dir(fixture_dir)
    assert problems
    rendered = problems[0].render()
    assert problems[0].source in rendered
    assert problems[0].rule in rendered
