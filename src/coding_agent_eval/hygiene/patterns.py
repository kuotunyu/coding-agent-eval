"""Leak detection patterns — the single source of truth (design spec §10.5).

Both the tracked-file scanner (gate G11) and the public-artifact sanitizer draw
their rules from here. Two independent copies of these regexes would drift, and
the copy that drifted would be the one guarding the artifact nobody re-read.

What differs between the two consumers is *policy*, not patterns; see
`policy.py` and spec §10.8.

Findings never carry the matched text. These are surfaced in CI logs, which are
themselves published, so echoing a detected secret would defeat the detection.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_WINDOWS_DRIVE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?=[^\s\\/])")
_WINDOWS_EXTENDED = re.compile(r"\\\\\?\\")  # leak-scan-allow: absolute_path (pattern definition)
_WINDOWS_UNC = re.compile(r"\\\\[A-Za-z0-9._-]+\\[A-Za-z0-9._$-]+")
_POSIX_HOME = re.compile(r"(?<![\w.-])/(?:home|Users|root)/[A-Za-z0-9._-]+")
_WSL_MOUNT = re.compile(r"(?<![\w.-])/mnt/[a-z]/(?:Users|home)/[A-Za-z0-9._-]+")

_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Vendor-shaped credentials. Length floors keep ordinary prose from matching.
_API_KEY = re.compile(
    r"(?:AIza[0-9A-Za-z_-]{30,}"
    r"|sk-[A-Za-z0-9_-]{20,}"
    r"|gh[pousr]_[A-Za-z0-9]{20,})"
)
_PRIVATE_KEY = re.compile(r"-----BEGIN [A-Z][A-Z ]* ?PRIVATE KEY-----")
# The payload segment can be short for a minimal claim set, so only the header
# segment carries a meaningful length floor.
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{10,}\b")

# A dotenv file, not a single assignment: two or more consecutive KEY=value lines.
#
# Shell scripts assign uppercase variables the same way, so a value containing
# command substitution or expansion is excluded. Without that, every build script
# with two consecutive assignments trips the rule, and a detector that fires on
# ordinary code is a detector people turn off.
_DOTENV_LINE = re.compile(r"^[A-Z][A-Z0-9_]{2,}=(?![^\n]*(?:\$\(|\$\{|`))[^\n]*$")

#: An assignment that actually carries a value.
#:
#: An empty one — `API_KEY=` — is a template, and a file of nothing but those
#: holds no secret; `.env.example` is exactly that file. Empty assignments still
#: continue a run rather than breaking it, so a real `.env` with some fields
#: left blank is still caught by the ones that are filled. They just do not
#: count toward the run on their own.
_DOTENV_VALUED = re.compile(r"^[A-Z][A-Z0-9_]{2,}=(?![^\n]*(?:\$\(|\$\{|`))\s*\S[^\n]*$")

RULE_NAMES: Final[tuple[str, ...]] = (
    "absolute_path",
    "email",
    "api_key",
    "private_key",
    "jwt",
    "dotenv_block",
)

_LINE_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("absolute_path", _WINDOWS_DRIVE),
    ("absolute_path", _WINDOWS_EXTENDED),
    ("absolute_path", _WINDOWS_UNC),
    ("absolute_path", _POSIX_HOME),
    ("absolute_path", _WSL_MOUNT),
    ("email", _EMAIL),
    ("api_key", _API_KEY),
    ("private_key", _PRIVATE_KEY),
    ("jwt", _JWT),
)

_DOTENV_RUN_LENGTH: Final[int] = 2

# Rule-scoped, line-scoped suppression.
#
# Some files must quote the very patterns being detected: the design spec lists
# what the sanitizer rejects, and this module's own docstrings show examples.
# Without an escape hatch the gate would fail on its own documentation, and a
# gate that fails on correct input gets disabled.
#
# The marker is deliberately narrow and auditable: it suppresses one named rule
# on one line, it must state a reason, and every use is greppable.
#
#     ... prose containing an example ...   leak-scan-allow: absolute_path (pattern doc)
_SUPPRESSION = re.compile(r"leak-scan-allow:\s*([a-z_]+)\s*\(([^)]{3,})\)")


@dataclass(frozen=True)
class Finding:
    """A detected leak. `excerpt` is a redacted hint, never the matched value."""

    rule: str
    line: int
    excerpt: str
    path: str = ""

    def render(self) -> str:
        where = f"{self.path}:{self.line}" if self.path else f"line {self.line}"
        return f"{where}: {self.rule} ({self.excerpt})"


def _excerpt(rule: str, match: str) -> str:
    """A stable, non-revealing description of what was found."""
    return f"{rule} match, {len(match)} chars"


def scan(text: str, *, email_allowlist: frozenset[str] = frozenset()) -> list[Finding]:
    """Return every leak finding in `text`, ordered by line then rule.

    `email_allowlist` holds exact, case-insensitive literals. It exists solely so
    the tracked-file policy can permit the repository's own published identity;
    the public-artifact policy passes an empty set.
    """
    allowed = {value.casefold() for value in email_allowlist}
    findings: list[Finding] = []
    lines = text.splitlines()

    for index, line in enumerate(lines, start=1):
        suppressed = {m.group(1) for m in _SUPPRESSION.finditer(line)}
        for rule, pattern in _LINE_RULES:
            if rule in suppressed:
                continue
            for match in pattern.finditer(line):
                value = match.group(0)
                if rule == "email" and value.casefold() in allowed:
                    continue
                findings.append(Finding(rule=rule, line=index, excerpt=_excerpt(rule, value)))

    findings.extend(_scan_dotenv_blocks(lines))
    findings.sort(key=lambda f: (f.line, f.rule))
    return findings


def _scan_dotenv_blocks(lines: list[str]) -> list[Finding]:
    findings: list[Finding] = []
    run_start = 0
    run_length = 0
    for index, line in enumerate(lines, start=1):
        if _DOTENV_LINE.match(line):
            if not _DOTENV_VALUED.match(line):
                continue  # Part of the block, but carries nothing.
            if run_length == 0:
                run_start = index
            run_length += 1
            continue
        if run_length >= _DOTENV_RUN_LENGTH:
            findings.append(
                Finding(
                    rule="dotenv_block",
                    line=run_start,
                    excerpt=f"dotenv_block match, {run_length} lines",
                )
            )
        run_length = 0
    if run_length >= _DOTENV_RUN_LENGTH:
        findings.append(
            Finding(
                rule="dotenv_block",
                line=run_start,
                excerpt=f"dotenv_block match, {run_length} lines",
            )
        )
    return findings
