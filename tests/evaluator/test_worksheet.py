"""The worksheet format: render for a human, parse only the two lines that matter.

The central claim under test is the asymmetry the module docstring makes: six
of eight rendered fields are never re-parsed, so a claim or an excerpt that
happens to contain the literal text "decision" must not confuse the parser.
"""

from __future__ import annotations

import pytest

from coding_agent_eval.evaluator.blinded_export import BlindedBatch
from coding_agent_eval.evaluator.worksheet import (
    DECISION_MARKER,
    RATIONALE_MARKER,
    WorksheetError,
    parse_worksheet,
    render_worksheet,
)

ITEM_A = {
    "seq": 1,
    "language": "python",
    "code_excerpt": "    52: def f():\n    53:     return True",
    "bug_claim": "The comparison accepts a longer token.",
    "bug_root_cause": "The token is sliced before compare_digest.",
    "finding_claim": "Token comparison is not constant time.",
    "finding_root_cause": "Uses == on strings.",
    "finding_evidence": "auth.py:53 uses ==.",
}
ITEM_B = {**ITEM_A, "seq": 2, "finding_claim": "A second, unrelated claim."}


def batch(*items: dict) -> BlindedBatch:
    return BlindedBatch(items=list(items) or [ITEM_A])


def filled(text: str, seq: int, decision: str, rationale: str) -> str:
    """Fill in item `seq`'s two blank marker lines in a rendered worksheet."""
    result = []
    current: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ITEM "):
            current = int(stripped.split()[1])
        if current == seq and line.startswith(DECISION_MARKER):
            result.append(f"{DECISION_MARKER} {decision}")
            continue
        if current == seq and line.startswith(RATIONALE_MARKER):
            result.append(f"{RATIONALE_MARKER} {rationale}")
            continue
        result.append(line)
    return "\n".join(result)


def fill_all(text: str, rulings: dict[int, tuple[str, str]]) -> str:
    for seq, (decision, rationale) in rulings.items():
        text = filled(text, seq, decision, rationale)
    return text


# ------------------------------------------------------------------ rendering


def test_every_item_appears_with_its_sequence_number() -> None:
    text = render_worksheet(batch(ITEM_A, ITEM_B))
    assert "ITEM 1 of 2" in text
    assert "ITEM 2 of 2" in text


def test_every_readable_field_is_present() -> None:
    text = render_worksheet(batch(ITEM_A))
    for field in (
        ITEM_A["language"],
        ITEM_A["bug_claim"],
        ITEM_A["bug_root_cause"],
        ITEM_A["finding_claim"],
        ITEM_A["finding_root_cause"],
        ITEM_A["finding_evidence"],
    ):
        assert field in text


def test_the_code_excerpt_keeps_its_own_lines() -> None:
    text = render_worksheet(batch(ITEM_A))
    assert "52: def f():" in text
    assert "53:     return True" in text


def test_a_missing_excerpt_is_shown_as_such_not_left_blank() -> None:
    text = render_worksheet(batch({**ITEM_A, "code_excerpt": ""}))
    assert "no excerpt available" in text


def test_blank_marker_lines_are_ready_to_fill_in() -> None:
    text = render_worksheet(batch(ITEM_A))
    assert f"{DECISION_MARKER} " in text
    assert f"{RATIONALE_MARKER} " in text


# ---------------------------------------------------------------------- parse


def test_a_fully_filled_worksheet_parses_cleanly() -> None:
    text = render_worksheet(batch(ITEM_A, ITEM_B))
    text = fill_all(
        text,
        {
            1: ("same_root_cause", "Same mechanism, same location."),
            2: ("different_root_cause", "Describes an unrelated defect."),
        },
    )
    result = parse_worksheet(text)
    assert result == {
        1: ("same_root_cause", "Same mechanism, same location."),
        2: ("different_root_cause", "Describes an unrelated defect."),
    }


def test_an_empty_decision_is_reported_by_item_number() -> None:
    text = render_worksheet(batch(ITEM_A))
    text = fill_all(text, {1: ("", "has a rationale but no decision")})
    with pytest.raises(WorksheetError, match="item 1: no decision"):
        parse_worksheet(text)


def test_an_unknown_decision_word_is_rejected() -> None:
    text = render_worksheet(batch(ITEM_A))
    text = fill_all(text, {1: ("probably_same", "x")})
    with pytest.raises(WorksheetError, match="not one of"):
        parse_worksheet(text)


def test_a_blank_rationale_is_reported() -> None:
    text = render_worksheet(batch(ITEM_A))
    text = fill_all(text, {1: ("same_root_cause", "")})
    with pytest.raises(WorksheetError, match="item 1: no rationale"):
        parse_worksheet(text)


def test_an_untouched_item_reports_no_decision_and_no_rationale() -> None:
    """The unfilled template itself must not parse as done."""
    text = render_worksheet(batch(ITEM_A))
    with pytest.raises(WorksheetError, match="no decision"):
        parse_worksheet(text)


def test_all_problems_are_reported_together_not_one_at_a_time() -> None:
    """A partial error message would send someone back and forth one item at a time."""
    text = render_worksheet(batch(ITEM_A, ITEM_B))
    with pytest.raises(WorksheetError) as excinfo:
        parse_worksheet(text)
    message = str(excinfo.value)
    assert "item 1" in message
    assert "item 2" in message


def test_a_document_with_no_item_headers_is_rejected_outright() -> None:
    with pytest.raises(WorksheetError, match="does not look like a worksheet"):
        parse_worksheet("just some unrelated text\nwith no items in it\n")


def test_a_duplicated_item_number_is_reported() -> None:
    text = render_worksheet(batch(ITEM_A))
    # Duplicate the same block by hand, forcing a second "ITEM 1 of 1" header.
    doubled = text + text
    with pytest.raises(WorksheetError, match="item 1: appears more than once"):
        parse_worksheet(doubled)


# ------------------------------------- the asymmetry the module is built on


def test_the_word_decision_inside_a_claim_does_not_confuse_the_parser() -> None:
    """Only a line *starting with* the exact marker is ever read."""
    text = render_worksheet(
        batch(
            {
                **ITEM_A,
                "finding_claim": "The routing decision: rationale text is logged verbatim.",
            }
        )
    )
    text = fill_all(text, {1: ("insufficient", "Cannot tell from the excerpt shown.")})
    assert parse_worksheet(text) == {1: ("insufficient", "Cannot tell from the excerpt shown.")}


def test_a_code_excerpt_containing_the_marker_text_does_not_leak_into_a_ruling() -> None:
    """A source comment could plausibly contain either marker string outright —
    it still must not be read as a real ruling unless it starts the line."""
    text = render_worksheet(
        batch({**ITEM_A, "code_excerpt": f"   10: # TODO {DECISION_MARKER} pending review"})
    )
    text = fill_all(text, {1: ("same_root_cause", "Confirmed against the excerpt.")})
    assert parse_worksheet(text) == {1: ("same_root_cause", "Confirmed against the excerpt.")}


def test_leading_and_trailing_whitespace_on_the_marker_value_is_stripped() -> None:
    text = render_worksheet(batch(ITEM_A))
    text = text.replace(f"{DECISION_MARKER} ", f"{DECISION_MARKER}   same_root_cause   ").replace(
        f"{RATIONALE_MARKER} ", f"{RATIONALE_MARKER}   spaced out   "
    )
    assert parse_worksheet(text) == {1: ("same_root_cause", "spaced out")}
