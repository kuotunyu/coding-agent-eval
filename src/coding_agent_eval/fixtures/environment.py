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
import json
import os
import re
import subprocess
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from coding_agent_eval.fixtures.image_identity import (
    SHA256_DIGEST,
    PreparedImageIdentity,
)
from coding_agent_eval.fixtures.report import Check, Observation
from coding_agent_eval.sandbox.fingerprint import (
    COMPONENTS,
    CURRENT_COMPONENTS,
    environment_fingerprint,
)

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
    image_identity: PreparedImageIdentity | None = None
    observed_manifest_digest: str | None = None
    observed_config_digest: str | None = None

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

    def to_document(self) -> dict[str, Any]:
        """Return a JSON-safe report with declared and observed identity separated."""
        identity = None
        if self.image_identity is not None:
            identity = {
                "immutable_ref": self.image_identity.immutable_ref,
                "declared_manifest_digest": self.image_identity.manifest_digest,
                "observed_manifest_digest": self.observed_manifest_digest,
                "declared_config_digest": self.image_identity.config_digest,
                "observed_config_digest": self.observed_config_digest,
            }
        return {
            "fixture_id": self.fixture_id,
            "fixture_version": self.fixture_version,
            "ok": self.ok,
            "image_identity": identity,
            "checks": [
                {
                    "name": check.name,
                    "ok": check.ok,
                    "expected": check.expected,
                    "actual": check.actual,
                    "detail": list(check.detail),
                }
                for check in self.checks
            ],
            "observations": [
                {
                    "name": observation.name,
                    "value": observation.value,
                    "why_unverified": observation.why_unverified,
                }
                for observation in self.observations
            ],
        }


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


def _docker(
    args: list[str], *, environment: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ["docker", *args],
            capture_output=True,
            timeout=_DOCKER_TIMEOUT,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise EnvironmentCheckError(f"docker {args[0]} failed: {exc}") from exc


def _archive_json(archive: tarfile.TarFile, name: str) -> dict[str, Any]:
    try:
        member = archive.getmember(name)
        handle = archive.extractfile(member)
        if handle is None:
            raise EnvironmentCheckError(f"OCI image archive member {name} has no content")
        loaded = json.loads(handle.read())
    except (KeyError, OSError, tarfile.TarError, json.JSONDecodeError) as exc:
        raise EnvironmentCheckError(f"cannot read OCI image archive member {name}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise EnvironmentCheckError(f"OCI image archive member {name} is not an object")
    return loaded


def _config_digest_from_oci_archive(path: Path) -> str:
    """Find the one linux/amd64 config object in a Docker OCI-layout export."""
    try:
        with tarfile.open(path) as archive:
            return _config_digest_from_open_oci_archive(archive)
    except (OSError, tarfile.TarError) as exc:
        raise EnvironmentCheckError(f"cannot open Docker OCI image archive: {exc}") from exc


def _config_digest_from_open_oci_archive(archive: tarfile.TarFile) -> str:
    root = _archive_json(archive, "index.json")
    pending: list[Mapping[str, Any]] = []
    manifests = root.get("manifests")
    if isinstance(manifests, list):
        pending.extend(item for item in manifests if isinstance(item, Mapping))
    configs: set[str] = set()
    visited: set[str] = set()
    while pending:
        descriptor = pending.pop()
        platform = descriptor.get("platform")
        if isinstance(platform, Mapping) and (
            platform.get("os") != "linux" or platform.get("architecture") != "amd64"
        ):
            continue
        digest = descriptor.get("digest")
        if not isinstance(digest, str) or SHA256_DIGEST.fullmatch(digest) is None:
            raise EnvironmentCheckError("OCI image archive contains an invalid descriptor")
        if digest in visited:
            continue
        visited.add(digest)
        document = _archive_json(archive, f"blobs/sha256/{digest.removeprefix('sha256:')}")
        config = document.get("config")
        if isinstance(config, Mapping):
            config_digest = config.get("digest")
            if not isinstance(config_digest, str) or SHA256_DIGEST.fullmatch(config_digest) is None:
                raise EnvironmentCheckError("OCI image manifest contains an invalid config digest")
            configs.add(config_digest)
            continue
        children = document.get("manifests")
        if isinstance(children, list):
            pending.extend(item for item in children if isinstance(item, Mapping))

    if len(configs) != 1:
        raise EnvironmentCheckError(
            f"expected one linux/amd64 config digest in OCI image archive, found {len(configs)}"
        )
    return configs.pop()


def local_config_digest(image_ref: str) -> str | None:
    """The local OCI config digest, independent of Docker's changing `.Id` semantics.

    Docker's classic image store exposes the config digest as ``.Id``. The
    containerd image store in Docker Desktop 29 exposes the OCI index digest
    there instead. Exporting the already-local image and following its OCI
    descriptors keeps this check offline and names the actual config object.
    """
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="cae-image-", suffix=".tar", delete=False
        ) as handle:
            temporary = Path(handle.name)
        proc = _docker(["image", "save", "--output", str(temporary), image_ref])
        if proc.returncode != 0:
            return None
        return _config_digest_from_oci_archive(temporary)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def local_image_id(tag: str) -> str | None:
    """Compatibility name for the legacy prepared-image check."""
    return local_config_digest(tag)


def registry_manifest_digest(
    image_ref: str, *, environment: Mapping[str, str] | None = None
) -> str | None:
    """Resolve an OCI registry manifest digest without confusing it with `.Id`."""
    proc = _docker(
        [
            "buildx",
            "imagetools",
            "inspect",
            "--format",
            "{{json .Manifest.Digest}}",
            image_ref,
        ],
        environment=environment,
    )
    if proc.returncode != 0:
        return None
    raw = proc.stdout.decode("utf-8", "replace").strip()
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        decoded = raw
    if not isinstance(decoded, str) or SHA256_DIGEST.fullmatch(decoded) is None:
        raise EnvironmentCheckError(
            f"registry inspection returned an invalid manifest digest for {image_ref}"
        )
    return decoded


def anonymous_registry_manifest_digest(image_ref: str) -> str:
    """Resolve and pull `image_ref` with an empty Docker credential directory."""
    with tempfile.TemporaryDirectory(prefix="cae-docker-config-") as directory:
        anonymous_environment = dict(os.environ)
        anonymous_environment["DOCKER_CONFIG"] = directory
        manifest_digest = registry_manifest_digest(image_ref, environment=anonymous_environment)
        if manifest_digest is None:
            raise EnvironmentCheckError(
                f"could not resolve {image_ref} without registry credentials"
            )
        pulled = _docker(["pull", image_ref], environment=anonymous_environment)
        if pulled.returncode != 0:
            raise EnvironmentCheckError(f"could not pull {image_ref} without registry credentials")
        return manifest_digest


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


def run_environment_check(fixture_dir: Path, *, online: bool = False) -> EnvironmentReport:
    """Re-derive one fixture's environment identity and compare with its manifest.

    Raises `EnvironmentCheckError` when the check cannot run — no daemon, no image,
    an unknown language. Returns a report whose `ok` is false when it ran and
    something did not hold.
    """
    manifest = load_manifest(fixture_dir)
    environment = manifest["environment"]
    if "prepared_image_repository" in environment:
        return _run_current_environment_check(fixture_dir, manifest=manifest, online=online)
    if online:
        raise EnvironmentCheckError(
            "online registry verification requires the current OCI image identity contract"
        )
    return _run_legacy_environment_check(fixture_dir, manifest=manifest)


def _run_legacy_environment_check(
    fixture_dir: Path, *, manifest: dict[str, Any]
) -> EnvironmentReport:
    """Keep historical fixture verification readable during the migration."""
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
        observed_config_digest=present,
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


def _run_current_environment_check(
    fixture_dir: Path, *, manifest: dict[str, Any], online: bool
) -> EnvironmentReport:
    """Verify current registry/config identities without conflating the two."""
    environment = manifest["environment"]
    identity = PreparedImageIdentity.from_environment(environment)

    observed_config = local_config_digest(identity.immutable_ref)
    if observed_config is None:
        raise EnvironmentCheckError(
            f"the prepared image {identity.immutable_ref} is not present locally; "
            "pull that digest-qualified reference before this can check anything"
        )

    checks: list[Check] = []
    observed_manifest: str | None = None
    if online:
        observed_manifest = anonymous_registry_manifest_digest(identity.immutable_ref)
        checks += [
            Check(
                name="prepared_image_manifest_digest",
                ok=observed_manifest == identity.manifest_digest,
                expected=identity.manifest_digest,
                actual=observed_manifest,
                detail=(
                    ()
                    if observed_manifest == identity.manifest_digest
                    else ("the registry resolved a different OCI manifest",)
                ),
            ),
            Check(
                name="anonymous_pull",
                ok=True,
                expected="pullable without registry credentials",
                actual=identity.immutable_ref,
            ),
        ]

    checks += [
        Check(
            name="prepared_image_config_digest",
            ok=observed_config == identity.config_digest,
            expected=identity.config_digest,
            actual=observed_config,
            detail=(
                ()
                if observed_config == identity.config_digest
                else ("the local image configuration is not the object declared by the fixture",)
            ),
        ),
        recipe_pins_the_base(fixture_dir / str(environment["rebuild_recipe"])),
    ]

    # Probe the object selected by the immutable registry manifest. A config
    # digest is a content identity, not necessarily a runnable Docker image ID
    # under containerd-backed image stores.
    probed = probe_image(identity.immutable_ref, str(manifest["language"]))
    lock_hex = lock_manifest_sha256(fixture_dir / str(environment["lock_manifest"]))
    components = {
        "base_image_digest": str(environment["base_image_digest"]),
        "prepared_image_manifest_digest": identity.manifest_digest,
        "prepared_image_config_digest": identity.config_digest,
        "os_release_id": probed.os_release_id,
        "os_release_version_id": probed.os_release_version_id,
        "primary_runtime_version": probed.primary_runtime_version,
        "package_manager_version": probed.package_manager_version,
        "lock_manifest_sha256": lock_hex,
        "arch": probed.arch,
    }
    assert set(components) == set(CURRENT_COMPONENTS), (
        "the probe must cover every current environment component"
    )

    rebuilt = environment_fingerprint(components, contract="current")
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

    observations = [
        Observation(
            name="base_image_digest",
            value=str(environment["base_image_digest"]),
            why_unverified=(
                "the rebuild recipe consumes this registry pin, but this check does not "
                "resolve the base image"
            ),
        )
    ]
    if not online:
        observations.append(
            Observation(
                name="prepared_image_manifest_digest",
                value=identity.manifest_digest,
                why_unverified=(
                    "offline mode does not contact the registry; use --online to verify "
                    "the manifest and anonymous pull"
                ),
            )
        )

    return EnvironmentReport(
        fixture_id=str(manifest["fixture_id"]),
        fixture_version=str(manifest["fixture_version"]),
        checks=tuple(checks),
        observations=tuple(observations),
        image_identity=identity,
        observed_manifest_digest=observed_manifest,
        observed_config_digest=observed_config,
    )
