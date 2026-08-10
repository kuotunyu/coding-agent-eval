"""Environment identity, re-derived rather than trusted (design spec §9.4).

Two halves, and only one of them needs Docker.

**Frozen spellings, checkable anywhere.** The recorded fingerprints depend on
exactly how each component is written — a bare `3.12.13` for the runtime, but
`pip 25.0.1` for the package manager. That asymmetry is a wart, and it is load
bearing: changing it would alter the identity of every environment in the
repository while making no environment different. The tests that pin it
reconstruct both real fingerprints from raw version strings, so they fail on any
machine if the rule drifts, daemon or no daemon.

**Re-derivation, which does.** Run the prepared image, read what is in it, and
compare. Each drift is introduced deliberately — a mistyped digest, an edited
lock manifest, a recipe that stopped pinning its base — so every check is
observed failing rather than only passing.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest
import yaml

from coding_agent_eval.fixtures.environment import (
    RUNTIMES,
    EnvironmentCheckError,
    manager_version,
    recipe_pins_the_base,
    run_environment_check,
    runtime_version,
)
from coding_agent_eval.sandbox.fingerprint import COMPONENTS, environment_fingerprint
from tests.conftest import DOCKER_AVAILABLE, REPO_ROOT

FIXTURE_IDS = ["fx-taskq-py", "fx-ledger-ts"]

requires_docker = pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker daemon is not reachable")

#: The raw strings each image actually emits, and what they must normalise to.
#:
#: Recorded here so the normalisation is pinned by example rather than by
#: restating the implementation. `pip --version` really does append its install
#: path, and `npm --version` really does print a bare number.
RAW_VERSIONS = {
    "fx-taskq-py": {
        "runtime_raw": "Python 3.12.13",
        "manager_raw": "pip 25.0.1 from /usr/local/lib/python3.12/site-packages/pip (python 3.12)",
        "runtime": "3.12.13",
        "manager": "pip 25.0.1",
    },
    "fx-ledger-ts": {
        "runtime_raw": "v22.23.2",
        "manager_raw": "10.9.8",
        "runtime": "22.23.2",
        "manager": "npm 10.9.8",
    },
}


def manifest_of(fixture_id: str) -> dict:
    path = REPO_ROOT / "fixtures" / fixture_id / "fixture.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def partial_copy(fixture_id: str, destination: Path) -> Path:
    """Copy only what the check reads: the manifest and `env/`.

    The working tree of fx-ledger-ts carries `node_modules`, and copying it to
    mutate one line of YAML would make these tests slow enough to skip.
    """
    source = REPO_ROOT / "fixtures" / fixture_id
    destination.mkdir(parents=True)
    shutil.copy2(source / "fixture.yaml", destination / "fixture.yaml")
    shutil.copytree(source / "env", destination / "env")
    return destination


def edit_environment(fixture_dir: Path, key: str, value: object) -> None:
    path = fixture_dir / "fixture.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["environment"][key] = value
    path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8", newline="\n")


def failed_names(report: object) -> set[str]:
    return {check.name for check in report.failures}  # type: ignore[attr-defined]


def local_baseline_failures(fixture_id: str = "fx-taskq-py") -> set[str]:
    """The official image may be unavailable; a local rebuild has a new bit identity."""
    return failed_names(run_environment_check(REPO_ROOT / "fixtures" / fixture_id))


# ------------------------------------------------- frozen component spellings


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_the_raw_version_strings_normalise_as_recorded(fixture_id: str) -> None:
    """Pinned by example. `Python 3.12.13` loses its name; `10.9.8` gains one."""
    case = RAW_VERSIONS[fixture_id]
    manager = RUNTIMES[manifest_of(fixture_id)["language"]].manager

    assert runtime_version(case["runtime_raw"]) == case["runtime"]
    assert manager_version(case["manager_raw"], manager) == case["manager"]


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_the_recorded_fingerprint_is_reconstructible_without_docker(fixture_id: str) -> None:
    """The strongest freeze available offline: rebuild the real recorded value.

    Every input is either in the manifest, on disk, or a raw version string this
    file records. If the normalisation rule drifts, this fails on any machine —
    which matters, because the rule is the kind of thing a later reader would be
    tempted to tidy up.
    """
    fixture_dir = REPO_ROOT / "fixtures" / fixture_id
    manifest = manifest_of(fixture_id)
    environment = manifest["environment"]
    case = RAW_VERSIONS[fixture_id]
    manager = RUNTIMES[manifest["language"]].manager

    lock = (fixture_dir / environment["lock_manifest"]).read_bytes()
    rebuilt = environment_fingerprint(
        {
            "base_image_digest": environment["base_image_digest"],
            "prepared_image_digest": environment["prepared_image_digest"],
            "os_release_id": "debian",
            "os_release_version_id": "12",
            "primary_runtime_version": runtime_version(case["runtime_raw"]),
            "package_manager_version": manager_version(case["manager_raw"], manager),
            "lock_manifest_sha256": hashlib.sha256(lock).hexdigest(),
            "arch": "linux/amd64",
        }
    )
    assert rebuilt == environment["fingerprint"]


def test_the_lock_hash_is_bare_hex_not_a_prefixed_digest() -> None:
    """A `sha256:` prefix here would change every fingerprint. Stated so it cannot creep in."""
    fixture_dir = REPO_ROOT / "fixtures" / "fx-taskq-py"
    manifest = manifest_of("fx-taskq-py")
    lock = (fixture_dir / manifest["environment"]["lock_manifest"]).read_bytes()
    bare = hashlib.sha256(lock).hexdigest()

    prefixed = environment_fingerprint(
        {
            **dict.fromkeys(COMPONENTS, "x"),
            "lock_manifest_sha256": f"sha256:{bare}",
        }
    )
    unprefixed = environment_fingerprint(
        {**dict.fromkeys(COMPONENTS, "x")} | {"lock_manifest_sha256": bare}
    )
    assert prefixed != unprefixed


def test_an_empty_version_string_is_refused_rather_than_hashed() -> None:
    """A blank component would silently make two environments look identical."""
    with pytest.raises(EnvironmentCheckError):
        runtime_version("   ")
    with pytest.raises(EnvironmentCheckError):
        manager_version("", "pip")


# ------------------------------------------------------------ recipe pinning


@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_the_shipped_recipes_pin_their_base(fixture_id: str) -> None:
    recipe = REPO_ROOT / "fixtures" / fixture_id / "env" / "Dockerfile"
    assert recipe_pins_the_base(recipe).ok


def test_a_recipe_naming_a_floating_tag_fails(tmp_path: Path) -> None:
    """The failure the recorded base digest exists to prevent."""
    recipe = tmp_path / "Dockerfile"
    recipe.write_text("FROM python:3.12-slim-bookworm\nRUN true\n", encoding="utf-8")

    check = recipe_pins_the_base(recipe)
    assert not check.ok
    assert "python:3.12-slim-bookworm" in check.actual


def test_a_recipe_that_never_declares_the_argument_fails(tmp_path: Path) -> None:
    recipe = tmp_path / "Dockerfile"
    recipe.write_text("FROM python@${BASE_DIGEST}\n", encoding="utf-8")

    check = recipe_pins_the_base(recipe)
    assert not check.ok
    assert "ARG BASE_DIGEST" in check.actual


def test_a_missing_recipe_fails(tmp_path: Path) -> None:
    assert not recipe_pins_the_base(tmp_path / "absent").ok


# ------------------------------------------------------------- re-derivation


@requires_docker
@pytest.mark.docker
@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_the_shipped_fixtures_reproduce_their_recorded_fingerprint(fixture_id: str) -> None:
    """The gate. Every other test here proves it can fail."""
    report = run_environment_check(REPO_ROOT / "fixtures" / fixture_id)
    if failed_names(report) == {"prepared_image_digest"}:
        pytest.skip("local tag is a best-effort rebuild, not the recorded prepared image")
    assert report.ok, report.render()
    assert {check.name for check in report.checks} == {
        "prepared_image_digest",
        "recipe_pins_the_base",
        "fingerprint",
    }


@requires_docker
@pytest.mark.docker
@pytest.mark.parametrize("fixture_id", FIXTURE_IDS)
def test_a_local_rebuild_still_matches_every_runtime_component(fixture_id: str) -> None:
    """A new image digest is a publication blocker, not permission to skip runtime checks."""
    report = run_environment_check(REPO_ROOT / "fixtures" / fixture_id)

    assert failed_names(report) <= {"prepared_image_digest"}, report.render()
    fingerprint = next(check for check in report.checks if check.name == "fingerprint")
    assert fingerprint.ok, report.render()


@requires_docker
@pytest.mark.docker
def test_the_base_digest_is_reported_as_unverified_not_as_a_pass(tmp_path: Path) -> None:
    """It cannot be checked offline, so it must not render as a check that passed.

    A reader skimming a green report would take a passing row as confirmed. The
    base digest is an *input* to the fingerprint, taken from the same manifest
    the fingerprint is compared against, so it is reported and labelled instead.
    """
    report = run_environment_check(REPO_ROOT / "fixtures" / "fx-taskq-py")
    assert "base_image_digest" not in {check.name for check in report.checks}

    observed = {observation.name for observation in report.observations}
    assert observed == {"base_image_digest"}
    assert "unverified" in report.render()


@requires_docker
@pytest.mark.docker
def test_a_fingerprint_that_drifted_from_the_image_fails(tmp_path: Path) -> None:
    fixture_dir = partial_copy("fx-taskq-py", tmp_path / "fx-taskq-py")
    edit_environment(fixture_dir, "fingerprint", "sha256:" + "0" * 64)

    report = run_environment_check(fixture_dir)
    assert failed_names(report) == local_baseline_failures() | {"fingerprint"}


@requires_docker
@pytest.mark.docker
def test_a_mistyped_prepared_digest_fails(tmp_path: Path) -> None:
    """The case that would otherwise pass every gate in the repository."""
    fixture_dir = partial_copy("fx-taskq-py", tmp_path / "fx-taskq-py")
    edit_environment(fixture_dir, "prepared_image_digest", "sha256:" + "1" * 64)

    report = run_environment_check(fixture_dir)
    assert failed_names(report) == {"prepared_image_digest", "fingerprint"}, (
        "the fingerprint covers the digest, so it must move too"
    )


@requires_docker
@pytest.mark.docker
def test_editing_the_lock_manifest_changes_the_fingerprint(tmp_path: Path) -> None:
    """The lock manifest is hashed into the identity, so touching it is a new environment."""
    fixture_dir = partial_copy("fx-taskq-py", tmp_path / "fx-taskq-py")
    lock = fixture_dir / "env" / "env.lock.json"
    lock.write_bytes(lock.read_bytes() + b"\n")

    report = run_environment_check(fixture_dir)
    assert failed_names(report) == local_baseline_failures() | {"fingerprint"}


@requires_docker
@pytest.mark.docker
def test_a_recipe_that_stopped_pinning_is_caught_even_when_the_fingerprint_still_matches(
    tmp_path: Path,
) -> None:
    """The two are independent, which is why both are checked.

    Rewriting the recipe does not touch any fingerprint component, so without
    this check a build that had quietly gone back to a floating tag would report
    a perfectly reproducible environment identity.
    """
    fixture_dir = partial_copy("fx-taskq-py", tmp_path / "fx-taskq-py")
    (fixture_dir / "env" / "Dockerfile").write_text(
        "FROM python:3.12-slim-bookworm\nRUN true\n", encoding="utf-8"
    )

    report = run_environment_check(fixture_dir)
    assert failed_names(report) == local_baseline_failures() | {"recipe_pins_the_base"}


@requires_docker
@pytest.mark.docker
def test_an_absent_prepared_image_cannot_be_checked(tmp_path: Path) -> None:
    """Not a failing check: the gate has no subject, and says so."""
    fixture_dir = partial_copy("fx-taskq-py", tmp_path / "fx-taskq-py")
    edit_environment(fixture_dir, "prepared_image_tag", "cae/definitely-absent:0.0.0")

    with pytest.raises(EnvironmentCheckError, match="not present locally"):
        run_environment_check(fixture_dir)


@requires_docker
@pytest.mark.docker
def test_an_unknown_language_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    """Probing the wrong binary would report a version for something unused."""
    fixture_dir = partial_copy("fx-taskq-py", tmp_path / "fx-taskq-py")
    path = fixture_dir / "fixture.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["language"] = "brainfuck"
    path.write_text(yaml.safe_dump(document, sort_keys=True), encoding="utf-8", newline="\n")

    with pytest.raises(EnvironmentCheckError, match="no probe for language"):
        run_environment_check(fixture_dir)
