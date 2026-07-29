"""Tests for worker.pushover — the channel that can bypass iOS silent mode."""
from __future__ import annotations

import pytest

from worker import pushover


class _Resp:
    def __init__(self, ok: bool = True):
        self._ok = ok

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("boom")


class _Session:
    def __init__(self, ok: bool = True):
        self.calls: list[dict] = []
        self._ok = ok

    def post(self, url, *, data, timeout):
        self.calls.append({"url": url, "data": data, "timeout": timeout})
        return _Resp(self._ok)


@pytest.fixture
def _creds(monkeypatch):
    monkeypatch.setenv("PUSHOVER_TOKEN", "app-token")
    monkeypatch.setenv("PUSHOVER_USER", "user-key")


class TestEnabled:
    def test_false_without_credentials(self, monkeypatch):
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        assert pushover.enabled() is False

    def test_false_with_only_one_of_the_pair(self, monkeypatch):
        monkeypatch.setenv("PUSHOVER_TOKEN", "t")
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        assert pushover.enabled() is False

    def test_true_with_both(self, _creds):
        assert pushover.enabled() is True


class TestSend:
    def test_no_op_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("PUSHOVER_TOKEN", raising=False)
        monkeypatch.delenv("PUSHOVER_USER", raising=False)
        sess = _Session()
        pushover.send("t", "m", session=sess)
        assert sess.calls == []

    def test_posts_credentials_and_body(self, _creds):
        sess = _Session()
        pushover.send("제목", "본문", session=sess)
        data = sess.calls[0]["data"]
        assert data["token"] == "app-token"
        assert data["user"] == "user-key"
        assert data["title"] == "제목"
        assert data["message"] == "본문"

    def test_high_priority_sends_no_retry_fields(self, _creds):
        # retry/expire are only meaningful (and only accepted) at priority 2.
        sess = _Session()
        pushover.send("t", "m", priority=pushover.PRIORITY_HIGH, session=sess)
        data = sess.calls[0]["data"]
        assert data["priority"] == 1
        assert "retry" not in data
        assert "expire" not in data

    def test_emergency_priority_repeats_until_acknowledged(self, _creds):
        sess = _Session()
        pushover.send("t", "m", priority=pushover.PRIORITY_EMERGENCY, session=sess)
        data = sess.calls[0]["data"]
        assert data["priority"] == 2
        assert data["retry"] == pushover.RETRY_SEC
        assert data["expire"] == pushover.EXPIRE_SEC

    def test_expire_stays_inside_the_payment_window(self):
        # A hold lasts ~20 min. An alert still ringing after the seat is gone
        # only teaches the user to ignore it.
        assert pushover.EXPIRE_SEC < 20 * 60

    def test_retry_meets_pushover_minimum(self):
        assert pushover.RETRY_SEC >= 60

    def test_failure_never_raises(self, _creds):
        # Telegram has already delivered by this point; a Pushover outage must
        # not take down the run.
        pushover.send("t", "m", session=_Session(ok=False))

    def test_optional_url_included_only_when_given(self, _creds):
        sess = _Session()
        pushover.send("t", "m", session=sess)
        assert "url" not in sess.calls[0]["data"]
        pushover.send("t", "m", url="https://example.com", session=sess)
        assert sess.calls[1]["data"]["url"] == "https://example.com"


class TestAlertMode:
    """Away, an emergency alert must not ring at full volume in a lecture —
    but it must still arrive."""

    def setup_method(self):
        pushover.set_mode("home")

    def teardown_method(self):
        pushover.set_mode("home")

    def test_away_downgrades_emergency_to_high(self):
        pushover.set_mode("away")
        assert pushover.effective_priority(pushover.PRIORITY_EMERGENCY) == pushover.PRIORITY_HIGH

    def test_away_leaves_high_alone(self):
        pushover.set_mode("away")
        assert pushover.effective_priority(pushover.PRIORITY_HIGH) == pushover.PRIORITY_HIGH

    def test_home_keeps_emergency(self):
        pushover.set_mode("home")
        assert pushover.effective_priority(pushover.PRIORITY_EMERGENCY) == pushover.PRIORITY_EMERGENCY

    def test_unknown_mode_falls_back_to_home(self):
        # A garbled value must not silently mute the alert that matters.
        pushover.set_mode("nonsense")
        assert pushover.effective_priority(pushover.PRIORITY_EMERGENCY) == pushover.PRIORITY_EMERGENCY

    def test_away_send_drops_retry_fields(self, _creds):
        pushover.set_mode("away")
        sess = _Session()
        pushover.send("t", "m", priority=pushover.PRIORITY_EMERGENCY, session=sess)
        data = sess.calls[0]["data"]
        assert data["priority"] == pushover.PRIORITY_HIGH
        assert "retry" not in data
        assert "expire" not in data


class TestTestAlertExpiry:
    """A test alert must prove it cuts through silence without then hounding
    the user for 15 minutes — nobody presses that button twice."""

    def test_expire_override_is_used(self, _creds):
        sess = _Session()
        pushover.send(
            "t", "m",
            priority=pushover.PRIORITY_EMERGENCY,
            expire_sec=pushover.TEST_EXPIRE_SEC,
            session=sess,
        )
        assert sess.calls[0]["data"]["expire"] == pushover.TEST_EXPIRE_SEC

    def test_real_alerts_keep_the_full_window(self, _creds):
        sess = _Session()
        pushover.send("t", "m", priority=pushover.PRIORITY_EMERGENCY, session=sess)
        assert sess.calls[0]["data"]["expire"] == pushover.EXPIRE_SEC

    def test_test_window_is_short_but_repeats_at_least_once(self):
        assert pushover.TEST_EXPIRE_SEC < pushover.EXPIRE_SEC
        assert pushover.TEST_EXPIRE_SEC >= pushover.RETRY_SEC * 2


class TestStopHint:
    """Whoever the phone wakes may not know what Pushover is. A repeating
    alert must carry its own off switch."""

    def test_emergency_message_explains_how_to_stop(self, _creds):
        sess = _Session()
        pushover.send("t", "좌석 발견", priority=pushover.PRIORITY_EMERGENCY, session=sess)
        msg = sess.calls[0]["data"]["message"]
        assert msg.startswith("좌석 발견")
        assert "Acknowledge" in msg

    def test_high_priority_message_is_left_alone(self, _creds):
        # A one-shot alert has nothing to stop; the hint would just be noise.
        sess = _Session()
        pushover.send("t", "좌석 발견", priority=pushover.PRIORITY_HIGH, session=sess)
        assert sess.calls[0]["data"]["message"] == "좌석 발견"

    def test_away_downgrade_drops_the_hint_too(self, _creds):
        # Downgraded to high, it no longer repeats — so it must not claim to.
        pushover.set_mode("away")
        try:
            sess = _Session()
            pushover.send("t", "좌석 발견", priority=pushover.PRIORITY_EMERGENCY, session=sess)
            assert sess.calls[0]["data"]["message"] == "좌석 발견"
        finally:
            pushover.set_mode("home")
