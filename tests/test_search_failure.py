"""A provider whose every search fails is as dead as one that cannot log in,
and dies just as quietly: the run stays green, state advances, the health card
notices nothing. The 코레일+ MACRO rejection ran exactly this way — found by
reading logs, not by an alert. These pin the provider-level detection.
"""
from __future__ import annotations

from worker import main, notifier, state as state_mod
from worker.models import Reservation, Train, Watch


def _watch_dict(**overrides) -> dict:
    base = {
        "id": "w1", "provider": "korail", "from": "서울", "to": "부산",
        "date": "2026-10-15", "time_min": "09:00", "time_max": "12:00",
        "train_types": ["KTX"], "passengers": {"adult": 1},
        "seat_class": "general", "auto_reserve": False, "active": True,
    }
    base.update(overrides)
    return base


class FlakyProvider:
    """search() outcome per watch id: an Exception raises, a list returns."""

    name = "korail"

    def __init__(self, outcomes: dict):
        self._outcomes = outcomes

    def login(self, user_id: str, password: str) -> None: ...

    def search(self, watch: Watch) -> list[Train]:
        v = self._outcomes[watch.id]
        if isinstance(v, Exception):
            raise v
        return v

    def reserve(self, train, passengers, *, allow_waiting: bool = False) -> Reservation:
        raise AssertionError("not used")

    def paid_reservation_keys(self) -> set[str]:
        return set()


def _run(cfg, s, provider, monkeypatch, alerts):
    monkeypatch.setattr(notifier, "notify_search_failed",
                        lambda p, err, **kw: alerts.append(("down", p)))
    monkeypatch.setattr(notifier, "notify_search_recovered",
                        lambda p, **kw: alerts.append(("up", p)))
    main.run_watches(
        cfg, s,
        providers={"korail": provider},
        creds={"korail": ("u", "p")},
        notify_fn=lambda *a, **kw: None,
        now_iso="2026-09-24T06:00:00Z",
    )


class TestAllWatchesFailing:
    def test_alerts_once(self, monkeypatch):
        cfg = {"version": 1, "watches": [_watch_dict(id="a"), _watch_dict(id="b", date="2026-10-16")]}
        alerts: list = []
        boom = RuntimeError("MACRO ERROR")
        _run(cfg, state_mod.empty_state(),
             FlakyProvider({"a": boom, "b": boom}), monkeypatch, alerts)
        assert alerts == [("down", "korail")]

    def test_does_not_realert_within_a_day(self, monkeypatch):
        cfg = {"version": 1, "watches": [_watch_dict(id="a")]}
        s = state_mod.empty_state()
        alerts: list = []
        _run(cfg, s, FlakyProvider({"a": RuntimeError("x")}), monkeypatch, alerts)
        _run(cfg, s, FlakyProvider({"a": RuntimeError("x")}), monkeypatch, alerts)
        assert alerts == [("down", "korail")]

    def test_realerts_after_a_day(self):
        s = state_mod.empty_state()
        assert state_mod.record_search_failure(s, "korail", "2026-09-24T06:00:00Z") is True
        assert state_mod.record_search_failure(s, "korail", "2026-09-24T12:00:00Z") is False
        assert state_mod.record_search_failure(s, "korail", "2026-09-25T06:00:00Z") is True


class TestRecovery:
    def test_recovery_notifies_once(self, monkeypatch):
        cfg = {"version": 1, "watches": [_watch_dict(id="a")]}
        s = state_mod.empty_state()
        alerts: list = []
        _run(cfg, s, FlakyProvider({"a": RuntimeError("x")}), monkeypatch, alerts)
        _run(cfg, s, FlakyProvider({"a": []}), monkeypatch, alerts)
        _run(cfg, s, FlakyProvider({"a": []}), monkeypatch, alerts)
        assert alerts == [("down", "korail"), ("up", "korail")]

    def test_next_outage_alerts_again(self, monkeypatch):
        cfg = {"version": 1, "watches": [_watch_dict(id="a")]}
        s = state_mod.empty_state()
        alerts: list = []
        _run(cfg, s, FlakyProvider({"a": RuntimeError("x")}), monkeypatch, alerts)
        _run(cfg, s, FlakyProvider({"a": []}), monkeypatch, alerts)
        _run(cfg, s, FlakyProvider({"a": RuntimeError("y")}), monkeypatch, alerts)
        assert alerts == [("down", "korail"), ("up", "korail"), ("down", "korail")]


class TestPartialFailureIsNotAnOutage:
    def test_one_bad_watch_among_working_ones_stays_quiet(self, monkeypatch):
        # A typo'd station name kills one watch, not the provider. Alerting
        # "감시 중단" for it would teach the user to ignore the alert that has
        # to be believed when the whole provider goes dark.
        cfg = {"version": 1, "watches": [_watch_dict(id="a"), _watch_dict(id="b", date="2026-10-16")]}
        alerts: list = []
        _run(cfg, state_mod.empty_state(),
             FlakyProvider({"a": RuntimeError("x"), "b": []}), monkeypatch, alerts)
        assert alerts == []

    def test_no_watches_is_not_an_outage(self, monkeypatch):
        cfg = {"version": 1, "watches": []}
        alerts: list = []
        _run(cfg, state_mod.empty_state(), FlakyProvider({}), monkeypatch, alerts)
        assert alerts == []


class TestMessageContent:
    def test_failed_message_names_the_provider_and_reason(self):
        text = notifier.format_search_failed("korail", "MACRO ERROR")
        assert "코레일 검색 실패" in text
        assert "MACRO ERROR" in text

    def test_pushover_is_opt_in(self, monkeypatch):
        calls: list = []
        monkeypatch.setattr(notifier.pushover, "send", lambda *a, **kw: calls.append(a))
        monkeypatch.setattr(notifier, "send_telegram", lambda *a, **kw: None)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
        notifier.notify_search_failed("korail", "x")
        assert calls == []
        notifier.notify_search_failed("korail", "x", push=True)
        assert len(calls) == 1
