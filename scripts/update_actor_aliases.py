"""Fetch JavDB actor lists or rebuild the bundled table from external snapshots."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

from caress.settings import ApiSettings
from caress.storage import atomic_write

PAGES = range(1, 205)
LIMIT = 50
INTERVAL = 10


def snapshot_path(folder: Path, kind: int, page: int) -> Path:
    return folder / f"type-{kind}-page-{page:03d}.json"


def snapshot_rows(content: bytes, kind: int) -> list[dict]:
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("success") not in (1, True):
        raise ValueError("JavDB actor response rejected or invalid")
    data = payload.get("data")
    rows = data.get("actors") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > LIMIT:
        raise ValueError("invalid actor list")
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid actor record")
        value = row.get("type")
        if type(value) is int and value in (0, 1) and value != kind:
            raise ValueError("actor type differs from requested list")
    return rows


def retry_delay(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        return max(0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            return max(0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return INTERVAL


def fetch(folder: Path) -> None:
    settings = ApiSettings()
    device = str(uuid.uuid4())
    next_request = 0.0
    with httpx.Client(timeout=60, follow_redirects=True, trust_env=False) as client:
        for kind in (0, 1):
            for page in PAGES:
                path = snapshot_path(folder, kind, page)
                if path.exists():
                    snapshot_rows(path.read_bytes(), kind)
                    print(f"cached type={kind} page={page}", flush=True)
                    continue
                for attempt in range(5):
                    time.sleep(max(0, next_request - time.monotonic()))
                    next_request = time.monotonic() + INTERVAL
                    stamp = str(int(time.time()))
                    signature = hashlib.md5(
                        (stamp + settings.signature_prefix).encode(), usedforsecurity=False
                    ).hexdigest()
                    try:
                        with client.stream(
                            "GET", settings.base_url.rstrip("/") + "/api/v1/actors",
                            params={
                                "app_channel": "official", "app_version": settings.app_version,
                                "app_version_number": settings.app_version_number,
                                "platform": "android", "system_version": "13",
                                "device_model": "Pixel 6", "device_name": "Pixel",
                                "device_uuid": device, "type": kind, "page": page, "limit": LIMIT,
                            },
                            headers={
                                "jdsignature": f"{stamp}.{settings.signature_version}.{signature}",
                                "User-Agent": "Dart/3.4 (dart:io)", "Accept": "application/json",
                                "Accept-Language": "en-US",
                            },
                        ) as response:
                            response.raise_for_status()
                            content = bytearray()
                            for chunk in response.iter_bytes():
                                content.extend(chunk)
                                if len(content) > 8 * 1024 * 1024:
                                    raise ValueError("actor response exceeds 8 MiB")
                        rows = snapshot_rows(content, kind)
                        atomic_write(path, bytes(content))
                        print(f"saved type={kind} page={page} rows={len(rows)}", flush=True)
                        break
                    except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                        if isinstance(exc, httpx.HTTPStatusError):
                            status = exc.response.status_code
                            if status != 429 and status < 500:
                                raise
                            next_request = max(next_request, time.monotonic() + retry_delay(exc.response))
                        if attempt == 4:
                            raise
                        print(f"retry type={kind} page={page} attempt={attempt + 1}: {type(exc).__name__}", file=sys.stderr, flush=True)


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def build(folder: Path, output: Path) -> None:
    actors = {}
    discarded = changed = 0
    for kind in (0, 1):
        for page in PAGES:
            for source in snapshot_rows(snapshot_path(folder, kind, page).read_bytes(), kind):
                name = source.get("name")
                if (type(source.get("type")) is not int or source["type"] not in (0, 1)
                    or not isinstance(name, str) or not name.strip()):
                    discarded += 1
                    continue
                ident = source.get("id")
                if not isinstance(ident, str) or not ident.strip() or ident != ident.strip():
                    raise ValueError("invalid actor ID")
                record = {"type": kind, "name": name.strip()}
                zht = source.get("name_zht")
                if zht is not None and not isinstance(zht, str):
                    raise ValueError(f"invalid name_zht: {ident}")
                zht = (zht or "").strip()
                if zht:
                    record["name_zht"] = zht
                raw = source.get("other_name")
                if raw is not None and not isinstance(raw, str):
                    raise ValueError(f"invalid other_name: {ident}")
                excluded = {normalized(record["name"]), normalized(zht)}
                unique = {}
                for alias in sorted(re.split("[,，]", raw or "")):
                    alias = alias.strip()
                    key = normalized(alias)
                    if key and key not in excluded:
                        unique.setdefault(key, alias)
                if unique:
                    record["other_name"] = [unique[key] for key in sorted(unique)]
                previous = actors.get(ident)
                if previous is not None:
                    if previous["type"] != kind:
                        raise ValueError(f"actor ID crosses types: {ident}")
                    changed += previous != record
                # Preserve the union; later pages win when the live list shifts during collection.
                actors[ident] = record
    if not actors or any(not any(row["type"] == kind for row in actors.values()) for kind in (0, 1)):
        raise ValueError("both actor types must contain valid records")
    ordered = sorted(actors, key=lambda ident: (actors[ident]["type"], ident))
    content = '{\n  "actors": {\n' + ',\n'.join(
        "    " + json.dumps(ident, ensure_ascii=False) + ":"
        + json.dumps(actors[ident], ensure_ascii=False, separators=(",", ":"))
        for ident in ordered
    ) + '\n  }\n}\n'
    atomic_write(output, content)
    print(json.dumps({
        "records": len(actors), "counts": {str(kind): sum(row["type"] == kind for row in actors.values()) for kind in (0, 1)},
        "discarded": discarded, "changed_duplicates": changed,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "build"))
    parser.add_argument("--snapshots", type=Path, required=True, help="raw snapshot directory outside the repository")
    parser.add_argument("--output", type=Path, help="build output (default: caress/data/actor_aliases.json)")
    args = parser.parse_args()
    folder = args.snapshots.resolve()
    if folder.is_relative_to(ROOT):
        parser.error("snapshots must stay outside the repository")
    if args.command == "fetch":
        if args.output is not None:
            parser.error("--output is only available for build")
        fetch(folder)
    else:
        output = (args.output or ROOT / "caress/data/actor_aliases.json").resolve()
        if output.is_relative_to(folder):
            parser.error("output must be separate from raw snapshots")
        build(folder, output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, httpx.HTTPError) as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)
