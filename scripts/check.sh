#!/usr/bin/env bash
# Fast non-Docker checks. Run this directly, never through a pipe: a pipeline
# reports the last stage's status, which would hide a failure here. Docker-backed
# witness, sandbox, and isolated E2E gates run separately in CI.
#
# G3 rebuilds each fixture tree from HEAD, so it judges the *committed* bytes.
# Editing a fixture tree therefore fails it until the change is committed and
# the manifest's checksum updated — which is the gate working, not a false
# alarm: a fixture whose identity is unrecorded cannot be measured against.
#
# mypy runs twice, for the host and for Linux: typeshed hides Windows-only
# names behind sys.platform, so a Windows-only run cannot see Linux errors.
set -euo pipefail
uv run --quiet ruff check .
uv run --quiet ruff format --check .
uv run --quiet mypy
uv run --quiet mypy --platform linux
uv run --quiet python -X utf8 -m pytest -q
uv run --quiet cae fixture rebuild fixtures
uv run --quiet cae hygiene leak-scan --tracked
uv run --quiet cae release audit
echo "NON-DOCKER CHECKS PASS"
