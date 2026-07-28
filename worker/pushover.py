"""Pushover delivery for alerts that must wake the user.

Telegram cannot do this. iOS only lets an app override the mute switch and
Do Not Disturb if Apple granted it the Critical Alerts entitlement, and
Telegram does not have it — no notification-sound setting or Focus exception
changes that. Pushover does have it, so time-critical alerts go here as well
as to Telegram.

Priorities used:
  1 (high)      bypasses Pushover's own quiet hours, alerts normally
  2 (emergency) repeats every RETRY_SEC until acknowledged in the app, and
                is delivered as an iOS critical alert when the user has
                enabled that for the app — this is the one that wakes you

Silent no-op when PUSHOVER_TOKEN / PUSHOVER_USER are unset, so the feature
is opt-in and an unconfigured install behaves exactly as before.
"""
from __future__ import annotations

import logging
import os
from typing import Protocol

import requests

log = logging.getLogger("ticket_watcher")

API_URL = "https://api.pushover.net/1/messages.json"

PRIORITY_HIGH = 1
PRIORITY_EMERGENCY = 2

# Emergency retry cadence. 60s is Pushover's minimum; the payment window is
# ~20 min, so expire a little short of it — an alert that keeps ringing after
# the seat is gone only teaches the user to ignore it.
RETRY_SEC = 60
EXPIRE_SEC = 900


class _PostSession(Protocol):
    def post(self, url: str, *, data: dict, timeout: int): ...


def enabled() -> bool:
    return bool(os.environ.get("PUSHOVER_TOKEN") and os.environ.get("PUSHOVER_USER"))


def send(
    title: str,
    message: str,
    *,
    priority: int = PRIORITY_HIGH,
    url: str | None = None,
    session: _PostSession | None = None,
) -> None:
    """Best-effort push. Never raises: Pushover is the backup channel and the
    Telegram message has usually already gone out by the time we're called."""
    if not enabled():
        return
    payload: dict = {
        "token": os.environ["PUSHOVER_TOKEN"],
        "user": os.environ["PUSHOVER_USER"],
        "title": title,
        "message": message,
        "priority": priority,
    }
    if priority == PRIORITY_EMERGENCY:
        payload["retry"] = RETRY_SEC
        payload["expire"] = EXPIRE_SEC
    if url:
        payload["url"] = url
    sess = session if session is not None else requests
    try:
        resp = sess.post(API_URL, data=payload, timeout=10)
        resp.raise_for_status()
    except Exception as e:
        log.error("pushover 전송 실패: %s", e)
