"""Allow `python -m coding_agent_eval` so the CLI is testable without an installed script."""

from __future__ import annotations

from coding_agent_eval.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
