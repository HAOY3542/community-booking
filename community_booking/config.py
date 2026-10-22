from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Facility(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    name: str = Field(min_length=1, max_length=80)
    resource: str = Field(pattern=r"^[a-z][a-z0-9-]{0,39}$")
    max_party: int = Field(ge=1, le=100)
    durations: list[int] = Field(min_length=1, max_length=12)
    guardian_required: bool = False


class SiteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    brand: str = Field(min_length=1, max_length=100)
    timezone: str
    open_minute: int = Field(ge=0, le=1410)
    close_minute: int = Field(ge=30, le=1440)
    slot_minutes: int = Field(ge=15, le=60)
    booking_days: int = Field(ge=1, le=90)
    cancel_notice_minutes: int = Field(ge=0, le=1440)
    checkin_early_minutes: int = Field(ge=0, le=120)
    agreement_version: str = Field(min_length=1, max_length=40)
    agreement_text: str = Field(min_length=1, max_length=4000)
    resources: dict[str, int]
    facilities: list[Facility] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def consistent(self):
        ZoneInfo(self.timezone)
        if self.close_minute <= self.open_minute or any(
            v % self.slot_minutes for v in (self.open_minute, self.close_minute, 1440)
        ):
            raise ValueError("opening hours must align to slots")
        if len({f.id for f in self.facilities}) != len(self.facilities):
            raise ValueError("duplicate facility")
        if not self.resources or any(type(v) is not int or not 1 <= v <= 100 for v in self.resources.values()):
            raise ValueError("invalid resource capacity")
        for f in self.facilities:
            if f.resource not in self.resources or f.max_party > self.resources[f.resource]:
                raise ValueError("invalid facility resource or party limit")
            if any(d <= 0 or d > 360 or d % self.slot_minutes for d in f.durations):
                raise ValueError("duration must align to slots and be at most six hours")
        return self


@dataclass(frozen=True)
class Settings:
    database: Path
    signing_key: bytes
    base_url: str
    site: SiteConfig
    accounts: dict[str, dict[str, str]]
    clock: Callable[[], datetime] | None = None

    def __post_init__(self):
        if not isinstance(self.signing_key, bytes) or len(self.signing_key) < 32:
            raise ValueError("An explicit independent signing key of at least 32 bytes is required")
        u = urlsplit(self.base_url)
        if (u.scheme != "http" or u.hostname not in {"127.0.0.1", "localhost"}
                or u.username or u.password or u.path or u.query or u.fragment or not u.port):
            raise ValueError("This candidate supports an explicit HTTP loopback origin only")
        for name, account in self.accounts.items():
            if not name or account.get("role") not in {"admin", "verifier"} or not account.get("password_hash", "").startswith("scrypt$"):
                raise ValueError("Staff accounts must have a role and a password hash")

    def now(self):
        value = self.clock() if self.clock else datetime.now(ZoneInfo(self.site.timezone))
        if value.tzinfo is None:
            raise ValueError("clock must return an aware datetime")
        return value.astimezone(ZoneInfo(self.site.timezone))
