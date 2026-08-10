"""Gate G11 — the tracked-file leak scanner.

It scans what Git tracks rather than the working tree, because tracked content is
what would actually be published.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coding_agent_eval.cli import main
from coding_agent_eval.hygiene.leak_scan import scan_paths, scan_tracked_files
from coding_agent_eval.hygiene.policy import OFFICIAL_PUBLIC_EMAIL

from .corpus import (
    SAMPLE_OTHER_EMAIL,
    SAMPLE_POSIX_PATH,
    SAMPLE_WINDOWS_PATH,
)


#: A git-archive export has no .git directory. "No repository to scan" is not the
#: same as "clean", so the self-scan tests skip rather than passing vacuously.
def _is_git_repo(path: Path) -> bool:
    return (path / ".git").exists()


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.name", "test")
    _git(tmp_path, "config", "user.email", OFFICIAL_PUBLIC_EMAIL)
    return tmp_path


def test_clean_tracked_tree_passes(tiny_repo: Path) -> None:
    (tiny_repo / "ok.md").write_text("relative path src/app.py\n", encoding="utf-8")
    _git(tiny_repo, "add", "ok.md")
    assert scan_tracked_files(tiny_repo) == []


def test_tracked_leak_is_reported_with_path(tiny_repo: Path) -> None:
    (tiny_repo / "bad.md").write_text(f"see {SAMPLE_WINDOWS_PATH}", encoding="utf-8")
    _git(tiny_repo, "add", "bad.md")
    findings = scan_tracked_files(tiny_repo)
    assert [f.path for f in findings] == ["bad.md"]
    assert findings[0].rule == "absolute_path"


def test_a_new_untracked_file_is_scanned(tiny_repo: Path) -> None:
    """A file `git add -A` would commit is a file about to be published.

    This test previously asserted the opposite, on the reasoning that untracked
    scratch files would be noise. That reasoning was wrong in the one case that
    matters: a **new** file is where a pasted credential sits, and the gate could
    not see it until the commit adding it had been made. It happened — a new test
    file with a key-shaped constant passed this scan while untracked and failed
    immediately after `git add -A`.
    """
    (tiny_repo / "scratch.md").write_text(SAMPLE_WINDOWS_PATH, encoding="utf-8")
    assert [f.path for f in scan_tracked_files(tiny_repo)] == ["scratch.md"]


def test_an_ignored_file_is_still_skipped(tiny_repo: Path) -> None:
    """`.gitignore` stays in force, so a real `.env` is never read.

    This is the line between "noise" and "about to ship": an ignored file cannot
    be committed, so it is not this gate's business. Without it, widening the
    scan would have made the gate read every operator's actual secrets.
    """
    (tiny_repo / ".gitignore").write_text(".env\n", encoding="utf-8")
    (tiny_repo / ".env").write_text(f"SECRET={SAMPLE_WINDOWS_PATH}\n", encoding="utf-8")
    _git(tiny_repo, "add", ".gitignore")

    assert [f.path for f in scan_tracked_files(tiny_repo)] == []


def test_official_email_allowed_in_tracked_files(tiny_repo: Path) -> None:
    (tiny_repo / "AUTHORS.md").write_text(f"kuotunyu <{OFFICIAL_PUBLIC_EMAIL}>\n", encoding="utf-8")
    _git(tiny_repo, "add", "AUTHORS.md")
    assert scan_tracked_files(tiny_repo) == []


def test_other_email_rejected_in_tracked_files(tiny_repo: Path) -> None:
    (tiny_repo / "AUTHORS.md").write_text(f"{SAMPLE_OTHER_EMAIL}\n", encoding="utf-8")
    _git(tiny_repo, "add", "AUTHORS.md")
    assert [f.rule for f in scan_tracked_files(tiny_repo)] == ["email"]


def test_binary_tracked_file_is_skipped(tiny_repo: Path) -> None:
    (tiny_repo / "blob.bin").write_bytes(b"\x00\x01\x02\xff" * 32)
    _git(tiny_repo, "add", "blob.bin")
    assert scan_tracked_files(tiny_repo) == []


def test_corpus_path_is_excluded_but_the_same_content_elsewhere_is_not(tiny_repo: Path) -> None:
    """The exclusion is a path, not a licence for the content it holds."""
    excluded = tiny_repo / "tests" / "hygiene"
    excluded.mkdir(parents=True)
    (excluded / "corpus.py").write_text(SAMPLE_POSIX_PATH, encoding="utf-8")
    (excluded / "other.py").write_text(SAMPLE_POSIX_PATH, encoding="utf-8")
    _git(tiny_repo, "add", "-A")

    findings = scan_tracked_files(tiny_repo)
    assert [f.path for f in findings] == ["tests/hygiene/other.py"]


def test_scan_paths_accepts_explicit_files(tmp_path: Path) -> None:
    good, bad = tmp_path / "a.md", tmp_path / "b.md"
    good.write_text("nothing here\n", encoding="utf-8")
    bad.write_text(f"{SAMPLE_POSIX_PATH}\n", encoding="utf-8")
    assert [f.path for f in scan_paths([good, bad], root=tmp_path)] == ["b.md"]


def test_this_repository_is_clean(repo_root: Path) -> None:
    """The scanner is pointed at its own repository: G11 in miniature."""
    if not _is_git_repo(repo_root):
        pytest.skip("not a git checkout (clean export); nothing to enumerate")
    findings = scan_tracked_files(repo_root)
    assert findings == [], "\n".join(f.render() for f in findings)


def test_cli_leak_scan_reports_clean_for_this_repo(repo_root: Path) -> None:
    if not _is_git_repo(repo_root):
        pytest.skip("not a git checkout (clean export); nothing to enumerate")
    assert main(["hygiene", "leak-scan", "--tracked", "--root", str(repo_root)]) == 0


def test_cli_leak_scan_exits_nonzero_on_a_leak(tiny_repo: Path) -> None:
    (tiny_repo / "bad.md").write_text(f"{SAMPLE_POSIX_PATH}\n", encoding="utf-8")
    _git(tiny_repo, "add", "bad.md")
    assert main(["hygiene", "leak-scan", "--tracked", "--root", str(tiny_repo)]) == 1


def test_cli_leak_scan_requires_tracked_flag(repo_root: Path) -> None:
    assert main(["hygiene", "leak-scan", "--root", str(repo_root)]) == 2


def test_scan_of_a_non_repository_raises_rather_than_reporting_clean(tmp_path: Path) -> None:
    """An unreadable scan must never be mistaken for a passing one."""
    from coding_agent_eval.hygiene.leak_scan import LeakScanError

    with pytest.raises(LeakScanError):
        scan_tracked_files(tmp_path)
