"""Regenerate the committed baseline runs under `runs/` (gate G9).

These artifacts were previously produced by hand, which made them evidence
nobody could check: a reader had no way to tell whether the committed numbers
were what the pipeline actually produces today. One command now rebuilds them,
and because the runs are deterministic, `git diff` after running it is either
empty or a real change.

**Deliberately the host backend.** The container backend produces identical
scores — `tests/test_e2e_baseline.py` asserts that under Docker — but making
these files require a daemon and a locally-built image to regenerate would trade
away the one property that makes them useful, which is that anyone can rebuild
them and compare. `tool_backend: host_process` in each file says which was used.

Usage:

    uv run python scripts/regenerate_runs.py
"""

from __future__ import annotations

from pathlib import Path

from coding_agent_eval.agent.baseline import high_noise, perfect
from coding_agent_eval.e2e import (
    CLEAN,
    MUTATED,
    Workspace,
    finding_for,
    load_fixture,
    run_snapshot,
    synthetic_ledger_for,
    write_results,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_IDS = ("fx-taskq-py", "fx-ledger-ts")

#: Matches `runs/README.md`, which explains why five findings on a clean tree is
#: a number that can be checked by hand.
NOISE_COUNT = 5


def main() -> int:
    for fixture_id in FIXTURE_IDS:
        fixture = load_fixture(REPO_ROOT / "fixtures" / fixture_id)
        with Workspace() as workspace:
            findings = [finding_for(bug, index=i) for i, bug in enumerate(fixture.bugs)]
            ledger = synthetic_ledger_for(fixture, findings, workspace / "ledger.jsonl")

            for snapshot, adapter in (
                (MUTATED, perfect([findings[0]])),
                (CLEAN, high_noise(count=NOISE_COUNT)),
            ):
                result = run_snapshot(
                    fixture,
                    adapter=adapter,
                    snapshot=snapshot,
                    ledger=ledger,
                    workspace=workspace,
                    bug_index=0,
                )
                destination = REPO_ROOT / "runs" / f"baseline-{fixture_id}-{snapshot}"
                path = write_results(result, destination)
                print(f"{path.relative_to(REPO_ROOT).as_posix()}  ({result.tool_backend})")

    print("\nRegenerated. A non-empty `git diff runs/` now means a real change.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
