"""`cae-loc` (design spec §6.10).

This counter produces the denominator of a headline metric, so it is defined in
the repository rather than delegated to an external tool: gate G3 re-counts and
compares against the manifest, and that comparison is only meaningful if the
rule is frozen and available everywhere the gate runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from coding_agent_eval.fixtures.loc import LOC_TOOL, count_loc


def write(root: Path, name: str, text: str) -> None:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_tool_identifier_is_versioned() -> None:
    assert LOC_TOOL.startswith("cae-loc ")


def test_counts_code_lines_only(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/app.py",
        "import os\n"  # 1
        "\n"  # blank
        "   \n"  # whitespace only
        "# a comment\n"  # whole-line comment
        "    # indented comment\n"
        "def main():\n"  # 2
        "    return os  # trailing comment is still code\n",  # 3
    )
    assert count_loc(tmp_path, ["src/**"], []) == 3


def test_typescript_line_and_block_comments_are_excluded(tmp_path: Path) -> None:
    write(
        tmp_path,
        "src/index.ts",
        "import fs from 'node:fs';\n"  # 1
        "// a line comment\n"
        "/*\n"
        " * a block comment\n"
        " */\n"
        "export const x = 1;\n"  # 2
        "const y = 2; /* trailing */\n",  # 3
    )
    assert count_loc(tmp_path, ["src/**"], []) == 3


def test_block_comment_opened_and_closed_on_one_line_leaves_code(tmp_path: Path) -> None:
    write(tmp_path, "src/a.ts", "const a = /* inline */ 1;\n")
    assert count_loc(tmp_path, ["src/**"], []) == 1


def test_out_of_scope_paths_are_excluded(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py", "x = 1\n")
    write(tmp_path, "tests/test_app.py", "y = 2\n")
    assert count_loc(tmp_path, ["src/**", "tests/**"], ["tests/**"]) == 1


def test_files_outside_in_scope_paths_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py", "x = 1\n")
    write(tmp_path, "docs/guide.md", "words\n")
    assert count_loc(tmp_path, ["src/**"], []) == 1


def test_binary_files_are_ignored(tmp_path: Path) -> None:
    write(tmp_path, "src/app.py", "x = 1\n")
    (tmp_path / "src" / "blob.bin").write_bytes(b"\x00\x01\x02" * 100)
    assert count_loc(tmp_path, ["src/**"], []) == 1


def test_count_is_deterministic(tmp_path: Path) -> None:
    write(tmp_path, "src/a.py", "x = 1\ny = 2\n")
    write(tmp_path, "src/b.py", "z = 3\n")
    assert count_loc(tmp_path, ["src/**"], []) == count_loc(tmp_path, ["src/**"], []) == 3


def test_unknown_extension_counts_non_blank_lines(tmp_path: Path) -> None:
    """Without a comment syntax to apply, only blank lines are excluded."""
    write(tmp_path, "src/data.txt", "alpha\n\nbeta\n")
    assert count_loc(tmp_path, ["src/**"], []) == 2


def test_empty_scope_is_rejected(tmp_path: Path) -> None:
    """A zero denominator would divide a headline metric by nothing."""
    with pytest.raises(ValueError):
        count_loc(tmp_path, [], [])
