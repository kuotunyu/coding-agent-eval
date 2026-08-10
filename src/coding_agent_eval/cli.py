"""`cae` command line entry point.

Subcommands are registered here as the sole surface for every subsystem. A
subcommand that is not yet wired exits 2 rather than 0: a stub that reports
success is worse than no stub at all, because CI would go green on nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from coding_agent_eval import BENCHMARK_VERSION

_SUBCOMMANDS: dict[str, str] = {
    "validate": "Validate fixture, bug, and artifact manifests against their schemas",
    "fixture": "Fixture lifecycle: checksum, patch, witness contracts, LOC",
    "run": "Execute an agent adapter against a fixture snapshot",
    "evaluate": "Score a run, manage the adjudication ledger, replay results",
    "sanitize": "Project a private run into a public artifact (fail-closed)",
    "store": "Inspect or prune the private raw evidence store",
    "hygiene": "Leak scanning and hygiene policy inspection",
    "release": "Audit local release artifacts and immutable contributor provenance",
}


def subcommand_names() -> tuple[str, ...]:
    """Registered subcommands, so callers need not reach into argparse internals."""
    return tuple(_SUBCOMMANDS)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cae",
        description=(
            "A ground-truth benchmark for measuring whether coding agents can discover "
            "known defects, how many unsupported findings they produce, and what "
            "resources they consume under reproducible sandbox conditions."
        ),
    )
    parser.add_argument("--version", action="version", version=BENCHMARK_VERSION)
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    for name, help_text in _SUBCOMMANDS.items():
        sub = subparsers.add_parser(name, help=help_text)
        if name == "hygiene":
            _add_hygiene_arguments(sub)
        elif name == "evaluate":
            sub.add_argument(
                "action",
                choices=("replay", "export", "import"),
                help=(
                    "replay: rescore a published run against a ledger. "
                    "export: build a blinded worksheet of a run's unruled candidate "
                    "pairs, for a human to adjudicate offline (no AI may fill it in). "
                    "import: read a filled-in worksheet back and append its rulings "
                    "to the formal ledger."
                ),
            )
            sub.add_argument(
                "run_dir",
                type=Path,
                nargs="?",
                help="replay/export: the run's evidence directory",
            )
            sub.add_argument("--fixture", type=Path, help="replay: a fixture-spec JSON")
            sub.add_argument("--bugs", type=Path, help="replay: a bug-set JSON")
            sub.add_argument(
                "--fixture-dir",
                type=Path,
                help="export: the fixture's own directory, e.g. fixtures/fx-taskq-py",
            )
            sub.add_argument("--ledger", type=Path, required=True, help="the ledger file")
            sub.add_argument(
                "--ledger-kind",
                choices=("formal", "synthetic"),
                default="formal",
                help="replay only: synthetic marks the result unpublishable",
            )
            sub.add_argument("--out", type=Path, help="export: where to write the worksheet")
            sub.add_argument(
                "--keymap",
                type=Path,
                help=(
                    "export: where to write the private key map (default: the "
                    "worksheet path with .keymap.json). import: where to read it "
                    "from. Never hand this file to whoever is adjudicating."
                ),
            )
            sub.add_argument("--worksheet", type=Path, help="import: the filled-in worksheet")
            sub.add_argument(
                "--adjudicator-id",
                help="import: who is ruling; must not start with SYNTHETIC-",
            )
        elif name == "store":
            sub.add_argument("action", choices=("prune",))
            sub.add_argument("--root", type=Path, default=Path(".run-store"))
            sub.add_argument("--retention-days", type=int, default=30)
        elif name == "release":
            sub.add_argument("action", choices=("audit",))
            sub.add_argument("--root", type=Path, default=Path("."))
            sub.add_argument("--check-git-history", action="store_true")
        elif name == "fixture":
            sub.add_argument(
                "action",
                choices=("verify", "rebuild", "environment"),
                help=(
                    "verify: gate G2, every bug's witness cycle (needs Docker). "
                    "rebuild: gate G3, re-export the committed tree and assert its "
                    "checksum and line count against the manifest (no Docker). "
                    "environment: spec §9.4, re-derive the environment fingerprint "
                    "from the prepared image (needs Docker)"
                ),
            )
            sub.add_argument(
                "fixture_dir",
                type=Path,
                help=("a fixture directory; rebuild also accepts a root holding several"),
            )
        elif name == "run":
            sub.add_argument("fixture_dir", type=Path, help="fixture directory to run against")
            sub.add_argument("--snapshot", choices=("clean", "mutated"), default="mutated")
            sub.add_argument("--bug-index", type=int, default=0)
            sub.add_argument("--out", type=Path, required=True, help="where to write evidence")
            sub.add_argument(
                "--isolate",
                metavar="IMAGE_DIGEST",
                help=(
                    "run the agent's tools inside the measure container, pinned to this "
                    "image digest; without it they run in this process"
                ),
            )
            sub.add_argument(
                "--env-file",
                type=Path,
                default=Path(".env"),
                help="read configuration from this file for names the shell does not set",
            )
            sub.add_argument(
                "--dry-run",
                action="store_true",
                help="validate configuration and print it redacted; make no request",
            )
        elif name == "validate":
            sub.add_argument(
                "root",
                type=Path,
                nargs="?",
                default=Path("fixtures"),
                help=("a fixture root, or a single fixture directory (default: fixtures/)"),
            )
        else:
            sub.add_argument("args", nargs=argparse.REMAINDER, help=argparse.SUPPRESS)
    return parser


def _run_validate(args: argparse.Namespace) -> int:
    from coding_agent_eval.schemas.fixture_dir import (
        FIXTURE_MANIFEST,
        validate_fixture_dir,
        validate_fixture_root,
    )

    root: Path = args.root
    if not root.is_dir():
        print(f"no such fixture root: {root}", file=sys.stderr)
        return 2

    # A path holding a manifest is one fixture, not a root of them. Without
    # this, `cae validate fixtures/fx-taskq-py` finds no `*/fixture.yaml` and
    # reports NO_FIXTURES — technically correct, and useless: validating one
    # fixture after touching it is the common case, and it is the command the
    # plan gives for verifying each bug.
    if (root / FIXTURE_MANIFEST).is_file():
        problems = validate_fixture_dir(root)
    else:
        problems = validate_fixture_root(root)
    if problems:
        for problem in problems:
            print(problem.render(), file=sys.stderr)
        print(f"\n{len(problems)} problem(s) in {root}", file=sys.stderr)
        return 1
    print(f"fixture validation clean: {root}")
    return 0


def _run_fixture_rebuild(args: argparse.Namespace) -> int:
    """Run gate G3: rebuild each fixture tree from HEAD and check it against its manifest."""
    from coding_agent_eval.fixtures.rebuild import RebuildError, run_g3
    from coding_agent_eval.schemas.fixture_dir import FIXTURE_MANIFEST, fixture_dirs

    target: Path = args.fixture_dir
    if not target.is_dir():
        print(f"no such path: {target}", file=sys.stderr)
        return 2

    # A path holding a manifest is one fixture; anything else is a root of them.
    directories = [target] if (target / FIXTURE_MANIFEST).is_file() else fixture_dirs(target)
    if not directories:
        # Checking nothing is not the same as everything checking out.
        print(f"no fixtures found under {target}", file=sys.stderr)
        return 2

    failed = 0
    for directory in directories:
        try:
            report = run_g3(directory)
        except RebuildError as exc:
            print(f"{directory.name}: G3 could not run\n  - {exc}", file=sys.stderr)
            failed += 1
            continue
        print(report.render(), file=sys.stdout if report.ok else sys.stderr)
        failed += 0 if report.ok else 1

    if failed:
        print(f"\n{failed} of {len(directories)} fixture(s) failed G3", file=sys.stderr)
        return 1
    print(f"\nG3 pass: {len(directories)} fixture(s) rebuilt and matching")
    return 0


def _run_fixture_environment(args: argparse.Namespace) -> int:
    """Re-derive each fixture's environment fingerprint from its prepared image (§9.4)."""
    from coding_agent_eval.fixtures.environment import (
        EnvironmentCheckError,
        run_environment_check,
    )
    from coding_agent_eval.schemas.fixture_dir import FIXTURE_MANIFEST, fixture_dirs

    target: Path = args.fixture_dir
    if not target.is_dir():
        print(f"no such path: {target}", file=sys.stderr)
        return 2

    directories = [target] if (target / FIXTURE_MANIFEST).is_file() else fixture_dirs(target)
    if not directories:
        print(f"no fixtures found under {target}", file=sys.stderr)
        return 2

    failed = 0
    for directory in directories:
        try:
            report = run_environment_check(directory)
        except EnvironmentCheckError as exc:
            print(f"{directory.name}: could not check the environment\n  - {exc}", file=sys.stderr)
            failed += 1
            continue
        print(report.render(), file=sys.stdout if report.ok else sys.stderr)
        failed += 0 if report.ok else 1

    if failed:
        print(f"\n{failed} of {len(directories)} fixture environment(s) failed", file=sys.stderr)
        return 1
    print(f"\nenvironment fingerprint re-derived for {len(directories)} fixture(s)")
    return 0


def _run_fixture(args: argparse.Namespace) -> int:
    """Run gate G2: every bug's witness cycle for one fixture."""
    import yaml

    from coding_agent_eval.fixtures.witness import (
        WitnessContract,
        WitnessError,
        resolve_image_digest,
        run_clean_control,
        run_g2_cycle,
    )

    fixture_dir: Path = args.fixture_dir
    manifest_path = fixture_dir / "fixture.yaml"
    if not manifest_path.is_file():
        print(f"no fixture manifest at {manifest_path}", file=sys.stderr)
        return 2

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    bug_ids: list[str] = list(manifest["bugs"])
    if not bug_ids:
        # Nothing to witness is not the same as everything witnessed. Saying so
        # is the difference between a gate and a green tick.
        print(f"{manifest['fixture_id']}: no bugs to verify", file=sys.stderr)
        return 2

    failed = 0
    image_tag = manifest["environment"]["prepared_image_tag"]
    clean_path = fixture_dir / manifest["clean_control"]["witness_suite"]
    try:
        clean_contract = WitnessContract.from_document(
            yaml.safe_load(clean_path.read_text(encoding="utf-8"))
        )
        clean_result = run_clean_control(
            fixture_dir=fixture_dir,
            contract=clean_contract,
            image_digest=resolve_image_digest(image_tag),
        )
    except (OSError, KeyError, WitnessError) as exc:
        print(f"clean control: FAIL\n  - {exc}", file=sys.stderr)
        failed += 1
    else:
        print(
            f"clean control: {'PASS' if clean_result.ok else 'FAIL'} "
            f"(exit {clean_result.exit_code})",
            file=sys.stdout if clean_result.ok else sys.stderr,
        )
        for violation in clean_result.violations:
            print(f"  - {violation}", file=sys.stderr)
        failed += 0 if clean_result.ok else 1

    for bug_id in bug_ids:
        name = bug_id.split("/")[-1]
        bug = yaml.safe_load((fixture_dir / "bugs" / f"{name}.yaml").read_text(encoding="utf-8"))
        try:
            result = run_g2_cycle(
                fixture_dir=fixture_dir,
                bug_id=bug_id,
                patch_path=fixture_dir / bug["patch"],
                contract=WitnessContract.from_document(bug["witness"]),
                image_tag=image_tag,
            )
        except WitnessError as exc:
            print(f"{bug_id}: FAIL\n  - {exc}", file=sys.stderr)
            failed += 1
            continue

        print(result.render(), file=sys.stderr if not result.ok else sys.stdout)
        failed += 0 if result.ok else 1

    if failed:
        print(f"\n{failed} of {len(bug_ids) + 1} witness contract(s) failed", file=sys.stderr)
        return 1
    print(
        f"\nG2 pass: clean control and {len(bug_ids)} witness cycle(s) in {manifest['fixture_id']}"
    )
    return 0


def _add_hygiene_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "action",
        choices=("leak-scan",),
        help="leak-scan: apply the tracked-file hygiene policy",
    )
    parser.add_argument(
        "--tracked",
        action="store_true",
        help="scan files under version control (what would actually be published)",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="repository root to scan (default: current directory)",
    )


def _run_hygiene(args: argparse.Namespace) -> int:
    from coding_agent_eval.hygiene.leak_scan import LeakScanError, scan_tracked_files
    from coding_agent_eval.hygiene.policy import TRACKED_FILE_POLICY

    if not args.tracked:
        print("cae hygiene leak-scan requires --tracked", file=sys.stderr)
        return 2
    try:
        findings = scan_tracked_files(args.root)
    except LeakScanError as exc:
        print(f"leak scan could not run: {exc}", file=sys.stderr)
        return 2

    if findings:
        for finding in findings:
            print(finding.render(), file=sys.stderr)
        print(f"\n{len(findings)} leak finding(s) in tracked files", file=sys.stderr)
        return 1
    print(f"tracked-file leak scan clean (policy {TRACKED_FILE_POLICY.version})")
    return 0


def _run_evaluate_replay(args: argparse.Namespace) -> int:
    from coding_agent_eval.evaluator.ledger import LedgerKind
    from coding_agent_eval.evaluator.metrics import EvaluationError
    from coding_agent_eval.evaluator.replay import replay_run

    if args.run_dir is None or args.fixture is None or args.bugs is None:
        print("replay needs run_dir, --fixture, and --bugs", file=sys.stderr)
        return 2

    trace = args.run_dir / "trace.jsonl"
    try:
        result = replay_run(
            trace_path=trace,
            fixture_path=args.fixture,
            bugs_path=args.bugs,
            ledger_path=args.ledger,
            ledger_kind=LedgerKind(args.ledger_kind),
        )
    except EvaluationError as exc:
        print(f"evaluation refused: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result.as_dict(), indent=2, sort_keys=True))
    if not result.publishable:
        print(
            "note: scored against a synthetic ledger, so these numbers validate the "
            "evaluator and describe no model",
            file=sys.stderr,
        )
    return 0


def _run_evaluate_export(args: argparse.Namespace) -> int:
    """Gate §8.3: build a blinded worksheet of a run's unruled candidate pairs."""
    from coding_agent_eval.adjudication import AdjudicationError, export_for_review

    if args.run_dir is None or args.fixture_dir is None or args.out is None:
        print("export needs run_dir, --fixture-dir, and --out", file=sys.stderr)
        return 2

    try:
        result = export_for_review(
            args.run_dir,
            fixture_dir=args.fixture_dir,
            ledger_path=args.ledger,
            out_path=args.out,
            keymap_path=args.keymap,
        )
    except AdjudicationError as exc:
        print(f"export refused: {exc}", file=sys.stderr)
        return 1

    if result.pending == 0:
        print(f"nothing pending: {result.already_ruled} candidate pair(s) already ruled, 0 remain")
        return 0

    print(f"{result.pending} pending pair(s), {result.already_ruled} already ruled")
    print(f"worksheet: {result.worksheet_path}")
    print(f"key map:   {result.keymap_path}  (private — do not hand this to the adjudicator)")
    print(
        "\nNo AI may fill this in. A person reads the worksheet, rules on each item, "
        "and returns it; `cae evaluate import` reads it back.",
        file=sys.stderr,
    )
    return 0


def _run_evaluate_import(args: argparse.Namespace) -> int:
    """Read a filled-in worksheet back and append its rulings to the formal ledger."""
    from datetime import UTC, datetime

    from coding_agent_eval.adjudication import AdjudicationError, apply_review
    from coding_agent_eval.evaluator.blinded_export import BlindingError
    from coding_agent_eval.evaluator.worksheet import WorksheetError

    if args.worksheet is None or args.keymap is None or args.adjudicator_id is None:
        print("import needs --worksheet, --keymap, and --adjudicator-id", file=sys.stderr)
        return 2

    try:
        result = apply_review(
            worksheet_path=args.worksheet,
            keymap_path=args.keymap,
            ledger_path=args.ledger,
            adjudicator_id=args.adjudicator_id,
            decided_at=datetime.now(UTC).date().isoformat(),
        )
    except (AdjudicationError, WorksheetError, BlindingError) as exc:
        print(f"import refused: {exc}", file=sys.stderr)
        return 1

    print(f"recorded {result.ruled} ruling(s) in {result.ledger_path}")
    return 0


def _run_run(args: argparse.Namespace) -> int:
    """Execute one live provider run. This is the command that spends money."""
    import os

    from coding_agent_eval.live import execute, write_evidence
    from coding_agent_eval.runconfig import (
        ConfigurationError,
        load_configuration,
        suspicious_variables,
    )

    for name in suspicious_variables(dict(os.environ)):
        # A misspelled budget leaves a run unbounded while its operator believes
        # otherwise, so an unread CAE_ variable is worth a line on stderr.
        print(f"warning: {name} is set but nothing reads it; check the spelling", file=sys.stderr)

    try:
        configuration = load_configuration(dotenv_path=args.env_file)
    except ConfigurationError as exc:
        print(f"not configured: {exc}", file=sys.stderr)
        return 2

    if args.dry_run:
        print(json.dumps(configuration.redacted(), indent=2, sort_keys=True))
        print("\ndry run: configuration is valid and no request was made", file=sys.stderr)
        return 0

    if not args.fixture_dir.is_dir():
        print(f"no such fixture directory: {args.fixture_dir}", file=sys.stderr)
        return 2

    run = execute(
        args.fixture_dir,
        configuration=configuration,
        snapshot=args.snapshot,
        bug_index=args.bug_index,
        isolate_image=args.isolate,
        run_id=args.out.name,
        raw_store_root=Path(".run-store"),
    )
    directory = write_evidence(run, args.out)

    usage = run.usage_total()
    failure = run.failure
    if failure:
        # Printed first and to stderr, because it is the only thing that matters
        # about a failed run and it is what the operator has to act on.
        print("provider failure:", file=sys.stderr)
        for name in ("exception", "status", "type", "code", "param", "message", "body_keys"):
            if name in failure:
                print(f"  {name:<10} {failure[name]}", file=sys.stderr)
        print("", file=sys.stderr)

    print(f"run {run.run_id}: {run.result.termination_reason.value}")
    print(f"  findings   {len(run.result.findings)}")
    print(f"  tool calls {run.result.tool_calls} in {run.result.steps} step(s)")
    print(
        f"  tokens     {usage['input_tokens']} in "
        f"({usage['cached_input_tokens']} cached) / {usage['output_tokens']} out"
    )
    print(f"  cost       ${usage['estimated_cost_usd']:.4f} ({usage['completeness']})")
    print(f"  isolation  {run.tool_backend}")
    print(f"  evidence   {directory}")
    print(
        "\nnot scored: `verified_*` metrics need a human ruling on blinded finding/bug pairs first",
        file=sys.stderr,
    )
    return 0


def _run_store(args: argparse.Namespace) -> int:
    from coding_agent_eval.trace.raw_store import RawStore

    removed = RawStore.prune(args.root, retention_days=args.retention_days)
    print(f"pruned {len(removed)} run(s) older than {args.retention_days} days")
    for run_id in removed:
        print(f"  {run_id}")
    return 0


def _run_release(args: argparse.Namespace) -> int:
    from coding_agent_eval.release_audit import audit_repository

    findings = audit_repository(args.root, check_git_history=args.check_git_history)
    for finding in findings:
        print(finding.render(), file=sys.stderr if finding.blocking else sys.stdout)
    blocking = sum(finding.blocking for finding in findings)
    if blocking:
        print(f"release audit blocked by {blocking} finding(s)", file=sys.stderr)
        return 1
    warnings = sum(not finding.blocking for finding in findings)
    print(f"release artifact audit clean ({warnings} warning(s))")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    if args.command == "hygiene":
        return _run_hygiene(args)
    if args.command == "validate":
        return _run_validate(args)
    if args.command == "fixture":
        actions = {
            "rebuild": _run_fixture_rebuild,
            "environment": _run_fixture_environment,
            "verify": _run_fixture,
        }
        return actions[args.action](args)
    if args.command == "evaluate":
        evaluate_actions = {
            "replay": _run_evaluate_replay,
            "export": _run_evaluate_export,
            "import": _run_evaluate_import,
        }
        return evaluate_actions[args.action](args)
    if args.command == "store":
        return _run_store(args)
    if args.command == "release":
        return _run_release(args)
    if args.command == "run":
        return _run_run(args)
    print(
        f"cae {args.command}: not implemented yet (benchmark {BENCHMARK_VERSION})",
        file=sys.stderr,
    )
    return 2


def run() -> None:
    """Console-script shim so `cae` propagates the exit code."""
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
