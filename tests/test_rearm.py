"""Auto-reserve re-arm: a hold that lapses unpaid must resume hunting.

Regression guard for the overnight failure mode — reserve succeeds at 02:00,
the 20-min payment window passes while the user sleeps, the seat is released,
and auto-reserve must NOT stay disabled.
"""
from worker import main
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

    def paid_reservation_keys(self):
        return self._paid


def _state(deadline="2026-08-01T02:20:00Z", rsv="R1", journey="KTX045|20260817"):
    rec = {"id": rsv, "deadline": deadline, "journey": journey}
    return {
        "auto_reserve_disabled": ["w1"],
        "pending_reservations": {"w1": rec},
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


class TestUnknownNeverRearms:
    """The expensive direction.

    This used to assert the opposite — that a failed lookup should still
    re-arm, so as not to "strand" the watch. That reasoning weighed a missed
    re-hunt against nothing, when the real cost on the other side is buying a
    second ticket for a seat the user has already paid for. A paused watch is
    visible in the PWA and one tap to re-arm; a duplicate booking is money and
    a cancellation. No evidence, no re-arm.
    """

    def test_lookup_exception_leaves_the_hold_alone(self):
        class Boom(FakeProvider):
            def paid_reservation_keys(self):
                raise RuntimeError("api down")

        s = _state()
        main._resolve_pending_reservations(Boom(), [_watch()], s, "2026-08-01T02:30:00Z")
        assert s["auto_reserve_disabled"] == ["w1"]
        assert s["pending_reservations"]["w1"]["id"] == "R1"

    def test_lookup_returning_none_leaves_the_hold_alone(self):
        # None is the adapters' "could not determine". An empty set means
        # "checked, nothing is paid" and must stay distinguishable from it.
        class Unknown(FakeProvider):
            def paid_reservation_keys(self):
                return None

        s = _state()
        main._resolve_pending_reservations(Unknown(), [_watch()], s, "2026-08-01T02:30:00Z")
        assert s["auto_reserve_disabled"] == ["w1"]
        assert s["pending_reservations"]["w1"]["id"] == "R1"

    def test_empty_set_still_rearms(self):
        # The lookup worked and the seat really is gone unpaid — hunt again.
        s = _state()
        main._resolve_pending_reservations(
            FakeProvider(paid=[]), [_watch()], s, "2026-08-01T02:30:00Z")
        assert s["auto_reserve_disabled"] == []


class TestJourneyMatch:
    """Korail issues tickets with no PNR on them, so the reservation id we
    stored can never appear in the paid set. Matching the journey is what
    actually settles whether the user holds that seat."""

    def test_journey_key_counts_as_paid_even_though_the_id_does_not(self):
        s = _state()
        rearmed = []
        main._resolve_pending_reservations(
            FakeProvider(paid=["KTX045|20260817"]), [_watch()], s,
            "2026-08-01T02:30:00Z", notify_rearmed_fn=lambda w: rearmed.append(w.id))
        assert s["auto_reserve_disabled"] == ["w1"]   # seat secured, stay off
        assert rearmed == []

    def test_a_different_journey_does_not_count(self):
        s = _state()
        main._resolve_pending_reservations(
            FakeProvider(paid=["KTX999|20260817"]), [_watch()], s, "2026-08-01T02:30:00Z")
        assert s["auto_reserve_disabled"] == []


class TestLegacyHoldsWithoutJourney:
    """Holds written before journey keys existed cannot be judged on Korail,
    so they get the same no-evidence-no-re-arm treatment."""

    def test_legacy_hold_is_not_rearmed(self):
        s = _state(journey="")
        main._resolve_pending_reservations(
            FakeProvider(), [_watch()], s, "2026-08-01T02:30:00Z")
        assert s["auto_reserve_disabled"] == ["w1"]

    def test_legacy_hold_is_dropped_once_long_dead(self):
        # Bounded so state does not accumulate; auto-reserve still stays off.
        s = _state(journey="")
        main._resolve_pending_reservations(
            FakeProvider(), [_watch()], s, "2026-08-02T05:00:00Z")
        assert "w1" not in s.get("pending_reservations", {})
        assert s["auto_reserve_disabled"] == ["w1"]


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
