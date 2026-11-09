"""Docker entry point; all long-lived local state is the small SQLite ledger."""

from .settings import Settings


def main():
    import uvicorn

    from .api import create_app
    from .logging import configure

    configure()
    uvicorn.run(create_app(Settings.load()), host="0.0.0.0", port=9307)


if __name__ == "__main__":
    main()
