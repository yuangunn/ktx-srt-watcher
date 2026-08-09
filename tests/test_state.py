"""Tests for worker.state — the in-memory poll-state dict."""
from __future__ import annotations

from worker import state


class TestNotifiedIds:
    def test_get_returns_empty_set_for_unknown_watch(self):
        s = {"last_run": None, "watches": {}}
        assert state.get_notified_ids(s, "missing") == set()

    def test_get_returns_existing_ids(self):
        s = {"last_run": None, "watches": {"w1": {"last_check": None, "notified_train_ids": ["a", "b"]}}}
        assert state.get_notified_ids(s, "w1") == {"a", "b"}

    def test_add_creates_watch_entry_if_missing(self):
        s = {"last_run": None, "watches": {}}
        state.add_notified_ids(s, "new", ["t1", "t2"])
        assert s["watches"]["new"]["notified_train_ids"] == ["t1", "t2"]

    def test_add_dedupes_and_sorts(self):
        s = {"last_run": None, "watches": {"w1": {"last_check": None, "notified_train_ids": ["b", "a"]}}}
        state.add_notified_ids(s, "w1", ["c", "a"])
        assert s["watches"]["w1"]["notified_train_ids"] == ["a", "b", "c"]


class TestMarkers:
    def test_mark_run_sets_timestamp(self):
        s = {"last_run": None, "watches": {}}
        state.mark_run(s, "2026-04-30T15:00:00Z")
        assert s["last_run"] == "2026-04-30T15:00:00Z"

    def test_mark_check_creates_watch_entry(self):
        s = {"last_run": None, "watches": {}}
        state.mark_check(s, "wid", "2026-04-30T15:00:00Z")
        assert s["watches"]["wid"]["last_check"] == "2026-04-30T15:00:00Z"
        assert s["watches"]["wid"]["notified_train_ids"] == []



class TestPruneByDate:
    def test_removes_entries_for_passed_dates(self):
        s = {
            "last_run": None,
            "watches": {
                "past": {"last_check": None, "notified_train_ids": ["a"], "watch_date": "2026-04-01"},
                "future": {"last_check": None, "notified_train_ids": ["b"], "watch_date": "2026-12-25"},
            },
        }
        state.prune_past_dates(s, today="2026-04-30")
        assert "past" not in s["watches"]
        assert "future" in s["watches"]

    def test_keeps_entries_without_watch_date(self):
        s = {"last_run": None, "watches": {"x": {"last_check": None, "notified_train_ids": ["a"]}}}
        state.prune_past_dates(s, today="2026-04-30")
        assert "x" in s["watches"]


class TestPollHistory:
    def test_record_poll_appends_entry(self):
        s = {"last_run": None, "watches": {}}
        state.record_poll(s, "2026-07-06T10:00:00Z", 0)
        state.record_poll(s, "2026-07-06T10:20:00Z", 2)
        assert s["poll_history"] == [
            {"t": "2026-07-06T10:00:00Z", "seats": 0},
            {"t": "2026-07-06T10:20:00Z", "seats": 2},
        ]

    def test_record_poll_caps_at_max_keeping_newest(self):
        s = {}
        for i in range(state.POLL_HISTORY_MAX + 50):
            state.record_poll(s, f"t{i}", 0)
        assert len(s["poll_history"]) == state.POLL_HISTORY_MAX
        assert s["poll_history"][-1]["t"] == f"t{state.POLL_HISTORY_MAX + 49}"


class TestAutoReserveDisabled:
    def test_disable_and_query(self):
        s = {}
        assert state.is_auto_reserve_disabled(s, "w1") is False
        state.disable_auto_reserve(s, "w1")
        assert state.is_auto_reserve_disabled(s, "w1") is True
        # idempotent
        state.disable_auto_reserve(s, "w1")
        assert s["auto_reserve_disabled"] == ["w1"]

    def test_prune_drops_disabled_for_removed_watch(self):
        s = {
            "last_run": None,
            "watches": {"keep": {"watch_date": "2026-12-25"}},
            "auto_reserve_disabled": ["keep", "gone"],
        }
        state.prune_past_dates(s, today="2026-04-30")
        assert s["auto_reserve_disabled"] == ["keep"]


class TestPruneOrphanFlags:
    def test_drops_disable_flag_for_watch_absent_from_config(self):
        s = {"watches": {"old": {"watch_date": "2026-08-17"}}, "auto_reserve_disabled": ["old", "live"]}
        state.prune_orphan_flags(s, ["live"])
        assert s["auto_reserve_disabled"] == ["live"]

    def test_keeps_flag_for_inactive_watch_still_in_config(self):
        # Deactivating a watch must not silently re-arm its auto-reserve.
        s = {"watches": {}, "auto_reserve_disabled": ["paused"]}
        state.prune_orphan_flags(s, ["paused"])
        assert s["auto_reserve_disabled"] == ["paused"]

    def test_drops_orphan_pending_reservation(self):
        s = {"watches": {}, "pending_reservations": {"old": {"id": "R1"}, "live": {"id": "R2"}}}
        state.prune_orphan_flags(s, ["live"])
        assert set(s["pending_reservations"]) == {"live"}

    def test_no_op_when_nothing_tracked(self):
        s = {"watches": {}}
        state.prune_orphan_flags(s, ["live"])
        assert "auto_reserve_disabled" not in s
        assert "pending_reservations" not in s
