from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, Literal

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
    OTHER = "other"
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


class ActorMapping(BaseModel):
    original_name: str
    name: str
    actor_id: str | None = None
    type: int | None = None


class Artwork(BaseModel):
    url: str
    role: Literal["cover", "poster", "sample"]
    source: SourceId
    referer: str


class Metadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    title: str
    original_title: str | None = None
    plot: str | None = None
    release_date: date | None = None
    runtime_minutes: int | None = Field(default=None, ge=0)
    # The work's production maker/brand; never its retailer or an actor's agency.
    studio: str | None = None
    series: str | None = None
    directors: list[str] = Field(default_factory=list)
    actors: list[Actor] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    genres: list[str] = Field(default_factory=list)
    artwork: list[Artwork] = Field(default_factory=list)
    classification: Classification = Field(default_factory=Classification)
    external_id: str | None = None
    source_url: str


class Candidate(BaseModel):
    code: str
    url: str
    title: str | None = None
    external_id: str | None = None


class SourceResult(BaseModel):
    translated_fields: list[str] = Field(default_factory=list)
    original_fields: dict[str, Any] = Field(default_factory=dict)
    elapsed_seconds: float | None = None
    source: SourceId
    requested_code: str
    status: Status
    metadata: Metadata | None = None
    message: str | None = None
    warnings: list[str] = Field(default_factory=list)
    fetched_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def report(self) -> dict:
        metadata = self.metadata
        return {"source": self.source.value, "status": self.status.value, "message": self.message,
                "elapsed_seconds": self.elapsed_seconds, "fetched_at": self.fetched_at.isoformat() if self.fetched_at else None,
                "has_plot": bool(metadata and metadata.plot),
                "has_cover": bool(metadata and any(a.role in ("cover", "poster") for a in metadata.artwork)),
                "sample_count": sum(a.role == "sample" for a in metadata.artwork) if metadata else 0}


class ScrapeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str
    source: SourceId | None = None
    source_url: str | None = None
    external_id: str | None = None
    category: Category | None = None


class ScrapeResult(BaseModel):
    code: str
    status: Status
    classification: Classification
    metadata: Metadata | None = None
    selected_source: SourceId | None = None
    results: list[SourceResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class SourceVariant(SourceResult):
    fetched_at: datetime | None = None
    translation_fingerprints: dict[str, str] = Field(default_factory=dict)
    translation_errors: list[dict] = Field(default_factory=list)
    processed_fields: dict[str, Any] = Field(default_factory=dict)
    actor_mappings: list[ActorMapping] = Field(default_factory=list)
    mapping_fingerprint: str = ""
    title_text: str | None = None
