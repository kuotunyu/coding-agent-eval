"""The OpenAI-compatible adapter, against a mock transport only (plan F3).

No test here reaches the network, and none needs an API key. That is the
requirement, not a convenience: a gate that needs a secret does not run for
anyone without one.

The properties that matter are the ones that decide whether a cost figure can be
believed — unknown is never zero, and any unknown makes the estimate partial —
and the one that decides whether a missing key can waste a request.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from coding_agent_eval.agent.loop import run_agent
from coding_agent_eval.agent.protocol import Budget, TerminationReason
from coding_agent_eval.agent.provider import (
    PLACEHOLDER_PRICING,
    OpenAICompatibleAdapter,
    PricingTable,
    ProviderConfigurationError,
    build_messages,
    estimate_cost,
    normalise_usage,
    render_system_prompt,
)
from coding_agent_eval.agent.tools import ToolContext

PRICING = PricingTable(
    version="test-1",
    effective_date="2026-01-01",
    source="https://example.invalid/pricing",
    input_per_mtok_usd=1.0,
    output_per_mtok_usd=2.0,
)


def test_system_prompt_defines_both_completion_paths_and_the_tool_budget() -> None:
    prompt = render_system_prompt(12)

    assert "at most 12 tool calls" in prompt
    assert "without calling write_findings" in prompt
    assert "one write_findings call" in prompt
    assert "final response without a tool call" in prompt


def test_unbounded_prompt_does_not_invent_a_tool_count() -> None:
    prompt = render_system_prompt(None)

    assert "at most" not in prompt
    assert "finite tool budget" in prompt


def completion(
    *,
    tool_name: str | None = "read_file",
    arguments: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    call_id: str = "call_1",
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": None}
    if tool_name is not None:
        message["tool_calls"] = [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": tool_name,
                    "arguments": json.dumps(arguments if arguments is not None else {"path": "."}),
                },
            }
        ]
    return {"choices": [{"message": message, "finish_reason": "tool_calls"}], "usage": usage or {}}


def adapter_with(handler: Any, **kwargs: Any) -> OpenAICompatibleAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAICompatibleAdapter(
        model="test-model", api_key="test-key", client=client, pricing=PRICING, **kwargs
    )


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_bytes(b"def verify(a, b):\n    return a == b\n")
    return root


# ------------------------------------------------------------------ api key


def test_a_missing_api_key_raises_without_attempting_a_request() -> None:
    """The point is the *without*: no connection, no wasted request."""
    attempted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request)
        return httpx.Response(200, json=completion())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderConfigurationError):
        OpenAICompatibleAdapter(model="test-model", api_key=None, client=client)
    assert attempted == []


def test_an_empty_api_key_is_treated_as_missing() -> None:
    with pytest.raises(ProviderConfigurationError):
        OpenAICompatibleAdapter(model="test-model", api_key="")


# ------------------------------------------------------- message conversion


def test_the_system_prompt_comes_first() -> None:
    messages = build_messages([])
    assert messages[0]["role"] == "system"


def test_the_request_carries_the_model_and_the_tools() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion())

    adapter = adapter_with(handler)
    adapter.next_step(
        tools=[{"name": "read_file", "description": "d", "parameters": {}}], transcript=[]
    )

    body = captured[0]
    assert body["model"] == "test-model"
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["function"]["name"] == "read_file"
    assert body["messages"][0]["role"] == "system"


def test_chat_requests_disable_parallel_tool_calls() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion(tool_name=None))

    adapter_with(handler).next_step(tools=[], transcript=[])
    assert captured[0]["parallel_tool_calls"] is False


def test_max_completion_tokens_is_only_sent_when_explicitly_configured() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=completion(tool_name=None))

    adapter_with(handler).next_step(tools=[], transcript=[])
    adapter_with(handler, max_output_tokens_per_request=2048).next_step(tools=[], transcript=[])

    assert "max_completion_tokens" not in captured[0]
    assert captured[1]["max_completion_tokens"] == 2048


def test_a_successful_call_carries_replayable_private_and_public_metadata() -> None:
    captured: list[dict[str, Any]] = []
    response_body = completion()

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response_body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    expected_hash = hashlib.sha256(
        json.dumps(captured[0], sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()

    assert step.trace["request_hash"] == expected_hash
    assert isinstance(step.trace["latency_ms"], int) and step.trace["latency_ms"] >= 0
    assert step.trace["finish_reason"] == "tool_calls"
    assert step.trace["request_body"] == captured[0]
    assert step.trace["response_body"] == response_body


def test_the_authorization_header_carries_the_key() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=completion())

    adapter_with(handler).next_step(tools=[], transcript=[])
    assert seen == ["Bearer test-key"]


def test_a_tool_call_round_trips_into_an_invocation() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=completion(tool_name="read_file", arguments={"path": "src/auth.py"})
        )

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.invocation is not None
    assert step.invocation.tool_name == "read_file"
    assert step.invocation.arguments == {"path": "src/auth.py"}


def test_second_chat_request_replays_the_assistant_call_and_linked_tool_result(
    tree: Path,
) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return httpx.Response(
                200,
                json=completion(
                    tool_name="read_file",
                    arguments={"path": "src/auth.py"},
                    call_id="call_read_1",
                ),
            )
        return httpx.Response(200, json=completion(tool_name=None))

    run_agent(adapter_with(handler), context=ToolContext(root=tree))

    assert captured[1]["messages"][1]["role"] == "assistant"
    assert captured[1]["messages"][1]["tool_calls"][0]["id"] == "call_read_1"
    assert captured[1]["messages"][2] == {
        "role": "tool",
        "tool_call_id": "call_read_1",
        "content": (
            '{"content":"     1\\tdef verify(a, b):\\n     2\\t    return a == b","is_error":false}'
        ),
    }


def test_chat_tool_error_is_a_tool_message_with_the_original_call_id(tree: Path) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return httpx.Response(
                200,
                json=completion(
                    tool_name="read_file",
                    arguments={"path": "missing.py"},
                    call_id="call_missing",
                ),
            )
        return httpx.Response(200, json=completion(tool_name=None))

    run_agent(adapter_with(handler), context=ToolContext(root=tree))

    assert captured[1]["messages"][-1] == {
        "role": "tool",
        "tool_call_id": "call_missing",
        "content": '{"content":"no file at \'missing.py\'","is_error":true}',
    }


def test_third_chat_request_retains_both_prior_tool_call_exchanges(tree: Path) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return httpx.Response(
                200,
                json=completion(
                    tool_name="read_file",
                    arguments={"path": "src/auth.py"},
                    call_id="call_read",
                ),
            )
        if len(captured) == 2:
            return httpx.Response(
                200,
                json=completion(
                    tool_name="list_directory",
                    arguments={"path": "src"},
                    call_id="call_list",
                ),
            )
        return httpx.Response(200, json=completion(tool_name=None))

    run_agent(adapter_with(handler), context=ToolContext(root=tree))

    linked = [
        (message["role"], message.get("tool_call_id") or message["tool_calls"][0]["id"])
        for message in captured[2]["messages"]
        if message["role"] in {"assistant", "tool"}
    ]
    assert linked == [
        ("assistant", "call_read"),
        ("tool", "call_read"),
        ("assistant", "call_list"),
        ("tool", "call_list"),
    ]


def test_a_response_with_no_tool_call_stops_the_run() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(tool_name=None))

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.COMPLETED


def test_a_final_assistant_message_completes_a_clean_run(tree: Path) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(tool_name=None))

    result = run_agent(adapter_with(handler), context=ToolContext(root=tree))

    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.findings == []


@pytest.mark.parametrize(
    "body",
    [
        {"choices": [], "usage": {}},
        {"choices": [{"finish_reason": "stop"}], "usage": {}},
        {"choices": [{"message": None, "finish_reason": "stop"}], "usage": {}},
    ],
    ids=["missing-choice", "missing-message", "non-object-message"],
)
def test_a_chat_response_without_an_assistant_message_is_no_output(
    body: dict[str, Any],
) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])

    assert step.stop is TerminationReason.NO_OUTPUT


def test_malformed_tool_arguments_do_not_end_the_run() -> None:
    """The model's mistake, so the tool layer reports it and the run continues."""

    def handler(_: httpx.Request) -> httpx.Response:
        body = completion()
        body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"] = "{not json"
        return httpx.Response(200, json=body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.invocation is not None
    assert step.invocation.arguments == {}


def test_multiple_chat_tool_calls_are_rejected_instead_of_partly_answered() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = completion()
        body["choices"][0]["message"]["tool_calls"].append(
            {
                "id": "call_2",
                "type": "function",
                "function": {"name": "list_directory", "arguments": "{}"},
            }
        )
        return httpx.Response(200, json=body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR
    assert step.error["exception"] == "UnexpectedParallelToolCalls"


def test_a_chat_tool_call_without_id_is_rejected_before_tool_execution() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = completion()
        del body["choices"][0]["message"]["tool_calls"][0]["id"]
        return httpx.Response(200, json=body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR
    assert step.error["exception"] == "MissingToolCallId"


# -------------------------------------------------------------- usage rules


def test_a_missing_field_is_null_and_named_never_zero() -> None:
    """Silence is not a measurement. Recording 0 would make it one."""
    usage = normalise_usage({"prompt_tokens": 100, "completion_tokens": 20})

    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.reasoning_tokens is None
    assert usage.cached_input_tokens is None
    assert set(usage.unknown_fields) == {"reasoning_tokens", "cached_input_tokens"}


def test_a_reported_zero_stays_zero_and_is_not_unknown() -> None:
    """The converse: a provider that says zero has told us something."""
    usage = normalise_usage(
        {
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    assert usage.cached_input_tokens == 0
    assert usage.reasoning_tokens == 0
    assert usage.unknown_fields == ()


def test_both_token_spellings_are_accepted() -> None:
    """OpenAI-compatible servers use both; a synonym should not lose a run."""
    assert normalise_usage({"input_tokens": 7, "output_tokens": 3}).input_tokens == 7
    assert normalise_usage({"prompt_tokens": 7, "completion_tokens": 3}).input_tokens == 7


def test_the_responses_api_detail_container_names_are_also_accepted() -> None:
    """`/v1/responses` nests cached/reasoning counts under `input_tokens_details`
    and `output_tokens_details` rather than the Chat Completions names. One
    normaliser has to read both, because both adapters price through it."""
    usage = normalise_usage(
        {
            "input_tokens": 1000,
            "output_tokens": 200,
            "input_tokens_details": {"cached_tokens": 300},
            "output_tokens_details": {"reasoning_tokens": 50},
        }
    )
    assert usage.input_tokens == 1000
    assert usage.output_tokens == 200
    assert usage.cached_input_tokens == 300
    assert usage.reasoning_tokens == 50
    assert usage.unknown_fields == ()


def test_a_reported_zero_stays_zero_under_the_responses_api_names_too() -> None:
    usage = normalise_usage(
        {
            "input_tokens": 10,
            "output_tokens": 5,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 0},
        }
    )
    assert usage.cached_input_tokens == 0
    assert usage.reasoning_tokens == 0
    assert usage.unknown_fields == ()


def test_chat_completions_detail_names_are_preferred_when_both_are_present() -> None:
    """Not a real-world shape from either endpoint; documents which one wins
    rather than leaving the tie-break implicit."""
    usage = normalise_usage(
        {
            "input_tokens": 1,
            "output_tokens": 1,
            "prompt_tokens_details": {"cached_tokens": 7},
            "input_tokens_details": {"cached_tokens": 99},
        }
    )
    assert usage.cached_input_tokens == 7


def test_an_absent_usage_block_leaves_everything_unknown() -> None:
    usage = normalise_usage(None)
    assert usage.as_dict()["input_tokens"] is None
    assert set(usage.unknown_fields) == set(usage.as_dict()) - {"unknown_fields"}


# --------------------------------------------------------------------- cost


def test_a_complete_usage_prices_as_complete() -> None:
    usage = normalise_usage(
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1_000_000,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    estimate = estimate_cost(usage, PRICING)
    assert estimate.completeness == "complete"
    assert estimate.estimated_cost_usd == pytest.approx(3.0)


def test_any_unknown_component_makes_the_estimate_partial() -> None:
    """A partial estimate compared against a complete one is not a comparison."""
    estimate = estimate_cost(normalise_usage({"prompt_tokens": 10}), PRICING)
    assert estimate.completeness == "partial"
    assert "output_tokens" in estimate.unknown_fields


def test_a_partial_estimate_says_what_it_left_out() -> None:
    estimate = estimate_cost(normalise_usage({"prompt_tokens": 10}), PRICING)
    assert any("Priced without" in note for note in estimate.estimator_limitations)


def test_the_estimate_carries_its_pricing_provenance() -> None:
    """A price without a date is not reproducible."""
    estimate = estimate_cost(normalise_usage({"prompt_tokens": 1}), PRICING)
    assert estimate.pricing_table_version == "test-1"
    assert estimate.pricing_effective_date == "2026-01-01"
    assert estimate.pricing_source.startswith("https://")


def test_the_placeholder_table_says_zero_means_unpriced() -> None:
    """Shipping a plausible price nobody checked would be worse than none."""
    estimate = estimate_cost(normalise_usage({"prompt_tokens": 1_000_000}), PLACEHOLDER_PRICING)
    assert estimate.estimated_cost_usd == 0.0
    assert any("not priced" in note for note in estimate.estimator_limitations)


def test_cached_input_is_not_charged_twice() -> None:
    pricing = PricingTable(
        version="t",
        effective_date="2026-01-01",
        source="https://example.invalid",
        input_per_mtok_usd=10.0,
        output_per_mtok_usd=0.0,
        cached_input_per_mtok_usd=1.0,
    )
    usage = normalise_usage(
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": 1_000_000},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    # All input was cached, so it prices at the cache rate and not the full one.
    assert estimate_cost(usage, pricing).estimated_cost_usd == pytest.approx(1.0)


def test_the_dollar_field_is_named_estimated_cost_usd() -> None:
    """§11.3 fixes the name; `cost_usd` would read as a measurement."""
    keys = estimate_cost(normalise_usage({"prompt_tokens": 1}), PRICING).as_dict()
    assert "estimated_cost_usd" in keys
    assert "cost_usd" not in keys


# --------------------------------------------------------- provider failure


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_an_http_error_is_a_provider_error_not_an_adapter_error(status: int) -> None:
    """§13.1 keeps them apart so a provider outage is excluded, not scored."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "nope"})

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR


def test_a_transport_failure_is_a_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR


def test_a_non_json_response_is_a_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>gateway</html>")

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR


# ------------------------------------------------------ budgets, end to end


def always_reads(usage: dict[str, Any]) -> Any:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=completion(tool_name="read_file", arguments={"path": "src/auth.py"}, usage=usage),
        )

    return handler


def test_the_token_budget_terminates(tree: Path) -> None:
    adapter = adapter_with(always_reads({"prompt_tokens": 400, "completion_tokens": 100}))
    result = run_agent(adapter, context=ToolContext(root=tree), budget=Budget(max_tokens=1000))
    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED_TOKENS


def test_the_cost_budget_terminates(tree: Path) -> None:
    adapter = adapter_with(always_reads({"prompt_tokens": 1_000_000, "completion_tokens": 0}))
    result = run_agent(
        adapter, context=ToolContext(root=tree), budget=Budget(max_estimated_cost_usd=2.5)
    )
    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED_COST


def test_the_tool_call_budget_terminates(tree: Path) -> None:
    adapter = adapter_with(always_reads({"prompt_tokens": 1, "completion_tokens": 1}))
    result = run_agent(adapter, context=ToolContext(root=tree), budget=Budget(max_tool_calls=3))
    assert result.termination_reason is TerminationReason.STEP_EXHAUSTED
    assert result.tool_calls == 3


def test_the_wallclock_budget_terminates(tree: Path) -> None:
    ticks = iter([0.0, 0.0, 9.0, 9.0, 9.0, 9.0])
    adapter = adapter_with(always_reads({"prompt_tokens": 1, "completion_tokens": 1}))
    result = run_agent(
        adapter,
        context=ToolContext(root=tree),
        budget=Budget(max_wallclock_seconds=1.0),
        clock=lambda: next(ticks, 99.0),
    )
    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED_WALLCLOCK


def test_all_four_budget_dimensions_are_covered_here() -> None:
    """Named as a set so a new dimension fails this rather than going untested."""
    assert set(Budget().as_dict()) == {
        "max_tokens",
        "max_tool_calls",
        "max_wallclock_seconds",
        "max_estimated_cost_usd",
    }


def test_a_full_run_reaches_findings(tree: Path) -> None:
    """One end-to-end pass: read a file, then submit."""
    calls = {"n": 0}
    finding = {
        "id": "f1",
        "file": "src/auth.py",
        "line_start": 2,
        "line_end": 2,
        "category": "security",
        "severity": "high",
        "claim": "Token comparison is not constant time.",
        "root_cause": "Uses == on strings.",
        "evidence": "src/auth.py line 2.",
        "suggested_verification": "Use compare_digest.",
    }

    def handler(_: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(
                200, json=completion(tool_name="read_file", arguments={"path": "src/auth.py"})
            )
        if calls["n"] == 2:
            return httpx.Response(
                200,
                json=completion(tool_name="write_findings", arguments={"findings": [finding]}),
            )
        return httpx.Response(200, json=completion(tool_name=None))

    result = run_agent(adapter_with(handler), context=ToolContext(root=tree))
    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.findings == [finding]
