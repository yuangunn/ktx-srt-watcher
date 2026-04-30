from __future__ import annotations

from korail2 import (
    AdultPassenger,
    ChildPassenger,
    Korail,
    NeedToLoginError,
    NoResultsError,
    SeniorPassenger,
)

from ..models import Passengers, Reservation, Train, Watch

LETSKORAIL_BOOKING = "https://www.letskorail.com"


class KorailProvider:
    name = "korail"

    def __init__(self) -> None:
        self._client: Korail | None = None

    def login(self, user_id: str, password: str) -> None:
        self._client = Korail(user_id, password, auto_login=False)
        self._client.login(user_id, password)

    def search(self, watch: Watch) -> list[Train]:
        if self._client is None:
            raise RuntimeError("KorailProvider.search called before login")

        date_str = watch.date.replace("-", "")
        time_str = watch.time_min.replace(":", "") + "00"
        passengers = _build_passengers(watch.passengers)

        try:
            raws = self._client.search_train_allday(
                dep=watch.from_,
                arr=watch.to,
                date=date_str,
                time=time_str,
                passengers=passengers,
                include_no_seats=False,
            )
        except (NoResultsError, NeedToLoginError):
            return []

        result: list[Train] = []
        for r in raws:
            dep_time = _fmt_time(r.dep_time)
            train_type = r.train_type_name
            if train_type not in watch.train_types:
                continue
            if not (watch.time_min <= dep_time <= watch.time_max):
                continue
            seats_g = 1 if r.has_general_seat() else 0
            seats_s = 1 if r.has_special_seat() else 0
            if seats_g + seats_s == 0:
                continue
            result.append(
                Train(
                    provider="korail",
                    train_no=str(r.train_no),
                    train_type=train_type,
                    dep_station=r.dep_name,
                    arr_station=r.arr_name,
                    date=watch.date,
                    dep_time=dep_time,
                    arr_time=_fmt_time(r.arr_time),
                    seats_general=seats_g,
                    seats_special=seats_s,
                    raw_id=f"{watch.date}-{r.train_no}-{dep_time}",
                    booking_url=LETSKORAIL_BOOKING,
                )
            )
        return result

    def reserve(self, train: Train) -> Reservation:
        raise NotImplementedError("auto_reserve is Phase 3 — not implemented yet")


def _build_passengers(p: Passengers) -> list:
    out: list = []
    if p.adult > 0:
        out.append(AdultPassenger(p.adult))
    if p.child > 0:
        out.append(ChildPassenger(p.child))
    if p.senior > 0:
        out.append(SeniorPassenger(p.senior))
    if not out:
        out.append(AdultPassenger(1))
    return out


def _fmt_time(s: str | int) -> str:
    padded = str(s).zfill(6)
    return f"{padded[0:2]}:{padded[2:4]}"
