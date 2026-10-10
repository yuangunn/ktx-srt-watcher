"""Poll-throttle decision logic — standard library only.

Kept dependency-free (no pydantic / korail2 / SRT) so the GHA workflow can
decide *before* the expensive `pip install` whether a run will actually poll
or just be throttled/skipped. See worker/gate.py and the workflow's gate step.

worker.main re-exports these so there is a single source of truth.
"""
from __future__ import annotations

import random
from datetime import date, datetime, timedelta, timezone
from typing import Any

# Hard floor for any user-set poll interval (anti-bot / server load).
MIN_POLL_INTERVAL_MIN = 10

# Events that obey the throttle (automated cadence). "push"
# is included so PWA setting changes don't force off-cadence polls; only manual
# "지금 확인" (workflow_dispatch) bypasses.
AUTOMATED_EVENTS = ("schedule", "repository_dispatch", "push")

# Imminent departures: cancellations cluster in the last days before a train
# leaves (refund fees step up, plans firm up), so when any active watch
# departs within URGENT_DAYS_DEFAULT days the cadence tightens to a random
# URGENT_RANGE_MIN gap. It still respects the 10-minute floor, and it never
# slows down a user who already polls faster than this.
URGENT_DAYS_DEFAULT = 2
URGENT_RANGE_MIN = (MIN_POLL_INTERVAL_MIN, 15)
KST = timezone(timedelta(hours=9))


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, TypeError, AttributeError):
        return None


def roll_interval_min(mode: str, poll_interval_min: int, settings: dict[str, Any]) -> int:
    """Pick the next poll interval in minutes. 0 means 'no throttle'."""
    if mode == "range":
        rng = settings.get("poll_interval_range") or []
        if len(rng) == 2:
            try:
                lo, hi = int(rng[0]), int(rng[1])
            except (TypeError, ValueError):
                lo = hi = 0
            lo, hi = min(lo, hi), max(lo, hi)
            lo = max(MIN_POLL_INTERVAL_MIN, lo)
            hi = max(lo, hi)
            return random.randint(lo, hi)
    elif mode == "choices":
        choices = []
        for c in settings.get("poll_interval_choices") or []:
            try:
                n = int(c)
            except (TypeError, ValueError):
                continue
            if n >= MIN_POLL_INTERVAL_MIN:
                choices.append(n)
        if choices:
            return random.choice(choices)
    # fixed mode, or a malformed randomized config: fall back to fixed value
    if poll_interval_min and poll_interval_min < MIN_POLL_INTERVAL_MIN:
        return MIN_POLL_INTERVAL_MIN
    return poll_interval_min


def should_throttle(
    mode: str, poll_interval_min: int, settings: dict[str, Any],
    state: dict[str, Any], now_iso: str,
) -> bool:
    """True if this automated run should skip because we polled too recently."""
    now_dt = parse_dt(now_iso)
    if now_dt is None:
        return False
    if mode in ("range", "choices"):
        # Randomized cadence: the target for this gap was rolled and stored
        # as next_poll_at the last time we actually polled. Skip until then.
        next_dt = parse_dt(state.get("next_poll_at"))
        if next_dt is not None and now_dt < next_dt:
            return True
        return False
    # fixed mode: epoch-bucket throttle (driftless, aligned to wall clock)
    if poll_interval_min <= 0:
        return False
    last_dt = parse_dt(state.get("last_run"))
    if last_dt is None:
        return False
    bucket = poll_interval_min * 60
    if int(last_dt.timestamp()) // bucket == int(now_dt.timestamp()) // bucket:
        return True
    return False


def set_next_poll(mode: str, settings: dict[str, Any], state: dict[str, Any], now_iso: str) -> None:
    """For randomized modes, roll the next interval and stash next_poll_at."""
    if mode not in ("range", "choices"):
        state.pop("next_poll_at", None)
        return
    poll_interval_min = int(settings.get("poll_interval_min") or 0)
    rolled = roll_interval_min(mode, poll_interval_min, settings)
    now_dt = parse_dt(now_iso)
    if rolled and now_dt is not None:
        nxt = now_dt + timedelta(minutes=rolled)
        state["next_poll_at"] = nxt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _days_until(watch_date: str, today: date) -> int | None:
    try:
        return (date.fromisoformat(watch_date) - today).days
    except (TypeError, ValueError):
        return None


def has_imminent_watch(config: dict[str, Any], now_iso: str, days: int) -> bool:
    """Any active watch departing today .. today+days (KST)?"""
    now_dt = parse_dt(now_iso)
    if now_dt is None:
        return False
    today = now_dt.astimezone(KST).date()
    for w in config.get("watches") or []:
        if not w.get("active", True):
            continue
        left = _days_until(w.get("date"), today)
        if left is not None and 0 <= left <= days:
            return True
    return False


def _slowest_gap_min(mode: str, poll_interval_min: int, settings: dict[str, Any]) -> int:
    """Longest gap the user's own cadence can produce (0 = unthrottled)."""
    if mode == "range":
        rng = settings.get("poll_interval_range") or []
        try:
            return max(int(x) for x in rng) if len(rng) == 2 else poll_interval_min
        except (TypeError, ValueError):
            return poll_interval_min
    if mode == "choices":
        nums = []
        for c in settings.get("poll_interval_choices") or []:
            try:
                nums.append(int(c))
            except (TypeError, ValueError):
                continue
        return max(nums) if nums else poll_interval_min
    return poll_interval_min


def effective_cadence(
    config: dict[str, Any], now_iso: str,
) -> tuple[str, int, dict[str, Any], bool]:
    """(mode, poll_interval_min, settings, urgent) to throttle with right now.

    Normally the user's own settings. Inside the imminent-departure window it
    is a randomized URGENT_RANGE_MIN cadence instead, unless the user turned
    that off (settings.urgent_poll_enabled) or already polls faster.
    """
    settings = config.get("settings") or {}
    mode = settings.get("poll_interval_mode") or "fixed"
    interval = int(settings.get("poll_interval_min") or 0)
    if not settings.get("urgent_poll_enabled", True):
        return mode, interval, settings, False
    try:
        days = int(settings.get("urgent_poll_days", URGENT_DAYS_DEFAULT))
    except (TypeError, ValueError):
        days = URGENT_DAYS_DEFAULT
    slowest = _slowest_gap_min(mode, interval, settings)
    if slowest <= URGENT_RANGE_MIN[1] or not has_imminent_watch(config, now_iso, days):
        return mode, interval, settings, False
    urgent = {**settings, "poll_interval_range": list(URGENT_RANGE_MIN)}
    return "range", 0, urgent, True


def skip_poll(config: dict[str, Any], state: dict[str, Any], now_iso: str) -> bool:
    """Should an automated run skip? Applies the imminent-departure cadence.

    Entering the window, a next_poll_at rolled from the user's slower cadence
    may still be far off; it is capped at the urgent gap from the last poll
    so the tighter cadence takes effect at once rather than one gap later.
    """
    mode, interval, settings, urgent = effective_cadence(config, now_iso)
    if urgent:
        last_dt = parse_dt(state.get("last_run"))
        if last_dt is not None:
            cap = last_dt + timedelta(minutes=URGENT_RANGE_MIN[1])
            next_dt = parse_dt(state.get("next_poll_at"))
            if next_dt is None or next_dt > cap:
                state = {**state, "next_poll_at": cap.strftime("%Y-%m-%dT%H:%M:%SZ")}
    return should_throttle(mode, interval, settings, state, now_iso)


def after_poll(config: dict[str, Any], state: dict[str, Any], now_iso: str) -> None:
    """Roll next_poll_at for the cadence in force and record whether the
    imminent-departure cadence is on, so the app can show why."""
    mode, _, settings, urgent = effective_cadence(config, now_iso)
    set_next_poll(mode, settings, state, now_iso)
    if urgent:
        state["urgent_cadence"] = True
    else:
        state.pop("urgent_cadence", None)


def will_poll(config: dict[str, Any], state: dict[str, Any], event_name: str | None, now_iso: str) -> bool:
    """Would run_watches actually poll (True) or skip via throttle (False)?

    Mirrors worker.main.run_watches's gate exactly:
      - manual (workflow_dispatch) always polls
      - automated events poll unless should_throttle() says skip
      - unknown events default to polling (safe)
    """
    if event_name == "workflow_dispatch":
        return True
    if event_name in AUTOMATED_EVENTS:
        return not skip_poll(config, state, now_iso)
    return True
