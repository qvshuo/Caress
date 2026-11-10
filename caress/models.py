from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceId(StrEnum):
    DMM = "dmm"
    JAVDB = "javdb"
    JAVBUS = "javbus"
    FC2 = "fc2"
    AVSOX = "avsox"
    AIRAV = "airav"
    ONEPONDO = "1pondo"
    CARIBBEANCOM = "caribbeancom"
    HEYZO = "heyzo"
    AVBASE = "avbase"


class Category(StrEnum):
    CENSORED = "censored"
    UNCENSORED = "uncensored"
    FC2 = "fc2"
    UNKNOWN = "unknown"


class Status(StrEnum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    LOGIN_REQUIRED = "login_required"
    REGION_BLOCKED = "region_blocked"
    IP_BLOCKED = "ip_blocked"
    BLOCKED = "blocked"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    COOLDOWN = "cooldown"
    NETWORK_ERROR = "network_error"
    PARSE_ERROR = "parse_error"
    IDENTITY_MISMATCH = "identity_mismatch"
    UNSUPPORTED = "unsupported"


class Evidence(BaseModel):
    basis: str
    value: str
    source: SourceId | None = None


class Classification(BaseModel):
    category: Category = Category.UNKNOWN
    evidence: list[Evidence] = Field(default_factory=list)


class Actor(BaseModel):
    name: str


class ArtworkCandidate(BaseModel):
    url: str
    role: Literal["cover", "poster", "sample"]
    source: SourceId
    referer: str


class WorkMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    title: str
    original_title: str | None = None
    plot: str | None = None
    release_date: date | None = None
    year: int | None = Field(default=None, ge=1, le=9999)
    # The work's production maker/brand; never its retailer or an actor's agency.
    studio: str | None = None
    directors: list[str] = Field(default_factory=list)
    actors: list[Actor] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)


class SourceMetadata(WorkMetadata):
    """Unprocessed source fields and artwork candidates; never final work output."""

    artwork: list[ArtworkCandidate] = Field(default_factory=list)


class Candidate(BaseModel):
    code: str
    url: str
    title: str | None = None
    external_id: str | None = None


class SourceResult(BaseModel):
    actor_ids: dict[str, str | None] = Field(default_factory=dict)
    elapsed_seconds: float | None = None
    source: SourceId
    requested_code: str
    status: Status
    metadata: SourceMetadata | None = None
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    source: SourceId | None = None
    source_url: str | None = None
    external_id: str | None = None


class ScrapeResult(BaseModel):
    code: str
    status: Status
    classification: Classification
    metadata: SourceMetadata | None = None
    selected_source: SourceId | None = None
    results: list[SourceResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MediaFile(BaseModel):
    path: Path
    filename: str
    subtitles: bool = False
    uncensored: bool = False
    part: int | None = None
