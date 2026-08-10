"""CI must audit owner history and pull the pinned public fixture images reliably."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_pull_request_jobs_use_owner_head_and_authenticated_registry(repo_root: Path) -> None:
    workflow = yaml.safe_load((repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8"))

    assert workflow["permissions"] == {"contents": "read", "packages": "read"}
    expected_ref = "${{ github.event.pull_request.head.sha || github.sha }}"
    for job_name in ("quality", "docker-gates"):
        steps = workflow["jobs"][job_name]["steps"]
        checkout = next(
            step for step in steps if step.get("uses", "").startswith("actions/checkout@")
        )
        assert checkout["with"]["ref"] == expected_ref
        assert checkout["with"]["fetch-depth"] == 0

    docker_steps = workflow["jobs"]["docker-gates"]["steps"]
    login = next(
        step for step in docker_steps if step.get("uses", "").startswith("docker/login-action@")
    )
    assert login["with"] == {
        "registry": "ghcr.io",
        "username": "${{ github.actor }}",
        "password": "${{ secrets.GITHUB_TOKEN }}",
    }
