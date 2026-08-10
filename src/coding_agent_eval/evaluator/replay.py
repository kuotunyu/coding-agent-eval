"""Rescore a run from its published artifacts alone (design spec §10.6).

The benchmark claims that what it publishes is enough to check what it reports.
That claim is only true if scoring can be reproduced from the public trace, the
fixture manifests, and the frozen ledger — nothing else. In particular, not from
the private evidence store, which nobody outside has.

So this module reads the public trace and calls the same scorer the original run
used. A test replaces the private store with something that raises on any access
and requires replay to succeed regardless, which is what turns the claim into a
property rather than an assertion in a README.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from coding_agent_eval.evaluator.dedup import deduplicate
from coding_agent_eval.evaluator.hashing import finding_hash
from coding_agent_eval.evaluator.ledger import (
    DecisionSource,
    LedgerError,
    LedgerKey,
    LedgerKind,
    load_ledger,
)
from coding_agent_eval.evaluator.matcher import candidate_pairs
from coding_agent_eval.evaluator.metrics import (
    EvaluationError,
    FixtureSpec,
    RunContext,
    ScoredRun,
    Usage,
    score_run,
)
from coding_agent_eval.evaluator.review_set import (
    ReviewSetError,
    ReviewSetEvidence,
    load_review_set,
)
from coding_agent_eval.schemas.validate import validate_document


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise EvaluationError(f"no public trace at {path}")
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"trace line {number} is not valid JSON: {exc}") from exc
        problems = validate_document("trace-record", record)
        if problems:
            rendered = "; ".join(problem.render() for problem in problems)
            raise EvaluationError(f"trace line {number} violates trace-record schema: {rendered}")
        records.append(record)
    return records


def _single_payload(records: list[dict[str, Any]], event: str) -> dict[str, Any]:
    matches = [record for record in records if record.get("event") == event]
    if len(matches) != 1:
        raise EvaluationError(
            f"public trace must contain exactly one {event}; found {len(matches)}"
        )
    payload = matches[0].get("payload")
    if not isinstance(payload, dict):
        raise EvaluationError(f"public trace {event} payload is not an object")
    return payload


def _sum_llm_usage(records: list[dict[str, Any]]) -> tuple[dict[str, int], Decimal | None]:
    totals = {"input_tokens": 0, "output_tokens": 0}
    call_costs: list[Decimal] = []
    calls = [record for record in records if record.get("event") == "llm_call"]
    for index, record in enumerate(calls, start=1):
        payload = record.get("payload")
        usage = payload.get("usage") if isinstance(payload, dict) else None
        if not isinstance(usage, dict):
            raise EvaluationError(f"llm_call {index} usage is not an object")
        for field in totals:
            value = usage.get(field)
            if value is None:
                continue
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise EvaluationError(f"llm_call {index} {field} must be a non-negative integer")
            totals[field] += value
        estimated = usage.get("estimated_cost_usd")
        if estimated is not None:
            if (
                isinstance(estimated, bool)
                or not isinstance(estimated, int | float)
                or estimated < 0
            ):
                raise EvaluationError(
                    f"llm_call {index} estimated_cost_usd must be a non-negative number"
                )
            call_costs.append(Decimal(str(estimated)))

    summed_cost = sum(call_costs, Decimal(0)) if calls and len(call_costs) == len(calls) else None
    return totals, summed_cost


def _validate_event_sequence(records: list[dict[str, Any]]) -> None:
    if not records:
        raise EvaluationError("public trace is empty")
    sequences = [record.get("seq") for record in records]
    if sequences != list(range(len(records))):
        raise EvaluationError("public trace sequence numbers are not contiguous from zero")
    if records[0].get("event") != "run_header":
        raise EvaluationError("public trace must begin with run_header")
    if len(records) < 2 or [record.get("event") for record in records[-2:]] != [
        "cost",
        "termination",
    ]:
        raise EvaluationError("public trace must end with cost then termination")


def _validate_aggregate_cost(cost: dict[str, Any], summed_call_cost: Decimal | None) -> None:
    aggregate = cost.get("estimated_cost_usd")
    if aggregate is None or summed_call_cost is None:
        return
    if isinstance(aggregate, bool) or not isinstance(aggregate, int | float) or aggregate < 0:
        raise EvaluationError("aggregate cost must be a non-negative number or null")
    precision = Decimal("0.000001")
    if Decimal(str(aggregate)).quantize(precision) != summed_call_cost.quantize(precision):
        raise EvaluationError(
            "aggregate cost does not equal the sum of the public llm_call cost estimates"
        )


def _findings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    for record in records:
        if record.get("event") == "findings_submitted":
            findings.extend(record["payload"]["findings"])
    return findings


def _load_document(path: Path, what: str) -> Any:
    if not path.is_file():
        raise EvaluationError(f"no {what} at {path}")
    try:
        text = path.read_text(encoding="utf-8")
        if path.suffix.lower() in {".yaml", ".yml"}:
            return yaml.safe_load(text)
        return json.loads(text)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as exc:
        raise EvaluationError(f"{what} is not readable structured data: {exc}") from exc


def _load_fixture(path: Path) -> tuple[FixtureSpec, dict[str, Any]]:
    loaded = _load_document(path, "fixture manifest")
    if not isinstance(loaded, dict):
        raise EvaluationError("fixture manifest must be an object")
    document: dict[str, Any] = loaded
    try:
        if "clean_control" in document:
            scope = document["scope"]
            fixture = FixtureSpec(
                fixture_id=str(document["fixture_id"]),
                fixture_version=str(document["fixture_version"]),
                tree_checksum=str(document["clean_control"]["tree_checksum"]),
                in_scope_paths=list(scope["in_scope_paths"]),
                out_of_scope_paths=list(scope["out_of_scope_paths"]),
                in_scope_loc=int(scope["in_scope_loc"]),
            )
        else:
            fixture = FixtureSpec(
                fixture_id=str(document["fixture_id"]),
                fixture_version=str(document["fixture_version"]),
                tree_checksum=str(document["tree_checksum"]),
                in_scope_paths=list(document["in_scope_paths"]),
                out_of_scope_paths=list(document["out_of_scope_paths"]),
                in_scope_loc=int(document["in_scope_loc"]),
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise EvaluationError(f"fixture manifest lacks evaluator fields: {exc}") from exc
    return fixture, document


def _load_bugs(*, bugs_path: Path | None, bug_paths: Sequence[Path] | None) -> list[dict[str, Any]]:
    if (bugs_path is None) == (bug_paths is None):
        raise EvaluationError("replay needs exactly one bug input: bugs_path or bug_paths")
    loaded_bugs: list[Any]
    if bugs_path is not None:
        loaded = _load_document(bugs_path, "bug set")
        if not isinstance(loaded, list):
            raise EvaluationError("bug set must be an array")
        loaded_bugs = loaded
    else:
        assert bug_paths is not None
        loaded_bugs = [_load_document(path, f"bug spec {path}") for path in bug_paths]

    bugs: list[dict[str, Any]] = []
    for index, bug in enumerate(loaded_bugs):
        if not isinstance(bug, dict):
            raise EvaluationError(f"bug input {index} must be an object")
        bugs.append(bug)
    return bugs


def _prefixed_sha256(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _canonical_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()
    return _prefixed_sha256(encoded)


def _review_evidence(
    *,
    trace_path: Path,
    fixture_path: Path,
    records: list[dict[str, Any]],
    header: dict[str, Any],
    findings: list[dict[str, Any]],
    bugs: list[dict[str, Any]],
    fixture: FixtureSpec,
) -> ReviewSetEvidence:
    scored_findings, _ = deduplicate(findings)
    keys = tuple(
        LedgerKey(
            fixture_version=fixture.fixture_version,
            bug_id=str(bug["bug_id"]),
            finding_hash=finding_hash(finding),
        )
        for finding, bug in candidate_pairs(scored_findings, bugs)
    )
    return ReviewSetEvidence(
        run_id=str(header["run_id"]),
        fixture_id=fixture.fixture_id,
        fixture_version=fixture.fixture_version,
        tree_checksum=fixture.tree_checksum,
        trace_sha256=_prefixed_sha256(trace_path.read_bytes()),
        findings_sha256=_canonical_sha256(findings),
        bugs_sha256=_canonical_sha256(bugs),
        fixture_manifest_sha256=_prefixed_sha256(fixture_path.read_bytes()),
        trace_schema_version=str(records[0]["schema_version"]),
        environment_fingerprint=str(header["env_fingerprint"]),
        candidate_keys=keys,
    )


def replay_run(
    *,
    trace_path: Path,
    fixture_path: Path,
    bugs_path: Path | None = None,
    bug_paths: Sequence[Path] | None = None,
    ledger_path: Path | None = None,
    ledger_kind: LedgerKind | None = None,
    review_set_path: Path | None = None,
) -> ScoredRun:
    """Rescore from published artifacts. Raises rather than guessing at a gap."""
    has_legacy = ledger_path is not None
    has_review_set = review_set_path is not None
    if has_legacy == has_review_set or (not has_legacy and ledger_kind is not None):
        raise EvaluationError(
            "replay needs exactly one decision source: a legacy ledger or a review set"
        )

    records = _read_trace(trace_path)
    header = _single_payload(records, "run_header")
    termination = _single_payload(records, "termination")
    cost = _single_payload(records, "cost")
    _validate_event_sequence(records)

    fixture, _ = _load_fixture(fixture_path)
    bugs = _load_bugs(bugs_path=bugs_path, bug_paths=bug_paths)
    findings = _findings(records)

    try:
        decision_source: DecisionSource
        if ledger_path is not None:
            decision_source = load_ledger(
                ledger_path,
                kind=ledger_kind or LedgerKind.SYNTHETIC,
            )
        else:
            assert review_set_path is not None
            decision_source = load_review_set(
                review_set_path,
                evidence=_review_evidence(
                    trace_path=trace_path,
                    fixture_path=fixture_path,
                    records=records,
                    header=header,
                    findings=findings,
                    bugs=bugs,
                    fixture=fixture,
                ),
            )
    except (LedgerError, ReviewSetError) as exc:
        raise EvaluationError(f"decision evidence refused: {exc}") from exc

    llm_usage, summed_call_cost = _sum_llm_usage(records)
    _validate_aggregate_cost(cost, summed_call_cost)

    context = RunContext(
        run_id=header["run_id"],
        fixture_version=header["fixture_version"],
        tree_checksum=header["fixture_tree_checksum"],
        trace_schema_version=records[0]["schema_version"],
        snapshot=header["snapshot"],
        tool_backend=header["tool_backend"],
        pricing_table_version=cost["pricing_table_version"],
        agent_adapter=header["agent_adapter"],
        agent_adapter_version=header["agent_adapter_version"],
        provider=header.get("provider"),
        model=header.get("model"),
        termination_reason=termination.get("reason", "completed"),
        budget=header.get("budget", {}),
    )

    usage = Usage(
        estimated_cost_usd=cost.get("estimated_cost_usd"),
        input_tokens=llm_usage.get("input_tokens", 0),
        output_tokens=llm_usage.get("output_tokens", 0),
    )

    return score_run(
        findings=findings,
        bugs=bugs,
        ledger=decision_source,
        fixture=fixture,
        context=context,
        usage=usage,
    )
