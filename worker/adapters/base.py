from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Passengers, Reservation, Train, Watch


@runtime_checkable
class Provider(Protocol):
    name: str

    def login(self, user_id: str, password: str) -> None: ...

    def search(self, watch: Watch) -> list[Train]: ...

    def reserve(
        self,
        train: Train,
        passengers: Passengers,
        *,
        allow_waiting: bool = False,
    ) -> Reservation: ...

    def paid_reservation_keys(self) -> set[str] | None:
        """Keys identifying reservations that are actually paid/ticketed.

        Used to tell "seat secured" from "hold expired unpaid": an unpaid hold
        vanishes after ~20 min, so auto-reserve must re-arm; a paid one means
        the job is done.

        A key is either a provider reservation id or a journey_key(). Ids alone
        are not enough: Korail's issued tickets carry no PNR at all, so a paid
        Korail hold can only be recognised by the journey it is for.

        Return **None** — not an empty set — when the lookup could not be
        performed. Empty means "nothing is paid", which sends the caller down
        the re-arm path and books a second ticket for a seat the user already
        owns. Absence of evidence must not read as evidence of absence.
        """
        ...


def journey_key(train_no: str | None, dep_date: str | None) -> str:
    """Stable "which train on which day" identity, comparable across a
    provider's reservation and ticket objects.

    Providers hand back dates in whatever shape their API uses (YYYYMMDD from
    Korail/SRT, YYYY-MM-DD from our own models), so digits only.
    """
    no = (train_no or "").strip()
    date = "".join(c for c in (dep_date or "") if c.isdigit())
    if not no or not date:
        return ""
    return f"{no}|{date}"
