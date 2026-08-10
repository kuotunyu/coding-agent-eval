"""Collapse exact duplicate findings before scoring (design spec §8.1).

v0.1 removes a finding only when another has an identical `finding_hash`. The
earlier design collapsed findings whose root causes were merely *similar*, and
that was removed rather than tuned.

The reason is one-directional bias. Merging two genuinely different defects
reported in the same region drops one of them; if the dropped one was
unsupported, the precision denominator loses an error and the score improves. A
heuristic whose mistakes can only ever flatter the agent under test does not
belong in the primary scoring path.

Similarity clustering may return later as diagnostics — output that cannot
delete a finding or move a metric. This module intentionally offers no way to
remove one by score, and a test guards that.
"""

from __future__ import annotations

from typing import Any

from coding_agent_eval.evaluator.hashing import finding_hash


def deduplicate(findings: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Return `(kept, removed_count)`.

    Among findings sharing a hash the smallest `id` survives, so the result does
    not depend on the order the agent happened to emit them in.
    """
    survivors: dict[str, dict[str, Any]] = {}

    for finding in findings:
        key = finding_hash(finding)
        incumbent = survivors.get(key)
        if incumbent is None or finding["id"] < incumbent["id"]:
            survivors[key] = finding

    kept = sorted(survivors.values(), key=lambda f: f["id"])
    return kept, len(findings) - len(kept)
