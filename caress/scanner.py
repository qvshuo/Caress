from __future__ import annotations

import os
from collections import defaultdict
from pathlib import Path

from pydantic import BaseModel, Field

from .catalog import normalize_code
from .models import Category

EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".strm"}


class Work(BaseModel):
    code: str
    path: Path
    media_files: list[Path]
    title: str | None = None
    actors: list[str] = Field(default_factory=list)
    poster: str | None = None
    status: str = "pending"
    category: Category = Category.UNKNOWN
    warnings: list[str] = Field(default_factory=list)


def validate_code(value: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or any(c in value for c in '/\\:*?"<>|')
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError("请将媒体所在文件夹改为正确番号。")
    if normalize_code(value) != value:
        raise ValueError("请将媒体所在文件夹改为正确番号。")
    return value


def validate_roots(roots: list[Path], output: Path, *, require_existing: bool = True) -> None:
    output = output.resolve()
    for root in roots:
        if require_existing and not root.is_dir():
            raise ValueError(f"media root is not a directory: {root}")
        root = root.resolve()
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("media roots and metadata root must be separate directory trees")


def scan(roots: list[Path], extensions: set[str] | None = None) -> list[Work]:
    folders: dict[Path, set[Path]] = defaultdict(set)
    for root in roots:
        if not root.is_dir():
            raise ValueError(f"media root is not a directory: {root}")

        def error(exc):
            raise exc

        for directory, dirs, filenames in os.walk(root.resolve(), followlinks=False, onerror=error):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(".")
                and d not in {"@eaDir", "#recycle"}
                and not (Path(directory) / d).is_symlink()
            )
            for name in filenames:
                path = Path(directory) / name
                if (
                    not name.startswith(".")
                    and path.suffix.lower() in (extensions or EXTENSIONS)
                    and not path.is_symlink()
                ):
                    folders[path.parent].add(path)
    works = []
    for folder, paths in sorted(folders.items()):
        work = Work(code=folder.name, path=folder, media_files=sorted(paths))
        try:
            work.code = validate_code(work.code)
        except ValueError:
            work.status = "invalid_key"
            work.warnings.append("请将媒体所在文件夹改为正确番号。")
        works.append(work)
    keys = defaultdict(list)
    for work in works:
        keys[work.code.casefold()].append(work)
    for group in keys.values():
        if len(group) > 1:
            for work in group:
                work.status = "conflict"
                work.warnings.append("番号重复，请检查媒体所在文件夹。")
    return works
