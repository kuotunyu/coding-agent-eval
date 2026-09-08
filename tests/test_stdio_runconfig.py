"""Preflight configuration for an external stdio agent process."""

from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from coding_agent_eval.runconfig import (
    StdioConfigurationError,
    StdioRunConfiguration,
    load_stdio_configuration,
)


def valid_config(
    *,
    command: list[str] | None = None,
    inherited_environment: list[str] | None = None,
    environ: dict[str, str] | None = None,
    **overrides: object,
) -> StdioRunConfiguration:
    """Build a configuration with the local Python executable as its child."""
    parameters: dict[str, object] = {
        "command": command if command is not None else [sys.executable, "-c", "pass"],
        "inherited_environment": (
            inherited_environment if inherited_environment is not None else ["AGENT_KEY"]
        ),
        "agent_name": "test-agent",
        "agent_version": "1.2.3",
        "agent_model": "test-model",
        "max_tool_calls": 4,
        "max_wallclock_seconds": 30.0,
        "startup_timeout_seconds": 10.0,
        "step_timeout_seconds": 120.0,
        "shutdown_grace_seconds": 2.0,
        "environ": {"AGENT_KEY": "value"} if environ is None else environ,
    }
    parameters.update(overrides)
    return load_stdio_configuration(**parameters)  # type: ignore[arg-type]


def test_stdio_requires_both_host_enforceable_budgets() -> None:
    """Removing either host counter would allow an unbounded child run."""
    with pytest.raises(StdioConfigurationError, match="max_tool_calls"):
        valid_config(max_tool_calls=None)
    with pytest.raises(StdioConfigurationError, match="max_wallclock_seconds"):
        valid_config(max_wallclock_seconds=None)


def test_stdio_requires_every_named_environment_variable() -> None:
    """A later spawn must not discover a missing delegated secret."""
    with pytest.raises(StdioConfigurationError, match="AGENT_KEY"):
        valid_config(inherited_environment=["AGENT_KEY"], environ={"PATH": "x"})


def test_redacted_configuration_contains_no_command_or_environment_name() -> None:
    """Public metadata may identify the agent but cannot disclose child inputs."""
    config = valid_config(inherited_environment=["AGENT_KEY"], environ={"AGENT_KEY": "secret"})

    rendered = json.dumps(config.redacted())

    assert "AGENT_KEY" not in rendered
    assert "secret" not in rendered
    assert str(config.command[0]) not in rendered
    assert config.redacted()["budget"] == {
        "max_tokens": None,
        "max_tool_calls": 4,
        "max_wallclock_seconds": 30.0,
        "max_estimated_cost_usd": None,
    }
    assert config.redacted()["usage_source"] == "agent_reported_unverified"
    assert config.redacted()["agent_process_profile"] == "host_unsandboxed"


@pytest.mark.parametrize("field", ["agent_name", "agent_version", "agent_model"])
@pytest.mark.parametrize("value", [None, ""])
def test_stdio_rejects_missing_agent_identity(field: str, value: str | None) -> None:
    """An empty identity would make results from different agents indistinguishable."""
    with pytest.raises(StdioConfigurationError, match=field):
        valid_config(**{field: value})


def test_stdio_rejects_empty_argv() -> None:
    """A child cannot be preflighted without one executable argument."""
    with pytest.raises(StdioConfigurationError, match="command"):
        valid_config(command=[])


@pytest.mark.parametrize("value", ["", " ", "\t"])
def test_stdio_rejects_an_empty_later_argv_element(value: str) -> None:
    """Every argv position is operator configuration and must carry a value."""
    with pytest.raises(StdioConfigurationError, match=r"command\[1\]"):
        valid_config(command=[sys.executable, value])


def test_stdio_rejects_duplicate_environment_names() -> None:
    """Duplicate delegated names indicate a malformed allow-list."""
    with pytest.raises(StdioConfigurationError, match="AGENT_KEY"):
        valid_config(inherited_environment=["AGENT_KEY", "AGENT_KEY"])


def test_stdio_resolves_executable_from_supplied_path_and_pathext() -> None:
    """Changing the supplied PATH must change the executable that will actually spawn."""
    executable = Path(sys.executable)
    config = valid_config(
        command=[executable.stem, "-c", "pass"],
        environ={
            "AGENT_KEY": "value",
            "PATH": str(executable.parent),
            "PATHEXT": executable.suffix,
        },
    )

    assert config.command[0] == str(executable.resolve())


def test_stdio_resolves_a_named_executable_that_already_has_an_extension() -> None:
    """A command with `.exe` must still use only the supplied PATH to resolve."""
    executable = Path(sys.executable)
    config = valid_config(
        command=[executable.name, "-c", "pass"],
        environ={
            "AGENT_KEY": "value",
            "PATH": str(executable.parent),
            "PATHEXT": executable.suffix,
        },
    )

    assert config.command[0] == str(executable.resolve())


def test_stdio_resolves_an_explicit_dot_relative_executable_outside_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `./agent.exe` command names a file directly, rather than a PATH lookup."""
    source = Path(sys.executable)
    copied_executable = tmp_path / source.name
    shutil.copy2(source, copied_executable)
    monkeypatch.chdir(tmp_path)

    config = valid_config(
        command=[os.path.join(".", source.name), "-c", "pass"],
        environ={"AGENT_KEY": "value", "PATH": "", "PATHEXT": source.suffix},
    )

    assert config.command[0] == str(copied_executable.resolve())


def test_stdio_refuses_an_existing_non_executable_file(tmp_path: Path) -> None:
    """A regular source file must be rejected before shell=False attempts to launch it."""
    non_executable = tmp_path / "agent.py"
    non_executable.write_text("print('not executable')", encoding="utf-8")

    with pytest.raises(StdioConfigurationError, match="not executable"):
        valid_config(command=[str(non_executable)])


def test_stdio_refuses_an_unresolvable_executable() -> None:
    """The runner must fail before a later subprocess attempt can fail ambiguously."""
    with pytest.raises(StdioConfigurationError, match="not found"):
        valid_config(command=["not-a-real-agent-command"])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_tool_calls", 0),
        ("max_tool_calls", -1),
        ("max_wallclock_seconds", 0.0),
        ("max_wallclock_seconds", -1.0),
        ("max_wallclock_seconds", math.nan),
        ("max_wallclock_seconds", math.inf),
        ("startup_timeout_seconds", 0.0),
        ("step_timeout_seconds", math.nan),
        ("shutdown_grace_seconds", -math.inf),
    ],
)
def test_stdio_rejects_non_positive_or_non_finite_limits(field: str, value: object) -> None:
    """Every supplied process limit must be a usable, finite positive number."""
    with pytest.raises(StdioConfigurationError, match=field):
        valid_config(**{field: value})


def test_stdio_argv_hash_is_canonical_and_private_parameters_keep_values_private() -> None:
    """The hash is stable for the resolved argv while private data stays owner-only."""
    config = valid_config(command=[sys.executable, "-c", "print('mødel')"])

    same = valid_config(command=[sys.executable, "-c", "print('mødel')"])

    canonical_json = json.dumps(list(config.command), ensure_ascii=False, separators=(",", ":"))
    expected_hash = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    assert config.redacted()["argv_sha256"] == expected_hash
    assert same.redacted()["argv_sha256"] == expected_hash
    assert config.private_parameters() == {
        "argv": list(config.command),
        "inherited_environment": ["AGENT_KEY"],
    }


def test_stdio_uses_resolved_executable_after_the_working_directory_changes(tmp_path: Path) -> None:
    """A PATH-resolved command remains runnable without a shell in an empty directory."""
    executable = Path(sys.executable)
    config = valid_config(
        command=[executable.stem, "-c", "import sys; sys.exit(0)"],
        environ={
            "AGENT_KEY": "value",
            "PATH": str(executable.parent),
            "PATHEXT": executable.suffix,
        },
    )
    empty_directory = tmp_path / "empty"
    empty_directory.mkdir()

    completed = subprocess.run(
        config.command,
        cwd=empty_directory,
        env=config.child_environment(),
        shell=False,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_stdio_child_environment_has_only_base_and_explicitly_inherited_names() -> None:
    """A child receives an allow-list, never the entire parent environment."""
    config = valid_config(
        environ={
            "AGENT_KEY": "value",
            "PATH": "path-value",
            "LANG": "en_US.UTF-8",
            "UNRELATED": "must-not-leak",
        }
    )

    assert config.child_environment() == {
        "AGENT_KEY": "value",
        "PATH": "path-value",
        "LANG": "en_US.UTF-8",
    }
