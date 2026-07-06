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

    def reserve(
        self,
        train: Train,
        passengers: Passengers,
        *,
        allow_waiting: bool = False,
    ) -> Reservation:
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
        is_standby = False
        try:
            rsv = self._client.reserve(raw, passengers=psgr, option=option)
        except SoldOutError as e:
            if not allow_waiting:
                raise RuntimeError(f"좌석 매진: {e}") from e
            # 대기예약 fallback: register the user on the standby list so
            # if anyone abandons their hold, the seat comes to us.
            try:
                rsv = self._client.reserve(
                    raw, passengers=psgr, option=option, try_waiting=True,
                )
                is_standby = True
            except (SoldOutError, KorailError) as e2:
                raise RuntimeError(f"좌석 매진 (대기예약도 불가): {e2}") from e2
        except KorailError as e:
            if _is_duplicate_reservation_error(e):
                return Reservation(
                    provider="korail",
                    reservation_id="(기존)",
                    train_no=train.train_no,
                    expires_at=None,
                    booking_url=LETSKORAIL_BOOKING,
                    already_existed=True,
                )
            raise RuntimeError(f"코레일 예약 오류: {e}") from e

        return Reservation(
            provider="korail",
            reservation_id=str(getattr(rsv, "rsv_id", "") or rsv),
            train_no=train.train_no,
            expires_at=None if is_standby else _korail_deadline_iso(rsv),
            booking_url=LETSKORAIL_BOOKING,
            is_standby=is_standby,
        )


def _korail_deadline_iso(rsv) -> str:
    """Build an ISO 8601 KST timestamp from korail2's buy_limit_* fields.

    korail2.Reservation exposes `buy_limit_date` (YYYYMMDD) and
    `buy_limit_time` (HHMMSS), both already in KST per Korail's response.
    Fallback to "now + 20 min UTC" if those fields are missing or malformed
    (older korail2 versions, or unexpected response shape).
    """
    buy_dt = getattr(rsv, "buy_limit_date", None)
    buy_tm = getattr(rsv, "buy_limit_time", None)
    if isinstance(buy_dt, str) and isinstance(buy_tm, str) and len(buy_dt) == 8 and len(buy_tm) >= 4:
        try:
            return (
                f"{buy_dt[0:4]}-{buy_dt[4:6]}-{buy_dt[6:8]}T"
                f"{buy_tm[0:2]}:{buy_tm[2:4]}:{buy_tm[4:6] if len(buy_tm) >= 6 else '00'}"
                f"+09:00"
            )
        except (IndexError, ValueError):
            pass
    return (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_HOLD_MIN)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _is_duplicate_reservation_error(e: Exception) -> bool:
    msg = str(e)
    return "WRR800029" in msg or "동일한 예약" in msg


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
