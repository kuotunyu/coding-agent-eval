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

from coding_agent_eval import BENCHMARK_VERSION, __version__
from coding_agent_eval.cli import build_parser, main, manual_run_id, subcommand_names
from coding_agent_eval.trace.raw_store import RawStore
from coding_agent_eval.trace.sanitizer import sanitize_events
from tests.conftest import REPO_ROOT, requires_checkout
from tests.hygiene.corpus import SAMPLE_API_KEY
from tests.trace.test_sanitizer_failclosed import clean_events

EXPECTED_SUBCOMMANDS = {
    "validate",
    "fixture",
    "run",
    "evaluate",
    "sanitize",
    "store",
    "hygiene",
    "release",
    "suite",
}


def test_manual_run_ids_do_not_collide_when_output_parents_differ() -> None:
    first = manual_run_id(Path("runs/smoke/attempt-1/clean"))
    second = manual_run_id(Path("runs/smoke/attempt-2/clean"))

    assert first != second
    assert first == manual_run_id(Path("runs/smoke/attempt-1/clean"))
    assert first.startswith("manual-clean-")


def test_manual_run_id_does_not_disclose_output_parent_names() -> None:
    run_id = manual_run_id(Path("private-name/evidence/clean"))

    assert "private" not in run_id


def test_benchmark_version_is_semver() -> None:
    parts = BENCHMARK_VERSION.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts), BENCHMARK_VERSION


def test_software_and_benchmark_versions_are_distinct() -> None:
    assert __version__ == "0.1.1"
    assert BENCHMARK_VERSION == "0.1.0"


def test_parser_exposes_every_planned_subcommand() -> None:
    assert set(subcommand_names()) == EXPECTED_SUBCOMMANDS


def test_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_version_prints_both_identities(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out == "cae 0.1.1 (benchmark 0.1.0)\n"


def test_sanitize_projects_an_existing_raw_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / ".run-store"
    events = clean_events()
    raw = RawStore(root, run_id="run-001")
    for record in events:
        raw.append_record(record)
    expected = tmp_path / "expected.jsonl"
    sanitize_events(events, expected)
    output = tmp_path / "public" / "trace.jsonl"

    assert (
        main(
            [
                "sanitize",
                "run-001",
                "--store-root",
                str(root),
                "--out",
                str(output),
            ]
        )
        == 0
    )
    assert output.read_bytes() == expected.read_bytes()
    assert capsys.readouterr().out.strip() == f"sanitized {len(events)} events to {output}"


def write_cli_raw_run(root: Path, run_id: str, events: list[dict[str, object]]) -> None:
    raw = RawStore(root, run_id=run_id)
    for record in events:
        raw.append_record(record)


@pytest.mark.parametrize("run_id", ["..", "../escape", "a/b", r"a\b"])
def test_sanitize_rejects_an_invalid_run_id_as_operator_syntax(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    run_id: str,
) -> None:
    root = tmp_path / ".run-store"
    output = tmp_path / "trace.jsonl"

    assert main(["sanitize", run_id, "--store-root", str(root), "--out", str(output)]) == 2
    captured = capsys.readouterr()
    assert "RUN_ID must be one ordinary path segment" in captured.err
    assert run_id not in captured.err
    assert not output.exists()
    assert not root.exists()


@pytest.mark.parametrize("mode", ["missing", "empty", "malformed", "envelope"])
def test_sanitize_rejects_unusable_raw_runs_without_echoing_content(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
) -> None:
    root = tmp_path / ".run-store"
    run_id = "run-001"
    run_dir = root / run_id
    secret = "RAW-SECRET-DO-NOT-ECHO"
    if mode != "missing":
        run_dir.mkdir(parents=True)
        content = {
            "empty": "",
            "malformed": f'{{"payload":"{secret}"',
            "envelope": '{"seq":0,"secret":"RAW-SECRET-DO-NOT-ECHO"}',
        }[mode]
        (run_dir / "events.jsonl").write_text(content, encoding="utf-8")
    output = tmp_path / "trace.jsonl"

    assert main(["sanitize", run_id, "--store-root", str(root), "--out", str(output)]) == 1
    captured = capsys.readouterr()
    assert secret not in captured.out
    assert secret not in captured.err
    assert not output.exists()


def test_sanitize_rejects_an_output_inside_the_private_store(tmp_path: Path) -> None:
    root = tmp_path / ".run-store"
    write_cli_raw_run(root, "run-001", clean_events())
    output = root / "public" / "trace.jsonl"

    assert main(["sanitize", "run-001", "--store-root", str(root), "--out", str(output)]) == 2
    assert not output.exists()


def test_sanitize_refuses_to_replace_existing_output_without_force(tmp_path: Path) -> None:
    root = tmp_path / ".run-store"
    write_cli_raw_run(root, "run-001", clean_events())
    output = tmp_path / "trace.jsonl"
    original = b"existing public evidence\n"
    output.write_bytes(original)

    assert main(["sanitize", "run-001", "--store-root", str(root), "--out", str(output)]) == 2
    assert output.read_bytes() == original


def test_sanitize_force_replaces_existing_output_atomically(tmp_path: Path) -> None:
    root = tmp_path / ".run-store"
    events = clean_events()
    write_cli_raw_run(root, "run-001", events)
    output = tmp_path / "trace.jsonl"
    output.write_bytes(b"old\n")
    expected = tmp_path / "expected.jsonl"
    sanitize_events(events, expected)

    assert (
        main(
            [
                "sanitize",
                "run-001",
                "--store-root",
                str(root),
                "--out",
                str(output),
                "--force",
            ]
        )
        == 0
    )
    assert output.read_bytes() == expected.read_bytes()


def test_sanitize_privacy_failure_preserves_existing_output_with_force(tmp_path: Path) -> None:
    root = tmp_path / ".run-store"
    events = clean_events()
    events[1]["payload"]["excerpt"] = SAMPLE_API_KEY
    write_cli_raw_run(root, "run-001", events)
    output = tmp_path / "trace.jsonl"
    original = b"existing public evidence\n"
    output.write_bytes(original)

    assert (
        main(
            [
                "sanitize",
                "run-001",
                "--store-root",
                str(root),
                "--out",
                str(output),
                "--force",
            ]
        )
        == 1
    )
    assert output.read_bytes() == original
    assert sorted(output.parent.glob(f".{output.name}.*.tmp")) == []


def test_sanitize_unknown_field_leaves_no_new_or_temporary_output(tmp_path: Path) -> None:
    root = tmp_path / ".run-store"
    events = clean_events()
    events[1]["payload"]["brand_new_field"] = "unclassified"
    write_cli_raw_run(root, "run-001", events)
    output = tmp_path / "public" / "trace.jsonl"

    assert main(["sanitize", "run-001", "--store-root", str(root), "--out", str(output)]) == 1
    assert not output.exists()
    assert not output.parent.exists()


def test_sanitize_requires_run_id_and_output() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["sanitize"])
    assert exc.value.code == 2


def test_evaluate_replay_requires_run_dir_fixture_and_bugs() -> None:
    """`--fixture`/`--bugs` became optional at the argparse level once `export`
    and `import` needed the flag names to mean something else for them; replay
    itself must still refuse to proceed without them, explicitly."""
    assert main(["evaluate", "replay", "--ledger", "x.jsonl"]) == 2


def test_evaluate_replay_requires_exactly_one_decision_source() -> None:
    base = [
        "evaluate",
        "replay",
        "run",
        "--fixture",
        "fixture.json",
        "--bugs",
        "bugs.json",
    ]
    assert main(base) == 2
    assert main([*base, "--ledger", "ledger.jsonl", "--review-set", "review-set"]) == 2


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


@pytest.mark.parametrize("action", ["dry-run", "register", "run", "replay"])
def test_suite_cli_contracts_parse(action: str) -> None:
    arguments = ["suite", action, "--out", "suite-output"]
    if action == "dry-run":
        arguments += ["--tasks", "tasks", "--env-file", ".env"]
    elif action == "register":
        arguments += ["--plan", "plan.json"]
    else:
        arguments += ["--registration", "registration.json"]
    if action == "replay":
        arguments += ["--review-sets", "ledger/review-sets"]
    assert build_parser().parse_args(arguments).action == action


def test_release_publication_flags_parse() -> None:
    arguments = build_parser().parse_args(["release", "audit", "--publication", "--online"])

    assert arguments.publication is True
    assert arguments.online is True


def test_release_online_requires_publication() -> None:
    assert main(["release", "audit", "--online"]) == 2


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
    image_refs: list[str] = []

    def fake_contract(*args: object, phase: str, **kwargs: object) -> witness.PhaseResult:
        calls.append(phase)
        return witness.PhaseResult(
            phase=phase, exit_code=0, stdout="205 passed", stderr="", timed_out=False
        )

    def fake_cycle(
        *args: object, bug_id: str, image_tag: str, **kwargs: object
    ) -> witness.CycleResult:
        calls.append(bug_id)
        image_refs.append(image_tag)
        return witness.CycleResult(
            bug_id=bug_id,
            phases=[
                witness.PhaseResult(
                    phase="clean", exit_code=0, stdout="ok", stderr="", timed_out=False
                )
            ],
        )

    def fake_resolve(image: str) -> str:
        image_refs.append(image)
        return "sha256:" + "a" * 64

    monkeypatch.setattr(witness, "resolve_image_digest", fake_resolve)
    monkeypatch.setattr(witness, "run_contract", fake_contract)
    monkeypatch.setattr(witness, "run_g2_cycle", fake_cycle)

    fixture = REPO_ROOT / "fixtures" / "fx-taskq-py"
    assert main(["fixture", "verify", str(fixture)]) == 0
    assert calls == ["clean control", *[f"fx-taskq-py/B-{index:03d}" for index in range(1, 5)]]
    immutable_ref = (
        "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@"
        "sha256:fc4e636299244b23a04a57f02cba1ed84b2cd4919cdc248eb7cb9a495bc75fc3"
    )
    assert image_refs == [immutable_ref] * 5
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
