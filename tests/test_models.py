"""Tests for worker.models — pydantic v2 schemas for Watch / Train / Reservation."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from worker.models import Passengers, Reservation, Train, Watch


VALID_WATCH_DICT = {
    "id": "seoul-busan-20260515-am",
    "provider": "korail",
    "from": "서울",
    "to": "부산",
    "date": "2026-05-15",
    "time_min": "09:00",
    "time_max": "12:00",
    "train_types": ["KTX", "KTX-산천"],
    "passengers": {"adult": 1, "child": 0, "senior": 0},
    "seat_class": "general",
    "auto_reserve": False,
    "active": True,
}


class TestWatch:
    def test_parses_valid_config_dict(self):
        watch = Watch.model_validate(VALID_WATCH_DICT)
        assert watch.id == "seoul-busan-20260515-am"
        assert watch.provider == "korail"
        assert watch.from_ == "서울"
        assert watch.to == "부산"
        assert watch.train_types == ["KTX", "KTX-산천"]

    def test_from_alias_round_trip(self):
        watch = Watch.model_validate(VALID_WATCH_DICT)
        dumped = watch.model_dump(by_alias=True)
        assert "from" in dumped
        assert "from_" not in dumped
        assert dumped["from"] == "서울"

    def test_rejects_unknown_provider(self):
        bad = {**VALID_WATCH_DICT, "provider": "letskorail"}
        with pytest.raises(ValidationError):
            Watch.model_validate(bad)

    def test_rejects_invalid_date_format(self):
        bad = {**VALID_WATCH_DICT, "date": "20260515"}
        with pytest.raises(ValidationError):
            Watch.model_validate(bad)

    def test_rejects_invalid_time_format(self):
        bad = {**VALID_WATCH_DICT, "time_min": "9am"}
        with pytest.raises(ValidationError):
            Watch.model_validate(bad)

    def test_rejects_time_min_after_time_max(self):
        bad = {**VALID_WATCH_DICT, "time_min": "13:00", "time_max": "09:00"}
        with pytest.raises(ValidationError):
            Watch.model_validate(bad)

    def test_passengers_default_to_one_adult(self):
        minimal = {
            "id": "x",
            "provider": "srt",
            "from": "수서",
            "to": "부산",
            "date": "2026-05-15",
            "time_min": "09:00",
            "time_max": "12:00",
            "train_types": ["SRT"],
        }
        watch = Watch.model_validate(minimal)
        assert watch.passengers.adult == 1
        assert watch.passengers.child == 0
        assert watch.seat_class == "general"
        assert watch.auto_reserve is False
        assert watch.active is True


class TestTrain:
    def test_minimal_train(self):
        train = Train(
            provider="korail",
            train_no="045",
            train_type="KTX",
            dep_station="서울",
            arr_station="부산",
            date="2026-05-15",
            dep_time="09:35",
            arr_time="12:10",
            seats_general=2,
            seats_special=0,
            raw_id="2026-05-15-045-09:35",
        )
        assert train.total_seats == 2

    def test_total_seats_sums_general_and_special(self):
        train = Train(
            provider="srt",
            train_no="301",
            train_type="SRT",
            dep_station="수서",
            arr_station="부산",
            date="2026-05-15",
            dep_time="18:00",
            arr_time="20:30",
            seats_general=1,
            seats_special=2,
            raw_id="2026-05-15-301-18:00",
        )
        assert train.total_seats == 3


class TestReservation:
    def test_minimal_reservation(self):
        r = Reservation(
            provider="korail",
            reservation_id="ABC123",
            train_no="045",
            expires_at=None,
        )
        assert r.reservation_id == "ABC123"
        assert r.expires_at is None


class TestPassengers:
    def test_defaults(self):
        p = Passengers()
        assert p.adult == 1
        assert p.child == 0
        assert p.senior == 0

    def test_rejects_negative(self):
        with pytest.raises(ValidationError):
            Passengers(adult=-1)
