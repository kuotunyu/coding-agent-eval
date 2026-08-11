"""An OpenAI-compatible provider adapter (plan F3, design spec §11).

The current conversation contract has mock coverage. Retained live observations
from 2026-08-06 used adapter 0.1 and remain historical evidence only. Nothing in
CI may require an API key — a gate that needs a secret is a gate that does not
run for anyone who lacks one, which is the same as not having it.

Two rules from §11.2 are load-bearing and are enforced here rather than left to
whoever reads the response:

**Unknown is never zero.** A provider that does not report reasoning or cached
tokens has not told us they were zero; it has told us nothing. Recording 0 would
turn silence into a measurement, and the cost derived from it would look exact.
Missing fields are `null` and are named in `unknown_fields`.

**A cost with any unknown component is `partial`.** `completeness` is what stops
a partial estimate being compared against a complete one as though the two were
the same kind of number.

Dollar amounts are always `estimated_cost_usd`, never `cost_usd` (§11.3): the
figure is an estimate from a pricing table, and costs across providers are not
apples-to-apples because cache, reasoning, and tiered pricing all differ.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from coding_agent_eval.agent.protocol import (
    AssistantTurn,
    Observation,
    Step,
    TerminationReason,
    ToolInvocation,
)
from coding_agent_eval.agent.tools import model_schemas

ADAPTER_VERSION = "0.3.0"

#: Usage fields that bear on price. Each is reported or explicitly unknown.
PRICED_USAGE_FIELDS: tuple[str, ...] = (
    "input_tokens",
    "cached_input_tokens",
    "output_tokens",
    "reasoning_tokens",
)

SYSTEM_PROMPT_VERSION = "0.2.0"


def render_system_prompt(max_tool_calls: int | None) -> str:
    """Render the shared, finite review and completion contract."""
    budget = (
        f"You have at most {max_tool_calls} tool calls; stop before using all of them. "
        if max_tool_calls is not None
        else "You have a finite tool budget; use tools selectively. "
    )
    return (
        "You are reviewing a code tree for defects. This is a selective review, not "
        "a proof that no defect exists. "
        + budget
        + "Report only defects supported by evidence and cite the file and line range. "
        "If supported defects exist, submit all of them in one write_findings call, "
        "then return a final response without a tool call. If none were found, return "
        "a final response without calling write_findings. Do not keep reading solely "
        "to prove the absence of defects."
    )


DEFAULT_SYSTEM_PROMPT = render_system_prompt(None)


def request_hash(payload: dict[str, Any]) -> str:
    """SHA-256 of canonical request JSON, excluding transport headers and credentials."""
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def response_body_for_trace(response: httpx.Response | None) -> Any:
    """The full private response body in a JSON-serialisable form."""
    if response is None:
        return None
    try:
        return response.json()
    except ValueError:
        return response.text


def provider_call_trace(
    payload: dict[str, Any],
    *,
    response_body: Any,
    started_ns: int,
    finish_reason: str,
) -> dict[str, Any]:
    """Fields recorded for one call; the sanitizer drops both full bodies."""
    return {
        "request_hash": request_hash(payload),
        "latency_ms": max(0, (time.monotonic_ns() - started_ns) // 1_000_000),
        "finish_reason": finish_reason,
        "request_body": payload,
        "response_body": response_body,
    }


class ProviderConfigurationError(RuntimeError):
    """The adapter cannot run as configured. Raised before any request is made."""


@dataclass(frozen=True)
class PricingTable:
    """Per-million-token prices, with the provenance that makes them checkable.

    The source URL and effective date travel with the numbers because a price
    without a date is not reproducible: the same run costed a year later would
    silently produce a different figure.
    """

    version: str
    effective_date: str
    source: str
    input_per_mtok_usd: float
    output_per_mtok_usd: float
    cached_input_per_mtok_usd: float | None = None
    limitations: tuple[str, ...] = ()


#: A placeholder table. The numbers are not a real provider's and are marked as
#: such, because shipping a plausible-looking price nobody checked would be
#: worse than shipping none: it would be believed.
PLACEHOLDER_PRICING = PricingTable(
    version="placeholder-0",
    effective_date="1970-01-01",
    source="none; no pricing has been entered for this build",
    input_per_mtok_usd=0.0,
    output_per_mtok_usd=0.0,
    limitations=(
        "Placeholder pricing: every rate is zero, so estimated_cost_usd is zero "
        "and means 'not priced', not 'free'.",
    ),
)


#: Real rates, read from the model's own page on the date below.
#:
#: Reasoning tokens are **not** priced separately here and must not be. The
#: provider bills them inside `completion_tokens`, so the output rate already
#: covers them; adding a third term would double-charge every reasoning token.
#: They are still recorded on the usage report, because a run that spent most of
#: its output budget on reasoning is a different run from one that did not.
GPT_5_6_LUNA_PRICING = PricingTable(
    version="openai-gpt-5.6-luna@2026-08-11",
    effective_date="2026-08-11",
    source="https://developers.openai.com/api/docs/models/gpt-5.6-luna",
    input_per_mtok_usd=1.00,
    output_per_mtok_usd=6.00,
    cached_input_per_mtok_usd=0.10,
    limitations=(
        "Rates were read from the model page on 2026-08-11 and are not re-checked "
        "at run time. A run costed after a price change reports a figure the "
        "provider no longer charges, which is why the date travels with the table.",
        "Reasoning tokens are billed inside the output-token count and are priced "
        "at the output rate, on both endpoints this repository speaks. They are "
        "reported separately but never priced twice.",
    ),
)


#: Model id to pricing. Absence is meaningful — see `pricing_for`.
PRICING_BY_MODEL: dict[str, PricingTable] = {
    "gpt-5.6-luna": GPT_5_6_LUNA_PRICING,
}


class UnpricedModelError(RuntimeError):
    """A dollar budget was requested for a model whose rates are not known."""


def pricing_for(model: str, *, require_priced: bool = False) -> PricingTable:
    """The pricing table for `model`, or the placeholder.

    `require_priced` is what a dollar budget passes. A cost cap enforced against
    the placeholder table is not a cap at all: every rate is zero, so the
    estimate is always 0.00 and the limit can never be reached. Refusing is the
    only honest option — a budget that silently cannot bind is worse than no
    budget, because the operator believes they have one.
    """
    table = PRICING_BY_MODEL.get(model)
    if table is not None:
        return table
    if require_priced:
        known = ", ".join(sorted(PRICING_BY_MODEL)) or "none"
        raise UnpricedModelError(
            f"no pricing table for {model!r}, so a dollar budget could never be "
            f"reached and would not bound anything. Priced models: {known}. "
            "Add rates with their source and date, or drop the dollar budget and "
            "bound the run by tokens, which is measured rather than estimated."
        )
    return PLACEHOLDER_PRICING


@dataclass(frozen=True)
class UsageReport:
    """Normalised token usage. `None` means the provider did not say."""

    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    unknown_fields: tuple[str, ...]
    raw_normalized: dict[str, Any] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        """Tokens known to have been spent. Unknown components contribute nothing.

        Deliberately not `None`-propagating: the token budget has to be able to
        stop a run, and a budget that gave up because one field was missing
        would let a run continue for ever on a provider that reports less.
        """
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "cached_input_tokens": self.cached_input_tokens,
            "output_tokens": self.output_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "unknown_fields": list(self.unknown_fields),
        }


def normalise_usage(raw: dict[str, Any] | None) -> UsageReport:
    """Map a provider usage payload onto the fields §11.1 requires.

    Accepts both the `prompt_tokens`/`completion_tokens` spelling and the
    `input_tokens`/`output_tokens` one, because OpenAI-compatible servers use
    both and a run should not be unpriceable because of a synonym. Also accepts
    both detail-container names: Chat Completions nests cached/reasoning counts
    under `prompt_tokens_details`/`completion_tokens_details`, and the Responses
    API (`responses_provider.py`) uses `input_tokens_details`/
    `output_tokens_details` for the same numbers. One normaliser for both, so
    "unknown is never zero" and "any unknown makes the estimate partial" are
    enforced identically regardless of which endpoint answered.
    """
    raw = raw or {}

    def first(*names: str) -> int | None:
        for name in names:
            value = raw.get(name)
            if isinstance(value, int):
                return value
        return None

    details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details") or {}
    completion_details = (
        raw.get("completion_tokens_details") or raw.get("output_tokens_details") or {}
    )

    input_tokens = first("input_tokens", "prompt_tokens")
    output_tokens = first("output_tokens", "completion_tokens")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    reasoning = (
        completion_details.get("reasoning_tokens") if isinstance(completion_details, dict) else None
    )
    cached = cached if isinstance(cached, int) else None
    reasoning = reasoning if isinstance(reasoning, int) else None

    values = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
    }
    unknown = tuple(name for name in PRICED_USAGE_FIELDS if values[name] is None)

    return UsageReport(
        input_tokens=input_tokens,
        cached_input_tokens=cached,
        output_tokens=output_tokens,
        reasoning_tokens=reasoning,
        unknown_fields=unknown,
        raw_normalized={key: value for key, value in raw.items() if isinstance(value, int | dict)},
    )


@dataclass(frozen=True)
class CostEstimate:
    estimated_cost_usd: float
    completeness: str
    unknown_fields: tuple[str, ...]
    pricing_table_version: str
    pricing_effective_date: str
    pricing_source: str
    estimator_limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "estimated_cost_usd": self.estimated_cost_usd,
            "completeness": self.completeness,
            "unknown_fields": list(self.unknown_fields),
            "pricing_table_version": self.pricing_table_version,
            "pricing_effective_date": self.pricing_effective_date,
            "pricing_source": self.pricing_source,
            "estimator_limitations": list(self.estimator_limitations),
        }


def estimate_cost(usage: UsageReport, pricing: PricingTable) -> CostEstimate:
    """Price a usage report. Any unknown component makes the result `partial`."""
    billed_input = usage.input_tokens or 0
    cached = usage.cached_input_tokens or 0
    if pricing.cached_input_per_mtok_usd is not None and cached:
        # Cached input is charged at its own rate, so it must not also be
        # charged at the full one.
        billed_input = max(billed_input - cached, 0)
        cached_cost = cached * pricing.cached_input_per_mtok_usd / 1_000_000
    else:
        cached_cost = 0.0

    total = (
        billed_input * pricing.input_per_mtok_usd / 1_000_000
        + (usage.output_tokens or 0) * pricing.output_per_mtok_usd / 1_000_000
        + cached_cost
    )

    limitations = list(pricing.limitations)
    if usage.unknown_fields:
        limitations.append(
            "Priced without "
            + ", ".join(usage.unknown_fields)
            + "; the true cost is at least this."
        )

    return CostEstimate(
        estimated_cost_usd=total,
        completeness="partial" if usage.unknown_fields else "complete",
        unknown_fields=usage.unknown_fields,
        pricing_table_version=pricing.version,
        pricing_effective_date=pricing.effective_date,
        pricing_source=pricing.source,
        estimator_limitations=tuple(limitations),
    )


#: Ceiling on the provider message kept for diagnosis.
#:
#: Long enough for any real explanation, short enough that a provider echoing
#: the request back cannot turn a diagnostic into a copy of the conversation.
MAX_ERROR_MESSAGE_CHARS = 400


def describe_provider_failure(exc: Exception, response: httpx.Response | None) -> dict[str, Any]:
    """Turn a failed call into something an operator can act on.

    Split by what it is, not by convenience. `status`, `type` and `code` are the
    provider's own structural classification of the failure and go in the public
    trace. `message` is free text the provider chose to write, and a provider may
    quote the request back — which here would mean source code the agent had
    read — so it is classified private and dropped from the projection.

    Nothing from the *request* is ever included, which is what keeps the key out.
    """
    detail: dict[str, Any] = {"exception": type(exc).__name__}

    if response is not None:
        detail["status"] = response.status_code
        try:
            body = response.json()
        except ValueError:
            body = None
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            for name in ("type", "code", "param"):
                value = error.get(name)
                if isinstance(value, str) and value:
                    detail[name] = value
            message = error.get("message")
            if isinstance(message, str) and message:
                detail["message"] = message[:MAX_ERROR_MESSAGE_CHARS]
        elif isinstance(body, dict):
            # A body that is JSON but not shaped like an error still narrows it
            # down; the keys alone say which contract the provider is speaking.
            detail["body_keys"] = sorted(str(key) for key in body)[:10]
    else:
        # No response at all: DNS, TLS, connect, or timeout. The exception type
        # is the whole diagnosis, and `str(exc)` can carry the URL, so it is
        # kept short and treated as the private half.
        detail["message"] = str(exc)[:MAX_ERROR_MESSAGE_CHARS]

    return detail


def build_messages(
    transcript: Sequence[Observation], *, system_prompt: str = DEFAULT_SYSTEM_PROMPT
) -> list[dict[str, Any]]:
    """Replay assistant tool calls and their linked results as chat messages."""
    messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for observation in transcript:
        turn = observation.assistant_turn
        if turn is None or turn.api != "chat_completions":
            raise ValueError("chat observation lacks its assistant tool-call turn")
        messages.extend(turn.output)
        messages.append(
            {
                "role": "tool",
                "tool_call_id": turn.tool_call_id,
                "content": tool_output_for_model(observation),
            }
        )
    return messages


def tool_output_for_model(observation: Observation) -> str:
    """Stable structured tool output shared by both OpenAI wire formats."""
    return json.dumps(
        {"content": observation.content, "is_error": observation.is_error},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


@dataclass
class OpenAICompatibleAdapter:
    """Drives a chat-completions endpoint that speaks OpenAI's tool-call shape.

    `client` is injected so the whole adapter can be exercised against
    `httpx.MockTransport`. That is the only way it has been exercised.
    """

    model: str
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    #: Sent only when set, and **never defaulted** on the model's behalf.
    #:
    #: Some reasoning models refuse function tools on `/v1/chat/completions`
    #: unless this is `"none"`, and say so: *"Function tools with
    #: reasoning_effort are not supported for <model> in /v1/chat/completions.
    #: To use function tools, use /v1/responses or set reasoning_effort to
    #: 'none'."* Setting it to `"none"` makes such a model run here — and
    #: measures it **with its reasoning disabled**, which is a different subject
    #: from the same model at its default effort.
    #:
    #: That is a measurement decision, not a workaround, so it is never chosen
    #: automatically. The operator sets it, and it is recorded in the run header
    #: so no result can hide which configuration produced it.
    reasoning_effort: str | None = None
    max_output_tokens_per_request: int | None = None
    client: httpx.Client | None = None
    pricing: PricingTable = PLACEHOLDER_PRICING
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    timeout_seconds: float = 120.0
    name: str = "openai-compatible"
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
            f"{self.base_url.rstrip('/')}/chat/completions",
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
            "messages": build_messages(transcript, system_prompt=self.system_prompt),
            "tools": [{"type": "function", "function": schema} for schema in tools],
            "tool_choice": "auto",
            "parallel_tool_calls": False,
        }
        if self.reasoning_effort is not None:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.max_output_tokens_per_request is not None:
            payload["max_completion_tokens"] = self.max_output_tokens_per_request

        response: httpx.Response | None = None
        started_ns = time.monotonic_ns()
        try:
            response = self._post(payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # A provider failure is not the model's result and not a harness
            # bug, and §13.1 keeps it in its own bucket so it is excluded from
            # aggregates rather than scored as a bad run.
            #
            # It still has to say what happened. Discarding the exception here
            # is what made the first live run report `provider_error` with
            # nothing an operator could act on.
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

        usage = normalise_usage(body.get("usage"))
        cost = estimate_cost(usage, self.pricing)
        reported = {
            **usage.as_dict(),
            "total_tokens": usage.total_tokens,
            **cost.as_dict(),
        }

        choices = body.get("choices") or []
        finish_reason = (
            str(choices[0].get("finish_reason") or "completed") if choices else "completed"
        )
        trace = provider_call_trace(
            payload,
            response_body=body,
            started_ns=started_ns,
            finish_reason=finish_reason,
        )
        message = choices[0].get("message", {}) if choices else {}
        calls = message.get("tool_calls") or []
        if not calls:
            return Step(stop=TerminationReason.COMPLETED, usage=reported, trace=trace)
        if len(calls) > 1:
            return Step(
                stop=TerminationReason.PROVIDER_ERROR,
                usage=reported,
                error={"exception": "UnexpectedParallelToolCalls", "count": len(calls)},
                trace=trace,
            )

        call_record = calls[0]
        call_id = call_record.get("id")
        if not isinstance(call_id, str) or not call_id:
            return Step(
                stop=TerminationReason.PROVIDER_ERROR,
                usage=reported,
                error={"exception": "MissingToolCallId"},
                trace=trace,
            )
        call = call_record.get("function", {})
        arguments = call.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except ValueError:
                # A malformed tool call is the model's mistake. Handing it to the
                # tool layer lets the ordinary error path report it and the run
                # continue, rather than ending on one bad message.
                arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}

        return Step(
            invocation=ToolInvocation(tool_name=call.get("name", ""), arguments=arguments),
            usage=reported,
            trace=trace,
            assistant_turn=AssistantTurn(
                api="chat_completions",
                output=(message,),
                tool_call_id=call_id,
            ),
        )


def default_tools() -> list[dict[str, Any]]:
    """The tool list this adapter would send. Exposed so it can be inspected."""
    return model_schemas()
