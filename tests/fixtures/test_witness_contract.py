"""Witness contract execution (design spec §6.6, gate G2).

The unit tests here run without Docker. The ones marked `docker` run the real
five-step cycle against the canary bug, and are excluded from the default run.

The property under most of these is that the runner can *fail*. A gate that
only ever reports success is not a gate, and every way a contract can be wrong —
a hash that moved, a witness that cannot tell the trees apart, a command that
hangs — is checked here rather than trusted.
"""

from __future__ import annotations

import shlex
import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
import yaml

from coding_agent_eval.fixtures import witness
from coding_agent_eval.fixtures.leak_audit import audit_measured_tree
from coding_agent_eval.fixtures.witness import (
    Expectation,
    WitnessContract,
    WitnessError,
    container_command,
    container_workdir,
    resolve_image_digest,
    run_g2_cycle,
    verify_artifacts,
)
from coding_agent_eval.sandbox.profiles import (
    MEASURE,
    WITNESS,
    build_create_argv,
    build_run_argv,
)

FIXTURE = Path("fixtures/fx-taskq-py")
IMAGE = "sha256:" + "a" * 64

CONTRACT: dict[str, Any] = {
    "contract_version": "0.1",
    "prepare": [],
    "command": ["python3", "-m", "pytest", "-q", "witness/B-001"],
    "workdir": ".",
    "timeout_seconds": 120,
    "environment": {"TZ": "UTC", "LC_ALL": "C.UTF-8", "PYTHONPATH": "src"},
    "expected_clean": {
        "exit_code": 0,
        "stdout_contains": ["2 passed"],
        "stdout_not_contains": [],
    },
    "expected_mutated": {
        "exit_code": 1,
        "stdout_contains": ["2 failed"],
        "stdout_not_contains": [],
    },
    "artifacts": [],
    "deterministic": True,
    "overlay_target": "/workspace/witness",
}


def contract(**overrides: Any) -> WitnessContract:
    document = deepcopy(CONTRACT)
    document.update(overrides)
    return WitnessContract.from_document(document)


def test_clean_control_materialises_only_fixture_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = tmp_path / "fixture"
    (fixture / "tree/src").mkdir(parents=True)
    (fixture / "tree/src/app.ts").write_text("export {};\n", encoding="utf-8")
    (fixture / "tree/dist").mkdir()
    (fixture / "tree/dist/stale.js").write_text("stale\n", encoding="utf-8")
    (fixture / "witness").mkdir()
    observed: list[Path] = []

    def fake_run_contract(*args: object, tree: Path, **kwargs: object) -> witness.PhaseResult:
        observed.append(tree)
        assert (tree / "src/app.ts").is_file()
        assert not (tree / "dist").exists()
        return witness.PhaseResult(
            phase="clean control", exit_code=0, stdout="2 passed", stderr="", timed_out=False
        )

    monkeypatch.setattr(witness, "run_contract", fake_run_contract)

    result = witness.run_clean_control(
        fixture_dir=fixture,
        contract=contract(expected_mutated=None),
        image_digest=IMAGE,
    )

    assert result.ok
    assert len(observed) == 1
    assert not observed[0].exists()


# ------------------------------------------------------------- no bind mount


@pytest.mark.parametrize("builder", [build_run_argv, build_create_argv])
def test_generated_argv_never_contains_a_bind_mount(builder: Any) -> None:
    """A mount would put a host path inside a container running fixture code."""
    argv = builder(WITNESS, image=IMAGE, command=["true"])
    assert "-v" not in argv
    assert "--volume" not in argv
    assert not any(a.startswith("--mount") for a in argv)


def test_create_argv_is_a_create_and_keeps_the_container() -> None:
    """`--rm` would let the container vanish before its exit status is read."""
    argv = build_create_argv(WITNESS, image=IMAGE, command=["true"])
    assert argv[:2] == ["docker", "create"]
    assert "--rm" not in argv


def test_run_argv_still_cleans_up_after_itself() -> None:
    assert "--rm" in build_run_argv(WITNESS, image=IMAGE, command=["true"])


def test_witness_profile_keeps_the_network_off() -> None:
    """A witness that could reach out would not be deterministic."""
    assert WITNESS.network_none
    assert "--network" in build_create_argv(WITNESS, image=IMAGE, command=["true"])


def test_witness_profile_has_a_writable_root_and_says_why() -> None:
    """`docker cp` into a read-only rootfs is refused by the daemon.

    The alternative is a bind mount, which is the thing being avoided. This is
    pinned so nobody 'hardens' it back to read-only and breaks overlay delivery.
    """
    assert MEASURE.read_only_root
    assert not WITNESS.read_only_root


# ------------------------------------------------------------- expectations


def test_expectation_accepts_a_matching_result() -> None:
    expectation = Expectation(0, ("2 passed",), ("failed",))
    assert expectation.violations(0, "== 2 passed ==") == []


@pytest.mark.parametrize(
    ("exit_code", "stdout", "fragment"),
    [
        (1, "2 passed", "exit code"),
        (0, "0 passed", "lacks"),
        (0, "2 passed but 1 failed", "must not"),
    ],
)
def test_expectation_reports_each_way_a_result_is_wrong(
    exit_code: int, stdout: str, fragment: str
) -> None:
    expectation = Expectation(0, ("2 passed",), ("failed",))
    problems = expectation.violations(exit_code, stdout)
    assert problems and any(fragment in p for p in problems)


# --------------------------------------------------------- broken contracts


def test_a_contract_expecting_the_same_of_both_trees_witnesses_nothing() -> None:
    same = {"exit_code": 0, "stdout_contains": ["ok"], "stdout_not_contains": []}
    assert not contract(expected_clean=same, expected_mutated=same).distinguishes()


def test_a_contract_with_no_mutated_side_does_not_distinguish() -> None:
    """A clean-suite contract asserts health, not the presence of one defect."""
    document = deepcopy(CONTRACT)
    del document["expected_mutated"]
    assert not WitnessContract.from_document(document).distinguishes()


def test_the_canary_contract_distinguishes() -> None:
    bug = yaml.safe_load((FIXTURE / "bugs" / "B-001.yaml").read_text(encoding="utf-8"))
    assert WitnessContract.from_document(bug["witness"]).distinguishes()


def test_cycle_refuses_a_contract_that_cannot_distinguish(tmp_path: Path) -> None:
    same = {"exit_code": 0, "stdout_contains": ["ok"], "stdout_not_contains": []}
    result = run_g2_cycle(
        fixture_dir=tmp_path,
        bug_id="fx-demo/B-001",
        patch_path=tmp_path / "nope.patch",
        contract=contract(expected_clean=same, expected_mutated=same),
        image_tag="unused",
    )
    assert not result.ok
    assert any("witnesses nothing" in f for f in result.failures)
    assert result.phases == [], "no container should have been started"


# -------------------------------------------------------- artifact pinning


def test_artifact_hash_mismatch_is_reported(tmp_path: Path) -> None:
    (tmp_path / "witness").mkdir()
    (tmp_path / "witness" / "t.py").write_bytes(b"assert True\n")
    problems = verify_artifacts(
        tmp_path,
        contract(artifacts=[{"path": "witness/t.py", "sha256": "0" * 64}]),
    )
    assert problems and "hashes to" in problems[0]


def test_missing_artifact_is_reported(tmp_path: Path) -> None:
    problems = verify_artifacts(
        tmp_path, contract(artifacts=[{"path": "witness/gone.py", "sha256": "0" * 64}])
    )
    assert problems and "missing" in problems[0]


def test_the_canary_artifacts_are_pinned_correctly() -> None:
    """The committed hash must match the committed file, or G2 is unrunnable."""
    bug = yaml.safe_load((FIXTURE / "bugs" / "B-001.yaml").read_text(encoding="utf-8"))
    assert verify_artifacts(FIXTURE, WitnessContract.from_document(bug["witness"])) == []


# ------------------------------------------------------------------ workdir


@pytest.mark.parametrize(
    ("declared", "expected"),
    [(".", "/workspace"), ("", "/workspace"), ("src", "/workspace/src"), ("/other", "/other")],
)
def test_workdir_maps_onto_the_container(declared: str, expected: str) -> None:
    assert container_workdir(contract(workdir=declared)) == expected


# ------------------------------------------------------------------ prepare


def test_a_contract_without_prepare_runs_its_command_directly() -> None:
    """No shell in between when there is nothing to prepare."""
    assert container_command(contract(prepare=[])) == list(CONTRACT["command"])


def test_prepare_runs_before_the_command_and_gates_it() -> None:
    """A prepare that fails must stop the witness, not precede it.

    Joined with `&&` rather than `;`: a compile that failed silently would
    otherwise leave the witness reporting against a stale build, which looks
    like a result and is not one.
    """
    argv = container_command(contract(prepare=["npm", "run", "build"]))
    assert argv[:2] == ["sh", "-c"]
    assert "npm run build &&" in argv[2]
    assert argv[2].endswith(shlex.join(CONTRACT["command"]))


def test_prepare_arguments_are_quoted() -> None:
    """The prepare and command are argv, not shell text, so they get quoted."""
    argv = container_command(contract(prepare=["sh", "-c", "echo hi; rm -rf /"], command=["true"]))
    assert "rm -rf /'" in argv[2] or '"rm -rf /"' in argv[2]
    assert not argv[2].startswith("sh -c echo hi; rm -rf /")


# ------------------------------------------------------------------- images


def test_running_against_a_missing_image_names_the_image() -> None:
    with pytest.raises(WitnessError, match="not present locally"):
        resolve_image_digest("cae/definitely-not-built:0.0.0")


# --------------------------------------------------------------- answer leak


def test_the_measured_tree_does_not_contain_the_overlay() -> None:
    """The overlay is delivered to the container, never into the tree.

    A witness inside the measured tree states the answer, so this is checked
    against the committed tree as well as by G4's own rules.
    """
    tree = FIXTURE / "tree"
    assert not (tree / "witness").exists()
    bug = yaml.safe_load((FIXTURE / "bugs" / "B-001.yaml").read_text(encoding="utf-8"))
    assert (
        audit_measured_tree(
            tree,
            bug_ids=[bug["bug_id"]],
            claims=[bug["canonical_claim"], bug["canonical_root_cause"]],
        )
        == []
    )


# ------------------------------------------------------------ docker-backed


def _docker_available() -> bool:
    try:
        return (
            subprocess.run(
                ["docker", "version", "--format", "{{.Server.Version}}"],
                capture_output=True,
                timeout=30,
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


needs_docker = pytest.mark.skipif(not _docker_available(), reason="no Docker daemon")


@pytest.mark.docker
@needs_docker
def test_the_full_cycle_passes_on_the_canary() -> None:
    """Clean passes, mutated fails as declared, and clean passes again after revert."""
    manifest = yaml.safe_load((FIXTURE / "fixture.yaml").read_text(encoding="utf-8"))
    bug = yaml.safe_load((FIXTURE / "bugs" / "B-001.yaml").read_text(encoding="utf-8"))

    result = run_g2_cycle(
        fixture_dir=FIXTURE,
        bug_id=bug["bug_id"],
        patch_path=FIXTURE / bug["patch"],
        contract=WitnessContract.from_document(bug["witness"]),
        image_tag=manifest["environment"]["prepared_image_tag"],
    )

    assert result.ok, result.render()
    assert [p.phase for p in result.phases] == ["clean", "mutated", "clean (reverted)"]


@pytest.mark.docker
@needs_docker
def test_the_mutated_phase_actually_depends_on_the_patch() -> None:
    """With the mutated expectation swapped for the clean one, the cycle must fail.

    Otherwise the runner would pass whether or not the patch reached the
    container, which is the failure mode that makes a witness meaningless.
    """
    manifest = yaml.safe_load((FIXTURE / "fixture.yaml").read_text(encoding="utf-8"))
    bug = yaml.safe_load((FIXTURE / "bugs" / "B-001.yaml").read_text(encoding="utf-8"))
    document = deepcopy(bug["witness"])
    document["expected_mutated"] = {
        "exit_code": 0,
        "stdout_contains": ["2 passed"],
        "stdout_not_contains": [],
    }

    result = run_g2_cycle(
        fixture_dir=FIXTURE,
        bug_id=bug["bug_id"],
        patch_path=FIXTURE / bug["patch"],
        contract=WitnessContract.from_document(document),
        image_tag=manifest["environment"]["prepared_image_tag"],
    )

    assert not result.ok
    mutated = next(p for p in result.phases if p.phase == "mutated")
    assert mutated.violations


@pytest.mark.docker
@needs_docker
def test_a_hanging_contract_is_a_contract_failure_not_a_crash() -> None:
    manifest = yaml.safe_load((FIXTURE / "fixture.yaml").read_text(encoding="utf-8"))
    bug = yaml.safe_load((FIXTURE / "bugs" / "B-001.yaml").read_text(encoding="utf-8"))
    document = deepcopy(bug["witness"])
    document["command"] = ["python3", "-c", "import time; time.sleep(120)"]
    document["timeout_seconds"] = 5

    result = run_g2_cycle(
        fixture_dir=FIXTURE,
        bug_id=bug["bug_id"],
        patch_path=FIXTURE / bug["patch"],
        contract=WitnessContract.from_document(document),
        image_tag=manifest["environment"]["prepared_image_tag"],
    )

    assert not result.ok
    assert result.phases[0].timed_out
