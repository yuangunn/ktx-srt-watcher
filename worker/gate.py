"""Pre-install poll gate — standard library only.

Run right after checkout, before `pip install`. Decides whether this workflow
run will actually poll Korail/SRT or just be throttled/skipped. Skipped runs
exit 0 with `poll=false` so the workflow can bypass the expensive install +
poll steps, keeping the concurrency queue from backing up (which showed up as
15-min-queued → cancelled runs).

Usage:
    python -m worker.gate            # reads config.json / state.json / env
Prints `poll=true` or `poll=false` to stdout (for $GITHUB_OUTPUT) and always
exits 0. On any error it fails open (poll=true) so we never miss a real poll.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from .throttle import will_poll


def _load(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return {}


def main() -> int:
    event_name = os.environ.get("GITHUB_EVENT_NAME")
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        config = _load("config.json")
        state = _load("state.json")
        poll = will_poll(config, state, event_name, now_iso)
    except Exception as e:  # fail open — never skip a real poll on a bug
        print(f"gate error, failing open: {e}", file=sys.stderr)
        poll = True
    print(f"poll={'true' if poll else 'false'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
