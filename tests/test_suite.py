"""Immutable reference-suite registration and complete outcome retention."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from coding_agent_eval.agent.protocol import Budget
from coding_agent_eval.agent.provider import PricingTable
from coding_agent_eval.cli import main
from coding_agent_eval.runconfig import RunConfiguration
from coding_agent_eval.suite import (
    ProviderFailure,
    SuiteError,
    build_registration,
    load_registration,
    run_suite,
    write_registration,
)
from tests.conftest import REPO_ROOT

TASKS = REPO_ROOT / "tasks" / "v0.1.json"


def configuration() -> RunConfiguration:
    return RunConfiguration(
        api_key="runtime-" + "secret",
        base_url="https://api.openai.com/v1",
        model="gpt-5.6-luna",
        reasoning_effort="high",
        api="responses",
        budget=Budget(
            max_tokens=200_000,
            max_tool_calls=60,
            max_wallclock_seconds=900,
            max_estimated_cost_usd=2.5,
        ),
        pricing=PricingTable(
            version="test-pricing",
            effective_date="2026-08-11",
            source="offline test",
            input_per_mtok_usd=0.2,
            output_per_mtok_usd=1.2,
        ),
    )


@pytest.fixture
def current_fixtures(tmp_path: Path) -> Path:
    root = tmp_path / "fixtures"
    shutil.copytree(REPO_ROOT / "fixtures", root)
    identities = {
        "fx-taskq-py": ("a" * 64, "b" * 64),
        "fx-ledger-ts": ("c" * 64, "d" * 64),
    }
    for fixture_id, (manifest_hex, config_hex) in identities.items():
        path = root / fixture_id / "fixture.yaml"
        manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
        environment = manifest["environment"]
        environment.pop("prepared_image_digest", None)
        environment.update(
            {
                "prepared_image_repository": f"ghcr.io/kuotunyu/coding-agent-eval-{fixture_id}",
                "prepared_image_tag": manifest["fixture_version"],
                "prepared_image_manifest_digest": f"sha256:{manifest_hex}",
                "prepared_image_config_digest": f"sha256:{config_hex}",
            }
        )
        path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    return root


def test_registration_is_complete_deterministic_and_secret_free(
    current_fixtures: Path,
) -> None:
    first = build_registration(
        task_registry_path=TASKS,
        fixture_root=current_fixtures,
        configuration=configuration(),
        provider="openai",
        created_date="2026-08-11",
    )
    second = build_registration(
        task_registry_path=TASKS,
        fixture_root=current_fixtures,
        configuration=configuration(),
        provider="openai",
        created_date="2026-08-12",
    )

    document = first.as_dict()
    rendered = json.dumps(document, sort_keys=True)
    registry_order = tuple(
        task["task_id"] for task in json.loads(TASKS.read_text(encoding="utf-8"))["tasks"]
    )
    assert len(first.ordered_task_ids) == 10
    assert first.ordered_task_ids == registry_order
    assert len(set(first.ordered_task_ids)) == 10
    assert first.suite_id == second.suite_id
    assert first.retry_policy == "no_automatic_retry"
    assert first.budgets["suite_total"]["max_estimated_cost_usd"] == 25.0
    assert set(first.image_identities) == {"fx-taskq-py", "fx-ledger-ts"}
    assert all("tag" not in identity for identity in document["image_identities"].values())
    assert "api_key" not in rendered
    assert configuration().api_key not in rendered
    assert str(current_fixtures) not in rendered


def test_registration_rejects_duplicate_tasks_and_fixture_version_drift(
    current_fixtures: Path, tmp_path: Path
) -> None:
    registry = json.loads(TASKS.read_text(encoding="utf-8"))
    registry["tasks"].append(dict(registry["tasks"][0]))
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text(json.dumps(registry), encoding="utf-8")
    with pytest.raises(SuiteError, match="duplicate"):
        build_registration(
            task_registry_path=duplicate,
            fixture_root=current_fixtures,
            configuration=configuration(),
            provider="openai",
            created_date="2026-08-11",
        )

    manifest_path = current_fixtures / "fx-taskq-py" / "fixture.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["fixture_version"] = "9.9.9"
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(SuiteError, match="fixture_version"):
        build_registration(
            task_registry_path=TASKS,
            fixture_root=current_fixtures,
            configuration=configuration(),
            provider="openai",
            created_date="2026-08-11",
        )


def test_registration_refuses_incomplete_budgets(current_fixtures: Path) -> None:
    config = configuration()
    incomplete = RunConfiguration(
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
        reasoning_effort=config.reasoning_effort,
        api=config.api,
        budget=Budget(max_tokens=200_000),
        pricing=config.pricing,
    )
    with pytest.raises(SuiteError, match="all four budget"):
        build_registration(
            task_registry_path=TASKS,
            fixture_root=current_fixtures,
            configuration=incomplete,
            provider="openai",
            created_date="2026-08-11",
        )


def test_load_detects_image_drift_and_write_refuses_overwrite(
    current_fixtures: Path, tmp_path: Path
) -> None:
    registration = build_registration(
        task_registry_path=TASKS,
        fixture_root=current_fixtures,
        configuration=configuration(),
        provider="openai",
        created_date="2026-08-11",
    )
    path = tmp_path / "registration.json"
    write_registration(registration, path)
    with pytest.raises(SuiteError, match="already exists"):
        write_registration(registration, path)

    manifest_path = current_fixtures / "fx-ledger-ts" / "fixture.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["environment"]["prepared_image_manifest_digest"] = "sha256:" + "e" * 64
    manifest_path.write_text(yaml.safe_dump(manifest), encoding="utf-8")
    with pytest.raises(SuiteError, match="image identity"):
        load_registration(path, task_registry_path=TASKS, fixture_root=current_fixtures)


def test_fake_provider_retains_all_ten_outcomes_and_refuses_resume(
    current_fixtures: Path, tmp_path: Path
) -> None:
    registration = build_registration(
        task_registry_path=TASKS,
        fixture_root=current_fixtures,
        configuration=configuration(),
        provider="openai",
        created_date="2026-08-11",
    )
    registration_path = tmp_path / "registration.json"
    write_registration(registration, registration_path)
    timeout_task = registration.ordered_task_ids[2]
    provider_error_task = registration.ordered_task_ids[7]
    attempts: list[str] = []

    def fake_provider(task: dict[str, object], artifact_dir: Path) -> str:
        attempts.append(str(task["task_id"]))
        (artifact_dir / "fake-trace.json").write_text("{}\n", encoding="utf-8")
        if task["task_id"] == timeout_task:
            raise TimeoutError("scripted timeout")
        if task["task_id"] == provider_error_task:
            raise ProviderFailure("scripted provider error")
        return "completed"

    out = tmp_path / "suite"
    summary = run_suite(
        registration_path,
        task_registry_path=TASKS,
        fixture_root=current_fixtures,
        out=out,
        executor=fake_provider,
    )

    statuses = [
        json.loads((out / "tasks" / task_id / "status.json").read_text(encoding="utf-8"))
        for task_id in registration.ordered_task_ids
    ]
    assert [status["task_id"] for status in statuses] == list(registration.ordered_task_ids)
    assert len(statuses) == 10
    assert attempts == list(registration.ordered_task_ids)
    assert summary["counts"] == {"completed": 8, "provider_error": 1, "timeout": 1}
    with pytest.raises(SuiteError, match="already has an artifact directory"):
        run_suite(
            registration_path,
            task_registry_path=TASKS,
            fixture_root=current_fixtures,
            out=out,
            executor=fake_provider,
        )


def test_cli_dry_run_and_register_make_no_provider_call(
    current_fixtures: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_key = "runtime-" + "credential-value"
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                f"CAE_PROVIDER_API_KEY={private_key}",
                "CAE_PROVIDER_MODEL=gpt-5.6-luna",
                "CAE_PROVIDER_API=responses",
                "CAE_PROVIDER_REASONING_EFFORT=high",
                "CAE_MAX_TOKENS=200000",
                "CAE_MAX_TOOL_CALLS=60",
                "CAE_MAX_WALLCLOCK_SECONDS=900",
                "CAE_MAX_ESTIMATED_COST_USD=2.5",
            ]
        ),
        encoding="utf-8",
    )

    def provider_must_not_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("dry-run called a provider")

    import coding_agent_eval.live as live

    monkeypatch.setattr(live, "execute", provider_must_not_run)
    plan = tmp_path / "plan.json"
    assert (
        main(
            [
                "suite",
                "dry-run",
                "--tasks",
                str(TASKS),
                "--fixtures",
                str(current_fixtures),
                "--env-file",
                str(env_file),
                "--out",
                str(plan),
            ]
        )
        == 0
    )
    output = capsys.readouterr()
    assert "planned 10 task(s)" in output.out
    assert "$25.00" in output.out
    assert private_key not in output.out + output.err + plan.read_text(encoding="utf-8")

    registration = tmp_path / "registration.json"
    assert (
        main(
            [
                "suite",
                "register",
                "--tasks",
                str(TASKS),
                "--fixtures",
                str(current_fixtures),
                "--plan",
                str(plan),
                "--out",
                str(registration),
            ]
        )
        == 0
    )
    assert registration.read_bytes() == plan.read_bytes()
