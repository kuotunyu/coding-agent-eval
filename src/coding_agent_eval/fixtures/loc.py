"""In-repository line counter (design spec §6.10).

This produces the denominator of `benchmark_unsupported_findings_per_kloc`, so
gate G3 re-counts it and compares against the manifest. That comparison only
works if the rule is frozen and present wherever the gate runs, which is why the
counter lives here rather than being delegated to an external tool that would
have to be installed, pinned, and trusted to keep counting the same way.

The rule is deliberately plain: a line counts when it has content that is not a
whole-line comment. Precision beyond that would invite arguments the metric does
not need — what matters is that the number is reproducible, not that it settles
what a line of code truly is.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

LOC_TOOL = "cae-loc 0.1.0"

_BINARY_SNIFF = 8192

#: Whole-line comment prefixes, by file extension.
_LINE_COMMENT: dict[str, tuple[str, ...]] = {
    ".py": ("#",),
    ".pyi": ("#",),
    ".ts": ("//",),
    ".tsx": ("//",),
    ".js": ("//",),
    ".mjs": ("//",),
    ".go": ("//",),
}

#: Extensions using C-style block comments.
_BLOCK_COMMENT: frozenset[str] = frozenset({".ts", ".tsx", ".js", ".mjs", ".go"})

_BLOCK_INLINE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _matches_any(relative: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) for pattern in patterns)


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


def count_file_loc(text: str, suffix: str) -> int:
    """Count content lines in one file's text."""
    if suffix in _BLOCK_COMMENT:
        # Collapse inline block comments first so `const a = /* x */ 1;` still counts,
        # then drop lines that consist only of a block-comment body.
        text = _BLOCK_INLINE.sub(" ", text)

    prefixes = _LINE_COMMENT.get(suffix, ())
    in_block = False
    count = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()

        if suffix in _BLOCK_COMMENT:
            if in_block:
                if "*/" in line:
                    in_block = False
                    line = line.split("*/", 1)[1].strip()
                else:
                    continue
            if "/*" in line:
                before = line.split("/*", 1)[0].strip()
                in_block = "*/" not in line.split("/*", 1)[1]
                line = before

        if not line:
            continue
        if prefixes and line.startswith(prefixes):
            continue
        count += 1

    return count


def count_loc(root: Path, in_scope_paths: list[str], out_of_scope_paths: list[str]) -> int:
    """Count content lines under `root` for the in-scope, non-excluded files."""
    if not in_scope_paths:
        raise ValueError(
            "in_scope_paths is empty; the resulting zero would divide a headline metric"
        )

    total = 0
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        if not _matches_any(relative, in_scope_paths):
            continue
        if _matches_any(relative, out_of_scope_paths):
            continue
        text = _read_text(path)
        if text is None:
            continue
        total += count_file_loc(text, path.suffix)
    return total
