from __future__ import annotations

from datetime import datetime, timedelta, timezone

from SRT import SRT, Adult, Child, SeatType, Senior, SRTNotLoggedInError, SRTResponseError

from ..models import Passengers, Reservation, Train, Watch

SRAIL_BOOKING = "https://etk.srail.kr"
RESERVATION_HOLD_MIN = 20


class SRTProvider:
    name = "srt"

    def __init__(self) -> None:
        self._client: SRT | None = None

    def login(self, user_id: str, password: str) -> None:
        self._client = SRT(user_id, password, auto_login=False)
        self._client.login(user_id, password)

    def search(self, watch: Watch) -> list[Train]:
        if self._client is None:
            raise RuntimeError("SRTProvider.search called before login")

        date_str = watch.date.replace("-", "")
        time_str = watch.time_min.replace(":", "") + "00"

        try:
            raws = self._client.search_train(
                dep=watch.from_,
                arr=watch.to,
                date=date_str,
                time=time_str,
                available_only=True,
            )
        except (SRTResponseError, SRTNotLoggedInError):
            return []

        result: list[Train] = []
        for r in raws:
            dep_time = _fmt_time(r.dep_time)
            if "SRT" not in watch.train_types and r.train_name not in watch.train_types:
                continue
            if not (watch.time_min <= dep_time <= watch.time_max):
                continue
            seats_g = 1 if r.general_seat_available() else 0
            seats_s = 1 if r.special_seat_available() else 0
            if seats_g + seats_s == 0:
                continue
            t = Train(
                provider="srt",
                train_no=str(r.train_number),
                train_type=r.train_name,
                dep_station=r.dep_station_name,
                arr_station=r.arr_station_name,
                date=watch.date,
                dep_time=dep_time,
                arr_time=_fmt_time(r.arr_time),
                seats_general=seats_g,
                seats_special=seats_s,
                raw_id=f"{watch.date}-{r.train_number}-{dep_time}",
                booking_url=SRAIL_BOOKING,
                seat_class=watch.seat_class,
            )
            t._raw = r
            result.append(t)
        return result

    def reserve(self, train: Train, passengers: Passengers) -> Reservation:
        if self._client is None:
            raise RuntimeError("SRTProvider.reserve called before login")
        raw = train._raw
        if raw is None:
            raise RuntimeError(
                "train.raw not set — provider.reserve() must be called on a Train returned "
                "by the same provider's search() in the same login session"
            )
        psgr = _build_passengers(passengers)
        seat_type = _seat_type(train.seat_class)
        try:
            rsv = self._client.reserve(raw, passengers=psgr, special_seat=seat_type)
        except (SRTResponseError, SRTNotLoggedInError) as e:
            if _is_duplicate_reservation_error(e):
                return Reservation(
                    provider="srt",
                    reservation_id="(기존)",
                    train_no=train.train_no,
                    expires_at=None,
                    booking_url=SRAIL_BOOKING,
                    already_existed=True,
                )
            raise RuntimeError(f"SRT 예약 오류: {e}") from e

        return Reservation(
            provider="srt",
            reservation_id=str(getattr(rsv, "reservation_number", "") or rsv),
            train_no=train.train_no,
            expires_at=_srt_deadline_iso(rsv),
            booking_url=SRAIL_BOOKING,
        )


def _srt_deadline_iso(rsv) -> str:
    """Build an ISO 8601 KST timestamp from SRTReservation's payment_*.

    SRTReservation exposes `payment_date` (YYYYMMDD) and `payment_time`
    (HHMMSS), both already in KST.  Fallback to "now + 20 min UTC" when
    fields are missing or malformed.
    """
    pay_dt = getattr(rsv, "payment_date", None)
    pay_tm = getattr(rsv, "payment_time", None)
    if isinstance(pay_dt, str) and isinstance(pay_tm, str) and len(pay_dt) == 8 and len(pay_tm) >= 4:
        try:
            return (
                f"{pay_dt[0:4]}-{pay_dt[4:6]}-{pay_dt[6:8]}T"
                f"{pay_tm[0:2]}:{pay_tm[2:4]}:{pay_tm[4:6] if len(pay_tm) >= 6 else '00'}"
                f"+09:00"
            )
        except (IndexError, ValueError):
            pass
    return (datetime.now(timezone.utc) + timedelta(minutes=RESERVATION_HOLD_MIN)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _is_duplicate_reservation_error(e: Exception) -> bool:
    msg = str(e)
    # SRT uses different error codes; match the substring that's stable
    # across both libraries' error wording.
    return "동일한 예약" in msg or "이미 예약" in msg or "duplicate" in msg.lower()


def _seat_type(seat_class: str) -> "SeatType":
    if seat_class == "general":
        return SeatType.GENERAL_ONLY
    if seat_class == "special":
        return SeatType.SPECIAL_ONLY
    return SeatType.GENERAL_FIRST


def _build_passengers(p: Passengers) -> list:
    out: list = []
    if p.adult > 0:
        out.append(Adult(p.adult))
    if p.child > 0:
        out.append(Child(p.child))
    if p.senior > 0:
        out.append(Senior(p.senior))
    if not out:
        out.append(Adult(1))
    return out


def _fmt_time(s: str | int) -> str:
    padded = str(s).zfill(6)
    return f"{padded[0:2]}:{padded[2:4]}"
