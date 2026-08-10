"""Fixture lifecycle: checksum, answer-leak audit, LOC counting, patching."""

from __future__ import annotations

from coding_agent_eval.fixtures.checksum import iter_tree_files, tree_checksum
from coding_agent_eval.fixtures.leak_audit import LeakFinding, audit_measured_tree
from coding_agent_eval.fixtures.loc import LOC_TOOL, count_loc
from coding_agent_eval.fixtures.patcher import (
    PatchError,
    apply_patch,
    check_patch,
    materialise,
    revert_patch,
)

__all__ = [
    "LOC_TOOL",
    "LeakFinding",
    "PatchError",
    "apply_patch",
    "audit_measured_tree",
    "check_patch",
    "count_loc",
    "iter_tree_files",
    "materialise",
    "revert_patch",
    "tree_checksum",
]
