"""Gate G6 — replay is deterministic and needs only public inputs (spec §10.6).

The claim this gate defends is that the published artifacts are sufficient to
check the published numbers. If replay quietly consulted the private store, a
third party holding only the public trace could not reproduce the result, and
"reproducible" would be a word rather than a property.

So one test replaces the private store with something that raises on any access,
and the replay still has to work. Another mutates a single byte of the golden
trace and requires the outcome to change or the evaluation to refuse — silently
producing the same numbers from different evidence would be the worst outcome of
the three.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import pytest

from coding_agent_eval.evaluator.ledger import LedgerKey, LedgerKind, build_entry, write_entries
from coding_agent_eval.evaluator.metrics import EvaluationError
from coding_agent_eval.evaluator.replay import replay_run
from coding_agent_eval.evaluator.review_set import candidate_set_sha256

GOLDEN = Path(__file__).parent / "golden"
TRACE = GOLDEN / "trace.jsonl"
EXPECTED = GOLDEN / "expected_results.json"
FIXTURE_MANIFEST = GOLDEN / "fixture.json"
BUGS = GOLDEN / "bugs.json"
LEDGER = GOLDEN / "synthetic_adjudications.jsonl"


def run_replay(trace: Path = TRACE):
    return replay_run(
        trace_path=trace,
        fixture_path=FIXTURE_MANIFEST,
        bugs_path=BUGS,
        ledger_path=LEDGER,
    )


def write_trace(path: Path, records: list[dict[str, object]]) -> Path:
    for seq, record in enumerate(records):
        record["seq"] = seq
    path.write_text(
        "".join(
            json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records
        ),
        encoding="utf-8",
    )
    return path


def _canonical_sha256(payload: object) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def write_complete_review_set(root: Path, trace_path: Path) -> Path:
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    header = records[0]["payload"]
    findings = next(
        record["payload"]["findings"]
        for record in records
        if record["event"] == "findings_submitted"
    )
    legacy_entry = json.loads(LEDGER.read_text(encoding="utf-8"))
    key = LedgerKey.from_dict(legacy_entry["key"])
    candidate_hash = candidate_set_sha256((key,))
    review_set_id = "rs-golden-deterministic-001"
    materials = {
        "review_set_id": review_set_id,
        "candidate_set_sha256": candidate_hash,
        "fixture_version": header["fixture_version"],
        "items": [{"key": key.as_dict(), "item": {"blinded_context": "golden"}}],
    }
    fixture = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": "1.0.0",
        "review_set_id": review_set_id,
        "run_id": header["run_id"],
        "fixture_id": fixture["fixture_id"],
        "fixture_version": fixture["fixture_version"],
        "tree_checksum": fixture["tree_checksum"],
        "trace_sha256": f"sha256:{sha256(trace_path.read_bytes()).hexdigest()}",
        "findings_sha256": _canonical_sha256(findings),
        "bugs_sha256": _canonical_sha256(json.loads(BUGS.read_text(encoding="utf-8"))),
        "fixture_manifest_sha256": f"sha256:{sha256(FIXTURE_MANIFEST.read_bytes()).hexdigest()}",
        "candidate_set_sha256": candidate_hash,
        "candidate_materials_sha256": _canonical_sha256(materials),
        "trace_schema_version": "0.2.0",
        "environment_fingerprint": header["env_fingerprint"],
        "fixture_author_ids": ["kuotunyu"],
        "run_operator_id": "kuotunyu",
        "primary": {
            "reviewer_id": "kuotunyu",
            "independent_of_fixture_authors": False,
            "independent_of_run_operator": False,
            "independent_of_other_reviewers": False,
        },
        "independent": {
            "reviewer_id": "reviewer-b",
            "independent_of_fixture_authors": True,
            "independent_of_run_operator": True,
            "independent_of_other_reviewers": True,
        },
        "resolver": None,
        "ledgers": {
            "primary": "primary.jsonl",
            "independent": "independent.jsonl",
            "resolutions": "resolutions.jsonl",
        },
    }
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (root / "candidates.json").write_text(
        json.dumps(materials, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_entries(
        root / "primary.jsonl",
        [
            build_entry(
                key=key,
                decision="same_root_cause",
                rationale="Primary human reviewed the blinded candidate.",
                adjudicator_id="kuotunyu",
                decided_at="2026-08-11",
            )
        ],
    )
    write_entries(
        root / "independent.jsonl",
        [
            build_entry(
                key=key,
                decision="same_root_cause",
                rationale="Independent human reviewed the blinded candidate.",
                adjudicator_id="reviewer-b",
                decided_at="2026-08-11",
            )
        ],
    )
    write_entries(root / "resolutions.jsonl", [])
    return root


def write_current_trace(path: Path) -> Path:
    records = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    for record in records:
        record["schema_version"] = "0.2.0"
    header = records[0]["payload"]
    header.pop("image_digest")
    manifest_digest = "sha256:" + "5e" * 32
    header.update(
        {
            "image_ref": "ghcr.io/kuotunyu/coding-agent-eval-fx-demo-py@" + manifest_digest,
            "image_manifest_digest": manifest_digest,
            "image_config_digest": "sha256:" + "1f" * 32,
        }
    )
    return write_trace(path, records)


# -------------------------------------------------------- decision source


def test_replay_requires_exactly_one_decision_source(tmp_path: Path) -> None:
    with pytest.raises(EvaluationError, match="exactly one decision source"):
        replay_run(
            trace_path=TRACE,
            fixture_path=FIXTURE_MANIFEST,
            bugs_path=BUGS,
        )

    with pytest.raises(EvaluationError, match="exactly one decision source"):
        replay_run(
            trace_path=TRACE,
            fixture_path=FIXTURE_MANIFEST,
            bugs_path=BUGS,
            ledger_path=LEDGER,
            ledger_kind=LedgerKind.SYNTHETIC,
            review_set_path=tmp_path / "review-set",
        )


def test_complete_dual_review_is_the_only_publishable_source(tmp_path: Path) -> None:
    trace = write_current_trace(tmp_path / "trace.jsonl")
    review_set = write_complete_review_set(tmp_path / "review-set", trace)

    result = replay_run(
        trace_path=trace,
        fixture_path=FIXTURE_MANIFEST,
        bugs_path=BUGS,
        review_set_path=review_set,
    )

    assert result.decision_source == "dual_review"
    assert result.publication_reason == "dual_review_complete"
    assert result.publishable is True
    assert result.publication_provenance["review_set_id"] == "rs-golden-deterministic-001"


def test_replay_refuses_incomplete_or_drifted_dual_review(tmp_path: Path) -> None:
    trace = write_current_trace(tmp_path / "trace.jsonl")
    review_set = write_complete_review_set(tmp_path / "review-set", trace)
    (review_set / "independent.jsonl").write_bytes(b"")

    with pytest.raises(EvaluationError, match="coverage"):
        replay_run(
            trace_path=trace,
            fixture_path=FIXTURE_MANIFEST,
            bugs_path=BUGS,
            review_set_path=review_set,
        )

    write_entries(
        review_set / "independent.jsonl",
        [
            build_entry(
                key=LedgerKey.from_dict(json.loads(LEDGER.read_text(encoding="utf-8"))["key"]),
                decision="same_root_cause",
                rationale="Independent human reviewed the blinded candidate.",
                adjudicator_id="reviewer-b",
                decided_at="2026-08-11",
            )
        ],
    )
    manifest_path = review_set / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["trace_sha256"] = "sha256:" + "f" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(EvaluationError, match="trace_sha256 drift"):
        replay_run(
            trace_path=trace,
            fixture_path=FIXTURE_MANIFEST,
            bugs_path=BUGS,
            review_set_path=review_set,
        )


def test_replay_binds_dual_review_to_exact_bug_content(tmp_path: Path) -> None:
    trace = write_current_trace(tmp_path / "trace.jsonl")
    review_set = write_complete_review_set(tmp_path / "review-set", trace)
    bugs = json.loads(BUGS.read_text(encoding="utf-8"))
    bugs[0]["canonical_root_cause"] = "Changed after the humans reviewed it."
    changed_bugs = tmp_path / "bugs.json"
    changed_bugs.write_text(json.dumps(bugs), encoding="utf-8")

    with pytest.raises(EvaluationError, match="bugs_sha256 drift"):
        replay_run(
            trace_path=trace,
            fixture_path=FIXTURE_MANIFEST,
            bugs_path=changed_bugs,
            review_set_path=review_set,
        )


def test_legacy_formal_and_synthetic_sources_remain_unpublishable(tmp_path: Path) -> None:
    synthetic = replay_run(
        trace_path=TRACE,
        fixture_path=FIXTURE_MANIFEST,
        bugs_path=BUGS,
        ledger_path=LEDGER,
        ledger_kind=LedgerKind.SYNTHETIC,
    )
    old = json.loads(LEDGER.read_text(encoding="utf-8"))
    formal_ledger = tmp_path / "formal.jsonl"
    write_entries(
        formal_ledger,
        [
            build_entry(
                key=LedgerKey.from_dict(old["key"]),
                decision=old["decision"],
                rationale="Owner human reviewed this legacy candidate.",
                adjudicator_id="kuotunyu",
                decided_at="2026-08-11",
            )
        ],
    )
    formal = replay_run(
        trace_path=TRACE,
        fixture_path=FIXTURE_MANIFEST,
        bugs_path=BUGS,
        ledger_path=formal_ledger,
        ledger_kind=LedgerKind.FORMAL,
    )

    assert formal.metrics == synthetic.metrics
    assert formal.decision_source == "legacy_formal"
    assert formal.publication_reason == "single_adjudicator_legacy"
    assert formal.publishable is False
    assert synthetic.publication_reason == "synthetic_adjudication"
    assert synthetic.publishable is False


# ------------------------------------------------------------ golden inputs


def test_the_golden_inputs_are_committed() -> None:
    for path in (TRACE, EXPECTED, FIXTURE_MANIFEST, BUGS, LEDGER):
        assert path.is_file(), f"missing golden input: {path.name}"


def test_replay_reproduces_the_expected_results_byte_for_byte() -> None:
    produced = json.dumps(run_replay().as_dict(), indent=2, sort_keys=True) + "\n"
    assert produced == EXPECTED.read_text(encoding="utf-8")


def test_replay_is_stable_across_repeated_runs() -> None:
    assert run_replay().as_dict() == run_replay().as_dict()


def test_replay_sums_every_llm_call_instead_of_selecting_the_first(tmp_path: Path) -> None:
    records = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    second = {
        "schema_version": records[0]["schema_version"],
        "seq": 0,
        "ts": "2026-08-05T00:00:01.500+00:00",
        "event": "llm_call",
        "payload": {
            "request_hash": "9c" * 32,
            "latency_ms": 1,
            "finish_reason": "stop",
            "usage": {"input_tokens": 1000, "output_tokens": 2000},
        },
    }
    cost_index = next(i for i, record in enumerate(records) if record["event"] == "cost")
    records.insert(cost_index, second)

    result = run_replay(write_trace(tmp_path / "trace.jsonl", records))

    assert result.metrics["tokens_per_verified_bug"] == 53_000.0


@pytest.mark.parametrize("event", ["run_header", "cost", "termination"])
def test_replay_refuses_duplicate_singleton_events(tmp_path: Path, event: str) -> None:
    records = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    duplicate = dict(next(record for record in records if record["event"] == event))
    records.insert(-1, duplicate)

    with pytest.raises(EvaluationError, match=f"exactly one {event}"):
        run_replay(write_trace(tmp_path / "trace.jsonl", records))


def test_replay_refuses_an_aggregate_cost_that_disagrees_with_calls(tmp_path: Path) -> None:
    records = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    llm = next(record for record in records if record["event"] == "llm_call")
    llm["payload"]["usage"]["estimated_cost_usd"] = 0.125
    cost = next(record for record in records if record["event"] == "cost")
    cost["payload"]["estimated_cost_usd"] = 0.5

    with pytest.raises(EvaluationError, match="aggregate cost"):
        run_replay(write_trace(tmp_path / "trace.jsonl", records))


# ------------------------------------------------- public inputs are enough


def test_replay_never_touches_the_private_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """If it did, a public artifact would not be sufficient to check the numbers."""
    import coding_agent_eval.trace.raw_store as raw_store

    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("replay read the private evidence store")

    monkeypatch.setattr(raw_store.RawStore, "__init__", refuse)
    monkeypatch.setattr(raw_store.RawStore, "read_events", refuse)
    monkeypatch.setattr(raw_store.RawStore, "get_blob", refuse)

    run_replay()


def test_the_golden_trace_carries_no_raw_tool_content() -> None:
    """The public trace holds hashes and excerpts, never whole tool outputs."""
    for line in TRACE.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)["payload"]
        assert "content" not in payload
        assert "response_body" not in payload


# --------------------------------------------------------------- tampering


def test_a_single_byte_change_changes_the_outcome_or_refuses() -> None:
    """Silently producing the same numbers from different evidence is the worst case."""
    original = TRACE.read_text(encoding="utf-8")
    tampered = original.replace('"line_start":104', '"line_start":900', 1)
    assert tampered != original, "the tamper target must exist in the golden trace"

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        path.write_text(tampered, encoding="utf-8")
        try:
            result = run_replay(path)
        except EvaluationError:
            return  # refusing is an acceptable outcome
        assert result.as_dict() != json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_a_fixture_version_change_refuses_rather_than_rescoring() -> None:
    original = TRACE.read_text(encoding="utf-8")
    tampered = original.replace('"fixture_version":"1.0.0"', '"fixture_version":"2.0.0"', 1)
    assert tampered != original

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        path.write_text(tampered, encoding="utf-8")
        with pytest.raises(EvaluationError, match="fixture_version"):
            run_replay(path)


def test_a_corrupt_trace_line_refuses(tmp_path: Path) -> None:
    path = tmp_path / "trace.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(EvaluationError):
        run_replay(path)


def test_a_trace_without_findings_refuses(tmp_path: Path) -> None:
    """A run that emitted nothing is a real state, but it is not this trace."""
    records = [
        json.loads(line)
        for line in TRACE.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["event"] != "findings_submitted"
    ]
    path = write_trace(tmp_path / "trace.jsonl", records)
    result = run_replay(path)
    assert result.counts["findings_scored"] == 0


def test_trace_0_2_replay_refuses_a_legacy_header_shape(tmp_path: Path) -> None:
    records = [json.loads(line) for line in TRACE.read_text(encoding="utf-8").splitlines()]
    records[0]["schema_version"] = "0.2.0"
    path = write_trace(tmp_path / "trace.jsonl", records)

    with pytest.raises(EvaluationError, match="image_config_digest"):
        run_replay(path)


# ------------------------------------------------------------ publishability


def test_the_golden_result_is_marked_unpublishable() -> None:
    """The historical 0.1 trace stays readable but cannot be publication evidence."""
    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    assert expected["trace_schema_version"] == "0.1.0"
    assert expected["decision_source"] == "synthetic"
    assert expected["publication_reason"] == "synthetic_adjudication"
    assert expected["publishable"] is False


def test_the_trace_0_1_golden_still_replays_as_read_only_history() -> None:
    result = run_replay()

    assert result.context.trace_schema_version == "0.1.0"
    assert result.publishable is False


# ----------------------------------------------------------------- CLI


def test_cli_replay_matches_the_golden_result(capsys: pytest.CaptureFixture[str]) -> None:
    from coding_agent_eval.cli import main

    exit_code = main(
        [
            "evaluate",
            "replay",
            str(GOLDEN),
            "--fixture",
            str(FIXTURE_MANIFEST),
            "--bugs",
            str(BUGS),
            "--ledger",
            str(LEDGER),
            "--ledger-kind",
            "synthetic",
        ]
    )
    assert exit_code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == json.loads(EXPECTED.read_text(encoding="utf-8"))


def test_cli_replay_warns_that_a_synthetic_result_describes_no_model(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from coding_agent_eval.cli import main

    main(
        [
            "evaluate",
            "replay",
            str(GOLDEN),
            "--fixture",
            str(FIXTURE_MANIFEST),
            "--bugs",
            str(BUGS),
            "--ledger",
            str(LEDGER),
            "--ledger-kind",
            "synthetic",
        ]
    )
    assert "describe no model" in capsys.readouterr().err


def test_cli_replay_exits_nonzero_when_evaluation_refuses(tmp_path: Path) -> None:
    from coding_agent_eval.cli import main

    (tmp_path / "trace.jsonl").write_text("{bad}\n", encoding="utf-8")
    assert (
        main(
            [
                "evaluate",
                "replay",
                str(tmp_path),
                "--fixture",
                str(FIXTURE_MANIFEST),
                "--bugs",
                str(BUGS),
                "--ledger",
                str(LEDGER),
                "--ledger-kind",
                "synthetic",
            ]
        )
        == 1
    )
