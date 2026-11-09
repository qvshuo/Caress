"""Transient projections of live Emby items, never a local media catalogue."""

import hashlib
import json
from pathlib import Path, PureWindowsPath
from urllib.parse import urlsplit

from .identifiers import parse_filename
from .models import MediaFile


def file_paths(item):
    paths = []
    path = item.get("Path")
    if path and (path.startswith("/") or PureWindowsPath(path).is_absolute()):
        paths.append(path)
    for source in item.get("MediaSources", []):
        path = source.get("Path", "")
        if str(source.get("ItemId", item["Id"])) != str(item["Id"]):
            continue
        if (
            source.get("Protocol", "File") == "File"
            and path
            and (path.startswith("/") or PureWindowsPath(path).is_absolute())
        ):
            paths.append(path)
    return list(dict.fromkeys(paths))


def identify(item):
    paths = file_paths(item)
    if not paths:
        raise ValueError("Emby 未提供实际视频或 STRM 文件路径")
    codes, media = set(), []
    for path in paths:
        if urlsplit(path).scheme and not PureWindowsPath(path).is_absolute():
            raise ValueError("无法从播放 URL 确定文件身份")
        code, row = parse_filename(Path(PureWindowsPath(path).as_posix()))
        codes.add(code)
        media.append(row)
    if len(codes) != 1:
        raise ValueError("同一 Item 的文件对应不同番号，请在 Emby 检查关联")
    if not item.get("DateCreated"):
        raise ValueError("Emby 未返回创建时间，无法安全识别重新入库项目")
    code = codes.pop()
    identity = hashlib.sha256(
        json.dumps([item["DateCreated"], code]).encode()
    ).hexdigest()
    return code, identity, media


def image_url(item, kind="Primary", index=0):
    tags = item.get("BackdropImageTags", []) if kind == "Backdrop" else []
    tag = tags[index] if index < len(tags) else item.get("ImageTags", {}).get(kind)
    if not tag:
        return None
    return f"/api/images/{item['Id']}/{kind}/{index}?tag={tag}"


def project(item, runtime, server):
    code = None
    error = None
    record = None
    media = [
        MediaFile(path=Path(p), filename=PureWindowsPath(p).name)
        for p in file_paths(item)
    ]
    try:
        code, identity, media = identify(item)
        record = runtime.record(server, str(item["Id"]), identity, code)
    except ValueError as exc:
        error = str(exc)
    status = "unsupported" if error else "pending"
    if record:
        status = (
            "failed"
            if record["status"] in ("failed", "cancelled")
            else "success"
            if record["succeeded_at"]
            else "pending"
        )

    return {
        "id": str(item["Id"]),
        "code": code,
        "title": item.get("Name", ""),
        "original_title": item.get("OriginalTitle", ""),
        "plot": item.get("Overview", ""),
        "release_date": item.get("PremiereDate", ""),
        "year": item.get("ProductionYear"),
        "studios": [s["Name"] for s in item.get("Studios", [])],
        "genres": [s["Name"] for s in item.get("GenreItems", [])],
        "tags": [s["Name"] for s in item.get("TagItems", [])],
        "people": [
            {
                "id": p.get("Id"),
                "name": p["Name"],
                "type": p.get("Type"),
                "image": f"/api/images/{p['Id']}/Primary/0?tag={p['PrimaryImageTag']}"
                if p.get("Id") and p.get("PrimaryImageTag")
                else None,
            }
            for p in item.get("People", [])
        ],
        "poster": image_url(item),
        "backdrops": [
            image_url(item, "Backdrop", i)
            for i in range(len(item.get("BackdropImageTags", [])))
        ],
        "media": [m.model_dump(mode="json") for m in media],
        "status": status,
        "error": error or (record["error"] if record else None),
        "last_result": record["status"] if record else None,
        "source": record["source"] if record else None,
        "succeeded_at": record["succeeded_at"] if record else None,
        "emby_url": "/web/index.html#!/item?id="
        + str(item["Id"])
        + "&serverId="
        + server,
    }


def filter_works(rows, query="", status=""):
    needle = query.casefold().strip()
    return [
        row
        for row in rows
        if (
            not needle
            or needle in (row["code"] or "").casefold()
            or needle in row["title"].casefold()
        )
        and (not status or row["status"] == status)
    ]


def neighbors(rows, ident):
    position = next(
        (index for index, row in enumerate(rows) if row["id"] == ident), None
    )

    def target(index):
        if index < 0 or index >= len(rows):
            return None
        row = rows[index]
        return {
            "id": row["id"],
            "label": row["code"] or row["title"],
            "position": index,
        }

    return {
        "previous": target(position - 1) if position is not None else None,
        "next": target(position + 1) if position is not None else None,
    }
