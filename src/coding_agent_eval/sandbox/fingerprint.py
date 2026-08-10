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
from typing import Any, Final

#: Everything that defines the measured environment.
COMPONENTS: Final[tuple[str, ...]] = (
    "base_image_digest",
    "prepared_image_digest",
    "os_release_id",
    "os_release_version_id",
    "primary_runtime_version",
    "package_manager_version",
    "lock_manifest_sha256",
    "arch",
)


def environment_fingerprint(components: dict[str, Any]) -> str:
    """Return `sha256:<hex>` over the declared components.

    Raises `KeyError` when one is absent.
    """
    payload = {name: components[name] for name in COMPONENTS}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(encoded.encode('utf-8')).hexdigest()}"
