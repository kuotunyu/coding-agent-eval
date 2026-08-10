"""Environment fingerprint (design spec §9.4).

A result is only evidence about another result taken in the same environment.
The fingerprint makes that checkable instead of assumed: two runs whose
fingerprints differ are not comparable, however close their numbers look.

It covers the things that change behaviour and nothing else. Folding in a
timestamp or a hostname would make every run incomparable to every other, which
is indistinguishable from having no fingerprint at all — so extra keys are
ignored rather than mixed in, and a missing one raises instead of being skipped.
Silently fingerprinting seven of eight components would claim more comparability
than the data supports.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final, Literal

#: Historical v0.1 identity. Read-only until the coordinated fixture re-pin.
LEGACY_COMPONENTS: Final[tuple[str, ...]] = (
    "base_image_digest",
    "prepared_image_digest",
    "os_release_id",
    "os_release_version_id",
    "primary_runtime_version",
    "package_manager_version",
    "lock_manifest_sha256",
    "arch",
)

#: Current identity separates the registry manifest from the image config.
CURRENT_COMPONENTS: Final[tuple[str, ...]] = (
    "base_image_digest",
    "prepared_image_manifest_digest",
    "prepared_image_config_digest",
    "os_release_id",
    "os_release_version_id",
    "primary_runtime_version",
    "package_manager_version",
    "lock_manifest_sha256",
    "arch",
)

#: Compatibility name for historical callers. New code names its contract.
COMPONENTS: Final[tuple[str, ...]] = LEGACY_COMPONENTS


def environment_fingerprint(
    components: dict[str, Any],
    *,
    contract: Literal["legacy", "current"] = "legacy",
) -> str:
    """Return `sha256:<hex>` over the declared components.

    Raises `KeyError` when one is absent.
    """
    if contract == "legacy":
        names = LEGACY_COMPONENTS
    elif contract == "current":
        names = CURRENT_COMPONENTS
    else:
        raise ValueError(f"unknown environment fingerprint contract: {contract}")
    payload = {name: components[name] for name in names}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
