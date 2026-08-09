from __future__ import annotations

import hashlib
import hmac
import os
from typing import Protocol

import requests

from . import pushover
from .models import Reservation, Train, Watch


class _PostSession(Protocol):
    def post(self, url: str, *, json: dict, timeout: int): ...


def format_message(watch: Watch, trains: list[Train]) -> str:
    types_label = "/".join(watch.train_types)
    header = f"🚄 [{types_label}] {watch.from_}→{watch.to} {watch.date}"
    if not trains:
        return header

    lines = [header, ""]
    booking_urls: set[str] = set()
    for t in trains:
        seat_parts: list[str] = []
        if t.seats_general > 0:
            seat_parts.append("일반")
        if t.seats_special > 0:
            seat_parts.append("특실")
        seats = " / ".join(seat_parts) if seat_parts else "잔여 정보 없음"
        lines.append(f"{t.dep_time} 발 {t.train_type} {t.train_no} · {seats}")
        if t.booking_url:
            booking_urls.add(t.booking_url)

    if booking_urls:
        lines.append("")
        for url in sorted(booking_urls):
            lines.append(f"예매: {url}")

    lines.append("")
    lines.append("※ 정확한 잔여석 수는 코레일톡/SR 앱에서 확인하세요.")
    return "\n".join(lines)


def send_telegram(
    bot_token: str,
    chat_id: str,
    text: str,
    *,
    session: _PostSession | None = None,
    silent: bool = False,
) -> None:
    """Post a message to Telegram.  When `silent`, sets disable_notification
    so the message arrives without a sound on the user's device — used
    during quiet hours for non-urgent alerts.  Reservation success/failure
    and payment-deadline reminders ignore this and always ring."""
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: dict = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    if silent:
        payload["disable_notification"] = True
    sess = session if session is not None else requests
    resp = sess.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def notify(
    watch: Watch,
    trains: list[Train],
    *,
    session: _PostSession | None = None,
    silent: bool = False,
) -> None:
    if not trains:
        return
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_message(watch, trains)
    send_telegram(bot_token, chat_id, text, session=session, silent=silent)
    # A cancelled seat at 3am is exactly the alert that gets slept through, so
    # this one is emergency priority: it repeats until acknowledged.
    pushover.send(
        f"🚄 {watch.from_}→{watch.to} {watch.date}",
        text,
        priority=pushover.PRIORITY_EMERGENCY,
    )


def format_summary(watches: list[Watch]) -> str:
    if not watches:
        return "🔍 확인 완료 · 활성 워치 없음"
    lines = ["🔍 확인 완료 · 잔여 0건", ""]
    for w in watches:
        types = "/".join(w.train_types)
        lines.append(f"[{types}] {w.from_}→{w.to} {w.date} {w.time_min}–{w.time_max}")
    return "\n".join(lines)


def notify_summary(
    watches: list[Watch],
    *,
    session: _PostSession | None = None,
    silent: bool = False,
) -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_summary(watches)
    send_telegram(bot_token, chat_id, text, session=session, silent=silent)


def reminder_cancel_url(reservation_id: str) -> str | None:
    """One-tap "I paid, stop the reminders" link.

    Nothing in the reminder loop can tell whether the user has paid — Korail/SR
    are only reachable from this watcher, which may not run again inside the
    20-minute window — so the message carries its own off switch instead.

    The token is an HMAC of the reservation id under REMINDER_TOKEN: both sides
    derive it independently, nothing extra is stored, and it cannot be guessed
    for someone else's reservation.
    """
    base = (os.environ.get("CF_WORKER_URL") or "").rstrip("/")
    secret = os.environ.get("REMINDER_TOKEN") or ""
    if not base or not secret or not reservation_id:
        return None
    tok = hmac.new(
        secret.encode("utf-8"), str(reservation_id).encode("utf-8"), hashlib.sha256,
    ).hexdigest()[:16]
    return f"{base}/reminder/done?id={reservation_id}&t={tok}"


def cancel_reminders(reservation_id: str) -> None:
    """Silence a reservation's reminders once payment is confirmed.

    Best-effort backstop to the link in the message: this only runs on a poll,
    which may be half an hour away, so it is not what saves the user from a
    ringing phone — it just stops stragglers.
    """
    url = reminder_cancel_url(reservation_id)
    if not url:
        return
    try:
        requests.get(url, timeout=10)
    except Exception:
        pass


def format_reservation_success(watch: Watch, train: Train, reservation: Reservation) -> str:
    app_name = "코레일톡" if watch.provider == "korail" else "SR 앱"
    if reservation.is_standby:
        return (
            f"⏳ 대기예약 등록\n"
            f"\n"
            f"{watch.from_}→{watch.to} {watch.date}\n"
            f"{train.dep_time} 발 {train.train_type} {train.train_no}\n"
            f"\n"
            f"예약번호 {reservation.reservation_id}\n"
            f"\n"
            f"매진 → 대기예약 등록 완료. 다른 사람이 결제 안 하면 자동 배정됩니다.\n"
            f"배정되면 결제 마감 시각이 정해지고 별도 알림이 옵니다.\n"
            f"진행 상태는 {app_name}에서 확인 가능."
        )
    deadline = _fmt_deadline(reservation.expires_at)
    return (
        f"✅ 임시예약 성공\n"
        f"\n"
        f"{watch.from_}→{watch.to} {watch.date}\n"
        f"{train.dep_time} 발 {train.train_type} {train.train_no}\n"
        f"\n"
        f"예약번호 {reservation.reservation_id}\n"
        f"결제 마감 {deadline}\n"
        f"\n"
        f"⚠️ 결제는 {app_name}에서 직접 해 주세요. "
        f"마감 시간 지나면 좌석이 자동 해제됩니다."
        + (f"\n\n결제를 마쳤다면 눌러서 알림 중지 → {cancel}" if (cancel := reminder_cancel_url(reservation.reservation_id)) else "")
    )


def format_reservation_failure(watch: Watch, train: Train, error_msg: str) -> str:
    return (
        f"❌ 임시예약 실패\n"
        f"\n"
        f"{watch.from_}→{watch.to} {watch.date}\n"
        f"{train.dep_time} 발 {train.train_type} {train.train_no}\n"
        f"\n"
        f"사유: {error_msg}"
    )


def notify_reservation_success(
    watch: Watch, train: Train, reservation: Reservation,
    *, session: _PostSession | None = None,
) -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_reservation_success(watch, train, reservation)
    send_telegram(bot_token, chat_id, text, session=session)
    # The hold lapses in ~20 minutes unless the user pays, so this must land.
    pushover.send(
        "✅ 임시예약 성공 — 결제 필요", text,
        priority=pushover.PRIORITY_EMERGENCY,
        url=reminder_cancel_url(reservation.reservation_id),
    )


def notify_reservation_failure(
    watch: Watch, train: Train, error_msg: str,
    *, session: _PostSession | None = None,
) -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_reservation_failure(watch, train, error_msg)
    send_telegram(bot_token, chat_id, text, session=session)
    # Not time-critical — high priority so it still bypasses quiet hours, but
    # no repeat-until-acknowledged.
    pushover.send("❌ 임시예약 실패", text, priority=pushover.PRIORITY_HIGH)


def format_auto_reserve_disabled(watch: Watch) -> str:
    return (
        f"🔕 자동예약 비활성화\n"
        f"\n"
        f"{watch.from_}→{watch.to} {watch.date}\n"
        f"\n"
        f"자동예약이 실행되어 자동예약 기능이 비활성 되었습니다. "
        f"추가 자동예약이 필요하면 자동예약 기능을 다시 켜주세요."
    )


def notify_auto_reserve_disabled(
    watch: Watch, *, session: _PostSession | None = None,
) -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_auto_reserve_disabled(watch)
    send_telegram(bot_token, chat_id, text, session=session)


def format_auto_reserve_rearmed(watch: Watch) -> str:
    return (
        f"🔁 자동예약 재가동\n"
        f"\n"
        f"{watch.from_}→{watch.to} {watch.date}\n"
        f"\n"
        f"결제 마감이 지나 좌석이 해제됐습니다. 자동예약을 다시 켜고 "
        f"계속 찾습니다."
    )


def notify_auto_reserve_rearmed(
    watch: Watch, *, session: _PostSession | None = None,
) -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_auto_reserve_rearmed(watch)
    send_telegram(bot_token, chat_id, text, session=session)


PROVIDER_LABEL = {"korail": "코레일", "srt": "SRT"}


def format_login_failed(provider: str, error_msg: str) -> str:
    label = PROVIDER_LABEL.get(provider, provider)
    return (
        f"🚨 {label} 로그인 실패 — 감시 중단\n"
        f"\n"
        f"{label} 계정으로 로그인하지 못해 해당 노선을 확인하지 못하고 있습니다.\n"
        f"좌석이 나와도 알림이 가지 않습니다.\n"
        f"\n"
        f"사유: {error_msg}\n"
        f"\n"
        f"비밀번호 변경, 계정 잠금, 또는 통합회원 전환으로 자격증명이 바뀌었을 수 있습니다. "
        f"앱에서 로그인해 확인한 뒤 저장소 시크릿을 갱신해 주세요."
    )


def notify_login_failed(
    provider: str, error_msg: str, *, push: bool = False,
    session: _PostSession | None = None,
) -> None:
    """Telegram always; Pushover only when the user opted in.

    Pushover is off by default here even though it is on for seats. Pushover
    priority 1 bypasses its own quiet hours, so a login that broke at 3am woke
    the user for something they cannot act on until morning — the password is
    not going to change itself, and the watcher will re-alert every 24h until
    it is fixed. Waking someone for a non-actionable alert is how they learn to
    swipe the whole channel away, which is exactly the channel that has to work
    when a seat appears.
    """
    text = format_login_failed(provider, error_msg)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        send_telegram(bot_token, chat_id, text, session=session)
    if push:
        # High, never emergency: even opted in, a broken password does not get
        # a repeat-until-acknowledged alarm.
        pushover.send(f"🚨 {PROVIDER_LABEL.get(provider, provider)} 로그인 실패",
                      text, priority=pushover.PRIORITY_HIGH)


def format_login_recovered(provider: str) -> str:
    label = PROVIDER_LABEL.get(provider, provider)
    return f"✅ {label} 로그인 복구 — 감시를 재개했습니다."


def notify_login_recovered(
    provider: str, *, push: bool = False, session: _PostSession | None = None,
) -> None:
    """Same opt-in as the failure alert. Good news least of all deserves to
    bypass quiet hours."""
    text = format_login_recovered(provider)
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if bot_token and chat_id:
        send_telegram(bot_token, chat_id, text, session=session)
    if push:
        pushover.send("✅ 로그인 복구", text, priority=pushover.PRIORITY_HIGH)


def _fmt_deadline(iso: str | None) -> str:
    """Render a timezone-aware ISO timestamp as KST HH:MM (UTC+9).

    Korail / SRT return KST-tagged timestamps (e.g. "...+09:00"); fallbacks
    use UTC ("...Z").  We normalise to KST so the user sees the same time
    they'd see in 코레일톡 / SR 앱.
    """
    if not iso:
        return "—"
    from datetime import datetime, timedelta, timezone
    kst_tz = timezone(timedelta(hours=9))
    try:
        normalised = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalised)
        if dt.tzinfo is None:
            # Naive timestamp: our adapters always produce timezone-aware
            # ISO, so this only happens for manually edited / legacy data —
            # default to KST since that's what the rest of the system uses.
            dt = dt.replace(tzinfo=kst_tz)
        kst = dt.astimezone(kst_tz)
        return kst.strftime("%H:%M")
    except (ValueError, TypeError, AttributeError):
        try:
            return iso.split("T")[1][:5]
        except (IndexError, AttributeError):
            return iso


def is_quiet_hour_kst(now_iso: str, start_hhmm: str | None, end_hhmm: str | None) -> bool:
    """True if now (parsed from ISO) falls inside the user's quiet window
    in KST.  Window can wrap midnight (e.g. start=23:00, end=07:00).
    Empty/missing start or end disables the check."""
    if not start_hhmm or not end_hhmm:
        return False
    from datetime import datetime, timedelta, timezone

    try:
        dt = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    kst = dt.astimezone(timezone(timedelta(hours=9)))
    now_min = kst.hour * 60 + kst.minute
    start_min = _parse_hhmm(start_hhmm)
    end_min = _parse_hhmm(end_hhmm)
    if start_min is None or end_min is None:
        return False
    if start_min == end_min:
        return False  # zero-length window
    if start_min < end_min:
        return start_min <= now_min < end_min
    # Wraps midnight
    return now_min >= start_min or now_min < end_min


def _parse_hhmm(s: str) -> int | None:
    try:
        h, m = s.split(":")
        h_i, m_i = int(h), int(m)
        if 0 <= h_i < 24 and 0 <= m_i < 60:
            return h_i * 60 + m_i
    except (ValueError, AttributeError):
        pass
    return None


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value


def schedule_reminders(
    watch: Watch,
    train: Train,
    reservation: Reservation,
    *,
    session: _PostSession | None = None,
) -> None:
    """POST to the CF Worker /reminder/schedule endpoint.

    The Worker stores the reservation context in its REMINDERS KV and fires
    timed payment-deadline reminders (T+5/10/15/19 min from now, capped at
    the actual deadline) to Telegram on its 1-min cron.

    Silent no-op when:
      - CF_WORKER_URL or REMINDER_TOKEN env not set (feature disabled)
      - reservation.expires_at missing (no deadline → no schedule)
      - reservation.already_existed is True (don't re-schedule for repeats)

    Failures are caught and logged by the caller (main._process_watch); we
    raise here so callers see what went wrong, but the reserve-success
    notification has already gone out by the time we're called, so a
    failed schedule never blocks the success path.
    """
    cf_url = (os.environ.get("CF_WORKER_URL") or "").rstrip("/")
    token = os.environ.get("REMINDER_TOKEN") or ""
    if not cf_url or not token:
        return
    if reservation.already_existed or not reservation.expires_at:
        return
    payload = {
        "reservation_id": reservation.reservation_id,
        "deadline_iso": reservation.expires_at,
        "route": f"{watch.from_}→{watch.to}",
        "train": f"{train.train_type} {train.train_no} {train.dep_time} 발",
        "date": watch.date,
        "provider": watch.provider,
    }
    sess = session if session is not None else requests
    resp = sess.post(
        f"{cf_url}/reminder/schedule",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()
