"""Where the tool surface's bytes come from (design spec §9.1, §9.2).

The four tools read files, and until now they read them with `open()` in the
harness process. That works, and it means a defect in the path check — one `..`
let through, a symlink followed where it should not have been — reaches the host
filesystem with the harness's own privileges. The check is the only thing
standing there.

Spec §9.1 puts the measure phase's tools in a container with `--network none`
and no host mount. This module is the seam that makes that possible: the tools
ask a backend for bytes, and the backend decides whether that means `open()` on
the host or a process inside a container that has no host path to reach at all.

Both backends stay. `LocalTree` is what the default suite uses, because several
hundred tests that each start a container would not be a suite anyone runs. The
container backend is what a measured run should use, and G9's Docker variant
asserts the two produce identical scores — same numbers, different blast radius.

**The path checks are not removed for the container backend.** They run first
either way. Defence in depth is the point: the lexical check is cheap and
catches the common case with a clear message, and the kernel is what catches the
case the check got wrong.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol, runtime_checkable

#: The tree root, as the tools address it.
ROOT = "."

#: Ceiling on the bytes one subtree read may move.
#:
#: `search_code` reads a whole subtree to match against it. Both fixtures are
#: around a megabyte, so this is far above any real use — it exists so that
#: pointing the tool at something enormous fails with a sentence instead of by
#: exhausting memory.
MAX_SUBTREE_BYTES = 32 * 1024 * 1024


class ToolFailure(Exception):
    """An expected failure: report it to the agent and carry on.

    Distinct from an unexpected exception, which is a harness problem. Only the
    latter counts toward the consecutive-failure limit that ends a run.

    Defined here rather than in `tools` because the backends raise it too, and
    the tool surface imports it back so `tools.ToolFailure` keeps working.
    """


@dataclass(frozen=True)
class DirEntry:
    """One entry in a directory listing, addressed as the tools address it."""

    path: str
    is_dir: bool


def normalise(relative: str) -> str:
    """Lexically validate a tree-relative path and return it in POSIX form.

    Rejects absolute paths, backslashes, and any `..` segment before anything
    touches a filesystem. This is the check both backends share; each adds its
    own containment on top, because a lexical check cannot see a symlink.
    """
    if not isinstance(relative, str) or not relative.strip():
        raise ToolFailure("path must be a non-empty string")

    pure = PurePosixPath(relative)
    if pure.is_absolute() or "\\" in relative or ".." in pure.parts:
        raise ToolFailure(
            f"path {relative!r} must be relative to the tree root, with no '..' segment"
        )
    return pure.as_posix()


def over_limit(relative: str, size: int, cap: int) -> ToolFailure:
    """The one wording for "too big", so both backends say the same thing."""
    return ToolFailure(f"{relative!r} is {size} bytes, over the {cap} byte limit")


def no_file(relative: str) -> ToolFailure:
    return ToolFailure(f"no file at {relative!r}")


def no_directory(relative: str) -> ToolFailure:
    return ToolFailure(f"no directory at {relative!r}")


@runtime_checkable
class TreeBackend(Protocol):
    """Everything the tool surface needs from a tree, and nothing more.

    Deliberately three methods. A backend that could also write, execute, or
    stat arbitrary paths would be a larger thing to have to trust, and none of
    the four tools needs any of that.
    """

    @property
    def description(self) -> str:
        """How a run records what its tools could reach. Goes in the result."""
        ...

    def read_bytes(self, relative: str, *, max_bytes: int) -> bytes:
        """One file's bytes, refusing before transfer if it is over `max_bytes`."""
        ...

    def list_entries(self, relative: str) -> list[DirEntry]:
        """One directory's entries, directories first, then by path."""
        ...

    def read_subtree(self, relative: str) -> list[tuple[str, bytes]]:
        """Every regular file under a directory, sorted, as (tree-relative path, bytes)."""
        ...


@dataclass(frozen=True)
class LocalTree:
    """Reads a materialised tree in the harness process.

    Isolated by its own path checks and nothing else — which is exactly the
    property that made moving to a container worth doing. Kept because the
    default suite needs a backend that costs nothing to start.
    """

    root: Path

    @property
    def description(self) -> str:
        return "host_process"

    def _locate(self, relative: str) -> Path:
        """Resolve within the tree, refusing a symlink that points out of it.

        The lexical check in `normalise` has already run. This is the part it
        cannot do: `escape.txt -> /etc/passwd` is a perfectly ordinary relative
        path until the filesystem is consulted.
        """
        root = self.root.resolve()
        candidate = (root / relative).resolve()
        if candidate != root and root not in candidate.parents:
            raise ToolFailure(f"path {relative!r} resolves outside the tree")
        return candidate

    def read_bytes(self, relative: str, *, max_bytes: int) -> bytes:
        path = self._locate(relative)
        if not path.is_file():
            raise no_file(relative)
        size = path.stat().st_size
        if size > max_bytes:
            raise over_limit(relative, size, max_bytes)
        return path.read_bytes()

    def list_entries(self, relative: str) -> list[DirEntry]:
        path = self._locate(relative)
        if not path.is_dir():
            raise no_directory(relative)
        root = self.root.resolve()
        entries = [
            DirEntry(path=child.relative_to(root).as_posix(), is_dir=child.is_dir())
            for child in path.iterdir()
        ]
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.path))

    def read_subtree(self, relative: str) -> list[tuple[str, bytes]]:
        directory = self._locate(relative)
        if not directory.is_dir():
            raise no_directory(relative)

        root = self.root.resolve()
        collected: list[tuple[str, bytes]] = []
        total = 0
        for candidate in sorted(directory.rglob("*"), key=lambda p: p.as_posix()):
            if not candidate.is_file() or candidate.is_symlink():
                continue
            try:
                payload = candidate.read_bytes()
            except OSError:
                continue  # Unreadable files are skipped, not reported as errors.
            total += len(payload)
            if total > MAX_SUBTREE_BYTES:
                raise over_limit(relative, total, MAX_SUBTREE_BYTES)
            collected.append((candidate.relative_to(root).as_posix(), payload))
        return collected
