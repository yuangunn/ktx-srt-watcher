"""Tests for worker.matcher — filter & dedupe trains against watch criteria."""
from __future__ import annotations

from worker.matcher import find_new_trains
from worker.models import Passengers, Train, Watch


def _watch(**overrides) -> Watch:
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
    }
    base.update(overrides)
    return Watch.model_validate(base)


def _train(**overrides) -> Train:
    base = dict(
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
    base.update(overrides)
    return Train(**base)


class TestFindNewTrains:
    def test_returns_all_when_no_notified(self):
        watch = _watch()
        t1 = _train(train_no="045", raw_id="2026-05-15-045-09:35")
        t2 = _train(train_no="047", dep_time="10:00", raw_id="2026-05-15-047-10:00")
        result = find_new_trains(watch, [t1, t2], notified_ids=set())
        assert result == [t1, t2]

    def test_excludes_already_notified(self):
        watch = _watch()
        t1 = _train(train_no="045", raw_id="2026-05-15-045-09:35")
        t2 = _train(train_no="047", dep_time="10:00", raw_id="2026-05-15-047-10:00")
        result = find_new_trains(watch, [t1, t2], notified_ids={"2026-05-15-045-09:35"})
        assert result == [t2]

    def test_returns_empty_when_all_notified(self):
        watch = _watch()
        t1 = _train(raw_id="rid-1")
        t2 = _train(train_no="047", raw_id="rid-2")
        result = find_new_trains(watch, [t1, t2], notified_ids={"rid-1", "rid-2"})
        assert result == []

    def test_filters_out_wrong_train_type(self):
        watch = _watch(train_types=["KTX"])
        ktx = _train(train_type="KTX", raw_id="ktx")
        mugunghwa = _train(train_type="무궁화", raw_id="mu")
        result = find_new_trains(watch, [ktx, mugunghwa], notified_ids=set())
        assert result == [ktx]

    def test_accepts_multiple_train_types(self):
        watch = _watch(train_types=["KTX", "KTX-산천"])
        a = _train(train_type="KTX", raw_id="a")
        b = _train(train_type="KTX-산천", raw_id="b")
        c = _train(train_type="ITX-새마을", raw_id="c")
        result = find_new_trains(watch, [a, b, c], notified_ids=set())
        assert result == [a, b]

    def test_filters_out_dep_time_before_min(self):
        watch = _watch(time_min="09:00", time_max="12:00")
        early = _train(dep_time="08:59", raw_id="early")
        on_min = _train(dep_time="09:00", raw_id="on_min")
        ok = _train(dep_time="10:00", raw_id="ok")
        result = find_new_trains(watch, [early, on_min, ok], notified_ids=set())
        assert result == [on_min, ok]

    def test_filters_out_dep_time_after_max(self):
        watch = _watch(time_min="09:00", time_max="12:00")
        on_max = _train(dep_time="12:00", raw_id="on_max")
        late = _train(dep_time="12:01", raw_id="late")
        result = find_new_trains(watch, [on_max, late], notified_ids=set())
        assert result == [on_max]

    def test_filters_out_wrong_date(self):
        watch = _watch(date="2026-05-15")
        right = _train(date="2026-05-15", raw_id="r")
        wrong = _train(date="2026-05-16", raw_id="w")
        result = find_new_trains(watch, [right, wrong], notified_ids=set())
        assert result == [right]

    def test_filters_out_zero_seat_trains(self):
        watch = _watch()
        sold_out = _train(seats_general=0, seats_special=0, raw_id="sold")
        available = _train(seats_general=1, raw_id="avail")
        result = find_new_trains(watch, [sold_out, available], notified_ids=set())
        assert result == [available]

    def test_general_seat_class_ignores_special_only_trains(self):
        watch = _watch(seat_class="general")
        special_only = _train(seats_general=0, seats_special=2, raw_id="sp")
        general = _train(seats_general=1, raw_id="gen")
        result = find_new_trains(watch, [special_only, general], notified_ids=set())
        assert result == [general]

    def test_special_seat_class_ignores_general_only(self):
        watch = _watch(seat_class="special")
        general_only = _train(seats_general=2, seats_special=0, raw_id="gen")
        special = _train(seats_general=0, seats_special=1, raw_id="sp")
        result = find_new_trains(watch, [general_only, special], notified_ids=set())
        assert result == [special]

    def test_any_seat_class_accepts_either(self):
        watch = _watch(seat_class="any")
        general = _train(seats_general=1, seats_special=0, raw_id="g")
        special = _train(seats_general=0, seats_special=1, raw_id="s")
        result = find_new_trains(watch, [general, special], notified_ids=set())
        assert result == [general, special]
