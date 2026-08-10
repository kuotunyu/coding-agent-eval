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
from typing import Any

import pytest

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
