"""Witness contract execution — gate G2 (design spec §6.6, plan C3).

A bug manifest is a claim. The witness contract is what turns it into evidence:
a command whose observable result differs between the clean tree and the mutated
one. Without it a bug is an assertion that some code is wrong, and a benchmark
built on assertions measures its author's confidence rather than an agent.

The cycle is five steps, and the last one is the one people leave out:

1. the clean contract passes on the clean tree
2. the patch applies
3. the mutated contract produces the declared `expected_mutated`
4. the patch reverts
5. the clean contract passes *again*

Without step 5, a patch that also changed something else would still look
correct: the mutated result would be as declared, and nothing would have checked
that the tree came back.

**No bind mount, ever.** Both the tree and the witness overlay are delivered with
`docker cp` into a created-but-not-started container. A mount would put a host
path inside a container running fixture code, and would let the witness see
files that are not in the tree it is judging.

Delivering the tree rather than relying on the image's baked copy is not an
optimisation. The container runs what is in the image; patching a directory on
the host would change nothing the container ever sees. Copying each phase's tree
in makes the phase's subject explicit, and means one prepared image serves both
phases instead of an image build per bug.

Everything fails closed. A contract that cannot run, an artifact whose hash has
moved, a witness that reports the same result on both trees — each ends the
cycle as a failure rather than a warning.
"""

from __future__ import annotations

import hashlib
import shlex
import shutil
import subprocess
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from coding_agent_eval.fixtures.patcher import (
    PatchError,
    apply_patch,
    materialise,
    revert_patch,
)
from coding_agent_eval.sandbox.profiles import WITNESS, build_create_argv

_DOCKER_TIMEOUT = 120


class WitnessError(RuntimeError):
    """A contract could not be run, or the environment it needs is absent."""


@dataclass(frozen=True)
class Expectation:
    """What a contract says one run should look like."""

    exit_code: int
    stdout_contains: tuple[str, ...]
    stdout_not_contains: tuple[str, ...]

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> Expectation:
        return cls(
            exit_code=int(document["exit_code"]),
            stdout_contains=tuple(document.get("stdout_contains", ())),
            stdout_not_contains=tuple(document.get("stdout_not_contains", ())),
        )

    def violations(self, exit_code: int, stdout: str) -> list[str]:
        """Every way an observed result fails this expectation."""
        problems: list[str] = []
        if exit_code != self.exit_code:
            problems.append(f"exit code was {exit_code}, contract says {self.exit_code}")
        problems += [f"stdout lacks {n!r}" for n in self.stdout_contains if n not in stdout]
        problems += [
            f"stdout contains {n!r}, which it must not"
            for n in self.stdout_not_contains
            if n in stdout
        ]
        return problems


@dataclass(frozen=True)
class WitnessArtifact:
    path: str
    sha256: str


@dataclass(frozen=True)
class WitnessContract:
    """A parsed witness contract.

    `expected_mutated` is optional: a clean-suite contract has no mutated side,
    because it asserts a tree is healthy rather than that one defect is present.
    """

    command: tuple[str, ...]
    workdir: str
    timeout_seconds: int
    environment: dict[str, str]
    expected_clean: Expectation
    expected_mutated: Expectation | None
    artifacts: tuple[WitnessArtifact, ...]
    overlay_target: str
    prepare: tuple[str, ...] = ()

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> WitnessContract:
        mutated = document.get("expected_mutated")
        return cls(
            command=tuple(document["command"]),
            workdir=document.get("workdir", "."),
            timeout_seconds=int(document["timeout_seconds"]),
            environment=dict(document.get("environment", {})),
            expected_clean=Expectation.from_document(document["expected_clean"]),
            expected_mutated=Expectation.from_document(mutated) if mutated else None,
            artifacts=tuple(
                WitnessArtifact(path=a["path"], sha256=a["sha256"])
                for a in document.get("artifacts", ())
            ),
            overlay_target=document["overlay_target"],
            prepare=tuple(document.get("prepare", ())),
        )

    def distinguishes(self) -> bool:
        """Whether the contract can tell the two trees apart.

        A contract expecting the same of both proves nothing — it would pass on
        a patch that changed nothing. The schema enforces this as well (rule
        WITNESS_DISTINGUISHES); it is repeated here because a runner must not
        assume the validator has been run.
        """
        return self.expected_mutated is not None and self.expected_mutated != self.expected_clean


@dataclass
class PhaseResult:
    """One execution of a contract."""

    phase: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    violations: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations and not self.timed_out


@dataclass
class CycleResult:
    """The full five-step cycle for one bug."""

    bug_id: str
    phases: list[PhaseResult] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.phases) and not self.failures and all(p.ok for p in self.phases)

    def render(self) -> str:
        lines = [f"{self.bug_id}: {'PASS' if self.ok else 'FAIL'}"]
        for phase in self.phases:
            lines.append(
                f"  {phase.phase:<18} {'ok' if phase.ok else 'FAILED'} (exit {phase.exit_code})"
            )
            lines += [f"      - {v}" for v in phase.violations]
            if phase.timed_out:
                lines.append("      - timed out")
        lines += [f"  - {f}" for f in self.failures]
        return "\n".join(lines)


def verify_artifacts(fixture_dir: Path, contract: WitnessContract) -> list[str]:
    """Check every declared artifact still hashes to what the contract pinned.

    A witness whose content has drifted is no longer the witness that was
    reviewed, so a mismatch fails rather than being quietly re-pinned.
    """
    problems: list[str] = []
    for artifact in contract.artifacts:
        path = fixture_dir / artifact.path
        if not path.is_file():
            problems.append(f"witness artifact missing: {artifact.path}")
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != artifact.sha256:
            problems.append(
                f"witness artifact {artifact.path} hashes to {actual}, "
                f"contract pins {artifact.sha256}"
            )
    return problems


def resolve_image_digest(tag: str) -> str:
    """Resolve a local image tag to its digest.

    `build_create_argv` refuses a tag, so this has to happen anyway; doing it
    here means a missing image is reported as a missing image rather than as a
    malformed argument.
    """
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
            capture_output=True,
            text=True,
            timeout=_DOCKER_TIMEOUT,
        )
    except OSError as exc:
        raise WitnessError(
            f"prepared image {tag!r} is not present locally because the Docker CLI "
            f"is unavailable ({exc})"
        ) from exc
    if proc.returncode != 0:
        raise WitnessError(
            f"prepared image {tag!r} is not present locally; build it from the "
            f"fixture's rebuild_recipe first ({proc.stderr.strip()})"
        )
    return proc.stdout.strip()


def container_workdir(contract: WitnessContract) -> str:
    """Map the contract's tree-relative workdir onto the container."""
    if contract.workdir in (".", ""):
        return "/workspace"
    if contract.workdir.startswith("/"):
        return contract.workdir
    return f"/workspace/{contract.workdir}"


def container_command(contract: WitnessContract) -> list[str]:
    """The argv the container runs, including `prepare` if the contract has one.

    A contract that needs its tree compiled before the witness can run says so
    in `prepare`. There is one command per container, so the two are joined with
    `&&` in a shell rather than run as separate steps: a prepare that failed
    silently would otherwise leave the witness reporting against a stale build,
    which is worse than not running at all.

    Contracts with no prepare are executed directly, with no shell in between.
    """
    if not contract.prepare:
        return list(contract.command)
    return [
        "sh",
        "-c",
        f"{shlex.join(contract.prepare)} && {shlex.join(contract.command)}",
    ]


def _docker(argv: list[str], *, timeout: int = _DOCKER_TIMEOUT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)


def run_contract(
    contract: WitnessContract,
    *,
    image_digest: str,
    tree: Path,
    overlay: Path,
    phase: str,
    expectation: Expectation,
) -> PhaseResult:
    """Run a contract once and check it against `expectation`.

    The container is created, given the tree and the overlay, started, and
    removed in a `finally`. It is never `docker run`: a running container cannot
    be handed files without a mount.
    """
    argv = build_create_argv(
        WITNESS,
        image=image_digest,
        command=container_command(contract),
        workdir=container_workdir(contract),
        environment=contract.environment,
    )
    created = _docker(argv)
    if created.returncode != 0:
        raise WitnessError(f"could not create container: {created.stderr.strip()}")
    container = created.stdout.strip()

    try:
        # `/.` copies the directory's contents rather than the directory, so the
        # phase's tree lands over the image's baked copy instead of beside it.
        delivered = _docker(["docker", "cp", f"{tree}/.", f"{container}:/workspace"])
        if delivered.returncode != 0:
            raise WitnessError(f"could not deliver the tree: {delivered.stderr.strip()}")

        copied = _docker(["docker", "cp", str(overlay), f"{container}:{contract.overlay_target}"])
        if copied.returncode != 0:
            raise WitnessError(f"could not deliver the witness overlay: {copied.stderr.strip()}")

        timed_out = False
        try:
            started = _docker(
                ["docker", "start", "--attach", container], timeout=contract.timeout_seconds
            )
            exit_code, stdout, stderr = started.returncode, started.stdout, started.stderr
        except subprocess.TimeoutExpired:
            # A contract that hangs is a contract failure, not a crash of the
            # runner, so the cycle can report which phase hung.
            _docker(["docker", "kill", container])
            timed_out = True
            exit_code, stdout, stderr = -1, "", f"timed out after {contract.timeout_seconds}s"
    finally:
        _docker(["docker", "rm", "-f", container])

    result = PhaseResult(
        phase=phase, exit_code=exit_code, stdout=stdout, stderr=stderr, timed_out=timed_out
    )
    result.violations = [] if timed_out else expectation.violations(exit_code, stdout)
    return result


def run_clean_control(
    *, fixture_dir: Path, contract: WitnessContract, image_digest: str
) -> PhaseResult:
    """Run the full clean suite against materialised fixture bytes only."""
    overlay = fixture_dir / "witness"
    if not overlay.is_dir():
        raise WitnessError(f"witness overlay directory not found: {overlay}")
    workspace = Path(tempfile.mkdtemp(prefix="cae-clean-control-"))
    try:
        try:
            tree = materialise(fixture_dir / "tree", workspace / "tree")
        except PatchError as exc:
            raise WitnessError(f"could not materialise clean tree: {exc}") from exc
        return run_contract(
            contract,
            image_digest=image_digest,
            tree=tree,
            overlay=overlay,
            phase="clean control",
            expectation=contract.expected_clean,
        )
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def run_g2_cycle(
    *,
    fixture_dir: Path,
    bug_id: str,
    patch_path: Path,
    contract: WitnessContract,
    image_tag: str,
) -> CycleResult:
    """Run the five-step cycle for one bug.

    The tree is materialised into a temporary copy and patched there. The
    committed fixture is never patched in place: a failure part way through
    would leave it mutated, and the next run would measure a tree nobody meant
    to ship.
    """
    result = CycleResult(bug_id=bug_id)

    if not contract.distinguishes():
        result.failures.append(
            "contract expects the same result on both trees, so it witnesses nothing"
        )
        return result

    result.failures.extend(verify_artifacts(fixture_dir, contract))
    if result.failures:
        return result

    overlay = fixture_dir / "witness"
    if not overlay.is_dir():
        result.failures.append(f"witness overlay directory not found: {overlay}")
        return result

    mutated_expectation = contract.expected_mutated
    if mutated_expectation is None:  # pragma: no cover - distinguishes() proved it
        result.failures.append("contract has no expected_mutated")
        return result

    image_digest = resolve_image_digest(image_tag)
    workspace = Path(tempfile.mkdtemp(prefix="cae-witness-"))

    def phase(name: str, tree: Path, expectation: Expectation) -> PhaseResult:
        outcome = run_contract(
            contract,
            image_digest=image_digest,
            tree=tree,
            overlay=overlay,
            phase=name,
            expectation=expectation,
        )
        result.phases.append(outcome)
        return outcome

    try:
        tree = materialise(fixture_dir / "tree", workspace / "tree")

        phase("clean", tree, contract.expected_clean)

        try:
            apply_patch(tree, patch_path)
        except PatchError as exc:
            result.failures.append(f"patch did not apply: {exc}")
            return result

        phase("mutated", tree, mutated_expectation)

        try:
            revert_patch(tree, patch_path)
        except PatchError as exc:
            result.failures.append(f"patch did not revert: {exc}")
            return result

        phase("clean (reverted)", tree, contract.expected_clean)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    return result


__all__: Sequence[str] = (
    "CycleResult",
    "Expectation",
    "PhaseResult",
    "WitnessArtifact",
    "WitnessContract",
    "WitnessError",
    "container_command",
    "container_workdir",
    "resolve_image_digest",
    "run_clean_control",
    "run_contract",
    "run_g2_cycle",
    "verify_artifacts",
)
