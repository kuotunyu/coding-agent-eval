"""Headline metrics and fail-closed evaluation (design spec §8.4, §8.5, §13.4).

Everything the benchmark claims is produced here, so the module is written to
fail rather than to produce a plausible number. Five conditions refuse to score
at all (gate G8), and every zero denominator yields `None` with a stated reason
instead of a value that reads as success.

Three decisions are worth stating outright, because each one costs the agent
under test and could have gone the flattering way:

* **Out-of-scope findings count in the precision denominator.** Noise aimed at
  the wrong part of the tree is still work a reviewer has to discard. They are
  also reported separately so the choice stays visible and arguable.
* **A finding verifies at most one bug** unless the manifest explicitly groups
  them. Otherwise one vague observation over an overlapping region would collect
  credit for several distinct defects.
* **`insufficient` does not verify.** Uncertainty is not a match.

v0.1 has no residual-defect exclusion. Excluding a finding because it sits near
a known-but-unfixed defect would exempt any wrong finding that happened to land
nearby, so clean fixtures are required to be genuinely clean instead (§6.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from coding_agent_eval import (
    ADJUDICATION_PROTOCOL_VERSION,
    BENCHMARK_VERSION,
    PUBLICATION_TRACE_SCHEMA_VERSION,
    READABLE_TRACE_SCHEMA_VERSIONS,
    REDACTION_MANIFEST_VERSION,
)
from coding_agent_eval.evaluator.dedup import deduplicate
from coding_agent_eval.evaluator.hashing import finding_hash
from coding_agent_eval.evaluator.ledger import Decision, DecisionSource, LedgerKey
from coding_agent_eval.evaluator.matcher import candidate_pairs, localization_recall
from coding_agent_eval.fixtures.image_identity import SHA256_DIGEST

Finding = dict[str, Any]
Bug = dict[str, Any]

#: Trace schema versions this evaluator understands.
SUPPORTED_TRACE_SCHEMA_VERSIONS = READABLE_TRACE_SCHEMA_VERSIONS


class EvaluationError(RuntimeError):
    """Scoring refused. The result would not have meant what it appeared to."""


def _is_measure_backend(tool_backend: str) -> bool:
    prefix = "measure_container:"
    return (
        tool_backend.startswith(prefix)
        and SHA256_DIGEST.fullmatch(tool_backend.removeprefix(prefix)) is not None
    )


@dataclass
class FixtureSpec:
    fixture_id: str
    fixture_version: str
    tree_checksum: str
    in_scope_paths: list[str]
    out_of_scope_paths: list[str]
    in_scope_loc: int


@dataclass
class RunContext:
    run_id: str
    fixture_version: str
    tree_checksum: str
    trace_schema_version: str
    snapshot: str
    tool_backend: str
    pricing_table_version: str
    agent_adapter: str = "unknown"
    agent_adapter_version: str = "0.0.0"
    provider: str | None = None
    model: str | None = None
    termination_reason: str = "completed"
    budget: dict[str, Any] = field(
        default_factory=lambda: {
            "max_tokens": None,
            "max_tool_calls": None,
            "max_wallclock_seconds": None,
            "max_estimated_cost_usd": None,
        }
    )


@dataclass
class Usage:
    estimated_cost_usd: float | None = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class ScoredRun:
    metrics: dict[str, Any]
    undefined_reasons: dict[str, str]
    counts: dict[str, int]
    verified_bug_ids: tuple[str, ...]
    decision_source: str
    publishable: bool
    publication_reason: str
    publication_provenance: dict[str, str]
    context: RunContext
    fixture: FixtureSpec

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1",
            "benchmark_version": BENCHMARK_VERSION,
            "adjudication_protocol_version": ADJUDICATION_PROTOCOL_VERSION,
            "trace_schema_version": self.context.trace_schema_version,
            "pricing_table_version": self.context.pricing_table_version,
            "redaction_manifest_version": REDACTION_MANIFEST_VERSION,
            "run_id": self.context.run_id,
            "fixture_id": self.fixture.fixture_id,
            "fixture_version": self.fixture.fixture_version,
            "snapshot": self.context.snapshot,
            "tool_backend": self.context.tool_backend,
            "agent_adapter": self.context.agent_adapter,
            "agent_adapter_version": self.context.agent_adapter_version,
            "provider": self.context.provider,
            "model": self.context.model,
            "budget": self.context.budget,
            "termination_reason": self.context.termination_reason,
            "decision_source": self.decision_source,
            "publishable": self.publishable,
            "publication_reason": self.publication_reason,
            "metrics": self.metrics,
            "undefined_reasons": self.undefined_reasons,
            "counts": self.counts,
            **self.publication_provenance,
        }


def _matches_any(relative: str, patterns: list[str]) -> bool:
    import fnmatch

    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


def _is_out_of_scope(finding: Finding, fixture: FixtureSpec) -> bool:
    path = finding["file"]
    if _matches_any(path, fixture.out_of_scope_paths):
        return True
    return not _matches_any(path, fixture.in_scope_paths)


def _check_preconditions(context: RunContext, fixture: FixtureSpec) -> None:
    if context.fixture_version != fixture.fixture_version:
        raise EvaluationError(
            f"run records fixture_version {context.fixture_version!r} but the fixture is "
            f"{fixture.fixture_version!r}; the run was scored against a different tree"
        )
    if context.tree_checksum != fixture.tree_checksum:
        raise EvaluationError(
            "tree checksum in the run does not match the fixture, so the measured tree was "
            "not the one this ground truth describes"
        )
    if context.trace_schema_version not in SUPPORTED_TRACE_SCHEMA_VERSIONS:
        raise EvaluationError(
            f"unsupported trace_schema_version {context.trace_schema_version!r}; "
            f"this evaluator understands {sorted(SUPPORTED_TRACE_SCHEMA_VERSIONS)}"
        )


def _lookup(
    source: DecisionSource, fixture_version: str, finding: Finding, bug: Bug
) -> Decision | None:
    return source.decision(
        LedgerKey(
            fixture_version=fixture_version,
            bug_id=bug["bug_id"],
            finding_hash=finding_hash(finding),
        )
    )


def _overlap_size(finding: Finding, bug: Bug) -> int:
    """Overlapping line count, used only to break a tie deterministically."""
    location = bug["localization"]["primary"]
    tolerance: int = bug["localization"]["line_tolerance"]
    low: int = max(finding["line_start"], location["line_start"] - tolerance)
    high: int = min(finding["line_end"], location["line_end"] + tolerance)
    return max(0, high - low + 1)


def _assign(
    verified_pairs: list[tuple[Finding, Bug]],
) -> tuple[set[str], set[str]]:
    """Resolve which bugs each finding is credited with (spec §8.4).

    A finding ruled `same_root_cause` for several bugs credits them all only when
    they share a non-null `compound_group`. Otherwise one vague observation
    spanning an overlapping region would collect credit for distinct defects, so
    exactly one is chosen: largest localisation overlap, then lowest bug id.
    """
    by_finding: dict[str, list[Bug]] = {}
    findings_by_id: dict[str, Finding] = {}
    for finding, bug in verified_pairs:
        by_finding.setdefault(finding["id"], []).append(bug)
        findings_by_id[finding["id"]] = finding

    verified_bugs: set[str] = set()
    matched_findings: set[str] = set()

    for finding_id, bugs in by_finding.items():
        finding = findings_by_id[finding_id]
        groups = {b.get("compound_group") for b in bugs}
        if len(bugs) == 1 or (len(groups) == 1 and next(iter(groups)) is not None):
            verified_bugs.update(b["bug_id"] for b in bugs)
        else:
            best = sorted(bugs, key=lambda b: (-_overlap_size(finding, b), b["bug_id"]))[0]
            verified_bugs.add(best["bug_id"])
        matched_findings.add(finding_id)

    return verified_bugs, matched_findings


def score_run(
    *,
    findings: list[Finding],
    bugs: list[Bug],
    ledger: DecisionSource,
    fixture: FixtureSpec,
    context: RunContext,
    usage: Usage,
) -> ScoredRun:
    """Score one run, or raise `EvaluationError` rather than produce a misleading number."""
    _check_preconditions(context, fixture)

    scored_findings, duplicates_removed = deduplicate(findings)

    pairs = candidate_pairs(scored_findings, bugs)
    rulings = [(f, b, _lookup(ledger, fixture.fixture_version, f, b)) for f, b in pairs]

    unadjudicated = [(f, b) for f, b, decision in rulings if decision is None]
    if unadjudicated:
        raise EvaluationError(
            f"{len(unadjudicated)} candidate pair(s) are unadjudicated; verified metrics "
            "cannot be computed from a partial ledger"
        )

    verified_pairs = [
        (f, b) for f, b, decision in rulings if decision is not None and decision.verifies
    ]
    verified_bug_ids, matched_finding_ids = _assign(verified_pairs)

    total_bugs = len(bugs)
    total_findings = len(scored_findings)
    out_of_scope = sum(1 for f in scored_findings if _is_out_of_scope(f, fixture))
    unsupported = total_findings - len(matched_finding_ids)

    metrics: dict[str, Any] = {}
    reasons: dict[str, str] = {}

    if total_bugs:
        metrics["localization_recall"] = localization_recall(scored_findings, bugs)
        metrics["verified_bug_recall"] = len(verified_bug_ids) / total_bugs
    else:
        metrics["localization_recall"] = None
        metrics["verified_bug_recall"] = None
        reasons["localization_recall"] = "no_bugs_in_snapshot"
        reasons["verified_bug_recall"] = "no_bugs_in_snapshot"

    if total_findings:
        metrics["verified_finding_precision"] = len(matched_finding_ids) / total_findings
    else:
        metrics["verified_finding_precision"] = None
        reasons["verified_finding_precision"] = "no_findings"

    metrics["unsupported_findings"] = unsupported
    metrics["benchmark_unsupported_findings_per_kloc"] = unsupported / (fixture.in_scope_loc / 1000)

    if verified_bug_ids:
        metrics["cost_per_verified_bug"] = (
            None
            if usage.estimated_cost_usd is None
            else usage.estimated_cost_usd / len(verified_bug_ids)
        )
        if metrics["cost_per_verified_bug"] is None:
            reasons["cost_per_verified_bug"] = "cost_not_reported"
        metrics["tokens_per_verified_bug"] = (usage.input_tokens + usage.output_tokens) / len(
            verified_bug_ids
        )
    else:
        metrics["cost_per_verified_bug"] = None
        metrics["tokens_per_verified_bug"] = None
        reasons["cost_per_verified_bug"] = "no_verified_bugs"
        reasons["tokens_per_verified_bug"] = "no_verified_bugs"

    metrics["out_of_scope_findings"] = out_of_scope
    metrics["exact_duplicates_removed"] = duplicates_removed

    publishable = ledger.publishable
    publication_reason = ledger.publication_reason
    if publishable and context.trace_schema_version != PUBLICATION_TRACE_SCHEMA_VERSION:
        publishable = False
        publication_reason = "legacy_trace_contract"
    elif publishable and not _is_measure_backend(context.tool_backend):
        publishable = False
        publication_reason = "non_measure_backend"

    return ScoredRun(
        metrics=metrics,
        undefined_reasons=reasons,
        counts={
            "bugs": total_bugs,
            "findings_scored": total_findings,
            "verified_bugs": len(verified_bug_ids),
            "matched_findings": len(matched_finding_ids),
            "unadjudicated_pairs": 0,
        },
        verified_bug_ids=tuple(sorted(verified_bug_ids)),
        decision_source=ledger.decision_source,
        publishable=publishable,
        publication_reason=publication_reason,
        publication_provenance=dict(ledger.publication_provenance),
        context=context,
        fixture=fixture,
    )
