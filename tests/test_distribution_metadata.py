"""Distribution metadata and archive-scope release contracts."""

from __future__ import annotations

import subprocess
import tarfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any

from coding_agent_eval import __version__


def load_pyproject(repo_root: Path) -> dict[str, Any]:
    return tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))


def test_distribution_exposes_recruiter_and_index_metadata(repo_root: Path) -> None:
    project = load_pyproject(repo_root)["project"]

    assert project["version"] == __version__ == "0.1.1"
    assert set(project["keywords"]) >= {
        "benchmark",
        "coding-agents",
        "developer-tools",
        "llm-evaluation",
        "reproducibility",
    }
    assert set(project["classifiers"]) >= {
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.12",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    }
    assert project["urls"] == {
        "Repository": "https://github.com/kuotunyu/coding-agent-eval",
        "Documentation": "https://github.com/kuotunyu/coding-agent-eval#readme",
        "Issues": "https://github.com/kuotunyu/coding-agent-eval/issues",
        "Releases": "https://github.com/kuotunyu/coding-agent-eval/releases",
    }


def test_sdist_excludes_internal_design_records(repo_root: Path) -> None:
    sdist = load_pyproject(repo_root)["tool"]["hatch"]["build"]["targets"]["sdist"]

    assert "docs/superpowers/**" in sdist["exclude"]


def test_sdist_includes_the_offline_agent_example_but_wheel_does_not(
    repo_root: Path, tmp_path: Path
) -> None:
    """The example belongs in source releases, never in the importable package."""
    result = subprocess.run(
        ["uv", "build", "--offline", "--out-dir", str(tmp_path)],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr

    sdist = next(tmp_path.glob("*.tar.gz"))
    wheel = next(tmp_path.glob("*.whl"))
    with tarfile.open(sdist) as archive:
        assert any(
            name.endswith("examples/external_agents/scripted_agent.py")
            for name in archive.getnames()
        )
    with zipfile.ZipFile(wheel) as archive:
        assert not any(name.endswith("scripted_agent.py") for name in archive.namelist())

    project = load_pyproject(repo_root)["tool"]["hatch"]["build"]["targets"]
    assert project["wheel"]["packages"] == ["src/coding_agent_eval"]


def test_external_agent_documentation_preserves_the_trust_boundary(repo_root: Path) -> None:
    readme = (repo_root / "README.md").read_text(encoding="utf-8")
    threat_model = (repo_root / "docs" / "THREAT_MODEL.md").read_text(encoding="utf-8")

    for document in (readme, threat_model):
        assert "cae-agent-stdio" in document
        assert "host_unsandboxed" in document
        assert "agent_reported_unverified" in document
    assert "--isolate does not sandbox the external process" in threat_model


def test_external_agent_operator_docs_preserve_run_and_review_boundaries(repo_root: Path) -> None:
    readme = " ".join((repo_root / "README.md").read_text(encoding="utf-8").split())
    manual = " ".join((repo_root / "docs" / "MANUAL_RUN.md").read_text(encoding="utf-8").split())

    assert "Resolve-Path 'examples/external_agents/scripted_agent.py'" in readme
    assert "不是 model benchmark 結果" in readme
    assert "operator-declared identity" in manual
    assert "run.json`、`trace.jsonl` 與 `findings.json`" in manual
    assert "owner-only `.run-store/`" in manual
    assert "blinded primary、independent" in manual
    assert "only the directly launched child" in manual
    assert "does not manage detached descendants" in manual
    assert "must not daemonize or leave helper processes behind" in manual
