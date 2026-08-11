"""Immutable registration and complete outcome retention for the reference suite."""

from __future__ import annotations

import hashlib
import json
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal, cast

import yaml

from coding_agent_eval.fixtures.image_identity import (
    ImageIdentityError,
    PreparedImageIdentity,
)
from coding_agent_eval.runconfig import DEFAULT_BASE_URL, RunConfiguration
from coding_agent_eval.schemas.validate import validate_document
from coding_agent_eval.tasks import validate_task_registry

RETRY_POLICY = "no_automatic_retry"
REGISTRATION_SCHEMA_VERSION = "1.1.0"
CONVERSATION_STATE = "manual_history"
EXPECTED_TASK_COUNT = 10
TaskStatus = Literal[
    "completed",
    "provider_error",
    "timeout",
    "budget_exhausted",
    "harness_error",
    "fixture_defect",
]
VALID_STATUSES: frozenset[str] = frozenset(
    {
        "completed",
        "provider_error",
        "timeout",
        "budget_exhausted",
        "harness_error",
        "fixture_defect",
    }
)


class SuiteError(RuntimeError):
    """A suite plan, registration, or outcome would not be reproducible."""


class ProviderFailure(RuntimeError):
    """The provider failed before producing a complete task outcome."""


class BudgetFailure(RuntimeError):
    """A registered task exhausted one of its fixed budgets."""


class HarnessFailure(RuntimeError):
    """The benchmark harness failed independently of model capability."""


class FixtureFailure(RuntimeError):
    """A clean control or fixture contract proved defective."""


BudgetValue = int | float
BudgetRecord = Mapping[str, BudgetValue]


@dataclass(frozen=True)
class SuiteRegistration:
    schema_version: str
    suite_id: str
    task_registry_sha256: str
    ordered_task_ids: tuple[str, ...]
    provider: str
    model: str
    api: str
    reasoning_effort: str | None
    agent_adapter: str | None
    agent_adapter_version: str | None
    system_prompt_version: str | None
    system_prompt_sha256: str | None
    conversation_state: str | None
    store: bool | None
    max_output_tokens_per_request: int | None
    budgets: Mapping[str, BudgetRecord]
    retry_policy: str
    image_identities: Mapping[str, PreparedImageIdentity]
    environment_fingerprints: Mapping[str, str]
    created_date: str

    def as_dict(self) -> dict[str, Any]:
        document: dict[str, Any] = {
            "schema_version": self.schema_version,
            "suite_id": self.suite_id,
            "task_registry_sha256": self.task_registry_sha256,
            "ordered_task_ids": list(self.ordered_task_ids),
            "provider": self.provider,
            "model": self.model,
            "api": self.api,
            "reasoning_effort": self.reasoning_effort,
            "budgets": {name: dict(values) for name, values in sorted(self.budgets.items())},
            "retry_policy": self.retry_policy,
            "image_identities": {
                fixture_id: {
                    "repository": identity.repository,
                    "manifest_digest": identity.manifest_digest,
                    "config_digest": identity.config_digest,
                }
                for fixture_id, identity in sorted(self.image_identities.items())
            },
            "environment_fingerprints": dict(sorted(self.environment_fingerprints.items())),
            "created_date": self.created_date,
        }
        if self.schema_version != "1.0.0":
            document.update(
                {
                    "agent_adapter": self.agent_adapter,
                    "agent_adapter_version": self.agent_adapter_version,
                    "system_prompt_version": self.system_prompt_version,
                    "system_prompt_sha256": self.system_prompt_sha256,
                    "conversation_state": self.conversation_state,
                    "store": self.store,
                    "max_output_tokens_per_request": self.max_output_tokens_per_request,
                }
            )
        return document


def _canonical_bytes(payload: Any) -> bytes:
    return json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def _sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _suite_id(document: Mapping[str, Any]) -> str:
    identity = {
        key: value for key, value in document.items() if key not in {"suite_id", "created_date"}
    }
    return f"suite-{hashlib.sha256(_canonical_bytes(identity)).hexdigest()}"


def _require_iso_date(value: str) -> None:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise SuiteError(f"created_date must be ISO 8601: {exc}") from exc
    if parsed.isoformat() != value:
        raise SuiteError("created_date must use canonical YYYY-MM-DD form")


def _read_registry(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot read task registry: {exc}") from exc
    if not isinstance(loaded, dict) or not isinstance(loaded.get("tasks"), list):
        raise SuiteError("task registry must be an object with a tasks array")
    return loaded


def _validate_registry(path: Path, fixture_root: Path) -> dict[str, Any]:
    problems = validate_task_registry(path, fixture_root)
    if problems:
        rendered = "; ".join(f"{problem.task_id}: {problem.message}" for problem in problems)
        raise SuiteError(f"task registry is invalid: {rendered}")
    document = _read_registry(path)
    tasks = document["tasks"]
    if len(tasks) != EXPECTED_TASK_COUNT:
        raise SuiteError(
            f"reference suite must contain exactly {EXPECTED_TASK_COUNT} tasks, found {len(tasks)}"
        )
    task_ids = [str(task["task_id"]) for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise SuiteError("task registry contains duplicate task IDs")
    return document


def _fixture_contracts(
    fixture_ids: set[str], fixture_root: Path
) -> tuple[dict[str, PreparedImageIdentity], dict[str, str]]:
    identities: dict[str, PreparedImageIdentity] = {}
    fingerprints: dict[str, str] = {}
    for fixture_id in sorted(fixture_ids):
        path = fixture_root / fixture_id / "fixture.yaml"
        try:
            manifest = yaml.safe_load(path.read_text(encoding="utf-8"))
            environment = manifest["environment"]
            identity = PreparedImageIdentity.from_environment(environment)
            fingerprint = environment["fingerprint"]
        except (OSError, KeyError, TypeError, ImageIdentityError, yaml.YAMLError) as exc:
            raise SuiteError(f"fixture {fixture_id} lacks current image identity: {exc}") from exc
        if not isinstance(fingerprint, str):
            raise SuiteError(f"fixture {fixture_id} environment fingerprint must be a string")
        identities[fixture_id] = identity
        fingerprints[fixture_id] = fingerprint
    return identities, fingerprints


def _budgets(configuration: RunConfiguration, task_count: int) -> dict[str, dict[str, Any]]:
    per_task = configuration.budget.as_dict()
    if any(value is None for value in per_task.values()):
        raise SuiteError("reference registration requires all four budget maxima")
    typed: dict[str, BudgetValue] = {
        "max_tokens": int(per_task["max_tokens"]),
        "max_tool_calls": int(per_task["max_tool_calls"]),
        "max_wallclock_seconds": float(per_task["max_wallclock_seconds"]),
        "max_estimated_cost_usd": float(per_task["max_estimated_cost_usd"]),
    }
    total: dict[str, BudgetValue] = {name: value * task_count for name, value in typed.items()}
    return {"per_task": typed, "suite_total": total}


def build_registration(
    *,
    task_registry_path: Path,
    fixture_root: Path,
    configuration: RunConfiguration,
    provider: str,
    created_date: str,
) -> SuiteRegistration:
    """Build a secret-free registration from validated local contracts."""
    if provider != "openai" or configuration.base_url != DEFAULT_BASE_URL:
        raise SuiteError("reference registration requires the official OpenAI provider endpoint")
    _require_iso_date(created_date)
    registry = _validate_registry(task_registry_path, fixture_root)
    ordered = tuple(str(task["task_id"]) for task in registry["tasks"])
    fixture_ids = {str(task["fixture_id"]) for task in registry["tasks"]}
    identities, fingerprints = _fixture_contracts(fixture_ids, fixture_root)
    budgets = _budgets(configuration, len(ordered))
    from coding_agent_eval.agent.provider import SYSTEM_PROMPT_VERSION
    from coding_agent_eval.live import build_adapter

    adapter = build_adapter(configuration, client=None)
    system_prompt = getattr(adapter, "system_prompt", None)
    if not isinstance(system_prompt, str):
        raise SuiteError("reference adapter lacks a rendered system prompt")
    system_prompt_sha256 = _sha256(system_prompt.encode("utf-8"))
    provisional: dict[str, Any] = {
        "schema_version": REGISTRATION_SCHEMA_VERSION,
        "task_registry_sha256": _sha256(task_registry_path.read_bytes()),
        "ordered_task_ids": list(ordered),
        "provider": provider,
        "model": configuration.model,
        "api": configuration.api,
        "reasoning_effort": configuration.reasoning_effort,
        "agent_adapter": adapter.name,
        "agent_adapter_version": adapter.version,
        "system_prompt_version": SYSTEM_PROMPT_VERSION,
        "system_prompt_sha256": system_prompt_sha256,
        "conversation_state": CONVERSATION_STATE,
        "store": False if configuration.api == "responses" else None,
        "max_output_tokens_per_request": configuration.max_output_tokens_per_request,
        "budgets": budgets,
        "retry_policy": RETRY_POLICY,
        "image_identities": {
            fixture_id: {
                "repository": identity.repository,
                "manifest_digest": identity.manifest_digest,
                "config_digest": identity.config_digest,
            }
            for fixture_id, identity in sorted(identities.items())
        },
        "environment_fingerprints": dict(sorted(fingerprints.items())),
        "created_date": created_date,
    }
    suite_id = _suite_id(provisional)
    registration = SuiteRegistration(
        schema_version=REGISTRATION_SCHEMA_VERSION,
        suite_id=suite_id,
        task_registry_sha256=provisional["task_registry_sha256"],
        ordered_task_ids=ordered,
        provider=provider,
        model=configuration.model,
        api=configuration.api,
        reasoning_effort=configuration.reasoning_effort,
        agent_adapter=adapter.name,
        agent_adapter_version=adapter.version,
        system_prompt_version=SYSTEM_PROMPT_VERSION,
        system_prompt_sha256=system_prompt_sha256,
        conversation_state=CONVERSATION_STATE,
        store=False if configuration.api == "responses" else None,
        max_output_tokens_per_request=configuration.max_output_tokens_per_request,
        budgets=budgets,
        retry_policy=RETRY_POLICY,
        image_identities=identities,
        environment_fingerprints=fingerprints,
        created_date=created_date,
    )
    problems = validate_document("suite-registration", registration.as_dict())
    if problems:
        raise SuiteError(
            "registration is invalid: " + "; ".join(problem.render() for problem in problems)
        )
    return registration


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
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
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False)
            handle.write("\n")
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_registration(registration: SuiteRegistration, path: Path) -> Path:
    if path.exists():
        raise SuiteError(f"registration already exists: {path}")
    _atomic_json(path, registration.as_dict())
    return path


def _registration_from_document(
    document: dict[str, Any],
    fixture_root: Path | None = None,
    *,
    identities: Mapping[str, PreparedImageIdentity] | None = None,
) -> SuiteRegistration:
    if identities is None:
        if fixture_root is None:
            raise SuiteError("fixture_root is required for a current registration")
        fixture_ids = set(document["image_identities"])
        identities, _ = _fixture_contracts(fixture_ids, fixture_root)
    return SuiteRegistration(
        schema_version=str(document["schema_version"]),
        suite_id=str(document["suite_id"]),
        task_registry_sha256=str(document["task_registry_sha256"]),
        ordered_task_ids=tuple(str(task_id) for task_id in document["ordered_task_ids"]),
        provider=str(document["provider"]),
        model=str(document["model"]),
        api=str(document["api"]),
        reasoning_effort=(
            None if document["reasoning_effort"] is None else str(document["reasoning_effort"])
        ),
        agent_adapter=(
            None if document.get("agent_adapter") is None else str(document["agent_adapter"])
        ),
        agent_adapter_version=(
            None
            if document.get("agent_adapter_version") is None
            else str(document["agent_adapter_version"])
        ),
        system_prompt_version=(
            None
            if document.get("system_prompt_version") is None
            else str(document["system_prompt_version"])
        ),
        system_prompt_sha256=(
            None
            if document.get("system_prompt_sha256") is None
            else str(document["system_prompt_sha256"])
        ),
        conversation_state=(
            None
            if document.get("conversation_state") is None
            else str(document["conversation_state"])
        ),
        store=(None if "store" not in document else bool(document["store"])),
        max_output_tokens_per_request=(
            None
            if document.get("max_output_tokens_per_request") is None
            else int(document["max_output_tokens_per_request"])
        ),
        budgets={
            name: {field: value for field, value in values.items()}
            for name, values in document["budgets"].items()
        },
        retry_policy=str(document["retry_policy"]),
        image_identities=identities,
        environment_fingerprints={
            str(key): str(value) for key, value in document["environment_fingerprints"].items()
        },
        created_date=str(document["created_date"]),
    )


def load_registration(
    path: Path, *, task_registry_path: Path, fixture_root: Path
) -> SuiteRegistration:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot read registration: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SuiteError("registration must be a JSON object")
    document: dict[str, Any] = loaded
    problems = validate_document("suite-registration", document)
    if problems:
        raise SuiteError(
            "registration schema is invalid: " + "; ".join(problem.render() for problem in problems)
        )
    _require_iso_date(str(document["created_date"]))
    if document["suite_id"] != _suite_id(document):
        raise SuiteError("suite_id does not match canonical registration content")
    registry = _validate_registry(task_registry_path, fixture_root)
    if document["task_registry_sha256"] != _sha256(task_registry_path.read_bytes()):
        raise SuiteError("task registry hash drifted after registration")
    expected_ids = [str(task["task_id"]) for task in registry["tasks"]]
    if document["ordered_task_ids"] != expected_ids:
        raise SuiteError("registered task order or coverage differs from the task registry")
    per_task = document["budgets"]["per_task"]
    suite_total = document["budgets"]["suite_total"]
    if any(suite_total[field] != per_task[field] * EXPECTED_TASK_COUNT for field in per_task):
        raise SuiteError("suite aggregate budgets do not equal ten per-task budgets")

    identities, fingerprints = _fixture_contracts(
        {str(task["fixture_id"]) for task in registry["tasks"]}, fixture_root
    )
    observed_identities = {
        fixture_id: {
            "repository": identity.repository,
            "manifest_digest": identity.manifest_digest,
            "config_digest": identity.config_digest,
        }
        for fixture_id, identity in sorted(identities.items())
    }
    if document["image_identities"] != observed_identities:
        raise SuiteError("fixture image identity drifted after registration")
    if document["environment_fingerprints"] != dict(sorted(fingerprints.items())):
        raise SuiteError("fixture environment fingerprint drifted after registration")
    return _registration_from_document(document, fixture_root)


def load_registration_snapshot(path: Path, *, task_registry_path: Path) -> SuiteRegistration:
    """Load immutable suite evidence without resolving it against current fixtures.

    This is intentionally separate from :func:`load_registration`: archived
    evidence is validated against the exact registry bytes bound by its
    registration, while executable registrations must still match the current
    fixture tree.
    """
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SuiteError(f"cannot read registration: {exc}") from exc
    if not isinstance(loaded, dict):
        raise SuiteError("registration must be a JSON object")
    document: dict[str, Any] = loaded
    problems = validate_document("suite-registration", document)
    if problems:
        raise SuiteError(
            "registration schema is invalid: " + "; ".join(problem.render() for problem in problems)
        )
    _require_iso_date(str(document["created_date"]))
    if document["suite_id"] != _suite_id(document):
        raise SuiteError("suite_id does not match canonical registration content")

    registry = _read_registry(task_registry_path)
    registry_problems = validate_document("task", registry)
    if registry_problems:
        raise SuiteError(
            "task registry schema is invalid: "
            + "; ".join(problem.render() for problem in registry_problems)
        )
    tasks: list[dict[str, Any]] = registry["tasks"]
    if len(tasks) != EXPECTED_TASK_COUNT:
        raise SuiteError(
            f"reference suite must contain exactly {EXPECTED_TASK_COUNT} tasks, found {len(tasks)}"
        )
    task_ids = [str(task["task_id"]) for task in tasks]
    if len(set(task_ids)) != len(task_ids):
        raise SuiteError("task registry contains duplicate task IDs")
    if document["task_registry_sha256"] != _sha256(task_registry_path.read_bytes()):
        raise SuiteError("task registry hash drifted after registration")
    if document["ordered_task_ids"] != task_ids:
        raise SuiteError("registered task order or coverage differs from the task registry")

    per_task = document["budgets"]["per_task"]
    suite_total = document["budgets"]["suite_total"]
    if any(suite_total[field] != per_task[field] * EXPECTED_TASK_COUNT for field in per_task):
        raise SuiteError("suite aggregate budgets do not equal ten per-task budgets")

    fixture_versions: dict[str, str] = {}
    for task in tasks:
        fixture_id = str(task["fixture_id"])
        version = str(task["fixture_version"])
        previous = fixture_versions.setdefault(fixture_id, version)
        if previous != version:
            raise SuiteError(
                f"archived task registry has multiple versions for fixture {fixture_id}"
            )
    fixture_ids = set(fixture_versions)
    if set(document["image_identities"]) != fixture_ids:
        raise SuiteError("registered image identity coverage differs from the task registry")
    if set(document["environment_fingerprints"]) != fixture_ids:
        raise SuiteError("registered environment coverage differs from the task registry")

    identities: dict[str, PreparedImageIdentity] = {}
    try:
        for fixture_id, version in sorted(fixture_versions.items()):
            recorded = document["image_identities"][fixture_id]
            identities[fixture_id] = PreparedImageIdentity(
                repository=str(recorded["repository"]),
                tag=version,
                manifest_digest=str(recorded["manifest_digest"]),
                config_digest=str(recorded["config_digest"]),
            )
    except (KeyError, TypeError, ImageIdentityError) as exc:
        raise SuiteError(f"registered image identity is invalid: {exc}") from exc
    return _registration_from_document(document, identities=identities)


TaskExecutor = Callable[[dict[str, Any], Path], str]


def _classify(executor: TaskExecutor, task: dict[str, Any], directory: Path) -> TaskStatus:
    try:
        status = executor(task, directory)
        if status not in VALID_STATUSES:
            raise HarnessFailure(f"executor returned unknown status {status!r}")
        return cast(TaskStatus, status)
    except TimeoutError:
        return "timeout"
    except ProviderFailure:
        return "provider_error"
    except BudgetFailure:
        return "budget_exhausted"
    except FixtureFailure:
        return "fixture_defect"
    except Exception:
        return "harness_error"


def run_suite(
    registration_path: Path,
    *,
    task_registry_path: Path,
    fixture_root: Path,
    out: Path,
    executor: TaskExecutor,
) -> dict[str, Any]:
    """Run every registered task once and retain every classified outcome."""
    registration = load_registration(
        registration_path,
        task_registry_path=task_registry_path,
        fixture_root=fixture_root,
    )
    registry = _read_registry(task_registry_path)
    by_id = {str(task["task_id"]): task for task in registry["tasks"]}
    task_directories = {
        task_id: out / "tasks" / Path(*task_id.split("/"))
        for task_id in registration.ordered_task_ids
    }
    for task_id, directory in task_directories.items():
        if directory.exists():
            raise SuiteError(f"task {task_id} already has an artifact directory: {directory}")

    statuses: list[dict[str, Any]] = []
    for ordinal, task_id in enumerate(registration.ordered_task_ids, start=1):
        directory = task_directories[task_id]
        directory.mkdir(parents=True)
        status = _classify(executor, by_id[task_id], directory)
        record = {
            "schema_version": "1.0.0",
            "suite_id": registration.suite_id,
            "ordinal": ordinal,
            "task_id": task_id,
            "status": status,
        }
        _atomic_json(directory / "status.json", record)
        statuses.append(record)

    counts = Counter(str(record["status"]) for record in statuses)
    summary: dict[str, Any] = {
        "schema_version": "1.0.0",
        "suite_id": registration.suite_id,
        "task_count": len(statuses),
        "ordered_task_ids": list(registration.ordered_task_ids),
        "counts": dict(sorted(counts.items())),
    }
    _atomic_json(out / "summary.json", summary)
    return summary


__all__ = [
    "BudgetFailure",
    "FixtureFailure",
    "HarnessFailure",
    "ProviderFailure",
    "SuiteError",
    "SuiteRegistration",
    "build_registration",
    "load_registration",
    "run_suite",
    "write_registration",
]
