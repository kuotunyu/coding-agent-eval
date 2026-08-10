"""Scoring: deduplication, matching, adjudication ledger, metrics, replay."""

from __future__ import annotations

from coding_agent_eval.evaluator.blinded_export import (
    BlindedBatch,
    BlindingError,
    KeyMap,
    export_batch,
    import_decisions,
)
from coding_agent_eval.evaluator.dedup import deduplicate
from coding_agent_eval.evaluator.hashing import (
    KEYED_FIELDS,
    UNKEYED_FIELDS,
    finding_hash,
    normalize_text,
)
from coding_agent_eval.evaluator.ledger import (
    SYNTHETIC_PREFIX,
    Decision,
    Ledger,
    LedgerError,
    LedgerKey,
    LedgerKind,
    load_ledger,
)
from coding_agent_eval.evaluator.matcher import (
    candidate_pairs,
    is_candidate,
    localization_recall,
    localized_bug_ids,
)
from coding_agent_eval.evaluator.metrics import (
    EvaluationError,
    FixtureSpec,
    RunContext,
    ScoredRun,
    Usage,
    score_run,
)
from coding_agent_eval.evaluator.replay import replay_run

__all__ = [
    "KEYED_FIELDS",
    "SYNTHETIC_PREFIX",
    "UNKEYED_FIELDS",
    "BlindedBatch",
    "BlindingError",
    "Decision",
    "EvaluationError",
    "FixtureSpec",
    "KeyMap",
    "Ledger",
    "LedgerError",
    "LedgerKey",
    "LedgerKind",
    "RunContext",
    "ScoredRun",
    "Usage",
    "candidate_pairs",
    "deduplicate",
    "export_batch",
    "finding_hash",
    "import_decisions",
    "is_candidate",
    "load_ledger",
    "localization_recall",
    "localized_bug_ids",
    "normalize_text",
    "replay_run",
    "score_run",
]
