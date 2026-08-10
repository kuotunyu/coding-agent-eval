"""Whole-fixture validation: the checks that span more than one file.

Schema validation looks at one document at a time. The failures that actually
occur when a fixture is edited span files: the fixture version is bumped and a
bug is left behind, a bug manifest is added but never listed, a listed bug has
no manifest. Each of those would leave the fixture scoreable but wrong, and none
is visible to a single-document validator.

v0.1 also enforces the empty-residual-defects rule here (spec §6.8), because
release eligibility is a property of the fixture as a whole.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from coding_agent_eval.schemas.validate import ValidationProblem, validate_document

FIXTURE_MANIFEST = "fixture.yaml"
BUGS_DIR = "bugs"


@dataclass(frozen=True)
class FixtureProblem:
    """A problem located by fixture-relative source, JSON pointer, and rule."""

    source: str
    pointer: str
    message: str
    rule: str

    def render(self) -> str:
        where = f"{self.source}{self.pointer}" if self.pointer else self.source
        return f"{where}: {self.message} [{self.rule}]"


def _problem(source: str, message: str, rule: str, pointer: str = "") -> FixtureProblem:
    return FixtureProblem(source=source, pointer=pointer, message=message, rule=rule)


def _from_document(source: str, problems: list[ValidationProblem]) -> list[FixtureProblem]:
    return [
        FixtureProblem(source=source, pointer=p.pointer, message=p.message, rule=p.rule)
        for p in problems
    ]


class _Unreadable(Exception):
    """A manifest could not be read or parsed. Carries the problem to report."""

    def __init__(self, problem: FixtureProblem) -> None:
        self.problem = problem
        super().__init__(problem.message)


def _load_yaml(path: Path, source: str) -> dict[str, Any]:
    """Load a manifest, or raise `_Unreadable` carrying a reportable problem.

    Raising rather than returning an optional keeps every caller's happy path
    free of None-narrowing, which is where the indexing errors came from.
    """
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise _Unreadable(
            _problem(source, f"could not be read as YAML: {exc}", "UNREADABLE")
        ) from exc
    if not isinstance(document, dict):
        raise _Unreadable(
            _problem(
                source, f"expected a YAML mapping, got {type(document).__name__}", "NOT_A_MAPPING"
            )
        )
    return document


def validate_fixture_dir(fixture_dir: Path) -> list[FixtureProblem]:
    """Validate one fixture directory. Returns every problem found."""
    problems: list[FixtureProblem] = []
    manifest_path = fixture_dir / FIXTURE_MANIFEST
    if not manifest_path.is_file():
        return [_problem(FIXTURE_MANIFEST, "fixture manifest is missing", "MISSING_MANIFEST")]

    try:
        manifest = _load_yaml(manifest_path, FIXTURE_MANIFEST)
    except _Unreadable as exc:
        return [exc.problem]

    schema_problems = validate_document("fixture", manifest)
    problems += _from_document(FIXTURE_MANIFEST, schema_problems)
    if schema_problems:
        # Cross-file checks read fields that the schema has just rejected.
        return problems

    problems += _check_bugs(fixture_dir, manifest)
    problems += _check_residual_defects(fixture_dir, manifest)
    return problems


def _check_bugs(fixture_dir: Path, manifest: dict[str, Any]) -> list[FixtureProblem]:
    problems: list[FixtureProblem] = []
    fixture_id = manifest["fixture_id"]
    fixture_version = manifest["fixture_version"]
    listed: list[str] = list(manifest["bugs"])

    bugs_dir = fixture_dir / BUGS_DIR
    on_disk = sorted(bugs_dir.glob("*.yaml")) if bugs_dir.is_dir() else []

    seen: set[str] = set()
    for path in on_disk:
        source = f"{BUGS_DIR}/{path.name}"
        try:
            bug = _load_yaml(path, source)
        except _Unreadable as exc:
            problems.append(exc.problem)
            continue

        document_problems = validate_document("bug", bug)
        problems += _from_document(source, document_problems)
        if document_problems:
            continue

        bug_id = bug["bug_id"]
        seen.add(bug_id)

        if bug_id.split("/")[-1] != path.stem:
            problems.append(
                _problem(
                    source,
                    f"bug_id {bug_id!r} does not match its filename {path.stem!r}",
                    "BUG_FILENAME_MISMATCH",
                    "/bug_id",
                )
            )
        if bug["fixture_id"] != fixture_id:
            problems.append(
                _problem(
                    source,
                    f"declares fixture_id {bug['fixture_id']!r}, but lives under {fixture_id!r}",
                    "BUG_FIXTURE_MISMATCH",
                    "/fixture_id",
                )
            )
        if bug["fixture_version"] != fixture_version:
            problems.append(
                _problem(
                    source,
                    (
                        f"declares fixture_version {bug['fixture_version']!r}, but the fixture is "
                        f"{fixture_version!r}; a stale bug would be scored against a tree it was "
                        "not authored for"
                    ),
                    "BUG_VERSION_MISMATCH",
                    "/fixture_version",
                )
            )
        if bug_id not in listed:
            problems.append(
                _problem(
                    source,
                    f"{bug_id} has a manifest but is not listed in fixture.yaml, so it "
                    "would never be scored",
                    "BUG_NOT_LISTED",
                    "/bug_id",
                )
            )

    for bug_id in listed:
        if bug_id not in seen:
            problems.append(
                _problem(
                    FIXTURE_MANIFEST,
                    f"{bug_id} is listed but has no manifest under {BUGS_DIR}/",
                    "BUG_MANIFEST_MISSING",
                    "/bugs",
                )
            )
    return problems


def _check_residual_defects(fixture_dir: Path, manifest: dict[str, Any]) -> list[FixtureProblem]:
    relative = manifest["known_residual_defects"]
    path = fixture_dir / relative
    if not path.is_file():
        return [
            _problem(
                relative,
                "known residual defects file is missing; v0.1 requires it to exist and be empty",
                "RESIDUAL_FILE_MISSING",
            )
        ]

    try:
        document = _load_yaml(path, relative)
    except _Unreadable as exc:
        return [exc.problem]

    problems = _from_document(relative, validate_document("known-residual-defects", document))
    if problems:
        # The schema caps defects at zero items, so a non-empty list lands here.
        # Say plainly what the consequence is rather than only that a rule failed.
        problems.append(
            _problem(
                relative,
                "v0.1 requires an empty residual defect list; this fixture is not "
                "release eligible until the defect is fixed and the fixture version bumped",
                "RESIDUAL_NOT_EMPTY",
                "/defects",
            )
        )
        return problems

    if document["fixture_id"] != manifest["fixture_id"]:
        problems.append(
            _problem(
                relative,
                f"declares fixture_id {document['fixture_id']!r}, but lives under "
                f"{manifest['fixture_id']!r}",
                "RESIDUAL_FIXTURE_MISMATCH",
                "/fixture_id",
            )
        )
    if document["fixture_version"] != manifest["fixture_version"]:
        problems.append(
            _problem(
                relative,
                f"declares fixture_version {document['fixture_version']!r}, but the fixture is "
                f"{manifest['fixture_version']!r}",
                "RESIDUAL_VERSION_MISMATCH",
                "/fixture_version",
            )
        )
    return problems


def fixture_dirs(root: Path) -> list[Path]:
    return sorted(p.parent for p in root.glob(f"*/{FIXTURE_MANIFEST}"))


def validate_fixture_root(root: Path) -> list[FixtureProblem]:
    """Validate every fixture under `root`.

    An empty root is an error rather than a pass: a gate that goes green when
    there is nothing to check reports the absence of fixtures as success.
    """
    directories = fixture_dirs(root)
    if not directories:
        return [
            _problem(
                str(root),
                f"no fixture directories found under {root}; "
                "an empty run would report success without checking anything",
                "NO_FIXTURES",
            )
        ]

    problems: list[FixtureProblem] = []
    for directory in directories:
        for problem in validate_fixture_dir(directory):
            problems.append(
                FixtureProblem(
                    source=f"{directory.name}/{problem.source}",
                    pointer=problem.pointer,
                    message=problem.message,
                    rule=problem.rule,
                )
            )
    return problems
