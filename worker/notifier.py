from __future__ import annotations

import os
from typing import Protocol

import requests

from .models import Train, Watch


class _PostSession(Protocol):
    def post(self, url: str, *, json: dict, timeout: int): ...


def format_message(watch: Watch, trains: list[Train]) -> str:
    types_label = "/".join(watch.train_types)
    header = f"🚄 [{types_label}] {watch.from_}→{watch.to} {watch.date}"
    if not trains:
        return header

    lines = [header, ""]
    for t in trains:
        seat_parts: list[str] = []
        if t.seats_general > 0:
            seat_parts.append(f"일반 {t.seats_general}석")
        if t.seats_special > 0:
            seat_parts.append(f"특실 {t.seats_special}석")
        seats = " / ".join(seat_parts) if seat_parts else "잔여석 미상"
        lines.append(f"{t.dep_time} 발 {t.train_type} {t.train_no} / {seats}")
        if t.booking_url:
            lines.append(f"예매: {t.booking_url}")
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


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing required env var: {name}")
    return value
