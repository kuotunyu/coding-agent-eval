"""Gate G3 — fixture rebuild determinism (design spec §6.7, §6.10, §13.3).

The gate protects a fixture's identity and a headline metric's denominator, and
until now both were verified by someone reading them. So the tests that matter
here are not the ones showing it passes on the real fixtures — that is one test.
They are the ones showing **each check fails when the thing it checks is
broken**, because a gate that has never been observed to fail is indistinguishable
from a gate that cannot.

Each drift is therefore introduced deliberately into a throwaway repository —
a manifest checksum edited, a line count nudged by one, a file changed on disk
but not committed, a checkout converted to CRLF — and the specific check that
should notice is asserted by name.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from coding_agent_eval.fixtures.checksum import tree_checksum
from coding_agent_eval.fixtures.loc import LOC_TOOL, count_loc
from coding_agent_eval.fixtures.rebuild import (
    RebuildError,
    RebuildReport,
    _mismatch_detail,
    export_committed_tree,
    locate,
    run_g3,
)
from coding_agent_eval.hygiene.policy import OFFICIAL_PUBLIC_EMAIL
from tests.conftest import REPO_ROOT, requires_checkout

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,
    reason="git is required to rebuild a tree from its commit",
)

#: `src/app.py` has one whole-line comment and two content lines; nothing else
#: is in scope. Written out so the expected count is arithmetic, not an echo.
DEMO_LOC = 2


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def commit(repo: Path, message: str) -> None:
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", message)


def write_manifest(fixture_dir: Path, *, checksum: str, loc: int, loc_tool: str = LOC_TOOL) -> None:
    """Write the minimal manifest the gate reads. Not schema-validated here."""
    document = {
        "fixture_id": "fx-demo",
        "fixture_version": "1.0.0",
        "scope": {
            "in_scope_paths": ["src/**"],
            "out_of_scope_paths": ["tests/**"],
            "in_scope_loc": loc,
            "loc_tool": loc_tool,
        },
        "clean_control": {"tree_checksum": checksum},
    }
    (fixture_dir / "fixture.yaml").write_text(
        yaml.safe_dump(document, sort_keys=True), encoding="utf-8", newline="\n"
    )


def edit_manifest(fixture_dir: Path, section: str, key: str, value: object) -> None:
    path = fixture_dir / "fixture.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document[section][key] = value
    path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8", newline="\n")


@pytest.fixture
def demo(tmp_path: Path) -> Path:
    """A committed one-fixture repository whose manifest describes its own tree.

    The manifest's values are taken from the **export**, not from the working
    copy, so the fixture is built the way a real one is and the test cannot
    accidentally assume the property it is about to check.
    """
    repo = tmp_path / "repo"
    fixture_dir = repo / "fixtures" / "fx-demo"
    tree = fixture_dir / "tree"
    (tree / "src").mkdir(parents=True)
    (tree / "tests").mkdir(parents=True)
    (tree / "src" / "app.py").write_bytes(b"# a comment\ndef main():\n    return 1\n")
    (tree / "tests" / "test_app.py").write_bytes(b"def test_main():\n    assert main() == 1\n")
    (tree / "README.md").write_bytes(b"# demo\n")
    (repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\nfixtures/** -text\n")

    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "t")
    # The one address the tracked-file policy allows, matching the other tests
    # that build a throwaway repository.
    git(repo, "config", "user.email", OFFICIAL_PUBLIC_EMAIL)
    commit(repo, "fixture tree")

    exported = export_committed_tree(fixture_dir, tmp_path / "reference")
    write_manifest(
        fixture_dir,
        checksum=tree_checksum(exported.root),
        loc=count_loc(exported.root, ["src/**"], ["tests/**"]),
    )
    commit(repo, "fixture manifest")
    return fixture_dir


def failed_names(report: RebuildReport) -> set[str]:
    return {check.name for check in report.failures}


def detail_of(report: RebuildReport, name: str) -> str:
    check = next(c for c in report.checks if c.name == name)
    return "\n".join(check.detail)


# ------------------------------------------------------------------ baseline


def test_a_faithful_fixture_passes_every_check(demo: Path) -> None:
    report = run_g3(demo)
    assert report.ok, report.render()
    assert failed_names(report) == set()


@pytest.mark.parametrize("autocrlf", ["true", "false", "input"])
def test_the_rebuild_ignores_the_hosts_line_ending_configuration(demo: Path, autocrlf: str) -> None:
    """A fixture's identity may not depend on how the operator configured git.

    This is the defect writing the gate found. `git archive` honours
    `core.autocrlf`, so under the Windows default the export came back CRLF in
    every line and the rebuilt checksum matched nothing. It looked correct here
    only because this repository carries `core.autocrlf=false` locally — meaning
    a clone that did not would have been told its fixtures had drifted.
    """
    git(demo.parents[1], "config", "core.autocrlf", autocrlf)
    report = run_g3(demo)
    assert report.ok, report.render()


def test_the_demo_fixtures_line_count_is_what_the_rule_produces(demo: Path) -> None:
    """Guards the baseline itself: if this were wrong, every drift test would be."""
    manifest = yaml.safe_load((demo / "fixture.yaml").read_text(encoding="utf-8"))
    assert manifest["scope"]["in_scope_loc"] == DEMO_LOC


# ------------------------------------------------------- checksum drift


def test_a_manifest_checksum_that_drifted_fails(demo: Path) -> None:
    edit_manifest(demo, "clean_control", "tree_checksum", "sha256:" + "0" * 64)
    report = run_g3(demo)
    assert failed_names(report) == {"tree_checksum"}
    assert "not the one committed at HEAD" in detail_of(report, "tree_checksum")


def test_a_committed_change_to_the_tree_fails_until_the_manifest_follows(demo: Path) -> None:
    """The realistic case: the tree was edited on purpose and committed.

    The identity in the manifest is now stale, and nothing else in the suite
    would say so. `working_tree_matches_head` stays green, which is what makes
    this different from an uncommitted edit.
    """
    repo = demo.parents[1]
    (demo / "tree" / "src" / "app.py").write_bytes(b"# a comment\ndef main():\n    return 2\n")
    commit(repo, "deliberate fixture change")

    report = run_g3(demo)
    assert failed_names(report) == {"tree_checksum"}
    assert "fixture version bump" in detail_of(report, "tree_checksum")


# --------------------------------------------------- working copy vs commit


def test_an_uncommitted_edit_to_the_tree_is_named(demo: Path) -> None:
    """Every other gate reads the working copy, so this is what they measured."""
    (demo / "tree" / "src" / "app.py").write_bytes(b"# a comment\ndef main():\n    return 99\n")

    report = run_g3(demo)
    assert failed_names(report) == {"working_tree_matches_head"}
    assert "differs: src/app.py" in detail_of(report, "working_tree_matches_head")


def test_an_uncommitted_new_file_is_named(demo: Path) -> None:
    (demo / "tree" / "src" / "extra.py").write_bytes(b"x = 1\n")

    report = run_g3(demo)
    assert failed_names(report) == {"working_tree_matches_head"}
    assert "only on disk: src/extra.py" in detail_of(report, "working_tree_matches_head")


def test_a_deleted_file_is_named(demo: Path) -> None:
    (demo / "tree" / "README.md").unlink()

    report = run_g3(demo)
    assert failed_names(report) == {"working_tree_matches_head"}
    assert "only in the commit: README.md" in detail_of(report, "working_tree_matches_head")


def test_the_mode_only_explanation_is_produced_whenever_no_file_differs(tmp_path: Path) -> None:
    """The message logic, tested where the platform cannot produce the condition.

    NTFS carries no executable bit, so the integration test below can only skip
    on Windows — and a test that only ever skips is not covering anything on the
    machine this is developed on. This drives `_mismatch_detail` directly with
    two trees whose contents are identical, which is the state that produced the
    misleading report.
    """
    left = tmp_path / "committed"
    right = tmp_path / "measured"
    for root in (left, right):
        (root / "src").mkdir(parents=True)
        (root / "src" / "app.py").write_bytes(b"x = 1\n")

    detail = "\n".join(_mismatch_detail(left, right))
    assert "content matches" in detail
    assert "executable bit" in detail
    assert "/mnt/c" in detail, "the known cause is worth naming"
    assert "until these are committed" not in detail


def test_a_mode_only_difference_says_so_instead_of_listing_nothing(demo: Path) -> None:
    """A mismatch with no differing file is a mode difference, and must say so.

    Found by running the gate from WSL against a `/mnt/c` checkout: DrvFs reports
    every Windows file as mode 777, the checksum covers the executable bit, and
    the gate failed while listing zero differing files — telling the operator to
    commit changes that did not exist. An unhelpful failure message on a real
    failure is its own defect.
    """
    target = demo / "tree" / "src" / "app.py"
    try:
        target.chmod(target.stat().st_mode | 0o111)
    except (OSError, NotImplementedError):  # pragma: no cover - platform dependent
        pytest.skip("this filesystem does not carry an executable bit")
    if not target.stat().st_mode & 0o111:
        pytest.skip("this filesystem does not carry an executable bit")

    report = run_g3(demo)
    assert failed_names(report) == {"working_tree_matches_head"}

    detail = detail_of(report, "working_tree_matches_head")
    assert "content matches" in detail
    assert "executable bit" in detail
    assert "differs:" not in detail, "there is no differing file to name"
    assert "until these are committed" not in detail, "nothing is uncommitted"


def test_a_crlf_working_copy_fails(demo: Path) -> None:
    """The failure `.gitattributes` `fixtures/** -text` exists to prevent.

    If checkout policy stopped holding, the working tree would differ from the
    commit in every line while `git status` still looked clean on some setups.
    The checksum measures bytes, so it sees it.
    """
    target = demo / "tree" / "src" / "app.py"
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))

    report = run_g3(demo)
    assert failed_names(report) == {"working_tree_matches_head"}


def test_the_checksum_check_reads_the_commit_not_the_working_copy(demo: Path) -> None:
    """The two checks must be independent, or one of them is decoration.

    With the working copy dirty and the manifest still describing the commit,
    `tree_checksum` has to stay green — otherwise it is really just measuring
    the working copy, and the whole "rebuild from the commit" claim is empty.
    """
    (demo / "tree" / "src" / "app.py").write_bytes(b"totally different\n")

    report = run_g3(demo)
    assert failed_names(report) == {"working_tree_matches_head"}


def test_build_output_in_the_working_tree_is_not_a_difference(demo: Path) -> None:
    """`materialise` drops ephemera, so a tree that has been run still matches.

    Without this the gate would fail on any machine that had executed the
    fixture's own suite once, which would make it something people switch off.
    """
    cache = demo / "tree" / "src" / "__pycache__"
    cache.mkdir()
    (cache / "app.cpython-312.pyc").write_bytes(b"\x00compiled\x00")

    report = run_g3(demo)
    assert report.ok, report.render()


# -------------------------------------------------------------- LOC drift


def test_a_manifest_line_count_that_drifted_fails(demo: Path) -> None:
    edit_manifest(demo, "scope", "in_scope_loc", DEMO_LOC + 1)
    report = run_g3(demo)
    assert failed_names(report) == {"in_scope_loc"}
    assert "denominator" in detail_of(report, "in_scope_loc")


def test_a_line_added_to_the_tree_moves_the_count(demo: Path) -> None:
    """The drift the gate is really for: the tree grew and the manifest did not."""
    repo = demo.parents[1]
    target = demo / "tree" / "src" / "app.py"
    target.write_bytes(target.read_bytes() + b"\n\nEXTRA = 1\n")
    commit(repo, "one more line")

    report = run_g3(demo)
    assert failed_names(report) == {"tree_checksum", "in_scope_loc"}
    check = next(c for c in report.checks if c.name == "in_scope_loc")
    assert check.actual == str(DEMO_LOC + 1)


def test_an_unrecognised_loc_tool_fails(demo: Path) -> None:
    """A count produced by other rules is not this benchmark's count."""
    edit_manifest(demo, "scope", "loc_tool", "cloc 2.02")
    report = run_g3(demo)
    assert failed_names(report) == {"loc_tool"}
    assert "no longer the ones in force" in detail_of(report, "loc_tool")


def test_a_comment_only_change_moves_the_checksum_but_not_the_count(demo: Path) -> None:
    """Shows the two numbers are measuring different things, not one twice."""
    repo = demo.parents[1]
    (demo / "tree" / "src" / "app.py").write_bytes(
        b"# an entirely different comment\ndef main():\n    return 1\n"
    )
    commit(repo, "reword a comment")

    report = run_g3(demo)
    assert failed_names(report) == {"tree_checksum"}


# ---------------------------------------------------------- portable modes


def test_a_committed_executable_file_fails(demo: Path) -> None:
    """The executable bit is part of the checksum and Windows cannot store it.

    A fixture carrying one would hash differently depending on who measured it,
    so the gate refuses rather than letting a tree hold two identities.
    """
    repo = demo.parents[1]
    git(repo, "update-index", "--chmod=+x", "fixtures/fx-demo/tree/src/app.py")
    commit(repo, "make app.py executable")

    report = run_g3(demo)
    assert "portable_file_modes" in failed_names(report)
    assert "executable: src/app.py" in detail_of(report, "portable_file_modes")


# ------------------------------------------------------------ cannot run


def test_a_tree_that_was_never_committed_cannot_be_rebuilt(tmp_path: Path) -> None:
    """Not a failing check: the gate has no subject, and says so."""
    repo = tmp_path / "repo"
    fixture_dir = repo / "fixtures" / "fx-demo"
    (fixture_dir / "tree" / "src").mkdir(parents=True)
    (fixture_dir / "tree" / "src" / "app.py").write_bytes(b"x = 1\n")
    write_manifest(fixture_dir, checksum="sha256:" + "0" * 64, loc=1)

    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "t")
    git(repo, "config", "user.email", OFFICIAL_PUBLIC_EMAIL)
    (repo / "placeholder.txt").write_bytes(b"committed, but not the fixture\n")
    git(repo, "add", "placeholder.txt")
    git(repo, "commit", "-q", "-m", "unrelated")

    with pytest.raises(RebuildError, match="HEAD:fixtures/fx-demo/tree"):
        run_g3(fixture_dir)


def test_a_directory_outside_a_repository_cannot_be_rebuilt(tmp_path: Path) -> None:
    outside = tmp_path / "loose" / "fx-demo"
    outside.mkdir(parents=True)
    write_manifest(outside, checksum="sha256:" + "0" * 64, loc=1)

    with pytest.raises(RebuildError):
        run_g3(outside)


def test_a_missing_manifest_cannot_be_rebuilt(tmp_path: Path) -> None:
    with pytest.raises(RebuildError, match="no fixture manifest"):
        run_g3(tmp_path)


def test_locate_finds_the_repository_root_without_parsing_an_absolute_path(demo: Path) -> None:
    """The root is reached by walking up the prefix, so a non-ASCII checkout is fine."""
    location = locate(demo)
    assert location.prefix == "fixtures/fx-demo/"
    assert (location.root / ".gitattributes").is_file()
    assert location.root == demo.parents[1].resolve()


# --------------------------------------------------------- the real gate


@requires_checkout
@pytest.mark.parametrize("fixture_id", ["fx-taskq-py", "fx-ledger-ts"])
def test_the_shipped_fixtures_pass_g3(fixture_id: str) -> None:
    """The gate itself. Every other test here proves this one can fail."""
    report = run_g3(REPO_ROOT / "fixtures" / fixture_id)
    assert report.ok, report.render()


@requires_checkout
@pytest.mark.parametrize("fixture_id", ["fx-taskq-py", "fx-ledger-ts"])
def test_the_shipped_fixtures_are_checked_by_every_rule(fixture_id: str) -> None:
    """A pass is only worth something if all five checks actually ran."""
    report = run_g3(REPO_ROOT / "fixtures" / fixture_id)
    assert {check.name for check in report.checks} == {
        "portable_file_modes",
        "tree_checksum",
        "working_tree_matches_head",
        "loc_tool",
        "in_scope_loc",
    }
