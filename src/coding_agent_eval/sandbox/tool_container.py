"""The agent's tools, executed inside the measure container (design spec §9.1).

This closes the gap `e2e.py` used to record: gate H2 verified the sandbox and
gate G9 ran the pipeline, but the tool surface still read the tree in-process on
the host, so a run was isolated by its own path checks rather than by the kernel.
Here the bytes come out of a container that has `--network none`, `--read-only`,
`--cap-drop ALL`, and — the part that matters most — **no path into the host
filesystem at all**.

Four facts decided the design, each measured rather than assumed:

1. **There is no runtime common to both prepared images.** `fx-taskq-py` has
   `python3` and no `node`; `fx-ledger-ts` has `node` and no `python3`. Both have
   `sh`, `cat`, `find`, and `tar`. So the container side does raw byte transport
   with POSIX utilities, and every rule the tools enforce — the byte caps, the
   line numbering, the regular expression dialect — stays on the host, where it
   is written once and behaves identically for both fixtures.
2. **`docker cp` into a `--read-only` container is refused by the daemon**
   ("container rootfs is marked read-only"). That is why the witness runner has
   a separate profile with a writable root. It is not needed here.
3. **A tar stream piped to `docker exec -i` is not refused**, because it is an
   ordinary process writing to a writable tmpfs rather than the daemon writing
   to the rootfs. So the tree arrives without weakening the profile and without
   a bind mount.
4. **`docker exec` inherits the container's isolation.** A process started that
   way gets the same network namespace and the same read-only mounts —
   `ENETUNREACH` on connect, `EROFS` on the measured tree. Asserted in the
   Docker-marked tests rather than taken on faith, because the whole value of
   this module rests on it.

The container's own main process is a bounded `sleep`, so a container leaked by
a crashed harness removes itself rather than living until someone notices.
"""

from __future__ import annotations

import io
import subprocess
import tarfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from coding_agent_eval.agent.backend import (
    DirEntry,
    no_directory,
    no_file,
    over_limit,
)
from coding_agent_eval.sandbox.profiles import MEASURE, SandboxProfile, build_create_argv

#: Where the tree is unpacked. A tmpfs under the measure profile, so it is the
#: one writable place — and it is wiped with the container.
SCRATCH = "/workspace/scratch"
TREE_ROOT = f"{SCRATCH}/tree"

#: Long enough for any plausible run, short enough that a leaked container is a
#: nuisance rather than a resident.
DEFAULT_LIFETIME_SECONDS = 3600

_DOCKER_TIMEOUT = 180

#: `[ -f ]` was false: the path is missing, or is a directory.
_EXIT_NO_FILE = 4
#: The file is over the caller's cap. Its size is on stderr.
_EXIT_TOO_LARGE = 5
#: `[ -d ]` was false.
_EXIT_NO_DIRECTORY = 6

#: Read one file, refusing before the bytes move.
#:
#: The path arrives as a positional argument, never interpolated into the
#: script, so a path containing shell metacharacters is data rather than syntax.
_READ_SCRIPT = f"""
p="$1"
[ -f "$p" ] || exit {_EXIT_NO_FILE}
size=$(wc -c < "$p")
if [ "$size" -gt "$2" ]; then printf '%s' "$size" >&2; exit {_EXIT_TOO_LARGE}; fi
cat -- "$p"
"""

#: List one directory. `%y` is the type character, `%P` the name without the
#: starting point, so the output needs no path arithmetic on this side.
_LIST_SCRIPT = f"""
p="$1"
[ -d "$p" ] || exit {_EXIT_NO_DIRECTORY}
find "$p" -mindepth 1 -maxdepth 1 -printf '%y %P\\n'
"""

#: Tar one subtree to stdout for the host to match against.
_SUBTREE_SCRIPT = f"""
p="$1"
[ -d "$p" ] || exit {_EXIT_NO_DIRECTORY}
tar -c -C "$p" .
"""


class ContainerError(RuntimeError):
    """The container could not be started, loaded, or reached.

    A harness problem, not a tool failure. Kept separate so a broken daemon
    never reaches the agent as "no file at ...", which it would then reason
    about as though it were a fact about the tree.
    """


def _docker(args: list[str], *, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["docker", *args], input=stdin, capture_output=True, timeout=_DOCKER_TIMEOUT
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ContainerError(f"docker {args[0]} failed: {exc}") from exc


def pack_tree(tree: Path, *, arcname: str = "tree") -> bytes:
    """Tar a host tree for delivery, dropping everything that is not a regular file.

    Symlinks are excluded rather than followed. They are already outside a
    fixture's identity — `tree_checksum` skips them, so a symlink is not part of
    what a fixture *is* — and excluding them means the container copy cannot
    contain a link out of the tree for a path check to have to catch. Neither
    shipped fixture has one.
    """
    if not tree.is_dir():
        raise ContainerError(f"no tree to deliver at {tree}")

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for path in sorted(tree.rglob("*"), key=lambda p: p.relative_to(tree).as_posix()):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                continue
            name = f"{arcname}/{path.relative_to(tree).as_posix()}"
            info = archive.gettarinfo(path, arcname=name)
            # Uniform, permissive modes: the container runs as uid 1000 and the
            # host's ownership means nothing inside it. A tree that arrived
            # unreadable would fail as "no file", which is a confusing way to
            # say "the permissions did not survive the trip".
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            info.mode = 0o755 if path.is_dir() else 0o644
            info.mtime = 0
            if path.is_dir():
                archive.addfile(info)
            else:
                with path.open("rb") as handle:
                    archive.addfile(info, handle)
    return buffer.getvalue()


@dataclass
class ContainerTree:
    """A fixture tree the tools can reach only through a process in a container."""

    container_id: str
    image: str
    root: str = TREE_ROOT

    @property
    def description(self) -> str:
        return f"measure_container:{self.image}"

    def _absolute(self, relative: str) -> str:
        """Map a validated tree-relative path to its path inside the container.

        `normalise` has already refused anything absolute or containing `..`, so
        this is concatenation rather than resolution — and even if it were not,
        the worst reachable thing is the container's own filesystem.
        """
        return self.root if relative == "." else f"{self.root}/{relative}"

    def _exec(self, script: str, *args: str) -> subprocess.CompletedProcess[bytes]:
        proc = _docker(["exec", self.container_id, "sh", "-c", script, "sh", *args])
        if proc.returncode in (125, 126, 127) and b"is not running" in proc.stderr:
            raise ContainerError(f"container {self.container_id[:12]} is no longer running")
        return proc

    def read_bytes(self, relative: str, *, max_bytes: int) -> bytes:
        proc = self._exec(_READ_SCRIPT, self._absolute(relative), str(max_bytes))
        if proc.returncode == _EXIT_NO_FILE:
            raise no_file(relative)
        if proc.returncode == _EXIT_TOO_LARGE:
            size = int(proc.stderr.decode("utf-8", "replace").strip() or 0)
            raise over_limit(relative, size, max_bytes)
        if proc.returncode != 0:
            raise ContainerError(
                f"reading {relative!r} exited {proc.returncode}: "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )
        return proc.stdout

    def list_entries(self, relative: str) -> list[DirEntry]:
        proc = self._exec(_LIST_SCRIPT, self._absolute(relative))
        if proc.returncode == _EXIT_NO_DIRECTORY:
            raise no_directory(relative)
        if proc.returncode != 0:
            raise ContainerError(
                f"listing {relative!r} exited {proc.returncode}: "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )

        prefix = "" if relative == "." else f"{relative}/"
        entries: list[DirEntry] = []
        for line in proc.stdout.decode("utf-8", "replace").splitlines():
            kind, _, name = line.partition(" ")
            if not name:
                continue
            entries.append(DirEntry(path=f"{prefix}{name}", is_dir=kind == "d"))
        return sorted(entries, key=lambda entry: (not entry.is_dir, entry.path))

    def read_subtree(self, relative: str) -> list[tuple[str, bytes]]:
        proc = self._exec(_SUBTREE_SCRIPT, self._absolute(relative))
        if proc.returncode == _EXIT_NO_DIRECTORY:
            raise no_directory(relative)
        if proc.returncode != 0:
            raise ContainerError(
                f"reading the subtree at {relative!r} exited {proc.returncode}: "
                f"{proc.stderr.decode('utf-8', 'replace').strip()}"
            )

        prefix = "" if relative == "." else f"{relative}/"
        collected: list[tuple[str, bytes]] = []
        try:
            with tarfile.open(fileobj=io.BytesIO(proc.stdout)) as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    handle = archive.extractfile(member)
                    if handle is None:
                        continue
                    payload = handle.read()
                    name = member.name.removeprefix("./")
                    collected.append((f"{prefix}{name}", payload))
        except tarfile.TarError as exc:
            raise ContainerError(f"could not read the subtree at {relative!r}: {exc}") from exc

        collected.sort(key=lambda pair: pair[0])
        return collected


@contextmanager
def tool_container(
    image: str,
    tree: Path,
    *,
    profile: SandboxProfile = MEASURE,
    lifetime_seconds: int = DEFAULT_LIFETIME_SECONDS,
) -> Iterator[ContainerTree]:
    """Start a measure container holding `tree`, and remove it however this ends.

    `image` must be pinned by digest; `build_create_argv` refuses a tag, because
    a tag can be repointed and a result taken against one cannot be reproduced.
    """
    argv = build_create_argv(
        profile,
        image=image,
        command=["sh", "-c", f"exec sleep {int(lifetime_seconds)}"],
        workdir="/workspace",
    )
    created = _docker(argv[1:])
    if created.returncode != 0:
        raise ContainerError(
            f"could not create the tool container: "
            f"{created.stderr.decode('utf-8', 'replace').strip()}"
        )
    container_id = created.stdout.decode("utf-8").strip()

    try:
        started = _docker(["start", container_id])
        if started.returncode != 0:
            raise ContainerError(
                f"could not start the tool container: "
                f"{started.stderr.decode('utf-8', 'replace').strip()}"
            )

        payload = pack_tree(tree)
        delivered = _docker(
            ["exec", "-i", container_id, "sh", "-c", f"tar -x -C {SCRATCH}"], stdin=payload
        )
        if delivered.returncode != 0:
            raise ContainerError(
                f"could not deliver the tree ({len(payload)} bytes): "
                f"{delivered.stderr.decode('utf-8', 'replace').strip()}"
            )

        tree_view = ContainerTree(container_id=container_id, image=image)
        # Proving the tree arrived is one call, and skipping it would turn a
        # failed delivery into every subsequent tool reporting an empty tree —
        # which an agent would report as findings about nothing.
        if not tree_view.list_entries("."):
            raise ContainerError("the tree was delivered but the container sees it as empty")
        yield tree_view
    finally:
        _docker(["rm", "--force", container_id])


def is_running(container_id: str) -> bool:
    """Whether a container is up. Used by tests to assert cleanup happened."""
    proc = _docker(["inspect", "--format", "{{.State.Running}}", container_id])
    return proc.returncode == 0 and proc.stdout.decode("utf-8").strip() == "true"
