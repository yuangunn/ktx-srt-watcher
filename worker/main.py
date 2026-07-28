from __future__ import annotations

import json
import logging
import os
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from . import notifier
from . import pushover
from . import remote
from . import state as state_mod
from .adapters.base import Provider
from .adapters.korail import KorailProvider
from .adapters.srt import SRTProvider
from .matcher import find_new_trains
from .models import Reservation, Train, Watch
from .throttle import (
    MIN_POLL_INTERVAL_MIN,
    should_throttle as _should_throttle,
    set_next_poll as _set_next_poll,
    roll_interval_min as _roll_interval_min,
    parse_dt as _parse_dt,
)

log = logging.getLogger("ticket_watcher")

NotifyFn = Callable[..., None]   # (Watch, list[Train], *, silent: bool=False)
SummaryFn = Callable[..., None]  # (list[Watch], *, silent: bool=False)
ReserveSuccessFn = Callable[[Watch, Train, "Reservation"], None]
ReserveFailureFn = Callable[[Watch, Train, str], None]
ScheduleRemindersFn = Callable[[Watch, Train, "Reservation"], None]

# How many candidate trains to attempt reserving when the first one(s) are
# already taken. Caps the burst of API calls into Korail/SRT per run.
MAX_RESERVE_CANDIDATES = 3

# Grace period after a hold's payment deadline before we declare it lapsed and
# re-arm auto-reserve. Covers clock skew and late payment propagation.
RE_ARM_GRACE_MIN = 3


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        cfg = load_config()
        s = remote.fetch_state()
    except remote.RemoteError as e:
        # We cannot tell whether there is anything to poll — fail loudly.
        log.error("설정을 불러오지 못했습니다: %s", e)
        return 1
    if cfg is None:
        # Nothing stored yet. Not a failure: the user simply hasn't added a
        # watch. Exiting non-zero here would paint every tick red.
        log.info("등록된 워치가 없습니다 — 앱에서 워치를 추가하세요")
        return 0
    # Only the phone knows whether the user is home; the mode decides whether
    # an urgent alert may override the mute switch.
    pushover.set_mode(remote.fetch_mode())
    creds = load_credentials()
    providers: dict[str, Provider] = {
        "korail": KorailProvider(),
        "srt": SRTProvider(),
    }
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_watches(
        cfg, s,
        providers=providers,
        creds=creds,
        notify_fn=notifier.notify,
        notify_summary_fn=notifier.notify_summary,
        notify_reserve_success_fn=notifier.notify_reservation_success,
        notify_reserve_failure_fn=notifier.notify_reservation_failure,
        schedule_reminders_fn=notifier.schedule_reminders,
        notify_auto_reserve_disabled_fn=notifier.notify_auto_reserve_disabled,
        notify_auto_reserve_rearmed_fn=notifier.notify_auto_reserve_rearmed,
        now_iso=now_iso,
        event_name=os.environ.get("GITHUB_EVENT_NAME"),
    )
    state_mod.prune_past_dates(s, today=now_iso[:10])
    state_mod.prune_orphan_flags(
        s, [w.get("id") for w in cfg.get("watches", []) if w.get("id")]
    )
    try:
        remote.push_state(s)
    except remote.RemoteError as e:
        # The poll already happened and any alerts already went out; losing the
        # write costs dedup history (a seat may be re-notified next run), so
        # log it rather than fail a run that did its job.
        log.error("상태 저장 실패: %s", e)
    return 0


def run_watches(
    config: dict[str, Any],
    state: dict[str, Any],
    *,
    providers: dict[str, Provider],
    creds: dict[str, tuple[str, str]],
    notify_fn: NotifyFn,
    now_iso: str,
    notify_summary_fn: SummaryFn | None = None,
    notify_reserve_success_fn: ReserveSuccessFn | None = None,
    notify_reserve_failure_fn: ReserveFailureFn | None = None,
    schedule_reminders_fn: ScheduleRemindersFn | None = None,
    notify_auto_reserve_disabled_fn: Callable[[Watch], None] | None = None,
    notify_auto_reserve_rearmed_fn: Callable[[Watch], None] | None = None,
    event_name: str | None = None,
) -> None:
    settings_pre = config.get("settings") or {}
    poll_interval_min = int(settings_pre.get("poll_interval_min") or 0)
    allow_waiting_list = bool(settings_pre.get("allow_waiting_list", False))
    renotify = bool(settings_pre.get("renotify_while_available", False))
    quiet_start = settings_pre.get("quiet_hours_start") or None
    quiet_end = settings_pre.get("quiet_hours_end") or None
    is_manual_pre = event_name == "workflow_dispatch"
    # "push" stays in the automated set for safety: config no longer lives in
    # git so saving settings makes no commit at all, but any future push-driven
    # run should obey the throttle rather than force an off-cadence poll. Only
    # manual "지금 확인" (workflow_dispatch) bypasses it.
    is_automated_pre = event_name in ("schedule", "repository_dispatch", "push")
    # Quiet hours suppress the notification *sound*, not the message.
    # Manual triggers (지금 확인) bypass quiet hours — user explicitly
    # asked. Reservation success/failure/reminders also bypass downstream.
    silent_now = (
        not is_manual_pre
        and notifier.is_quiet_hour_kst(now_iso, quiet_start, quiet_end)
    )
    # Poll throttle. Three modes (settings.poll_interval_mode):
    #   fixed   — epoch-bucket cadence at poll_interval_min (default)
    #   range   — random minutes in [lo, hi] each gap (anti-bot)
    #   choices — random pick from a list of minutes each gap (anti-bot)
    # Randomized modes store the rolled target as state.next_poll_at.
    poll_mode = settings_pre.get("poll_interval_mode") or "fixed"
    if is_automated_pre and _should_throttle(
        poll_mode, poll_interval_min, settings_pre, state, now_iso
    ):
        return

    state_mod.mark_run(state, now_iso)
    _set_next_poll(poll_mode, settings_pre, state, now_iso)

    active = [Watch.model_validate(w) for w in config.get("watches", []) if w.get("active", True)]
    by_provider: dict[str, list[Watch]] = {}
    for w in active:
        by_provider.setdefault(w.provider, []).append(w)

    new_train_total = 0
    for provider_name, watches in by_provider.items():
        provider = providers.get(provider_name)
        if provider is None:
            log.error("unknown provider: %s", provider_name)
            continue
        user, password = creds.get(provider_name, ("", ""))
        try:
            provider.login(user, password)
        except Exception as e:
            log.exception("[%s] login failed: %s", provider_name, e)
            continue
        # Settle holds placed on earlier runs before hunting again: a hold that
        # lapsed unpaid re-arms auto-reserve here, so this poll can re-reserve.
        _resolve_pending_reservations(
            provider, watches, state, now_iso,
            notify_rearmed_fn=notify_auto_reserve_rearmed_fn,
        )
        for watch in watches:
            try:
                new_train_total += _process_watch(
                    watch, provider, state, notify_fn, now_iso,
                    notify_reserve_success_fn=notify_reserve_success_fn,
                    notify_reserve_failure_fn=notify_reserve_failure_fn,
                    schedule_reminders_fn=schedule_reminders_fn,
                    notify_auto_reserve_disabled_fn=notify_auto_reserve_disabled_fn,
                    allow_waiting=allow_waiting_list,
                    silent=silent_now,
                    renotify=renotify,
                )
            except Exception as e:
                log.exception("[%s] watch %s failed: %s", provider_name, watch.id, e)

    # Record this *actual* poll (skip runs returned earlier, so they're never
    # counted). PWA stats read poll_history instead of GHA run counts.
    state_mod.record_poll(state, now_iso, new_train_total)

    settings = config.get("settings") or {}
    notify_empty_on_cron = bool(settings.get("notify_empty_on_cron", False))
    is_manual = event_name == "workflow_dispatch"
    is_automated = event_name in ("schedule", "repository_dispatch", "push")
    should_summarize = is_manual or (notify_empty_on_cron and is_automated)
    if should_summarize and new_train_total == 0 and notify_summary_fn is not None:
        try:
            notify_summary_fn(active, silent=silent_now)
        except TypeError:
            # Older test fakes don't accept silent kwarg
            notify_summary_fn(active)
        except Exception as e:
            log.exception("summary notify failed: %s", e)


def _resolve_pending_reservations(
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
    now_dt = _parse_dt(now_iso)
    paid: set[str] = set()
    try:
        paid = provider.paid_reservation_ids()
    except Exception as e:  # never let this break the poll
        log.exception("[%s] paid-reservation lookup failed: %s", provider.name, e)
        paid = set()

    for watch in pending:
        rec = state_mod.get_pending_reservation(state, watch.id) or {}
        rsv_id = str(rec.get("id") or "")
        if rsv_id and rsv_id in paid:
            log.info("[%s] watch %s: reservation %s paid — auto-reserve stays off",
                     provider.name, watch.id, rsv_id)
            state_mod.clear_pending_reservation(state, watch.id)
            continue
        deadline = _parse_dt(rec.get("deadline"))
        if deadline is None or now_dt is None:
            continue  # standby / unknown deadline — leave the hold alone
        if now_dt <= deadline + timedelta(minutes=RE_ARM_GRACE_MIN):
            continue  # still payable
        # Hold lapsed unpaid → the seat is back in the pool; hunt again.
        log.info("[%s] watch %s: hold %s expired unpaid — re-arming auto-reserve",
                 provider.name, watch.id, rsv_id or "?")
        state_mod.clear_pending_reservation(state, watch.id)
        state_mod.enable_auto_reserve(state, watch.id)
        if notify_rearmed_fn is not None:
            try:
                notify_rearmed_fn(watch)
            except Exception as e:
                log.exception("re-arm notify failed: %s", e)


def _process_watch(
    watch: Watch,
    provider: Provider,
    state: dict[str, Any],
    notify_fn: NotifyFn,
    now_iso: str,
    *,
    notify_reserve_success_fn: ReserveSuccessFn | None = None,
    notify_reserve_failure_fn: ReserveFailureFn | None = None,
    schedule_reminders_fn: ScheduleRemindersFn | None = None,
    notify_auto_reserve_disabled_fn: Callable[[Watch], None] | None = None,
    allow_waiting: bool = False,
    silent: bool = False,
    renotify: bool = False,
) -> int:
    state_mod.mark_check(state, watch.id, now_iso)
    state_mod.set_watch_date(state, watch.id, watch.date)

    notified = state_mod.get_notified_ids(state, watch.id)
    trains = provider.search(watch)
    # renotify: alert on every poll while seats remain (dedup skipped). We
    # still record notified_train_ids so turning the toggle back off resumes
    # normal dedup cleanly.
    new_trains = find_new_trains(watch, trains, notified, renotify=renotify)

    if not new_trains:
        log.info("[%s] watch %s: no new seats (%d searched)", provider.name, watch.id, len(trains))
        return 0

    log.info("[%s] watch %s: %d seat(s)%s", provider.name, watch.id, len(new_trains),
             " (renotify)" if renotify else " new")
    try:
        notify_fn(watch, new_trains, silent=silent)
    except TypeError:
        notify_fn(watch, new_trains)
    state_mod.add_notified_ids(state, watch.id, [t.raw_id for t in new_trains])

    # Auto-reserve is one-shot: after a successful reservation the watch's
    # auto_reserve is self-disabled (recorded in state, since the worker can't
    # edit the watch). This prevents re-reserving the same seat on every
    # renotify poll, and lets the user re-enable from the PWA when they want
    # another. A prior success in state short-circuits here.
    if watch.auto_reserve and not state_mod.is_auto_reserve_disabled(state, watch.id):
        # Try up to MAX_RESERVE_CANDIDATES trains in case earlier candidates
        # got snatched between our search and the reserve call (anti-bot
        # macros / faster bots / human race wins). Stop on first success.
        candidates = new_trains[:MAX_RESERVE_CANDIDATES]
        reservation: Reservation | None = None
        chosen: Train | None = None
        last_err: tuple[Train, Exception] | None = None
        for candidate in candidates:
            try:
                r = provider.reserve(
                    candidate, watch.passengers, allow_waiting=allow_waiting,
                )
                reservation = r
                chosen = candidate
                break
            except Exception as e:
                last_err = (candidate, e)
                log.info(
                    "[%s] watch %s: reserve %s failed (%s); trying next candidate",
                    provider.name, watch.id, candidate.train_no, e,
                )

        if reservation is not None and chosen is not None:
            if reservation.already_existed:
                log.info(
                    "[%s] watch %s: train %s already reserved — skipping notify",
                    provider.name, watch.id, chosen.train_no,
                )
            else:
                kind = "standby" if reservation.is_standby else "reserved"
                log.info(
                    "[%s] watch %s: %s %s (id=%s)",
                    provider.name, watch.id, kind, chosen.train_no, reservation.reservation_id,
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
                # _resolve_pending_reservations).
                state_mod.disable_auto_reserve(state, watch.id)
                state_mod.set_pending_reservation(
                    state, watch.id, reservation.reservation_id, reservation.expires_at,
                )
                if notify_auto_reserve_disabled_fn is not None:
                    try:
                        notify_auto_reserve_disabled_fn(watch)
                    except Exception as e:
                        log.exception("auto-reserve-disabled notify failed: %s", e)
        elif last_err is not None:
            target, err = last_err
            log.exception(
                "[%s] watch %s: all %d reserve candidates failed; last error on %s: %s",
                provider.name, watch.id, len(candidates), target.train_no, err,
            )
            if notify_reserve_failure_fn is not None:
                try:
                    notify_reserve_failure_fn(watch, target, str(err))
                except Exception as nfx:
                    log.exception("reserve-failure notify itself failed: %s", nfx)

    return len(new_trains)


def load_config() -> dict[str, Any]:
    """Watch list, from CF KV. See worker/remote.py for why it is not in git."""
    return remote.fetch_config()


def send_test_notification(channel: str = "all") -> int:
    """Fire the real alert path with a synthetic find.

    The seat-found path is the one that matters and the hardest to exercise:
    it only runs when a cancellation actually appears, which is precisely when
    you cannot afford to discover it is broken.  This builds the same message
    through the same formatter, so the phone shows exactly what a genuine find
    will — and, for pushover, sounds like it too.

    channel: "telegram" | "pushover" | "all".  Testing them separately matters
    because they fail for entirely different reasons (bot token vs. Critical
    Alerts permission), and a combined test hides which one broke.

    Nothing is searched and nothing is reserved.
    """
    pushover.set_mode(remote.fetch_mode())
    watch = Watch(
        id="test-notification",
        provider="korail",
        **{"from": "서울"},
        to="부산",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        time_min="00:00",
        time_max="23:59",
        train_types=["KTX"],
    )
    train = Train(
        provider="korail",
        train_no="000",
        train_type="KTX",
        dep_station="서울",
        arr_station="부산",
        date=watch.date,
        dep_time="09:35",
        arr_time="12:14",
        seats_general=1,
        seats_special=0,
        raw_id="test-notification",
        booking_url="https://www.letskorail.com",
        seat_class="general",
    )
    body = "🧪 테스트 알림 (실제 좌석 아님)\n\n" + notifier.format_message(watch, [train])

    if channel in ("telegram", "all"):
        notifier.send_telegram(
            os.environ["TELEGRAM_BOT_TOKEN"], os.environ["TELEGRAM_CHAT_ID"], body,
        )
        log.info("텔레그램 테스트 알림 발송 완료")

    if channel in ("pushover", "all"):
        if not pushover.enabled():
            log.error(
                "PUSHOVER_TOKEN / PUSHOVER_USER 가 설정되지 않아 발송하지 않았습니다"
            )
            return 1
        pushover.send(
            "🧪 테스트 — 좌석 발견 알림",
            body,
            priority=pushover.PRIORITY_EMERGENCY,
        )
        log.info("pushover 테스트 알림 발송 완료 (mode=%s)", pushover._mode)

    return 0


def load_credentials() -> dict[str, tuple[str, str]]:
    return {
        "korail": (os.environ.get("KORAIL_ID", ""), os.environ.get("KORAIL_PW", "")),
        "srt": (os.environ.get("SRT_ID", ""), os.environ.get("SRT_PW", "")),
    }


if __name__ == "__main__":
    _test = next((a for a in sys.argv if a.startswith("--test-notify")), None)
    if _test is not None:
        _channel = _test.split("=", 1)[1] if "=" in _test else "all"
        sys.exit(send_test_notification(_channel))
    sys.exit(main())
