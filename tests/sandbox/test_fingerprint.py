"""Environment fingerprint (design spec §9.4).

A result is only comparable to another result taken in the same environment.
The fingerprint is what makes that checkable rather than assumed: two runs whose
fingerprints differ are not evidence about each other, however similar their
numbers look.

It deliberately covers the things that change behaviour — the image, the runtime
version, the resolved dependencies, the architecture — and nothing else. Folding
in a timestamp would make every run incomparable to every other, which is the
same as having no fingerprint at all.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, cast

import pytest

from coding_agent_eval.sandbox import fingerprint as fingerprint_module
from coding_agent_eval.sandbox.fingerprint import COMPONENTS, environment_fingerprint

BASE: dict[str, Any] = {
    "base_image_digest": "sha256:" + "a" * 64,
    "prepared_image_digest": "sha256:" + "b" * 64,
    "os_release_id": "debian",
    "os_release_version_id": "12",
    "primary_runtime_version": "Python 3.12.13",
    "package_manager_version": "pip 24.0",
    "lock_manifest_sha256": "c" * 64,
    "arch": "linux/amd64",
}

CURRENT_BASE: dict[str, Any] = {
    "base_image_digest": "sha256:" + "a" * 64,
    "prepared_image_manifest_digest": "sha256:" + "b" * 64,
    "prepared_image_config_digest": "sha256:" + "c" * 64,
    "os_release_id": "debian",
    "os_release_version_id": "12",
    "primary_runtime_version": "3.12.13",
    "package_manager_version": "pip 25.0.1",
    "lock_manifest_sha256": "d" * 64,
    "arch": "linux/amd64",
}


def test_fingerprint_is_a_prefixed_sha256() -> None:
    value = environment_fingerprint(BASE)
    assert value.startswith("sha256:")
    assert len(value) == len("sha256:") + 64


def test_fingerprint_is_stable() -> None:
    assert environment_fingerprint(BASE) == environment_fingerprint(deepcopy(BASE))


def test_key_order_does_not_matter() -> None:
    reordered = {key: BASE[key] for key in sorted(BASE, reverse=True)}
    assert environment_fingerprint(reordered) == environment_fingerprint(BASE)


@pytest.mark.parametrize("component", COMPONENTS)
def test_changing_any_component_changes_the_fingerprint(component: str) -> None:
    other = deepcopy(BASE)
    other[component] = other[component] + "-changed"
    assert environment_fingerprint(other) != environment_fingerprint(BASE)


def test_a_missing_component_raises() -> None:
    """An incomplete fingerprint would claim more comparability than it has."""
    incomplete = deepcopy(BASE)
    del incomplete["arch"]
    with pytest.raises(KeyError):
        environment_fingerprint(incomplete)


def test_extra_keys_are_ignored() -> None:
    """Only the declared components define the environment.

    A caller passing a timestamp along must not make every run incomparable to
    every other, which is what folding it in would do.
    """
    with_extra = dict(BASE, captured_at="2026-08-05T12:00:00Z", hostname="anything")
    assert environment_fingerprint(with_extra) == environment_fingerprint(BASE)


def test_components_are_the_documented_eight() -> None:
    assert set(COMPONENTS) == set(BASE)


def test_current_contract_is_the_documented_nine_components() -> None:
    assert set(fingerprint_module.CURRENT_COMPONENTS) == set(CURRENT_BASE)


@pytest.mark.parametrize("component", sorted(CURRENT_BASE))
def test_changing_any_current_component_changes_the_fingerprint(component: str) -> None:
    other = deepcopy(CURRENT_BASE)
    other[component] = other[component] + "-changed"

    assert environment_fingerprint(other, contract="current") != environment_fingerprint(
        CURRENT_BASE, contract="current"
    )


def test_repository_and_tag_do_not_change_the_current_fingerprint() -> None:
    with_aliases = {
        **CURRENT_BASE,
        "prepared_image_repository": "ghcr.io/kuotunyu/one",
        "prepared_image_tag": "1.0.4",
    }
    changed_aliases = {
        **CURRENT_BASE,
        "prepared_image_repository": "ghcr.io/kuotunyu/two",
        "prepared_image_tag": "9.9.9",
    }

    assert environment_fingerprint(with_aliases, contract="current") == environment_fingerprint(
        changed_aliases, contract="current"
    )


def test_current_contract_refuses_a_missing_config_digest() -> None:
    incomplete = deepcopy(CURRENT_BASE)
    del incomplete["prepared_image_config_digest"]

    with pytest.raises(KeyError, match="prepared_image_config_digest"):
        environment_fingerprint(incomplete, contract="current")


def test_unknown_fingerprint_contract_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown environment fingerprint contract"):
        environment_fingerprint(CURRENT_BASE, contract=cast(Any, "typo"))
