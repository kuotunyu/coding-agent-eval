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

echo "== offline publication audit =="
uv run cae release audit --publication

echo "== build =="
BUILD="$WORK/dist"
mkdir -p "$BUILD"
uv build --out-dir "$BUILD"

WHEEL="$(ls "$BUILD"/*.whl)"
SDIST="$(ls "$BUILD"/*.tar.gz)"

echo
echo "== archive privacy audit =="
uv run --quiet python - "$WHEEL" "$SDIST" <<'PY'
import sys, tarfile, zipfile
from email.parser import BytesParser

FORBIDDEN = (".run-store", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", ".env")
wheel, sdist = sys.argv[1], sys.argv[2]

with zipfile.ZipFile(wheel) as wheel_archive:
    wheel_names = wheel_archive.namelist()
    metadata_names = [name for name in wheel_names if name.endswith(".dist-info/METADATA")]
    if len(metadata_names) != 1:
        raise SystemExit(f"wheel has {len(metadata_names)} METADATA files; expected one")
    metadata = BytesParser().parsebytes(wheel_archive.read(metadata_names[0]))
with tarfile.open(sdist) as sdist_archive:
    sdist_names = sdist_archive.getnames()

names = wheel_names + sdist_names

bad = [n for n in names if any(f in n for f in FORBIDDEN)]
if bad:
    raise SystemExit("forbidden entries in archives:\n  " + "\n  ".join(bad))

internal = [name for name in sdist_names if "/docs/superpowers/" in f"/{name}"]
if internal:
    raise SystemExit("internal design records in sdist:\n  " + "\n  ".join(internal))

if not any(name.endswith(".schema.json") for name in wheel_names):
    raise SystemExit("wheel ships no schema documents; the installed package cannot validate")

if metadata["Version"] != "0.1.1":
    raise SystemExit(f"wheel version is {metadata['Version']!r}, expected '0.1.1'")

required_urls = {"Repository", "Documentation", "Issues", "Releases"}
url_labels = {
    value.split(",", 1)[0].strip() for value in metadata.get_all("Project-URL", [])
}
missing_urls = sorted(required_urls - url_labels)
if missing_urls:
    raise SystemExit("wheel is missing Project-URL labels: " + ", ".join(missing_urls))

keywords = {value.strip() for value in (metadata["Keywords"] or "").split(",")}
required_keywords = {
    "benchmark", "coding-agents", "developer-tools", "llm-evaluation", "reproducibility"
}
missing_keywords = sorted(required_keywords - keywords)
if missing_keywords:
    raise SystemExit("wheel is missing keywords: " + ", ".join(missing_keywords))

required_classifiers = {
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "License :: OSI Approved :: MIT License",
    "Operating System :: OS Independent",
    "Topic :: Scientific/Engineering :: Artificial Intelligence",
}
missing_classifiers = sorted(required_classifiers - set(metadata.get_all("Classifier", [])))
if missing_classifiers:
    raise SystemExit("wheel is missing classifiers: " + ", ".join(missing_classifiers))

print(
    f"{len(names)} archive entries, no forbidden/internal paths, schemas present; "
    f"wheel metadata has {len(url_labels)} project URLs"
)
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
required = {"finding", "review-set", "suite-registration", "task", "trace-record"}
assert required <= set(names), names
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
