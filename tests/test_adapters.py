"""Smoke tests for the KORAIL adapter — full integration needs a live account.

Since the 2026-09-01 merger this is the only adapter: SRT's app and API shut
down 2026-08-31 and its trains sell through KORAIL as KTX-산천.
"""
from __future__ import annotations

import pytest

from worker.adapters import korail
from worker.adapters.base import Provider
from worker.models import Passengers, Watch


class TestModuleImports:
    def test_korail_provider_is_instantiable(self):
        provider = korail.KorailProvider()
        assert provider.name == "korail"

    def test_korail_satisfies_provider_protocol(self):
        provider: Provider = korail.KorailProvider()
        assert callable(provider.login)
        assert callable(provider.search)
        assert callable(provider.reserve)
        assert callable(provider.paid_reservation_keys)


class TestDeviceProfilePinning:
    def test_fresh_provider_mints_a_profile_id(self):
        assert korail.KorailProvider().device_profile_id

    def test_saved_id_is_reused(self):
        # 20+ polls a day each presenting a different device is what looks
        # like a bot farm; the id must round-trip through state.
        first = korail.KorailProvider()
        again = korail.KorailProvider(device_profile_id=first.device_profile_id)
        assert again.device_profile_id == first.device_profile_id

    def test_unknown_id_falls_back_to_a_new_profile(self):
        provider = korail.KorailProvider(device_profile_id="no-such-profile")
        assert provider.device_profile_id


class TestKorailHelpers:
    def test_format_time_pads_six_digit(self):
        assert korail._fmt_time("093500") == "09:35"

    def test_format_time_pads_short(self):
        assert korail._fmt_time("93500") == "09:35"

    def test_build_passengers_uses_adult_count(self):
        result = korail._build_passengers(Passengers(adult=2, child=0, senior=0))
        assert len(result) == 1
        assert result[0].count == 2

    def test_build_passengers_combines_classes(self):
        result = korail._build_passengers(Passengers(adult=1, child=1, senior=1))
        assert len(result) == 3

    def test_build_passengers_falls_back_to_one_adult_when_all_zero(self):
        result = korail._build_passengers(Passengers(adult=0, child=0, senior=0))
        assert len(result) == 1


def _watch(**overrides) -> Watch:
    base = {
        "id": "x", "provider": "korail", "from": "서울", "to": "부산",
        "date": "2026-10-15", "time_min": "09:00", "time_max": "12:00",
        "train_types": ["KTX"],
    }
    base.update(overrides)
    return Watch.model_validate(base)


class TestUnauthenticatedCallsRaise:
    def test_search_without_login_raises(self):
        with pytest.raises(RuntimeError):
            korail.KorailProvider().search(_watch())

    def test_paid_keys_without_login_are_unknown_not_empty(self):
        # None means "could not check"; an empty set would read as "nothing is
        # paid" and re-arm auto-reserve into a double booking.
        assert korail.KorailProvider().paid_reservation_keys() is None


class _FakeTrain:
    """Duck-typed stand-in for pykorail's Train."""

    def __init__(self, train_no="101", type_name="KTX", dep="093500", arr="121000",
                 general=True, special=False, dep_name="서울", arr_name="부산"):
        self.train_no = train_no
        self.train_type_name = type_name
        self.dep_time = dep
        self.arr_time = arr
        self.dep_name = dep_name
        self.arr_name = arr_name
        self._general, self._special = general, special

    def has_general_seat(self):
        return self._general

    def has_special_seat(self):
        return self._special


class _FakeTrains:
    def __init__(self, result):
        self._result = result

    def search(self, dep, arr, *, depart_after=None, passengers=None):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeClient:
    def __init__(self, trains):
        self.trains = _FakeTrains(trains)


class TestSearchMapping:
    def _provider_with(self, trains) -> korail.KorailProvider:
        p = korail.KorailProvider()
        p._client = _FakeClient(trains)
        return p

    def test_maps_pykorail_trains_to_our_model(self):
        p = self._provider_with([_FakeTrain()])
        [t] = p.search(_watch())
        assert (t.train_no, t.dep_time, t.raw_id) == ("101", "09:35", "2026-10-15-101-09:35")
        assert t.seats_general == 1 and t.seats_special == 0

    def test_filters_by_type_window_and_seats(self):
        p = self._provider_with([
            _FakeTrain(train_no="1", type_name="무궁화호"),          # wrong type
            _FakeTrain(train_no="2", dep="130000"),                  # past window
            _FakeTrain(train_no="3", general=False, special=False),  # no seats
            _FakeTrain(train_no="4", type_name="KTX-산천"),          # wrong type for this watch
            _FakeTrain(train_no="5"),                                # keeper
        ])
        assert [t.train_no for t in p.search(_watch())] == ["5"]

    def test_ktx_sancheon_watch_matches_ex_srt_trains(self):
        # The shape a migrated SRT watch takes: 수서 route, KTX-산천 type.
        p = self._provider_with([_FakeTrain(type_name="KTX-산천", dep_name="수서", arr_name="부산")])
        watch = _watch(**{"from": "수서", "train_types": ["KTX-산천"]})
        assert len(p.search(watch)) == 1

    def test_no_results_is_empty_not_an_error(self):
        from pykorail import NoResultsError
        p = self._provider_with(NoResultsError("WRD000061"))
        assert p.search(_watch()) == []

    def test_past_departure_is_empty_not_an_error(self):
        from datetime import datetime
        from pykorail import PastDepartureError
        p = self._provider_with(PastDepartureError(datetime(2020, 1, 1), datetime(2026, 1, 1)))
        assert p.search(_watch()) == []


class TestPaidKeys:
    def test_keys_carry_pnr_and_journey(self):
        class _Tk:
            pnr_no = "PNR123"
            train = _FakeTrain(train_no="101")

        _Tk.train.dep_date = "20261015"

        class _Tickets:
            def all(self):
                return [_Tk()]

        p = korail.KorailProvider()
        p._client = type("C", (), {"tickets": _Tickets()})()
        assert p.paid_reservation_keys() == {"PNR123", "101|20261015"}

    def test_lookup_failure_is_none(self):
        class _Tickets:
            def all(self):
                raise RuntimeError("down")

        p = korail.KorailProvider()
        p._client = type("C", (), {"tickets": _Tickets()})()
        assert p.paid_reservation_keys() is None
