"""Configuration for a live provider run, read from the environment.

Separate from `cae run` itself so the rules can be tested without a network, a
key, or a subprocess. Everything here is refusal logic; none of it makes a
request.

Three principles, each of which exists because the alternative silently fails:

**A budget that cannot bind is refused, not accepted.** A dollar cap enforced
against the placeholder pricing table can never be reached, because every rate
in it is zero. Accepting one would leave the operator believing they had a limit.

**The shell wins over the file.** `.env` is read only for names the environment
does not already define, so exporting a variable to override a file's value
works the way people expect rather than the reverse.

**Values are never logged.** The key is read, held, and passed to the adapter.
It is not echoed, not written to a trace, and not included in any error message
— the errors name the *variable*, never its content.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from coding_agent_eval.agent.protocol import Budget
from coding_agent_eval.agent.provider import (
    PricingTable,
    UnpricedModelError,
    pricing_for,
)

#: Everything the run reads. Listed so an unknown `CAE_` variable can be
#: reported as probably-a-typo rather than silently ignored.
KNOWN_VARIABLES: tuple[str, ...] = (
    "CAE_PROVIDER_API_KEY",
    "CAE_PROVIDER_BASE_URL",
    "CAE_PROVIDER_MODEL",
    "CAE_PROVIDER_REASONING_EFFORT",
    "CAE_PROVIDER_API",
    "CAE_MAX_OUTPUT_TOKENS_PER_REQUEST",
    "CAE_MAX_TOKENS",
    "CAE_MAX_TOOL_CALLS",
    "CAE_MAX_WALLCLOCK_SECONDS",
    "CAE_MAX_ESTIMATED_COST_USD",
)

DEFAULT_BASE_URL = "https://api.openai.com/v1"

#: Which endpoint shape `cae run` speaks. Older adapter versions have retained
#: live observations; `chat_completions` remains the compatibility default, while
#: `responses` supports the reasoning-enabled request shape.
DEFAULT_API = "chat_completions"
VALID_APIS: tuple[str, ...] = ("chat_completions", "responses")


class ConfigurationError(RuntimeError):
    """The run was not configured well enough to start. No request was made."""


def read_dotenv(path: Path) -> dict[str, str]:
    """Parse a `.env` file into a mapping, without touching the environment.

    Deliberately strict and small. It understands `KEY=value`, `#` comments and
    blank lines, and nothing else — no interpolation, no `export`, no multi-line
    values. A configuration format that can execute or expand is a configuration
    format that can surprise, and this one only ever has to carry six names and
    a key.
    """
    if not path.is_file():
        return {}

    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        # Surrounding quotes are stripped because people write them; nothing
        # inside is interpreted.
        values[name] = value.strip().strip("\"'")
    return values


def _int_or_none(source: dict[str, str], name: str) -> int | None:
    raw = source.get(name, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a whole number, not {raw!r}") from exc


def _float_or_none(source: dict[str, str], name: str) -> float | None:
    raw = source.get(name, "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number, not {raw!r}") from exc


@dataclass(frozen=True)
class RunConfiguration:
    """A validated live-run configuration. Holds the key; never renders it."""

    api_key: str
    base_url: str
    model: str
    budget: Budget
    pricing: PricingTable
    #: `None` means the request does not mention it and the model uses its default.
    reasoning_effort: str | None = None
    #: One of `VALID_APIS`. Determines which adapter class `live.execute` builds.
    api: str = DEFAULT_API
    #: Provider-side ceiling for one response. Total observed budgets are checked
    #: after a paid request; this limits how far a single request can overshoot.
    max_output_tokens_per_request: int | None = None

    def redacted(self) -> dict[str, object]:
        """What may be written to a trace, a log, or the terminal.

        The key is represented by its presence, never by any part of its value.
        Not even a prefix: a prefix is still key material, and a run artifact is
        a thing people paste into issues.
        """
        return {
            "base_url": self.base_url,
            "model": self.model,
            "api_key_present": True,
            "api": self.api,
            # Recorded even when unset. `null` here means "the request did not
            # mention it", which is a different run from one that set it to
            # "none" — and on a reasoning model, a different subject entirely.
            "reasoning_effort": self.reasoning_effort,
            "max_output_tokens_per_request": self.max_output_tokens_per_request,
            "pricing_table_version": self.pricing.version,
            "pricing_effective_date": self.pricing.effective_date,
            "budget": {
                "max_tokens": self.budget.max_tokens,
                "max_tool_calls": self.budget.max_tool_calls,
                "max_wallclock_seconds": self.budget.max_wallclock_seconds,
                "max_estimated_cost_usd": self.budget.max_estimated_cost_usd,
            },
        }


def load_configuration(
    *,
    environ: dict[str, str] | None = None,
    dotenv_path: Path | None = None,
) -> RunConfiguration:
    """Build a configuration from the environment, falling back to `.env`.

    Raises `ConfigurationError` before anything is opened. A run that cannot be
    configured must fail at the point of configuration, not part way through a
    conversation it has already paid for.
    """
    environ = dict(os.environ if environ is None else environ)
    file_values = read_dotenv(dotenv_path) if dotenv_path is not None else {}

    # The shell wins. A file that overrode an exported variable would make
    # `$env:CAE_PROVIDER_MODEL = "..."` appear to do nothing.
    source = {**file_values, **{k: v for k, v in environ.items() if v}}

    api_key = source.get("CAE_PROVIDER_API_KEY", "").strip()
    if not api_key:
        raise ConfigurationError(
            "CAE_PROVIDER_API_KEY is not set. Export it, or put it in a .env file "
            "that is not committed. No request has been attempted."
        )

    model = source.get("CAE_PROVIDER_MODEL", "").strip()
    if not model:
        raise ConfigurationError(
            "CAE_PROVIDER_MODEL is not set. There is no default: a result that did "
            "not record which model produced it describes nothing."
        )

    api = source.get("CAE_PROVIDER_API", "").strip() or DEFAULT_API
    if api not in VALID_APIS:
        raise ConfigurationError(
            f"CAE_PROVIDER_API must be one of {', '.join(VALID_APIS)}, not {api!r}"
        )

    max_output_tokens_per_request = _int_or_none(source, "CAE_MAX_OUTPUT_TOKENS_PER_REQUEST")
    if max_output_tokens_per_request is not None and max_output_tokens_per_request <= 0:
        raise ConfigurationError("CAE_MAX_OUTPUT_TOKENS_PER_REQUEST must be greater than zero")

    max_cost = _float_or_none(source, "CAE_MAX_ESTIMATED_COST_USD")
    try:
        pricing = pricing_for(model, require_priced=max_cost is not None)
    except UnpricedModelError as exc:
        raise ConfigurationError(str(exc)) from exc

    budget = Budget(
        max_tokens=_int_or_none(source, "CAE_MAX_TOKENS"),
        max_tool_calls=_int_or_none(source, "CAE_MAX_TOOL_CALLS"),
        max_wallclock_seconds=_float_or_none(source, "CAE_MAX_WALLCLOCK_SECONDS"),
        max_estimated_cost_usd=max_cost,
    )
    if not any(
        value is not None
        for value in (
            budget.max_tokens,
            budget.max_tool_calls,
            budget.max_wallclock_seconds,
            budget.max_estimated_cost_usd,
        )
    ):
        raise ConfigurationError(
            "no budget is set, so nothing would stop the run. Set at least "
            "CAE_MAX_TOKENS; it is measured rather than estimated, and is the only "
            "one that bounds spend without depending on a pricing table."
        )

    return RunConfiguration(
        api_key=api_key,
        base_url=source.get("CAE_PROVIDER_BASE_URL", "").strip() or DEFAULT_BASE_URL,
        model=model,
        budget=budget,
        pricing=pricing,
        reasoning_effort=source.get("CAE_PROVIDER_REASONING_EFFORT", "").strip() or None,
        api=api,
        max_output_tokens_per_request=max_output_tokens_per_request,
    )


def suspicious_variables(environ: dict[str, str]) -> list[str]:
    """`CAE_`-prefixed names nothing reads, which are usually typos.

    Reported rather than ignored: a misspelled `CAE_MAX_TOKEN` would leave a run
    with no token budget while its operator believed one was in force.
    """
    return sorted(
        name for name in environ if name.startswith("CAE_") and name not in KNOWN_VARIABLES
    )
