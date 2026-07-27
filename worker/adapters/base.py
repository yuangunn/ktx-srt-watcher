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

    def paid_reservation_ids(self) -> set[str]:
        """IDs of reservations that are actually paid/ticketed.

        Used to tell "seat secured" from "hold expired unpaid": an unpaid hold
        vanishes after ~20 min, so auto-reserve must re-arm; a paid one means
        the job is done. Implementations should return an empty set rather than
        raise when the lookup is unavailable.
        """
        ...
