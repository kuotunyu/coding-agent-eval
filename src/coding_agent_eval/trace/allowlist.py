"""Raw field classification (design spec §10.2, correction 8).

Every field of every raw event is one of three things:

* ``PUBLIC`` — projected into the public trace
* ``KNOWN_PRIVATE`` — dropped on purpose, because someone decided it must not be
  published
* unknown — not classified, which raises

The distinction between the second and third is the point. Silently ignoring an
unknown field means a newly added raw field either leaks into the public trace
or disappears from the record, and in both cases nobody finds out. Raising forces
the decision to be made once, explicitly, by whoever adds the field.

This is a data table rather than scattered conditionals so the contract can be
diffed. A reviewer should be able to see the privacy decision change in a pull
request, not have to trace it through code.
"""

from __future__ import annotations

from enum import Enum


class FieldClass(Enum):
    PUBLIC = "public"
    KNOWN_PRIVATE = "known_private"


class UnknownFieldError(RuntimeError):
    """A raw field has no classification, so its privacy has never been decided."""


#: Fields projected into the public trace, by event.
PUBLIC_FIELDS: dict[str, frozenset[str]] = {
    "run_header": frozenset(
        {
            "run_id",
            "benchmark_version",
            "fixture_id",
            "fixture_version",
            "fixture_tree_checksum",
            "snapshot",
            "bug_set_hash",
            "agent_adapter",
            "agent_adapter_version",
            "provider",
            "model",
            "prompt_hash",
            "system_prompt_version",
            "params_hash",
            "seed",
            "image_ref",
            "image_manifest_digest",
            "image_config_digest",
            "env_fingerprint",
            "sandbox_profile",
            "tool_backend",
            "budget",
            "redaction_manifest_version",
        }
    ),
    "llm_call": frozenset(
        {
            "request_hash",
            "request_write",
            "latency_ms",
            "finish_reason",
            "usage",
            "executable_tool_call_limit",
            "executable_tool_calls_remaining",
            "interface_mode",
            "tools_offered",
        }
    ),
    "tool_call": frozenset({"tool_name", "args_safe", "args_hash"}),
    "tool_result": frozenset(
        {"is_error", "content_sha256", "content_bytes", "excerpt", "excerpt_policy"}
    ),
    "context_compression": frozenset(
        {
            "strategy_version",
            "pre_view_hash",
            "post_view_hash",
            "raw_content_sha256",
            "replaced_count",
        }
    ),
    "findings_submitted": frozenset({"findings"}),
    # `provider_error` carries the provider's own structural classification of a
    # failure — exception type, HTTP status, error type, code, param. None of it
    # is content, and without it a failed run cannot be acted on at all.
    "termination": frozenset(
        {
            "reason",
            "steps",
            "tool_calls",
            "wall_clock_ms",
            "provider_error",
            "adapter_error",
        }
    ),
    "cost": frozenset(
        {
            "estimated_cost_usd",
            "completeness",
            "unknown_fields",
            "pricing_table_version",
            "pricing_effective_date",
            "pricing_source",
            "estimator_limitations",
        }
    ),
}

#: Fields kept only in the private store. Dropping these is the intent, not a failure.
KNOWN_PRIVATE_FIELDS: dict[str, frozenset[str]] = {
    "run_header": frozenset({"system_prompt", "params", "host_paths"}),
    "llm_call": frozenset({"request_body", "response_body", "messages"}),
    "tool_call": frozenset({"args_raw"}),
    "tool_result": frozenset({"content"}),
    "context_compression": frozenset({"removed_content", "pre_view", "post_view"}),
    "findings_submitted": frozenset(),
    # The provider's free text. Useful to the operator, and a provider may quote
    # the request back — which here would be source code the agent had read — so
    # it stays out of the projection and is written to the run directory instead.
    "termination": frozenset({"final_message", "provider_error_message", "adapter_error_message"}),
    "cost": frozenset({"provider_raw_usage"}),
}


def classify(event: str, field: str) -> FieldClass:
    """Classify one field, raising when it has never been classified."""
    if event not in PUBLIC_FIELDS:
        raise UnknownFieldError(
            f"unknown raw event type {event!r}; classify its fields in allowlist.py before "
            "any run can be published"
        )
    if field in PUBLIC_FIELDS[event]:
        return FieldClass.PUBLIC
    if field in KNOWN_PRIVATE_FIELDS.get(event, frozenset()):
        return FieldClass.KNOWN_PRIVATE
    raise UnknownFieldError(
        f"field {field!r} on event {event!r} is neither public nor known-private; "
        "add it to allowlist.py so its privacy is a recorded decision"
    )
