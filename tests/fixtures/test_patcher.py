"""Patch application (design spec §5.1, gate G2 step 2).

Two properties matter more than the mechanics. Every bug patch must apply to the
*same* clean base, or the bugs are not independent and a mutated tree contains
more than one defect without saying so. And apply-then-revert must restore the
original bytes exactly, because gate G2 uses the restored tree to re-run the
clean contract.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent_eval.fixtures.checksum import tree_checksum
from coding_agent_eval.fixtures.patcher import (
    PatchError,
    apply_patch,
    check_patch,
    materialise,
    revert_patch,
)

ORIGINAL = "def verify(a, b):\n    return compare_digest(a, b)\n"
MUTATED = "def verify(a, b):\n    return a == b\n"

PATCH = """\
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,2 +1,2 @@
 def verify(a, b):
-    return compare_digest(a, b)
+    return a == b
"""

SECOND_PATCH = """\
--- a/src/util.py
+++ b/src/util.py
@@ -1 +1 @@
-LIMIT = 100
+LIMIT = 0
"""

ESCAPING_PATCH = """\
--- a/../outside.txt
+++ b/../outside.txt
@@ -0,0 +1 @@
+owned
"""


# Everything below is written as bytes rather than through `write_text`, which
# on Windows emits CRLF. Committed fixture trees and patches are LF, so a CRLF
# test fixture is not a harsher case — it is a different one, and it hid the
# line-ending defect these tests now cover.
@pytest.fixture
def source_tree(tmp_path: Path) -> Path:
    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "auth.py").write_bytes(ORIGINAL.encode("utf-8"))
    (tree / "src" / "util.py").write_bytes(b"LIMIT = 100\n")
    return tree


def write_patch(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_bytes(body.encode("utf-8"))
    return path


def test_materialise_copies_without_touching_the_source(source_tree: Path, tmp_path: Path) -> None:
    """Fixture trees are never mutated in place; a run works on a copy."""
    before = tree_checksum(source_tree)
    work = materialise(source_tree, tmp_path / "work")
    (work / "src" / "auth.py").write_text("changed\n", encoding="utf-8")
    assert tree_checksum(source_tree) == before


def test_materialise_reproduces_the_checksum(source_tree: Path, tmp_path: Path) -> None:
    work = materialise(source_tree, tmp_path / "work")
    assert tree_checksum(work) == tree_checksum(source_tree)


def test_materialise_leaves_build_output_and_caches_behind(
    source_tree: Path, tmp_path: Path
) -> None:
    """Running a fixture's own suite once must not change what a gate measures.

    `__pycache__` and `.pytest_cache` appear the first time anyone runs the
    fixture's tests, and `node_modules`/`dist` the first time anyone builds it.
    None are committed. If they were copied, G3's checksum would stop matching
    the manifest and G2 would witness against a tree that is not the fixture.
    """
    (source_tree / "src" / "__pycache__").mkdir()
    (source_tree / "src" / "__pycache__" / "auth.cpython-312.pyc").write_bytes(b"\x00cached")
    (source_tree / ".pytest_cache").mkdir()
    (source_tree / ".pytest_cache" / "CACHEDIR.TAG").write_bytes(b"Signature: 8a477f5\n")
    (source_tree / "node_modules").mkdir()
    (source_tree / "node_modules" / "left-pad.js").write_bytes(b"module.exports = 1\n")

    work = materialise(source_tree, tmp_path / "work")

    assert not (work / "src" / "__pycache__").exists()
    assert not (work / ".pytest_cache").exists()
    assert not (work / "node_modules").exists()
    assert (work / "src" / "auth.py").read_bytes() == ORIGINAL.encode("utf-8")


def test_check_succeeds_on_a_well_formed_patch(source_tree: Path, tmp_path: Path) -> None:
    check_patch(source_tree, write_patch(tmp_path, "ok.patch", PATCH))


def test_check_fails_when_the_context_has_drifted(source_tree: Path, tmp_path: Path) -> None:
    (source_tree / "src" / "auth.py").write_text(
        "def verify(a, b):\n    return 1\n", encoding="utf-8"
    )
    with pytest.raises(PatchError):
        check_patch(source_tree, write_patch(tmp_path, "drift.patch", PATCH))


def test_apply_produces_the_mutation(source_tree: Path, tmp_path: Path) -> None:
    apply_patch(source_tree, write_patch(tmp_path, "ok.patch", PATCH))
    assert (source_tree / "src" / "auth.py").read_text(encoding="utf-8") == MUTATED


def test_apply_then_revert_restores_the_exact_checksum(source_tree: Path, tmp_path: Path) -> None:
    """Gate G2 re-runs the clean contract on the reverted tree."""
    patch = write_patch(tmp_path, "ok.patch", PATCH)
    before = tree_checksum(source_tree)
    apply_patch(source_tree, patch)
    assert tree_checksum(source_tree) != before
    revert_patch(source_tree, patch)
    assert tree_checksum(source_tree) == before


def test_apply_and_revert_never_rewrite_line_endings(tmp_path: Path) -> None:
    """The committed trees are LF, and the host's Git config must not change that.

    With `core.autocrlf=true` — the Windows default — `git apply` rewrites the
    whole target file in the platform's line endings rather than only the hunk.
    Apply-then-revert then returns a file differing from the original in every
    line, the tree checksum moves, and G2 can never pass. Nothing reports it:
    the patch applies, the revert succeeds, and only comparing bytes shows it.

    Written as bytes rather than through `write_text`, because `write_text` on
    Windows produces CRLF and would hide exactly the case this covers.
    """
    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    target = tree / "src" / "auth.py"
    target.write_bytes(ORIGINAL.encode("utf-8"))
    (tree / "src" / "util.py").write_bytes(b"LIMIT = 100\n")

    patch = tmp_path / "lf.patch"
    patch.write_bytes(PATCH.encode("utf-8"))
    original_bytes = target.read_bytes()
    assert b"\r\n" not in original_bytes

    apply_patch(tree, patch)
    assert b"\r\n" not in target.read_bytes(), "apply rewrote LF as CRLF"

    revert_patch(tree, patch)
    assert target.read_bytes() == original_bytes


def test_applying_twice_fails_rather_than_double_applying(
    source_tree: Path, tmp_path: Path
) -> None:
    patch = write_patch(tmp_path, "ok.patch", PATCH)
    apply_patch(source_tree, patch)
    with pytest.raises(PatchError):
        apply_patch(source_tree, patch)


def test_every_patch_applies_to_the_same_clean_base(source_tree: Path, tmp_path: Path) -> None:
    """Bugs must be independent: each mutation starts from the identical tree."""
    base = tree_checksum(source_tree)
    for name, body in (("one.patch", PATCH), ("two.patch", SECOND_PATCH)):
        work = materialise(source_tree, tmp_path / f"work-{name}")
        assert tree_checksum(work) == base
        apply_patch(work, write_patch(tmp_path, name, body))
        assert tree_checksum(work) != base


def test_patch_escaping_the_tree_is_rejected_before_git_runs(
    source_tree: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    with pytest.raises(PatchError, match="outside"):
        apply_patch(source_tree, write_patch(tmp_path, "evil.patch", ESCAPING_PATCH))
    assert not outside.exists()


def test_missing_patch_file_raises(source_tree: Path, tmp_path: Path) -> None:
    with pytest.raises(PatchError):
        apply_patch(source_tree, tmp_path / "absent.patch")


def test_error_message_names_the_patch(source_tree: Path, tmp_path: Path) -> None:
    (source_tree / "src" / "auth.py").write_text("different\n", encoding="utf-8")
    patch = write_patch(tmp_path, "named.patch", PATCH)
    with pytest.raises(PatchError) as exc:
        apply_patch(source_tree, patch)
    assert "named.patch" in str(exc.value)
