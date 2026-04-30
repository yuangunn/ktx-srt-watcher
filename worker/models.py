from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator, model_validator


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")

Provider = Literal["korail", "srt"]
SeatClass = Literal["general", "special", "any"]


class Passengers(BaseModel):
    model_config = ConfigDict(extra="forbid")

    adult: NonNegativeInt = 1
    child: NonNegativeInt = 0
    senior: NonNegativeInt = 0


class Watch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    provider: Provider
    from_: str = Field(alias="from")
    to: str
    date: str
    time_min: str
    time_max: str
    train_types: list[str]
    passengers: Passengers = Field(default_factory=Passengers)
    seat_class: SeatClass = "general"
    auto_reserve: bool = False
    active: bool = True

    @field_validator("date")
    @classmethod
    def _date_format(cls, v: str) -> str:
        if not _DATE_RE.match(v):
            raise ValueError("date must be YYYY-MM-DD")
        return v

    @field_validator("time_min", "time_max")
    @classmethod
    def _time_format(cls, v: str) -> str:
        if not _TIME_RE.match(v):
            raise ValueError("time must be HH:MM")
        return v

    @field_validator("train_types")
    @classmethod
    def _at_least_one_train_type(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("train_types must contain at least one entry")
        return v

    @model_validator(mode="after")
    def _time_order(self) -> Watch:
        if self.time_min > self.time_max:
            raise ValueError("time_min must be <= time_max")
        return self


class Train(BaseModel):
    provider: str
    train_no: str
    train_type: str
    dep_station: str
    arr_station: str
    date: str
    dep_time: str
    arr_time: str
    seats_general: NonNegativeInt = 0
    seats_special: NonNegativeInt = 0
    raw_id: str
    booking_url: str = ""

    @property
    def total_seats(self) -> int:
        return self.seats_general + self.seats_special


class Reservation(BaseModel):
    provider: str
    reservation_id: str
    train_no: str
    expires_at: str | None = None
