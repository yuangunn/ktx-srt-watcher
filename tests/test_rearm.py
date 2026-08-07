"""Auto-reserve re-arm: a hold that lapses unpaid must resume hunting.

Regression guard for the overnight failure mode — reserve succeeds at 02:00,
the 20-min payment window passes while the user sleeps, the seat is released,
and auto-reserve must NOT stay disabled.
"""
from worker import main, state as state_mod
from worker.models import Watch


def _watch(wid="w1"):
    return Watch.model_validate({
        "id": wid, "provider": "korail", "from": "부산", "to": "수원",
        "date": "2026-08-17", "time_min": "09:00", "time_max": "12:00",
        "train_types": ["KTX"], "passengers": {"adult": 1},
        "seat_class": "general", "auto_reserve": True, "active": True,
    })


class FakeProvider:
    name = "korail"

    def __init__(self, paid=()):
        self._paid = set(paid)

    def paid_reservation_ids(self):
        return self._paid


def _state(deadline="2026-08-01T02:20:00Z", rsv="R1"):
    return {
        "auto_reserve_disabled": ["w1"],
        "pending_reservations": {"w1": {"id": rsv, "deadline": deadline}},
    }


def test_expired_unpaid_rearms():
    s = _state()
    rearmed = []
    main._resolve_pending_reservations(
        FakeProvider(), [_watch()], s, "2026-08-01T02:30:00Z",
        notify_rearmed_fn=lambda w: rearmed.append(w.id),
    )
    assert s.get("auto_reserve_disabled") == []
    assert "w1" not in s.get("pending_reservations", {})
    assert rearmed == ["w1"]


def test_paid_stays_disabled():
    s = _state()
    rearmed = []
    main._resolve_pending_reservations(
        FakeProvider(paid=["R1"]), [_watch()], s, "2026-08-01T02:30:00Z",
        notify_rearmed_fn=lambda w: rearmed.append(w.id),
    )
    assert s["auto_reserve_disabled"] == ["w1"]      # goal achieved, stay off
    assert "w1" not in s.get("pending_reservations", {})
    assert rearmed == []


def test_within_deadline_untouched():
    s = _state()
    main._resolve_pending_reservations(
        FakeProvider(), [_watch()], s, "2026-08-01T02:10:00Z")
    assert s["auto_reserve_disabled"] == ["w1"]
    assert s["pending_reservations"]["w1"]["id"] == "R1"


def test_grace_period_respected():
    # 1 min past deadline is still inside RE_ARM_GRACE_MIN → not yet re-armed
    s = _state()
    main._resolve_pending_reservations(
        FakeProvider(), [_watch()], s, "2026-08-01T02:21:00Z")
    assert s["auto_reserve_disabled"] == ["w1"]


def test_lookup_failure_still_rearms_when_expired():
    """Fail-safe: if the paid lookup errors we must NOT strand the watch."""
    class Boom(FakeProvider):
        def paid_reservation_ids(self):
            raise RuntimeError("api down")

    s = _state()
    main._resolve_pending_reservations(Boom(), [_watch()], s, "2026-08-01T02:30:00Z")
    assert s.get("auto_reserve_disabled") == []


def test_standby_without_deadline_untouched():
    s = _state(deadline=None)
    main._resolve_pending_reservations(
        FakeProvider(), [_watch()], s, "2026-08-01T02:30:00Z")
    assert s["auto_reserve_disabled"] == ["w1"]


def test_no_pending_is_noop():
    s = {"auto_reserve_disabled": ["w1"]}
    main._resolve_pending_reservations(
        FakeProvider(), [_watch()], s, "2026-08-01T02:30:00Z")
    assert s["auto_reserve_disabled"] == ["w1"]


class TestPaidCancelsReminders:
    """The paid branch must also silence queued payment reminders.

    This call was documented as the backstop to the in-message "결제 완료 —
    알림 중지" link but was missing from the merged code: the function and its
    unit tests existed, nothing ever called it. Pin the wiring, not just the
    function.
    """

    def test_paid_reservation_cancels_its_reminders(self, monkeypatch):
        cancelled = []
        monkeypatch.setattr(main.notifier, "cancel_reminders", cancelled.append)
        main._resolve_pending_reservations(
            FakeProvider(paid=["R1"]), [_watch()], _state(), "2026-08-01T02:30:00Z")
        assert cancelled == ["R1"]

    def test_unpaid_expiry_does_not_cancel(self, monkeypatch):
        # The hold lapsed and auto-reserve re-arms; there is nothing paid to
        # stop reminding about, and the reminders expire on their own.
        cancelled = []
        monkeypatch.setattr(main.notifier, "cancel_reminders", cancelled.append)
        main._resolve_pending_reservations(
            FakeProvider(), [_watch()], _state(), "2026-08-01T02:30:00Z")
        assert cancelled == []

    def test_cancel_failure_never_breaks_the_poll(self, monkeypatch):
        # Tidy-up must not take down a run that already did its job.
        def boom(_):
            raise RuntimeError("worker down")
        monkeypatch.setattr(main.notifier, "cancel_reminders", boom)
        s = _state()
        main._resolve_pending_reservations(
            FakeProvider(paid=["R1"]), [_watch()], s, "2026-08-01T02:30:00Z")
        assert "w1" not in s.get("pending_reservations", {})
