"""Docker entry point with configuration at a stable, mounted location."""

from pathlib import Path

from .settings import Settings


def load_settings(path: Path = Path("/data/caress.sqlite3")) -> Settings:
    return Settings.load(path)


def main() -> None:
    import uvicorn

    from .api import create_app
    from .directories import Directories
    from .logging import configure

    configure()
    Path("/data/images").mkdir(parents=True, exist_ok=True)
    uvicorn.run(
        create_app(
            load_settings(),
            settings_path=Path("/data/caress.sqlite3"),
            directories=Directories([Path("/")]),
        ),
        host="0.0.0.0",
        port=9307,
    )


if __name__ == "__main__":
    main()
