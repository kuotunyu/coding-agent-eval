"""Validated OCI identity for a prepared fixture image.

An OCI registry manifest and a Docker image configuration are different
objects.  Keeping both digests named prevents a local image ID from being
published as though it were a registry-pull identity.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY = re.compile(r"^ghcr\.io/kuotunyu/[a-z0-9][a-z0-9._-]*$")
_VERSION_TAG = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class ImageIdentityError(ValueError):
    """A prepared-image field cannot identify the approved OCI object."""


def _required_string(environment: Mapping[str, object], field: str) -> str:
    value = environment.get(field)
    if not isinstance(value, str):
        raise ImageIdentityError(f"{field} must be a string")
    return value


@dataclass(frozen=True)
class PreparedImageIdentity:
    """The registry and local identities observed for one prepared image."""

    repository: str
    tag: str
    manifest_digest: str
    config_digest: str

    def __post_init__(self) -> None:
        if _REPOSITORY.fullmatch(self.repository) is None:
            raise ImageIdentityError(
                "prepared_image_repository must be an unqualified ghcr.io/kuotunyu repository"
            )
        if _VERSION_TAG.fullmatch(self.tag) is None:
            raise ImageIdentityError("prepared_image_tag must be a semantic version alias")
        for field, digest in (
            ("prepared_image_manifest_digest", self.manifest_digest),
            ("prepared_image_config_digest", self.config_digest),
        ):
            if SHA256_DIGEST.fullmatch(digest) is None:
                raise ImageIdentityError(
                    f"{field} must be lowercase sha256 followed by 64 hexadecimal characters"
                )

    @property
    def immutable_ref(self) -> str:
        """Return the only image reference suitable for measurement."""
        return f"{self.repository}@{self.manifest_digest}"

    @classmethod
    def from_environment(cls, environment: Mapping[str, object]) -> PreparedImageIdentity:
        """Build a current identity without inferring either digest."""
        return cls(
            repository=_required_string(environment, "prepared_image_repository"),
            tag=_required_string(environment, "prepared_image_tag"),
            manifest_digest=_required_string(environment, "prepared_image_manifest_digest"),
            config_digest=_required_string(environment, "prepared_image_config_digest"),
        )


__all__ = ["SHA256_DIGEST", "ImageIdentityError", "PreparedImageIdentity"]
