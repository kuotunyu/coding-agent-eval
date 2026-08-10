"""Environment identity, re-derived rather than trusted (design spec §9.4).

A fixture manifest records what a run was measured in: a prepared image digest,
an OS, a language runtime, a package manager, a lock manifest, an architecture,
and a `fingerprint` over all of it. Every result carries that fingerprint, and
two runs whose fingerprints differ are not comparable however close their numbers
look.

Until now those values were **recorded and trusted**. Nothing pulled the image
and confirmed the digest, and nothing recomputed the fingerprint from a running
container. A manifest naming an environment that had drifted — or one whose
digest had simply been mistyped — passed every gate in the repository, and the
comparability claim resting on it was unchecked.

This re-derives them. Run the prepared image, read what is actually inside it,
hash the lock manifest on disk, recompute the fingerprint, and compare.

**What this cannot check, and says so rather than implying otherwise.**
`base_image_digest` is a *registry manifest* digest, obtained with
`docker buildx imagetools inspect`. It is not the local image ID of anything, it
does not resolve offline, and neither base image is present on a machine that has
only ever built the prepared images. So it is taken from the manifest as an input
to the fingerprint rather than verified against reality, and is reported as an
`Observation` — not as a passing check — so a green report cannot be read as
confirming it. What *is* checked is that the rebuild recipe consumes the pin
instead of naming a floating tag, which is what makes the recorded value
load-bearing at build time.

**The component spellings are frozen, warts included.** `primary_runtime_version`
is a bare version (`3.12.13`), while `package_manager_version` carries its tool's
name (`pip 25.0.1`). That asymmetry is not a good rule; it is the rule the
recorded fingerprints were computed under, and normalising it would change the
identity of every environment in the repository while making no environment
different. It is frozen here and asserted by reproducing both manifests.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from coding_agent_eval.fixtures.report import Check, Observation
from coding_agent_eval.sandbox.fingerprint import COMPONENTS, environment_fingerprint

_DOCKER_TIMEOUT = 180

#: Separates the probe's answers, so one command reads everything at once.
#:
#: A separate `docker run` per value would be four container starts for four
#: strings, and would leave room for two of them to come from different images.
_FIELD = "|CAE|"

_PROBE = (
    ". /etc/os-release || exit 20; "
    f'printf "%s{_FIELD}%s{_FIELD}" "$ID" "$VERSION_ID"; '
    "$0 --version | head -1 | tr -d '\\n'; "
    f'printf "{_FIELD}"; '
    "$1 --version | head -1 | tr -d '\\n'"
)


class EnvironmentCheckError(RuntimeError):
    """The gate could not run. Distinct from the gate running and failing."""


@dataclass(frozen=True)
class Runtime:
    """How to ask an image what it is, per fixture language."""

    runtime: str
    manager: str


#: Keyed by the manifest's `language`. An unknown language fails rather than
#: guessing: probing the wrong binary would report a version for something the
#: fixture does not run on.
RUNTIMES: dict[str, Runtime] = {
    "python": Runtime(runtime="python3", manager="pip"),
    "typescript": Runtime(runtime="node", manager="npm"),
}


@dataclass(frozen=True)
class EnvironmentReport:
    """Every §9.4 check for one fixture, plus what could only be observed."""

    fixture_id: str
    fixture_version: str
    checks: tuple[Check, ...]
    observations: tuple[Observation, ...] = ()

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(check for check in self.checks if not check.ok)

    def render(self) -> str:
        head = f"{self.fixture_id} {self.fixture_version}: {'PASS' if self.ok else 'FAIL'}"
        body = [check.render() for check in self.checks]
        body += [observation.render() for observation in self.observations]
        return "\n".join([head, *body])


def runtime_version(raw: str) -> str:
    """`Python 3.12.13` and `v22.23.2` both become a bare version.

    Frozen: the recorded fingerprints were computed this way.
    """
    tokens = raw.split()
    if not tokens:
        raise EnvironmentCheckError("the runtime reported no version")
    return tokens[-1].lstrip("v")


def manager_version(raw: str, manager: str) -> str:
    """`pip 25.0.1 from /usr/...` and `10.9.8` both become `<manager> <version>`.

    Note the asymmetry with `runtime_version`, which drops the tool's name. It
    is preserved deliberately; see the module docstring.
    """
    tokens = raw.split()
    if tokens and tokens[0].lower() == manager:
        tokens = tokens[1:]
    if not tokens:
        raise EnvironmentCheckError(f"{manager} reported no version")
    return f"{manager} {tokens[0]}"


def _docker(args: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["docker", *args], capture_output=True, timeout=_DOCKER_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvironmentCheckError(f"docker {args[0]} failed: {exc}") from exc


def local_image_id(tag: str) -> str | None:
    """The local image ID for a tag, or None when it is not present."""
    proc = _docker(["image", "inspect", "--format", "{{.Id}}", tag])
    return proc.stdout.decode("utf-8").strip() if proc.returncode == 0 else None


def image_arch(image: str) -> str:
    proc = _docker(["image", "inspect", "--format", "{{.Os}}/{{.Architecture}}", image])
    if proc.returncode != 0:
        raise EnvironmentCheckError(f"could not read the architecture of {image}")
    return proc.stdout.decode("utf-8").strip()


@dataclass(frozen=True)
class Probed:
    """What the prepared image says it is."""

    os_release_id: str
    os_release_version_id: str
    primary_runtime_version: str
    package_manager_version: str
    arch: str


def probe_image(image: str, language: str) -> Probed:
    """Read the OS, runtime, and package manager out of a prepared image."""
    if language not in RUNTIMES:
        raise EnvironmentCheckError(
            f"no probe for language {language!r}; add one rather than guessing a binary"
        )
    tools = RUNTIMES[language]

    proc = _docker(
        [
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "sh",
            image,
            "-c",
            _PROBE,
            tools.runtime,
            tools.manager,
        ]
    )
    if proc.returncode != 0:
        raise EnvironmentCheckError(
            f"could not probe {image}: {proc.stderr.decode('utf-8', 'replace').strip()}"
        )

    parts = proc.stdout.decode("utf-8", "replace").split(_FIELD)
    if len(parts) != 4:
        raise EnvironmentCheckError(
            f"the probe returned {len(parts)} fields, expected 4 "
            "(os id, os version, runtime, package manager)"
        )

    return Probed(
        os_release_id=parts[0].strip(),
        os_release_version_id=parts[1].strip(),
        primary_runtime_version=runtime_version(parts[2]),
        package_manager_version=manager_version(parts[3], tools.manager),
        arch=image_arch(image),
    )


def lock_manifest_sha256(path: Path) -> str:
    """Bare hex, not `sha256:`-prefixed. Frozen: the fingerprints assume it."""
    if not path.is_file():
        raise EnvironmentCheckError(f"no lock manifest at {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def recipe_pins_the_base(recipe: Path) -> Check:
    """The Dockerfile must take the digest as a build argument and use it.

    Without this the recorded `base_image_digest` would be decorative: the build
    could name a floating tag while the manifest claimed a pin, and nothing
    downstream would notice. It is the one part of the base pin that can be
    checked without a network.
    """
    if not recipe.is_file():
        return Check(
            name="recipe_pins_the_base",
            ok=False,
            expected=f"a rebuild recipe at {recipe.name}",
            actual="the file is absent",
        )

    text = recipe.read_text(encoding="utf-8")
    declares = re.search(r"^ARG\s+BASE_DIGEST\s*$", text, re.MULTILINE) is not None
    consumes = re.search(r"^FROM\s+\S+@\$\{BASE_DIGEST\}", text, re.MULTILINE) is not None
    floating = re.findall(r"^FROM\s+(?!\S+@)(\S+)", text, re.MULTILINE)

    problems: list[str] = []
    if not declares:
        problems.append("no `ARG BASE_DIGEST` declaration")
    if not consumes:
        problems.append("no `FROM <image>@${BASE_DIGEST}` line")
    problems += [f"a FROM line names {ref!r} rather than a digest" for ref in floating]

    return Check(
        name="recipe_pins_the_base",
        ok=not problems,
        expected="FROM <image>@${BASE_DIGEST}, taken as a build argument",
        actual="; ".join(problems) if problems else "pinned",
        detail=(
            ()
            if not problems
            else ("a recorded base digest the build does not consume describes nothing",)
        ),
    )


def load_manifest(fixture_dir: Path) -> dict[str, Any]:
    path = fixture_dir / "fixture.yaml"
    if not path.is_file():
        raise EnvironmentCheckError(f"no fixture manifest at {path}")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise EnvironmentCheckError(f"{path} is not a mapping")
    return document


def run_environment_check(fixture_dir: Path) -> EnvironmentReport:
    """Re-derive one fixture's environment identity and compare with its manifest.

    Raises `EnvironmentCheckError` when the check cannot run — no daemon, no image,
    an unknown language. Returns a report whose `ok` is false when it ran and
    something did not hold.
    """
    manifest = load_manifest(fixture_dir)
    environment = manifest["environment"]
    declared_digest = str(environment["prepared_image_digest"])
    tag = str(environment["prepared_image_tag"])

    present = local_image_id(tag)
    if present is None:
        raise EnvironmentCheckError(
            f"the prepared image {tag} is not present locally; build it from "
            f"{environment['rebuild_recipe']} before this can check anything"
        )

    checks: list[Check] = [
        Check(
            name="prepared_image_digest",
            ok=present == declared_digest,
            expected=declared_digest,
            actual=present,
            detail=(
                ()
                if present == declared_digest
                else (
                    f"the image tagged {tag} is not the one the manifest names; "
                    "a result taken against it carries a fingerprint for a different "
                    "environment",
                )
            ),
        ),
        recipe_pins_the_base(fixture_dir / str(environment["rebuild_recipe"])),
    ]

    # Probe the image the manifest names, not whatever the tag points at. If the
    # digest check above failed, the tag is the wrong image, and reporting its
    # Python version as the fixture's would compound one error with another.
    subject = declared_digest if present == declared_digest else present
    probed = probe_image(subject, str(manifest["language"]))
    lock_hex = lock_manifest_sha256(fixture_dir / str(environment["lock_manifest"]))

    components = {
        "base_image_digest": str(environment["base_image_digest"]),
        "prepared_image_digest": declared_digest,
        "os_release_id": probed.os_release_id,
        "os_release_version_id": probed.os_release_version_id,
        "primary_runtime_version": probed.primary_runtime_version,
        "package_manager_version": probed.package_manager_version,
        "lock_manifest_sha256": lock_hex,
        "arch": probed.arch,
    }
    assert set(components) == set(COMPONENTS), "the probe must cover every §9.4 component"

    rebuilt = environment_fingerprint(components)
    declared_fingerprint = str(environment["fingerprint"])
    checks.append(
        Check(
            name="fingerprint",
            ok=rebuilt == declared_fingerprint,
            expected=declared_fingerprint,
            actual=rebuilt,
            detail=(
                tuple(f"{name} = {value}" for name, value in sorted(components.items()))
                if rebuilt != declared_fingerprint
                else ()
            ),
        )
    )

    return EnvironmentReport(
        fixture_id=str(manifest["fixture_id"]),
        fixture_version=str(manifest["fixture_version"]),
        checks=tuple(checks),
        observations=(
            Observation(
                name="base_image_digest",
                value=str(environment["base_image_digest"]),
                why_unverified=(
                    "a registry manifest digest; confirming it needs the network, and "
                    "the fingerprint above consumes it as an input rather than checking it"
                ),
            ),
        ),
    )
