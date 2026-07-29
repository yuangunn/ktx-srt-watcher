"""The reminder loop cannot know whether the user has paid, so every message
carries a one-tap off switch. These pin the token derivation, since the Worker
recomputes it independently and a mismatch would make the link silently 403."""
from __future__ import annotations

import hashlib
import hmac

import pytest

from worker import notifier


@pytest.fixture
def _cf(monkeypatch):
    monkeypatch.setenv("CF_WORKER_URL", "https://worker.example/")
    monkeypatch.setenv("REMINDER_TOKEN", "s3cret")


class TestCancelUrl:
    def test_none_without_worker_url(self, monkeypatch):
        monkeypatch.delenv("CF_WORKER_URL", raising=False)
        monkeypatch.setenv("REMINDER_TOKEN", "s3cret")
        assert notifier.reminder_cancel_url("R1") is None

    def test_none_without_token(self, monkeypatch):
        monkeypatch.setenv("CF_WORKER_URL", "https://worker.example")
        monkeypatch.delenv("REMINDER_TOKEN", raising=False)
        assert notifier.reminder_cancel_url("R1") is None

    def test_none_without_reservation_id(self, _cf):
        assert notifier.reminder_cancel_url("") is None

    def test_trailing_slash_does_not_double_up(self, _cf):
        assert notifier.reminder_cancel_url("R1").startswith(
            "https://worker.example/reminder/done?id=R1&t=")

    def test_token_is_hmac_of_the_reservation_id(self, _cf):
        expected = hmac.new(b"s3cret", b"R1", hashlib.sha256).hexdigest()[:16]
        assert notifier.reminder_cancel_url("R1").endswith(f"&t={expected}")

    def test_different_reservations_get_different_tokens(self, _cf):
        # Otherwise one link would silence someone else's reminders.
        a = notifier.reminder_cancel_url("R1")
        b = notifier.reminder_cancel_url("R2")
        assert a.split("&t=")[1] != b.split("&t=")[1]

    def test_token_length_matches_the_worker(self, _cf):
        # The Worker slices its HMAC to 16 hex chars; a longer token here would
        # never compare equal.
        assert len(notifier.reminder_cancel_url("R1").split("&t=")[1]) == 16


class TestCancelIsBestEffort:
    def test_no_op_without_config(self, monkeypatch):
        monkeypatch.delenv("CF_WORKER_URL", raising=False)
        called = []
        monkeypatch.setattr(notifier.requests, "get", lambda *a, **k: called.append(1))
        notifier.cancel_reminders("R1")
        assert called == []

    def test_network_failure_never_raises(self, _cf, monkeypatch):
        # The poll it runs inside must not fail because a cleanup call did.
        def boom(*a, **k):
            raise RuntimeError("down")
        monkeypatch.setattr(notifier.requests, "get", boom)
        notifier.cancel_reminders("R1")
