"""`cae run` — the one path that spends money, exercised without spending any.

Two things are asserted harder than anything else here.

**No request is made when the configuration is bad.** Every refusal is checked
against a transport that records attempts, so "it raised" is never mistaken for
"it raised before connecting". A run that discovers its budget is malformed part
way through a conversation has already been paid for.

**The key never leaves.** It is passed to the adapter and nowhere else — not
into the run header, not into the trace, not into an error message, not even as
a prefix. A run directory is a thing people paste into issues.

Every test in this file uses `httpx.MockTransport`, whatever the provider was
asked to do against a real endpoint elsewhere. Both endpoint adapters have
retained live observations, but those traces predate the current strict-replay
contract; see `docs/MANUAL_RUN.md`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import httpx
import pytest
import yaml

from coding_agent_eval.agent.backend import LocalTree
from coding_agent_eval.agent.provider import (
    GPT_5_6_LUNA_PRICING,
    MAX_ERROR_MESSAGE_CHARS,
    PLACEHOLDER_PRICING,
    UnpricedModelError,
    estimate_cost,
    normalise_usage,
    pricing_for,
    render_system_prompt,
)
from coding_agent_eval.live import build_adapter, execute, write_evidence
from coding_agent_eval.runconfig import (
    ConfigurationError,
    load_configuration,
    read_dotenv,
    suspicious_variables,
)
from coding_agent_eval.trace.sanitizer import SanitizerError
from tests.conftest import REPO_ROOT

#: Deliberately **not** key-shaped. The first version of this constant began
#: `sk-`, which is a real vendor prefix, and gate G11 flagged it — correctly. A
#: fixture that looks like a credential is one somebody eventually greps for and
#: panics over, and it teaches the scanner's corpus nothing it does not already
#: cover. What these tests need is a distinctive string, not a realistic one:
#: `redacted()` never includes the key whatever its shape.
KEY = "placeholder-value-never-logged-4f19c2"
FIXTURE = REPO_ROOT / "fixtures" / "fx-taskq-py"
MANIFEST_DIGEST = "sha256:" + "a" * 64
CONFIG_DIGEST = "sha256:" + "b" * 64
IMAGE_REPOSITORY = "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py"
IMMUTABLE_IMAGE_REF = f"{IMAGE_REPOSITORY}@{MANIFEST_DIGEST}"

VALID = {
    "CAE_PROVIDER_API_KEY": KEY,
    "CAE_PROVIDER_MODEL": "gpt-5.6-luna",
    "CAE_MAX_TOKENS": "200000",
}


def configuration(**overrides: str) -> Any:
    environ = {**VALID, **overrides}
    return load_configuration(environ={k: v for k, v in environ.items() if v})


# --------------------------------------------------------------- pricing


def test_the_luna_rates_are_the_ones_that_were_read_from_the_model_page() -> None:
    """Pinned by value, with the source that makes them checkable."""
    assert GPT_5_6_LUNA_PRICING.version == "openai-gpt-5.6-luna@2026-08-11-r2"
    assert GPT_5_6_LUNA_PRICING.input_per_mtok_usd == 0.20
    assert GPT_5_6_LUNA_PRICING.output_per_mtok_usd == 1.20
    assert GPT_5_6_LUNA_PRICING.cached_input_per_mtok_usd == 0.02
    assert GPT_5_6_LUNA_PRICING.effective_date == "2026-08-11"
    assert (
        GPT_5_6_LUNA_PRICING.source == "https://developers.openai.com/api/docs/models/gpt-5.6-luna"
    )


def test_a_priced_run_costs_what_the_rates_say() -> None:
    """Arithmetic checked by hand, so a units error shows up as a wrong number.

    1M input, 1M output: 0.20 + 1.20 = 1.40.
    """
    usage = normalise_usage(
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1_000_000,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    estimate = estimate_cost(usage, GPT_5_6_LUNA_PRICING)
    assert estimate.estimated_cost_usd == pytest.approx(1.40)
    assert estimate.completeness == "complete"


def test_cached_input_is_charged_once_at_the_cached_rate() -> None:
    """Half cached: 0.5M x 0.20 + 0.5M x 0.02 = 0.10 + 0.01."""
    usage = normalise_usage(
        {
            "prompt_tokens": 1_000_000,
            "completion_tokens": 0,
            "prompt_tokens_details": {"cached_tokens": 500_000},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    assert estimate_cost(usage, GPT_5_6_LUNA_PRICING).estimated_cost_usd == pytest.approx(0.11)


def test_current_luna_pricing_reconciles_attempt_3_usage() -> None:
    """Literal retained usage: uncached input + cached input + output."""
    usage = normalise_usage(
        {
            "input_tokens": 88_934,
            "output_tokens": 1_052,
            "input_tokens_details": {"cached_tokens": 70_786},
            "output_tokens_details": {"reasoning_tokens": 446},
        }
    )

    estimate = estimate_cost(usage, GPT_5_6_LUNA_PRICING)

    assert estimate.estimated_cost_usd == pytest.approx(0.00630772)
    assert estimate.pricing_table_version == "openai-gpt-5.6-luna@2026-08-11-r2"
    assert estimate.completeness == "complete"


def test_reasoning_tokens_are_not_charged_a_second_time() -> None:
    """They are billed inside completion_tokens; pricing them again would double.

    Same output total, all of it reasoning — the cost must not move.
    """
    plain = normalise_usage(
        {
            "prompt_tokens": 0,
            "completion_tokens": 1_000_000,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        }
    )
    reasoning = normalise_usage(
        {
            "prompt_tokens": 0,
            "completion_tokens": 1_000_000,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 1_000_000},
        }
    )
    assert estimate_cost(plain, GPT_5_6_LUNA_PRICING).estimated_cost_usd == pytest.approx(
        estimate_cost(reasoning, GPT_5_6_LUNA_PRICING).estimated_cost_usd
    )
    assert reasoning.reasoning_tokens == 1_000_000, "still recorded, just not repriced"


def test_an_unknown_model_is_unpriced_and_says_so_when_a_budget_needs_it() -> None:
    assert pricing_for("no-such-model") is PLACEHOLDER_PRICING
    with pytest.raises(UnpricedModelError, match="could never be reached"):
        pricing_for("no-such-model", require_priced=True)


# ---------------------------------------------------------- configuration


def test_a_dollar_budget_on_an_unpriced_model_is_refused() -> None:
    """The refusal that matters most: a cap that cannot bind is not a cap.

    Placeholder pricing is all zeroes, so `estimated_cost_usd` is always 0.00
    and the limit is unreachable. An operator who set one would believe they
    were protected.
    """
    with pytest.raises(ConfigurationError, match="could never be reached"):
        configuration(CAE_PROVIDER_MODEL="mystery-model", CAE_MAX_ESTIMATED_COST_USD="7.00")


def test_a_dollar_budget_on_a_priced_model_is_accepted() -> None:
    config = configuration(CAE_MAX_ESTIMATED_COST_USD="7.00")
    assert config.budget.max_estimated_cost_usd == 7.00
    assert config.pricing is GPT_5_6_LUNA_PRICING


def test_an_unpriced_model_is_allowed_when_no_dollar_budget_is_set() -> None:
    """Tokens still bound it, and they are measured rather than estimated."""
    config = configuration(CAE_PROVIDER_MODEL="mystery-model")
    assert config.pricing is PLACEHOLDER_PRICING
    assert config.budget.max_tokens == 200000


def test_a_run_with_no_budget_at_all_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="nothing would stop the run"):
        configuration(CAE_MAX_TOKENS="")


def test_a_missing_key_is_refused_by_name_not_by_value() -> None:
    with pytest.raises(ConfigurationError) as excinfo:
        configuration(CAE_PROVIDER_API_KEY="")
    assert "CAE_PROVIDER_API_KEY" in str(excinfo.value)


def test_a_missing_model_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="CAE_PROVIDER_MODEL"):
        configuration(CAE_PROVIDER_MODEL="")


def test_a_malformed_budget_is_refused_rather_than_ignored() -> None:
    """Silently dropping an unparseable limit would leave the run unbounded."""
    with pytest.raises(ConfigurationError, match="whole number"):
        configuration(CAE_MAX_TOKENS="lots")


def test_a_per_request_output_limit_is_validated_and_recorded() -> None:
    config = configuration(CAE_MAX_OUTPUT_TOKENS_PER_REQUEST="2048")

    assert config.max_output_tokens_per_request == 2048
    assert config.redacted()["max_output_tokens_per_request"] == 2048


@pytest.mark.parametrize("value", ["0", "-1"])
def test_a_non_positive_per_request_output_limit_is_refused(value: str) -> None:
    with pytest.raises(ConfigurationError, match="greater than zero"):
        configuration(CAE_MAX_OUTPUT_TOKENS_PER_REQUEST=value)


def test_the_default_api_is_chat_completions() -> None:
    assert configuration().api == "chat_completions"


def test_the_responses_api_may_be_selected() -> None:
    assert configuration(CAE_PROVIDER_API="responses").api == "responses"


@pytest.mark.parametrize("api", ["chat_completions", "responses"])
def test_both_live_adapters_receive_the_registered_tool_budget_in_the_prompt(
    api: str,
) -> None:
    config = configuration(CAE_PROVIDER_API=api, CAE_MAX_TOOL_CALLS="12")

    adapter = build_adapter(config, client=None)

    assert adapter.system_prompt == render_system_prompt(12)


def test_an_unknown_api_value_is_refused() -> None:
    with pytest.raises(ConfigurationError, match="CAE_PROVIDER_API"):
        configuration(CAE_PROVIDER_API="v2beta")


def test_the_default_base_url_is_openai() -> None:
    assert configuration().base_url == "https://api.openai.com/v1"
    assert configuration(CAE_PROVIDER_BASE_URL="https://x.invalid/v1").base_url == (
        "https://x.invalid/v1"
    )


# ------------------------------------------------------------------ dotenv


def test_the_shell_overrides_the_file(tmp_path: Path) -> None:
    """Otherwise `$env:CAE_PROVIDER_MODEL = "..."` would appear to do nothing."""
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"CAE_PROVIDER_API_KEY={KEY}\nCAE_PROVIDER_MODEL=from-file\nCAE_MAX_TOKENS=1000\n",
        encoding="utf-8",
    )
    config = load_configuration(
        environ={"CAE_PROVIDER_MODEL": "gpt-5.6-luna"}, dotenv_path=env_file
    )
    assert config.model == "gpt-5.6-luna"
    assert config.budget.max_tokens == 1000, "the file still supplies what the shell does not"


def test_dotenv_ignores_comments_blanks_and_quotes(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# a comment\n\nCAE_PROVIDER_MODEL="quoted"\nnot a pair\nCAE_MAX_TOKENS=5\n',
        encoding="utf-8",
    )
    assert read_dotenv(env_file) == {"CAE_PROVIDER_MODEL": "quoted", "CAE_MAX_TOKENS": "5"}


def test_a_missing_dotenv_is_not_an_error(tmp_path: Path) -> None:
    assert read_dotenv(tmp_path / "absent") == {}


def test_a_misspelled_variable_is_reported() -> None:
    """A typo'd budget leaves a run unbounded while its operator believes otherwise."""
    assert suspicious_variables({"CAE_MAX_TOKEN": "1", "PATH": "x"}) == ["CAE_MAX_TOKEN"]
    assert suspicious_variables({"CAE_MAX_TOKENS": "1"}) == []


# ------------------------------------------------------------------ secrecy


def test_the_redacted_view_carries_no_part_of_the_key() -> None:
    """Not even a prefix. A prefix is still key material."""
    rendered = json.dumps(configuration().redacted())
    assert KEY not in rendered
    assert KEY[:12] not in rendered
    assert '"api_key_present": true' in rendered


# ------------------------------------------------------------ the run path


def completion(content: str | None = None, tool_calls: Any = None, usage: Any = None) -> dict:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "choices": [{"message": message, "finish_reason": "stop"}],
        "usage": usage
        or {
            "prompt_tokens": 1000,
            "completion_tokens": 200,
            "prompt_tokens_details": {"cached_tokens": 0},
            "completion_tokens_details": {"reasoning_tokens": 0},
        },
    }


def submit_then_stop() -> Any:
    """A provider that submits one finding and then stops."""
    finding = {
        "id": "f-1",
        "file": "src/taskq/util.py",
        "line_start": 1,
        "line_end": 2,
        "category": "correctness",
        "severity": "low",
        "claim": "A placeholder finding produced by a mock transport.",
        "root_cause": "There is no model here; this exercises the wiring.",
        "evidence": "Constructed by the test, not observed.",
        "suggested_verification": "None; this is not a real finding.",
    }
    responses = [
        completion(
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "write_findings",
                        "arguments": json.dumps({"findings": [finding]}),
                    },
                }
            ]
        ),
        completion(content="Done."),
    ]
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json=responses[min(len(calls) - 1, len(responses) - 1)])

    return handler, calls


def current_fixture(tmp_path: Path, *, include_config_digest: bool = True) -> Path:
    """Copy the fixture and replace its OCI identity with deterministic test values."""
    destination = tmp_path / "current-fixture"
    shutil.copytree(FIXTURE, destination)
    manifest_path = destination / "fixture.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    environment = manifest["environment"]
    environment.pop("prepared_image_digest", None)
    environment.update(
        {
            "prepared_image_repository": IMAGE_REPOSITORY,
            "prepared_image_tag": manifest["fixture_version"],
            "prepared_image_manifest_digest": MANIFEST_DIGEST,
        }
    )
    if include_config_digest:
        environment["prepared_image_config_digest"] = CONFIG_DIGEST
    else:
        environment.pop("prepared_image_config_digest", None)
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8", newline="\n"
    )
    return destination


def test_a_run_writes_evidence_and_never_writes_the_key(tmp_path: Path) -> None:
    """The whole path, end to end, against a mock transport."""
    handler, calls = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(CAE_MAX_ESTIMATED_COST_USD="7.00"),
        snapshot="mutated",
        bug_index=0,
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert calls, "the mock provider was never called"

    directory = write_evidence(run, tmp_path / "out")
    written = "\n".join(path.read_text(encoding="utf-8") for path in sorted(directory.iterdir()))
    assert KEY not in written
    assert KEY[:12] not in written

    header = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert header["fixture_id"] == "fx-taskq-py"
    assert header["snapshot"] == "mutated"
    assert header["bugs_in_snapshot"] == ["fx-taskq-py/B-001"]
    assert header["tool_backend"] == "host_process"
    assert header["provider"] == configuration(CAE_MAX_ESTIMATED_COST_USD="7.00").redacted()
    assert header["provider"]["api_key_present"] is True

    raw_header = run.raw_store.read_events()[0]["payload"]
    assert raw_header["provider"] == "chat_completions"
    assert raw_header["model"] == "gpt-5.6-luna"
    assert raw_header["agent_adapter"] == "openai-compatible"
    assert raw_header["image_ref"] is None
    assert raw_header["image_manifest_digest"] is None
    assert raw_header["image_config_digest"] is None

    public_header = json.loads(
        (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert public_header["schema_version"] == "0.2.0"


def test_provider_stable_raw_and_public_mapping_matches_the_golden_contract(
    tmp_path: Path,
) -> None:
    """The shared executor must preserve every stable provider identity field together."""
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        raw_store_root=tmp_path / ".run-store",
        run_id="provider-golden",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    directory = write_evidence(run, tmp_path / "out")
    raw = run.raw_store.read_events()[0]["payload"]
    public = json.loads((directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()[0])[
        "payload"
    ]
    stable_keys = (
        "agent_adapter",
        "agent_adapter_version",
        "provider",
        "model",
        "prompt_hash",
        "system_prompt_version",
        "params_hash",
        "budget",
    )
    expected_identity = {
        "agent_adapter": "openai-compatible",
        "agent_adapter_version": "0.5.0",
        "provider": "chat_completions",
        "model": "gpt-5.6-luna",
        "prompt_hash": "6d0160d379fbe0c0a0bc2190e063adc0ef1aef6d36407f73e5580483b4523423",
        "system_prompt_version": "0.3.0",
        "params_hash": "22cf264d4cf853dac6c109ef6da63152873953f635e5a44f9c05dc0454787ad6",
        "budget": {
            "max_tokens": 200000,
            "max_tool_calls": None,
            "max_wallclock_seconds": None,
            "max_estimated_cost_usd": None,
        },
    }
    expected_parameters = {
        "api": "chat_completions",
        "api_key_present": True,
        "base_url": "https://api.openai.com/v1",
        "budget": expected_identity["budget"],
        "max_output_tokens_per_request": None,
        "model": "gpt-5.6-luna",
        "pricing_effective_date": "2026-08-11",
        "pricing_table_version": "openai-gpt-5.6-luna@2026-08-11-r2",
        "reasoning_effort": None,
    }

    assert {key: raw[key] for key in stable_keys} == expected_identity
    assert raw["params"] == expected_parameters
    assert {key: public[key] for key in stable_keys} == expected_identity
    assert "params" not in public


def test_returned_headers_cannot_mutate_execution_metadata_or_trace_identity(
    tmp_path: Path,
) -> None:
    """A caller-owned artifact must not alias the execution's frozen identity."""
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        raw_store_root=tmp_path / ".run-store",
        run_id="immutable-metadata",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    raw_before = run.raw_store.read_events()[0]["payload"]
    returned_header = run.header()
    returned_trace_header = run.trace_header()

    returned_header["provider"]["model"] = "tampered-model"
    returned_header["provider"]["budget"]["max_tokens"] = 1
    returned_trace_header["params"]["model"] = "tampered-params"

    assert run.metadata.public_configuration["model"] == "gpt-5.6-luna"
    assert run.metadata.private_parameters["model"] == "gpt-5.6-luna"
    assert run.metadata.public_configuration is not run.metadata.private_parameters
    public_budget = cast(dict[str, Any], run.metadata.public_configuration["budget"])
    private_budget = cast(dict[str, Any], run.metadata.private_parameters["budget"])
    assert public_budget is not private_budget
    with pytest.raises(TypeError):
        public_budget["max_tokens"] = 7
    assert run.events[0]["payload"]["params"]["model"] == "gpt-5.6-luna"
    assert run.raw_store.read_events()[0]["payload"] == raw_before
    raw_encoded_params = json.dumps(
        raw_before["params"],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert (
        raw_before["params_hash"] == hashlib.sha256(raw_encoded_params.encode("utf-8")).hexdigest()
    )
    assert run.header()["provider"]["model"] == "gpt-5.6-luna"

    subsequent_trace_header = run.trace_header()
    assert subsequent_trace_header["params"]["model"] == "gpt-5.6-luna"
    encoded_params = json.dumps(
        subsequent_trace_header["params"],
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    assert (
        subsequent_trace_header["params_hash"]
        == hashlib.sha256(encoded_params.encode("utf-8")).hexdigest()
    )


def test_current_isolated_run_binds_the_fixture_oci_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = current_fixture(tmp_path)

    @contextmanager
    def fake_tool_container(image: str, tree: Path) -> Any:
        assert image == IMMUTABLE_IMAGE_REF
        local = LocalTree(tree)
        yield SimpleNamespace(
            description=f"measure_container:{image}",
            read_bytes=local.read_bytes,
            list_entries=local.list_entries,
            read_subtree=local.read_subtree,
        )

    import coding_agent_eval.sandbox.tool_container as tool_container_module

    monkeypatch.setattr(tool_container_module, "tool_container", fake_tool_container)
    handler, calls = submit_then_stop()
    run = execute(
        fixture,
        configuration=configuration(),
        snapshot="clean",
        isolate_image=IMMUTABLE_IMAGE_REF,
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    assert calls
    trace_header = run.raw_store.read_events()[0]["payload"]
    assert trace_header["image_ref"] == IMMUTABLE_IMAGE_REF
    assert trace_header["image_manifest_digest"] == MANIFEST_DIGEST
    assert trace_header["image_config_digest"] == CONFIG_DIGEST
    assert trace_header["tool_backend"] == f"measure_container:{MANIFEST_DIGEST}"
    assert trace_header["sandbox_profile"] == "measure"


@pytest.mark.parametrize(
    "image",
    [
        f"{IMAGE_REPOSITORY}:1.0.3",
        f"{IMAGE_REPOSITORY}@sha256:" + "c" * 64,
    ],
)
def test_tag_only_or_mismatched_isolate_is_refused_before_provider_initialization(
    image: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coding_agent_eval.live as live_module

    fixture = current_fixture(tmp_path)

    def provider_must_not_be_built(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider initialized before image identity validation")

    monkeypatch.setattr(live_module, "build_adapter", provider_must_not_be_built)

    with pytest.raises(ValueError, match="fixture-derived immutable"):
        execute(
            fixture,
            configuration=configuration(),
            snapshot="clean",
            isolate_image=image,
            workspace=tmp_path / "work",
        )


def test_missing_config_digest_is_refused_before_provider_initialization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coding_agent_eval.live as live_module

    fixture = current_fixture(tmp_path, include_config_digest=False)

    def provider_must_not_be_built(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider initialized before image identity validation")

    monkeypatch.setattr(live_module, "build_adapter", provider_must_not_be_built)

    with pytest.raises(ValueError, match="prepared_image_config_digest"):
        execute(
            fixture,
            configuration=configuration(),
            snapshot="clean",
            isolate_image=IMMUTABLE_IMAGE_REF,
            workspace=tmp_path / "work",
        )


def test_legacy_fixture_cannot_claim_current_isolation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import coding_agent_eval.live as live_module

    fixture = tmp_path / "legacy-fixture"
    shutil.copytree(FIXTURE, fixture)
    manifest_path = fixture / "fixture.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    environment = manifest["environment"]
    for field in (
        "prepared_image_repository",
        "prepared_image_manifest_digest",
        "prepared_image_config_digest",
    ):
        environment.pop(field)
    environment["prepared_image_tag"] = "cae/fx-taskq-py:1.0.3"
    environment["prepared_image_digest"] = "sha256:" + "c" * 64
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    def provider_must_not_be_built(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("provider initialized before image identity validation")

    monkeypatch.setattr(live_module, "build_adapter", provider_must_not_be_built)

    with pytest.raises(ValueError, match="current OCI identity"):
        execute(
            fixture,
            configuration=configuration(),
            snapshot="clean",
            isolate_image="sha256:" + "c" * 64,
            workspace=tmp_path / "work",
        )


def test_a_live_run_persists_one_complete_private_event_sequence(tmp_path: Path) -> None:
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="mutated",
        workspace=tmp_path / "work",
        raw_store_root=tmp_path / ".run-store",
        run_id="mock-live-run",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    raw_events = run.raw_store.read_events()
    event_names = [event["event"] for event in raw_events]
    assert event_names[0] == "run_header"
    assert event_names[-2:] == ["cost", "termination"]
    assert event_names.count("run_header") == 1
    assert event_names.count("cost") == 1
    assert event_names.count("termination") == 1

    directory = write_evidence(run, tmp_path / "public")
    public_events = [
        json.loads(line)
        for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in public_events] == event_names
    assert all("request_body" not in event["payload"] for event in public_events)
    assert all("response_body" not in event["payload"] for event in public_events)


def test_live_evidence_refuses_a_tampered_unknown_raw_field_atomically(tmp_path: Path) -> None:
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        raw_store_root=tmp_path / ".run-store",
        run_id="tampered-live-run",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    events = run.raw_store.read_events()
    events[0]["payload"]["unclassified"] = "must fail closed"
    run.raw_store.events_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events), encoding="utf-8"
    )
    output = tmp_path / "public"

    with pytest.raises(SanitizerError, match="unclassified"):
        write_evidence(run, output)

    assert not output.exists() or list(output.iterdir()) == []


def test_the_header_names_the_adapter_that_actually_ran_not_a_literal(tmp_path: Path) -> None:
    """Regression test for the bug a second adapter existing exposed.

    `agent_adapter` was a hardcoded `"openai-compatible"` string until
    `OpenAIResponsesAdapter` existed to prove it wrong — every run would have
    reported the same adapter name regardless of which one built the request.
    `agent_adapter_version` was missing from the live header entirely, unlike
    the synthetic e2e path, which already carried it.
    """
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="mutated",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    header = run.header()
    assert header["agent_adapter"] == "openai-compatible"
    assert header["agent_adapter_version"]


def test_choosing_the_responses_api_selects_the_other_adapter(tmp_path: Path) -> None:
    """`CAE_PROVIDER_API=responses` has to change which class builds the request,
    which endpoint it posts to, and what the header says produced the run —
    all three, not just one of them silently disagreeing with the others.
    """
    from coding_agent_eval.agent.responses_provider import ADAPTER_VERSION as RESPONSES_VERSION

    posted_to: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        posted_to.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"type": "message", "role": "assistant", "content": []}],
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "input_tokens_details": {"cached_tokens": 0},
                    "output_tokens_details": {"reasoning_tokens": 0},
                },
            },
        )

    run = execute(
        FIXTURE,
        configuration=configuration(CAE_PROVIDER_API="responses"),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert posted_to and posted_to[0].endswith("/responses")

    header = run.header()
    assert header["agent_adapter"] == "openai-responses"
    assert header["agent_adapter_version"] == RESPONSES_VERSION
    assert header["provider"]["api"] == "responses"


def test_the_run_header_carries_no_score(tmp_path: Path) -> None:
    """A live run produces findings, not metrics. The distinction is the point.

    `verified_*` needs a human ruling; a header with a recall field would make
    that step look like something the command had already done.
    """
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="mutated",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    header = run.header()
    for forbidden in ("verified_bug_recall", "localization_recall", "metrics", "publishable"):
        assert forbidden not in header
    assert "human ruling" in header["adjudication"]


def test_the_usage_summary_can_be_reconciled_with_its_own_cost(tmp_path: Path) -> None:
    """Cached input has to be reported or the summary contradicts itself.

    The first live run billed 199,181 input tokens for $0.0115 — 3.7x below the
    headline input rate — because 87% of it was a cache hit priced at a tenth.
    A summary carrying the input count and the cost but not the cached count
    gives a reader two numbers that cannot both be right.
    """
    cached_usage = {
        "prompt_tokens": 1_000_000,
        "completion_tokens": 0,
        "prompt_tokens_details": {"cached_tokens": 900_000},
        "completion_tokens_details": {"reasoning_tokens": 0},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=completion(content="done", usage=cached_usage))

    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    usage = run.usage_total()
    assert usage["cached_input_tokens"] == 900_000

    # 100k at 0.20 plus 900k at 0.02, both per million.
    reconciled = (
        (usage["input_tokens"] - usage["cached_input_tokens"]) * 0.20 / 1e6
        + usage["cached_input_tokens"] * 0.02 / 1e6
        + usage["output_tokens"] * 1.20 / 1e6
    )
    assert usage["estimated_cost_usd"] == pytest.approx(reconciled, abs=1e-9)
    assert reconciled == pytest.approx(0.038)


def test_the_usage_total_prices_the_run_with_the_real_table(tmp_path: Path) -> None:
    """Two calls at 1000 in / 200 out each, priced with the current table."""
    handler, calls = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    usage = run.usage_total()
    expected = usage["input_tokens"] * 0.20 / 1e6 + usage["output_tokens"] * 1.20 / 1e6

    assert usage["input_tokens"] == 1000 * len(calls)
    assert usage["estimated_cost_usd"] == pytest.approx(expected, abs=1e-9)
    assert usage["pricing_table_version"] == GPT_5_6_LUNA_PRICING.version
    assert usage["completeness"] == "complete"


def test_a_clean_snapshot_records_no_bug(tmp_path: Path) -> None:
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert run.bug_ids == ()
    assert run.header()["bugs_in_snapshot"] == []


def test_the_public_trace_carries_no_tool_output(tmp_path: Path) -> None:
    """Tool output is third-party source the agent read; it must not be published."""
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    directory = write_evidence(run, tmp_path / "out")
    for line in (directory / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        assert "content" not in json.loads(line)["payload"]


# --------------------------------------------------------- reasoning effort


def test_reasoning_effort_is_absent_unless_set(tmp_path: Path) -> None:
    """An unset value must not become `"none"` by accident.

    A request that does not mention `reasoning_effort` and one that sets it to
    `"none"` are different runs, and on a reasoning model they measure different
    things. Silence has to stay silence.
    """
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=completion(content="done"))

    execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert "reasoning_effort" not in sent[0]


def test_reasoning_effort_is_sent_when_set(tmp_path: Path) -> None:
    """The escape hatch for a model that refuses function tools without it."""
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=completion(content="done"))

    execute(
        FIXTURE,
        configuration=configuration(CAE_PROVIDER_REASONING_EFFORT="none"),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert sent[0]["reasoning_effort"] == "none"


def test_the_run_header_records_the_reasoning_effort_either_way() -> None:
    """A result must declare the configuration that produced it.

    `null` and `"none"` both appear, because the difference between them is the
    difference between measuring a reasoning model and measuring it with its
    reasoning switched off.
    """
    assert configuration().redacted()["reasoning_effort"] is None
    assert configuration(CAE_PROVIDER_REASONING_EFFORT="none").redacted()["reasoning_effort"] == (
        "none"
    )


# ------------------------------------------------------- provider failures


def failing(status: int, body: Any) -> Any:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return handler


def failed_run(tmp_path: Path, handler: Any) -> Any:
    return execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_a_provider_failure_says_what_happened(tmp_path: Path) -> None:
    """The defect the first live run exposed: `provider_error` and nothing else.

    A run that failed and cannot say why is a run nobody can act on. It stopped
    after two seconds with a one-line trace, because the exception had been
    caught and discarded.
    """
    run = failed_run(
        tmp_path,
        failing(
            404,
            {
                "error": {
                    "message": "The model `x` does not exist or you do not have access to it.",
                    "type": "invalid_request_error",
                    "code": "model_not_found",
                }
            },
        ),
    )
    assert run.result.termination_reason.value == "provider_error"

    failure = run.failure
    assert failure["status"] == 404
    assert failure["type"] == "invalid_request_error"
    assert failure["code"] == "model_not_found"
    assert "does not exist" in failure["message"]
    assert run.usage_total()["completeness"] == "complete"


@pytest.mark.parametrize("status", [401, 429, 500])
def test_every_http_failure_carries_its_status(tmp_path: Path, status: int) -> None:
    """A 401, a 429 and a 500 need different responses from the operator."""
    run = failed_run(tmp_path, failing(status, {"error": {"message": "no", "type": "t"}}))
    assert run.failure["status"] == status


def test_a_transport_failure_with_no_response_still_reports_something(tmp_path: Path) -> None:
    """DNS, TLS, connect and timeout produce no response at all."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nowhere to connect", request=request)

    run = failed_run(tmp_path, handler)
    assert run.failure["exception"] == "ConnectError"
    assert "status" not in run.failure


def test_a_non_error_shaped_body_still_narrows_it_down(tmp_path: Path) -> None:
    """A JSON body that is not an error object still says which contract it speaks."""
    run = failed_run(tmp_path, failing(400, {"detail": "something", "trace_id": "abc"}))
    assert run.failure["body_keys"] == ["detail", "trace_id"]


def test_the_provider_message_stays_out_of_the_public_trace(tmp_path: Path) -> None:
    """The classification is publishable; the free text is not.

    A provider may quote the request back, and the request carries the tree the
    agent read. The status and code are enough to act on in public.
    """
    secret_sounding = "here is the source we received: def verify(a, b): return a == b"
    run = failed_run(
        tmp_path,
        failing(400, {"error": {"message": secret_sounding, "type": "bad", "code": "c"}}),
    )
    directory = write_evidence(run, tmp_path / "out")

    trace = (directory / "trace.jsonl").read_text(encoding="utf-8")
    assert secret_sounding not in trace
    assert "def verify" not in trace, "no fragment of the quoted request may survive"

    raw = json.dumps(run.raw_store.read_events())
    assert secret_sounding in raw, "the owner-only store must retain the actionable diagnostic"

    payload = json.loads(trace.splitlines()[-1])["payload"]
    assert payload["provider_error"]["status"] == 400
    assert payload["provider_error"]["code"] == "c"
    assert "provider_error_message" not in payload

    # `write_evidence` creates a publishable directory, so its companion JSON
    # follows the same boundary as the sanitized trace. The operator still has
    # the complete message in `.run-store` and in the immediate CLI diagnostic.
    header = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert secret_sounding not in json.dumps(header)
    assert "message" not in header["provider_error"]


def test_a_long_provider_message_is_truncated(tmp_path: Path) -> None:
    """A provider echoing the whole request must not turn a diagnostic into a copy."""
    run = failed_run(tmp_path, failing(400, {"error": {"message": "x" * 5000, "type": "t"}}))
    assert len(run.failure["message"]) == MAX_ERROR_MESSAGE_CHARS


def test_a_successful_run_carries_no_failure(tmp_path: Path) -> None:
    """So an empty `provider_error` is never mistaken for one nobody filled in."""
    handler, _ = submit_then_stop()
    run = execute(
        FIXTURE,
        configuration=configuration(),
        snapshot="clean",
        workspace=tmp_path / "work",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    assert run.failure == {}
    directory = write_evidence(run, tmp_path / "out")
    header = json.loads((directory / "run.json").read_text(encoding="utf-8"))
    assert "provider_error" not in header


def test_an_unknown_snapshot_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="snapshot must be"):
        execute(
            FIXTURE,
            configuration=configuration(),
            snapshot="halfway",
            workspace=tmp_path / "work",
            client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200))),
        )
