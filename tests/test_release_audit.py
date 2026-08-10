"""Repository release audit: artifact contracts and immutable Git provenance."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from coding_agent_eval.fixtures.image_identity import PreparedImageIdentity
from coding_agent_eval.release_audit import (
    audit_git_history,
    audit_release,
    audit_release_metadata,
    audit_repository,
)
from coding_agent_eval.release_manifest import build_release_manifest


def test_all_implementable_repository_release_contracts_are_clean(repo_root: Path) -> None:
    findings = audit_repository(repo_root)

    assert [finding.render() for finding in findings if finding.blocking] == []
    assert sum(finding.code == "trace.legacy" for finding in findings) == 8


def test_default_audit_stays_green_while_publication_fails_closed(repo_root: Path) -> None:
    default = audit_release(repo_root)
    publication = audit_release(repo_root, publication=True)

    assert [finding for finding in default if finding.blocking] == []
    codes = {finding.code for finding in publication if finding.blocking}
    assert {
        "oci.identity",
        "suite.registration",
        "suite.coverage",
        "suite.trace_contract",
        "suite.outcomes",
        "review.coverage",
        "review.independence",
        "review.resolution",
        "results.replay",
        "claims.scope",
    } <= codes
    assert "git.owner_only" not in codes
    assert "artifact.private_data" not in codes
    assert "oci.anonymous_pull" not in codes


def test_offline_publication_audit_never_uses_the_online_probe(repo_root: Path) -> None:
    def forbidden_probe(*args: object) -> tuple[str, str]:
        raise AssertionError("offline audit invoked a registry/network probe")

    audit_release(
        repo_root,
        publication=True,
        online=False,
        online_probe=forbidden_probe,
    )


def test_online_publication_audit_checks_anonymous_manifest_and_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coding_agent_eval.release_audit as release_audit

    monkeypatch.setattr(release_audit, "audit_repository", lambda root: [])
    observed: list[str] = []
    for fixture_id, digit in (("fx-one", "a"), ("fx-two", "b")):
        fixture = tmp_path / "fixtures" / fixture_id
        fixture.mkdir(parents=True)
        manifest = {
            "fixture_id": fixture_id,
            "fixture_version": "1.0.0",
            "environment": {
                "prepared_image_repository": f"ghcr.io/kuotunyu/{fixture_id}",
                "prepared_image_tag": "1.0.0",
                "prepared_image_manifest_digest": "sha256:" + digit * 64,
                "prepared_image_config_digest": "sha256:" + digit * 64,
                "fingerprint": "sha256:" + "c" * 64,
            },
        }
        (fixture / "fixture.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    def probe(identity: PreparedImageIdentity) -> tuple[str, str]:
        observed.append(identity.immutable_ref)
        return identity.manifest_digest, identity.config_digest

    findings = audit_release(
        tmp_path,
        publication=True,
        online=True,
        online_probe=probe,
    )

    assert len(observed) == 2
    assert not any(finding.code == "oci.anonymous_pull" for finding in findings)


def test_online_publication_audit_rejects_registry_identity_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coding_agent_eval.release_audit as release_audit

    monkeypatch.setattr(release_audit, "audit_repository", lambda root: [])
    fixture = tmp_path / "fixtures" / "fx-one"
    fixture.mkdir(parents=True)
    manifest = {
        "fixture_id": "fx-one",
        "fixture_version": "1.0.0",
        "environment": {
            "prepared_image_repository": "ghcr.io/kuotunyu/fx-one",
            "prepared_image_tag": "1.0.0",
            "prepared_image_manifest_digest": "sha256:" + "a" * 64,
            "prepared_image_config_digest": "sha256:" + "b" * 64,
            "fingerprint": "sha256:" + "c" * 64,
        },
    }
    (fixture / "fixture.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    findings = audit_release(
        tmp_path,
        publication=True,
        online=True,
        online_probe=lambda identity: ("sha256:" + "d" * 64, identity.config_digest),
    )

    assert any(finding.code == "oci.anonymous_pull" for finding in findings)


def test_publication_audit_maps_non_owner_history_to_owner_only(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "someone-else")
    git(tmp_path, "config", "user.email", "someone" + chr(64) + "example.invalid")
    (tmp_path / "evidence.txt").write_text("identity\n", encoding="utf-8")
    git(tmp_path, "add", "evidence.txt")
    git(tmp_path, "commit", "-m", "test: wrong identity")

    findings = audit_release(tmp_path, publication=True)

    assert any(finding.code == "git.owner_only" for finding in findings)


def test_publication_audit_rejects_tracked_private_review_keymaps(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "kuotunyu")
    git(
        tmp_path,
        "config",
        "user.email",
        "61350295+kuotunyu" + chr(64) + "users.noreply.github.com",
    )
    private = tmp_path / "ledger" / "review-sets" / "primary.keymap.json"
    private.parent.mkdir(parents=True)
    private.write_text("{}\n", encoding="utf-8")
    git(tmp_path, "add", private.relative_to(tmp_path).as_posix())
    git(tmp_path, "commit", "-m", "test: private artifact")

    findings = audit_release(tmp_path, publication=True)

    assert any(finding.code == "artifact.private_data" for finding in findings)


def test_release_manifest_is_deterministic_and_covers_core_artifacts(repo_root: Path) -> None:
    first = build_release_manifest(repo_root)
    second = build_release_manifest(repo_root)

    assert first == second
    assert first["schema_version"] == "0.1.0"
    assert first["artifact_scope"] == "benchmark_contracts_and_evidence"
    artifacts = first["artifacts"]
    paths = [artifact["path"] for artifact in artifacts]
    assert paths == sorted(paths)
    assert "release-manifest.json" not in paths
    assert not any("/node_modules/" in f"/{path}/" or "/dist/" in f"/{path}/" for path in paths)
    assert {
        ".zenodo.json",
        "CITATION.cff",
        "ledger/adjudications.jsonl",
        "pyproject.toml",
        "schemas/task.schema.json",
        "tasks/v0.1.json",
        "uv.lock",
    } <= set(paths)
    for artifact in artifacts:
        path = repo_root / artifact["path"]
        assert artifact["bytes"] == path.stat().st_size
        assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def prepare_minimal_release(root: Path, repo_root: Path) -> None:
    for relative in ("docs", "fixtures", "ledger", "runs", "schemas", "tasks"):
        (root / relative).mkdir(parents=True)
    for relative in (
        ".zenodo.json",
        "CITATION.cff",
        "LICENSE",
        "README.md",
        "ledger/README.md",
        "pyproject.toml",
        "runs/README.md",
        "uv.lock",
    ):
        source = repo_root / relative
        target = root / relative
        target.write_bytes(source.read_bytes())
    manifest = build_release_manifest(root)
    (root / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def test_metadata_audit_rejects_an_incomplete_artifact_manifest(
    tmp_path: Path, repo_root: Path
) -> None:
    prepare_minimal_release(tmp_path, repo_root)
    manifest_path = tmp_path / "release-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifacts"] = []
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    findings = audit_release_metadata(tmp_path)

    assert [finding.code for finding in findings] == ["release.manifest"]


def test_metadata_audit_reports_malformed_metadata_instead_of_crashing(
    tmp_path: Path, repo_root: Path
) -> None:
    prepare_minimal_release(tmp_path, repo_root)
    (tmp_path / ".zenodo.json").write_text("not-json", encoding="utf-8")

    findings = audit_release_metadata(tmp_path)

    assert [finding.code for finding in findings] == ["release.metadata"]
    assert findings[0].path == ".zenodo.json"


def test_metadata_audit_reports_a_malformed_creator_entry(tmp_path: Path, repo_root: Path) -> None:
    prepare_minimal_release(tmp_path, repo_root)
    citation_path = tmp_path / "CITATION.cff"
    citation = citation_path.read_text(encoding="utf-8")
    citation_path.write_text(
        citation.replace("  - name: kuotunyu", "  - invalid"), encoding="utf-8"
    )

    findings = audit_release_metadata(tmp_path)

    assert findings[0].code == "release.metadata"
    assert findings[0].path == "CITATION.cff"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_git_audit_reports_a_coauthor_without_rewriting_it(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "kuotunyu")
    git(tmp_path, "config", "user.email", "61350295+kuotunyu@users.noreply.github.com")
    (tmp_path / "evidence.txt").write_text("immutable\n", encoding="utf-8")
    git(tmp_path, "add", "evidence.txt")
    git(
        tmp_path,
        "commit",
        "-m",
        "test: provenance",
        "-m",
        "Co-Authored-By: Claude Opus 5 <noreply" + "@anthropic.com>",
    )

    findings = audit_git_history(tmp_path)

    assert [finding.code for finding in findings] == ["git.coauthor"]
    assert "Claude Opus 5" in findings[0].message
    assert (tmp_path / "evidence.txt").read_text(encoding="utf-8") == "immutable\n"


def test_git_audit_rejects_a_non_owner_author_and_committer(tmp_path: Path) -> None:
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "someone-else")
    git(tmp_path, "config", "user.email", "someone" + "@example.invalid")
    (tmp_path / "evidence.txt").write_text("identity\n", encoding="utf-8")
    git(tmp_path, "add", "evidence.txt")
    git(tmp_path, "commit", "-m", "test: wrong identity")

    findings = audit_git_history(tmp_path)

    assert {finding.code for finding in findings} == {"git.author", "git.committer"}
