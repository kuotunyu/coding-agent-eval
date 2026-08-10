"""Locate and load the JSON Schema documents.

Schemas are data files rather than Python literals, so the contract can be read,
diffed, and handed to a provider as a tool-call schema without going through this
codebase.

They are authored at the repository root and copied into the package at build
time. Shipping only the code produces a package that imports cleanly and then
cannot validate anything — a failure that appears after publication rather than
in the test suite, since a checkout always has the root copy at hand.
"""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import Any

_SUFFIX = ".schema.json"


class SchemaNotFoundError(LookupError):
    """A schema, or the schema directory itself, could not be located."""


#: Candidate locations, most specific first. The packaged copy is placed by a
#: force-include in pyproject.toml; the repository root is the authoring location.
_PACKAGED = Path(__file__).resolve().parent.parent / "_schemas"
_REPO_ROOT = Path(__file__).resolve().parents[3] / "schemas"
SCHEMA_DIR_CANDIDATES: tuple[Path, ...] = (_PACKAGED, _REPO_ROOT)


def schema_dir() -> Path:
    for candidate in SCHEMA_DIR_CANDIDATES:
        if candidate.is_dir():
            return candidate
    raise SchemaNotFoundError(
        "no schema directory found; looked in " + ", ".join(str(c) for c in SCHEMA_DIR_CANDIDATES)
    )


#: Resolved at import time for convenience; `schema_dir()` is the authority.
SCHEMA_DIR: Path = schema_dir()


def schema_names() -> tuple[str, ...]:
    directory = schema_dir()
    return tuple(sorted(p.name[: -len(_SUFFIX)] for p in directory.glob(f"*{_SUFFIX}")))


@cache
def load_schema(name: str) -> dict[str, Any]:
    directory = schema_dir()
    path = directory / f"{name}{_SUFFIX}"
    if not path.is_file():
        raise SchemaNotFoundError(f"no schema named {name!r} in {directory}")
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data
