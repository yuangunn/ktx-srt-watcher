"""Log-safe handles. Actions logs on this public repo are world-readable."""
from __future__ import annotations

import hashlib


def wid(watch_id: str) -> str:
    """A log handle for a watch — never the watch id itself.

    Watch ids read like "부산-동탄-20260817-wpvm": origin, destination and
    travel date in the clear. Actions logs on a public repo are readable by
    anyone without signing in, so printing the id publishes exactly what moving
    config out of git into CF KV was meant to hide — when the user's home is
    empty.

    The trailing random token is enough to line a log line up with the app and
    discloses nothing by itself. Ids in another shape fall back to a digest.
    """
    raw = (watch_id or "").strip()
    if not raw:
        return "?"
    tail = raw.rsplit("-", 1)[-1]
    if tail != raw and tail.isalnum() and not tail.isdigit():
        return tail
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
