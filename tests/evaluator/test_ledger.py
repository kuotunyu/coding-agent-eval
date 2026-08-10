"""The adjudication ledger (design spec §6.6, §8.3, §8.3.1).

Two properties carry the whole design.

Append-only with content hashing, so a frozen ledger replays identically and a
silent edit is detectable. If a ruling could be rewritten in place, every number
derived from it would be unfalsifiable.

A hard separation between real human rulings and synthetic test data. The
evaluator has to be able to produce numbers in CI without a person, and the
mechanism that makes that possible is exactly the mechanism that could fabricate
a human ruling. So the boundary is enforced in code and fails closed: the formal
loader refuses a synthetic entry outright, and anything scored against synthetic
data is stamped unpublishable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from coding_agent_eval import ADJUDICATION_PROTOCOL_VERSION
from coding_agent_eval.evaluator.ledger import (
    SYNTHETIC_PREFIX,
    Decision,
    LedgerError,
    LedgerKey,
    LedgerKind,
    build_entry,
    entry_hash,
    load_ledger,
    write_entries,
)

KEY = LedgerKey(fixture_version="1.0.0", bug_id="fx-taskq-py/B-001", finding_hash="a" * 64)
OTHER_KEY = LedgerKey(fixture_version="1.0.0", bug_id="fx-taskq-py/B-002", finding_hash="b" * 64)


def human_entry(key: LedgerKey = KEY, decision: str = "same_root_cause") -> dict:
    return build_entry(
        key=key,
        decision=decision,
        rationale="Same mechanism, and the cited line does contain the comparison.",
        adjudicator_id="A1",
        decided_at="2026-08-05",
    )


def synthetic_entry(key: LedgerKey = KEY, decision: str = "same_root_cause") -> dict:
    return build_entry(
        key=key,
        decision=decision,
        rationale="Synthetic fixture for evaluator arithmetic.",
        adjudicator_id=f"{SYNTHETIC_PREFIX}scenario-a",
        decided_at="2026-08-05",
    )


# --------------------------------------------------------------- entry shape


def test_entry_carries_the_protocol_version() -> None:
    assert human_entry()["adjudication_protocol_version"] == ADJUDICATION_PROTOCOL_VERSION


def test_entry_hash_covers_the_ruling() -> None:
    entry = human_entry()
    tampered = dict(entry, decision="different_root_cause")
    assert entry_hash(tampered) != entry["entry_hash"]


def test_entry_hash_covers_the_key() -> None:
    assert entry_hash(dict(human_entry(), key=OTHER_KEY.as_dict())) != human_entry()["entry_hash"]


def test_entry_hash_covers_the_rationale() -> None:
    """A ruling whose stated reason can be swapped is not auditable."""
    entry = human_entry()
    assert entry_hash(dict(entry, rationale="Changed my mind.")) != entry["entry_hash"]


def test_entry_hash_excludes_itself() -> None:
    entry = human_entry()
    assert entry_hash(dict(entry, entry_hash="0" * 64)) == entry["entry_hash"]


# ------------------------------------------------------------- append-only


def test_entries_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "adjudications.jsonl"
    write_entries(path, [human_entry()])
    ledger = load_ledger(path, kind=LedgerKind.FORMAL)
    assert ledger.decision(KEY) is Decision.SAME_ROOT_CAUSE


def test_an_unknown_key_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "adjudications.jsonl"
    write_entries(path, [human_entry()])
    assert load_ledger(path, kind=LedgerKind.FORMAL).decision(OTHER_KEY) is None


def test_a_repeated_key_is_rejected(tmp_path: Path) -> None:
    """Append-only means append, not overwrite."""
    path = tmp_path / "adjudications.jsonl"
    write_entries(path, [human_entry(), human_entry(decision="different_root_cause")])
    with pytest.raises(LedgerError, match="already"):
        load_ledger(path, kind=LedgerKind.FORMAL)


def test_a_tampered_entry_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "adjudications.jsonl"
    entry = human_entry()
    entry["decision"] = "different_root_cause"  # hash left untouched
    write_entries(path, [entry])
    with pytest.raises(LedgerError, match="hash"):
        load_ledger(path, kind=LedgerKind.FORMAL)


def test_a_malformed_entry_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "adjudications.jsonl"
    path.write_text('{"decision": "same_root_cause"}\n', encoding="utf-8")
    with pytest.raises(LedgerError):
        load_ledger(path, kind=LedgerKind.FORMAL)


def test_a_missing_ledger_file_is_empty_not_an_error(tmp_path: Path) -> None:
    """A benchmark with nothing adjudicated yet is a real state, not a failure."""
    ledger = load_ledger(tmp_path / "absent.jsonl", kind=LedgerKind.FORMAL)
    assert ledger.decision(KEY) is None
    assert len(ledger) == 0


# ------------------------------------------------- formal / synthetic split


def test_the_formal_loader_refuses_a_synthetic_entry(tmp_path: Path) -> None:
    """The one rule that stops an agent manufacturing human rulings."""
    path = tmp_path / "adjudications.jsonl"
    write_entries(path, [synthetic_entry()])
    with pytest.raises(LedgerError, match="synthetic"):
        load_ledger(path, kind=LedgerKind.FORMAL)


def test_the_formal_loader_refuses_a_mixed_file(tmp_path: Path) -> None:
    path = tmp_path / "adjudications.jsonl"
    write_entries(path, [human_entry(), synthetic_entry(key=OTHER_KEY)])
    with pytest.raises(LedgerError, match="synthetic"):
        load_ledger(path, kind=LedgerKind.FORMAL)


def test_the_synthetic_loader_requires_the_prefix(tmp_path: Path) -> None:
    """Symmetry matters: a real ruling must not be smuggled into a test fixture."""
    path = tmp_path / "synthetic_adjudications.jsonl"
    write_entries(path, [human_entry()])
    with pytest.raises(LedgerError, match="SYNTHETIC"):
        load_ledger(path, kind=LedgerKind.SYNTHETIC)


def test_a_synthetic_ledger_is_not_publishable(tmp_path: Path) -> None:
    path = tmp_path / "synthetic_adjudications.jsonl"
    write_entries(path, [synthetic_entry()])
    ledger = load_ledger(path, kind=LedgerKind.SYNTHETIC)
    assert ledger.kind is LedgerKind.SYNTHETIC
    assert ledger.publishable is False


def test_a_formal_ledger_is_publishable(tmp_path: Path) -> None:
    path = tmp_path / "adjudications.jsonl"
    write_entries(path, [human_entry()])
    assert load_ledger(path, kind=LedgerKind.FORMAL).publishable is True


def test_the_synthetic_prefix_is_case_sensitive_and_exact(tmp_path: Path) -> None:
    """`synthetic-` lowercase must not pass for a real adjudicator id either way."""
    path = tmp_path / "adjudications.jsonl"
    entry = build_entry(
        key=KEY,
        decision="same_root_cause",
        rationale="Looks human, is not.",
        adjudicator_id="synthetic-lowercase",
        decided_at="2026-08-05",
    )
    write_entries(path, [entry])
    # It does not carry the marker, so the formal loader accepts it; the guard is
    # the exact prefix, and this test records that limitation honestly.
    assert load_ledger(path, kind=LedgerKind.FORMAL).decision(KEY) is Decision.SAME_ROOT_CAUSE


# -------------------------------------------------------------- decisions


def test_insufficient_is_not_a_verification() -> None:
    """Conservative by design: uncertainty must not count as a match."""
    assert Decision.INSUFFICIENT.verifies is False
    assert Decision.DIFFERENT_ROOT_CAUSE.verifies is False
    assert Decision.SAME_ROOT_CAUSE.verifies is True


def test_an_unknown_decision_value_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "adjudications.jsonl"
    with pytest.raises(LedgerError):
        write_entries(
            path,
            [
                build_entry(
                    key=KEY,
                    decision="probably",
                    rationale="Not one of the three.",
                    adjudicator_id="A1",
                    decided_at="2026-08-05",
                )
            ],
        )


# ------------------------------------------------- the committed ledger


def test_the_committed_formal_ledger_contains_only_valid_human_rulings(repo_root: Path) -> None:
    """Human rulings may ship; synthetic rulings may never enter the formal ledger."""
    path = repo_root / "ledger" / "adjudications.jsonl"
    assert path.is_file(), "the formal ledger must exist so its contents are reviewable"
    ledger = load_ledger(path, kind=LedgerKind.FORMAL)
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    assert len(ledger) == len(entries) == 2
    assert all(not entry["adjudicator_id"].startswith(SYNTHETIC_PREFIX) for entry in entries)
