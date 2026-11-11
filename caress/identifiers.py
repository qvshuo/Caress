from __future__ import annotations

import re
from pathlib import Path

from .catalog import normalize_code
from .models import MediaFile

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
                raise ValueError("文件名中的分集编号冲突")
            part = number
        else:
            subtitles |= token in {"C", "UC"}
            uncensored |= token in {"U", "UC"}
        code = code[: match.start()]
    if path.suffix.lower() not in EXTENSIONS:
        raise ValueError("不支持此文件格式")
    code = validate_code(code)
    return code, MediaFile(
        path=path,
        filename=path.name,
        subtitles=subtitles,
        uncensored=uncensored,
        part=part,
    )


def validate_code(value: str) -> str:
    # Only case and supported catalog spelling are normalized; no filename cleanup.
    if not value or value != value.strip() or any(c.isspace() for c in value):
        raise ValueError("无法从文件名识别有效番号")
    try:
        normalized = normalize_code(value)
    except ValueError:
        raise ValueError("无法从文件名识别有效番号") from None
    if normalized != value.upper():
        raise ValueError("文件名中的番号格式不符合要求")
    return normalized
