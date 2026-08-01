"""A dead Korail/SRT login is the quietest way this system can fail: the run
still succeeds and last_run still advances, so nothing else notices. These pin
the alert-once / recover / re-alert-daily behaviour."""
from __future__ import annotations

from worker import state


class TestRecordLoginFailure:
    def test_first_failure_asks_for_an_alert(self):
        s = {}
        assert state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z") is True
        assert s["login_failures"]["srt"]["since"] == "2026-07-31T10:00:00Z"

    def test_repeat_within_a_day_stays_quiet(self):
        # It fails every poll — ~48 messages/day would get the channel muted.
        s = {}
        state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z")
        assert state.record_login_failure(s, "srt", "2026-07-31T10:30:00Z") is False
        assert state.record_login_failure(s, "srt", "2026-08-01T09:00:00Z") is False

    def test_re_alerts_after_a_day(self):
        # Alerting only once means a 3am notice dismissed half-asleep is never
        # raised again while the watcher sits dead.
        s = {}
        state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z")
        assert state.record_login_failure(s, "srt", "2026-08-01T10:00:01Z") is True

    def test_since_is_preserved_across_re_alerts(self):
        s = {}
        state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z")
        state.record_login_failure(s, "srt", "2026-08-01T10:00:01Z")
        assert s["login_failures"]["srt"]["since"] == "2026-07-31T10:00:00Z"

    def test_providers_are_tracked_separately(self):
        s = {}
        assert state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z") is True
        assert state.record_login_failure(s, "korail", "2026-07-31T10:00:00Z") is True

    def test_unparseable_timestamp_does_not_spam(self):
        s = {}
        state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z")
        assert state.record_login_failure(s, "srt", "쓰레기") is False


class TestClearLoginFailure:
    def test_returns_true_only_when_it_had_been_failing(self):
        s = {}
        assert state.clear_login_failure(s, "srt") is False
        state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z")
        assert state.clear_login_failure(s, "srt") is True
        assert state.clear_login_failure(s, "srt") is False

    def test_clearing_one_provider_leaves_the_other(self):
        s = {}
        state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z")
        state.record_login_failure(s, "korail", "2026-07-31T10:00:00Z")
        state.clear_login_failure(s, "srt")
        assert set(s["login_failures"]) == {"korail"}

    def test_recovery_lets_the_next_failure_alert_again(self):
        s = {}
        state.record_login_failure(s, "srt", "2026-07-31T10:00:00Z")
        state.clear_login_failure(s, "srt")
        assert state.record_login_failure(s, "srt", "2026-07-31T10:30:00Z") is True


class TestSurvivesMerge:
    def test_login_failures_carry_through_a_state_merge(self):
        # Two runs can both write state; losing this key would silently reset
        # the alert cooldown and re-notify on every poll.
        ours = {"watches": {}, "login_failures": {"srt": {"since": "t", "notified_at": "t"}}}
        merged = state.merge_states(ours, {"watches": {}})
        assert merged["login_failures"]["srt"]["since"] == "t"
