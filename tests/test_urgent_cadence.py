"""Imminent-departure cadence (worker.throttle.effective_cadence & co).

When a watch leaves within a couple of days the poll gap tightens to a
random 10–15 minutes, without ever dipping under the 10-minute floor or
slowing a user who already polls faster.
"""

from worker import throttle

NOW = "2026-10-10T03:00:00Z"   # 12:00 KST, Oct 10


def _cfg(date, settings=None, active=True):
    return {
        "settings": {"poll_interval_mode": "fixed", "poll_interval_min": 30, **(settings or {})},
        "watches": [{"id": "w", "date": date, "active": active}],
    }


def test_watch_within_window_turns_urgent():
    for d in ("2026-10-10", "2026-10-11", "2026-10-12"):
        mode, interval, settings, urgent = throttle.effective_cadence(_cfg(d), NOW)
        assert urgent and mode == "range" and interval == 0
        assert settings["poll_interval_range"] == [10, 15]


def test_outside_window_keeps_user_cadence():
    for d in ("2026-10-13", "2026-10-09"):   # too far / already left
        mode, interval, _, urgent = throttle.effective_cadence(_cfg(d), NOW)
        assert (mode, interval, urgent) == ("fixed", 30, False)


def test_today_is_judged_in_kst():
    # 23:30 UTC on Oct 9 is already Oct 10 08:30 in Korea: D-2 is Oct 12.
    _, _, _, urgent = throttle.effective_cadence(_cfg("2026-10-12"), "2026-10-09T23:30:00Z")
    assert urgent


def test_inactive_watch_is_ignored():
    assert not throttle.effective_cadence(_cfg("2026-10-10", active=False), NOW)[3]


def test_can_be_turned_off_and_window_widened():
    assert not throttle.effective_cadence(_cfg("2026-10-10", {"urgent_poll_enabled": False}), NOW)[3]
    assert throttle.effective_cadence(_cfg("2026-10-15", {"urgent_poll_days": 5}), NOW)[3]


def test_never_slows_a_faster_user():
    assert not throttle.effective_cadence(_cfg("2026-10-10", {"poll_interval_min": 12}), NOW)[3]
    assert not throttle.effective_cadence(_cfg("2026-10-10", {"poll_interval_min": 0}), NOW)[3]
    rng = {"poll_interval_mode": "range", "poll_interval_range": [10, 14]}
    assert not throttle.effective_cadence(_cfg("2026-10-10", rng), NOW)[3]
    ch = {"poll_interval_mode": "choices", "poll_interval_choices": ["11", 13]}
    assert not throttle.effective_cadence(_cfg("2026-10-10", ch), NOW)[3]


def test_entering_window_caps_a_far_next_poll():
    # Last poll 16 min ago under a 24~36 range that rolled 36 → next in 20 min.
    cfg = _cfg("2026-10-11", {"poll_interval_mode": "range", "poll_interval_range": [24, 36]})
    state = {"last_run": "2026-10-10T02:44:00Z", "next_poll_at": "2026-10-10T03:20:00Z"}
    assert throttle.skip_poll(cfg, state, NOW) is False
    assert state["next_poll_at"] == "2026-10-10T03:20:00Z"   # caller's state untouched
    # 9 minutes after the last poll is still inside the urgent gap.
    state = {"last_run": "2026-10-10T02:51:00Z"}
    assert throttle.skip_poll(cfg, state, NOW) is True


def test_after_poll_rolls_urgent_gap_and_flags_it():
    state = {}
    throttle.after_poll(_cfg("2026-10-10"), state, NOW)
    assert state["urgent_cadence"] is True
    assert "2026-10-10T03:10:00Z" <= state["next_poll_at"] <= "2026-10-10T03:15:00Z"
    throttle.after_poll(_cfg("2026-10-20"), state, NOW)
    assert "urgent_cadence" not in state and "next_poll_at" not in state


def test_gate_and_main_agree():
    cfg = _cfg("2026-10-10")
    # 16 min since the last poll, same 30-min bucket: fixed-30 alone would skip.
    state = {"last_run": "2026-10-10T03:04:00Z"}
    now = "2026-10-10T03:20:00Z"
    assert throttle.should_throttle("fixed", 30, cfg["settings"], state, now) is True
    assert throttle.will_poll(cfg, state, "repository_dispatch", now) is True
