from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from .catalog import normalize_code
from .models import MediaFile, Work

EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".m4v", ".ts", ".strm"}


SUFFIX = re.compile(r"-(UC|C|U|CD[0-9]+)$", re.IGNORECASE)


def parse_filename(path: Path) -> tuple[str, MediaFile]:
    code = path.stem
    subtitles = uncensored = False
    part = None
    while match := SUFFIX.search(code):
        token = match[1].upper()
        if token.startswith("CD"):
            number = int(token[2:])
            if part is not None and part != number:
                raise ValueError("分集后缀冲突")
            part = number
        else:
            subtitles |= token in {"C", "UC"}
            uncensored |= token in {"U", "UC"}
        code = code[:match.start()]
    code = validate_code(code)
    return code, MediaFile(id=hashlib.sha256(str(path).encode()).hexdigest(), path=path,
        filename=path.name, subtitles=subtitles, uncensored=uncensored, part=part)


def validate_code(value: str) -> str:
    # Only case and supported catalog spelling are normalized; no filename cleanup.
    if not value or value != value.strip() or any(c.isspace() for c in value):
        raise ValueError("文件名不是有效番号")
    try:
        normalized = normalize_code(value)
    except ValueError:
        raise ValueError("文件名不是有效番号") from None
    if normalized != value.upper():
        raise ValueError("文件名不是标准番号")
    return normalized


def validate_roots(roots: list[Path], output: Path, *, require_existing: bool = True) -> None:
    output = output.resolve()
    for root in roots:
        if require_existing and not root.is_dir():
            raise ValueError(f"media root is not a directory: {root}")
        root = root.resolve()
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise ValueError("media roots and image root must be separate directory trees")


def scan(roots: list[Path], extensions: set[str] | None = None) -> list[Work]:
    files: set[Path] = set()
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
                    files.add(path)
    works = {}
    for path in sorted(files):
        try:
            code, media = parse_filename(path)
        except ValueError as exc:
            key = "invalid:" + hashlib.sha256(str(path).encode()).hexdigest()
            works[key] = Work(code=key, media=[MediaFile(id=hashlib.sha256(str(path).encode()).hexdigest(), path=path, filename=path.name)],
                status="invalid_key", warnings=[str(exc)])
            continue
        if code not in works:
            works[code] = Work(code=code, media=[])
        works[code].media.append(media)
    return sorted(works.values(), key=lambda work: work.code)
