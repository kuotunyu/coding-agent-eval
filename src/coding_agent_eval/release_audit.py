"""Fail-closed repository artifact and immutable Git-provenance audit."""

from __future__ import annotations

import json
import re
import subprocess
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml
from jsonschema import Draft202012Validator

from coding_agent_eval import PUBLICATION_TRACE_SCHEMA_VERSION
from coding_agent_eval.evaluator.ledger import LedgerKind, load_ledger
from coding_agent_eval.evaluator.metrics import EvaluationError
from coding_agent_eval.evaluator.replay import replay_run
from coding_agent_eval.fixtures.environment import (
    EnvironmentCheckError,
    anonymous_registry_manifest_digest,
    local_config_digest,
)
from coding_agent_eval.fixtures.image_identity import (
    SHA256_DIGEST,
    ImageIdentityError,
    PreparedImageIdentity,
)
from coding_agent_eval.hygiene.leak_scan import LeakScanError, scan_paths, scan_tracked_files
from coding_agent_eval.hygiene.policy import PUBLIC_ARTIFACT_POLICY
from coding_agent_eval.release_manifest import build_release_manifest
from coding_agent_eval.schemas.loader import load_schema
from coding_agent_eval.schemas.validate import validate_document
from coding_agent_eval.suite import (
    VALID_STATUSES,
    SuiteError,
    SuiteRegistration,
    load_registration_snapshot,
)
from coding_agent_eval.tasks import validate_task_registry

OWNER_NAME = "kuotunyu"
OWNER_EMAIL = "61350295+kuotunyu@users.noreply.github.com"
_COAUTHOR = re.compile(r"^Co-Authored-By:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
_PRIVATE_PATH_PARTS = frozenset({".run-store", "__private__"})
OnlineProbe = Callable[[PreparedImageIdentity], tuple[str, str]]


@dataclass(frozen=True)
class AuditFinding:
    code: str
    path: str
    message: str
    blocking: bool = True

    def render(self) -> str:
        level = "BLOCK" if self.blocking else "WARN"
        return f"{level} {self.code} {self.path}: {self.message}"


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git command failed")
    return proc.stdout


def audit_git_history(root: Path, *, revision: str = "--all") -> list[AuditFinding]:
    """Report contributor-attribution blockers without changing any commit."""
    findings: list[AuditFinding] = []
    expected_identity = f"{OWNER_NAME} <{OWNER_EMAIL}>"
    for commit in _git(root, "log", revision, "--format=%H").splitlines():
        fields = _git(root, "show", "-s", "--format=%an%n%ae%n%cn%n%ce%n%B", commit).splitlines()
        if len(fields) < 4:
            findings.append(AuditFinding("git.parse", commit, "could not parse commit identity"))
            continue
        author_name, author_email, committer_name, committer_email = fields[:4]
        body = "\n".join(fields[4:])
        if (author_name, author_email) != (OWNER_NAME, OWNER_EMAIL):
            findings.append(
                AuditFinding(
                    "git.author",
                    commit,
                    f"author is {author_name} <{author_email}>, expected {expected_identity}",
                )
            )
        if (committer_name, committer_email) != (OWNER_NAME, OWNER_EMAIL):
            findings.append(
                AuditFinding(
                    "git.committer",
                    commit,
                    f"committer is {committer_name} <{committer_email}>, "
                    f"expected {expected_identity}",
                )
            )
        for trailer in _COAUTHOR.findall(body):
            findings.append(
                AuditFinding("git.coauthor", commit, f"co-author trailer attributes {trailer}")
            )
    return findings


def _audit_results(root: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    validator = Draft202012Validator(load_schema("results"))
    for path in sorted((root / "runs").glob("baseline-*/results.json")):
        relative = path.relative_to(root).as_posix()
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            findings.append(AuditFinding("result.json", relative, str(exc)))
            continue
        for error in validator.iter_errors(document):
            findings.append(AuditFinding("result.schema", relative, error.message))
        fixture_path = root / "fixtures" / str(document.get("fixture_id")) / "fixture.yaml"
        if fixture_path.is_file():
            fixture = yaml.safe_load(fixture_path.read_text(encoding="utf-8"))
            recorded_version = document.get("fixture_version")
            current_version = fixture.get("fixture_version")
            if recorded_version != current_version:
                findings.append(
                    AuditFinding(
                        "result.fixture_version",
                        relative,
                        f"records {recorded_version}, fixture is {current_version}",
                    )
                )
        if (
            document.get("decision_source") != "synthetic"
            or document.get("publication_reason") != "synthetic_adjudication"
            or document.get("publishable") is not False
        ):
            findings.append(
                AuditFinding(
                    "result.publishability",
                    relative,
                    "committed v0.1 baselines must be synthetic and unpublishable",
                )
            )
    return findings


def _audit_traces(root: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    validator = Draft202012Validator(load_schema("trace-record"))
    for path in sorted((root / "runs").glob("live-*/trace.jsonl")):
        relative = path.relative_to(root).as_posix()
        events: list[str] = []
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                findings.append(AuditFinding("trace.json", f"{relative}:{number}", str(exc)))
                continue
            events.append(str(record.get("event")))
            for error in validator.iter_errors(record):
                findings.append(AuditFinding("trace.schema", f"{relative}:{number}", error.message))
        missing = sorted({"run_header", "cost"} - set(events))
        if missing:
            findings.append(
                AuditFinding(
                    "trace.legacy",
                    relative,
                    f"historical trace is not replayable because it lacks {', '.join(missing)}",
                    blocking=False,
                )
            )
    return findings


def _audit_markdown_links(root: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    paths = [root / "README.md", *sorted((root / "docs").rglob("*.md"))]
    paths += [root / "ledger" / "README.md", root / "runs" / "README.md"]
    for path in paths:
        if not path.is_file():
            continue
        for target in _MARKDOWN_LINK.findall(path.read_text(encoding="utf-8")):
            target = target.strip().strip("<>")
            if not target or target.startswith(("#", "http://", "https://", "mailto:")):
                continue
            target = unquote(target.split("#", 1)[0])
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                findings.append(
                    AuditFinding(
                        "docs.link",
                        path.relative_to(root).as_posix(),
                        f"link target does not exist: {target}",
                    )
                )
    return findings


def audit_release_metadata(root: Path) -> list[AuditFinding]:
    """Validate prepared release metadata and its complete deterministic manifest."""
    findings: list[AuditFinding] = []
    required = [root / "CITATION.cff", root / ".zenodo.json", root / "release-manifest.json"]
    for path in required:
        if not path.is_file():
            findings.append(AuditFinding("release.metadata", path.name, "required file is missing"))
    if findings:
        return findings

    try:
        citation = yaml.safe_load((root / "CITATION.cff").read_text(encoding="utf-8"))
        if not isinstance(citation, dict):
            raise TypeError("CITATION.cff must contain a mapping")
    except (OSError, TypeError, yaml.YAMLError) as exc:
        return [AuditFinding("release.metadata", "CITATION.cff", str(exc))]
    authors = citation.get("authors")
    if not isinstance(authors, list) or not all(isinstance(author, dict) for author in authors):
        return [
            AuditFinding("release.metadata", "CITATION.cff", "authors must be a list of mappings")
        ]
    aliases = {str(author.get("alias") or author.get("name") or "") for author in authors}
    if aliases != {OWNER_NAME}:
        findings.append(
            AuditFinding(
                "release.creator", "CITATION.cff", f"creator aliases are {sorted(aliases)}"
            )
        )
    expected_citation = {
        "cff-version": "1.2.0",
        "license": "MIT",
        "type": "software",
        "version": "0.1.0",
    }
    for field, expected in expected_citation.items():
        if citation.get(field) != expected:
            findings.append(
                AuditFinding("release.metadata", "CITATION.cff", f"{field} must be {expected!r}")
            )

    try:
        zenodo = json.loads((root / ".zenodo.json").read_text(encoding="utf-8"))
        if not isinstance(zenodo, dict):
            raise TypeError(".zenodo.json must contain an object")
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        return [*findings, AuditFinding("release.metadata", ".zenodo.json", str(exc))]
    creator_entries = zenodo.get("creators")
    if not isinstance(creator_entries, list) or not all(
        isinstance(creator, dict) for creator in creator_entries
    ):
        return [
            *findings,
            AuditFinding("release.metadata", ".zenodo.json", "creators must be a list of objects"),
        ]
    creators = {str(creator.get("name")) for creator in creator_entries}
    if creators != {OWNER_NAME}:
        findings.append(
            AuditFinding("release.creator", ".zenodo.json", f"creators are {sorted(creators)}")
        )
    expected_zenodo = {
        "access_right": "open",
        "license": "mit",
        "upload_type": "software",
        "version": "0.1.0",
    }
    for field, expected in expected_zenodo.items():
        if zenodo.get(field) != expected:
            findings.append(
                AuditFinding("release.metadata", ".zenodo.json", f"{field} must be {expected!r}")
            )

    try:
        manifest = json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))
        expected_manifest = build_release_manifest(root)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        return [*findings, AuditFinding("release.metadata", "release-manifest.json", str(exc))]
    if manifest != expected_manifest:
        findings.append(
            AuditFinding(
                "release.manifest",
                "release-manifest.json",
                "manifest is incomplete, stale, or not deterministically generated",
            )
        )
    return findings


def _fixture_identities(
    root: Path,
) -> tuple[dict[str, PreparedImageIdentity], list[AuditFinding]]:
    """Read every declared OCI identity without resolving it over the network."""
    identities: dict[str, PreparedImageIdentity] = {}
    findings: list[AuditFinding] = []
    manifests = sorted((root / "fixtures").glob("*/fixture.yaml"))
    if not manifests:
        return {}, [AuditFinding("oci.identity", "fixtures", "no fixture manifests found")]
    for path in manifests:
        relative = path.relative_to(root).as_posix()
        try:
            loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise TypeError("fixture manifest must contain a mapping")
            fixture_id = loaded.get("fixture_id")
            environment = loaded.get("environment")
            if not isinstance(fixture_id, str) or not isinstance(environment, Mapping):
                raise TypeError("fixture_id and environment are required")
            identity = PreparedImageIdentity.from_environment(environment)
            fingerprint = environment.get("fingerprint")
            if not isinstance(fingerprint, str) or SHA256_DIGEST.fullmatch(fingerprint) is None:
                raise ImageIdentityError("environment fingerprint must be a sha256 digest")
            identities[fixture_id] = identity
        except (OSError, TypeError, ImageIdentityError, yaml.YAMLError) as exc:
            findings.append(AuditFinding("oci.identity", relative, str(exc)))
    return identities, findings


def _anonymous_image_probe(identity: PreparedImageIdentity) -> tuple[str, str]:
    """Resolve and pull with no credentials, then inspect the pulled config object."""
    observed_manifest = anonymous_registry_manifest_digest(identity.immutable_ref)
    observed_config = local_config_digest(identity.immutable_ref)
    if observed_config is None:
        raise EnvironmentCheckError(
            f"anonymous pull left no inspectable image for {identity.immutable_ref}"
        )
    return observed_manifest, observed_config


def _audit_anonymous_images(
    identities: Mapping[str, PreparedImageIdentity], probe: OnlineProbe
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for fixture_id, identity in sorted(identities.items()):
        try:
            manifest_digest, config_digest = probe(identity)
        except Exception as exc:
            findings.append(
                AuditFinding(
                    "oci.anonymous_pull",
                    f"fixtures/{fixture_id}/fixture.yaml",
                    f"anonymous registry verification failed: {exc}",
                )
            )
            continue
        mismatches: list[str] = []
        if manifest_digest != identity.manifest_digest:
            mismatches.append(
                f"manifest is {manifest_digest!r}, expected {identity.manifest_digest!r}"
            )
        if config_digest != identity.config_digest:
            mismatches.append(f"config is {config_digest!r}, expected {identity.config_digest!r}")
        if mismatches:
            findings.append(
                AuditFinding(
                    "oci.anonymous_pull",
                    f"fixtures/{fixture_id}/fixture.yaml",
                    "; ".join(mismatches),
                )
            )
    return findings


def _read_json_object(path: Path) -> dict[str, Any]:
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise TypeError("document must be a JSON object")
    return loaded


def _task_registry(path: Path) -> tuple[dict[str, dict[str, Any]], list[str]]:
    document = _read_json_object(path)
    tasks = document.get("tasks")
    if not isinstance(tasks, list) or not all(isinstance(task, dict) for task in tasks):
        raise TypeError("task registry must contain a tasks array of objects")
    typed_tasks: list[dict[str, Any]] = tasks
    by_id = {str(task.get("task_id")): task for task in typed_tasks}
    return by_id, [str(task.get("task_id")) for task in typed_tasks]


def _publication_registration(
    root: Path,
) -> tuple[Path | None, Path | None, SuiteRegistration | None, list[AuditFinding]]:
    registrations = sorted((root / "runs" / "reference").glob("**/registration.json"))
    if len(registrations) != 1:
        return (
            None,
            None,
            None,
            [
                AuditFinding(
                    "suite.registration",
                    "runs/reference",
                    f"expected exactly one immutable registration, found {len(registrations)}",
                )
            ],
        )
    path = registrations[0]
    registry_path = path.parent / "task-registry.json"
    try:
        registration = load_registration_snapshot(
            path,
            task_registry_path=registry_path,
        )
    except (OSError, TypeError, SuiteError) as exc:
        return (
            path,
            None,
            None,
            [
                AuditFinding(
                    "suite.registration",
                    path.relative_to(root).as_posix(),
                    str(exc),
                )
            ],
        )
    return path, registry_path, registration, []


def _load_trace(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    problems: list[str] = []
    if not path.is_file():
        return [], ["public trace is missing"]
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"line {number} is not JSON: {exc}")
            continue
        if not isinstance(loaded, dict):
            problems.append(f"line {number} is not an object")
            continue
        records.append(loaded)
        problems.extend(problem.render() for problem in validate_document("trace-record", loaded))
        if loaded.get("schema_version") != PUBLICATION_TRACE_SCHEMA_VERSION:
            problems.append(
                f"line {number} uses trace schema {loaded.get('schema_version')!r}, "
                f"expected {PUBLICATION_TRACE_SCHEMA_VERSION!r}"
            )
    return records, problems


def _single_event_payload(
    records: list[dict[str, Any]], event: str, problems: list[str]
) -> Mapping[str, Any] | None:
    matches = [record for record in records if record.get("event") == event]
    if len(matches) != 1:
        problems.append(f"expected exactly one {event} event, found {len(matches)}")
        return None
    payload = matches[0].get("payload")
    if not isinstance(payload, Mapping):
        problems.append(f"{event} payload is not an object")
        return None
    return payload


def _trace_contract_problems(
    path: Path,
    *,
    task: Mapping[str, Any],
    registration: SuiteRegistration,
) -> tuple[list[dict[str, Any]], list[str]]:
    records, problems = _load_trace(path)
    if not records:
        return records, problems
    header = _single_event_payload(records, "run_header", problems)
    termination = _single_event_payload(records, "termination", problems)
    cost = _single_event_payload(records, "cost", problems)
    if header is not None:
        fixture_id = str(task["fixture_id"])
        identity = registration.image_identities.get(fixture_id)
        expected: dict[str, object] = {
            "run_id": (f"{registration.suite_id}-{str(task['task_id']).replace('/', '-')}"),
            "fixture_id": fixture_id,
            "fixture_version": task["fixture_version"],
            "fixture_tree_checksum": task["tree_checksum"],
            "snapshot": task["snapshot"],
            "provider": registration.api,
            "model": registration.model,
            "env_fingerprint": registration.environment_fingerprints.get(fixture_id),
            "sandbox_profile": "measure",
            "budget": dict(registration.budgets["per_task"]),
        }
        if identity is not None:
            expected.update(
                {
                    "image_ref": identity.immutable_ref,
                    "image_manifest_digest": identity.manifest_digest,
                    "image_config_digest": identity.config_digest,
                    "tool_backend": f"measure_container:{identity.manifest_digest}",
                }
            )
        for field, value in expected.items():
            if header.get(field) != value:
                problems.append(f"run_header {field} is {header.get(field)!r}, expected {value!r}")
    if termination is not None:
        wall_clock_ms = termination.get("wall_clock_ms")
        if not isinstance(wall_clock_ms, int) or wall_clock_ms < 0:
            problems.append("termination must record a non-negative wall_clock_ms")
    if cost is not None:
        amount = cost.get("estimated_cost_usd")
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount < 0:
            problems.append("cost must record a non-negative estimated_cost_usd")
        if not isinstance(cost.get("pricing_table_version"), str):
            problems.append("cost must record pricing_table_version")
    for record in records:
        if record.get("event") != "llm_call":
            continue
        payload = record.get("payload")
        latency = payload.get("latency_ms") if isinstance(payload, Mapping) else None
        if not isinstance(latency, int) or latency < 0:
            problems.append("every llm_call must record a non-negative latency_ms")
    return records, problems


def _status_paths(suite_root: Path, task_ids: tuple[str, ...]) -> dict[str, Path]:
    return {
        task_id: suite_root / "tasks" / Path(*task_id.split("/")) / "status.json"
        for task_id in task_ids
    }


def _audit_suite_outcomes(
    root: Path,
    registration_path: Path,
    registration: SuiteRegistration,
) -> tuple[dict[str, dict[str, Any]], list[AuditFinding]]:
    suite_root = registration_path.parent
    findings: list[AuditFinding] = []
    statuses: dict[str, dict[str, Any]] = {}
    paths = _status_paths(suite_root, registration.ordered_task_ids)
    discovered = {
        path.parent.relative_to(suite_root / "tasks").as_posix()
        for path in (suite_root / "tasks").glob("**/status.json")
    }
    expected = set(registration.ordered_task_ids)
    if discovered != expected:
        findings.append(
            AuditFinding(
                "suite.coverage",
                registration_path.relative_to(root).as_posix(),
                f"status coverage differs: missing {sorted(expected - discovered)}, "
                f"unexpected {sorted(discovered - expected)}",
            )
        )
    for ordinal, task_id in enumerate(registration.ordered_task_ids, start=1):
        path = paths[task_id]
        try:
            status = _read_json_object(path)
        except (OSError, TypeError, json.JSONDecodeError) as exc:
            findings.append(
                AuditFinding(
                    "suite.outcomes",
                    path.relative_to(root).as_posix(),
                    f"cannot read retained outcome: {exc}",
                )
            )
            continue
        statuses[task_id] = status
        expected_fields = {
            "schema_version": "1.0.0",
            "suite_id": registration.suite_id,
            "ordinal": ordinal,
            "task_id": task_id,
        }
        mismatches = [
            f"{field}={status.get(field)!r}"
            for field, expected_value in expected_fields.items()
            if status.get(field) != expected_value
        ]
        status_name = status.get("status")
        if status_name not in VALID_STATUSES:
            mismatches.append(f"status={status_name!r}")
        if mismatches:
            findings.append(
                AuditFinding(
                    "suite.outcomes",
                    path.relative_to(root).as_posix(),
                    "invalid retained outcome: " + ", ".join(mismatches),
                )
            )

    summary_path = suite_root / "summary.json"
    try:
        summary = _read_json_object(summary_path)
        counts = Counter(str(status["status"]) for status in statuses.values())
        expected_summary = {
            "schema_version": "1.0.0",
            "suite_id": registration.suite_id,
            "task_count": len(registration.ordered_task_ids),
            "ordered_task_ids": list(registration.ordered_task_ids),
            "counts": dict(sorted(counts.items())),
        }
        if summary != expected_summary:
            raise ValueError("summary does not exactly match the ten retained outcomes")
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        findings.append(
            AuditFinding(
                "suite.outcomes",
                summary_path.relative_to(root).as_posix(),
                str(exc),
            )
        )
    return statuses, findings


def _add_review_failure(
    findings: list[AuditFinding], relative: str, message: str, *, all_gates: bool = False
) -> None:
    lowered = message.lower()
    codes: set[str] = set()
    if all_gates or any(word in lowered for word in ("coverage", "candidate", "missing")):
        codes.add("review.coverage")
    if all_gates or any(
        word in lowered for word in ("independent", "reviewer", "fixture author", "operator")
    ):
        codes.add("review.independence")
    if all_gates or any(word in lowered for word in ("disagreement", "resolver", "resolution")):
        codes.add("review.resolution")
    if not codes:
        codes.add("review.coverage")
    findings.extend(AuditFinding(code, relative, message) for code in sorted(codes))


def _result_review_path(root: Path, result: Mapping[str, Any]) -> Path | None:
    review_set_id = result.get("review_set_id")
    if not isinstance(review_set_id, str):
        return None
    direct = root / "ledger" / "review-sets" / review_set_id
    if direct.is_dir():
        return direct
    matches = [
        path.parent
        for path in (root / "ledger" / "review-sets").glob("**/manifest.json")
        if _manifest_review_set_id(path) == review_set_id
    ]
    return matches[0] if len(matches) == 1 else None


def _manifest_review_set_id(path: Path) -> str | None:
    try:
        value = _read_json_object(path).get("review_set_id")
    except (OSError, TypeError, json.JSONDecodeError):
        return None
    return str(value) if isinstance(value, str) else None


def _audit_replay(
    root: Path,
    task: Mapping[str, Any],
    task_dir: Path,
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    results_path = task_dir / "results.json"
    relative = results_path.relative_to(root).as_posix()
    try:
        result = _read_json_object(results_path)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        message = f"publishable replay result is missing or invalid: {exc}"
        findings.append(AuditFinding("results.replay", relative, message))
        _add_review_failure(findings, relative, message, all_gates=True)
        return findings
    result_problems = validate_document("results", result)
    if result_problems:
        findings.append(
            AuditFinding(
                "results.replay",
                relative,
                "; ".join(problem.render() for problem in result_problems),
            )
        )
    if (
        result.get("decision_source") != "dual_review"
        or result.get("publishable") is not True
        or result.get("publication_reason") != "dual_review_complete"
    ):
        message = "reference result is not complete dual-review publication evidence"
        findings.append(AuditFinding("results.replay", relative, message))
        _add_review_failure(findings, relative, message, all_gates=True)
        return findings
    review_set = _result_review_path(root, result)
    if review_set is None:
        message = "result does not resolve to exactly one public review set"
        findings.append(AuditFinding("results.replay", relative, message))
        _add_review_failure(findings, relative, message, all_gates=True)
        return findings

    fixture_id = str(task["fixture_id"])
    bug_id = task.get("bug_id")
    bug_paths: tuple[Path, ...] = ()
    if isinstance(bug_id, str):
        bug_paths = (root / "fixtures" / fixture_id / "bugs" / f"{bug_id.rsplit('/', 1)[-1]}.yaml",)
    try:
        replayed = replay_run(
            trace_path=task_dir / "trace.jsonl",
            fixture_path=root / "fixtures" / fixture_id / "fixture.yaml",
            bug_paths=bug_paths,
            review_set_path=review_set,
        )
    except EvaluationError as exc:
        message = str(exc)
        findings.append(AuditFinding("results.replay", relative, message))
        _add_review_failure(findings, review_set.relative_to(root).as_posix(), message)
        return findings

    expected_document = replayed.as_dict()
    expected_text = (
        json.dumps(expected_document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    )
    if result != expected_document or results_path.read_text(encoding="utf-8") != expected_text:
        findings.append(
            AuditFinding(
                "results.replay",
                relative,
                "results.json differs from deterministic replay bytes",
            )
        )
    return findings


def _audit_publication_suite(
    root: Path,
    registration_path: Path | None,
    task_registry_path: Path | None,
    registration: SuiteRegistration | None,
) -> list[AuditFinding]:
    if registration_path is None or task_registry_path is None or registration is None:
        unavailable = "cannot verify without one valid immutable suite registration"
        return [
            AuditFinding(code, "runs/reference", unavailable)
            for code in (
                "suite.coverage",
                "suite.trace_contract",
                "suite.outcomes",
                "review.coverage",
                "review.independence",
                "review.resolution",
                "results.replay",
            )
        ]

    findings: list[AuditFinding] = []
    try:
        tasks, registry_order = _task_registry(task_registry_path)
    except (OSError, TypeError, json.JSONDecodeError) as exc:
        relative_registry = (
            task_registry_path.relative_to(root).as_posix()
            if task_registry_path.is_relative_to(root)
            else str(task_registry_path)
        )
        return [
            AuditFinding("suite.coverage", relative_registry, str(exc)),
            AuditFinding("suite.trace_contract", "runs/reference", str(exc)),
            AuditFinding("suite.outcomes", "runs/reference", str(exc)),
            AuditFinding("results.replay", "runs/reference", str(exc)),
        ]
    if registry_order != list(registration.ordered_task_ids):
        findings.append(
            AuditFinding(
                "suite.coverage",
                registration_path.relative_to(root).as_posix(),
                "registered order differs from the task registry",
            )
        )
    statuses, outcome_findings = _audit_suite_outcomes(root, registration_path, registration)
    findings.extend(outcome_findings)
    suite_root = registration_path.parent
    for task_id in registration.ordered_task_ids:
        task = tasks.get(task_id)
        if task is None:
            continue
        task_dir = suite_root / "tasks" / Path(*task_id.split("/"))
        status = statuses.get(task_id, {}).get("status")
        trace_required = status in {
            "completed",
            "provider_error",
            "timeout",
            "budget_exhausted",
        }
        if trace_required:
            _, problems = _trace_contract_problems(
                task_dir / "trace.jsonl", task=task, registration=registration
            )
            if problems:
                findings.append(
                    AuditFinding(
                        "suite.trace_contract",
                        (task_dir / "trace.jsonl").relative_to(root).as_posix(),
                        "; ".join(problems[:8]),
                    )
                )
        if status == "completed":
            findings.extend(_audit_replay(root, task, task_dir))
    if not statuses:
        missing = "no retained task outcomes are available"
        _add_review_failure(findings, "runs/reference", missing, all_gates=True)
        findings.append(AuditFinding("results.replay", "runs/reference", missing))
    return findings


def _audit_claim_scope(root: Path, registration: SuiteRegistration | None) -> list[AuditFinding]:
    if registration is None:
        return [
            AuditFinding(
                "claims.scope",
                "README.md",
                "claims cannot identify an absent reference-suite registration",
            )
        ]
    paths = [
        root / "README.md",
        root / "docs" / "BENCHMARK_CARD.md",
        root / "docs" / "REFERENCE_SUITE.md",
        root / "docs" / "RELEASE_READINESS.md",
    ]
    text = "\n".join(path.read_text(encoding="utf-8") for path in paths if path.is_file())
    lowered = text.lower()
    missing: list[str] = []
    for label, value in (
        ("suite_id", registration.suite_id),
        ("model", registration.model),
        ("provider", registration.provider),
    ):
        if value.lower() not in lowered:
            missing.append(label)
    if not re.search(r"(?:10[- ]task|10\s*(?:個|項).*(?:task|任務)|10 tasks)", lowered):
        missing.append("task_count")
    if missing:
        return [
            AuditFinding(
                "claims.scope",
                "README.md;docs/BENCHMARK_CARD.md;docs/REFERENCE_SUITE.md",
                "reference claim surface omits " + ", ".join(missing),
            )
        ]
    return []


def _audit_owner_only(root: Path) -> list[AuditFinding]:
    if not (root / ".git").exists():
        return [
            AuditFinding(
                "git.owner_only", ".git", "Git history is unavailable for contributor audit"
            )
        ]
    try:
        history_findings = audit_git_history(root, revision="HEAD")
    except RuntimeError as exc:
        return [AuditFinding("git.owner_only", ".git", str(exc))]
    return [
        AuditFinding(
            "git.owner_only",
            finding.path,
            f"{finding.code}: {finding.message}",
        )
        for finding in history_findings
    ]


def _release_manifest_paths(root: Path) -> list[Path]:
    manifest_path = root / "release-manifest.json"
    try:
        manifest = _read_json_object(manifest_path)
    except (OSError, TypeError, json.JSONDecodeError):
        return []
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list):
        return []
    paths: list[Path] = []
    for artifact in artifacts:
        if isinstance(artifact, Mapping) and isinstance(artifact.get("path"), str):
            paths.append(root / str(artifact["path"]))
    return paths


def _private_path(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return True
    lowered = relative.lower()
    parts = set(Path(lowered).parts)
    name = path.name.lower()
    return (
        bool(parts & _PRIVATE_PATH_PARTS)
        or "keymap" in name
        or lowered.endswith(".docker/config.json")
        or name in {"docker-auth.json", "auth.json"}
        or (name == ".env" or (name.startswith(".env.") and name != ".env.example"))
    )


def _audit_private_data(root: Path) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    try:
        tracked_output = _git(
            root, "ls-files", "--cached", "--others", "--exclude-standard"
        ).splitlines()
        candidates = [root / name for name in tracked_output]
    except RuntimeError as exc:
        return [AuditFinding("artifact.private_data", ".git", str(exc))]
    candidates.extend(_release_manifest_paths(root))
    by_relative = {
        path.relative_to(root).as_posix(): path
        for path in candidates
        if path.exists() and path.is_file() and path.is_relative_to(root)
    }
    for relative, path in sorted(by_relative.items()):
        if _private_path(path, root):
            findings.append(
                AuditFinding(
                    "artifact.private_data", relative, "private path must not be published"
                )
            )
    try:
        for leak in scan_tracked_files(root):
            findings.append(
                AuditFinding(
                    "artifact.private_data",
                    leak.path,
                    f"tracked content matches private-data rule {leak.rule}",
                )
            )
    except LeakScanError as exc:
        findings.append(AuditFinding("artifact.private_data", ".git", str(exc)))

    public_paths = [
        path
        for path in by_relative.values()
        if path.is_relative_to(root / "runs" / "reference")
        or path.is_relative_to(root / "ledger" / "review-sets")
    ]
    for leak in scan_paths(public_paths, root=root, policy=PUBLIC_ARTIFACT_POLICY):
        findings.append(
            AuditFinding(
                "artifact.private_data",
                leak.path,
                f"public evidence matches private-data rule {leak.rule}",
            )
        )
    return findings


def audit_repository(root: Path, *, check_git_history: bool = False) -> list[AuditFinding]:
    """Audit every implementable release contract; history is opt-in and immutable."""
    root = root.resolve()
    findings: list[AuditFinding] = []
    for problem in validate_task_registry(root / "tasks" / "v0.1.json", root / "fixtures"):
        findings.append(AuditFinding("task.registry", problem.task_id, problem.message))
    try:
        load_ledger(root / "ledger" / "adjudications.jsonl", kind=LedgerKind.FORMAL)
    except Exception as exc:
        findings.append(AuditFinding("ledger.formal", "ledger/adjudications.jsonl", str(exc)))
    findings.extend(_audit_results(root))
    findings.extend(_audit_traces(root))
    findings.extend(_audit_markdown_links(root))
    findings.extend(audit_release_metadata(root))
    if check_git_history:
        findings.extend(audit_git_history(root))
    return findings


def audit_release(
    root: Path,
    *,
    publication: bool = False,
    online: bool = False,
    online_probe: OnlineProbe | None = None,
) -> list[AuditFinding]:
    """Audit repository integrity, and optionally all publication evidence.

    The publication audit is fully offline unless ``online`` is explicitly set.
    Online mode is deliberately invalid without publication mode because registry
    reachability cannot turn an otherwise incomplete evidence set into a release.
    """
    if online and not publication:
        raise ValueError("online release audit requires publication=True")
    root = root.resolve()
    findings = audit_repository(root)
    if not publication:
        return findings

    identities, identity_findings = _fixture_identities(root)
    findings.extend(identity_findings)
    registration_path, task_registry_path, registration, registration_findings = (
        _publication_registration(root)
    )
    findings.extend(registration_findings)
    findings.extend(
        _audit_publication_suite(root, registration_path, task_registry_path, registration)
    )
    findings.extend(_audit_claim_scope(root, registration))
    findings.extend(_audit_owner_only(root))
    findings.extend(_audit_private_data(root))
    if online:
        findings.extend(_audit_anonymous_images(identities, online_probe or _anonymous_image_probe))
    return findings
