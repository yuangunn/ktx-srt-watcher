from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import notifier
from . import state as state_mod
from .adapters.base import Provider
from .adapters.korail import KorailProvider
from .adapters.srt import SRTProvider
from .matcher import find_new_trains
from .models import Reservation, Train, Watch

log = logging.getLogger("ticket_watcher")

CONFIG_PATH = Path("config.json")
STATE_PATH = Path("state.json")

NotifyFn = Callable[..., None]   # (Watch, list[Train], *, silent: bool=False)
SummaryFn = Callable[..., None]  # (list[Watch], *, silent: bool=False)
ReserveSuccessFn = Callable[[Watch, Train, "Reservation"], None]
ReserveFailureFn = Callable[[Watch, Train, str], None]
ScheduleRemindersFn = Callable[[Watch, Train, "Reservation"], None]

# How many candidate trains to attempt reserving when the first one(s) are
# already taken. Caps the burst of API calls into Korail/SRT per run.
MAX_RESERVE_CANDIDATES = 3


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = load_config(CONFIG_PATH)
    s = state_mod.read_state(STATE_PATH)
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
        now_iso=now_iso,
        event_name=os.environ.get("GITHUB_EVENT_NAME"),
    )
    state_mod.prune_past_dates(s, today=now_iso[:10])
    state_mod.write_state(STATE_PATH, s)
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
    event_name: str | None = None,
) -> None:
    settings_pre = config.get("settings") or {}
    poll_interval_min = int(settings_pre.get("poll_interval_min") or 0)
    allow_waiting_list = bool(settings_pre.get("allow_waiting_list", False))
    quiet_start = settings_pre.get("quiet_hours_start") or None
    quiet_end = settings_pre.get("quiet_hours_end") or None
    is_manual_pre = event_name == "workflow_dispatch"
    is_automated_pre = event_name in ("schedule", "repository_dispatch")
    # Quiet hours suppress the notification *sound*, not the message.
    # Manual triggers (지금 확인) bypass quiet hours — user explicitly
    # asked. Reservation success/failure/reminders also bypass downstream.
    silent_now = (
        not is_manual_pre
        and notifier.is_quiet_hour_kst(now_iso, quiet_start, quiet_end)
    )
    if poll_interval_min > 0 and is_automated_pre and state.get("last_run"):
        try:
            last_dt = datetime.fromisoformat(state["last_run"].replace("Z", "+00:00"))
            now_dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
            # Bucket-based throttle: divide epoch time into N-min windows;
            # skip if the previous run fell into the same window we're in
            # right now.  This avoids the elapsed-based drift problem where
            # a run at HH:00:25 makes the HH:30:00 tick measure 29:35
            # elapsed and skip — losing the cadence — even though it's a
            # different cron boundary.
            bucket_size = poll_interval_min * 60
            last_bucket = int(last_dt.timestamp()) // bucket_size
            now_bucket = int(now_dt.timestamp()) // bucket_size
            if last_bucket == now_bucket:
                elapsed_sec = (now_dt - last_dt).total_seconds()
                log.info(
                    "skip run: same %dm bucket as last_run (elapsed=%.0fs, %s)",
                    poll_interval_min, elapsed_sec, event_name,
                )
                return
        except (ValueError, TypeError):
            pass

    state_mod.mark_run(state, now_iso)

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
        for watch in watches:
            try:
                new_train_total += _process_watch(
                    watch, provider, state, notify_fn, now_iso,
                    notify_reserve_success_fn=notify_reserve_success_fn,
                    notify_reserve_failure_fn=notify_reserve_failure_fn,
                    schedule_reminders_fn=schedule_reminders_fn,
                    allow_waiting=allow_waiting_list,
                    silent=silent_now,
                )
            except Exception as e:
                log.exception("[%s] watch %s failed: %s", provider_name, watch.id, e)

    settings = config.get("settings") or {}
    notify_empty_on_cron = bool(settings.get("notify_empty_on_cron", False))
    is_manual = event_name == "workflow_dispatch"
    is_automated = event_name in ("schedule", "repository_dispatch")
    should_summarize = is_manual or (notify_empty_on_cron and is_automated)
    if should_summarize and new_train_total == 0 and notify_summary_fn is not None:
        try:
            notify_summary_fn(active, silent=silent_now)
        except TypeError:
            # Older test fakes don't accept silent kwarg
            notify_summary_fn(active)
        except Exception as e:
            log.exception("summary notify failed: %s", e)


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
    allow_waiting: bool = False,
    silent: bool = False,
) -> int:
    state_mod.mark_check(state, watch.id, now_iso)
    state_mod.set_watch_date(state, watch.id, watch.date)

    notified = state_mod.get_notified_ids(state, watch.id)
    trains = provider.search(watch)
    new_trains = find_new_trains(watch, trains, notified)

    if not new_trains:
        log.info("[%s] watch %s: no new seats (%d searched)", provider.name, watch.id, len(trains))
        return 0

    log.info("[%s] watch %s: %d new seat(s)", provider.name, watch.id, len(new_trains))
    try:
        notify_fn(watch, new_trains, silent=silent)
    except TypeError:
        notify_fn(watch, new_trains)
    state_mod.add_notified_ids(state, watch.id, [t.raw_id for t in new_trains])

    if watch.auto_reserve:
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


def load_config(path: Path | str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def load_credentials() -> dict[str, tuple[str, str]]:
    return {
        "korail": (os.environ.get("KORAIL_ID", ""), os.environ.get("KORAIL_PW", "")),
        "srt": (os.environ.get("SRT_ID", ""), os.environ.get("SRT_PW", "")),
    }


if __name__ == "__main__":
    sys.exit(main())
