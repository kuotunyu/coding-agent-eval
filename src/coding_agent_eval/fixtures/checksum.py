"""Canonical tree checksum (design spec §6.7).

Computed over committed bytes with no normalisation of any kind. The direct
consequence is that changing LF to CRLF changes the checksum, which is correct:
those are different bytes and therefore a different tree.

Cross-platform stability is provided by checkout policy (`fixtures/** -text`),
not by the checksum. Normalising here instead would let two genuinely different
trees share an identity, which is a worse failure than a platform-dependent
value: it would let a fixture change without the version noticing.

Only the executable bit of the mode is included. Everything else differs between
POSIX and Windows for reasons that have nothing to do with fixture content.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

_EXECUTABLE = "100755"
_REGULAR = "100644"
_READ_CHUNK = 1 << 20


def _mode_bits(path: Path) -> str:
    return _EXECUTABLE if path.stat().st_mode & 0o111 else _REGULAR


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_READ_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def iter_tree_files(root: Path) -> list[Path]:
    """Every regular file under `root`, sorted by POSIX-relative path.

    Sorting is what makes the checksum independent of filesystem iteration
    order, which differs between platforms and between runs.
    """
    files = [p for p in root.rglob("*") if p.is_file() and not p.is_symlink()]
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


def file_digests(root: Path) -> dict[str, str]:
    """Each file's content digest, keyed by POSIX-relative path.

    The checksum is one value, so a mismatch says only that two trees differ.
    Gate G3 uses this to say *which* files differ, which is the difference
    between a report someone can act on and one they have to reproduce by hand.
    """
    return {path.relative_to(root).as_posix(): _file_digest(path) for path in iter_tree_files(root)}


def tree_checksum(root: Path) -> str:
    """Return `sha256:<hex>` over the tree's paths, executable bits, and contents."""
    if not root.is_dir():
        raise FileNotFoundError(f"not a directory: {root}")

    digest = hashlib.sha256()
    for path in iter_tree_files(root):
        relative = path.relative_to(root).as_posix()
        line = f"{relative}\0{_mode_bits(path)}\0{_file_digest(path)}\n"
        digest.update(line.encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"
