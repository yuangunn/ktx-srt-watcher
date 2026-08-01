from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterable

DEFAULT_STATE: dict[str, Any] = {"last_run": None, "watches": {}}


def read_state(path: Path | str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return _default()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return _default()


def write_state(path: Path | str, state: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def get_notified_ids(state: dict[str, Any], watch_id: str) -> set[str]:
    return set(state.get("watches", {}).get(watch_id, {}).get("notified_train_ids", []))


def add_notified_ids(state: dict[str, Any], watch_id: str, train_ids: Iterable[str]) -> None:
    entry = _entry(state, watch_id)
    merged = set(entry.get("notified_train_ids", []))
    merged.update(train_ids)
    entry["notified_train_ids"] = sorted(merged)


def mark_run(state: dict[str, Any], when: str) -> None:
    state["last_run"] = when


# Keep at most this many poll-history entries (≈ a month of real polls at
# a 15-min cadence is ~2900, but stats only need 30 days; cap generously).
POLL_HISTORY_MAX = 2000


def record_poll(state: dict[str, Any], when: str, seats_found: int) -> None:
    """Append one *actual* poll to poll_history.

    Only non-skipped runs call this, so poll_history reflects real Korail/SRT
    polls (not GHA trigger ticks). The PWA stats read this instead of counting
    workflow runs, so throttled/skip runs never inflate the numbers.
    Each entry: {"t": ISO8601, "seats": int}. Capped at POLL_HISTORY_MAX.
    """
    hist = state.setdefault("poll_history", [])
    hist.append({"t": when, "seats": int(seats_found)})
    if len(hist) > POLL_HISTORY_MAX:
        del hist[: len(hist) - POLL_HISTORY_MAX]


def is_auto_reserve_disabled(state: dict[str, Any], watch_id: str) -> bool:
    """True if auto-reserve was self-disabled for this watch after a success.

    The worker never writes config (the PWA owns it), so a one-shot
    auto-reserve is remembered in state instead. The PWA still shows the
    config's auto_reserve flag; the user re-enables from the app when they want
    another reservation.
    """
    return watch_id in (state.get("auto_reserve_disabled") or [])


def disable_auto_reserve(state: dict[str, Any], watch_id: str) -> None:
    lst = state.setdefault("auto_reserve_disabled", [])
    if watch_id not in lst:
        lst.append(watch_id)


def enable_auto_reserve(state: dict[str, Any], watch_id: str) -> None:
    """Re-arm auto-reserve (hold expired unpaid → the seat is back in the pool)."""
    lst = state.get("auto_reserve_disabled")
    if lst and watch_id in lst:
        lst.remove(watch_id)


def set_pending_reservation(
    state: dict[str, Any], watch_id: str, reservation_id: str, deadline_iso: str | None,
) -> None:
    """Remember the hold we just placed so a later run can judge its outcome.

    Cleared when the reservation is confirmed paid (job done) or when the hold
    expires unpaid (auto-reserve re-arms).
    """
    state.setdefault("pending_reservations", {})[watch_id] = {
        "id": reservation_id,
        "deadline": deadline_iso,
    }


def get_pending_reservation(state: dict[str, Any], watch_id: str) -> dict[str, Any] | None:
    return (state.get("pending_reservations") or {}).get(watch_id)


def clear_pending_reservation(state: dict[str, Any], watch_id: str) -> None:
    pend = state.get("pending_reservations")
    if pend and watch_id in pend:
        del pend[watch_id]


# Re-alert about a still-broken login once a day. Alerting every poll would be
# ~48 messages/day and get muted; alerting only once means a failure noticed at
# 3am and dismissed half-asleep is never raised again.
LOGIN_ALERT_REPEAT_HOURS = 24


def record_login_failure(state: dict[str, Any], provider: str, when: str) -> bool:
    """Remember that a provider login failed. True if the caller should alert.

    A dead login is the quietest possible failure: the run still succeeds, the
    state timestamp still advances, so neither the heartbeat nor the health card
    notices — the watcher simply stops checking that provider.
    """
    failures = state.setdefault("login_failures", {})
    entry = failures.get(provider)
    if entry is None:
        failures[provider] = {"since": when, "notified_at": when}
        return True
    last = _parse_iso(entry.get("notified_at"))
    now = _parse_iso(when)
    if last is None or now is None:
        return False
    if (now - last).total_seconds() >= LOGIN_ALERT_REPEAT_HOURS * 3600:
        entry["notified_at"] = when
        return True
    return False


def clear_login_failure(state: dict[str, Any], provider: str) -> bool:
    """Called on a successful login. True if it had been failing, so the caller
    can say it is back — otherwise the user is left wondering."""
    failures = state.get("login_failures") or {}
    if provider in failures:
        del failures[provider]
        return True
    return False


def _parse_iso(value: str | None):
    from datetime import datetime
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def mark_check(state: dict[str, Any], watch_id: str, when: str) -> None:
    _entry(state, watch_id)["last_check"] = when


def set_watch_date(state: dict[str, Any], watch_id: str, watch_date: str) -> None:
    _entry(state, watch_id)["watch_date"] = watch_date


def prune_past_dates(state: dict[str, Any], today: str) -> None:
    watches = state.get("watches", {})
    expired = [wid for wid, entry in watches.items() if entry.get("watch_date") and entry["watch_date"] < today]
    for wid in expired:
        del watches[wid]
    # Drop stale auto-reserve-disable flags for watches that no longer exist.
    disabled = state.get("auto_reserve_disabled")
    if disabled:
        state["auto_reserve_disabled"] = [w for w in disabled if w in watches]


def prune_orphan_flags(state: dict[str, Any], config_watch_ids: Iterable[str]) -> None:
    """Drop per-watch flags whose watch is no longer in config.json.

    Editing a watch in the PWA mints a fresh id, so the old id lingers in
    state until its travel date passes — carrying a disable flag that matches
    nothing.  Keying the cleanup on config (not on state["watches"]) clears it
    immediately.  Pass *every* config watch id, active or not: deactivating a
    watch must not silently re-arm its auto-reserve.
    """
    known = set(config_watch_ids)
    disabled = state.get("auto_reserve_disabled")
    if disabled:
        state["auto_reserve_disabled"] = [w for w in disabled if w in known]
    pending = state.get("pending_reservations")
    if pending:
        for wid in [w for w in pending if w not in known]:
            del pending[wid]


def _entry(state: dict[str, Any], watch_id: str) -> dict[str, Any]:
    watches = state.setdefault("watches", {})
    return watches.setdefault(watch_id, {"last_check": None, "notified_train_ids": []})


def _default() -> dict[str, Any]:
    return {"last_run": None, "watches": {}}


def merge_states(ours: dict[str, Any], remote: dict[str, Any]) -> dict[str, Any]:
    """Three-way semantic merge of two state.json snapshots.

    Used by the workflow's commit step to resolve the situation where two
    runs (e.g. CF cron + GHA fallback, or back-to-back ticks) both committed
    state.json before either could push.  git's line-merge can't reconcile
    different edits to the same JSON keys, so we do it here:

      - last_run: max (most recent timestamp wins)
      - watches keys: union (both runs' watch IDs survive)
      - per watch:
        - last_check: max
        - notified_train_ids: union (sorted, deduped)
        - watch_date: prefer non-empty (ours falls back to remote)

    The merge is symmetric and idempotent on identical inputs.
    """
    result: dict[str, Any] = {}

    result["last_run"] = _later(ours.get("last_run"), remote.get("last_run"))

    o_watches = ours.get("watches") or {}
    r_watches = remote.get("watches") or {}
    merged_watches: dict[str, dict[str, Any]] = {}
    for wid in set(o_watches) | set(r_watches):
        o_w = o_watches.get(wid) or {}
        r_w = r_watches.get(wid) or {}
        merged_watches[wid] = {
            "last_check": _later(o_w.get("last_check"), r_w.get("last_check")),
            "notified_train_ids": sorted(
                set(o_w.get("notified_train_ids") or [])
                | set(r_w.get("notified_train_ids") or [])
            ),
        }
        watch_date = o_w.get("watch_date") or r_w.get("watch_date")
        if watch_date:
            merged_watches[wid]["watch_date"] = watch_date
    result["watches"] = merged_watches

    # poll_history: union by timestamp, chronologically sorted, capped.
    # Concurrent runs may each append; dedupe on (t, seats) so a double-push
    # doesn't count a poll twice.
    o_hist = ours.get("poll_history") or []
    r_hist = remote.get("poll_history") or []
    seen: set[tuple[str, int]] = set()
    merged_hist: list[dict[str, Any]] = []
    for e in sorted(o_hist + r_hist, key=lambda x: x.get("t") or ""):
        key = (e.get("t") or "", int(e.get("seats") or 0))
        if key in seen:
            continue
        seen.add(key)
        merged_hist.append({"t": e.get("t"), "seats": int(e.get("seats") or 0)})
    if merged_hist:
        result["poll_history"] = merged_hist[-POLL_HISTORY_MAX:]

    # auto_reserve_disabled: union (a disable on either side sticks).
    # auto_reserve_disabled: ours wins. A concurrent run may have re-armed a
    # watch (hold expired) — union would resurrect the stale disable and leave
    # auto-reserve dead, so prefer this run's view.
    disabled = ours.get("auto_reserve_disabled")
    if disabled is None:
        disabled = remote.get("auto_reserve_disabled")
    if disabled:
        result["auto_reserve_disabled"] = sorted(set(disabled))

    # pending_reservations: per-watch, ours wins (it reflects the newer poll).
    pend = {**(remote.get("pending_reservations") or {}), **(ours.get("pending_reservations") or {})}
    if pend:
        result["pending_reservations"] = pend

    # Carry over any top-level keys we don't know about (forward compat)
    for k, v in {**remote, **ours}.items():
        if k not in result:
            result[k] = v

    return result


def _later(a: str | None, b: str | None) -> str | None:
    if not a:
        return b
    if not b:
        return a
    return a if a >= b else b


def push_to_kv_mirror(state_path: Path | str) -> None:
    """Best-effort PUT of state.json to the CF Worker /state endpoint.

    PWA reads from there before falling back to the GitHub Contents API,
    saving ~300ms per page open and avoiding repeated API quota use.
    Failure is silent — the canonical state still lives in git, and the
    PWA fallback handles missing/stale mirror data.
    """
    cf_url = (os.environ.get("CF_WORKER_URL") or "").rstrip("/")
    token = os.environ.get("REMINDER_TOKEN") or ""
    if not cf_url or not token:
        return
    try:
        body = Path(state_path).read_bytes()
        import urllib.request
        req = urllib.request.Request(
            f"{cf_url}/state",
            data=body,
            method="PUT",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "ktx-srt-watcher",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 202):
                # Log via stderr; this is a mirror, never critical
                print(f"state KV mirror push: HTTP {resp.status}", file=sys.stderr)
    except Exception as e:
        print(f"state KV mirror push failed: {e}", file=sys.stderr)


if __name__ == "__main__":
    # CLI:
    #   python -m worker.state merge OUR_STATE REMOTE_STATE
    #     Three-way merge for the workflow's concurrent-push race fix.
    #   python -m worker.state push-mirror STATE_PATH
    #     Best-effort PUT of state.json to CF Worker /state endpoint
    #     (PWA fast-read mirror).  Reads CF_WORKER_URL + REMINDER_TOKEN
    #     from env; silent no-op when either is missing.

    def _fail(msg: str, code: int = 2) -> None:
        print(msg, file=sys.stderr)
        sys.exit(code)

    if len(sys.argv) < 2:
        _fail("usage: python -m worker.state {merge|push-mirror} ARGS")

    cmd = sys.argv[1]
    if cmd == "merge":
        if len(sys.argv) != 4:
            _fail("usage: python -m worker.state merge OUR_STATE REMOTE_STATE")
        ours_path = Path(sys.argv[2])
        remote_path = Path(sys.argv[3])
        if not ours_path.exists() or not remote_path.exists():
            _fail(f"missing state file: ours={ours_path.exists()}, remote={remote_path.exists()}")
        ours = json.loads(ours_path.read_text(encoding="utf-8"))
        remote = json.loads(remote_path.read_text(encoding="utf-8"))
        merged = merge_states(ours, remote)
        remote_path.write_text(
            json.dumps(merged, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"merged into {remote_path} ({len(merged.get('watches', {}))} watches)")
    elif cmd == "push-mirror":
        if len(sys.argv) != 3:
            _fail("usage: python -m worker.state push-mirror STATE_PATH")
        push_to_kv_mirror(sys.argv[2])
        print("pushed to KV mirror (or silently skipped)")
    else:
        _fail(f"unknown command: {cmd}")
