"""Apply, check, and revert bug patches.

Two properties this module exists to protect:

* Every bug patch applies to the *same* clean base. If mutations were layered,
  a "mutated" tree would contain more than one defect while claiming to contain
  one, and recall would be measured against the wrong ground truth.
* Apply-then-revert restores the original bytes exactly, because gate G2 re-runs
  the clean contract on the reverted tree to prove the patch was the only
  difference.

`git apply` does the work, but the paths in a patch are attacker-controlled data
as far as this code is concerned, so they are checked before git is invoked
rather than relying on `--unsafe-paths` being absent.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath

_GIT_TIMEOUT = 120

#: Line-ending conversion is disabled for every git invocation that touches
#: fixture bytes. Exported because gate G3 needs the same guarantee.
#:
#: With `core.autocrlf=true` — the Windows default, and in force on at least one
#: machine this is developed on — `git apply` rewrites the whole target file in
#: the platform's line endings, not just the hunk. Apply-then-revert then
#: returns a file that differs from the original in every line, so the tree
#: checksum changes and gate G2 can never pass. The failure is silent: the
#: patch applies, the revert reports success, and only a byte comparison shows
#: it. `git archive` converts the same way, which would make a rebuilt tree's
#: checksum depend on the operator's git configuration.
#:
#: These are passed per-invocation rather than relied on from a config, because
#: the whole point is not to depend on how the host is set up. This repository
#: does carry `core.autocrlf=false` locally, and that is precisely why it had to
#: be passed explicitly: without it the gates pass here and nowhere else.
GIT_NO_EOL_REWRITE = ("-c", "core.autocrlf=false", "-c", "core.eol=lf")

#: Target paths in a unified diff header, with the conventional a/ and b/ prefixes.
_DIFF_PATH = re.compile(r"^(?:---|\+\+\+)\s+(?:[ab]/)?(\S+)", re.MULTILINE)


class PatchError(RuntimeError):
    """A patch could not be validated, applied, or reverted."""


#: Artefacts that appear in a working tree but are never committed.
#:
#: A fixture tree is a set of committed bytes. Running its own suite once leaves
#: `__pycache__` and `.pytest_cache` behind, and building fx-ledger-ts leaves
#: `node_modules` and `dist` — none of which are tracked. Copying them would
#: mean the tree a gate measures depends on what the last person ran locally:
#: G3's checksum would stop matching the manifest, and G2 would witness against
#: a tree that is not the fixture.
_EPHEMERAL = (
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "node_modules",
    "dist",
    "build",
    "*.egg-info",
    "*.pyc",
)


def materialise(source_tree: Path, destination: Path) -> Path:
    """Copy a fixture tree so operations never mutate the committed source.

    Build output and caches are left behind, so the copy is the fixture rather
    than the fixture plus whatever the last local test run produced.
    """
    if destination.exists():
        raise PatchError(f"destination already exists: {destination}")
    shutil.copytree(
        source_tree, destination, symlinks=True, ignore=shutil.ignore_patterns(*_EPHEMERAL)
    )
    return destination


def _targets(patch_text: str) -> list[str]:
    return [m.group(1) for m in _DIFF_PATH.finditer(patch_text) if m.group(1) != "/dev/null"]


def _assert_contained(patch_path: Path, patch_text: str) -> None:
    """Reject any patch whose targets leave the tree, before git sees it."""
    for target in _targets(patch_text):
        pure = PurePosixPath(target)
        if pure.is_absolute() or ".." in pure.parts:
            raise PatchError(
                f"{patch_path.name} targets {target!r}, which is outside the fixture tree"
            )


def _read_patch(patch_path: Path) -> str:
    try:
        return patch_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PatchError(f"could not read patch {patch_path.name}: {exc}") from exc


def _run_git_apply(tree: Path, patch_path: Path, *flags: str) -> None:
    patch_text = _read_patch(patch_path)
    _assert_contained(patch_path, patch_text)

    try:
        proc = subprocess.run(
            ["git", *GIT_NO_EOL_REWRITE, "apply", "--numstat", "-q", *flags, "-"],
            cwd=tree,
            input=patch_text.encode("utf-8"),
            capture_output=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PatchError(f"git apply failed for {patch_path.name}: {exc}") from exc

    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise PatchError(f"{patch_path.name}: {detail or 'git apply reported failure'}")


def check_patch(tree: Path, patch_path: Path) -> None:
    """Verify the patch would apply cleanly. Raises `PatchError` if not."""
    _run_git_apply(tree, patch_path, "--check")


def apply_patch(tree: Path, patch_path: Path) -> None:
    """Apply the patch in place. Applying twice fails rather than double-applying."""
    _run_git_apply(tree, patch_path, "--apply")


def revert_patch(tree: Path, patch_path: Path) -> None:
    """Undo a previously applied patch, restoring the original bytes."""
    _run_git_apply(tree, patch_path, "--apply", "--reverse")
