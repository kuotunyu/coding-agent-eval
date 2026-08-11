"""Distribution metadata and archive-scope release contracts."""

from __future__ import annotations

import tomllib
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
