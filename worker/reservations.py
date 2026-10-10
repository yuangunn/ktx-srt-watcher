"""Auto-reserve: place a temporary hold, then settle it on later runs.

Holds only. Payment is never automated (CLAUDE.md non-goal): the user pays
in 코레일톡 within the ~20-minute window, and the CF Worker reminds them.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable

from . import reminders
from . import state as state_mod
from .adapters.base import Provider, journey_key
from .logsafe import wid as _wid
from .models import Reservation, Train, Watch
from .throttle import parse_dt as _parse_dt

log = logging.getLogger("ticket_watcher")

ReserveSuccessFn = Callable[[Watch, Train, Reservation], None]
ReserveFailureFn = Callable[[Watch, Train, str], None]
ScheduleRemindersFn = Callable[[Watch, Train, Reservation], None]

# How many candidate trains to attempt reserving when the first one(s) are
# already taken. Caps the burst of API calls into Korail per run.
MAX_RESERVE_CANDIDATES = 3

# Grace period after a hold's payment deadline before we declare it lapsed and
# re-arm auto-reserve. Covers clock skew and late payment propagation.
RE_ARM_GRACE_MIN = 3

# How long a hold we cannot judge is kept before being dropped from state.
# Long enough that a transient outage never discards a live record, short
# enough that stale entries do not pile up.
STALE_HOLD_HOURS = 24


# ----- settling earlier holds -----------------------------------------------

def resolve_pending_reservations(
    provider: Provider,
    watches: list[Watch],
    state: dict[str, Any],
    now_iso: str,
    *,
    notify_rearmed_fn: Callable[[Watch], None] | None = None,
) -> None:
    """Decide the fate of holds this provider placed on earlier runs.

    A reservation is only a *hold* until it's paid (~20 min). Previously any
    successful reserve disabled auto-reserve permanently, so a hold that
    expired overnight left the watch dead — no further attempts, seat gone.

    Now: paid → keep auto-reserve off (goal achieved, hold cleared).
         expired unpaid → re-arm auto-reserve so we keep hunting.
         still within deadline → leave as-is.
    """
    pending = [w for w in watches if state_mod.get_pending_reservation(state, w.id)]
    if not pending:
        return
    paid = _paid_keys(provider)
    if paid is None:
        # Cannot tell paid from lapsed. Re-arming on a guess buys the user a
        # second ticket for a seat they already own and paid for; leaving the
        # hold alone costs at most one missed re-hunt, which the PWA shows as
        # a paused watch and the user can re-arm themselves. Fail towards not
        # spending money.
        log.warning("[%s] 결제 여부를 확인할 수 없어 %d건의 예약 판정을 보류합니다",
                    provider.name, len(pending))
        return
    now_dt = _parse_dt(now_iso)
    for watch in pending:
        _settle_hold(provider, watch, state, paid, now_dt, notify_rearmed_fn)


def _paid_keys(provider: Provider) -> set[str] | None:
    try:
        return provider.paid_reservation_keys()
    except Exception as e:  # never let this break the poll
        log.exception("[%s] paid-reservation lookup failed: %s", provider.name, e)
        return None


def _settle_hold(
    provider: Provider, watch: Watch, state: dict[str, Any], paid: set[str],
    now_dt: datetime | None, notify_rearmed_fn: Callable[[Watch], None] | None,
) -> None:
    rec = state_mod.get_pending_reservation(state, watch.id) or {}
    rsv_id = str(rec.get("id") or "")
    journey = str(rec.get("journey") or "")
    if (rsv_id and rsv_id in paid) or (journey and journey in paid):
        _clear_paid_hold(provider, watch, state, rsv_id)
        return
    deadline = _parse_dt(rec.get("deadline"))
    if deadline is None or now_dt is None:
        return  # standby / unknown deadline — leave the hold alone
    if now_dt <= deadline + timedelta(minutes=RE_ARM_GRACE_MIN):
        return  # still payable
    if not journey:
        # Written before holds recorded a journey key, so the paid check
        # above could not have matched it on Korail whatever the truth is.
        # Same rule as an unavailable lookup: no evidence, no re-arm. Drop
        # the record once it is long dead so state does not accumulate —
        # auto-reserve stays off and the PWA shows the watch as paused.
        if now_dt > deadline + timedelta(hours=STALE_HOLD_HOURS):
            log.info("[%s] watch %s: dropping unverifiable legacy hold %s",
                     provider.name, _wid(watch.id), rsv_id or "?")
            state_mod.clear_pending_reservation(state, watch.id)
        return
    _rearm_lapsed_hold(provider, watch, state, rsv_id, notify_rearmed_fn)


def _clear_paid_hold(provider: Provider, watch: Watch, state: dict[str, Any], rsv_id: str) -> None:
    log.info("[%s] watch %s: reservation %s paid — auto-reserve stays off",
             provider.name, _wid(watch.id), rsv_id)
    state_mod.clear_pending_reservation(state, watch.id)
    # Backstop to the "결제 완료 — 알림 중지" link in the message: if the
    # user paid without tapping it, silence whatever reminders are still
    # queued. The link is the fast path; this only catches stragglers on
    # the next poll, so it must never take down a run that already did
    # its job.
    try:
        reminders.cancel_reminders(rsv_id)
    except Exception as e:
        log.exception("리마인더 취소 실패: %s", e)


def _rearm_lapsed_hold(
    provider: Provider, watch: Watch, state: dict[str, Any], rsv_id: str,
    notify_rearmed_fn: Callable[[Watch], None] | None,
) -> None:
    # Hold lapsed unpaid → the seat is back in the pool; hunt again.
    log.info("[%s] watch %s: hold %s expired unpaid — re-arming auto-reserve",
             provider.name, _wid(watch.id), rsv_id or "?")
    state_mod.clear_pending_reservation(state, watch.id)
    state_mod.enable_auto_reserve(state, watch.id)
    if notify_rearmed_fn is not None:
        try:
            notify_rearmed_fn(watch)
        except Exception as e:
            log.exception("re-arm notify failed: %s", e)


# ----- placing a new hold ---------------------------------------------------

def try_auto_reserve(
    watch: Watch,
    provider: Provider,
    state: dict[str, Any],
    new_trains: list[Train],
    *,
    notify_reserve_success_fn: ReserveSuccessFn | None = None,
    notify_reserve_failure_fn: ReserveFailureFn | None = None,
    schedule_reminders_fn: ScheduleRemindersFn | None = None,
    notify_auto_reserve_disabled_fn: Callable[[Watch], None] | None = None,
    allow_waiting: bool = False,
) -> None:
    """Hold the first of the newly found trains that can still be booked.

    Auto-reserve is one-shot: after a successful reservation the watch's
    auto_reserve is self-disabled (recorded in state, since the worker can't
    edit the watch). This prevents re-reserving the same seat on every
    renotify poll, and lets the user re-enable from the PWA when they want
    another. A prior success in state short-circuits here.
    """
    if not watch.auto_reserve or state_mod.is_auto_reserve_disabled(state, watch.id):
        return
    candidates = new_trains[:MAX_RESERVE_CANDIDATES]
    chosen, reservation, last_err = _reserve_first(watch, provider, candidates, allow_waiting)
    if reservation is not None and chosen is not None:
        if reservation.already_existed:
            _pause_for_existing(provider, watch, state, chosen)
        else:
            _record_new_hold(
                provider, watch, state, chosen, reservation,
                notify_reserve_success_fn, schedule_reminders_fn,
                notify_auto_reserve_disabled_fn,
            )
    elif last_err is not None:
        _report_all_failed(provider, watch, len(candidates), last_err, notify_reserve_failure_fn)


def _reserve_first(
    watch: Watch, provider: Provider, candidates: list[Train], allow_waiting: bool,
) -> tuple[Train | None, Reservation | None, tuple[Train, Exception] | None]:
    # Try up to MAX_RESERVE_CANDIDATES trains in case earlier candidates
    # got snatched between our search and the reserve call (anti-bot
    # macros / faster bots / human race wins). Stop on first success.
    last_err: tuple[Train, Exception] | None = None
    for candidate in candidates:
        try:
            r = provider.reserve(candidate, watch.passengers, allow_waiting=allow_waiting)
            return candidate, r, None
        except Exception as e:
            last_err = (candidate, e)
            log.info(
                "[%s] watch %s: reserve %s failed (%s); trying next candidate",
                provider.name, _wid(watch.id), candidate.train_no, e,
            )
    return None, None, last_err


def _pause_for_existing(provider: Provider, watch: Watch, state: dict[str, Any], chosen: Train) -> None:
    # The provider itself said the user already holds this — the
    # strongest evidence there is. Stop hunting: leaving
    # auto-reserve armed meant every later poll fired another
    # reserve call that could only be rejected the same way, which
    # is both pointless and exactly the pattern bot detection looks
    # for. No pending record: we never learned this hold's
    # deadline, and guessing one is what re-books seats.
    log.info(
        "[%s] watch %s: train %s already reserved — pausing auto-reserve",
        provider.name, _wid(watch.id), chosen.train_no,
    )
    state_mod.disable_auto_reserve(state, watch.id)


def _record_new_hold(
    provider: Provider, watch: Watch, state: dict[str, Any],
    chosen: Train, reservation: Reservation,
    notify_reserve_success_fn: ReserveSuccessFn | None,
    schedule_reminders_fn: ScheduleRemindersFn | None,
    notify_auto_reserve_disabled_fn: Callable[[Watch], None] | None,
) -> None:
    kind = "standby" if reservation.is_standby else "reserved"
    log.info(
        "[%s] watch %s: %s %s (id=%s)",
        provider.name, _wid(watch.id), kind, chosen.train_no, reservation.reservation_id,
    )
    if notify_reserve_success_fn is not None:
        notify_reserve_success_fn(watch, chosen, reservation)
    if schedule_reminders_fn is not None and not reservation.is_standby:
        # Standby has no payment deadline yet — reminders kick in
        # only after assignment via a separate notification.
        try:
            schedule_reminders_fn(watch, chosen, reservation)
        except Exception as e:
            log.exception("schedule_reminders failed for %s: %s", reservation.reservation_id, e)
    # Pause auto-reserve while this hold is alive so we don't
    # re-reserve on every later poll. It is *not* permanent: a
    # later run checks whether the hold got paid — if it expired
    # unpaid, auto-reserve re-arms automatically (see
    # resolve_pending_reservations).
    state_mod.disable_auto_reserve(state, watch.id)
    state_mod.set_pending_reservation(
        state, watch.id, reservation.reservation_id, reservation.expires_at,
        journey=journey_key(chosen.train_no, chosen.date),
    )
    if notify_auto_reserve_disabled_fn is not None:
        try:
            notify_auto_reserve_disabled_fn(watch)
        except Exception as e:
            log.exception("auto-reserve-disabled notify failed: %s", e)


def _report_all_failed(
    provider: Provider, watch: Watch, tried: int,
    last_err: tuple[Train, Exception],
    notify_reserve_failure_fn: ReserveFailureFn | None,
) -> None:
    target, err = last_err
    log.exception(
        "[%s] watch %s: all %d reserve candidates failed; last error on %s: %s",
        provider.name, _wid(watch.id), tried, target.train_no, err,
    )
    if notify_reserve_failure_fn is not None:
        try:
            notify_reserve_failure_fn(watch, target, str(err))
        except Exception as nfx:
            log.exception("reserve-failure notify itself failed: %s", nfx)
