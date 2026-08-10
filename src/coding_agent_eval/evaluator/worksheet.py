"""Render a blinded batch as a plain-text worksheet, and parse it back.

A `BlindedBatch` is a data structure; a person adjudicating needs a document.
This is the boundary between them, and it is deliberately narrow: **only two
things are ever parsed back out of a filled-in worksheet** — a decision and a
rationale, each on its own line behind an unambiguous marker. Everything
else in the file — the code excerpt, the claims, the language — is read-only
context, rendered generously for a human to read and never re-parsed. That
asymmetry is what keeps this robust: a code excerpt or a claim that happens to
contain the word "decision" cannot corrupt the parse, because nothing outside
the two marker lines is ever inspected.

The two markers, `>>> DECISION:` and `>>> RATIONALE:`, are chosen to be
vanishingly unlikely to appear in prose or source code, and are matched only
at the start of a line — a mid-sentence mention does not trigger them.

Rationale is required to fit on one physical line. That is a real limitation,
not an oversight: parsing a free-form multi-line field back out of a document
that also contains multi-line code excerpts and long prose claims would need a
much less robust format, and one line is enough room for the sentence a
rationale actually needs to be (spec §8.3.1's own worked examples are one
sentence each).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from coding_agent_eval.evaluator.blinded_export import BlindedBatch

#: Matched at the start of a line, so a mention mid-sentence elsewhere in the
#: document — inside a code excerpt or a claim — is never mistaken for it.
DECISION_MARKER = ">>> DECISION:"
RATIONALE_MARKER = ">>> RATIONALE:"

_ITEM_HEADER = re.compile(r"^ITEM (\d+) of \d+$")
_RULE = "=" * 78

#: The only values `parse_worksheet` accepts. Kept as a plain tuple, not
#: imported from `evaluator.ledger.Decision`, so this module never needs a
#: dependency on the ledger beyond the three literal strings its entries use.
VALID_DECISIONS = ("same_root_cause", "different_root_cause", "insufficient")


class WorksheetError(RuntimeError):
    """The worksheet is malformed, or is missing what a decision needs."""


def render_worksheet(batch: BlindedBatch) -> str:
    """A plain-text document a person can read, annotate, and return.

    Item order is whatever `batch.items` already holds — shuffling, if wanted,
    is the caller's job (see `adjudication.export_for_review`), because this
    function's only responsibility is turning items into text, not deciding
    what order defeats inference.
    """
    total = len(batch.items)
    lines: list[str] = [
        "# Adjudication worksheet",
        f"# {total} item(s) below. For each, fill in the two lines marked >>> and save.",
        f"# DECISION must be exactly one of: {', '.join(VALID_DECISIONS)}",
        "# RATIONALE is required, and must fit on one line.",
        "# Do not add, remove, reorder, or renumber items — a private key on the",
        "# other side of this file is matched to each one by its ITEM number alone.",
        "",
    ]
    for item in batch.items:
        lines += [
            _RULE,
            f"ITEM {item['seq']} of {total}",
            _RULE,
            f"Language: {item['language']}",
            "",
            "Code excerpt:",
            item["code_excerpt"] or "  (no excerpt available)",
            "",
            "Bug claim:",
            f"  {item['bug_claim']}",
            "",
            "Bug root cause:",
            f"  {item['bug_root_cause']}",
            "",
            "Finding claim:",
            f"  {item['finding_claim']}",
            "",
            "Finding root cause:",
            f"  {item['finding_root_cause']}",
            "",
            "Finding evidence:",
            f"  {item['finding_evidence']}",
            "",
            f"{DECISION_MARKER} ",
            f"{RATIONALE_MARKER} ",
            "",
        ]
    return "\n".join(lines) + "\n"


def parse_worksheet(text: str) -> dict[int, tuple[str, str]]:
    """Extract `{seq: (decision, rationale)}` from a filled-in worksheet.

    Raises `WorksheetError` naming every item that is not ready, all at once —
    a partial return would let a caller silently import fewer rulings than the
    human believed they had made.
    """
    seq: int | None = None
    seen_items: set[int] = set()
    decisions: dict[int, str] = {}
    rationales: dict[int, str] = {}
    problems: list[str] = []

    for raw_line in text.splitlines():
        header = _ITEM_HEADER.match(raw_line.strip())
        if header:
            seq = int(header.group(1))
            if seq in seen_items:
                problems.append(f"item {seq}: appears more than once")
            seen_items.add(seq)
            continue
        if seq is None:
            continue
        if raw_line.startswith(DECISION_MARKER):
            decisions[seq] = raw_line[len(DECISION_MARKER) :].strip()
        elif raw_line.startswith(RATIONALE_MARKER):
            rationales[seq] = raw_line[len(RATIONALE_MARKER) :].strip()

    if not seen_items:
        raise WorksheetError("no ITEM headers found; this does not look like a worksheet")

    result: dict[int, tuple[str, str]] = {}
    for item_seq in sorted(seen_items):
        decision = decisions.get(item_seq, "")
        rationale = rationales.get(item_seq, "")
        if not decision:
            problems.append(f"item {item_seq}: no decision")
        elif decision not in VALID_DECISIONS:
            problems.append(
                f"item {item_seq}: decision {decision!r} is not one of {VALID_DECISIONS}"
            )
        elif not rationale:
            problems.append(f"item {item_seq}: no rationale")
        else:
            result[item_seq] = (decision, rationale)

    if problems:
        raise WorksheetError(
            "worksheet is not ready to import:\n" + "\n".join(f"  - {p}" for p in problems)
        )

    return result
