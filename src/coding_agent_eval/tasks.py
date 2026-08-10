"""Resolve the public task registry against the fixture artifacts it names."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from jsonschema import Draft202012Validator

from coding_agent_eval.e2e import load_fixture
from coding_agent_eval.schemas.loader import load_schema


@dataclass(frozen=True)
class TaskProblem:
    """One actionable registry validation failure."""

    task_id: str
    message: str


def _safe_artifact(fixture_dir: Path, relative: str) -> bool:
    path = PurePosixPath(relative)
    return not path.is_absolute() and ".." not in path.parts and (fixture_dir / path).is_file()


def validate_task_registry(path: Path, fixture_root: Path) -> list[TaskProblem]:
    """Validate schema, uniqueness, coverage, and every fixture artifact reference."""
    try:
        document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [TaskProblem("<registry>", f"cannot read task registry: {exc}")]

    validator = Draft202012Validator(load_schema("task"))
    schema_errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if schema_errors:
        return [
            TaskProblem(
                "<registry>",
                f"schema at /{'/'.join(str(part) for part in error.path)}: {error.message}",
            )
            for error in schema_errors
        ]

    tasks: list[dict[str, Any]] = document["tasks"]
    problems: list[TaskProblem] = []
    counts = Counter(str(task["task_id"]) for task in tasks)
    for task_id, count in sorted(counts.items()):
        if count > 1:
            problems.append(TaskProblem(task_id, f"duplicate task_id appears {count} times"))

    registered_fixtures = {str(task["fixture_id"]) for task in tasks}
    fixture_directories = {manifest.parent.name for manifest in fixture_root.glob("*/fixture.yaml")}
    for fixture_id in sorted(fixture_directories - registered_fixtures):
        problems.append(
            TaskProblem(
                f"{fixture_id}/*",
                "fixture directory is not represented in the task registry",
            )
        )

    expected: set[str] = set()
    observed: set[str] = set()
    for task in tasks:
        task_id = str(task["task_id"])
        fixture_id = str(task["fixture_id"])
        fixture_dir = fixture_root / fixture_id
        try:
            fixture = load_fixture(fixture_dir)
        except (OSError, KeyError, TypeError) as exc:
            problems.append(TaskProblem(task_id, f"cannot load fixture {fixture_id}: {exc}"))
            continue

        expected.add(f"{fixture_id}/clean")
        expected.update(str(bug["bug_id"]) for bug in fixture.bugs)
        observed.add(task_id)

        if task["fixture_version"] != fixture.version:
            problems.append(
                TaskProblem(
                    task_id,
                    f"fixture_version {task['fixture_version']} does not match {fixture.version}",
                )
            )
        clean = fixture.manifest["clean_control"]
        if task["tree_checksum"] != clean["tree_checksum"]:
            problems.append(TaskProblem(task_id, "tree_checksum does not match fixture manifest"))

        witness = str(task["witness"])
        if not _safe_artifact(fixture_dir, witness):
            problems.append(TaskProblem(task_id, f"witness {witness!r} does not exist"))

        if task["snapshot"] == "clean":
            if task_id != f"{fixture_id}/clean":
                problems.append(TaskProblem(task_id, "clean task_id does not match fixture_id"))
            if witness != clean["witness_suite"]:
                problems.append(
                    TaskProblem(task_id, "clean witness does not match fixture manifest")
                )
            continue

        bug_id = str(task["bug_id"])
        bug = next((candidate for candidate in fixture.bugs if candidate["bug_id"] == bug_id), None)
        if bug is None:
            problems.append(TaskProblem(task_id, f"bug_id {bug_id!r} is not in fixture manifest"))
            continue
        if task_id != bug_id:
            problems.append(TaskProblem(task_id, "mutated task_id must equal bug_id"))
        patch = str(task["patch"])
        if patch != bug["patch"]:
            problems.append(TaskProblem(task_id, "patch does not match bug manifest"))
        if not _safe_artifact(fixture_dir, patch):
            problems.append(TaskProblem(task_id, f"patch {patch!r} does not exist"))
        expected_witness = f"bugs/{bug_id.rsplit('/', 1)[-1]}.yaml"
        if witness != expected_witness:
            problems.append(TaskProblem(task_id, "witness does not name the bug manifest"))

    for missing in sorted(expected - observed):
        problems.append(TaskProblem(missing, "fixture task is missing from registry"))
    for extra in sorted(observed - expected):
        problems.append(TaskProblem(extra, "registry task is not declared by a fixture"))
    return problems
