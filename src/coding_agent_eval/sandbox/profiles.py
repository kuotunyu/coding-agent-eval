"""Sandbox profiles (design spec §9.1, §9.2).

Two profiles, differing in exactly one thing that matters.

**prepare** installs dependencies, so it needs the network. It runs once per
fixture version and produces an immutable image.

**measure** runs the agent's tools against that image with the network off. The
code being examined is untrusted by assumption — it is a fixture full of seeded
defects, and in v0.2 it will include deliberately poisoned content — so it gets
no route out.

`--network none` applies to the *tool container only*. The harness still talks to
a provider over the host network; that is a different process on a different side
of the boundary (spec §9.1).

Everything here builds argv. Constructing the right arguments is not the same as
the container behaving correctly, and only gate H2 can tell the difference by
running one and observing it. Until H2 passes, these properties are design
requirements and no document may describe them otherwise (spec §9.5).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

#: A digest reference, optionally repository-qualified: `name@sha256:…` or `sha256:…`.
_DIGEST = re.compile(r"^(?:[^@\s]+@)?sha256:[0-9a-f]{64}$")


class ProfileError(ValueError):
    """The requested run would not be the sandbox it claims to be."""


@dataclass(frozen=True)
class SandboxProfile:
    """An execution profile. Frozen: a run must not weaken the profile it was given."""

    name: str
    network_none: bool
    read_only_root: bool
    user: str
    memory: str
    cpus: str
    pids_limit: int
    tmpfs: tuple[str, ...]


#: Network on, writable. Used once per fixture version to build the prepared image.
PREPARE: Final = SandboxProfile(
    name="prepare",
    network_none=False,
    read_only_root=False,
    user="1000:1000",
    memory="4g",
    cpus="4",
    pids_limit=512,
    tmpfs=(),
)

#: Network off, read-only root, writable only where work has to happen.
#:
#: Both tmpfs mounts carry `mode=1777`. Without it they are created root-owned
#: with default permissions, and the container runs as uid 1000 — so the one
#: place work is supposed to happen is the one place it cannot. An image that
#: `chown`s the directory does not help: the mount replaces what was there.
#: Gate H2 caught this, which is the whole reason H2 observes behaviour instead
#: of inspecting flags. 1777 rather than an explicit uid so the mode does not
#: silently disagree with `user` if that changes.
MEASURE: Final = SandboxProfile(
    name="measure",
    network_none=True,
    read_only_root=True,
    user="1000:1000",
    memory="2g",
    cpus="2",
    pids_limit=256,
    tmpfs=("/tmp:size=256m,mode=1777", "/workspace/scratch:size=256m,mode=1777"),
)


#: Network off, but the root filesystem is writable.
#:
#: The writable root is forced, not preferred. A witness contract needs its test
#: files inside the container, and `docker cp` into a `--read-only` container is
#: refused by the daemon outright — "container rootfs is marked read-only". The
#: only other way in is a bind mount, which would put a host path inside the
#: container, and that is the precise thing the no-bind-mount rule exists to
#: prevent. Between a writable rootfs and a host mount, the writable rootfs
#: concedes less.
#:
#: What it does not concede is the network. A witness able to reach out would not
#: be deterministic, and `deterministic: true` is a claim every contract makes.
#:
#: This is not the profile agent output runs under. It executes fixture-authored
#: verification code against a fixture from the same repository, which is a
#: narrower trust boundary than MEASURE has to hold.
WITNESS: Final = SandboxProfile(
    name="witness",
    network_none=True,
    read_only_root=False,
    user="1000:1000",
    memory="2g",
    cpus="2",
    pids_limit=256,
    tmpfs=(),
)


def _check_image(image: str) -> None:
    if not _DIGEST.match(image):
        raise ProfileError(
            f"image {image!r} is not pinned by digest; a tag can be repointed, so a "
            "result taken against one cannot be reproduced"
        )


def build_run_argv(
    profile: SandboxProfile,
    *,
    image: str,
    command: list[str],
    workdir: str = "/workspace",
    environment: dict[str, str] | None = None,
    memory: str | None = None,
    cpus: str | None = None,
    pids_limit: int | None = None,
) -> list[str]:
    """Assemble `docker run` arguments for `profile`.

    No bind mount is ever produced. The fixture tree is baked into the prepared
    image, so the container has no path into the host filesystem at all — which
    is a stronger position than mounting one read-only.
    """
    return _build_container_argv(
        profile,
        verb="run",
        image=image,
        command=command,
        workdir=workdir,
        environment=environment,
        memory=memory,
        cpus=cpus,
        pids_limit=pids_limit,
    )


def build_create_argv(
    profile: SandboxProfile,
    *,
    image: str,
    command: list[str],
    workdir: str = "/workspace",
    environment: dict[str, str] | None = None,
    memory: str | None = None,
    cpus: str | None = None,
    pids_limit: int | None = None,
) -> list[str]:
    """Assemble `docker create` arguments for `profile`.

    Same flags as `build_run_argv`, and deliberately the same code path: the
    no-bind-mount guarantee has to hold for every container this package starts,
    and a second assembler would be a second place for it to stop holding.

    `create` exists so files can be delivered with `docker cp` before the
    container starts, which is how a witness overlay gets in without a mount.
    """
    return _build_container_argv(
        profile,
        verb="create",
        image=image,
        command=command,
        workdir=workdir,
        environment=environment,
        memory=memory,
        cpus=cpus,
        pids_limit=pids_limit,
    )


def _build_container_argv(
    profile: SandboxProfile,
    *,
    verb: str,
    image: str,
    command: list[str],
    workdir: str,
    environment: dict[str, str] | None,
    memory: str | None,
    cpus: str | None,
    pids_limit: int | None,
) -> list[str]:
    _check_image(image)
    if not command:
        raise ProfileError("command is empty; a container with nothing to run is a mistake")

    # `run` cleans up after itself. `create` does not get `--rm`: the caller has
    # to copy files in, start it, and read its exit status, and an auto-removing
    # container can vanish before the status is read.
    argv: list[str] = ["docker", verb] + (["--rm"] if verb == "run" else [])

    if profile.network_none:
        argv += ["--network", "none"]

    argv += [
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--user",
        profile.user,
        "--memory",
        memory or profile.memory,
        "--cpus",
        cpus or profile.cpus,
        "--pids-limit",
        str(pids_limit if pids_limit is not None else profile.pids_limit),
    ]

    if profile.read_only_root:
        argv.append("--read-only")

    for mount in profile.tmpfs:
        argv += ["--tmpfs", mount]

    for key, value in sorted((environment or {}).items()):
        argv += ["--env", f"{key}={value}"]

    argv += ["--workdir", workdir, image, *command]
    return argv
