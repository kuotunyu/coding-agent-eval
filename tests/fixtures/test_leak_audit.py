"""Gate G4 — the measured tree must not contain the answers.

An agent that can read the bug manifest, the patch, or a witness file named
after the defect is not being measured on detection. This is the cheapest way
for a benchmark to become meaningless, and it is invisible in the results: the
numbers look excellent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent_eval.fixtures.leak_audit import audit_measured_tree

CLAIM = "Session token comparison is not constant time and leaks the prefix."
ROOT_CAUSE = "The equality operator short-circuits on the first differing byte."


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_text(
        "def verify(a: str, b: str) -> bool:\n    return a == b\n", encoding="utf-8"
    )
    (root / "README.md").write_text("# demo service\n", encoding="utf-8")
    return root


def audit(tree: Path) -> list[str]:
    return [
        f.rule
        for f in audit_measured_tree(tree, bug_ids=["fx-demo-py/B-001"], claims=[CLAIM, ROOT_CAUSE])
    ]


def test_clean_tree_passes(tree: Path) -> None:
    assert audit(tree) == []


def test_bug_manifest_directory_is_flagged(tree: Path) -> None:
    (tree / "bugs").mkdir()
    (tree / "bugs" / "B-001.yaml").write_text("bug_id: x\n", encoding="utf-8")
    assert "FORBIDDEN_PATH" in audit(tree)


def test_patch_file_is_flagged(tree: Path) -> None:
    (tree / "fix.patch").write_text("--- a\n+++ b\n", encoding="utf-8")
    assert "FORBIDDEN_PATH" in audit(tree)


def test_witness_directory_is_flagged(tree: Path) -> None:
    (tree / "witness").mkdir()
    (tree / "witness" / "t.py").write_text("assert True\n", encoding="utf-8")
    assert "FORBIDDEN_PATH" in audit(tree)


def test_patch_directory_is_flagged_even_without_a_patch_in_it(tree: Path) -> None:
    """The directory is the leak, not only the files with the telling suffix.

    A `patches/` holding a README says which files a bug touches, and a copy
    that failed part way through leaves one holding nothing at all. Neither is
    caught by the suffix rule.
    """
    (tree / "patches").mkdir()
    (tree / "patches" / "README.md").write_text("one patch per bug\n", encoding="utf-8")
    assert "FORBIDDEN_PATH" in audit(tree)


def test_environment_recipe_directory_is_flagged(tree: Path) -> None:
    (tree / "env").mkdir()
    (tree / "env" / "Dockerfile").write_text("FROM python\n", encoding="utf-8")
    assert "FORBIDDEN_PATH" in audit(tree)


def test_defect_inventory_is_flagged(tree: Path) -> None:
    (tree / "defects.md").write_text("# audited defects\n", encoding="utf-8")
    assert "FORBIDDEN_PATH" in audit(tree)


def test_residual_defects_file_is_flagged(tree: Path) -> None:
    (tree / "known_residual_defects.yaml").write_text("defects: []\n", encoding="utf-8")
    assert "FORBIDDEN_PATH" in audit(tree)


def test_bug_id_appearing_in_content_is_flagged(tree: Path) -> None:
    (tree / "src" / "auth.py").write_text("# see fx-demo-py/B-001\n", encoding="utf-8")
    assert "BUG_ID_IN_CONTENT" in audit(tree)


def test_canonical_claim_five_gram_is_flagged(tree: Path) -> None:
    """Paraphrase is fine; lifting a five-word run from the claim is not."""
    (tree / "README.md").write_text(
        "Note: session token comparison is not constant time here.\n", encoding="utf-8"
    )
    assert "CLAIM_NGRAM_IN_CONTENT" in audit(tree)


def test_short_incidental_overlap_is_not_flagged(tree: Path) -> None:
    """A four-word overlap is ordinary English and must not trip the gate."""
    (tree / "README.md").write_text(
        "The session token comparison happens here.\n", encoding="utf-8"
    )
    assert audit(tree) == []


def test_ngram_match_ignores_case_and_punctuation(tree: Path) -> None:
    (tree / "README.md").write_text(
        "SESSION TOKEN COMPARISON, IS NOT CONSTANT time!\n", encoding="utf-8"
    )
    assert "CLAIM_NGRAM_IN_CONTENT" in audit(tree)


def test_binary_files_are_skipped(tree: Path) -> None:
    (tree / "blob.bin").write_bytes(b"\x00fx-demo-py/B-001\x00")
    assert audit(tree) == []


def test_findings_report_the_offending_path(tree: Path) -> None:
    (tree / "src" / "auth.py").write_text("# fx-demo-py/B-001\n", encoding="utf-8")
    findings = audit_measured_tree(tree, bug_ids=["fx-demo-py/B-001"], claims=[CLAIM])
    assert findings[0].path == "src/auth.py"
    assert "src/auth.py" in findings[0].render()
