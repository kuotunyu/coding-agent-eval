"""The agent's tools, observed running inside the measure container (spec §9.1).

Two claims are made here, and they are different in kind.

**Equivalence.** The container backend and the host backend produce byte-identical
tool output over the real fixture trees. That is what makes isolation free to
choose: if the two disagreed, running measured work in the container would change
the scores, and nobody would be able to say which set was right.

**Containment.** The tools cannot reach the host filesystem, cannot reach the
network, and cannot write to the tree they are judging. Asserted by observation,
not by reading the flags — the flags were right about `/workspace/scratch` for a
whole task before it turned out to be unwritable, which is why gate H2 exists at
all.

Marked `docker`, so the default suite stays runnable without a daemon.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from coding_agent_eval.agent.backend import LocalTree, ToolFailure
from coding_agent_eval.agent.tools import TOOLS_BY_NAME, ToolContext
from coding_agent_eval.sandbox.profiles import MEASURE, ProfileError
from coding_agent_eval.sandbox.run import docker_available, resolve_digest
from coding_agent_eval.sandbox.tool_container import (
    SCRATCH,
    ContainerError,
    ContainerTree,
    is_running,
    pack_tree,
    tool_container,
)
from tests.conftest import REPO_ROOT

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(not docker_available(), reason="no Docker daemon"),
]

FIXTURES = {
    "fx-taskq-py": (
        "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@"
        "sha256:db6a0afabe3acfd9c704e020b27a5b55ccef430b4864d8e565711b0b9cbc8966"
    ),
    "fx-ledger-ts": (
        "ghcr.io/kuotunyu/coding-agent-eval-fx-ledger-ts@"
        "sha256:38450742408270a0e48ae053499dd626f61a4cf09139d40ae494838def4b0312"
    ),
}
FIXTURE_TAGS = {
    "fx-taskq-py": "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py:1.0.5",
    "fx-ledger-ts": "ghcr.io/kuotunyu/coding-agent-eval-fx-ledger-ts:1.0.3",
}


def tree_of(fixture_id: str) -> Path:
    return REPO_ROOT / "fixtures" / fixture_id / "tree"


@pytest.fixture(scope="module", params=sorted(FIXTURES))
def fixture_id(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture(scope="module")
def image(fixture_id: str) -> str:
    try:
        return resolve_digest(FIXTURES[fixture_id])
    except RuntimeError as exc:  # pragma: no cover - environment dependent
        pytest.skip(str(exc))


def call(context: ToolContext, name: str, **arguments: object) -> str:
    return TOOLS_BY_NAME[name].handler(context, dict(arguments))


# ------------------------------------------------------------- equivalence


def test_the_two_backends_return_the_same_bytes_for_every_tool(fixture_id: str, image: str) -> None:
    """The claim that makes isolation free: same output, different containment.

    Run over the real fixture tree rather than a toy one, because the shapes
    that would break — a nested package, a dotfile, a file with no trailing
    newline — are the ones a constructed tree quietly leaves out.
    """
    tree = tree_of(fixture_id)
    host = ToolContext(root=tree)

    with tool_container(image, tree) as view:
        contained = ToolContext(backend=view)

        # Non-vacuity, stated per comparison: two backends that both returned
        # nothing would agree perfectly and prove nothing. The root has several
        # entries in both fixtures; `src` has one in fx-taskq-py, so it is only
        # required to be non-empty.
        root_listing = call(host, "list_directory", path=".")
        assert len(root_listing.splitlines()) >= 4, root_listing
        assert root_listing == call(contained, "list_directory", path=".")

        src_listing = call(host, "list_directory", path="src")
        assert src_listing.strip(), "src listed nothing to compare"
        assert src_listing == call(contained, "list_directory", path="src")

        for path, pattern in ((".", r"def |function |class "), ("src", "return")):
            found = call(host, "search_code", path=path, pattern=pattern)
            assert found != "no matches" and found.count("\n") >= 5, (
                f"search({path!r}, {pattern!r}) found too little to compare"
            )
            assert found == call(contained, "search_code", path=path, pattern=pattern)

        readme = call(host, "read_file", path="README.md")
        assert readme.startswith("     1\t"), "expected numbered lines"
        assert readme == call(contained, "read_file", path="README.md")


def test_a_search_that_matches_nothing_agrees_too(fixture_id: str, image: str) -> None:
    """The empty case has its own wording, so it needs its own assertion."""
    tree = tree_of(fixture_id)
    host = ToolContext(root=tree)
    with tool_container(image, tree) as view:
        pattern = "zzz_no_such_token_zzz"
        assert call(host, "search_code", path=".", pattern=pattern) == "no matches"
        assert call(ToolContext(backend=view), "search_code", path=".", pattern=pattern) == (
            "no matches"
        )


# ---------------------------------------------------------------- containment


def test_the_tools_cannot_read_a_host_file(fixture_id: str, image: str, tmp_path: Path) -> None:
    """The whole point. A host file is not reachable, by any spelling.

    The host backend refuses the first two by path check. The container refuses
    them the same way *and* has nothing to reach even if the check were wrong,
    which is the difference this module exists to make.
    """
    secret = tmp_path / "host-secret.txt"
    secret.write_bytes(b"host side only\n")

    with tool_container(image, tree_of(fixture_id)) as view:
        context = ToolContext(backend=view)
        for spelling in (secret.as_posix(), "../" * 8 + "etc/passwd", "/etc/passwd"):
            with pytest.raises(ToolFailure):
                call(context, "read_file", path=spelling)


def test_the_container_cannot_see_the_host_path_even_without_the_tool_surface(
    fixture_id: str, image: str, tmp_path: Path
) -> None:
    """Checked underneath the path checks, so it is the kernel being asserted.

    If this passed only because `normalise` rejected the path, it would prove
    the check works, not that the boundary exists. Here the path goes straight
    to a process in the container.
    """
    secret = tmp_path / "host-secret.txt"
    secret.write_bytes(b"host side only\n")

    with tool_container(image, tree_of(fixture_id)) as view:
        proc = subprocess.run(
            ["docker", "exec", view.container_id, "sh", "-c", f"cat '{secret.as_posix()}' 2>&1"],
            capture_output=True,
            timeout=60,
        )
        assert proc.returncode != 0
        assert b"host side only" not in proc.stdout


def test_the_container_has_no_network_interface_but_loopback(fixture_id: str, image: str) -> None:
    """`--network none` is requested by the profile; this observes it was granted.

    Read from procfs rather than by attempting a connection. The first version
    of this test used `cat < /dev/tcp/...`, which is a bash feature — and the
    shell in both images is dash, where it fails whether or not there is a
    network. It passed for the wrong reason. `/proc/net/dev` is always there and
    lists exactly the interfaces the namespace has.
    """
    with tool_container(image, tree_of(fixture_id)) as view:
        proc = subprocess.run(
            ["docker", "exec", view.container_id, "cat", "/proc/net/dev"],
            capture_output=True,
            timeout=60,
        )
        assert proc.returncode == 0, proc.stderr

        interfaces = {
            line.split(":")[0].strip() for line in proc.stdout.decode().splitlines() if ":" in line
        }
        assert interfaces == {"lo"}, f"expected loopback only, got {sorted(interfaces)}"


def test_the_container_has_no_route_off_the_host(fixture_id: str, image: str) -> None:
    """The complement: no interface and also nowhere to send anything."""
    with tool_container(image, tree_of(fixture_id)) as view:
        proc = subprocess.run(
            ["docker", "exec", view.container_id, "cat", "/proc/net/route"],
            capture_output=True,
            timeout=60,
        )
        routes = [line for line in proc.stdout.decode().splitlines()[1:] if line.strip()]
        assert routes == [], f"expected no routes, got {routes}"


def test_the_measured_tree_cannot_be_written_by_the_agents_own_container(
    fixture_id: str, image: str
) -> None:
    """A run must not be able to edit the tree it is being scored against."""
    with tool_container(image, tree_of(fixture_id)) as view:
        proc = subprocess.run(
            [
                "docker",
                "exec",
                view.container_id,
                "sh",
                "-c",
                "touch /workspace/README.md.x 2>&1 || echo REFUSED",
            ],
            capture_output=True,
            timeout=60,
        )
        assert b"REFUSED" in proc.stdout


def test_the_delivered_tree_carries_no_symlink(fixture_id: str, image: str) -> None:
    """Excluded at pack time, so there is no link for a path check to have to catch."""
    with tool_container(image, tree_of(fixture_id)) as view:
        proc = subprocess.run(
            [
                "docker",
                "exec",
                view.container_id,
                "sh",
                "-c",
                f"find {SCRATCH}/tree -type l | wc -l",
            ],
            capture_output=True,
            timeout=60,
        )
        assert proc.stdout.decode().strip() == "0"


# ------------------------------------------------------------- tool failures


def test_a_missing_file_is_a_tool_failure_not_a_container_error(
    fixture_id: str, image: str
) -> None:
    """An agent must be able to tell "no such file" from "the harness broke"."""
    with tool_container(image, tree_of(fixture_id)) as view:
        context = ToolContext(backend=view)
        with pytest.raises(ToolFailure, match="no file at"):
            call(context, "read_file", path="src/definitely_absent.txt")
        with pytest.raises(ToolFailure, match="no directory at"):
            call(context, "list_directory", path="src/definitely_absent")


def test_reading_a_directory_reports_no_file_exactly_as_the_host_does(
    fixture_id: str, image: str
) -> None:
    tree = tree_of(fixture_id)
    with tool_container(image, tree) as view:
        with pytest.raises(ToolFailure) as contained:
            call(ToolContext(backend=view), "read_file", path="src")
        with pytest.raises(ToolFailure) as host:
            call(ToolContext(root=tree), "read_file", path="src")
    assert str(contained.value) == str(host.value)


def test_a_file_over_the_cap_is_refused_with_its_real_size(fixture_id: str, image: str) -> None:
    """Refused in the container, so the bytes never cross. The size still gets back."""
    tree = tree_of(fixture_id)
    with tool_container(image, tree) as view:
        context = ToolContext(backend=view, max_file_bytes=16)
        with pytest.raises(ToolFailure, match=r"over the 16 byte limit") as excinfo:
            call(context, "read_file", path="README.md")

    actual = (tree / "README.md").stat().st_size
    assert f"is {actual} bytes" in str(excinfo.value), "the size must be the file's, not the cap's"


# ------------------------------------------------------------------ lifecycle


def test_the_container_is_removed_when_the_block_ends(fixture_id: str, image: str) -> None:
    with tool_container(image, tree_of(fixture_id)) as view:
        container_id = view.container_id
        assert is_running(container_id)
    assert not is_running(container_id)


def test_the_container_is_removed_when_the_block_raises(fixture_id: str, image: str) -> None:
    """The case that actually leaks in practice: scoring fails, nobody cleans up."""
    container_id = ""
    with (
        pytest.raises(RuntimeError, match="deliberate"),
        tool_container(image, tree_of(fixture_id)) as view,
    ):
        container_id = view.container_id
        raise RuntimeError("deliberate")
    assert container_id
    assert not is_running(container_id)


def test_an_image_given_by_tag_is_refused(fixture_id: str) -> None:
    """A tag can be repointed, so a result taken against one is not reproducible."""
    with (
        pytest.raises(ProfileError, match="not pinned by digest"),
        tool_container(FIXTURE_TAGS[fixture_id], tree_of(fixture_id)),
    ):
        pass


def test_a_tree_that_does_not_exist_is_a_container_error(image: str, tmp_path: Path) -> None:
    with (
        pytest.raises(ContainerError, match="no tree to deliver"),
        tool_container(image, tmp_path / "absent"),
    ):
        pass


def test_the_backend_reports_the_image_it_was_pinned_to(fixture_id: str, image: str) -> None:
    """`tool_backend` in a result has to name something checkable."""
    with tool_container(image, tree_of(fixture_id)) as view:
        assert view.description == f"measure_container:{image}"
        assert image.startswith("sha256:")


def test_the_host_backend_says_plainly_that_it_is_not_isolated() -> None:
    """The counterpart, so a result produced on the host cannot read as sandboxed."""
    assert LocalTree(Path(".")).description == "host_process"


# ------------------------------------------------------------------ packing


def test_packing_drops_symlinks_but_keeps_regular_files(tmp_path: Path) -> None:
    """Runs without Docker if it gets that far; the marker keeps it with its subject."""
    tree = tmp_path / "tree"
    (tree / "src").mkdir(parents=True)
    (tree / "src" / "a.py").write_bytes(b"x = 1\n")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"secret\n")
    try:
        (tree / "escape.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available to this user")

    import io
    import tarfile

    with tarfile.open(fileobj=io.BytesIO(pack_tree(tree))) as archive:
        names = {member.name for member in archive.getmembers()}
    assert "tree/src/a.py" in names
    assert "tree/escape.txt" not in names


def test_the_profile_used_is_the_measure_profile() -> None:
    """Stated so a future edit cannot quietly relax it to the witness profile."""
    assert MEASURE.network_none
    assert MEASURE.read_only_root
    assert MEASURE.user == "1000:1000"


def test_a_container_tree_addresses_paths_under_the_scratch_mount() -> None:
    """The tree lands on the one writable mount, not on the read-only rootfs."""
    view = ContainerTree(container_id="none", image="sha256:" + "0" * 64)
    assert view.root == f"{SCRATCH}/tree"
    assert view._absolute(".") == f"{SCRATCH}/tree"
    assert view._absolute("src/app.py") == f"{SCRATCH}/tree/src/app.py"
