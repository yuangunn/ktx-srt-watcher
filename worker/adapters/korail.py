"""The one adapter left after the 2026-09-01 KORAIL–SR merger.

Since the merger every high-speed train — including what used to be SRT,
now branded KTX-산천 — sells through KORAIL's backend, and the SRT app and
API were shut down on 2026-08-31. One account, one search, one adapter.

Built on pykorail (MIT, PyPI), which speaks the 코레일+ protocol: the old
korail2 requests were refused wholesale after the merger with "앱을 최신
버전으로 업데이트..." (MACRO ERROR), and presenting a current app version
did not help — the backend fingerprints the TLS handshake itself, which is
why pykorail depends on curl-cffi impersonation and why patching korail2
was never going to work.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from pykorail import (
    AdultPassenger,
    ChildPassenger,
    Korail,
    KorailError,
    NoResultsError,
    PastDepartureError,
    ReserveOption,
    SeniorPassenger,
    SoldOutError,
)
from pykorail.device import profile_by_id, random_profile

from ..models import Passengers, Reservation, Train, Watch
from .base import journey_key

log = logging.getLogger("ticket_watcher")

LETSKORAIL_BOOKING = "https://www.letskorail.com"
RESERVATION_HOLD_MIN = 20


class KorailProvider:
    name = "korail"

    def __init__(self, device_profile_id: str | None = None) -> None:
        self._client: Korail | None = None
        # pykorail's own guidance: presenting a different device on every run
        # is what looks unnatural. We poll 20+ times a day from fresh
        # processes, so the profile is minted once, persisted in state by
        # main(), and re-presented on every later run.
        profile = profile_by_id(device_profile_id) if device_profile_id else None
        self._profile = profile or random_profile()
        self.device_profile_id: str = self._profile.id

    def login(self, user_id: str, password: str) -> None:
        self._client = Korail(device_profile=self._profile)
        self._client.login(user_id, password)

    def search(self, watch: Watch) -> list[Train]:
        if self._client is None:
            raise RuntimeError("KorailProvider.search called before login")

        y, m, d = (int(p) for p in watch.date.split("-"))
        hh, mm = (int(p) for p in watch.time_min.split(":"))
        # Naive datetimes are read as KST by pykorail regardless of the
        # machine's timezone, which is exactly what a watch means.
        depart_after = datetime(y, m, d, hh, mm)

        try:
            raws = self._client.trains.search(
                watch.from_, watch.to,
                depart_after=depart_after,
                passengers=_build_passengers(watch.passengers),
            )
        except NoResultsError:
            return []
        except PastDepartureError:
            # The watch's whole window is in the past; the daily prune will
            # drop it, and there is nothing to find meanwhile.
            return []

        result: list[Train] = []
        for r in raws:
            dep_time = _fmt_time(r.dep_time)
            if r.train_type_name not in watch.train_types:
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
                train_type=r.train_type_name,
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
        try:
            rsv = self._client.reservations.create(raw, passengers=psgr, option=option)
        except SoldOutError as e:
            # pykorail joins the waiting list by itself when one is open, so
            # reaching here means no seats AND no waiting list. Nothing to
            # escalate to — allow_waiting or not, this train is gone.
            raise RuntimeError(f"좌석 매진: {e}") from e
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

        if rsv.is_waiting and not allow_waiting:
            # A race: the seat vanished between search and create, and create
            # silently fell through to the waiting list — which the user has
            # switched off. Booking them a waitlist spot they said no to is
            # worse than missing the seat, so undo it and report sold out.
            try:
                self._client.reservations.cancel(rsv)
            except Exception as e:
                log.warning("원치 않는 예약대기 취소 실패 (수동 취소 필요): %s", e)
            raise RuntimeError("좌석 매진 (대기예약은 설정에서 꺼져 있음)")

        return Reservation(
            provider="korail",
            reservation_id=str(rsv.rsv_id),
            train_no=train.train_no,
            expires_at=None if rsv.is_waiting else _deadline_iso(rsv),
            booking_url=LETSKORAIL_BOOKING,
            is_standby=bool(rsv.is_waiting),
        )

    def paid_reservation_keys(self) -> set[str] | None:
        """Issued (paid) tickets, by PNR and by journey.

        Unlike korail2's Ticket, pykorail's carries the PNR (pnr_no) and a
        full Train reference — so the id match works again, and the journey
        key stays as a second witness for holds recorded before this
        migration.
        """
        if self._client is None:
            return None
        keys: set[str] = set()
        try:
            for t in self._client.tickets.all():
                if t.pnr_no:
                    keys.add(str(t.pnr_no))
                key = journey_key(
                    getattr(t.train, "train_no", None), getattr(t.train, "dep_date", None),
                )
                if key:
                    keys.add(key)
        except Exception as e:
            log.warning("코레일 발권 내역 조회 실패 — 결제 여부 판단 불가: %s", e)
            return None
        return keys


def _deadline_iso(rsv) -> str:
    """ISO 8601 KST from pykorail's buy_limit_* (same shape korail2 had)."""
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
    return "WRR800029" in msg or "동일한 예약" in msg or "이미 예약" in msg


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
