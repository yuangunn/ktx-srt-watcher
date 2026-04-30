from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..models import Reservation, Train, Watch


@runtime_checkable
class Provider(Protocol):
    name: str

    def login(self, user_id: str, password: str) -> None: ...

    def search(self, watch: Watch) -> list[Train]: ...

    def reserve(self, train: Train) -> Reservation: ...
