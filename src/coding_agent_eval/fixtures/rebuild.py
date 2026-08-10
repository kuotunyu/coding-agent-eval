"""Gate G3 — fixture rebuild determinism (design spec §6.7, §6.10, §13.3).

Two numbers in a fixture manifest are load-bearing, and neither is checked by
anything else:

* `clean_control.tree_checksum` is the fixture's **identity**. Every result
  records it, and the evaluator refuses to score a run whose tree checksum does
  not match. If the manifest's value drifted away from the tree, that refusal
  would fire on correct runs and never on the incorrect one.
* `scope.in_scope_loc` is the **denominator** of
  `benchmark_unsupported_findings_per_kloc`. A denominator that drifts changes a
  headline metric silently, in a direction nobody chose, and no test elsewhere
  would notice — the number would simply become slightly wrong.

Both were previously verified by reading them. Reading them is not a gate.

**The subject is the committed tree, not the working directory.** The manifest
says so, and it matters: a working copy holds `node_modules`, `__pycache__`, and
whatever the last local test run produced, none of which any run will ever see.
So the tree is rebuilt from git — `git archive HEAD:<path>` — and the checksum
and line count are taken from that.

Rebuilding from the commit leaves a second question the export cannot answer:
whether the bytes *on disk* are those bytes. Every other gate reads the working
tree, so if a checkout rewrote line endings — the exact failure `.gitattributes`
`fixtures/** -text` exists to prevent — G2 and G9 would be measuring a tree the
manifest does not describe, and this gate would still pass by only ever looking
at the commit. It therefore compares both, and names the differing files.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from coding_agent_eval.fixtures.checksum import file_digests, tree_checksum
from coding_agent_eval.fixtures.loc import LOC_TOOL, count_loc
from coding_agent_eval.fixtures.patcher import GIT_NO_EOL_REWRITE, PatchError, materialise
from coding_agent_eval.fixtures.report import Check

_GIT_TIMEOUT = 120

#: Differing paths printed before the list is truncated.
#:
#: A line-ending change marks *every* file as differing, and a failure report
#: that scrolls a terminal off the top is one nobody reads to the end of.
_MAX_DIFF_LINES = 12


class RebuildError(RuntimeError):
    """The gate could not run at all.

    Kept distinct from the gate running and failing. "No git" and "the checksum
    moved" call for entirely different responses, and collapsing them would let
    a broken environment read as a broken fixture.
    """


@dataclass(frozen=True)
class RebuildReport:
    """Every G3 check for one fixture."""

    fixture_id: str
    fixture_version: str
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def render(self) -> str:
        head = f"{self.fixture_id} {self.fixture_version}: {'PASS' if self.ok else 'FAIL'}"
        return "\n".join([head, *(check.render() for check in self.checks)])


@dataclass(frozen=True)
class ExportedTree:
    """A fixture tree reconstructed from the commit.

    `modes` comes from the archive entries rather than from the extracted files,
    so it is the mode git recorded on every platform — including one that cannot
    represent an executable bit at all.
    """

    root: Path
    modes: dict[str, int]


def _git(args: list[str], *, cwd: Path) -> bytes:
    """Run git and return stdout, turning every failure into `RebuildError`.

    Line-ending conversion is pinned off. `git archive` honours `core.autocrlf`,
    so without this the exported bytes — and therefore the fixture's identity —
    would depend on the operator's git configuration. Writing this gate found
    that the manifests' checksums only reproduced because this repository
    happens to carry `core.autocrlf=false` locally; a clone that did not would
    have failed G3 and reported a defect that was not there.
    """
    try:
        proc = subprocess.run(
            ["git", *GIT_NO_EOL_REWRITE, *args], cwd=cwd, capture_output=True, timeout=_GIT_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RebuildError(f"could not run git {' '.join(args)}: {exc}") from exc
    if proc.returncode != 0:
        detail = proc.stderr.decode("utf-8", "replace").strip()
        raise RebuildError(f"git {' '.join(args)} failed: {detail or proc.returncode}")
    return proc.stdout


@dataclass(frozen=True)
class RepositoryLocation:
    """Where a fixture sits in its repository."""

    root: Path
    #: POSIX, with a trailing slash; empty when the fixture *is* the repository root.
    prefix: str


def locate(fixture_dir: Path) -> RepositoryLocation:
    """Find the repository root and the fixture's path within it.

    `--show-prefix` rather than `--show-toplevel` because its answer is
    repository-relative and therefore ASCII whatever the checkout is called. The
    root is then reached by walking up that many components, so git's idea of an
    absolute path — which this checkout, living under a non-ASCII directory,
    would make us decode — never has to be parsed at all.
    """
    if not fixture_dir.is_dir():
        raise RebuildError(f"no such fixture directory: {fixture_dir}")

    prefix = _git(["rev-parse", "--show-prefix"], cwd=fixture_dir).decode("utf-8").strip()
    root = fixture_dir.resolve()
    for _ in [part for part in prefix.split("/") if part]:
        root = root.parent
    return RepositoryLocation(root=root, prefix=prefix)


def export_committed_tree(fixture_dir: Path, destination: Path) -> ExportedTree:
    """Reconstruct `<fixture>/tree` from HEAD into `destination`.

    The subtree form (`HEAD:<prefix>tree`) archives paths relative to the tree
    itself, so the export is directly comparable with the working copy rather
    than nested under the repository layout.

    **Run from the repository root, never from the fixture.** From a
    subdirectory `git archive` limits its output to that directory's prefix,
    which combined with a subtree tree-ish matches nothing — and it reports that
    by exiting 0 with a well-formed archive containing no files. Writing this
    gate found exactly that, so the empty case below is a real failure mode
    rather than a defensive flourish.
    """
    location = locate(fixture_dir)
    tree_ish = f"HEAD:{location.prefix}tree"
    archive = _git(["archive", tree_ish], cwd=location.root)

    destination.mkdir(parents=True, exist_ok=True)
    modes: dict[str, int] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive)) as handle:
            members = handle.getmembers()
            modes = {m.name: m.mode for m in members if m.isfile()}
            # `filter="data"` refuses absolute paths, `..` traversal, links out
            # of the tree, and device nodes. git would not produce them, but the
            # archive is bytes being unpacked into a directory, and unpacking it
            # trustingly because of where it came from is how that goes wrong.
            handle.extractall(destination, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise RebuildError(f"could not unpack the export of {tree_ish}: {exc}") from exc

    if not modes:
        raise RebuildError(
            f"{tree_ish} exported no files; the fixture tree is not committed at HEAD"
        )
    return ExportedTree(root=destination, modes=modes)


def _digest_diff(committed: Path, measured: Path) -> tuple[str, ...]:
    """Name the files that differ between the commit and the working copy."""
    left = file_digests(committed)
    right = file_digests(measured)

    lines = [f"only in the commit: {path}" for path in sorted(set(left) - set(right))]
    lines += [f"only on disk: {path}" for path in sorted(set(right) - set(left))]
    lines += [
        f"differs: {path}" for path in sorted(set(left) & set(right)) if left[path] != right[path]
    ]
    if len(lines) > _MAX_DIFF_LINES:
        hidden = len(lines) - _MAX_DIFF_LINES
        return (*lines[:_MAX_DIFF_LINES], f"... and {hidden} more")
    return tuple(lines)


def _checksum_check(export: ExportedTree, manifest: dict[str, Any]) -> Check:
    declared = str(manifest["clean_control"]["tree_checksum"])
    rebuilt = tree_checksum(export.root)
    return Check(
        name="tree_checksum",
        ok=rebuilt == declared,
        expected=declared,
        actual=rebuilt,
        detail=(
            ()
            if rebuilt == declared
            else (
                "the manifest names a tree that is not the one committed at HEAD",
                "if the tree changed on purpose: commit it, re-run this gate, and put "
                "the rebuilt value in the manifest with a fixture version bump",
            )
        ),
    )


def _measured_tree_check(fixture_dir: Path, export: ExportedTree, destination: Path) -> Check:
    """Compare what the other gates actually read against what was committed.

    The comparison goes through `materialise`, which is the same function G2 and
    the end-to-end runner use to obtain a tree, so this checks the bytes those
    gates measure rather than a separately-defined idea of the working copy.
    """
    try:
        measured = materialise(fixture_dir / "tree", destination)
    except (PatchError, OSError) as exc:
        raise RebuildError(f"could not copy the working tree of {fixture_dir.name}: {exc}") from exc

    committed_checksum = tree_checksum(export.root)
    measured_checksum = tree_checksum(measured)
    ok = measured_checksum == committed_checksum
    return Check(
        name="working_tree_matches_head",
        ok=ok,
        expected=committed_checksum,
        actual=measured_checksum,
        detail=() if ok else _mismatch_detail(export.root, measured),
    )


def _mismatch_detail(committed: Path, measured: Path) -> tuple[str, ...]:
    """Explain a working-tree mismatch by what actually differs.

    An empty file diff with a differing checksum is not a contradiction: the
    checksum also covers the executable bit, so identical content can still hash
    differently. Reporting "commit these changes" with nothing listed sends
    someone hunting for content drift that is not there — which is exactly what
    happened when this gate was run from WSL against a `/mnt/c` checkout, where
    DrvFs reports every Windows file as mode 777.
    """
    differences = _digest_diff(committed, measured)
    if differences:
        return (
            *differences,
            "the committed tree is the fixture, so until these are committed no gate "
            "result describes anything with a recorded identity",
        )

    return (
        "every file's content matches; the difference is in the executable bit, "
        "which the checksum covers",
        "this is what a filesystem view that fabricates file modes looks like — "
        "notably WSL's /mnt/c, where every Windows file reads as mode 777",
        "run the gates on a native checkout (Git Bash or a Linux clone), not "
        "through a mount that rewrites modes",
    )


def _portable_modes_check(export: ExportedTree) -> Check:
    """No committed file may carry an executable bit.

    The checksum folds the executable bit into a tree's identity, and Windows
    cannot represent one — a file committed `100755` hashes as `100755` on Linux
    and `100644` here, so the fixture would have two identities depending on who
    measured it. No fixture file carries one today. This turns that from a fact
    someone happens to know into one that stops being true loudly.
    """
    executable = sorted(path for path, mode in export.modes.items() if mode & 0o111)
    return Check(
        name="portable_file_modes",
        ok=not executable,
        expected="no committed file carries an executable bit",
        actual=f"{len(executable)} executable file(s)",
        detail=tuple(f"executable: {path}" for path in executable[:_MAX_DIFF_LINES]),
    )


def _loc_checks(export: ExportedTree, manifest: dict[str, Any]) -> list[Check]:
    scope = manifest["scope"]
    declared_tool = str(scope["loc_tool"])
    tool_matches = declared_tool == LOC_TOOL

    counted = count_loc(
        export.root,
        list(scope["in_scope_paths"]),
        list(scope["out_of_scope_paths"]),
    )
    declared_loc = int(scope["in_scope_loc"])
    return [
        Check(
            name="loc_tool",
            ok=tool_matches,
            expected=LOC_TOOL,
            actual=declared_tool,
            detail=(
                ()
                if tool_matches
                else (
                    "the manifest's line count was produced by rules that are no longer "
                    "the ones in force, so the count below proves nothing either way",
                )
            ),
        ),
        Check(
            name="in_scope_loc",
            ok=counted == declared_loc,
            expected=str(declared_loc),
            actual=str(counted),
            detail=(
                ()
                if counted == declared_loc
                else (
                    "this is the denominator of benchmark_unsupported_findings_per_kloc; "
                    "a run scored against the manifest would divide by the wrong number",
                )
            ),
        ),
    ]


def load_manifest(fixture_dir: Path) -> dict[str, Any]:
    path = fixture_dir / "fixture.yaml"
    if not path.is_file():
        raise RebuildError(f"no fixture manifest at {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RebuildError(f"could not read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise RebuildError(f"{path} is not a mapping")
    return document


def run_g3(fixture_dir: Path) -> RebuildReport:
    """Run every G3 check for one fixture.

    Raises `RebuildError` when the gate cannot run. Returns a report whose `ok`
    is false when it ran and something did not hold.
    """
    manifest = load_manifest(fixture_dir)

    with tempfile.TemporaryDirectory(prefix="cae-g3-") as scratch:
        work = Path(scratch)
        export = export_committed_tree(fixture_dir, work / "committed")
        checks = [
            _portable_modes_check(export),
            _checksum_check(export, manifest),
            _measured_tree_check(fixture_dir, export, work / "measured"),
            *_loc_checks(export, manifest),
        ]

    return RebuildReport(
        fixture_id=str(manifest["fixture_id"]),
        fixture_version=str(manifest["fixture_version"]),
        checks=tuple(checks),
    )
