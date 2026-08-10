"""Blinded adjudication, end to end: a run's findings to a worksheet and back.

The properties that matter most are the ones a person adjudicating would never
catch by reading the code: that the excerpt shown is the *mutated* tree the
finding was actually made against, that shuffling really happens rather than
silently passing candidates through in their matcher-given order, and that
importing the same worksheet twice cannot silently duplicate a ruling.
"""

from __future__ import annotations

import json
import random
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from coding_agent_eval.adjudication import (
    AdjudicationError,
    apply_review,
    export_for_review,
)
from coding_agent_eval.evaluator.ledger import (
    SYNTHETIC_PREFIX,
    LedgerKind,
    load_ledger,
    read_entries,
    write_entries,
)
from coding_agent_eval.evaluator.worksheet import (
    DECISION_MARKER,
    RATIONALE_MARKER,
)

ORIGINAL_AUTH = "def verify(a, b):\n    return compare_digest(a, b)\n"

AUTH_PATCH = """\
--- a/src/auth.py
+++ b/src/auth.py
@@ -1,2 +1,2 @@
 def verify(a, b):
-    return compare_digest(a, b)
+    return a == b
"""

FINDING_MATCHING = {
    "id": "F-1",
    "file": "src/auth.py",
    "line_start": 2,
    "line_end": 2,
    "category": "security",
    "severity": "high",
    "claim": "Token comparison is not constant time.",
    "root_cause": "Uses == on secrets instead of compare_digest.",
    "evidence": "auth.py line 2 compares with ==.",
    "suggested_verification": "Time it against a range of prefixes.",
}
FINDING_ALSO_MATCHING = {
    **FINDING_MATCHING,
    "id": "F-2",
    "claim": "A second, independently phrased report of the same location.",
}
FINDING_ELSEWHERE = {
    **FINDING_MATCHING,
    "id": "F-3",
    "file": "src/other.py",
    "claim": "Unrelated to the seeded bug.",
}


def write_fixture(root: Path) -> Path:
    """A minimal, self-contained fixture: one bug, one patch, one tree."""
    fixture_dir = root / "fx-demo"
    tree = fixture_dir / "src"
    tree.mkdir(parents=True)
    (fixture_dir / "src" / "auth.py").write_bytes(ORIGINAL_AUTH.encode("utf-8"))
    (fixture_dir / "tree").mkdir(exist_ok=True)
    # materialise() reads from `<fixture>/tree`, not `<fixture>/src` directly.
    shutil.move(str(fixture_dir / "src"), str(fixture_dir / "tree" / "src"))

    (fixture_dir / "patches").mkdir()
    (fixture_dir / "patches" / "B-001.patch").write_bytes(AUTH_PATCH.encode("utf-8"))

    (fixture_dir / "bugs").mkdir()
    bug = {
        "bug_id": "fx-demo/B-001",
        "category": "security",
        "patch": "patches/B-001.patch",
        "localization": {
            "primary": {"file": "src/auth.py", "line_start": 2, "line_end": 2},
            "line_tolerance": 1,
            "acceptable_alternates": [],
        },
        "canonical_claim": "The comparison accepts any input in constant-time-unsafe fashion.",
        "canonical_root_cause": "compare_digest was replaced with ==.",
    }
    (fixture_dir / "bugs" / "B-001.yaml").write_text(
        yaml.safe_dump(bug, sort_keys=False), encoding="utf-8", newline="\n"
    )

    manifest = {
        "fixture_id": "fx-demo",
        "fixture_version": "1.0.0",
        "language": "python",
        "bugs": ["fx-demo/B-001"],
    }
    (fixture_dir / "fixture.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return fixture_dir


def write_run(root: Path, *, bug_ids: list[str], findings: list[dict[str, Any]]) -> Path:
    run_dir = root / "run"
    run_dir.mkdir()
    (run_dir / "run.json").write_text(json.dumps({"bugs_in_snapshot": bug_ids}), encoding="utf-8")
    (run_dir / "findings.json").write_text(json.dumps({"findings": findings}), encoding="utf-8")
    return run_dir


def fill(text: str, rulings: dict[int, tuple[str, str]]) -> str:
    """Fill in the marker lines for the given item numbers, matching the real tool's format."""
    result = []
    current: int | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ITEM "):
            current = int(stripped.split()[1])
        if current in rulings and line.startswith(DECISION_MARKER):
            result.append(f"{DECISION_MARKER} {rulings[current][0]}")
            continue
        if current in rulings and line.startswith(RATIONALE_MARKER):
            result.append(f"{RATIONALE_MARKER} {rulings[current][1]}")
            continue
        result.append(line)
    return "\n".join(result)


@pytest.fixture
def fixture_dir(tmp_path: Path) -> Path:
    return write_fixture(tmp_path)


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.jsonl"


# ------------------------------------------------------------------- export


def test_export_writes_a_worksheet_and_a_keymap(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    assert result.pending == 1
    assert result.already_ruled == 0
    assert result.worksheet_path is not None and result.worksheet_path.is_file()
    assert result.keymap_path is not None and result.keymap_path.is_file()


def test_the_worksheet_shows_the_finding_and_the_bugs_own_claim(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    text = result.worksheet_path.read_text(encoding="utf-8")
    assert FINDING_MATCHING["claim"] in text
    assert FINDING_MATCHING["evidence"] in text
    assert "The comparison accepts any input" in text  # canonical_claim


def test_the_excerpt_shows_the_mutated_code_not_the_clean_code(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    """The single most important correctness property here: a finding was made
    against the mutated tree, so showing the clean tree's code would show a
    human something the agent never actually saw.

    `compare_digest` legitimately appears elsewhere in the worksheet — it is
    named in the bug's own `canonical_root_cause`, read-only context that is
    correctly shown — so this checks the excerpt block specifically, not the
    whole document.
    """
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    text = result.worksheet_path.read_text(encoding="utf-8")
    excerpt = text.split("Code excerpt:")[1].split("Bug claim:")[0]
    assert "return a == b" in excerpt
    assert "compare_digest" not in excerpt


def test_only_findings_that_localise_a_bug_are_exported(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(
        tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING, FINDING_ELSEWHERE]
    )
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    assert result.pending == 1
    text = result.worksheet_path.read_text(encoding="utf-8")
    assert FINDING_ELSEWHERE["claim"] not in text


def test_a_clean_control_run_has_nothing_to_adjudicate(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(tmp_path, bug_ids=[], findings=[FINDING_MATCHING])
    with pytest.raises(AdjudicationError, match="clean-control"):
        export_for_review(
            run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
        )


def test_a_bug_id_not_in_the_manifest_is_refused(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-999"], findings=[FINDING_MATCHING])
    with pytest.raises(AdjudicationError, match="B-999"):
        export_for_review(
            run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
        )


def test_already_ruled_candidates_are_excluded(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(
        tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING, FINDING_ALSO_MATCHING]
    )
    # Rule F-1 first via a real export/import round trip, then export again.
    first = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w1.txt"
    )
    text = first.worksheet_path.read_text(encoding="utf-8")
    # Whichever of the two landed as item 1, rule both, then only re-export
    # after ruling just one to see the other one still pending.
    text = fill(text, {1: ("same_root_cause", "matches"), 2: ("same_root_cause", "matches too")})
    (first.worksheet_path).write_text(text, encoding="utf-8")
    apply_review(
        worksheet_path=first.worksheet_path,
        keymap_path=first.keymap_path,
        ledger_path=ledger_path,
        adjudicator_id="A1",
        decided_at="2026-08-06",
    )

    second = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w2.txt"
    )
    assert second.pending == 0
    assert second.already_ruled == 2
    assert second.worksheet_path is None


class _RecordingRandom(random.Random):
    """A real RNG that also counts how many times it was asked to shuffle."""

    def __init__(self) -> None:
        super().__init__(0)
        self.shuffle_calls = 0

    def shuffle(self, x: Any) -> None:  # type: ignore[override]
        self.shuffle_calls += 1
        super().shuffle(x)


def test_the_pending_pairs_are_actually_shuffled(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    """Not a probabilistic check on the resulting order — two seeds can land on
    the same permutation of two items by pure chance — but a direct assertion
    that the shuffle call this depends on actually happened."""
    run_dir = write_run(
        tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING, FINDING_ALSO_MATCHING]
    )
    rng = _RecordingRandom()
    export_for_review(
        run_dir,
        fixture_dir=fixture_dir,
        ledger_path=ledger_path,
        out_path=tmp_path / "w.txt",
        rng=rng,
    )
    assert rng.shuffle_calls == 1


def test_a_single_pending_pair_is_not_an_error_for_shuffling(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    """Shuffling a one-item list is a no-op either way; this just proves the
    code path handles it rather than assuming there are always at least two."""
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    assert result.pending == 1


def test_a_missing_run_directory_is_refused(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    with pytest.raises(AdjudicationError, match="run evidence"):
        export_for_review(
            tmp_path / "nope",
            fixture_dir=fixture_dir,
            ledger_path=ledger_path,
            out_path=tmp_path / "w.txt",
        )


# ------------------------------------------------------------------- import


def test_import_writes_a_ruling_the_ledger_can_load(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    text = fill(
        result.worksheet_path.read_text(encoding="utf-8"),
        {1: ("same_root_cause", "The excerpt confirms it.")},
    )
    result.worksheet_path.write_text(text, encoding="utf-8")

    outcome = apply_review(
        worksheet_path=result.worksheet_path,
        keymap_path=result.keymap_path,
        ledger_path=ledger_path,
        adjudicator_id="A1",
        decided_at="2026-08-06",
    )
    assert outcome.ruled == 1

    ledger = load_ledger(ledger_path, kind=LedgerKind.FORMAL)
    assert len(ledger) == 1


def test_import_preserves_entries_already_in_the_ledger(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    """The append has to be additive — read_entries + write_entries, not a
    fresh write that would discard history write_entries cannot see."""
    from coding_agent_eval.evaluator.ledger import LedgerKey, build_entry

    pre_existing = build_entry(
        key=LedgerKey(fixture_version="9.9.9", bug_id="fx-other/B-1", finding_hash="a" * 64),
        decision="insufficient",
        rationale="Unrelated prior ruling.",
        adjudicator_id="A1",
        decided_at="2026-08-01",
    )
    write_entries(ledger_path, [pre_existing])

    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    text = fill(
        result.worksheet_path.read_text(encoding="utf-8"),
        {1: ("same_root_cause", "Confirmed.")},
    )
    result.worksheet_path.write_text(text, encoding="utf-8")
    apply_review(
        worksheet_path=result.worksheet_path,
        keymap_path=result.keymap_path,
        ledger_path=ledger_path,
        adjudicator_id="A1",
        decided_at="2026-08-06",
    )

    entries = read_entries(ledger_path, kind=LedgerKind.FORMAL)
    assert len(entries) == 2
    assert any(e["rationale"] == "Unrelated prior ruling." for e in entries)


def test_reimporting_the_same_worksheet_is_refused_not_duplicated(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    text = fill(
        result.worksheet_path.read_text(encoding="utf-8"),
        {1: ("same_root_cause", "Confirmed.")},
    )
    result.worksheet_path.write_text(text, encoding="utf-8")
    kwargs = dict(
        worksheet_path=result.worksheet_path,
        keymap_path=result.keymap_path,
        ledger_path=ledger_path,
        adjudicator_id="A1",
        decided_at="2026-08-06",
    )
    apply_review(**kwargs)
    with pytest.raises(AdjudicationError, match="already exist"):
        apply_review(**kwargs)

    assert len(read_entries(ledger_path, kind=LedgerKind.FORMAL)) == 1


def test_a_synthetic_adjudicator_id_is_refused_for_the_formal_ledger(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    text = fill(
        result.worksheet_path.read_text(encoding="utf-8"),
        {1: ("same_root_cause", "Confirmed.")},
    )
    result.worksheet_path.write_text(text, encoding="utf-8")
    with pytest.raises(AdjudicationError, match=SYNTHETIC_PREFIX):
        apply_review(
            worksheet_path=result.worksheet_path,
            keymap_path=result.keymap_path,
            ledger_path=ledger_path,
            adjudicator_id=f"{SYNTHETIC_PREFIX}test",
            decided_at="2026-08-06",
        )
    assert read_entries(ledger_path, kind=LedgerKind.FORMAL) == []


def test_an_incomplete_worksheet_is_refused(
    fixture_dir: Path, ledger_path: Path, tmp_path: Path
) -> None:
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    with pytest.raises(Exception, match="no decision"):
        apply_review(
            worksheet_path=result.worksheet_path,
            keymap_path=result.keymap_path,
            ledger_path=ledger_path,
            adjudicator_id="A1",
            decided_at="2026-08-06",
        )


def test_a_missing_keymap_is_refused(fixture_dir: Path, ledger_path: Path, tmp_path: Path) -> None:
    run_dir = write_run(tmp_path, bug_ids=["fx-demo/B-001"], findings=[FINDING_MATCHING])
    result = export_for_review(
        run_dir, fixture_dir=fixture_dir, ledger_path=ledger_path, out_path=tmp_path / "w.txt"
    )
    with pytest.raises(AdjudicationError, match="no key map"):
        apply_review(
            worksheet_path=result.worksheet_path,
            keymap_path=tmp_path / "absent.keymap.json",
            ledger_path=ledger_path,
            adjudicator_id="A1",
            decided_at="2026-08-06",
        )
