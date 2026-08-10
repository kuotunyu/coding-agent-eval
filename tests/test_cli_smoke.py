"""CLI skeleton smoke tests.

The CLI is the single entry point for every subsystem, so its contract is fixed
here before any subsystem exists: one console script, a stable version string,
and subcommands that fail loudly rather than silently doing nothing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from coding_agent_eval import BENCHMARK_VERSION
from coding_agent_eval.cli import build_parser, main, subcommand_names
from tests.conftest import REPO_ROOT, requires_checkout

EXPECTED_SUBCOMMANDS = {
    "validate",
    "fixture",
    "run",
    "evaluate",
    "sanitize",
    "store",
    "hygiene",
    "release",
}


def test_benchmark_version_is_semver() -> None:
    parts = BENCHMARK_VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts), BENCHMARK_VERSION


def test_parser_exposes_every_planned_subcommand() -> None:
    assert set(subcommand_names()) == EXPECTED_SUBCOMMANDS


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_version_prints_benchmark_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert BENCHMARK_VERSION in capsys.readouterr().out


@pytest.mark.parametrize("command", ["sanitize"])
def test_an_unimplemented_subcommand_exits_two_not_zero(command: str) -> None:
    """A stub must not look like success. Exit 2 keeps CI honest.

    Everything else is now wired — `validate`, `evaluate`, `store`, `hygiene`,
    `fixture`, and `run` — so `sanitize` is the last one this covers.
    """
    assert main([command]) == 2


def test_evaluate_replay_requires_run_dir_fixture_and_bugs() -> None:
    """`--fixture`/`--bugs` became optional at the argparse level once `export`
    and `import` needed the flag names to mean something else for them; replay
    itself must still refuse to proceed without them, explicitly."""
    assert main(["evaluate", "replay", "--ledger", "x.jsonl"]) == 2


def test_evaluate_export_requires_run_dir_fixture_dir_and_out() -> None:
    assert main(["evaluate", "export", "--ledger", "x.jsonl"]) == 2


def test_evaluate_import_requires_worksheet_keymap_and_adjudicator_id() -> None:
    assert main(["evaluate", "import", "--ledger", "x.jsonl"]) == 2


@pytest.mark.parametrize(
    ("argv", "action"),
    [
        (
            [
                "evaluate",
                "init",
                "--trace",
                "trace.jsonl",
                "--bugs",
                "bugs.json",
                "--fixture",
                "fixtures/fx-demo",
                "--review-set",
                "review-set",
                "--fixture-author-id",
                "kuotunyu",
                "--run-operator-id",
                "kuotunyu",
                "--primary-id",
                "kuotunyu",
                "--independent-id",
                "reviewer-b",
            ],
            "init",
        ),
        (
            [
                "evaluate",
                "export",
                "--review-set",
                "review-set",
                "--slot",
                "primary",
                "--worksheet",
                "primary.txt",
                "--keymap",
                "primary.keymap.json",
            ],
            "export",
        ),
        (
            [
                "evaluate",
                "resolve-export",
                "--review-set",
                "review-set",
                "--worksheet",
                "resolver.txt",
                "--keymap",
                "resolver.keymap.json",
            ],
            "resolve-export",
        ),
        (
            [
                "evaluate",
                "resolve-import",
                "--review-set",
                "review-set",
                "--resolver-id",
                "reviewer-c",
                "--worksheet",
                "resolver.txt",
                "--keymap",
                "resolver.keymap.json",
            ],
            "resolve-import",
        ),
    ],
)
def test_dual_review_cli_contracts_parse(argv: list[str], action: str) -> None:
    assert build_parser().parse_args(argv).action == action


def test_evaluate_export_end_to_end_through_main(tmp_path: Path) -> None:
    """One real pass through `main()`, not just the underlying function — the
    argument wiring is what this test is actually for."""
    import json

    import yaml

    fixture_dir = tmp_path / "fx-demo"
    (fixture_dir / "tree" / "src").mkdir(parents=True)
    (fixture_dir / "tree" / "src" / "a.py").write_bytes(b"def f():\n    return True\n")
    (fixture_dir / "patches").mkdir()
    (fixture_dir / "patches" / "B-001.patch").write_bytes(
        b"--- a/src/a.py\n"
        b"+++ b/src/a.py\n"
        b"@@ -1,2 +1,2 @@\n"
        b" def f():\n"
        b"-    return True\n"
        b"+    return 1\n"
    )
    (fixture_dir / "bugs").mkdir()
    (fixture_dir / "bugs" / "B-001.yaml").write_text(
        yaml.safe_dump(
            {
                "bug_id": "fx-demo/B-001",
                "category": "correctness",
                "patch": "patches/B-001.patch",
                "localization": {
                    "primary": {"file": "src/a.py", "line_start": 2, "line_end": 2},
                    "line_tolerance": 0,
                    "acceptable_alternates": [],
                },
                "canonical_claim": "Returns True instead of 1.",
                "canonical_root_cause": "A literal was changed.",
            }
        ),
        encoding="utf-8",
    )
    (fixture_dir / "fixture.yaml").write_text(
        yaml.safe_dump(
            {
                "fixture_id": "fx-demo",
                "fixture_version": "1.0.0",
                "language": "python",
                "bugs": ["fx-demo/B-001"],
            }
        ),
        encoding="utf-8",
    )

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text('{"bugs_in_snapshot": ["fx-demo/B-001"]}', encoding="utf-8")
    finding = {
        "id": "f1",
        "file": "src/a.py",
        "line_start": 2,
        "line_end": 2,
        "category": "correctness",
        "severity": "low",
        "claim": "f returns 1 instead of True.",
        "root_cause": "The literal was edited.",
        "evidence": "a.py line 2.",
        "suggested_verification": "Call f() and check the type.",
    }
    (run_dir / "findings.json").write_text(json.dumps({"findings": [finding]}), encoding="utf-8")

    exit_code = main(
        [
            "evaluate",
            "export",
            str(run_dir),
            "--fixture-dir",
            str(fixture_dir),
            "--ledger",
            str(tmp_path / "ledger.jsonl"),
            "--out",
            str(tmp_path / "worksheet.txt"),
        ]
    )
    assert exit_code == 0
    assert (tmp_path / "worksheet.txt").is_file()


def test_run_requires_a_fixture_and_an_output_directory() -> None:
    """`run` is wired now, so it argues about arguments rather than exiting 2.

    Both are required and neither has a default: a live run that wrote its
    evidence somewhere the operator did not choose would be a run whose output
    nobody finds.
    """
    with pytest.raises(SystemExit) as exc:
        main(["run"])
    assert exc.value.code == 2


def test_run_refuses_without_configuration(tmp_path: Path) -> None:
    """No key, no request. Exit 2 before anything is opened."""
    assert (
        main(
            [
                "run",
                str(tmp_path),
                "--out",
                str(tmp_path / "out"),
                "--env-file",
                str(tmp_path / "absent.env"),
            ]
        )
        == 2
    )


def test_fixture_verify_still_exits_two_for_a_path_that_is_not_a_fixture(
    tmp_path: Path,
) -> None:
    """Wired, so no longer a stub — but a missing manifest must still exit 2."""
    assert main(["fixture", "verify", str(tmp_path)]) == 2


def test_fixture_verify_runs_the_clean_suite_and_every_bug(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from coding_agent_eval.fixtures import witness

    calls: list[str] = []

    def fake_contract(*args: object, phase: str, **kwargs: object) -> witness.PhaseResult:
        calls.append(phase)
        return witness.PhaseResult(
            phase=phase, exit_code=0, stdout="205 passed", stderr="", timed_out=False
        )

    def fake_cycle(*args: object, bug_id: str, **kwargs: object) -> witness.CycleResult:
        calls.append(bug_id)
        return witness.CycleResult(
            bug_id=bug_id,
            phases=[
                witness.PhaseResult(
                    phase="clean", exit_code=0, stdout="ok", stderr="", timed_out=False
                )
            ],
        )

    monkeypatch.setattr(witness, "resolve_image_digest", lambda image: "sha256:" + "a" * 64)
    monkeypatch.setattr(witness, "run_contract", fake_contract)
    monkeypatch.setattr(witness, "run_g2_cycle", fake_cycle)

    fixture = REPO_ROOT / "fixtures" / "fx-taskq-py"
    assert main(["fixture", "verify", str(fixture)]) == 0
    assert calls == ["clean control", *[f"fx-taskq-py/B-{index:03d}" for index in range(1, 5)]]
    assert "clean control" in capsys.readouterr().out


def test_fixture_rebuild_exits_two_when_there_is_nothing_to_check(tmp_path: Path) -> None:
    """A gate that finds no fixtures must not report success on the empty set."""
    assert main(["fixture", "rebuild", str(tmp_path)]) == 2


def test_fixture_rebuild_exits_two_for_a_path_that_does_not_exist(tmp_path: Path) -> None:
    assert main(["fixture", "rebuild", str(tmp_path / "absent")]) == 2


@requires_checkout
def test_fixture_rebuild_passes_over_the_shipped_fixtures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gate G3 through the surface an operator actually runs."""
    assert main(["fixture", "rebuild", str(REPO_ROOT / "fixtures")]) == 0
    assert "G3 pass: 2 fixture(s)" in capsys.readouterr().out


@requires_checkout
def test_fixture_rebuild_accepts_a_single_fixture_directory(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["fixture", "rebuild", str(REPO_ROOT / "fixtures" / "fx-taskq-py")]) == 0
    assert "G3 pass: 1 fixture(s)" in capsys.readouterr().out


@requires_checkout
def test_fixture_online_and_json_options_are_environment_only() -> None:
    assert main(["fixture", "rebuild", str(REPO_ROOT / "fixtures"), "--online"]) == 2
    assert main(["fixture", "verify", str(REPO_ROOT / "fixtures"), "--json"]) == 2


def test_console_script_is_installed() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "coding_agent_eval", "--version"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert BENCHMARK_VERSION in proc.stdout
