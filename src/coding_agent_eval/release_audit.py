"""Fail-closed repository artifact and immutable Git-provenance audit."""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

import yaml
from jsonschema import Draft202012Validator

from coding_agent_eval.evaluator.ledger import LedgerKind, load_ledger
from coding_agent_eval.release_manifest import build_release_manifest
from coding_agent_eval.schemas.loader import load_schema
from coding_agent_eval.tasks import validate_task_registry

OWNER_NAME = "kuotunyu"
OWNER_EMAIL = "61350295+kuotunyu@users.noreply.github.com"
_COAUTHOR = re.compile(r"^Co-Authored-By:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


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


def audit_git_history(root: Path) -> list[AuditFinding]:
    """Report contributor-attribution blockers without changing any commit."""
    findings: list[AuditFinding] = []
    expected_identity = f"{OWNER_NAME} <{OWNER_EMAIL}>"
    for commit in _git(root, "log", "--all", "--format=%H").splitlines():
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
        if document.get("ledger_kind") != "synthetic" or document.get("publishable") is not False:
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
