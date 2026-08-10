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
from hashlib import sha256
from pathlib import Path
from typing import Any

import pytest
import yaml

from coding_agent_eval.adjudication import (
    AdjudicationError,
    apply_resolver_review,
    apply_review,
    apply_review_slot,
    export_for_review,
    export_resolver_review,
    export_review_slot,
    init_review_set,
)
from coding_agent_eval.evaluator.ledger import (
    SYNTHETIC_PREFIX,
    LedgerKey,
    LedgerKind,
    load_ledger,
    read_entries,
    write_entries,
)
from coding_agent_eval.evaluator.review_set import ReviewSetEvidence, load_review_set
from coding_agent_eval.evaluator.worksheet import (
    DECISION_MARKER,
    RATIONALE_MARKER,
)

ORIGINAL_AUTH = "def verify(a, b):\n    return compare_digest(a, b)\n"
TREE_CHECKSUM = "sha256:" + "b" * 64
ENV_FINGERPRINT = "sha256:" + "c" * 64

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
        "clean_control": {"tree_checksum": TREE_CHECKSUM},
        "environment": {"fingerprint": ENV_FINGERPRINT},
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


# ---------------------------------------------------------- dual-review flow


def write_review_inputs(
    root: Path, fixture_dir: Path, findings: list[dict[str, Any]]
) -> tuple[Path, Path]:
    bug = yaml.safe_load((fixture_dir / "bugs" / "B-001.yaml").read_text(encoding="utf-8"))
    bugs_path = root / "bugs.json"
    bugs_path.write_text(json.dumps([bug]), encoding="utf-8")
    manifest_digest = "sha256:" + "a" * 64
    records = [
        {
            "schema_version": "0.2.0",
            "seq": 0,
            "ts": "2026-08-11T00:00:00+00:00",
            "event": "run_header",
            "payload": {
                "run_id": "reference-fx-demo-mutated",
                "fixture_id": "fx-demo",
                "fixture_version": "1.0.0",
                "fixture_tree_checksum": TREE_CHECKSUM,
                "snapshot": "mutated",
                "bug_set_hash": sha256(
                    json.dumps([bug["bug_id"]], sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "env_fingerprint": ENV_FINGERPRINT,
                "image_ref": "ghcr.io/kuotunyu/coding-agent-eval-fx-demo-py@" + manifest_digest,
                "image_manifest_digest": manifest_digest,
                "image_config_digest": "sha256:" + "d" * 64,
                "sandbox_profile": "measure",
                "tool_backend": "measure_container:" + manifest_digest,
            },
        },
        {
            "schema_version": "0.2.0",
            "seq": 1,
            "ts": "2026-08-11T00:00:01+00:00",
            "event": "findings_submitted",
            "payload": {"findings": findings},
        },
        {
            "schema_version": "0.2.0",
            "seq": 2,
            "ts": "2026-08-11T00:00:02+00:00",
            "event": "cost",
            "payload": {},
        },
        {
            "schema_version": "0.2.0",
            "seq": 3,
            "ts": "2026-08-11T00:00:03+00:00",
            "event": "termination",
            "payload": {},
        },
    ]
    trace_path = root / "trace.jsonl"
    trace_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return trace_path, bugs_path


def initialized_review_set(tmp_path: Path, fixture_dir: Path) -> Path:
    trace, bugs = write_review_inputs(
        tmp_path, fixture_dir, [FINDING_MATCHING, FINDING_ALSO_MATCHING]
    )
    review_set = tmp_path / "review-set"
    init_review_set(
        trace_path=trace,
        bugs_path=bugs,
        fixture_dir=fixture_dir,
        review_set_dir=review_set,
        fixture_author_ids=("kuotunyu",),
        run_operator_id="kuotunyu",
        primary_id="kuotunyu",
        independent_id="reviewer-b",
    )
    return review_set


def test_review_set_init_freezes_inputs_without_creating_rulings(
    fixture_dir: Path, tmp_path: Path
) -> None:
    review_set = initialized_review_set(tmp_path, fixture_dir)
    manifest = json.loads((review_set / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["trace_schema_version"] == "0.2.0"
    assert manifest["candidate_set_sha256"].startswith("sha256:")
    assert manifest["candidate_materials_sha256"].startswith("sha256:")
    assert manifest["fixture_manifest_sha256"].startswith("sha256:")
    assert (review_set / "primary.jsonl").read_bytes() == b""
    assert (review_set / "independent.jsonl").read_bytes() == b""
    assert (review_set / "resolutions.jsonl").read_bytes() == b""


def test_review_set_export_refuses_candidate_material_drift(
    fixture_dir: Path, tmp_path: Path
) -> None:
    review_set = initialized_review_set(tmp_path, fixture_dir)
    materials_path = review_set / "candidates.json"
    materials = json.loads(materials_path.read_text(encoding="utf-8"))
    materials["items"][0]["item"]["bug_claim"] = "tampered reviewer context"
    materials_path.write_text(json.dumps(materials), encoding="utf-8")

    with pytest.raises(AdjudicationError, match="candidate materials"):
        export_review_slot(
            review_set,
            slot="primary",
            worksheet_path=tmp_path / "primary.txt",
            keymap_path=tmp_path / "primary.keymap.json",
        )


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    [
        ("fixture_tree_checksum", "sha256:" + "e" * 64, "tree checksum"),
        ("env_fingerprint", "sha256:" + "e" * 64, "environment fingerprint"),
        ("bug_set_hash", "e" * 64, "bug-set hash"),
    ],
)
def test_review_set_init_refuses_trace_fixture_identity_drift(
    fixture_dir: Path,
    tmp_path: Path,
    field: str,
    replacement: str,
    message: str,
) -> None:
    trace, bugs = write_review_inputs(tmp_path, fixture_dir, [FINDING_MATCHING])
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    records[0]["payload"][field] = replacement
    trace.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(AdjudicationError, match=message):
        init_review_set(
            trace_path=trace,
            bugs_path=bugs,
            fixture_dir=fixture_dir,
            review_set_dir=tmp_path / "review-set",
            fixture_author_ids=("kuotunyu",),
            run_operator_id="kuotunyu",
            primary_id="kuotunyu",
            independent_id="reviewer-b",
        )


def test_review_set_init_refuses_host_process_trace(fixture_dir: Path, tmp_path: Path) -> None:
    trace, bugs = write_review_inputs(tmp_path, fixture_dir, [FINDING_MATCHING])
    records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
    header = records[0]["payload"]
    header.update(
        {
            "sandbox_profile": "host_process",
            "image_ref": None,
            "image_manifest_digest": None,
            "image_config_digest": None,
            "tool_backend": "host_process",
        }
    )
    trace.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(AdjudicationError, match="measure sandbox"):
        init_review_set(
            trace_path=trace,
            bugs_path=bugs,
            fixture_dir=fixture_dir,
            review_set_dir=tmp_path / "review-set",
            fixture_author_ids=("kuotunyu",),
            run_operator_id="kuotunyu",
            primary_id="kuotunyu",
            independent_id="reviewer-b",
        )


@pytest.mark.parametrize(
    ("fixture_author_ids", "run_operator_id"),
    [("owner@example.com", "kuotunyu"), ("kuotunyu", "operator@example.com")],
)
def test_review_set_init_refuses_private_identity_data(
    fixture_dir: Path,
    tmp_path: Path,
    fixture_author_ids: str,
    run_operator_id: str,
) -> None:
    trace, bugs = write_review_inputs(tmp_path, fixture_dir, [FINDING_MATCHING])

    with pytest.raises(AdjudicationError, match="manifest would be invalid"):
        init_review_set(
            trace_path=trace,
            bugs_path=bugs,
            fixture_dir=fixture_dir,
            review_set_dir=tmp_path / "review-set",
            fixture_author_ids=(fixture_author_ids,),
            run_operator_id=run_operator_id,
            primary_id="kuotunyu",
            independent_id="reviewer-b",
        )


def test_primary_and_independent_exports_have_distinct_bound_shuffle_orders(
    fixture_dir: Path, tmp_path: Path
) -> None:
    review_set = initialized_review_set(tmp_path, fixture_dir)
    primary = export_review_slot(
        review_set,
        slot="primary",
        worksheet_path=tmp_path / "primary.txt",
        keymap_path=tmp_path / "primary.keymap.json",
    )
    independent = export_review_slot(
        review_set,
        slot="independent",
        worksheet_path=tmp_path / "independent.txt",
        keymap_path=tmp_path / "independent.keymap.json",
    )
    primary_map = json.loads(primary.keymap_path.read_text(encoding="utf-8"))
    independent_map = json.loads(independent.keymap_path.read_text(encoding="utf-8"))

    assert primary_map["review_set_id"] == independent_map["review_set_id"]
    assert primary_map["slot"] == "primary"
    assert independent_map["slot"] == "independent"
    assert list(primary_map["entries"].values()) != list(independent_map["entries"].values())


def test_cross_slot_keymap_and_malformed_import_leave_ledgers_unchanged(
    fixture_dir: Path, tmp_path: Path
) -> None:
    review_set = initialized_review_set(tmp_path, fixture_dir)
    exported = export_review_slot(
        review_set,
        slot="primary",
        worksheet_path=tmp_path / "primary.txt",
        keymap_path=tmp_path / "primary.keymap.json",
    )
    before = (review_set / "independent.jsonl").read_bytes()

    with pytest.raises(AdjudicationError, match="slot"):
        apply_review_slot(
            review_set,
            slot="independent",
            worksheet_path=exported.worksheet_path,
            keymap_path=exported.keymap_path,
            decided_at="2026-08-11",
        )
    assert (review_set / "independent.jsonl").read_bytes() == before

    malformed_keymap = tmp_path / "malformed.keymap.json"
    malformed_keymap.write_text("{", encoding="utf-8")
    with pytest.raises(AdjudicationError, match="well-formed key map"):
        apply_review_slot(
            review_set,
            slot="primary",
            worksheet_path=exported.worksheet_path,
            keymap_path=malformed_keymap,
            decided_at="2026-08-11",
        )
    assert (review_set / "primary.jsonl").read_bytes() == b""

    with pytest.raises(Exception, match="no decision"):
        apply_review_slot(
            review_set,
            slot="primary",
            worksheet_path=exported.worksheet_path,
            keymap_path=exported.keymap_path,
            decided_at="2026-08-11",
        )
    assert (review_set / "primary.jsonl").read_bytes() == b""

    tampered = exported.worksheet_path.read_text(encoding="utf-8").replace(
        "Bug claim:", "Altered bug claim:", 1
    )
    exported.worksheet_path.write_text(
        fill(
            tampered,
            {
                1: ("same_root_cause", "Reviewed by the assigned human."),
                2: ("same_root_cause", "Reviewed by the assigned human."),
            },
        ),
        encoding="utf-8",
    )
    with pytest.raises(AdjudicationError, match="worksheet content"):
        apply_review_slot(
            review_set,
            slot="primary",
            worksheet_path=exported.worksheet_path,
            keymap_path=exported.keymap_path,
            decided_at="2026-08-11",
        )
    assert (review_set / "primary.jsonl").read_bytes() == b""


def test_disagreement_exports_one_resolver_item_and_round_trips(
    fixture_dir: Path, tmp_path: Path
) -> None:
    review_set = initialized_review_set(tmp_path, fixture_dir)
    primary = export_review_slot(
        review_set,
        slot="primary",
        worksheet_path=tmp_path / "primary.txt",
        keymap_path=tmp_path / "primary.keymap.json",
    )
    independent = export_review_slot(
        review_set,
        slot="independent",
        worksheet_path=tmp_path / "independent.txt",
        keymap_path=tmp_path / "independent.keymap.json",
    )
    primary.worksheet_path.write_text(
        fill(
            primary.worksheet_path.read_text(encoding="utf-8"),
            {
                1: ("same_root_cause", "Primary agrees."),
                2: ("same_root_cause", "Primary agrees."),
            },
        ),
        encoding="utf-8",
    )
    independent.worksheet_path.write_text(
        fill(
            independent.worksheet_path.read_text(encoding="utf-8"),
            {
                1: ("same_root_cause", "Independent agrees here."),
                2: ("different_root_cause", "Independent disagrees here."),
            },
        ),
        encoding="utf-8",
    )
    apply_review_slot(
        review_set,
        slot="primary",
        worksheet_path=primary.worksheet_path,
        keymap_path=primary.keymap_path,
        decided_at="2026-08-11",
    )
    apply_review_slot(
        review_set,
        slot="independent",
        worksheet_path=independent.worksheet_path,
        keymap_path=independent.keymap_path,
        decided_at="2026-08-11",
    )

    resolver = export_resolver_review(
        review_set,
        worksheet_path=tmp_path / "resolver.txt",
        keymap_path=tmp_path / "resolver.keymap.json",
    )
    assert resolver.pending == 1
    assert "reviewer-b" not in resolver.worksheet_path.read_text(encoding="utf-8")
    resolver.worksheet_path.write_text(
        fill(
            resolver.worksheet_path.read_text(encoding="utf-8"),
            {1: ("insufficient", "Resolver cannot establish equivalence.")},
        ),
        encoding="utf-8",
    )
    outcome = apply_resolver_review(
        review_set,
        resolver_id="reviewer-c",
        worksheet_path=resolver.worksheet_path,
        keymap_path=resolver.keymap_path,
        decided_at="2026-08-11",
    )

    assert outcome.ruled == 1
    assert len(read_entries(review_set / "resolutions.jsonl", kind=LedgerKind.FORMAL)) == 1

    manifest = json.loads((review_set / "manifest.json").read_text(encoding="utf-8"))
    materials = json.loads((review_set / "candidates.json").read_text(encoding="utf-8"))
    evidence = ReviewSetEvidence(
        run_id=manifest["run_id"],
        fixture_id=manifest["fixture_id"],
        fixture_version=manifest["fixture_version"],
        tree_checksum=manifest["tree_checksum"],
        trace_sha256=manifest["trace_sha256"],
        findings_sha256=manifest["findings_sha256"],
        fixture_manifest_sha256=manifest["fixture_manifest_sha256"],
        trace_schema_version=manifest["trace_schema_version"],
        environment_fingerprint=manifest["environment_fingerprint"],
        candidate_keys=tuple(LedgerKey.from_dict(item["key"]) for item in materials["items"]),
    )
    assert load_review_set(review_set, evidence=evidence).publishable is True
