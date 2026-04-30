from __future__ import annotations

from datetime import datetime, timedelta, timezone

from korail2 import (
    AdultPassenger,
    ChildPassenger,
    Korail,
    KorailError,
    NeedToLoginError,
    NoResultsError,
    ReserveOption,
    SeniorPassenger,
    SoldOutError,
)

from ..models import Passengers, Reservation, Train, Watch

LETSKORAIL_BOOKING = "https://www.letskorail.com"
RESERVATION_HOLD_MIN = 20


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
            t = Train(
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
                seat_class=watch.seat_class,
            )
            t._raw = r
            result.append(t)
        return result

    def reserve(self, train: Train, passengers: Passengers) -> Reservation:
        if self._client is None:
            raise RuntimeError("KorailProvider.reserve called before login")
        raw = train._raw
        if raw is None:
            raise RuntimeError(
                "train.raw not set — provider.reserve() must be called on a Train returned "
                "by the same provider's search() in the same login session"
            )
        psgr = _build_passengers(passengers)
        option = _reserve_option(train.seat_class)
        try:
            rsv = self._client.reserve(raw, passengers=psgr, option=option)
        except SoldOutError as e:
            raise RuntimeError(f"좌석 매진: {e}") from e
        except KorailError as e:
            raise RuntimeError(f"코레일 예약 오류: {e}") from e

        expires_at = (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_HOLD_MIN)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        return Reservation(
            provider="korail",
            reservation_id=str(getattr(rsv, "rsv_id", "") or rsv),
            train_no=train.train_no,
            expires_at=expires_at,
            booking_url=LETSKORAIL_BOOKING,
        )


def _reserve_option(seat_class: str) -> "ReserveOption":
    if seat_class == "general":
        return ReserveOption.GENERAL_ONLY
    if seat_class == "special":
        return ReserveOption.SPECIAL_ONLY
    return ReserveOption.GENERAL_FIRST


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
