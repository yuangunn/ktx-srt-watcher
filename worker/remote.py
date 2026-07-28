"""Config/state I/O against the Cloudflare Worker KV store.

config.json and state.json used to be committed to this public repo, where
the watch ids ("서울-대전-20260101-a1b2") published the user's travel plans.
They now live in CF KV and are reached over HTTP with REMINDER_TOKEN.

Stdlib only: worker.gate imports this to decide whether to poll *before* the
workflow installs pip dependencies.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

TIMEOUT_SEC = 10

# Cloudflare's bot protection answers 403 to the default "Python-urllib/x.y"
# User-Agent before the request ever reaches the Worker, so send our own.
# (This silently broke the old state mirror push for weeks.)
USER_AGENT = "ktx-srt-watcher"


class RemoteError(RuntimeError):
    """Config/state could not be reached or was unusable."""


def _base_url() -> str:
    url = (os.environ.get("CF_WORKER_URL") or "").rstrip("/")
    if not url:
        raise RemoteError("CF_WORKER_URL is not set")
    return url


def _token() -> str:
    token = os.environ.get("REMINDER_TOKEN") or ""
    if not token:
        raise RemoteError("REMINDER_TOKEN is not set")
    return token


def _request(path: str, *, method: str = "GET", body: bytes | None = None) -> bytes:
    req = urllib.request.Request(
        f"{_base_url()}{path}",
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RemoteError(f"{method} {path} → HTTP {e.code}") from e
    except Exception as e:  # URLError, socket timeout, DNS, ...
        raise RemoteError(f"{method} {path} → {e}") from e


def fetch_config() -> dict[str, Any] | None:
    """Load the watch list.

    Returns None when nothing is stored yet (404) — a fresh install with no
    watches is a normal state, not a failure, and treating it as one turns
    every 5-minute tick into a red run.  Any other problem (CF down, bad
    token, malformed body) still raises: those mean we *cannot tell* whether
    there is anything to poll, which is worth failing over.
    """
    try:
        raw = _request("/config")
    except RemoteError as e:
        if "HTTP 404" in str(e):
            return None
        raise
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise RemoteError(f"config is not valid JSON: {e}") from e


def fetch_state() -> dict[str, Any]:
    """Load poll state.  A missing state is normal on a fresh install, so
    404 yields the empty default instead of raising."""
    try:
        raw = _request("/state")
    except RemoteError as e:
        if "HTTP 404" in str(e):
            return {"last_run": None, "watches": {}}
        raise
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        # Corrupt state costs us dedup history, not correctness: worst case a
        # seat is re-notified. Losing the run entirely would be worse.
        return {"last_run": None, "watches": {}}


def push_state(state: dict[str, Any]) -> None:
    body = json.dumps(state, ensure_ascii=False).encode("utf-8")
    _request("/state", method="PUT", body=body)
