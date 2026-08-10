"""The tool surface an agent is given, and nothing else (design spec §6.4, §13.3).

Four tools: read a file, list a directory, search the tree, submit findings.
That is the whole surface. Every one is read-only except the last, which writes
nowhere but the run's own finding list — the measured tree is never modified by
an agent, so a run cannot change the thing it is being scored against.

Two properties are enforced here rather than trusted:

**Schemas are strict.** `additionalProperties: false` and a complete `required`
list, per spec §6.4. A loose schema costs steps: the model sends something
plausible, the call is rejected, and the budget pays for the round trip.

**Runtime resources are not parameters.** The tree root, the finding list, and
the byte caps live in a context the harness supplies. They are deliberately
absent from the model-facing schema, because a tool whose root is an argument
is a tool the model can point somewhere else.

Expected failures — a missing file, a path that leaves the tree, an argument
that does not fit — are returned to the agent as ordinary error content and do
not end the run (§13.3). They are part of using tools, not evidence of a broken
harness.

**Where the bytes come from is not decided here.** Every rule below — the caps,
the line numbering, the regular expression dialect, the containment check — runs
against a `TreeBackend`, which is either the host process or a process inside the
measure container (spec §9.1). Keeping the rules on this side is what makes the
two produce identical output, so choosing isolation costs nothing in behaviour.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from coding_agent_eval.agent.backend import (
    LocalTree,
    ToolFailure,
    TreeBackend,
    normalise,
)
from coding_agent_eval.schemas.validate import validate_document

#: `ToolFailure` now lives in `backend`, because the backends raise it too. It is
#: re-exported here so every caller keeps importing it from the tool surface,
#: which is where it belongs conceptually.
__all__ = [
    "MAX_DIRECTORY_ENTRIES",
    "MAX_FILE_BYTES",
    "MAX_FINDINGS",
    "MAX_SEARCH_MATCHES",
    "TOOLS",
    "TOOLS_BY_NAME",
    "ToolContext",
    "ToolFailure",
    "ToolSpec",
    "model_schemas",
]

#: Caps chosen so one tool result cannot dominate a context window, and so a
#: run's cost is bounded by its steps rather than by the size of the tree.
MAX_FILE_BYTES = 64 * 1024
MAX_DIRECTORY_ENTRIES = 500
MAX_SEARCH_MATCHES = 100
MAX_FINDINGS = 200


class ToolContext:
    """Runtime resources the tools need and the model never sees.

    Constructed either with a host `root` — the default, and what the fast suite
    uses — or with an explicit `backend`, which is how a run gets its tools into
    the measure container. Everything downstream is written against the backend,
    so the two differ in isolation and in nothing else.
    """

    def __init__(
        self,
        root: Path | None = None,
        *,
        backend: TreeBackend | None = None,
        findings: list[dict[str, Any]] | None = None,
        max_file_bytes: int = MAX_FILE_BYTES,
    ) -> None:
        if backend is None:
            if root is None:
                raise ValueError("a ToolContext needs either a tree root or a backend")
            backend = LocalTree(root)
        self.root = root
        self.tree = backend
        self.findings: list[dict[str, Any]] = [] if findings is None else findings
        self.max_file_bytes = max_file_bytes

    def resolve(self, relative: str) -> str:
        """Validate a tree-relative path lexically and return it in POSIX form.

        This is the first of two checks and the only one that can run before a
        filesystem is consulted. The second belongs to the backend, because what
        it has to catch differs: `LocalTree` resolves symlinks, since one inside
        the tree could point out of it; the container backend has no host path
        to point at in the first place.
        """
        return normalise(relative)


@dataclass(frozen=True)
class ToolSpec:
    """One tool: what the model is told, and what the harness runs."""

    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[ToolContext, dict[str, Any]], str]

    def model_schema(self) -> dict[str, Any]:
        """The provider-facing description. Carries no handler and no context."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }


def _strict(properties: dict[str, Any], required: Sequence[str]) -> dict[str, Any]:
    """Build a strict object schema.

    Every property is required and nothing else is allowed. Optional arguments
    are expressed as a nullable type rather than by omission, so the model never
    has to guess whether leaving a field out is legal.
    """
    return {
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }


# --------------------------------------------------------------------- tools


def _read_file(context: ToolContext, args: dict[str, Any]) -> str:
    relative = context.resolve(args["path"])
    raw = context.tree.read_bytes(relative, max_bytes=context.max_file_bytes)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolFailure(f"{args['path']!r} is not valid UTF-8: {exc}") from exc

    lines = text.splitlines()
    # Numbered, because a finding has to cite a line range and an agent that
    # counted them itself would be reporting its own arithmetic.
    return "\n".join(f"{number:6d}\t{line}" for number, line in enumerate(lines, start=1))


def _list_directory(context: ToolContext, args: dict[str, Any]) -> str:
    entries = context.tree.list_entries(context.resolve(args["path"]))
    return "\n".join(
        f"{'dir ' if entry.is_dir else 'file'} {entry.path}"
        for entry in entries[:MAX_DIRECTORY_ENTRIES]
    )


def _search_code(context: ToolContext, args: dict[str, Any]) -> str:
    relative = context.resolve(args["path"])
    try:
        pattern = re.compile(args["pattern"])
    except re.error as exc:
        raise ToolFailure(f"pattern is not a valid regular expression: {exc}") from exc

    matches: list[str] = []
    for path, payload in context.tree.read_subtree(relative):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            continue  # Binary files are skipped, not reported as errors.
        for number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                matches.append(f"{path}:{number}: {line.strip()}")
                if len(matches) >= MAX_SEARCH_MATCHES:
                    return "\n".join(matches) + f"\n... truncated at {MAX_SEARCH_MATCHES} matches"
    return "\n".join(matches) if matches else "no matches"


def _ranges_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """The same interval test `evaluator.matcher` uses for finding-vs-bug.

    Kept as a separate, local copy rather than an import: the two comparisons
    answer different questions (that one also requires a category match and a
    fixture-defined tolerance, both meaningless here) and this tool surface has
    no business depending on the evaluator package. The formula agreeing is
    what matters, not the code being shared.
    """
    return a_start <= b_end and b_start <= a_end


def _overlapping_finding(
    candidate: dict[str, Any], recorded: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """The first already-recorded finding at the same file and overlapping lines.

    Category is deliberately excluded from the comparison, on evidence rather
    than by assumption: a live run resubmitted one location seven times across
    separate calls, and the *same* claim was tagged three different categories
    across those seven — `data_boundary`, `security`, `correctness` — so a
    same-category requirement would have caught fewer than half of them.
    """
    file = candidate.get("file")
    start, end = candidate.get("line_start"), candidate.get("line_end")
    if not (isinstance(file, str) and isinstance(start, int) and isinstance(end, int)):
        return None
    for prior in recorded:
        if prior.get("file") != file:
            continue
        p_start, p_end = prior.get("line_start"), prior.get("line_end")
        if (
            isinstance(p_start, int)
            and isinstance(p_end, int)
            and _ranges_overlap(start, end, p_start, p_end)
        ):
            return prior
    return None


def _write_findings(context: ToolContext, args: dict[str, Any]) -> str:
    """Validate and record findings.

    Rejected as a whole rather than partly accepted. A partial accept would
    leave the agent unsure which of its findings landed, and the run's finding
    list dependent on the order they happened to be validated in.

    A location that overlaps something already recorded is **noted in the
    success message, not refused**. Refusing would override spec §8.5's own
    position — "no fuzzy deduplication: two distinct findings at the same
    location each count" — from inside a tool that cannot tell a resubmission
    from a second, genuinely different defect that happens to live on the same
    lines. The note exists because this harness gives the model no other way
    to know: transcripts never replay the model's own past turns (see
    `provider.build_messages`), and until this note existed the only feedback
    a `write_findings` call ever got back was a bare count. A 51-step live run
    against a reasoning model resubmitted one seeded bug seven times under
    seven different ids, each in its own separate call, because nothing it was
    ever told distinguished "new finding" from "already said this" — see
    `fixtures/fx-taskq-py/defects.md` for the run this was measured from.
    """
    findings = args["findings"]
    if not isinstance(findings, list) or not findings:
        raise ToolFailure("findings must be a non-empty array")
    if len(context.findings) + len(findings) > MAX_FINDINGS:
        raise ToolFailure(f"a run may submit at most {MAX_FINDINGS} findings")

    problems: list[str] = []
    for index, finding in enumerate(findings):
        for problem in validate_document("finding", finding):
            # The pointer is the whole value of this message: "invalid finding"
            # costs a step and teaches nothing.
            problems.append(f"/findings/{index}{problem.pointer}: {problem.message}")

    seen = {existing["id"] for existing in context.findings}
    for index, finding in enumerate(findings):
        identifier = finding.get("id") if isinstance(finding, dict) else None
        if isinstance(identifier, str):
            if identifier in seen:
                problems.append(f"/findings/{index}/id: {identifier!r} was already submitted")
            seen.add(identifier)

    if problems:
        raise ToolFailure("findings rejected:\n" + "\n".join(problems))

    # Checked against what existed before this call, growing as the batch is
    # walked, so two overlapping findings submitted in the same call are also
    # caught — not just overlaps against a previous, separate call.
    pool = list(context.findings)
    notes: list[str] = []
    for finding in findings:
        prior = _overlapping_finding(finding, pool)
        if prior is not None:
            notes.append(
                f"note: {finding['file']}:{finding['line_start']}-{finding['line_end']} "
                f"overlaps an already-recorded finding at the same location "
                f"(id={prior['id']!r}); if this is the same defect, it does not need "
                "to be resubmitted"
            )
        pool.append(finding)

    context.findings.extend(findings)
    base = f"recorded {len(findings)} finding(s); {len(context.findings)} in total"
    return base if not notes else base + "\n" + "\n".join(notes)


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="read_file",
        description=(
            "Read one UTF-8 text file from the tree, with line numbers. "
            "Paths are relative to the tree root."
        ),
        parameters=_strict(
            {"path": {"type": "string", "description": "Tree-relative POSIX path to a file."}},
            ["path"],
        ),
        handler=_read_file,
    ),
    ToolSpec(
        name="list_directory",
        description="List the entries of one directory in the tree. Directories are listed first.",
        parameters=_strict(
            {
                "path": {
                    "type": "string",
                    "description": "Tree-relative POSIX path to a directory. Use '.' for the root.",
                }
            },
            ["path"],
        ),
        handler=_list_directory,
    ),
    ToolSpec(
        name="search_code",
        description=(
            "Search the tree for a regular expression, returning file, line number, and the "
            "matching line."
        ),
        parameters=_strict(
            {
                "path": {
                    "type": "string",
                    "description": "Tree-relative directory to search. Use '.' for the whole tree.",
                },
                "pattern": {"type": "string", "description": "Python regular expression."},
            },
            ["path", "pattern"],
        ),
        handler=_search_code,
    ),
    ToolSpec(
        name="write_findings",
        description=(
            "Submit findings. Each must give a file and line range, a category, a severity, "
            "what the defect is, why it is wrong, what was observed, and how a person could "
            "confirm it."
        ),
        parameters=_strict(
            {
                "findings": {
                    "type": "array",
                    "minItems": 1,
                    "items": _strict(
                        {
                            "id": {"type": "string"},
                            "file": {"type": "string"},
                            "line_start": {"type": "integer", "minimum": 1},
                            "line_end": {"type": "integer", "minimum": 1},
                            "category": {
                                "enum": [
                                    "correctness",
                                    "security",
                                    "concurrency",
                                    "data_boundary",
                                    "release_claim",
                                ]
                            },
                            "severity": {"enum": ["critical", "high", "medium", "low"]},
                            "claim": {"type": "string", "maxLength": 400},
                            "root_cause": {"type": "string", "maxLength": 800},
                            "evidence": {"type": "string", "maxLength": 800},
                            "suggested_verification": {"type": "string", "maxLength": 400},
                        },
                        [
                            "id",
                            "file",
                            "line_start",
                            "line_end",
                            "category",
                            "severity",
                            "claim",
                            "root_cause",
                            "evidence",
                            "suggested_verification",
                        ],
                    ),
                }
            },
            ["findings"],
        ),
        handler=_write_findings,
    ),
)

TOOLS_BY_NAME: dict[str, ToolSpec] = {tool.name: tool for tool in TOOLS}


def model_schemas() -> list[dict[str, Any]]:
    """The tool list as a provider is given it."""
    return [tool.model_schema() for tool in TOOLS]
