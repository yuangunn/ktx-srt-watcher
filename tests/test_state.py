"""Tests for worker.state — read/write state.json with atomic semantics."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker import state


class TestReadState:
    def test_returns_default_when_file_missing(self, tmp_path: Path):
        result = state.read_state(tmp_path / "missing.json")
        assert result == {"last_run": None, "watches": {}}

    def test_reads_existing_file(self, tmp_path: Path):
        path = tmp_path / "state.json"
        payload = {"last_run": "2026-04-30T15:00:00Z", "watches": {"x": {"last_check": None, "notified_train_ids": ["a"]}}}
        path.write_text(json.dumps(payload), encoding="utf-8")
        assert state.read_state(path) == payload

    def test_handles_corrupted_file_by_returning_default(self, tmp_path: Path):
        path = tmp_path / "state.json"
        path.write_text("not json{{", encoding="utf-8")
        result = state.read_state(path)
        assert result == {"last_run": None, "watches": {}}


class TestWriteState:
    def test_round_trip(self, tmp_path: Path):
        path = tmp_path / "state.json"
        payload = {"last_run": "2026-04-30T15:00:00Z", "watches": {"id1": {"last_check": "2026-04-30T15:00:00Z", "notified_train_ids": ["x"]}}}
        state.write_state(path, payload)
        assert json.loads(path.read_text(encoding="utf-8")) == payload

    def test_preserves_korean_characters_unescaped(self, tmp_path: Path):
        path = tmp_path / "state.json"
        payload = {"last_run": None, "watches": {"부산행": {"last_check": None, "notified_train_ids": []}}}
        state.write_state(path, payload)
        text = path.read_text(encoding="utf-8")
        assert "부산행" in text
        assert "\\u" not in text

    def test_atomic_write_does_not_leave_tmp_file(self, tmp_path: Path):
        path = tmp_path / "state.json"
        state.write_state(path, {"last_run": None, "watches": {}})
        leftovers = list(tmp_path.glob("*.tmp"))
        assert leftovers == []

    def test_overwrites_existing(self, tmp_path: Path):
        path = tmp_path / "state.json"
        path.write_text('{"old": true}', encoding="utf-8")
        state.write_state(path, {"last_run": "now", "watches": {}})
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert "old" not in loaded
        assert loaded["last_run"] == "now"


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


class TestMergeStates:
    def test_takes_later_last_run(self):
        a = {"last_run": "2026-04-30T11:00:00Z", "watches": {}}
        b = {"last_run": "2026-04-30T11:30:00Z", "watches": {}}
        assert state.merge_states(a, b)["last_run"] == "2026-04-30T11:30:00Z"
        assert state.merge_states(b, a)["last_run"] == "2026-04-30T11:30:00Z"

    def test_unions_notified_ids_per_watch(self):
        a = {"last_run": "t", "watches": {"w1": {"notified_train_ids": ["x", "y"], "last_check": None}}}
        b = {"last_run": "t", "watches": {"w1": {"notified_train_ids": ["y", "z"], "last_check": None}}}
        merged = state.merge_states(a, b)
        assert merged["watches"]["w1"]["notified_train_ids"] == ["x", "y", "z"]

    def test_unions_watch_keys(self):
        a = {"last_run": "t", "watches": {"w1": {"notified_train_ids": [], "last_check": None}}}
        b = {"last_run": "t", "watches": {"w2": {"notified_train_ids": [], "last_check": None}}}
        merged = state.merge_states(a, b)
        assert set(merged["watches"]) == {"w1", "w2"}

    def test_takes_later_last_check_per_watch(self):
        a = {"last_run": "t", "watches": {"w1": {"last_check": "2026-04-30T11:00:00Z", "notified_train_ids": []}}}
        b = {"last_run": "t", "watches": {"w1": {"last_check": "2026-04-30T11:30:00Z", "notified_train_ids": []}}}
        merged = state.merge_states(a, b)
        assert merged["watches"]["w1"]["last_check"] == "2026-04-30T11:30:00Z"

    def test_preserves_watch_date(self):
        a = {
            "last_run": "t",
            "watches": {"w1": {"notified_train_ids": [], "last_check": None, "watch_date": "2026-05-15"}},
        }
        b = {"last_run": "t", "watches": {"w1": {"notified_train_ids": [], "last_check": None}}}
        merged = state.merge_states(a, b)
        assert merged["watches"]["w1"]["watch_date"] == "2026-05-15"
        assert state.merge_states(b, a)["watches"]["w1"]["watch_date"] == "2026-05-15"

    def test_idempotent_on_identical_inputs(self):
        s = {
            "last_run": "2026-04-30T11:00:00Z",
            "watches": {"w1": {"last_check": "2026-04-30T11:00:00Z", "notified_train_ids": ["a", "b"]}},
        }
        assert state.merge_states(s, s) == s

    def test_handles_empty_states(self):
        a = {"last_run": None, "watches": {}}
        b = {"last_run": None, "watches": {}}
        merged = state.merge_states(a, b)
        assert merged["last_run"] is None
        assert merged["watches"] == {}

    def test_handles_one_side_null(self):
        a = {"last_run": "2026-04-30T11:00:00Z", "watches": {}}
        b = {"last_run": None, "watches": {}}
        assert state.merge_states(a, b)["last_run"] == "2026-04-30T11:00:00Z"
        assert state.merge_states(b, a)["last_run"] == "2026-04-30T11:00:00Z"


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

    def test_merge_unions_and_sorts_history(self):
        ours = {"watches": {}, "poll_history": [
            {"t": "2026-07-06T10:00:00Z", "seats": 0},
            {"t": "2026-07-06T10:20:00Z", "seats": 2},
        ]}
        remote = {"watches": {}, "poll_history": [
            {"t": "2026-07-06T10:00:00Z", "seats": 0},
            {"t": "2026-07-06T10:10:00Z", "seats": 1},
        ]}
        merged = state.merge_states(ours, remote)["poll_history"]
        assert [e["t"] for e in merged] == [
            "2026-07-06T10:00:00Z", "2026-07-06T10:10:00Z", "2026-07-06T10:20:00Z",
        ]

    def test_merge_one_sided_history(self):
        merged = state.merge_states({"watches": {}}, {"watches": {}, "poll_history": [{"t": "a", "seats": 0}]})
        assert merged["poll_history"] == [{"t": "a", "seats": 0}]

    def test_merge_no_history_key_when_both_empty(self):
        merged = state.merge_states({"watches": {}}, {"watches": {}})
        assert "poll_history" not in merged


class TestAutoReserveDisabled:
    def test_disable_and_query(self):
        s = {}
        assert state.is_auto_reserve_disabled(s, "w1") is False
        state.disable_auto_reserve(s, "w1")
        assert state.is_auto_reserve_disabled(s, "w1") is True
        # idempotent
        state.disable_auto_reserve(s, "w1")
        assert s["auto_reserve_disabled"] == ["w1"]

    def test_merge_prefers_ours_for_disabled(self):
        # "ours" wins rather than unioning: this run may have just re-armed a
        # watch (hold expired unpaid), and a union would resurrect the remote's
        # stale disable — leaving auto-reserve dead exactly when it must hunt.
        ours = {"watches": {}, "auto_reserve_disabled": []}
        remote = {"watches": {}, "auto_reserve_disabled": ["w1"]}
        merged = state.merge_states(ours, remote)
        assert merged.get("auto_reserve_disabled", []) == []

    def test_merge_falls_back_to_remote_when_ours_absent(self):
        ours = {"watches": {}}
        remote = {"watches": {}, "auto_reserve_disabled": ["w1"]}
        assert state.merge_states(ours, remote)["auto_reserve_disabled"] == ["w1"]

    def test_merge_pending_reservations_ours_wins(self):
        ours = {"watches": {}, "pending_reservations": {"w1": {"id": "NEW", "deadline": None}}}
        remote = {"watches": {}, "pending_reservations": {"w1": {"id": "OLD", "deadline": None}, "w2": {"id": "R2"}}}
        merged = state.merge_states(ours, remote)["pending_reservations"]
        assert merged["w1"]["id"] == "NEW"
        assert merged["w2"]["id"] == "R2"

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
