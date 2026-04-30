"""Tests for worker.main — orchestration of config → providers → matcher → notifier → state."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from worker import main, state as state_mod
from worker.models import Reservation, Train, Watch


def _watch_dict(**overrides) -> dict:
    base = {
        "id": "w1",
        "provider": "korail",
        "from": "서울",
        "to": "부산",
        "date": "2026-05-15",
        "time_min": "09:00",
        "time_max": "12:00",
        "train_types": ["KTX"],
        "passengers": {"adult": 1},
        "seat_class": "general",
        "auto_reserve": False,
        "active": True,
    }
    base.update(overrides)
    return base


def _train(raw_id: str = "rid", **overrides) -> Train:
    base = dict(
        provider="korail",
        train_no="045",
        train_type="KTX",
        dep_station="서울",
        arr_station="부산",
        date="2026-05-15",
        dep_time="09:35",
        arr_time="12:10",
        seats_general=1,
        seats_special=0,
        raw_id=raw_id,
    )
    base.update(overrides)
    return Train(**base)


class FakeProvider:
    def __init__(
        self,
        name: str,
        search_results=None,
        raise_on_search: Exception | None = None,
        reserve_result=None,
        raise_on_reserve: Exception | None = None,
    ):
        self.name = name
        self.logged_in_with: tuple[str, str] | None = None
        self.searches: list[Watch] = []
        self.reserve_calls: list[tuple[Train, object]] = []
        self._results = search_results
        self._raise = raise_on_search
        self._reserve_result = reserve_result
        self._reserve_raise = raise_on_reserve

    def login(self, user_id: str, password: str) -> None:
        self.logged_in_with = (user_id, password)

    def search(self, watch: Watch) -> list[Train]:
        self.searches.append(watch)
        if self._raise:
            raise self._raise
        if callable(self._results):
            return self._results(watch)
        return list(self._results or [])

    def reserve(self, train: Train, passengers) -> Reservation:
        self.reserve_calls.append((train, passengers))
        if self._reserve_raise:
            raise self._reserve_raise
        if self._reserve_result:
            return self._reserve_result
        return Reservation(provider=self.name, reservation_id="FAKE", train_no=train.train_no)


class NotifyRecorder:
    def __init__(self) -> None:
        self.calls: list[tuple[Watch, list[Train]]] = []

    def __call__(self, watch: Watch, trains: list[Train], *, session=None) -> None:
        self.calls.append((watch, list(trains)))


@pytest.fixture
def cfg_with_two_korail_watches() -> dict[str, Any]:
    return {
        "version": 1,
        "watches": [
            _watch_dict(id="a", date="2026-05-15"),
            _watch_dict(id="b", date="2026-05-16"),
        ],
    }


class TestRunWatches:
    def test_logs_in_only_for_used_providers(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="a", provider="korail")]}
        korail = FakeProvider("korail")
        srt = FakeProvider("srt")
        s = state_mod._default()
        main.run_watches(
            cfg, s,
            providers={"korail": korail, "srt": srt},
            creds={"korail": ("u1", "p1"), "srt": ("u2", "p2")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T15:00:00Z",
        )
        assert korail.logged_in_with == ("u1", "p1")
        assert srt.logged_in_with is None

    def test_logs_in_once_per_provider_for_multiple_watches(self):
        cfg = {
            "version": 1,
            "watches": [
                _watch_dict(id="a", provider="korail"),
                _watch_dict(id="b", provider="korail", date="2026-05-16"),
            ],
        }
        korail = FakeProvider("korail")
        s = state_mod._default()
        main.run_watches(
            cfg, s,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="t",
        )
        assert korail.logged_in_with == ("u", "p")
        assert len(korail.searches) == 2

    def test_skips_inactive_watches(self):
        cfg = {
            "version": 1,
            "watches": [
                _watch_dict(id="active", active=True),
                _watch_dict(id="off", active=False, date="2026-06-01"),
            ],
        }
        korail = FakeProvider("korail")
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="t",
        )
        assert len(korail.searches) == 1
        assert korail.searches[0].id == "active"

    def test_notifies_on_new_trains_and_updates_state(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w")]}
        korail = FakeProvider("korail", search_results=[_train(raw_id="r1")])
        recorder = NotifyRecorder()
        s = state_mod._default()
        main.run_watches(
            cfg, s,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=recorder,
            now_iso="t",
        )
        assert len(recorder.calls) == 1
        watch, trains = recorder.calls[0]
        assert watch.id == "w"
        assert [t.raw_id for t in trains] == ["r1"]
        assert s["watches"]["w"]["notified_train_ids"] == ["r1"]
        assert s["watches"]["w"]["last_check"] == "t"

    def test_does_not_renotify_already_seen_train(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w")]}
        korail = FakeProvider("korail", search_results=[_train(raw_id="r1")])
        s = {"last_run": None, "watches": {"w": {"last_check": None, "notified_train_ids": ["r1"]}}}
        recorder = NotifyRecorder()
        main.run_watches(
            cfg, s,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=recorder,
            now_iso="t",
        )
        assert recorder.calls == []

    def test_continues_when_one_watch_fails(self):
        cfg = {
            "version": 1,
            "watches": [
                _watch_dict(id="boom"),
                _watch_dict(id="ok", date="2026-05-16"),
            ],
        }
        boom = RuntimeError("network down")

        def search_results(watch: Watch):
            if watch.id == "boom":
                raise boom
            return [_train(raw_id="r-ok", date="2026-05-16")]

        korail = FakeProvider("korail", search_results=search_results)
        recorder = NotifyRecorder()
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=recorder,
            now_iso="t",
        )
        assert [c[0].id for c in recorder.calls] == ["ok"]

    def test_continues_when_provider_login_fails(self):
        cfg = {
            "version": 1,
            "watches": [
                _watch_dict(id="korail-only", provider="korail"),
                _watch_dict(id="srt-only", provider="srt", **{"from": "수서", "to": "부산", "train_types": ["SRT"]}),
            ],
        }
        korail_fail = FakeProvider("korail")

        def boom_login(*a, **kw):
            raise RuntimeError("auth failed")

        korail_fail.login = boom_login  # type: ignore
        srt = FakeProvider("srt", search_results=[_train(provider="srt", raw_id="srt-rid", train_type="SRT")])
        recorder = NotifyRecorder()
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail_fail, "srt": srt},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=recorder,
            now_iso="t",
        )
        assert [c[0].id for c in recorder.calls] == ["srt-only"]

    def test_marks_last_run(self):
        cfg = {"version": 1, "watches": []}
        s = state_mod._default()
        main.run_watches(
            cfg, s,
            providers={"korail": FakeProvider("korail"), "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T15:00:00Z",
        )
        assert s["last_run"] == "2026-04-30T15:00:00Z"

    def test_records_watch_date_for_pruning(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", date="2026-05-15")]}
        s = state_mod._default()
        main.run_watches(
            cfg, s,
            providers={"korail": FakeProvider("korail"), "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="t",
        )
        assert s["watches"]["w"]["watch_date"] == "2026-05-15"


class TestManualTriggerSummary:
    def test_sends_summary_on_workflow_dispatch_with_zero_results(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w")]}
        korail = FakeProvider("korail", search_results=[])
        summary_calls: list[list[Watch]] = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_summary_fn=lambda ws: summary_calls.append(list(ws)),
            now_iso="t",
            event_name="workflow_dispatch",
        )
        assert len(summary_calls) == 1
        assert [w.id for w in summary_calls[0]] == ["w"]

    def test_does_not_send_summary_on_cron_trigger(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w")]}
        korail = FakeProvider("korail", search_results=[])
        summary_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_summary_fn=lambda ws: summary_calls.append(ws),
            now_iso="t",
            event_name="schedule",
        )
        assert summary_calls == []

    def test_does_not_send_summary_when_new_seats_found(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w")]}
        korail = FakeProvider("korail", search_results=[_train(raw_id="r1")])
        summary_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_summary_fn=lambda ws: summary_calls.append(ws),
            now_iso="t",
            event_name="workflow_dispatch",
        )
        assert summary_calls == []

    def test_summary_function_optional(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w")]}
        korail = FakeProvider("korail", search_results=[])
        # No summary fn passed — should not raise
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="t",
            event_name="workflow_dispatch",
        )

    def test_cron_with_notify_empty_setting_sends_summary(self):
        cfg = {
            "version": 1,
            "settings": {"notify_empty_on_cron": True},
            "watches": [_watch_dict(id="w")],
        }
        korail = FakeProvider("korail", search_results=[])
        summary_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_summary_fn=lambda ws: summary_calls.append(ws),
            now_iso="t",
            event_name="schedule",
        )
        assert len(summary_calls) == 1

    def test_repository_dispatch_treated_like_schedule_with_setting(self):
        cfg = {
            "version": 1,
            "settings": {"notify_empty_on_cron": True},
            "watches": [_watch_dict(id="w")],
        }
        korail = FakeProvider("korail", search_results=[])
        summary_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_summary_fn=lambda ws: summary_calls.append(ws),
            now_iso="t",
            event_name="repository_dispatch",
        )
        assert len(summary_calls) == 1

    def test_repository_dispatch_no_summary_without_setting(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w")]}
        korail = FakeProvider("korail", search_results=[])
        summary_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_summary_fn=lambda ws: summary_calls.append(ws),
            now_iso="t",
            event_name="repository_dispatch",
        )
        assert summary_calls == []


class TestPollInterval:
    def test_skip_when_within_interval(self):
        cfg = {
            "version": 1,
            "settings": {"poll_interval_min": 10},
            "watches": [_watch_dict(id="w")],
        }
        state = {"last_run": "2026-04-30T11:00:00Z", "watches": {}}
        korail = FakeProvider("korail", search_results=[_train(raw_id="r1")])
        main.run_watches(
            cfg, state,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T11:03:00Z",
            event_name="repository_dispatch",
        )
        assert korail.searches == []  # skipped before search
        # last_run not updated since we early-exit
        assert state["last_run"] == "2026-04-30T11:00:00Z"

    def test_run_when_interval_elapsed(self):
        cfg = {
            "version": 1,
            "settings": {"poll_interval_min": 10},
            "watches": [_watch_dict(id="w")],
        }
        state = {"last_run": "2026-04-30T11:00:00Z", "watches": {}}
        korail = FakeProvider("korail", search_results=[])
        main.run_watches(
            cfg, state,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T11:11:00Z",
            event_name="repository_dispatch",
        )
        assert len(korail.searches) == 1
        assert state["last_run"] == "2026-04-30T11:11:00Z"

    def test_manual_dispatch_overrides_interval(self):
        cfg = {
            "version": 1,
            "settings": {"poll_interval_min": 30},
            "watches": [_watch_dict(id="w")],
        }
        state = {"last_run": "2026-04-30T11:00:00Z", "watches": {}}
        korail = FakeProvider("korail", search_results=[])
        main.run_watches(
            cfg, state,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T11:01:00Z",
            event_name="workflow_dispatch",
        )
        assert len(korail.searches) == 1

    def test_zero_interval_means_no_skip(self):
        cfg = {
            "version": 1,
            "settings": {"poll_interval_min": 0},
            "watches": [_watch_dict(id="w")],
        }
        state = {"last_run": "2026-04-30T11:00:00Z", "watches": {}}
        korail = FakeProvider("korail", search_results=[])
        main.run_watches(
            cfg, state,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T11:00:30Z",
            event_name="repository_dispatch",
        )
        assert len(korail.searches) == 1

    def test_first_run_no_skip(self):
        cfg = {
            "version": 1,
            "settings": {"poll_interval_min": 10},
            "watches": [_watch_dict(id="w")],
        }
        state = {"last_run": None, "watches": {}}
        korail = FakeProvider("korail", search_results=[])
        main.run_watches(
            cfg, state,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T11:11:00Z",
            event_name="repository_dispatch",
        )
        assert len(korail.searches) == 1

    def test_runs_at_next_bucket_even_when_drift_under_interval(self):
        # Regression: under the old elapsed-based check, a run that started
        # at 11:00:25 (typical GHA setup delay) would cause the 11:30:00
        # tick to measure 29:35 elapsed and skip. Bucket-based check
        # treats these as different 30-min windows, so it runs.
        cfg = {
            "version": 1,
            "settings": {"poll_interval_min": 30},
            "watches": [_watch_dict(id="w")],
        }
        state = {"last_run": "2026-04-30T11:00:25Z", "watches": {}}
        korail = FakeProvider("korail", search_results=[])
        main.run_watches(
            cfg, state,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T11:30:00Z",
            event_name="repository_dispatch",
        )
        assert len(korail.searches) == 1

    def test_skips_within_same_bucket(self):
        # Regression sibling: 11:00:00 and 11:29:59 are the same 30-min
        # bucket, so the second tick still skips.
        cfg = {
            "version": 1,
            "settings": {"poll_interval_min": 30},
            "watches": [_watch_dict(id="w")],
        }
        state = {"last_run": "2026-04-30T11:00:00Z", "watches": {}}
        korail = FakeProvider("korail", search_results=[])
        main.run_watches(
            cfg, state,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="2026-04-30T11:29:59Z",
            event_name="repository_dispatch",
        )
        assert korail.searches == []


class TestAlreadyExistedReservation:
    def test_already_existed_skips_success_notify(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True)]}
        existing = Reservation(
            provider="korail",
            reservation_id="(기존)",
            train_no="045",
            already_existed=True,
        )
        korail = FakeProvider(
            "korail",
            search_results=[_train(raw_id="r1")],
            reserve_result=existing,
        )
        success_calls: list = []
        failure_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_reserve_success_fn=lambda *a: success_calls.append(a),
            notify_reserve_failure_fn=lambda *a: failure_calls.append(a),
            now_iso="t",
            event_name="repository_dispatch",
        )
        assert success_calls == []
        assert failure_calls == []
        assert len(korail.reserve_calls) == 1

    def test_normal_reservation_still_notifies_success(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True)]}
        fresh = Reservation(
            provider="korail",
            reservation_id="ABC123",
            train_no="045",
            expires_at="2026-04-30T20:56:00+09:00",
        )
        korail = FakeProvider(
            "korail",
            search_results=[_train(raw_id="r1")],
            reserve_result=fresh,
        )
        success_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_reserve_success_fn=lambda *a: success_calls.append(a),
            now_iso="t",
            event_name="repository_dispatch",
        )
        assert len(success_calls) == 1


class TestScheduleReminders:
    def test_schedules_after_fresh_reservation(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True)]}
        fresh = Reservation(
            provider="korail", reservation_id="ABC", train_no="045",
            expires_at="2026-04-30T20:56:00+09:00",
        )
        korail = FakeProvider(
            "korail",
            search_results=[_train(raw_id="r1")],
            reserve_result=fresh,
        )
        sched_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_reserve_success_fn=lambda *a: None,
            schedule_reminders_fn=lambda *a: sched_calls.append(a),
            now_iso="t",
            event_name="repository_dispatch",
        )
        assert len(sched_calls) == 1
        assert sched_calls[0][2].reservation_id == "ABC"

    def test_does_not_schedule_for_already_existed_reservation(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True)]}
        existing = Reservation(
            provider="korail", reservation_id="(기존)", train_no="045",
            already_existed=True,
        )
        korail = FakeProvider(
            "korail",
            search_results=[_train(raw_id="r1")],
            reserve_result=existing,
        )
        sched_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            schedule_reminders_fn=lambda *a: sched_calls.append(a),
            now_iso="t",
            event_name="repository_dispatch",
        )
        assert sched_calls == []

    def test_schedule_failure_does_not_crash_run(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True)]}
        fresh = Reservation(
            provider="korail", reservation_id="ABC", train_no="045",
            expires_at="2026-04-30T20:56:00+09:00",
        )
        korail = FakeProvider(
            "korail",
            search_results=[_train(raw_id="r1")],
            reserve_result=fresh,
        )

        def boom(*a):
            raise RuntimeError("CF Worker unreachable")

        # Should not raise — failure is logged and swallowed
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            schedule_reminders_fn=boom,
            now_iso="t",
            event_name="repository_dispatch",
        )

    def test_cron_with_notify_empty_setting_skips_summary_when_seats_found(self):
        cfg = {
            "version": 1,
            "settings": {"notify_empty_on_cron": True},
            "watches": [_watch_dict(id="w")],
        }
        korail = FakeProvider("korail", search_results=[_train(raw_id="r1")])
        summary_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_summary_fn=lambda ws: summary_calls.append(ws),
            now_iso="t",
            event_name="schedule",
        )
        assert summary_calls == []

    def test_cron_with_setting_disabled_does_not_send(self):
        cfg = {
            "version": 1,
            "settings": {"notify_empty_on_cron": False},
            "watches": [_watch_dict(id="w")],
        }
        korail = FakeProvider("korail", search_results=[])
        summary_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_summary_fn=lambda ws: summary_calls.append(ws),
            now_iso="t",
            event_name="schedule",
        )
        assert summary_calls == []


class TestAutoReserve:
    def test_auto_reserve_flag_off_does_not_call_reserve(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=False)]}
        korail = FakeProvider("korail", search_results=[_train(raw_id="r1")])
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="t",
        )
        assert korail.reserve_calls == []

    def test_auto_reserve_calls_reserve_with_first_new_train(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True)]}
        t1 = _train(raw_id="r1", train_no="045")
        t2 = _train(raw_id="r2", train_no="047", dep_time="10:00")
        korail = FakeProvider("korail", search_results=[t1, t2])
        success_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_reserve_success_fn=lambda w, t, r: success_calls.append((w.id, t.train_no, r.reservation_id)),
            now_iso="t",
        )
        assert len(korail.reserve_calls) == 1
        assert korail.reserve_calls[0][0].train_no == "045"
        assert success_calls == [("w", "045", "FAKE")]

    def test_auto_reserve_skipped_when_no_new_trains(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True)]}
        korail = FakeProvider("korail", search_results=[])
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="t",
        )
        assert korail.reserve_calls == []

    def test_auto_reserve_skipped_for_already_notified(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True)]}
        korail = FakeProvider("korail", search_results=[_train(raw_id="r1")])
        s = {"last_run": None, "watches": {"w": {"last_check": None, "notified_train_ids": ["r1"]}}}
        main.run_watches(
            cfg, s,
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="t",
        )
        assert korail.reserve_calls == []

    def test_reserve_failure_calls_failure_notify_and_continues(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True), _watch_dict(id="w2", auto_reserve=False, date="2026-05-16")]}
        korail = FakeProvider(
            "korail",
            search_results=[_train(raw_id="r1")],
            raise_on_reserve=RuntimeError("좌석 매진"),
        )
        success_calls: list = []
        failure_calls: list = []
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            notify_reserve_success_fn=lambda *a: success_calls.append(a),
            notify_reserve_failure_fn=lambda w, t, msg: failure_calls.append((w.id, t.train_no, msg)),
            now_iso="t",
        )
        assert success_calls == []
        assert len(failure_calls) == 1
        assert failure_calls[0][0] == "w"
        assert "매진" in failure_calls[0][2]
        # Second watch (auto_reserve=False) was processed, no exception bubbled
        assert len(korail.searches) == 2

    def test_reserve_passengers_threaded_through(self):
        cfg = {"version": 1, "watches": [_watch_dict(id="w", auto_reserve=True, passengers={"adult": 2, "child": 1, "senior": 0})]}
        korail = FakeProvider("korail", search_results=[_train(raw_id="r1")])
        main.run_watches(
            cfg, state_mod._default(),
            providers={"korail": korail, "srt": FakeProvider("srt")},
            creds={"korail": ("u", "p"), "srt": ("u", "p")},
            notify_fn=NotifyRecorder(),
            now_iso="t",
        )
        assert len(korail.reserve_calls) == 1
        passengers = korail.reserve_calls[0][1]
        assert passengers.adult == 2
        assert passengers.child == 1


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_path: Path):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"version": 1, "watches": [_watch_dict()]}), encoding="utf-8")
        cfg = main.load_config(path)
        assert cfg["version"] == 1
        assert len(cfg["watches"]) == 1


class TestLoadCredentials:
    def test_reads_env_for_both_providers(self, monkeypatch):
        monkeypatch.setenv("KORAIL_ID", "k_id")
        monkeypatch.setenv("KORAIL_PW", "k_pw")
        monkeypatch.setenv("SRT_ID", "s_id")
        monkeypatch.setenv("SRT_PW", "s_pw")
        creds = main.load_credentials()
        assert creds["korail"] == ("k_id", "k_pw")
        assert creds["srt"] == ("s_id", "s_pw")

    def test_returns_empty_tuple_when_creds_missing(self, monkeypatch):
        for var in ("KORAIL_ID", "KORAIL_PW", "SRT_ID", "SRT_PW"):
            monkeypatch.delenv(var, raising=False)
        creds = main.load_credentials()
        assert creds["korail"] == ("", "")
        assert creds["srt"] == ("", "")
