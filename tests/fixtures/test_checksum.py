"""Tree checksum (design spec §6.7).

The checksum is taken over committed bytes with no normalisation. That choice
has a consequence people find surprising and which is asserted here explicitly:
converting LF to CRLF *must* change the checksum, because it changes the bytes.

Cross-platform stability is not the checksum's job. It comes from checkout
policy — `fixtures/** -text` in .gitattributes — so the same commit produces the
same bytes on Linux and Windows. Making the checksum normalise instead would
mean two genuinely different trees could share a checksum, which is worse.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coding_agent_eval.fixtures.checksum import tree_checksum
from coding_agent_eval.hygiene.policy import OFFICIAL_PUBLIC_EMAIL


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "app.py").write_bytes(b"def main():\n    return 1\n")
    (root / "README.md").write_bytes(b"# demo\n")
    return root


def test_checksum_is_prefixed_sha256(tree: Path) -> None:
    value = tree_checksum(tree)
    assert value.startswith("sha256:")
    assert len(value) == len("sha256:") + 64


def test_checksum_is_stable_across_repeated_runs(tree: Path) -> None:
    assert tree_checksum(tree) == tree_checksum(tree)


def test_checksum_is_independent_of_filesystem_iteration_order(tree: Path, tmp_path: Path) -> None:
    """Paths are sorted, so creation order must not matter."""
    other = tmp_path / "other"
    (other / "src").mkdir(parents=True)
    (other / "README.md").write_bytes(b"# demo\n")
    (other / "src" / "app.py").write_bytes(b"def main():\n    return 1\n")
    assert tree_checksum(other) == tree_checksum(tree)


def test_changing_a_byte_changes_the_checksum(tree: Path) -> None:
    before = tree_checksum(tree)
    (tree / "src" / "app.py").write_bytes(b"def main():\n    return 2\n")
    assert tree_checksum(tree) != before


def test_adding_a_file_changes_the_checksum(tree: Path) -> None:
    before = tree_checksum(tree)
    (tree / "src" / "extra.py").write_bytes(b"x = 1\n")
    assert tree_checksum(tree) != before


def test_deleting_a_file_changes_the_checksum(tree: Path) -> None:
    before = tree_checksum(tree)
    (tree / "README.md").unlink()
    assert tree_checksum(tree) != before


def test_lf_to_crlf_changes_the_checksum(tree: Path) -> None:
    """The checksum measures bytes. A line-ending change is a byte change."""
    before = tree_checksum(tree)
    target = tree / "src" / "app.py"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    assert tree_checksum(tree) != before


def test_empty_file_and_missing_file_are_distinguished(tree: Path) -> None:
    with_empty = tree / "src" / "empty.py"
    with_empty.write_bytes(b"")
    with_empty_checksum = tree_checksum(tree)
    with_empty.unlink()
    assert tree_checksum(tree) != with_empty_checksum


def test_renaming_a_file_changes_the_checksum(tree: Path) -> None:
    """Content alone is not the identity; the path is part of it."""
    before = tree_checksum(tree)
    (tree / "src" / "app.py").rename(tree / "src" / "main.py")
    assert tree_checksum(tree) != before


@pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is required",
)
def test_two_checkouts_of_the_same_commit_agree(tmp_path: Path) -> None:
    """Stability under the controlled checkout policy, which is where it comes from."""
    repo = tmp_path / "repo"
    fixture_tree = repo / "fixtures" / "fx-demo" / "tree"
    fixture_tree.mkdir(parents=True)
    (fixture_tree / "a.py").write_bytes(b"x = 1\n")
    (fixture_tree / "b.md").write_bytes(b"# b\n")
    (repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\nfixtures/** -text\n")

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.name", "t")
    # The project's own identity: the one address the tracked-file policy allows,
    # so a throwaway repository in a test does not trip gate G11.
    git("config", "user.email", OFFICIAL_PUBLIC_EMAIL)
    git("add", "-A")
    git("commit", "-q", "-m", "fixture")

    checksums = []
    for index in (1, 2):
        export = tmp_path / f"export{index}"
        export.mkdir()
        archive = subprocess.run(
            ["git", "-C", str(repo), "archive", "HEAD"], check=True, capture_output=True
        )
        subprocess.run(
            ["tar", "-x", "-C", str(export)], input=archive.stdout, check=True, capture_output=True
        )
        checksums.append(tree_checksum(export / "fixtures" / "fx-demo" / "tree"))

    assert checksums[0] == checksums[1]


def test_missing_tree_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        tree_checksum(tmp_path / "nope")
