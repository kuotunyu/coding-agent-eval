"""An adapter for OpenAI's `/v1/responses` endpoint (design spec §11, extends F3).

The adapter has exhaustive mock coverage and three retained live observations
from 2026-08-06. Those historical traces predate the current strict-replay event
contract. It exists because `/v1/chat/completions` refused function tools on the
measured model at any `reasoning_effort` other than `"none"`, so observing that
model with reasoning enabled required this different request and response shape.

Three shape differences from `/v1/chat/completions`, each handled here rather
than in the shared module:

**`input`, not `messages`.** Items are `{"role": ..., "content": ...}` for
text, which the Responses API accepts as shorthand for a full message item —
matching the harness's transcript exactly as `provider.build_messages` does
for the other adapter. Tool output is still sent as a `user`-role item, not a
`function_call_output` referencing a call id: this harness's transcript
(`Observation`) does not carry the assistant turn's `call_id` to reference,
the same constraint the other adapter has, handled the same way.

**A flat tool schema.** `{"type": "function", "name": ..., "description": ...,
"parameters": ...}` rather than nesting the schema under `"function"`.

**`output`, not `choices[0].message`.** A list of typed items —
`function_call`, `message`, `reasoning`, and others not handled here — and the
harness only ever asks for one action per step, so `parallel_tool_calls` is
sent `false` and only the first `function_call` item is read.

**A response can be HTTP 200 and still not be usable.** A `status` other than
`"completed"` — truncated by a token limit, refused, incomplete — is treated as
`PROVIDER_ERROR` rather than silently read as a stop or a tool call, because a
truncated `function_call` item's arguments may not even be valid JSON, and a
truncated turn with no visible tool call is not the same fact as the model
choosing to stop. This is a conservative, unverified choice — the exact set of
non-`completed` status values has not been observed — and is written up as one.

Everything else — pricing, cost estimation, provider-failure diagnosis, usage
normalisation — is shared with `provider.py`. `normalise_usage` already accepts
this endpoint's detail-container names (`input_tokens_details` /
`output_tokens_details`) alongside the chat-completions ones, so both adapters
enforce "unknown is never zero" identically.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from coding_agent_eval.agent.protocol import Observation, Step, TerminationReason, ToolInvocation
from coding_agent_eval.agent.provider import (
    DEFAULT_SYSTEM_PROMPT,
    PLACEHOLDER_PRICING,
    PricingTable,
    ProviderConfigurationError,
    describe_provider_failure,
    estimate_cost,
    normalise_usage,
    provider_call_trace,
    response_body_for_trace,
)

ADAPTER_VERSION = "0.1.0"

#: The only status this adapter proceeds on. Anything else — a string that is
#: present and different, including one never observed — is a provider error.
_USABLE_STATUS = "completed"


def build_input(
    transcript: Sequence[Observation], *, system_prompt: str = DEFAULT_SYSTEM_PROMPT
) -> list[dict[str, Any]]:
    """Turn the harness transcript into Responses API `input` items.

    Mirrors `provider.build_messages` field for field. See the module
    docstring for why tool output is a plain `user` item rather than a
    `function_call_output`.
    """
    items: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for observation in transcript:
        label = "error" if observation.is_error else "result"
        items.append(
            {
                "role": "user",
                "content": f"[{observation.tool_name} {label}]\n{observation.content}",
            }
        )
    return items


def responses_tool_schemas(tools: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flatten the model-facing tool schema for the Responses API's shape.

    `tools` arrives as `{"name", "description", "parameters"}` — the shape
    `model_schemas()` returns. Chat Completions nests that under `"function"`;
    the Responses API wants it at the top level next to `"type"`.
    """
    return [{"type": "function", **schema} for schema in tools]


def first_function_call(output: Any) -> dict[str, Any] | None:
    """The first `function_call` item in an `output` array, or `None`.

    A response may carry several item types — reasoning, message, and (absent
    `parallel_tool_calls: false`) more than one function_call. This harness
    asks for one action per step, so only the first is read; a second one
    present anyway would be the provider disregarding the request rather than
    a case this adapter has to act on.
    """
    if not isinstance(output, list):
        return None
    for item in output:
        if isinstance(item, dict) and item.get("type") == "function_call":
            return item
    return None


@dataclass
class OpenAIResponsesAdapter:
    """Drives `/v1/responses`. Read the module docstring before trusting it.

    `client` is injected so the whole adapter can be exercised against
    `httpx.MockTransport` — the only way it has been exercised.
    """

    model: str
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    #: Unlike `OpenAICompatibleAdapter.reasoning_effort`, this endpoint is the
    #: one that accepts reasoning at any effort alongside function tools — the
    #: refusal is specific to `/v1/chat/completions`. Still never defaulted:
    #: unset means the request does not mention it, and the model applies its
    #: own default, which is a decision this adapter does not make for it.
    reasoning_effort: str | None = None
    client: httpx.Client | None = None
    pricing: PricingTable = PLACEHOLDER_PRICING
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    timeout_seconds: float = 120.0
    name: str = "openai-responses"
    version: str = ADAPTER_VERSION

    def __post_init__(self) -> None:
        # Checked at construction, so a run cannot get as far as opening a
        # connection before discovering it has no credentials.
        if not self.api_key:
            raise ProviderConfigurationError(
                "no API key: set CAE_PROVIDER_API_KEY, or use a scripted baseline. "
                "No request has been attempted."
            )

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        client = self.client or httpx.Client(timeout=self.timeout_seconds)
        return client.post(
            f"{self.base_url.rstrip('/')}/responses",
            headers={
                "authorization": f"Bearer {self.api_key}",
                "content-type": "application/json",
            },
            json=payload,
        )

    def next_step(
        self,
        *,
        tools: Sequence[dict[str, Any]],
        transcript: Sequence[Observation],
    ) -> Step:
        payload: dict[str, Any] = {
            "model": self.model,
            "input": build_input(transcript, system_prompt=self.system_prompt),
            "tools": responses_tool_schemas(tools),
            "tool_choice": "auto",
            # One action per step is what this harness asks for; without this a
            # response may carry several function_call items and only the
            # first would ever be read (see `first_function_call`).
            "parallel_tool_calls": False,
        }
        if self.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.reasoning_effort}

        response: httpx.Response | None = None
        started_ns = time.monotonic_ns()
        try:
            response = self._post(payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Same bucket, same reasoning, as the chat-completions adapter: not
            # the model's result and not a harness bug, excluded from
            # aggregates rather than scored as a bad run — and the diagnostic
            # is kept, because a run that fails silently cannot be acted on.
            return Step(
                stop=TerminationReason.PROVIDER_ERROR,
                error=describe_provider_failure(exc, response),
                trace=provider_call_trace(
                    payload,
                    response_body=response_body_for_trace(response),
                    started_ns=started_ns,
                    finish_reason="provider_error",
                ),
            )

        # Usage is computed before the status check and attached either way.
        # A 200 that turns out unusable still spent tokens, and the loop only
        # ever records `step.usage` — never dropping it here would silently
        # understate real spend on exactly the calls worth knowing about.
        usage = normalise_usage(body.get("usage"))
        cost = estimate_cost(usage, self.pricing)
        reported = {
            **usage.as_dict(),
            "total_tokens": usage.total_tokens,
            **cost.as_dict(),
        }

        status = body.get("status")
        finish_reason = str(status or "completed")
        trace = provider_call_trace(
            payload,
            response_body=body,
            started_ns=started_ns,
            finish_reason=finish_reason,
        )
        if isinstance(status, str) and status != _USABLE_STATUS:
            return Step(
                stop=TerminationReason.PROVIDER_ERROR,
                usage=reported,
                error={"exception": "IncompleteResponse", "status": status},
                trace=trace,
            )

        call = first_function_call(body.get("output"))
        if call is None:
            return Step(stop=TerminationReason.COMPLETED, usage=reported, trace=trace)

        arguments = call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                # A malformed tool call is the model's mistake. Handing it to
                # the tool layer lets the ordinary error path report it and
                # the run continue, rather than ending on one bad message.
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        return Step(
            invocation=ToolInvocation(tool_name=call.get("name", ""), arguments=arguments),
            usage=reported,
            trace=trace,
        )
