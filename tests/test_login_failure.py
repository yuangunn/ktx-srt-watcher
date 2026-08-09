"""A dead Korail/SRT login is the quietest way this system can fail: the run
still succeeds and last_run still advances, so nothing else notices. These pin
the alert-once / recover / re-alert-daily behaviour."""
from __future__ import annotations

from worker import notifier, state


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


class TestPushoverIsOptIn:
    """Pushover priority 1 bypasses its own quiet hours, so a login that broke
    at 3am used to wake the user for something they cannot fix until morning.
    Telegram always; Pushover only when asked for."""

    def _sent(self, monkeypatch):
        calls = []
        monkeypatch.setattr(notifier.pushover, "send",
                            lambda *a, **kw: calls.append((a, kw)))
        monkeypatch.setattr(notifier, "send_telegram", lambda *a, **kw: None)
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "c")
        return calls

    def test_failure_is_telegram_only_by_default(self, monkeypatch):
        calls = self._sent(monkeypatch)
        notifier.notify_login_failed("korail", "bad password")
        assert calls == []

    def test_recovery_is_telegram_only_by_default(self, monkeypatch):
        calls = self._sent(monkeypatch)
        notifier.notify_login_recovered("korail")
        assert calls == []

    def test_failure_pushes_when_opted_in(self, monkeypatch):
        calls = self._sent(monkeypatch)
        notifier.notify_login_failed("korail", "bad password", push=True)
        assert len(calls) == 1
        # High, never emergency: no repeat-until-acknowledged for a password.
        assert calls[0][1]["priority"] == notifier.pushover.PRIORITY_HIGH

    def test_recovery_pushes_when_opted_in(self, monkeypatch):
        calls = self._sent(monkeypatch)
        notifier.notify_login_recovered("korail", push=True)
        assert len(calls) == 1

    def test_telegram_still_goes_out_with_push_off(self, monkeypatch):
        self._sent(monkeypatch)
        sent = []
        monkeypatch.setattr(notifier, "send_telegram",
                            lambda tok, cid, text, **kw: sent.append(text))
        notifier.notify_login_failed("srt", "locked")
        assert len(sent) == 1
        assert "SRT 로그인 실패" in sent[0]
