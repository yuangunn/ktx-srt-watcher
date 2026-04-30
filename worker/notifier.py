from __future__ import annotations

import os
from typing import Protocol

import requests

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


def send_telegram(bot_token: str, chat_id: str, text: str, *, session: _PostSession | None = None) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "disable_web_page_preview": True}
    sess = session if session is not None else requests
    resp = sess.post(url, json=payload, timeout=10)
    resp.raise_for_status()


def notify(watch: Watch, trains: list[Train], *, session: _PostSession | None = None) -> None:
    if not trains:
        return
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_message(watch, trains)
    send_telegram(bot_token, chat_id, text, session=session)


def format_summary(watches: list[Watch]) -> str:
    if not watches:
        return "🔍 확인 완료 · 활성 워치 없음"
    lines = ["🔍 확인 완료 · 잔여 0건", ""]
    for w in watches:
        types = "/".join(w.train_types)
        lines.append(f"[{types}] {w.from_}→{w.to} {w.date} {w.time_min}–{w.time_max}")
    return "\n".join(lines)


def notify_summary(watches: list[Watch], *, session: _PostSession | None = None) -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_summary(watches)
    send_telegram(bot_token, chat_id, text, session=session)


def format_reservation_success(watch: Watch, train: Train, reservation: Reservation) -> str:
    deadline = _fmt_deadline(reservation.expires_at)
    app_name = "코레일톡" if watch.provider == "korail" else "SR 앱"
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


def notify_reservation_failure(
    watch: Watch, train: Train, error_msg: str,
    *, session: _PostSession | None = None,
) -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    chat_id = _require_env("TELEGRAM_CHAT_ID")
    text = format_reservation_failure(watch, train, error_msg)
    send_telegram(bot_token, chat_id, text, session=session)


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
