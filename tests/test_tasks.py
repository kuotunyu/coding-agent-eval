"""The machine-readable v0.1 task registry resolves to frozen fixture artifacts."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy
from pathlib import Path
from typing import Any

from coding_agent_eval.tasks import validate_task_registry


def registry(repo_root: Path) -> dict[str, Any]:
    return json.loads((repo_root / "tasks" / "v0.1.json").read_text(encoding="utf-8"))


def test_v0_1_registry_resolves_two_clean_and_eight_mutated_tasks(repo_root: Path) -> None:
    document = registry(repo_root)
    tasks = document["tasks"]

    assert len(tasks) == 10
    assert len({task["task_id"] for task in tasks}) == 10
    assert sum(task["snapshot"] == "clean" for task in tasks) == 2
    assert sum(task["snapshot"] == "mutated" for task in tasks) == 8
    assert validate_task_registry(repo_root / "tasks" / "v0.1.json", repo_root / "fixtures") == []


def test_a_stale_fixture_version_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    document = deepcopy(registry(repo_root))
    document["tasks"][0]["fixture_version"] = "0.0.0"
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    problems = validate_task_registry(path, repo_root / "fixtures")

    assert any("fixture_version" in problem.message for problem in problems)


def test_a_duplicate_task_id_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    document = deepcopy(registry(repo_root))
    document["tasks"][1]["task_id"] = document["tasks"][0]["task_id"]
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    problems = validate_task_registry(path, repo_root / "fixtures")

    assert any("duplicate task_id" in problem.message for problem in problems)


def test_a_missing_patch_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    document = deepcopy(registry(repo_root))
    mutated = next(task for task in document["tasks"] if task["snapshot"] == "mutated")
    mutated["patch"] = "bugs/missing.patch"
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    problems = validate_task_registry(path, repo_root / "fixtures")

    assert any(
        "patch" in problem.message and "does not exist" in problem.message for problem in problems
    )


def test_a_wrong_tree_checksum_is_rejected(repo_root: Path, tmp_path: Path) -> None:
    document = deepcopy(registry(repo_root))
    document["tasks"][0]["tree_checksum"] = "sha256:" + "0" * 64
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    problems = validate_task_registry(path, repo_root / "fixtures")

    assert any("tree_checksum" in problem.message for problem in problems)


def test_an_entirely_unregistered_fixture_directory_is_rejected(
    repo_root: Path, tmp_path: Path
) -> None:
    fixtures = tmp_path / "fixtures"
    for source in (repo_root / "fixtures").iterdir():
        if source.is_dir():
            shutil.copytree(
                source,
                fixtures / source.name,
                ignore=shutil.ignore_patterns("node_modules", "dist", "__pycache__"),
            )
    extra = fixtures / "fx-unregistered"
    extra.mkdir()
    (extra / "fixture.yaml").write_text("fixture_id: fx-unregistered\n", encoding="utf-8")

    problems = validate_task_registry(repo_root / "tasks" / "v0.1.json", fixtures)

    assert any(
        problem.task_id == "fx-unregistered/*" and "not represented" in problem.message
        for problem in problems
    )
