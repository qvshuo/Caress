from __future__ import annotations

from contextlib import closing
from pathlib import Path
from typing import ClassVar
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .models import SourceId


class SiteSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str | None = None

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
            raise ValueError(
                "source address requires an HTTPS URL without credentials or query"
            )
        return value.rstrip("/")


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
    library_ids: list[str] = Field(default_factory=list)

    @field_validator("url")
    @classmethod
    def address(cls, value):
        if value:
            parsed = urlparse(value)
            if (
                parsed.scheme not in ("http", "https")
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("Emby requires an HTTP or HTTPS URL")
        return value.rstrip("/")


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    flaresolverr_url: str | None = "http://flaresolverr:8191/v1"
    sites: dict[SourceId, SiteSettings] = Field(default_factory=dict)
    javdb_api: ClassVar[ApiSettings] = ApiSettings()
    database: Path = Field(default=Path("/data/caress.sqlite3"), exclude=True)
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
                SourceId.ONEPONDO,
                SourceId.CARIBBEANCOM,
                SourceId.HEYZO,
                SourceId.FC2,
                SourceId.DMM,
                SourceId.JAVDB,
                SourceId.JAVBUS,
                SourceId.AVBASE,
                SourceId.AVSOX,
                SourceId.AIRAV,
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
        self.routes["censored"] = [
            source for source in self.routes["censored"] if source != SourceId.AVSOX
        ]
        for name in ("censored", "uncensored", "fc2", "unknown"):
            route = self.routes.get(name, [])
            if not route or len(set(route)) != len(route):
                raise ValueError(
                    f"route {name} must be nonempty without duplicate sources"
                )
        return self

    def save(self, path: Path | None = None, *, connection=None):
        import sqlite3

        if connection is None:
            path = path or self.database
            path.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(path, timeout=10)) as database, database:
                self.save(connection=database)
            return
        connection.execute(
            "CREATE TABLE IF NOT EXISTS options (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO options VALUES (1, ?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
            (self.model_dump_json(),),
        )

    @classmethod
    def load(cls, path: Path | None = None):
        import sqlite3

        path = path or Path("/data/caress.sqlite3")
        if not path.exists():
            return cls(database=path)
        with closing(sqlite3.connect(path, timeout=10)) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS options (id INTEGER PRIMARY KEY CHECK(id=1), data TEXT NOT NULL)"
            )
            row = connection.execute("SELECT data FROM options WHERE id=1").fetchone()
        try:
            value = cls.model_validate_json(row[0]) if row else cls()
        except ValidationError:
            raise ValueError(
                "配置不兼容或无效，请备份并通过 caress.import_settings 导入新数据库"
            ) from None
        value.database = path
        return value
