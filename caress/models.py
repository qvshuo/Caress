from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
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
    actor_ids: dict[str, str] = Field(default_factory=dict)
    translated_fields: list[str] = Field(default_factory=list)
    original_fields: dict[str, Any] = Field(default_factory=dict)
    elapsed_seconds: float | None = None
    source: SourceId
    requested_code: str
    status: Status
    metadata: SourceMetadata | None = None
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


class ScrapeResult(BaseModel):
    code: str
    status: Status
    classification: Classification
    metadata: SourceMetadata | None = None
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

EDITABLE_FIELDS = ("title", "original_title", "plot", "release_date", "year", "actors", "directors", "studio", "genres", "tags")


class MediaFile(BaseModel):
    id: str
    path: Path
    filename: str
    subtitles: bool = False
    uncensored: bool = False
    part: int | None = None


class Work(BaseModel):
    code: str
    media: list[MediaFile] = Field(default_factory=list)
    title: str | None = None
    actors: list[str] = Field(default_factory=list)
    poster: str | None = None
    status: str = "pending"
    category: Category = Category.UNKNOWN
    warnings: list[str] = Field(default_factory=list)




class ImageChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str
    index: int | None = Field(default=None, ge=0)
    file: str | None = None
    box: tuple[int, int, int, int] | None = None
    fingerprint: str | None = None


class ArtworkRoleState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file: str | None = None
    choice: ImageChoice | None = None
    candidate: ArtworkCandidate | None = None
    fingerprint: str | None = None
    origin: Literal["automatic", "selection", "local", "crop"] = "automatic"
    protected: bool = False
    status: Literal["pending", "ready", "failed"] = "pending"
    error: str | None = None


class ArtworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: SourceId
    candidates: list[ArtworkCandidate]


class ArtworkState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    roles: dict[str, ArtworkRoleState] = Field(default_factory=dict)
    samples_protected: bool = False
    pending: ArtworkRequest | None = None
    error: str | None = None


class WorkSelection(BaseModel):
    """Provenance and explicit protection; drafts never enter this record."""
    model_config = ConfigDict(extra="forbid")
    field_sources: dict[str, str] = Field(default_factory=dict)
    manual_fields: list[str] = Field(default_factory=list)
    original_fields: dict[str, Any] = Field(default_factory=dict)
    translated_fields: list[str] = Field(default_factory=list)
    translation_fingerprints: dict[str, str] = Field(default_factory=dict)
    actor_mappings: list[ActorMapping] = Field(default_factory=list)
    mapping_fingerprint: str = ""
    title_text: str | None = None
    artwork: ArtworkState = Field(default_factory=ArtworkState)


class WorkHealth(BaseModel):
    metadata: bool
    incomplete_stages: list[str] = Field(default_factory=list)
    artwork_problems: list[str] = Field(default_factory=list)
