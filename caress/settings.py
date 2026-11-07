from __future__ import annotations

import re
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import SourceId

SOURCE_INTERVALS = {
    SourceId.DMM: 5,
    SourceId.JAVDB: 60,
    SourceId.JAVBUS: 15,
    SourceId.FC2: 10,
    SourceId.AVSOX: 30,
    SourceId.AIRAV: 15,
    SourceId.ONEPONDO: 5,
    SourceId.CARIBBEANCOM: 5,
    SourceId.HEYZO: 5,
    SourceId.AVBASE: 15,
}


class SiteSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None
    cookie: str = Field(default="", repr=False)
    interval_seconds: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @field_validator("base_url")
    @classmethod
    def address(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parsed = urlparse(value)
        if (
            value != value.strip()
            or parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("source address requires an HTTPS URL without credentials or query")
        return value.rstrip("/")

    @field_validator("cookie")
    @classmethod
    def validate_cookie(cls, value: str) -> str:
        if (
            value != value.strip()
            or "\n" in value
            or "\r" in value
            or value.lower().startswith("cookie:")
        ):
            raise ValueError("paste only the single-line Cookie value: name=value; name2=value2")
        seen = set()
        for part in value.split(";"):
            if not part.strip():
                continue
            name, separator, _ = part.strip().partition("=")
            if not separator or not re.fullmatch(r"[^\s=;]+", name) or name in seen:
                raise ValueError("cookie must contain unique name=value pairs")
            if name.lower() in {
                "path",
                "domain",
                "expires",
                "max-age",
                "samesite",
                "secure",
                "httponly",
            }:
                raise ValueError("paste the request Cookie value, not Set-Cookie")
            seen.add(name)
        return value

    def cookie_values(self) -> dict[str, str]:
        return dict(part.strip().split("=", 1) for part in self.cookie.split(";") if part.strip())


class ApiSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    base_url: str = "https://jdforrepam.com"
    app_version: str = "1.9.28"
    app_version_number: str = "10928"
    signature_prefix: str = Field(
        default="71cf27bb3c0bcdf207b64abecddc970098c7421ee7203b9cdae54478478a199e7d5a6e1a57691123c1a931c057842fb73ba3b3c83bcd69c17ccf174081e3d8aa",
        repr=False,
    )
    signature_version: str = "lpw6vgqzsp"


class TranslationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    endpoint: str = "https://api.openai.com/v1"
    model: str = ""
    api_key: str = Field(default="", repr=False)
    timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False)
    interval_seconds: float = Field(default=1, ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def configured(self) -> TranslationSettings:
        parsed = urlparse(self.endpoint)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("translation endpoint requires an HTTP or HTTPS URL")
        return self

    @property
    def enabled(self) -> bool:
        return bool(self.model.strip() and self.api_key.strip())


class EmbySettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = ""
    api_key: str = Field(default="", repr=False)

    @field_validator("url")
    @classmethod
    def address(cls, value):
        if value:
            parsed = urlparse(value)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError("Emby requires an HTTP or HTTPS URL")
        return value.rstrip("/")


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flaresolverr_url: str | None = "http://flaresolverr:8191/v1"
    timeout_seconds: float = Field(default=25, gt=0, allow_inf_nan=False)
    source_timeout_seconds: float = Field(default=180, ge=5, allow_inf_nan=False)
    retry_delay_seconds: int = Field(default=5, ge=1)
    retries: int = Field(default=1, ge=0, le=3)
    sites: dict[SourceId, SiteSettings] = Field(default_factory=dict)
    javdb_api: ClassVar[ApiSettings] = ApiSettings()
    database: Path = Field(default=Path("/data/caress.sqlite3"), exclude=True)
    media_directories: list[Path] = Field(default_factory=list)
    emby: EmbySettings = Field(default_factory=EmbySettings)
    translation: TranslationSettings = Field(default_factory=TranslationSettings)
    routes: dict[str, list[SourceId]] = Field(
        default_factory=lambda: {
            "censored": [
                SourceId.DMM,
                SourceId.JAVDB,
                SourceId.JAVBUS,
                SourceId.AVBASE,
                SourceId.AIRAV,
            ],
            "uncensored": [
                SourceId.ONEPONDO,
                SourceId.CARIBBEANCOM,
                SourceId.HEYZO,
                SourceId.JAVDB,
                SourceId.JAVBUS,
                SourceId.AVSOX,
                SourceId.AVBASE,
                SourceId.AIRAV,
            ],
            "fc2": [SourceId.FC2, SourceId.JAVDB, SourceId.AVSOX, SourceId.AIRAV],
            "unknown": [
                SourceId.ONEPONDO, SourceId.CARIBBEANCOM, SourceId.HEYZO, SourceId.FC2,
                SourceId.DMM, SourceId.JAVDB, SourceId.JAVBUS, SourceId.AVBASE,
                SourceId.AVSOX, SourceId.AIRAV,
            ],
        }
    )
    @field_validator("flaresolverr_url")
    @classmethod
    def solver_address(cls, value: str | None) -> str | None:
        value = value.strip() if value else None
        if not value:
            return None
        if not value.startswith(("http://", "https://")):
            raise ValueError("FlareSolverr requires an HTTP or HTTPS address")
        return value

    @model_validator(mode="after")
    def validate_routes(self) -> Settings:
        if set(self.routes) != {"censored", "uncensored", "fc2", "unknown"}:
            raise ValueError("routes must contain exactly the four catalog categories")
        if any(site != SourceId.JAVDB and settings.cookie for site, settings in self.sites.items()):
            raise ValueError("only JavDB supports a configured Cookie")
        self.routes["censored"] = [source for source in self.routes["censored"] if source != SourceId.AVSOX]
        for name in ("censored", "uncensored", "fc2", "unknown"):
            route = self.routes.get(name, [])
            if not route or len(set(route)) != len(route):
                raise ValueError(f"route {name} must be nonempty without duplicate sources")
        return self

    def source_interval(self, source: SourceId) -> float:
        configured = self.sites.get(source)
        return (
            configured.interval_seconds
            if configured and configured.interval_seconds is not None
            else SOURCE_INTERVALS[source]
        )

    def save(self, path: Path | None = None, *, connection=None):
        import sqlite3
        if connection is None:
            path = path or self.database
            path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(path, timeout=10) as database:
                self.save(connection=database)
            return
        connection.execute("CREATE TABLE IF NOT EXISTS options (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
        connection.execute("INSERT INTO options VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (self.model_dump_json(),))

    @classmethod
    def load(cls, path: Path | None = None):
        import sqlite3
        path = path or Path("/data/caress.sqlite3")
        if not path.exists():
            return cls(database=path)
        with sqlite3.connect(path, timeout=10) as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS options (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)")
            row = connection.execute("SELECT data FROM options WHERE id=1").fetchone()
        value = cls.model_validate_json(row[0]) if row else cls()
        value.database = path
        return value
