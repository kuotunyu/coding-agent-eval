"""Gate G4 — prove the measured tree does not contain the answers.

An agent that can read the bug manifest, the patch, or a witness file named
after the defect is not being measured on detection at all. This is the cheapest
way for a benchmark to stop meaning anything, and it is invisible in the output:
the scores simply look excellent.

Three checks, in increasing subtlety:

* forbidden paths — the authoring artefacts, which must never be materialised
* bug identifiers in content — a stray reference in a comment or test name
* claim n-grams — text lifted from the canonical claim or root cause

The n-gram check uses five words because shorter runs occur in ordinary English.
"Session token comparison happens" is a sentence someone might legitimately
write; five consecutive words from the canonical claim is not a coincidence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Directory and file names that belong to fixture authoring, never to a measured tree.
#:
#: `patches` and `env` are named as directories even though the `.patch` suffix
#: below already catches the files inside one. The suffix rule says "these files
#: do not belong here"; the name rule says "this directory does not", which also
#: covers a patch directory holding a README and an `env/` holding only a
#: Dockerfile. Both have been copied into a tree by hand during authoring.
FORBIDDEN_NAMES: frozenset[str] = frozenset(
    {
        "bugs",
        "patches",
        "witness",
        "env",
        "defects.md",
        "known_residual_defects.yaml",
        "fixture.yaml",
    }
)
FORBIDDEN_SUFFIXES: frozenset[str] = frozenset({".patch"})

NGRAM_SIZE = 5
_BINARY_SNIFF = 8192
_WORD = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class LeakFinding:
    rule: str
    path: str
    detail: str

    def render(self) -> str:
        return f"{self.path}: {self.detail} [{self.rule}]"


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _ngrams(words: list[str], size: int = NGRAM_SIZE) -> set[tuple[str, ...]]:
    if len(words) < size:
        return set()
    return {tuple(words[i : i + size]) for i in range(len(words) - size + 1)}


def _read_text(path: Path) -> str | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw[:_BINARY_SNIFF]:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def audit_measured_tree(
    tree: Path,
    *,
    bug_ids: list[str],
    claims: list[str],
) -> list[LeakFinding]:
    """Return every way `tree` gives away the answers. Empty means the gate passes."""
    findings: list[LeakFinding] = []
    forbidden_ngrams: set[tuple[str, ...]] = set()
    for claim in claims:
        forbidden_ngrams |= _ngrams(_words(claim))

    for path in sorted(tree.rglob("*"), key=lambda p: p.relative_to(tree).as_posix()):
        relative = path.relative_to(tree).as_posix()

        if path.name in FORBIDDEN_NAMES or path.suffix in FORBIDDEN_SUFFIXES:
            findings.append(
                LeakFinding(
                    rule="FORBIDDEN_PATH",
                    path=relative,
                    detail=(
                        "fixture authoring artefact must not be materialised into the measured tree"
                    ),
                )
            )
            continue

        if not path.is_file() or path.is_symlink():
            continue

        text = _read_text(path)
        if text is None:
            continue

        for bug_id in bug_ids:
            if bug_id in text:
                findings.append(
                    LeakFinding(
                        rule="BUG_ID_IN_CONTENT",
                        path=relative,
                        detail=f"contains the bug identifier {bug_id}",
                    )
                )
                break

        if forbidden_ngrams:
            overlap = _ngrams(_words(text)) & forbidden_ngrams
            if overlap:
                sample = " ".join(sorted(overlap)[0])
                findings.append(
                    LeakFinding(
                        rule="CLAIM_NGRAM_IN_CONTENT",
                        path=relative,
                        detail=(
                            f"repeats {NGRAM_SIZE} consecutive words of a "
                            f"canonical claim: {sample!r}"
                        ),
                    )
                )

    return findings
