"""Deterministic manifest for the benchmark contracts and committed evidence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TypedDict


class ManifestArtifact(TypedDict):
    path: str
    bytes: int
    sha256: str


class ReleaseManifest(TypedDict):
    schema_version: str
    benchmark_version: str
    artifact_scope: str
    artifacts: list[ManifestArtifact]


_RELEASE_FILES = (
    ".zenodo.json",
    "CITATION.cff",
    "LICENSE",
    "README.md",
    "ledger/README.md",
    "pyproject.toml",
    "runs/README.md",
    "uv.lock",
)
_RELEASE_TREES = (
    "docs",
    "fixtures",
    "ledger",
    "runs",
    "schemas",
    "tasks",
)
_EXCLUDED_PARTS = {
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
    "build",
    "dist",
    "node_modules",
}
_EXCLUDED_PREFIXES = {"docs/superpowers/"}


def _release_paths(root: Path) -> list[Path]:
    paths = {root / relative for relative in _RELEASE_FILES}
    for relative in _RELEASE_TREES:
        tree = root / relative
        if not tree.is_dir():
            raise FileNotFoundError(f"release artifact directory is missing: {relative}")
        paths.update(path for path in tree.rglob("*") if path.is_file())
    filtered = []
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if any(part in _EXCLUDED_PARTS or part.endswith(".egg-info") for part in path.parts):
            continue
        if path.suffix == ".pyc":
            continue
        if any(relative.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            continue
        if path.name == "release-manifest.json":
            continue
        if not path.is_file():
            raise FileNotFoundError(f"release artifact is missing: {relative}")
        filtered.append(path)
    return sorted(filtered, key=lambda path: path.relative_to(root).as_posix())


def build_release_manifest(root: Path) -> ReleaseManifest:
    """Describe the immutable core without timestamps or repository-state noise."""
    root = root.resolve()
    artifacts: list[ManifestArtifact] = []
    for path in _release_paths(root):
        content = path.read_bytes()
        artifacts.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    return {
        "schema_version": "0.1.0",
        "benchmark_version": "0.1.0",
        "artifact_scope": "benchmark_contracts_and_evidence",
        "artifacts": artifacts,
    }
