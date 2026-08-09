"""In-memory poll state: what we have already notified about, what is
pending, which logins are broken.

State is a plain dict.  Reading and writing it is remote.py's job — it lives
in CF KV, not on disk, so nothing here touches the filesystem.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


def empty_state() -> dict[str, Any]:
    """A fresh state.  Returned as a new dict every call — callers mutate it."""
    return {"last_run": None, "watches": {}}


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


def _parse_iso(value: str | None) -> datetime | None:
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

