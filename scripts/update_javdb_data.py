"""Fetch JavDB lists and rebuild local actor or catalog JSON from scratch."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
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

from caress.catalog import CatalogRules
from caress.settings import ApiSettings
from caress.storage import atomic_write

LIMIT = 50
ACTOR_PAGES = range(1, 205)
CATALOG_PAGES = range(1, 1001)
CATEGORIES = {0: "censored", 1: "uncensored", 2: "other", 3: "fc2", 4: "other"}


def retry_delay(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "")
    try:
        return max(0, float(value))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
            return max(0, (date - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            return 10


class Api:
    def __init__(self, interval: float = 10):
        self.settings = ApiSettings()
        self.device = str(uuid.uuid4())
        self.interval = interval
        self.next_request = 0.0
        self.client = httpx.Client(timeout=60, follow_redirects=True, trust_env=False)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.client.close()

    def get(self, path: str, **query) -> bytes:
        settings = self.settings
        for attempt in range(5):
            time.sleep(max(0, self.next_request - time.monotonic()))
            self.next_request = time.monotonic() + self.interval
            stamp = str(int(time.time()))
            signature = hashlib.md5((stamp + settings.signature_prefix).encode(), usedforsecurity=False).hexdigest()
            try:
                with self.client.stream(
                    "GET", settings.base_url.rstrip("/") + path,
                    params={"app_channel": "official", "app_version": settings.app_version,
                            "app_version_number": settings.app_version_number,
                            "platform": "android", "system_version": "13",
                            "device_model": "Pixel 6", "device_name": "Pixel",
                            "device_uuid": self.device, **query},
                    headers={"jdsignature": f"{stamp}.{settings.signature_version}.{signature}",
                             "User-Agent": "Dart/3.4 (dart:io)", "Accept": "application/json",
                             "Accept-Language": "en-US"},
                ) as response:
                    response.raise_for_status()
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 8 * 1024 * 1024:
                            raise ValueError("JavDB response exceeds 8 MiB")
                return bytes(content)
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    if exc.response.status_code != 429 and exc.response.status_code < 500:
                        raise
                    self.next_request = max(self.next_request, time.monotonic() + retry_delay(exc.response))
                if attempt == 4:
                    raise
        raise AssertionError("unreachable")


def actor_path(folder: Path, kind: int, page: int) -> Path:
    return folder / f"type-{kind}-page-{page:03d}.json"


def actor_rows(content: bytes, kind: int) -> list[dict]:
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


def fetch_actors(folder: Path, interval: float) -> None:
    with Api(interval) as api:
        for kind in (0, 1):
            for page in ACTOR_PAGES:
                path = actor_path(folder, kind, page)
                content = path.read_bytes() if path.exists() else api.get(
                    "/api/v1/actors", type=kind, page=page, limit=LIMIT)
                rows = actor_rows(content, kind)
                atomic_write(path, content)
                print(f"type={kind} page={page} rows={len(rows)}", flush=True)


def normalized(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).strip()).casefold()


def build_actors(folder: Path, output: Path) -> None:
    actors = {}
    discarded = 0
    for kind in (0, 1):
        for page in ACTOR_PAGES:
            for source in actor_rows(actor_path(folder, kind, page).read_bytes(), kind):
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
                if previous is not None and previous["type"] != kind:
                    raise ValueError(f"actor ID crosses types: {ident}")
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
        "discarded": discarded,
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }, ensure_ascii=False))


def catalog_path(folder: Path, page: int) -> Path:
    return folder / f"page-{page:04d}.json"


def catalog_rows(content: bytes) -> list[dict]:
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("success") not in (1, True):
        raise ValueError("JavDB prefix response rejected or invalid")
    data = payload.get("data")
    rows = data.get("letters") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > LIMIT:
        raise ValueError("invalid prefix list")
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("letter"), str)
            or not row["letter"].strip() or type(row.get("type")) is not int
            or row["type"] not in CATEGORIES):
            raise ValueError("invalid prefix record")
    return rows


def fetch_catalog(folder: Path, interval: float) -> None:
    seen = set()
    with Api(interval) as api:
        for page in CATALOG_PAGES:
            path = catalog_path(folder, page)
            content = path.read_bytes() if path.exists() else api.get(
                "/api/v1/series/letters", page=page, limit=LIMIT)
            rows = catalog_rows(content)
            atomic_write(path, content)
            keys = tuple(sorted((row["letter"], row["type"]) for row in rows))
            print(f"page={page} rows={len(rows)}", flush=True)
            if not rows or keys in seen:
                return
            seen.add(keys)
    raise ValueError("prefix collection did not reach an empty or repeated page")


def build_catalog(folder: Path, output: Path) -> None:
    prefixes, seen = {}, set()
    for page in CATALOG_PAGES:
        rows = catalog_rows(catalog_path(folder, page).read_bytes())
        keys = tuple(sorted((row["letter"], row["type"]) for row in rows))
        if not rows or keys in seen:
            break
        seen.add(keys)
        for row in rows:
            prefix = row["letter"].strip().upper()
            if not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", prefix) or not re.search(r"[A-Z]", prefix):
                raise ValueError(f"invalid prefix: {prefix!r}")
            category = CATEGORIES[row["type"]]
            if prefix in prefixes and prefixes[prefix] != category:
                raise ValueError(f"conflicting prefix: {prefix}")
            prefixes[prefix] = category
    else:
        raise ValueError("prefix snapshots have no empty or repeated page")
    if not prefixes:
        raise ValueError("prefix list is empty")
    data = {"version": 6, **{
        f"{category}_prefixes": sorted(prefix for prefix, kind in prefixes.items() if kind == category)
        for category in dict.fromkeys(CATEGORIES.values())
    }}
    CatalogRules.model_validate(data)
    content = (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode()
    atomic_write(output, content)
    print(json.dumps({"prefixes": {key: len(value) for key, value in data.items() if isinstance(value, list)},
                      "sha256": hashlib.sha256(content).hexdigest()}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("resource", choices=("actors", "catalog"))
    parser.add_argument("command", choices=("fetch", "build"))
    parser.add_argument("--snapshots", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--interval", type=float, help="request interval in seconds (actors: 10, catalog: 5)")
    args = parser.parse_args()
    folder = args.snapshots.resolve()
    interval = args.interval if args.interval is not None else (10 if args.resource == "actors" else 5)
    if not math.isfinite(interval) or interval < 0:
        parser.error("interval must be finite and nonnegative")
    if folder.is_relative_to(ROOT):
        parser.error("snapshots must stay outside the repository")
    if args.command == "fetch":
        if args.output is not None:
            parser.error("--output is only available for build")
        fetch = fetch_actors if args.resource == "actors" else fetch_catalog
        fetch(folder, interval)
    else:
        filename = "actor_aliases.json" if args.resource == "actors" else "catalog_rules.json"
        output = (args.output or ROOT / "caress/data" / filename).resolve()
        if output.is_relative_to(folder):
            parser.error("output must be separate from raw snapshots")
        build = build_actors if args.resource == "actors" else build_catalog
        build(folder, output)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, httpx.HTTPError) as exc:
        sys.exit(f"error: {exc}")
    except KeyboardInterrupt:
        sys.exit(130)
