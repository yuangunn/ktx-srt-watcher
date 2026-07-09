"""Tests for the pre-install poll gate (worker.throttle.will_poll)."""
from worker.throttle import will_poll, should_throttle, AUTOMATED_EVENTS


CFG = {"settings": {"poll_interval_mode": "range", "poll_interval_range": [24, 36]}}
MID = {"last_run": "2026-07-09T12:00:00Z", "next_poll_at": "2026-07-09T12:30:00Z"}


def test_manual_always_polls():
    assert will_poll(CFG, MID, "workflow_dispatch", "2026-07-09T12:05:00Z") is True


def test_repository_dispatch_throttled_mid_cycle():
    assert will_poll(CFG, MID, "repository_dispatch", "2026-07-09T12:05:00Z") is False


def test_repository_dispatch_polls_after_next():
    assert will_poll(CFG, MID, "repository_dispatch", "2026-07-09T12:31:00Z") is True


def test_push_throttled_mid_cycle():
    # push (config save) obeys throttle, same as PR #8
    assert will_poll(CFG, MID, "push", "2026-07-09T12:05:00Z") is False


def test_first_run_without_next_poll_polls():
    assert will_poll(CFG, {}, "repository_dispatch", "2026-07-09T12:05:00Z") is True


def test_unknown_event_fails_open():
    assert will_poll(CFG, MID, None, "2026-07-09T12:05:00Z") is True


def test_fixed_mode_bucket_throttle():
    cfg = {"settings": {"poll_interval_mode": "fixed", "poll_interval_min": 30}}
    st = {"last_run": "2026-07-09T12:00:00Z"}
    assert will_poll(cfg, st, "schedule", "2026-07-09T12:20:00Z") is False  # same 30m bucket
    assert will_poll(cfg, st, "schedule", "2026-07-09T12:35:00Z") is True   # next bucket


def test_automated_events_set():
    assert set(AUTOMATED_EVENTS) == {"schedule", "repository_dispatch", "push"}
