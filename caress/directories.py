"""Directory selection within fixed Docker mounts."""

from pathlib import Path

from .settings import Settings


class Directories:
    def __init__(self, media: list[Path], data: Path):
        self.media = [p.resolve() for p in media]
        self.data = data.resolve()

    def roots(self, kind: str) -> list[Path]:
        if kind == "media":
            return self.media
        if kind == "metadata":
            return [self.data]
        raise ValueError("unknown directory type")

    def checked(self, path: Path, kind: str) -> Path:
        resolved = path.resolve()
        if not any(resolved == root or resolved.is_relative_to(root) for root in self.roots(kind)):
            raise ValueError("directory is outside the available mounts")
        return resolved

    def list(self, kind: str, path: Path | None = None) -> dict:
        if path is None:
            return {
                "path": None,
                "parent": None,
                "directories": [{"name": p.name, "path": str(p)} for p in self.roots(kind) if p.is_dir()],
            }
        path = self.checked(path, kind)
        if not path.is_dir():
            raise ValueError("directory does not exist")
        try:
            entries = sorted(
                (
                    {"name": p.name, "path": str(p)}
                    for p in path.iterdir()
                    if not p.name.startswith(".") and p.is_dir() and not p.is_symlink()
                ),
                key=lambda p: p["name"].casefold(),
            )
        except PermissionError:
            raise ValueError("directory is not readable") from None
        parent = None if path in self.roots(kind) else str(path.parent)
        return {"path": str(path), "parent": parent, "directories": entries}

    def validate(self, settings: Settings) -> None:
        for path in settings.media_directories:
            self.checked(path, "media")
        if settings.metadata_directory is not None:
            self.checked(settings.metadata_directory, "metadata")
