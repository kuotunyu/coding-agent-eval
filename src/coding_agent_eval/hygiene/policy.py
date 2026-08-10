"""Context-specific hygiene policies (design spec §10.8).

Patterns are shared; policy is not.

The repository necessarily contains its own published identity — in commit
metadata, in documentation, in this file. A tracked-file scanner that rejected
every address would therefore reject the repository itself, and a rule that
fails on correct input gets switched off. So the tracked-file policy carries one
exact-literal exception, recorded and versioned here where it can be reviewed.

A run artifact has no comparable pressure: nothing legitimate puts an address in
a trace. The public-artifact policy keeps zero tolerance, and that includes the
official address.

The allowlist is matched as an exact literal, case-insensitively. Domain- or
prefix-based matching would admit `...github.com.evil.com`, which is the whole
reason allowlists are usually a bad idea.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from coding_agent_eval import HYGIENE_POLICY_VERSION
from coding_agent_eval.hygiene.patterns import Finding, scan

#: The project's published Git identity. The only address any scanner may permit.
OFFICIAL_PUBLIC_EMAIL: Final[str] = "61350295+kuotunyu@users.noreply.github.com"

#: Paths the tracked-file scanner skips entirely.
#:
#: Exactly one entry, and it exists for a structural reason: the detector's test
#: corpus has to contain strings shaped like the things being detected, or the
#: detector is untested. Those samples are synthetic and are kept in a single
#: reviewable file rather than scattered as inline suppressions.
#:
#: This is a hole in the gate, so it is deliberately narrow: one literal path,
#: matched exactly, asserted by tests to contain exactly one entry. The public
#: artifact policy has no exclusions at all.
LEAK_CORPUS_EXCLUSIONS: Final[frozenset[str]] = frozenset({"tests/hygiene/corpus.py"})


@dataclass(frozen=True)
class HygienePolicy:
    """A named application of the shared pattern set."""

    name: str
    version: str
    email_allowlist: frozenset[str]
    path_exclusions: frozenset[str] = frozenset()

    def findings(self, text: str) -> list[Finding]:
        return scan(text, email_allowlist=self.email_allowlist)

    def is_clean(self, text: str) -> bool:
        return not self.findings(text)

    def excludes(self, relative_path: str) -> bool:
        """Exact path match only; no prefix or glob semantics."""
        return relative_path in self.path_exclusions


#: Applied by gate G11 to files under version control.
TRACKED_FILE_POLICY: Final[HygienePolicy] = HygienePolicy(
    name="tracked_file",
    version=HYGIENE_POLICY_VERSION,
    email_allowlist=frozenset({OFFICIAL_PUBLIC_EMAIL}),
    path_exclusions=LEAK_CORPUS_EXCLUSIONS,
)

#: Applied by the sanitizer to anything destined for publication as a run artifact.
#: No email allowlist and no path exclusions: an artifact has no legitimate reason
#: to contain either.
PUBLIC_ARTIFACT_POLICY: Final[HygienePolicy] = HygienePolicy(
    name="public_artifact",
    version=HYGIENE_POLICY_VERSION,
    email_allowlist=frozenset(),
    path_exclusions=frozenset(),
)
