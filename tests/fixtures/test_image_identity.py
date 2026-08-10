"""OCI prepared-image identity contract."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

MANIFEST_DIGEST = "sha256:" + "a" * 64
CONFIG_DIGEST = "sha256:" + "b" * 64


def current_environment(**overrides: object) -> dict[str, object]:
    environment: dict[str, object] = {
        "prepared_image_repository": "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py",
        "prepared_image_tag": "1.0.4",
        "prepared_image_manifest_digest": MANIFEST_DIGEST,
        "prepared_image_config_digest": CONFIG_DIGEST,
    }
    environment.update(overrides)
    return environment


def image_identity_api() -> tuple[type[Any], type[ValueError]]:
    """Import inside the test so a missing feature is a useful RED assertion."""
    try:
        from coding_agent_eval.fixtures.image_identity import (
            ImageIdentityError,
            PreparedImageIdentity,
        )
    except ModuleNotFoundError:
        pytest.fail("PreparedImageIdentity is not implemented")
    return PreparedImageIdentity, ImageIdentityError


def identity_from(environment: Mapping[str, object]) -> Any:
    identity_type, _ = image_identity_api()
    return identity_type.from_environment(environment)


def test_current_environment_builds_the_digest_qualified_reference() -> None:
    identity = identity_from(current_environment())

    assert identity.repository == "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py"
    assert identity.tag == "1.0.4"
    assert identity.manifest_digest == MANIFEST_DIGEST
    assert identity.config_digest == CONFIG_DIGEST
    assert identity.immutable_ref == (
        "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py@" + MANIFEST_DIGEST
    )


def test_mutable_tag_is_not_part_of_the_immutable_reference() -> None:
    first = identity_from(current_environment(prepared_image_tag="1.0.4"))
    second = identity_from(current_environment(prepared_image_tag="1.0.5"))

    assert first.immutable_ref == second.immutable_ref


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("prepared_image_manifest_digest", "sha256:" + "a" * 63),
        ("prepared_image_manifest_digest", "sha256:" + "A" * 64),
        ("prepared_image_config_digest", "sha256:" + "b" * 65),
        ("prepared_image_config_digest", "sha512:" + "b" * 64),
    ],
)
def test_digest_fields_require_lowercase_sha256(field: str, value: str) -> None:
    _, error_type = image_identity_api()

    with pytest.raises(error_type, match=field):
        identity_from(current_environment(**{field: value}))


@pytest.mark.parametrize(
    "repository",
    [
        "ghcr.io/kuotunyu/coding-agent-eval-fx-taskq-py:1.0.4",
        "ghcr.io/another-owner/coding-agent-eval-fx-taskq-py",
        "docker.io/kuotunyu/coding-agent-eval-fx-taskq-py",
        "GHCR.io/kuotunyu/coding-agent-eval-fx-taskq-py",
    ],
)
def test_repository_is_an_unqualified_owner_scoped_ghcr_name(repository: str) -> None:
    _, error_type = image_identity_api()

    with pytest.raises(error_type, match="prepared_image_repository"):
        identity_from(current_environment(prepared_image_repository=repository))


@pytest.mark.parametrize(
    "tag",
    ["latest", "v1.0.4", "1.0", "1.0.4@sha256:" + "a" * 64],
)
def test_tag_is_a_version_alias_only(tag: str) -> None:
    _, error_type = image_identity_api()

    with pytest.raises(error_type, match="prepared_image_tag"):
        identity_from(current_environment(prepared_image_tag=tag))


@pytest.mark.parametrize(
    "field",
    [
        "prepared_image_repository",
        "prepared_image_tag",
        "prepared_image_manifest_digest",
        "prepared_image_config_digest",
    ],
)
def test_every_current_identity_field_is_required(field: str) -> None:
    _, error_type = image_identity_api()
    environment = current_environment()
    environment.pop(field)

    with pytest.raises(error_type, match=field):
        identity_from(environment)


def test_legacy_environment_cannot_be_misread_as_current_identity() -> None:
    _, error_type = image_identity_api()
    legacy = {
        "prepared_image_tag": "cae/fx-taskq-py:1.0.3",
        "prepared_image_digest": "sha256:" + "c" * 64,
    }

    with pytest.raises(error_type, match="prepared_image_repository"):
        identity_from(legacy)
