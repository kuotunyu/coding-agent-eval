"""Public trace projection (design spec §10.2, correction 8).

Every raw field is classified as one of three things, and the difference between
the last two is the whole point:

* **public** — projected into the trace
* **known private** — dropped deliberately, silently, because someone decided it
  must not be published
* **unknown** — not classified at all, which fails closed

Silently ignoring an unknown field would mean a newly added raw field could
either leak or vanish from the public record, and nobody would find out either
way. Refusing forces the decision to be made explicitly, once, by a person.
"""

from __future__ import annotations

from typing import Any

import pytest

from coding_agent_eval.trace.allowlist import (
    FieldClass,
    UnknownFieldError,
    classify,
)
from coding_agent_eval.trace.public_trace import ExcerptPolicy, project_record

RUN_HEADER: dict[str, Any] = {
    "run_id": "run-1",
    "benchmark_version": "0.1.0",
    "fixture_id": "fx-taskq-py",
    "fixture_version": "1.0.0",
    "fixture_tree_checksum": "sha256:" + "a" * 64,
    "snapshot": "mutated",
    "bug_set_hash": "b" * 64,
    "agent_adapter": "deterministic",
    "agent_adapter_version": "0.1.0",
    "provider": None,
    "model": None,
    "prompt_hash": "c" * 64,
    "system_prompt_version": "0.1.0",
    "params_hash": "d" * 64,
    "seed": 7,
    "image_ref": ("ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@sha256:" + "e" * 64),
    "image_manifest_digest": "sha256:" + "e" * 64,
    "image_config_digest": "sha256:" + "1" * 64,
    "env_fingerprint": "sha256:" + "f" * 64,
    "sandbox_profile": "measure",
    "tool_backend": "measure_container:sha256:" + "e" * 64,
    "budget": {"max_tokens": 200000},
    "redaction_manifest_version": "0.1.0",
}


def record(event: str, payload: dict[str, Any], seq: int = 0) -> dict[str, Any]:
    return {"seq": seq, "ts": "2026-08-05T00:00:00+00:00", "event": event, "payload": payload}


# ------------------------------------------------------------ classification


def test_a_public_field_is_classified_public() -> None:
    assert classify("run_header", "run_id") is FieldClass.PUBLIC


@pytest.mark.parametrize("field", ["image_ref", "image_manifest_digest", "image_config_digest"])
def test_trace_0_2_oci_identity_fields_are_public(field: str) -> None:
    assert classify("run_header", field) is FieldClass.PUBLIC


def test_legacy_ambiguous_image_digest_is_not_a_writer_field() -> None:
    with pytest.raises(UnknownFieldError, match="image_digest"):
        classify("run_header", "image_digest")


def test_a_known_private_field_is_classified_private() -> None:
    assert classify("tool_result", "content") is FieldClass.KNOWN_PRIVATE


def test_an_unclassified_field_raises() -> None:
    with pytest.raises(UnknownFieldError, match="surprise"):
        classify("tool_result", "surprise")


def test_the_error_names_the_event_and_the_field() -> None:
    with pytest.raises(UnknownFieldError) as exc:
        classify("llm_call", "mystery_field")
    assert "llm_call" in str(exc.value)
    assert "mystery_field" in str(exc.value)


def test_an_unknown_event_type_raises() -> None:
    with pytest.raises(UnknownFieldError, match="no_such_event"):
        classify("no_such_event", "anything")


# ---------------------------------------------------------------- projection


def test_a_run_header_projects_every_public_field() -> None:
    public = project_record(record("run_header", RUN_HEADER))
    assert public["payload"] == RUN_HEADER


def test_the_envelope_is_preserved() -> None:
    public = project_record(record("run_header", RUN_HEADER, seq=5))
    assert public["seq"] == 5
    assert public["event"] == "run_header"
    assert public["schema_version"] == "0.2.0"


def test_a_known_private_field_is_dropped_without_complaint() -> None:
    """Dropping is the decision, not a failure."""
    public = project_record(
        record(
            "tool_result",
            {
                "is_error": False,
                "content_sha256": "a" * 64,
                "content_bytes": 4096,
                "excerpt": "<redacted>",
                "excerpt_policy": "third_party",
                "content": "the entire file, which must not be published",
            },
        )
    )
    assert "content" not in public["payload"]
    assert public["payload"]["content_sha256"] == "a" * 64


def test_an_unknown_field_makes_the_projection_raise() -> None:
    """E3 turns this into a fail-closed rejection of the whole artifact."""
    with pytest.raises(UnknownFieldError):
        project_record(
            record("tool_result", {"is_error": False, "newly_added_by_someone": "value"})
        )


def test_findings_are_retained_in_full() -> None:
    """Replay scores from the public trace, so the findings themselves must survive."""
    findings = [
        {
            "id": "F-001",
            "file": "src/auth.py",
            "line_start": 1,
            "line_end": 2,
            "category": "security",
            "severity": "high",
            "claim": "c",
            "root_cause": "r",
            "evidence": "e",
            "suggested_verification": "v",
        }
    ]
    public = project_record(record("findings_submitted", {"findings": findings}))
    assert public["payload"]["findings"] == findings


def test_context_compression_carries_hashes_and_never_content() -> None:
    public = project_record(
        record(
            "context_compression",
            {
                "strategy_version": "0.1.0",
                "pre_view_hash": "a" * 64,
                "post_view_hash": "b" * 64,
                "raw_content_sha256": ["c" * 64],
                "replaced_count": 3,
                "removed_content": "the text that was compressed away",
            },
        )
    )
    assert "removed_content" not in public["payload"]
    assert public["payload"]["raw_content_sha256"] == ["c" * 64]


def test_cost_records_unknown_fields_rather_than_zero() -> None:
    public = project_record(
        record(
            "cost",
            {
                "estimated_cost_usd": None,
                "completeness": "partial",
                "unknown_fields": ["reasoning_tokens"],
                "pricing_table_version": "none-offline",
                "pricing_effective_date": "2026-08-05",
                "pricing_source": "n/a",
                "estimator_limitations": ["no live pricing in v0.1"],
            },
        )
    )
    assert public["payload"]["estimated_cost_usd"] is None
    assert public["payload"]["unknown_fields"] == ["reasoning_tokens"]


# ------------------------------------------------------------------ excerpts


def test_first_party_content_may_carry_a_short_excerpt() -> None:
    excerpt = ExcerptPolicy.excerpt_for("first_party", "def verify(a, b):\n    return a == b\n")
    assert "def verify" in excerpt


def test_third_party_content_is_always_redacted() -> None:
    """Upstream source is not ours to republish, whatever its length."""
    assert ExcerptPolicy.excerpt_for("third_party", "x = 1\n") == "<redacted>"


def test_first_party_excerpts_are_truncated_at_the_limit() -> None:
    excerpt = ExcerptPolicy.excerpt_for("first_party", "y" * 5000)
    assert len(excerpt.encode("utf-8")) <= ExcerptPolicy.FIRST_PARTY_BYTES + 32


def test_harness_output_gets_the_larger_allowance() -> None:
    """Our own error strings are safe to publish and are what a reader needs."""
    assert ExcerptPolicy.HARNESS_BYTES > ExcerptPolicy.FIRST_PARTY_BYTES
    excerpt = ExcerptPolicy.excerpt_for("harness", "tool failed: file not found")
    assert "file not found" in excerpt


def test_an_unknown_excerpt_policy_raises() -> None:
    with pytest.raises(ValueError, match="somewhere_else"):
        ExcerptPolicy.excerpt_for("somewhere_else", "x")
