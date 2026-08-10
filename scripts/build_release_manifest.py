"""Write the deterministic release artifact manifest."""

from __future__ import annotations

import json
from pathlib import Path

from coding_agent_eval.release_manifest import build_release_manifest


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    output = root / "release-manifest.json"
    output.write_text(
        json.dumps(build_release_manifest(root), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
