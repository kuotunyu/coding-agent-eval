"""Sandbox profiles (design spec §9.1, §9.2).

These tests check the *arguments* the harness would pass to Docker. That proves
intent, not behaviour: a flag can be present and still not do what it claims.
Gate H2 runs the container and observes what actually happens, and until it
passes the isolation properties remain design requirements (spec §9.5).

Checking the argv is still worth doing, because it catches the failure this
project is most likely to have — a flag quietly dropped during a refactor, where
nothing breaks and the sandbox simply stops being one.
"""

from __future__ import annotations

import dataclasses

import pytest

from coding_agent_eval.sandbox.profiles import (
    MEASURE,
    PREPARE,
    ProfileError,
    SandboxProfile,
    build_run_argv,
)

DIGEST = "sha256:" + "a" * 64
TAG_ONLY = "cae-fx-taskq-py:1.0.0"


def measure_argv(**kwargs: object) -> list[str]:
    return build_run_argv(MEASURE, image=DIGEST, command=["true"], **kwargs)  # type: ignore[arg-type]


def prepare_argv(**kwargs: object) -> list[str]:
    return build_run_argv(PREPARE, image=DIGEST, command=["true"], **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------- measure profile


@pytest.mark.parametrize(
    "flag",
    [
        "--network",
        "--cap-drop",
        "--security-opt",
        "--read-only",
        "--pids-limit",
        "--memory",
        "--cpus",
        "--user",
        "--tmpfs",
    ],
)
def test_measure_argv_carries_every_required_flag(flag: str) -> None:
    assert flag in measure_argv(), flag


def test_measure_disables_the_network() -> None:
    argv = measure_argv()
    assert argv[argv.index("--network") + 1] == "none"


def test_measure_drops_all_capabilities() -> None:
    argv = measure_argv()
    assert argv[argv.index("--cap-drop") + 1] == "ALL"


def test_measure_forbids_privilege_escalation() -> None:
    argv = measure_argv()
    assert argv[argv.index("--security-opt") + 1] == "no-new-privileges"


def test_measure_runs_as_a_non_root_user() -> None:
    argv = measure_argv()
    uid = argv[argv.index("--user") + 1].split(":")[0]
    assert uid != "0"
    assert int(uid) > 0


def test_measure_uses_no_host_bind_mount() -> None:
    """A bind mount would hand the container a path into the host filesystem."""
    argv = measure_argv()
    assert "-v" not in argv
    assert "--volume" not in argv
    assert not any(arg.startswith("--mount") for arg in argv)


def test_measure_removes_the_container_afterwards() -> None:
    assert "--rm" in measure_argv()


def test_the_image_is_the_last_argument_before_the_command() -> None:
    argv = build_run_argv(MEASURE, image=DIGEST, command=["python3", "-V"])
    assert argv[-3:] == [DIGEST, "python3", "-V"]


# ------------------------------------------------------- prepare profile


def test_prepare_leaves_the_network_on() -> None:
    """Dependencies have to be fetched somewhere, and this is that somewhere."""
    assert "--network" not in prepare_argv()


def test_prepare_is_still_non_root_and_capability_dropped() -> None:
    """Network access is the only thing prepare relaxes."""
    argv = prepare_argv()
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--user") + 1].split(":")[0] != "0"


def test_prepare_is_writable_because_installing_requires_it() -> None:
    assert "--read-only" not in prepare_argv()


def test_the_two_profiles_are_distinguishable_by_name() -> None:
    assert MEASURE.name == "measure"
    assert PREPARE.name == "prepare"
    assert MEASURE.name != PREPARE.name


# --------------------------------------------------------- image pinning


def test_a_tag_only_image_reference_is_rejected() -> None:
    """A tag can be repointed, so a result taken against one is unreproducible."""
    with pytest.raises(ProfileError, match="digest"):
        build_run_argv(MEASURE, image=TAG_ONLY, command=["true"])


def test_a_digest_reference_is_accepted() -> None:
    assert DIGEST in measure_argv()


def test_a_repository_qualified_digest_is_accepted() -> None:
    reference = f"ghcr.io/example/cae-fixture@{DIGEST}"
    assert reference in build_run_argv(MEASURE, image=reference, command=["true"])


def test_a_malformed_digest_is_rejected() -> None:
    with pytest.raises(ProfileError):
        build_run_argv(MEASURE, image="sha256:tooshort", command=["true"])


# ------------------------------------------------------------- overrides


def test_resource_limits_are_overridable_per_run() -> None:
    argv = build_run_argv(
        MEASURE, image=DIGEST, command=["true"], memory="512m", cpus="1", pids_limit=64
    )
    assert argv[argv.index("--memory") + 1] == "512m"
    assert argv[argv.index("--cpus") + 1] == "1"
    assert argv[argv.index("--pids-limit") + 1] == "64"


def test_an_empty_command_is_rejected() -> None:
    """A container with nothing to run is a configuration mistake, not a no-op."""
    with pytest.raises(ProfileError, match="command"):
        build_run_argv(MEASURE, image=DIGEST, command=[])


def test_environment_variables_are_passed_as_pairs() -> None:
    argv = build_run_argv(
        MEASURE, image=DIGEST, command=["true"], environment={"TZ": "UTC", "LC_ALL": "C.UTF-8"}
    )
    pairs = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--env"]
    assert set(pairs) == {"TZ=UTC", "LC_ALL=C.UTF-8"}


def test_environment_variables_are_ordered_deterministically() -> None:
    """Sorted, not insertion-ordered: the argv is hashed into the run fingerprint."""
    forward = build_run_argv(
        MEASURE, image=DIGEST, command=["true"], environment={"TZ": "UTC", "LC_ALL": "C"}
    )
    backward = build_run_argv(
        MEASURE, image=DIGEST, command=["true"], environment={"LC_ALL": "C", "TZ": "UTC"}
    )
    assert forward == backward


def test_a_profile_is_immutable() -> None:
    """A run must not be able to weaken the profile it was given."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        MEASURE.network_none = False  # type: ignore[misc]


def test_custom_profiles_must_declare_every_property() -> None:
    with pytest.raises(TypeError):
        SandboxProfile(name="incomplete")  # type: ignore[call-arg]
