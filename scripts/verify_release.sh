#!/usr/bin/env bash
# Release verification (design spec §14.2).
#
# The unit suite cannot catch packaging defects, because a checkout always has
# the repository-root files at hand. This installs the built wheel into an empty
# environment and exercises it there, which is the only way to find out what the
# package actually contains.
set -euo pipefail

cd "$(dirname "$0")/.."
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

echo "== build =="
rm -rf dist
uv build

WHEEL="$(ls dist/*.whl)"
SDIST="$(ls dist/*.tar.gz)"

echo
echo "== archive privacy audit =="
uv run --quiet python - "$WHEEL" "$SDIST" <<'PY'
import sys, tarfile, zipfile

FORBIDDEN = (".run-store", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".env")
wheel, sdist = sys.argv[1], sys.argv[2]

names = zipfile.ZipFile(wheel).namelist()
names += tarfile.open(sdist).getnames()

bad = [n for n in names if any(f in n for f in FORBIDDEN)]
if bad:
    raise SystemExit("forbidden entries in archives:\n  " + "\n  ".join(bad))

if not any(n.endswith(".schema.json") for n in zipfile.ZipFile(wheel).namelist()):
    raise SystemExit("wheel ships no schema documents; the installed package cannot validate")

print(f"{len(names)} archive entries, no forbidden paths, schemas present")
PY

echo
echo "== isolated wheel install =="
uv venv --quiet --python 3.12 "$WORK/venv"
VENV_PY="$WORK/venv/Scripts/python.exe"
[ -x "$VENV_PY" ] || VENV_PY="$WORK/venv/bin/python"
uv pip install --quiet --python "$VENV_PY" "$WHEEL"

echo "-- CLI smoke --"
"$VENV_PY" -m coding_agent_eval --version

echo "-- schemas load from the installed package --"
"$VENV_PY" - <<'PY'
from coding_agent_eval.schemas.loader import load_schema, schema_dir, schema_names

directory = schema_dir()
names = schema_names()
assert "_schemas" in str(directory), f"resolved to {directory}, not the packaged copy"
assert len(names) == 8, names
assert "task" in names, names
load_schema("finding")
print(f"{len(names)} schemas loaded from {directory.name}/")
PY

echo
echo "== clean export install and test =="
git archive HEAD --prefix=export/ | tar -x -C "$WORK"
uv venv --quiet --python 3.12 "$WORK/exportvenv"
EXPORT_PY="$WORK/exportvenv/Scripts/python.exe"
[ -x "$EXPORT_PY" ] || EXPORT_PY="$WORK/exportvenv/bin/python"
uv pip install --quiet --python "$EXPORT_PY" "$WORK/export" pytest
(cd "$WORK/export" && "$EXPORT_PY" -m pytest -q)

echo
echo "RELEASE VERIFICATION PASS"
