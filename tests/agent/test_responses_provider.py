"""The `/v1/responses` adapter, against a mock transport only.

Never exercised against a live endpoint — see the module docstring in
`responses_provider.py`. Every test here proves the *shape mapping* is what the
module claims it is: a flat tool schema, `input` items instead of `messages`,
`output` items instead of `choices`, and the one thing that has no equivalent
in the chat-completions adapter at all — an HTTP 200 whose `status` says the
turn is not actually usable.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import httpx
import pytest

from coding_agent_eval.agent.loop import run_agent
from coding_agent_eval.agent.protocol import Budget, TerminationReason
from coding_agent_eval.agent.provider import PricingTable, ProviderConfigurationError
from coding_agent_eval.agent.responses_provider import (
    OpenAIResponsesAdapter,
    build_input,
    responses_tool_schemas,
)
from coding_agent_eval.agent.tools import ToolContext

PRICING = PricingTable(
    version="test-1",
    effective_date="2026-01-01",
    source="https://example.invalid/pricing",
    input_per_mtok_usd=1.0,
    output_per_mtok_usd=2.0,
)


def response(
    *,
    tool_name: str | None = "read_file",
    arguments: dict[str, Any] | None = None,
    usage: dict[str, Any] | None = None,
    status: str = "completed",
    extra_output: list[dict[str, Any]] | None = None,
    call_id: str = "call_1",
) -> dict[str, Any]:
    output: list[dict[str, Any]] = list(extra_output or [])
    if tool_name is not None:
        output.append(
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": call_id,
                "name": tool_name,
                "arguments": json.dumps(arguments if arguments is not None else {"path": "."}),
            }
        )
    return {"status": status, "output": output, "usage": usage or {}}


def adapter_with(handler: Any, **kwargs: Any) -> OpenAIResponsesAdapter:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OpenAIResponsesAdapter(
        model="test-model", api_key="test-key", client=client, pricing=PRICING, **kwargs
    )


@pytest.fixture
def tree(tmp_path: Any) -> Any:
    root = tmp_path / "tree"
    (root / "src").mkdir(parents=True)
    (root / "src" / "auth.py").write_bytes(b"def verify(a, b):\n    return a == b\n")
    return root


# ------------------------------------------------------------------ api key


def test_a_missing_api_key_raises_without_attempting_a_request() -> None:
    attempted: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempted.append(request)
        return httpx.Response(200, json=response())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(ProviderConfigurationError):
        OpenAIResponsesAdapter(model="test-model", api_key=None, client=client)
    assert attempted == []


# --------------------------------------------------------------- shape: input


def test_the_system_item_comes_first() -> None:
    items = build_input([])
    assert items[0]["role"] == "system"


# ---------------------------------------------------------- shape: tool schema


def test_the_tool_schema_is_flattened_not_nested() -> None:
    schemas = responses_tool_schemas(
        [{"name": "read_file", "description": "d", "parameters": {"type": "object"}}]
    )
    assert schemas == [
        {
            "type": "function",
            "name": "read_file",
            "description": "d",
            "parameters": {"type": "object"},
        }
    ]
    assert "function" not in schemas[0], "must not nest under 'function' like Chat Completions"


def test_the_request_carries_input_not_messages() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    adapter_with(handler).next_step(
        tools=[{"name": "read_file", "description": "d", "parameters": {}}], transcript=[]
    )

    body = captured[0]
    assert body["model"] == "test-model"
    assert "input" in body and "messages" not in body
    assert body["input"][0]["role"] == "system"
    assert body["tools"][0]["type"] == "function"
    assert body["tools"][0]["name"] == "read_file"
    assert "function" not in body["tools"][0]


def test_a_successful_call_carries_replayable_private_and_public_metadata() -> None:
    captured: list[dict[str, Any]] = []
    response_body = response()

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
    assert step.trace["finish_reason"] == "completed"
    assert step.trace["request_body"] == captured[0]
    assert step.trace["response_body"] == response_body


def test_parallel_tool_calls_is_always_sent_false() -> None:
    """The harness executes one action per step and rejects extra calls."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    adapter_with(handler).next_step(tools=[], transcript=[])
    assert captured[0]["parallel_tool_calls"] is False


def test_every_request_disables_server_side_response_storage() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response(tool_name=None))

    adapter_with(handler).next_step(tools=[], transcript=[])
    assert captured[0]["store"] is False


def test_max_output_tokens_is_only_sent_when_explicitly_configured() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response(tool_name=None))

    adapter_with(handler).next_step(tools=[], transcript=[])
    adapter_with(handler, max_output_tokens_per_request=2048).next_step(tools=[], transcript=[])

    assert "max_output_tokens" not in captured[0]
    assert captured[1]["max_output_tokens"] == 2048


def test_the_authorization_header_carries_the_key() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("authorization", ""))
        return httpx.Response(200, json=response())

    adapter_with(handler).next_step(tools=[], transcript=[])
    assert seen == ["Bearer test-key"]


def test_the_endpoint_path_is_responses_not_chat_completions() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=response())

    adapter_with(handler).next_step(tools=[], transcript=[])
    assert seen[0].endswith("/responses")


# ----------------------------------------------------- shape: reasoning_effort


def test_reasoning_effort_is_absent_unless_set() -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    adapter_with(handler).next_step(tools=[], transcript=[])
    assert "reasoning" not in captured[0]


def test_reasoning_effort_is_sent_nested_under_reasoning() -> None:
    """The Responses API's shape differs from Chat Completions' flat field."""
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        return httpx.Response(200, json=response())

    adapter_with(handler, reasoning_effort="high").next_step(tools=[], transcript=[])
    assert captured[0]["reasoning"] == {"effort": "high"}


# --------------------------------------------------------------- shape: output


def test_a_function_call_round_trips_into_an_invocation() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=response(tool_name="read_file", arguments={"path": "src/auth.py"})
        )

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.invocation is not None
    assert step.invocation.tool_name == "read_file"
    assert step.invocation.arguments == {"path": "src/auth.py"}


def test_a_response_with_no_function_call_stops_the_run() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response(
                tool_name=None,
                extra_output=[{"type": "message", "role": "assistant", "content": []}],
            ),
        )

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.COMPLETED


def test_a_reasoning_item_ahead_of_the_function_call_is_skipped() -> None:
    """A reasoning item is context, not the action selected for the harness."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response(
                tool_name="read_file",
                extra_output=[{"type": "reasoning", "id": "r_1", "summary": []}],
            ),
        )

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.invocation is not None
    assert step.invocation.tool_name == "read_file"


def test_second_request_replays_every_assistant_output_before_the_linked_result(
    tree: Any,
) -> None:
    captured: list[dict[str, Any]] = []
    reasoning = {
        "type": "reasoning",
        "id": "r_1",
        "summary": [],
        "encrypted_content": "opaque-test-ciphertext",
    }
    function_call = {
        "type": "function_call",
        "id": "fc_1",
        "call_id": "call_1",
        "name": "read_file",
        "arguments": '{"path":"src/auth.py"}',
    }

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return httpx.Response(
                200,
                json={"status": "completed", "output": [reasoning, function_call], "usage": {}},
            )
        return httpx.Response(200, json=response(tool_name=None))

    run_agent(adapter_with(handler), context=ToolContext(root=tree))

    assert captured[1]["input"][1:] == [
        reasoning,
        function_call,
        {
            "type": "function_call_output",
            "call_id": "call_1",
            "output": (
                '{"content":"     1\\tdef verify(a, b):\\n'
                '     2\\t    return a == b","is_error":false}'
            ),
        },
    ]


def test_tool_error_is_linked_to_the_call_that_caused_it(tree: Any) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return httpx.Response(
                200,
                json=response(
                    tool_name="read_file",
                    arguments={"path": "missing.py"},
                ),
            )
        return httpx.Response(200, json=response(tool_name=None))

    run_agent(adapter_with(handler), context=ToolContext(root=tree))

    assert captured[1]["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": '{"content":"no file at \'missing.py\'","is_error":true}',
    }


def test_third_request_retains_both_prior_function_call_exchanges(tree: Any) -> None:
    captured: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content))
        if len(captured) == 1:
            return httpx.Response(
                200,
                json=response(
                    tool_name="read_file",
                    arguments={"path": "src/auth.py"},
                    call_id="call_read",
                ),
            )
        if len(captured) == 2:
            return httpx.Response(
                200,
                json=response(
                    tool_name="list_directory",
                    arguments={"path": "src"},
                    call_id="call_list",
                ),
            )
        return httpx.Response(200, json=response(tool_name=None))

    run_agent(adapter_with(handler), context=ToolContext(root=tree))

    linked = [
        (item["type"], item["call_id"])
        for item in captured[2]["input"]
        if item.get("type") in {"function_call", "function_call_output"}
    ]
    assert linked == [
        ("function_call", "call_read"),
        ("function_call_output", "call_read"),
        ("function_call", "call_list"),
        ("function_call_output", "call_list"),
    ]


def test_multiple_function_calls_are_rejected_instead_of_partly_answered() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = response()
        body["output"].append(
            {
                "type": "function_call",
                "call_id": "call_2",
                "name": "list_directory",
                "arguments": "{}",
            }
        )
        return httpx.Response(200, json=body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR
    assert step.error["exception"] == "UnexpectedParallelFunctionCalls"


def test_a_function_call_without_call_id_is_rejected_before_tool_execution() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = response()
        del body["output"][0]["call_id"]
        return httpx.Response(200, json=body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR
    assert step.error["exception"] == "MissingFunctionCallId"


def test_malformed_arguments_do_not_end_the_run() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        body = response()
        body["output"][0]["arguments"] = "{not json"
        return httpx.Response(200, json=body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.invocation is not None
    assert step.invocation.arguments == {}


# --------------------------------------------------------- shape: status field


def test_a_completed_status_proceeds_normally() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response(status="completed"))

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.invocation is not None


@pytest.mark.parametrize("status", ["incomplete", "failed", "cancelled", "queued"])
def test_a_non_completed_status_is_a_provider_error_not_a_stop_or_a_call(status: str) -> None:
    """A 200 is not proof the turn is usable. See the module docstring: this is
    a conservative, unverified choice, and this test is what would need to
    change if a live run shows a status this adapter should have acted on."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response(status=status))

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR
    assert step.error["status"] == status


def test_a_non_completed_status_still_reports_its_usage() -> None:
    """The call still spent tokens. Dropping them here would understate real
    spend on exactly the calls most worth tracking accurately."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response(
                status="incomplete",
                usage={
                    "input_tokens": 500,
                    "output_tokens": 40,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            ),
        )

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.usage["input_tokens"] == 500
    assert step.usage["output_tokens"] == 40


def test_a_missing_status_field_is_not_treated_as_an_error() -> None:
    """Not every deployment necessarily echoes it back; absence must not be
    mistaken for a known-bad value."""

    def handler(_: httpx.Request) -> httpx.Response:
        body = response()
        del body["status"]
        return httpx.Response(200, json=body)

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.invocation is not None


# ------------------------------------------------------------- usage, shared


def test_usage_is_read_from_the_responses_shaped_detail_containers() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response(
                tool_name=None,
                usage={
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "input_tokens_details": {"cached_tokens": 300},
                    "output_tokens_details": {"reasoning_tokens": 50},
                },
            ),
        )

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.usage["input_tokens"] == 1000
    assert step.usage["cached_input_tokens"] == 300
    assert step.usage["reasoning_tokens"] == 50
    assert step.usage["completeness"] == "complete"


# --------------------------------------------------------- provider failure


@pytest.mark.parametrize("status_code", [401, 429, 500, 503])
def test_an_http_error_is_a_provider_error(status_code: int) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={"error": {"message": "nope", "type": "t"}})

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR
    assert step.error["status"] == status_code


def test_a_transport_failure_is_a_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    step = adapter_with(handler).next_step(tools=[], transcript=[])
    assert step.stop is TerminationReason.PROVIDER_ERROR


# ------------------------------------------------------ budgets, end to end


def always_reads(usage: dict[str, Any]) -> Any:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=response(tool_name="read_file", arguments={"path": "src/auth.py"}, usage=usage),
        )

    return handler


def test_the_token_budget_terminates(tree: Any) -> None:
    adapter = adapter_with(always_reads({"input_tokens": 400, "output_tokens": 100}))
    result = run_agent(adapter, context=ToolContext(root=tree), budget=Budget(max_tokens=1000))
    assert result.termination_reason is TerminationReason.BUDGET_EXHAUSTED_TOKENS


def test_a_full_run_reaches_findings(tree: Any) -> None:
    """One end-to-end pass through the real loop: read a file, then submit."""
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
                200, json=response(tool_name="read_file", arguments={"path": "src/auth.py"})
            )
        if calls["n"] == 2:
            return httpx.Response(
                200, json=response(tool_name="write_findings", arguments={"findings": [finding]})
            )
        return httpx.Response(
            200,
            json=response(
                tool_name=None,
                extra_output=[{"type": "message", "role": "assistant", "content": []}],
            ),
        )

    result = run_agent(adapter_with(handler), context=ToolContext(root=tree))
    assert result.termination_reason is TerminationReason.COMPLETED
    assert result.findings == [finding]


# ------------------------------------------------------------ adapter identity


def test_the_adapter_names_itself_distinctly_from_chat_completions() -> None:
    """§13's promise — two runs of different adapters are never silently
    comparable — depends on this name being different from the other one."""
    adapter = adapter_with(lambda r: httpx.Response(200, json=response()))
    assert adapter.name == "openai-responses"
