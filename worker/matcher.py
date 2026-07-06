from __future__ import annotations

from .models import Train, Watch


def find_new_trains(watch: Watch, trains: list[Train], notified_ids: set[str]) -> list[Train]:
    return [t for t in trains if _matches(t, watch) and t.raw_id not in notified_ids]


def _matches(train: Train, watch: Watch) -> bool:
    if train.date != watch.date:
        return False
    if train.train_type not in watch.train_types:
        return False
    if not (watch.time_min <= train.dep_time <= watch.time_max):
        return False
    return _has_required_seats(train, watch.seat_class)


def _has_required_seats(train: Train, seat_class: str) -> bool:
    if seat_class == "general":
        return train.seats_general > 0
    if seat_class == "special":
        return train.seats_special > 0
    return train.total_seats > 0
