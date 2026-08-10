"""Leak-shaped sample strings used to test the detector.

This file necessarily contains text that looks exactly like the things the
scanner exists to find. It is the one path listed in
`hygiene.policy.LEAK_CORPUS_EXCLUSIONS`, so the scanner skips it.

None of these values is real. They are synthetic strings with the right shape
and nothing behind them.

Keeping every sample here means the exclusion covers a single reviewable file
rather than being sprinkled across the suite as dozens of inline suppressions.
"""

from __future__ import annotations

#: (rule, sample). Every rule in `patterns.RULE_NAMES` needs at least two entries:
#: one sample tends to encode an accidental spelling of the rule, not the rule.
POSITIVE: list[tuple[str, str]] = [
    ("absolute_path", r"see C:\Users\someone\Desktop\project\file.txt"),
    ("absolute_path", r"path = D:/build/output"),
    ("absolute_path", r"\\?\C:\very\long\path"),
    ("absolute_path", r"\\fileserver\share\thing"),
    ("absolute_path", "/home/alice/.config/app"),
    ("absolute_path", "/Users/bob/Library/Preferences"),
    ("absolute_path", "/root/.ssh/config"),
    ("absolute_path", "/mnt/c/Users/carol/data"),
    ("email", "reach me at someone@example.com"),
    ("email", "first.last+tag@sub.domain.co.uk"),
    ("api_key", "AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"),
    ("api_key", "sk-abcdefghijklmnopqrstuvwxyz0123456789"),
    ("api_key", "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    ("api_key", "gho_ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"),
    ("private_key", "-----BEGIN RSA PRIVATE KEY-----"),
    ("private_key", "-----BEGIN OPENSSH PRIVATE KEY-----"),
    ("jwt", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NSJ9.dBjftJeZ4CVPmB92K27uhbUJU1p1r0"),
    ("jwt", "eyJ0eXAiOiJKV1QiLCJhbGciOiJub25lIn0.eyJhIjoxfQ.QWJjRGVmR2hpSktMbW5PcFFyU3Q"),
    ("dotenv_block", "API_TOKEN=abc123\nDATABASE_URL=postgres://x\n"),
    ("dotenv_block", "AWS_ACCESS_KEY_ID=AAAA\nAWS_SECRET_ACCESS_KEY=BBBB\n"),
    # A blank field between filled ones must not break the run: a real .env with
    # some settings unset is still a real .env.
    ("dotenv_block", "API_TOKEN=abc123\nUNUSED_SETTING=\nDATABASE_URL=postgres://x\n"),
]

#: Ordinary content that must not be flagged. A scanner that cries wolf gets disabled.
NEGATIVE: list[str] = [
    "relative path src/coding_agent_eval/cli.py",
    "output written to <PROJECT_ROOT>/runs/abc123",
    "the fixture is licensed MIT (SPDX-License-Identifier: MIT)",
    "See docs/METRICS.md for denominators and zero-denominator behaviour.",
    "line_tolerance is 8 by default; overlap is inclusive.",
    "A single VAR=value line is ordinary prose, not a dotenv block.",
    # A template of empty assignments is what .env.example is. It names the
    # settings an operator must choose and holds no value to leak.
    "CAE_MAX_TOKENS=\nCAE_MAX_TOOL_CALLS=\nCAE_MAX_WALLCLOCK_SECONDS=\nCAE_MAX_COST=\n",
    "ratios like 0.80 and identifiers like B-001 are not secrets",
    "sha256:0123456789abcdef is a digest, not a key",
    # Shell scripts assign uppercase variables the same way a dotenv file does.
    # A detector that fires on ordinary build scripts is one people turn off.
    'WHEEL="$(ls dist/*.whl)"\nSDIST="$(ls dist/*.tar.gz)"\n',
    "OUT=${BASE}/x\nDIR=`pwd`\n",
]

#: Addresses that must fail the tracked-file scanner despite resembling the allowlisted one.
EMAIL_NEAR_MISSES: list[str] = [
    "kuotunyu@users.noreply.github.com",
    "61350295+kuotunyu@users.noreply.github.com.evil.com",
    "61350295+kuotunyu@users.noreply.github.co",
    "x61350295+kuotunyu@users.noreply.github.com",
    "61350296+kuotunyu@users.noreply.github.com",
    "someone.else@gmail.com",
    "olduser0226@gmail.com",
]

#: A sample secret used where a test needs one value rather than the whole corpus.
SAMPLE_API_KEY = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
SAMPLE_WINDOWS_PATH = r"C:\Users\someone\notes.txt"
SAMPLE_POSIX_PATH = "/home/alice/.ssh/id_rsa"
SAMPLE_OTHER_EMAIL = "someone@example.com"

#: A different account at the same host as the allowlisted address. Proves the
#: allowlist is not domain-scoped.
SAME_HOST_OTHER_ACCOUNT = "99999999+someone@users.noreply.github.com"

# Inputs for the suppression-marker tests. These deliberately include markers that
# are malformed or absent, so the samples must not be suppressed where they are
# defined either — which is exactly why they live in this excluded file.
MARKER_RULE_SCOPED = (
    f"example /home/alice/x and {SAMPLE_API_KEY} "
    "leak-scan-allow: absolute_path (pattern documentation)"
)
MARKER_LINE_SCOPED = (
    "leak-scan-allow: absolute_path (pattern documentation) /home/alice/x\n/home/bob/y\n"
)
MARKER_WITHOUT_REASON = "/home/alice/x  leak-scan-allow: absolute_path"
MARKER_WITH_EMPTY_REASON = "/home/alice/x  leak-scan-allow: absolute_path ()"
