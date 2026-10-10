from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import ValidationError

from . import notifier
from . import pushover
from . import reminders
from . import remote
from . import reservations
from . import state as state_mod
from .adapters.base import Provider
from .adapters.korail import KorailProvider
from .logsafe import wid as _wid
from .matcher import find_new_trains
from .models import Train, Watch
from .reservations import (
    ReserveFailureFn,
    ReserveSuccessFn,
    ScheduleRemindersFn,
)
from .throttle import (
    after_poll as _after_poll,
    skip_poll as _skip_poll,
)

log = logging.getLogger("ticket_watcher")

NotifyFn = Callable[..., None]   # (Watch, list[Train], *, silent: bool=False)
SummaryFn = Callable[..., None]  # (list[Watch], *, silent: bool=False)

# Events that obey the throttle. "push" stays in the automated set for
# safety: config no longer lives in git so saving settings makes no commit at
# all, but any future push-driven run should obey the throttle rather than
# force an off-cadence poll. Only manual "지금 확인" (workflow_dispatch)
# bypasses it.
AUTOMATED_EVENTS = ("schedule", "repository_dispatch", "push")


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
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    run_watches(
        cfg, s,
        providers=_providers(s),
        creds=load_credentials(),
        notify_fn=notifier.notify,
        notify_summary_fn=notifier.notify_summary,
        notify_reserve_success_fn=notifier.notify_reservation_success,
        notify_reserve_failure_fn=notifier.notify_reservation_failure,
        schedule_reminders_fn=reminders.schedule_reminders,
        notify_auto_reserve_disabled_fn=notifier.notify_auto_reserve_disabled,
        notify_auto_reserve_rearmed_fn=notifier.notify_auto_reserve_rearmed,
        now_iso=now_iso,
        event_name=os.environ.get("GITHUB_EVENT_NAME"),
    )
    _save_state(cfg, s, now_iso)
    return 0


def _providers(state: dict[str, Any]) -> dict[str, Provider]:
    # One device identity across runs: a fresh random profile on every poll is
    # what looks like a bot farm. Minted once, kept in state.
    korail = KorailProvider(device_profile_id=state.get("korail_device_profile"))
    state["korail_device_profile"] = korail.device_profile_id
    return {"korail": korail}


def _save_state(cfg: dict[str, Any], s: dict[str, Any], now_iso: str) -> None:
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


def _load_active_watches(config: dict[str, Any]) -> list[Watch]:
    """Active watches that validate. A malformed one is logged and skipped:
    one bad entry must not stop every other watch from being polled."""
    active: list[Watch] = []
    for w in config.get("watches", []):
        if not w.get("active", True):
            continue
        try:
            active.append(Watch.model_validate(w))
        except ValidationError as e:
            # Errors only, never the id: Actions logs on this repo are public.
            log.error("워치 형식 오류로 건너뜀: %s", e.errors(include_input=False, include_url=False))
    return active


@dataclass(frozen=True)
class _RunOptions:
    """What the settings and the trigger decide for this run."""
    allow_waiting: bool
    renotify: bool
    login_push: bool
    silent: bool
    summarize_empty: bool


@dataclass(frozen=True)
class _Hooks:
    """The notification side effects, injected so tests can record them."""
    notify: NotifyFn
    summary: SummaryFn | None = None
    reserve_success: ReserveSuccessFn | None = None
    reserve_failure: ReserveFailureFn | None = None
    schedule_reminders: ScheduleRemindersFn | None = None
    auto_reserve_disabled: Callable[[Watch], None] | None = None
    auto_reserve_rearmed: Callable[[Watch], None] | None = None


def _run_options(settings: dict[str, Any], event_name: str | None, now_iso: str) -> _RunOptions:
    is_manual = event_name == "workflow_dispatch"
    quiet_start = settings.get("quiet_hours_start") or None
    quiet_end = settings.get("quiet_hours_end") or None
    return _RunOptions(
        allow_waiting=bool(settings.get("allow_waiting_list", False)),
        renotify=bool(settings.get("renotify_while_available", False)),
        # Default off: a broken login is not actionable until morning, so it
        # goes to Telegram only unless the user asks for it loud.
        login_push=bool(settings.get("login_alert_pushover", False)),
        # Quiet hours suppress the notification *sound*, not the message.
        # Manual triggers (지금 확인) bypass quiet hours — user explicitly
        # asked. Reservation success/failure/reminders also bypass downstream.
        silent=not is_manual and notifier.is_quiet_hour_kst(now_iso, quiet_start, quiet_end),
        summarize_empty=is_manual or (
            bool(settings.get("notify_empty_on_cron", False))
            and event_name in AUTOMATED_EVENTS
        ),
    )


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
    # Poll throttle. Three modes (settings.poll_interval_mode):
    #   fixed   — epoch-bucket cadence at poll_interval_min (default)
    #   range   — random minutes in [lo, hi] each gap (anti-bot)
    #   choices — random pick from a list of minutes each gap (anti-bot)
    # Randomized modes store the rolled target as state.next_poll_at. When a
    # watch departs within a couple of days the cadence tightens on its own
    # (throttle.effective_cadence).
    if event_name in AUTOMATED_EVENTS and _skip_poll(config, state, now_iso):
        return
    state_mod.mark_run(state, now_iso)
    _after_poll(config, state, now_iso)

    opts = _run_options(config.get("settings") or {}, event_name, now_iso)
    hooks = _Hooks(
        notify=notify_fn,
        summary=notify_summary_fn,
        reserve_success=notify_reserve_success_fn,
        reserve_failure=notify_reserve_failure_fn,
        schedule_reminders=schedule_reminders_fn,
        auto_reserve_disabled=notify_auto_reserve_disabled_fn,
        auto_reserve_rearmed=notify_auto_reserve_rearmed_fn,
    )
    active = _load_active_watches(config)
    by_provider: dict[str, list[Watch]] = {}
    for w in active:
        by_provider.setdefault(w.provider, []).append(w)

    new_train_total = 0
    for provider_name, watches in by_provider.items():
        provider = providers.get(provider_name)
        if provider is None:
            log.error("unknown provider: %s", provider_name)
            continue
        new_train_total += _poll_provider(
            provider_name, provider, watches, state, now_iso,
            creds.get(provider_name, ("", "")), opts, hooks,
        )

    # Record this *actual* poll (skip runs returned earlier, so they're never
    # counted). PWA stats read poll_history instead of GHA run counts.
    state_mod.record_poll(state, now_iso, new_train_total)
    if opts.summarize_empty and new_train_total == 0:
        _send_summary(hooks.summary, active, opts.silent)


def _poll_provider(
    provider_name: str, provider: Provider, watches: list[Watch],
    state: dict[str, Any], now_iso: str, cred: tuple[str, str],
    opts: _RunOptions, hooks: _Hooks,
) -> int:
    """Log in, settle earlier holds, then check each watch. Returns the
    number of newly found trains."""
    if not _login(provider_name, provider, cred, state, now_iso, opts.login_push):
        return 0
    # Settle holds placed on earlier runs before hunting again: a hold that
    # lapsed unpaid re-arms auto-reserve here, so this poll can re-reserve.
    reservations.resolve_pending_reservations(
        provider, watches, state, now_iso,
        notify_rearmed_fn=hooks.auto_reserve_rearmed,
    )
    found = 0
    any_watch_ok = False
    last_watch_err: Exception | None = None
    for watch in watches:
        try:
            found += _process_watch(watch, provider, state, now_iso, opts, hooks)
            any_watch_ok = True
        except Exception as e:
            last_watch_err = e
            log.exception("[%s] watch %s failed: %s", provider_name, _wid(watch.id), e)
    _track_search_outage(
        provider_name, state, now_iso, bool(watches), any_watch_ok,
        last_watch_err, opts.login_push,
    )
    return found


def _login(
    provider_name: str, provider: Provider, cred: tuple[str, str],
    state: dict[str, Any], now_iso: str, push: bool,
) -> bool:
    try:
        provider.login(*cred)
    except Exception as e:
        # A failed login used to be logged and skipped, which is the
        # quietest way this system can die: the run still succeeds, the
        # state timestamp still advances, so the heartbeat and the health
        # card both stay green while nothing is being watched.
        log.exception("[%s] login failed: %s", provider_name, e)
        if state_mod.record_login_failure(state, provider_name, now_iso):
            _safe_notify("login-failure", notifier.notify_login_failed,
                         provider_name, str(e), push=push)
        return False
    if state_mod.clear_login_failure(state, provider_name):
        log.info("[%s] login recovered", provider_name)
        _safe_notify("login-recovery", notifier.notify_login_recovered,
                     provider_name, push=push)
    return True


def _track_search_outage(
    provider_name: str, state: dict[str, Any], now_iso: str, had_watches: bool,
    any_watch_ok: bool, last_err: Exception | None, push: bool,
) -> None:
    # Login failure has an alert; a provider whose every SEARCH fails died
    # just as completely and just as quietly — the run stays green, state
    # advances, the health card smiles. That is how the 코레일+ MACRO
    # rejection went unnoticed until a seat was already missed. Alert on
    # the systemic case (all watches failed); a single failing watch among
    # working ones is a per-watch problem, not a dead provider.
    if had_watches and not any_watch_ok:
        if state_mod.record_search_failure(state, provider_name, now_iso):
            _safe_notify("search-failure", notifier.notify_search_failed,
                         provider_name, str(last_err), push=push)
    elif any_watch_ok and state_mod.clear_search_failure(state, provider_name):
        log.info("[%s] search recovered", provider_name)
        _safe_notify("search-recovery", notifier.notify_search_recovered,
                     provider_name, push=push)


def _safe_notify(what: str, fn: Callable[..., None], *args: Any, **kwargs: Any) -> None:
    """An outage alert that fails must not take down the poll it reports on."""
    try:
        fn(*args, **kwargs)
    except Exception as nfx:
        log.exception("%s notify itself failed: %s", what, nfx)


def _send_summary(summary_fn: SummaryFn | None, active: list[Watch], silent: bool) -> None:
    if summary_fn is None:
        return
    try:
        summary_fn(active, silent=silent)
    except TypeError:
        # Older test fakes don't accept silent kwarg
        summary_fn(active)
    except Exception as e:
        log.exception("summary notify failed: %s", e)


def _process_watch(
    watch: Watch, provider: Provider, state: dict[str, Any], now_iso: str,
    opts: _RunOptions, hooks: _Hooks,
) -> int:
    state_mod.mark_check(state, watch.id, now_iso)
    state_mod.set_watch_date(state, watch.id, watch.date)

    notified = state_mod.get_notified_ids(state, watch.id)
    trains = provider.search(watch)
    # renotify: alert on every poll while seats remain (dedup skipped). We
    # still record notified_train_ids so turning the toggle back off resumes
    # normal dedup cleanly.
    new_trains = find_new_trains(watch, trains, notified, renotify=opts.renotify)
    if not new_trains:
        log.info("[%s] watch %s: no new seats (%d searched)", provider.name, _wid(watch.id), len(trains))
        return 0

    log.info("[%s] watch %s: %d seat(s)%s", provider.name, _wid(watch.id), len(new_trains),
             " (renotify)" if opts.renotify else " new")
    try:
        hooks.notify(watch, new_trains, silent=opts.silent)
    except TypeError:
        hooks.notify(watch, new_trains)
    state_mod.add_notified_ids(state, watch.id, [t.raw_id for t in new_trains])

    reservations.try_auto_reserve(
        watch, provider, state, new_trains,
        notify_reserve_success_fn=hooks.reserve_success,
        notify_reserve_failure_fn=hooks.reserve_failure,
        schedule_reminders_fn=hooks.schedule_reminders,
        notify_auto_reserve_disabled_fn=hooks.auto_reserve_disabled,
        allow_waiting=opts.allow_waiting,
    )
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
    watch, train = _synthetic_find()
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
            expire_sec=pushover.TEST_EXPIRE_SEC,
        )
        log.info("pushover 테스트 알림 발송 완료 (mode=%s)", pushover._mode)

    return 0


def _synthetic_find() -> tuple[Watch, Train]:
    """A made-up 서울→부산 seat for today, for the test alert only."""
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
    return watch, train


def load_credentials() -> dict[str, tuple[str, str]]:
    # SRT_ID/SRT_PW are gone with the service (2026-08-31); the secrets can be
    # deleted from the repo whenever.
    return {
        "korail": (os.environ.get("KORAIL_ID", ""), os.environ.get("KORAIL_PW", "")),
    }


if __name__ == "__main__":
    _test = next((a for a in sys.argv if a.startswith("--test-notify")), None)
    if _test is not None:
        _channel = _test.split("=", 1)[1] if "=" in _test else "all"
        sys.exit(send_test_notification(_channel))
    sys.exit(main())
