"""coding-agent-eval — a ground-truth benchmark for coding-agent defect discovery.

Version fields are deliberately separate. They move independently, and a result is
only comparable to another result when the relevant versions match, so every
`results.json` carries all of them (spec §15).
"""

from __future__ import annotations

#: Installed software/tooling release. This may advance without changing evidence.
__version__ = "0.1.1"

#: The dataset + protocol as a whole. Minor bump on new fixtures/bugs;
#: major bump on any change to matching or metric definitions.
BENCHMARK_VERSION = "0.1.0"

#: Frozen rules for finding_hash, deduplication, and blinding (spec §6.5, §8.1, §8.3).
ADJUDICATION_PROTOCOL_VERSION = "0.1.0"

#: Public trace field contract emitted by every current writer (spec §10.2).
TRACE_SCHEMA_VERSION = "0.2.0"

#: Historical traces stay readable so checked-in evidence remains replayable.
READABLE_TRACE_SCHEMA_VERSIONS = frozenset({"0.1.0", TRACE_SCHEMA_VERSION})

#: Only this contract may participate in publication evidence.
PUBLICATION_TRACE_SCHEMA_VERSION = TRACE_SCHEMA_VERSION

#: Sanitizer rule set (spec §10.5).
REDACTION_MANIFEST_VERSION = "0.1.0"

#: Context-specific hygiene policy, including the tracked-file email allowlist (spec §10.8).
HYGIENE_POLICY_VERSION = "0.1.0"

__all__ = [
    "ADJUDICATION_PROTOCOL_VERSION",
    "BENCHMARK_VERSION",
    "HYGIENE_POLICY_VERSION",
    "PUBLICATION_TRACE_SCHEMA_VERSION",
    "READABLE_TRACE_SCHEMA_VERSIONS",
    "REDACTION_MANIFEST_VERSION",
    "TRACE_SCHEMA_VERSION",
    "__version__",
]
