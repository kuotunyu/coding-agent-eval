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

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

from coding_agent_eval.evaluator.ledger import LedgerKind, load_ledger
from coding_agent_eval.evaluator.metrics import (
    EvaluationError,
    FixtureSpec,
    RunContext,
    ScoredRun,
    Usage,
    score_run,
)


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise EvaluationError(f"no public trace at {path}")
    records: list[dict[str, Any]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise EvaluationError(f"trace line {number} is not valid JSON: {exc}") from exc
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


def _load_json(path: Path, what: str) -> Any:
    if not path.is_file():
        raise EvaluationError(f"no {what} at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def replay_run(
    *,
    trace_path: Path,
    fixture_path: Path,
    bugs_path: Path,
    ledger_path: Path,
    ledger_kind: LedgerKind = LedgerKind.SYNTHETIC,
) -> ScoredRun:
    """Rescore from published artifacts. Raises rather than guessing at a gap."""
    records = _read_trace(trace_path)
    header = _single_payload(records, "run_header")
    termination = _single_payload(records, "termination")
    cost = _single_payload(records, "cost")
    _validate_event_sequence(records)

    fixture_data = _load_json(fixture_path, "fixture manifest")
    bugs = _load_json(bugs_path, "bug set")

    fixture = FixtureSpec(
        fixture_id=fixture_data["fixture_id"],
        fixture_version=fixture_data["fixture_version"],
        tree_checksum=fixture_data["tree_checksum"],
        in_scope_paths=fixture_data["in_scope_paths"],
        out_of_scope_paths=fixture_data["out_of_scope_paths"],
        in_scope_loc=fixture_data["in_scope_loc"],
    )

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
        findings=_findings(records),
        bugs=bugs,
        ledger=load_ledger(ledger_path, kind=ledger_kind),
        fixture=fixture,
        context=context,
        usage=usage,
    )
