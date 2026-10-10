"""Payment-deadline reminders, kept by the CF Worker.

A temporary hold has to be paid within ~20 minutes, and the watcher may not
run again inside that window. So it hands the deadline to the CF Worker
(/reminder/schedule), whose 1-minute cron sends the reminders, and every
message carries a signed link that stops them once the user has paid.
"""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Protocol

import requests

from .models import Reservation, Train, Watch


class PostSession(Protocol):
    def post(self, url: str, *, json: dict, timeout: int): ...


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


def schedule_reminders(
    watch: Watch,
    train: Train,
    reservation: Reservation,
    *,
    session: PostSession | None = None,
) -> None:
    """POST to the CF Worker /reminder/schedule endpoint.

    The Worker stores the reservation context in its REMINDERS KV and fires
    timed payment-deadline reminders (T+5/10/15/19 min from now, capped at
    the actual deadline) to Telegram on its 1-min cron.

    Silent no-op when:
      - CF_WORKER_URL or REMINDER_TOKEN env not set (feature disabled)
      - reservation.expires_at missing (no deadline → no schedule)
      - reservation.already_existed is True (don't re-schedule for repeats)

    Failures are caught and logged by the caller (worker.reservations); we
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
    sess = session if session is not None else requests
    resp = sess.post(
        f"{cf_url}/reminder/schedule",
        json=_schedule_payload(watch, train, reservation),
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    resp.raise_for_status()


def _schedule_payload(watch: Watch, train: Train, reservation: Reservation) -> dict:
    return {
        "reservation_id": reservation.reservation_id,
        "deadline_iso": reservation.expires_at,
        "route": f"{watch.from_}→{watch.to}",
        "train": f"{train.train_type} {train.train_no} {train.dep_time} 발",
        "date": watch.date,
        "provider": watch.provider,
    }
