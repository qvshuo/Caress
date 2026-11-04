"""Fetch JavDB catalog prefixes or rebuild local rules from external snapshots."""

from __future__ import annotations

import argparse
import math
import hashlib
import json
import re
import sys
import time
import uuid
import unicodedata
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))

from caress.catalog import CatalogRules, catalog_prefix, normalize_code
from caress.storage import atomic_write
from caress.settings import ApiSettings
from update_actor_aliases import retry_delay

LIMIT = 50
INTERVAL = 10
CATEGORIES = {0: "censored", 1: "uncensored", 2: "other", 3: "fc2", 4: "other"}
# Verified families supplement prefixes absent from the provider's index.
SEEDS = {
    "censored": "300MIUM ABF ABP ABW ACHJ ACZD ADN APNS APPT ARAN ATID AVOP AVSA BBAN BBI BDA BOKD BTIS CAWB CAWD CJOD CMC DAA DASD DASS DBER DDFF DLDSS DNJR DSVR DVAJ DVMM EBOD EBWH EYAN FEM FNS FSDSS FWAY GMA GTJ HMN HND HOWS HSM HSODA HUBLK HUNBL IESM IPTD IPVR IPX IPZ IPZZ ISRD JBD JUFE JUL JUQ JUR KAWD KBR KUSE KV LULU MDHR MDYD MEYD MFYD MIAA MIAB MIDA MIDE MIDV MIID MIKR MIMK MIRD MISM MIZD MKMP MNGS MOOC MSFH MUDR MUKA MUKC MVG MVSD MXGS NAMH NGOD NHDTB NIMA NMSL NPJS NVH OAE OFES OMHD OPPW PFES PGD PPPD PPT PRED PXH QRDA RBK RCKK REAL RKI ROYD SAME SDDE SDMU SHKD SNIS SNOS SONE SORA SSIS SSNI STARS START STSD STZY SXMA TANF TCD TIKB TIMD TOTK TPNS URE URKK VDD WAAA WANZ WAVR".split(),
    "uncensored": "10MU 10MUSUME 1PONDO CARIB CARIBBEANCOM CARIBBEANCOMPR CWP CWPBD EMP GACHI GACHINCO HEYDOUGA HEYZO KIN8 LAFBD LEGSJAPAN MKBD-S MYWIFE N PACO PACOPACOMAMA RED SKY SKYHD SMBD TOKYOHOT XXX-AV".split(),
    "fc2": ["FC2PPV"],
    "other": [],
}


def snapshot_rows(content: bytes) -> list[dict]:
    payload = json.loads(content)
    if not isinstance(payload, dict) or payload.get("success") not in (1, True):
        raise ValueError("JavDB prefix response rejected or invalid")
    data = payload.get("data")
    rows = data.get("letters") if isinstance(data, dict) else None
    if not isinstance(rows, list) or len(rows) > LIMIT:
        raise ValueError("invalid prefix list")
    for row in rows:
        if (not isinstance(row, dict) or not isinstance(row.get("letter"), str)
            or not row["letter"].strip()):
            raise ValueError("invalid prefix record")
    return rows


class Api:
    def __init__(self, interval: float):
        self.settings = ApiSettings()
        self.device = str(uuid.uuid4())
        self.interval = interval
        self.next_request = 0.0
        self.client = httpx.Client(timeout=60, follow_redirects=True, trust_env=False)

    def get(self, path: str, **query) -> dict:
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
                            raise ValueError("prefix response exceeds 8 MiB")
                payload = json.loads(content)
                if not isinstance(payload, dict) or payload.get("success") not in (1, True) or not isinstance(payload.get("data"), dict):
                    raise ValueError("JavDB response rejected or invalid")
                return payload
            except (httpx.RequestError, httpx.HTTPStatusError) as exc:
                if isinstance(exc, httpx.HTTPStatusError):
                    if exc.response.status_code != 429 and exc.response.status_code < 500:
                        raise
                    self.next_request = max(self.next_request, time.monotonic() + retry_delay(exc.response))
                if attempt == 4:
                    raise
                print(f"retry {path} attempt={attempt + 1}", file=sys.stderr, flush=True)
        raise AssertionError("unreachable")


def evidence_path(folder: Path, prefix: str) -> Path:
    return folder / "evidence" / (hashlib.sha256(prefix.encode()).hexdigest() + ".json")


def indexed_prefixes(folder: Path) -> set[str]:
    collection = json.loads((folder / "collection.json").read_bytes())
    return {row["letter"].strip().upper()
            for name in collection["sha256"]
            for row in snapshot_rows((folder / name).read_bytes())}


def usable(prefix: str) -> bool:
    return bool(re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", prefix) and re.search(r"[A-Z]", prefix))


def sample_code(value: str) -> str:
    try:
        return normalize_code(value)
    except ValueError:
        code = unicodedata.normalize("NFKC", value).strip().upper()
        # Source samples may carry edition letters that runtime folder validation rejects.
        if re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*-\d{2,8}[A-Z]{1,8}", code):
            return code
        raise


def matches_prefix(code: str, prefix: str) -> bool:
    actual = catalog_prefix(code)
    if prefix == "FC2" and code.startswith("FC2PPV-"):
        return True
    return actual == prefix or code.startswith(prefix + "-") or re.fullmatch(r"\d+" + re.escape(prefix), actual) is not None


def verified_movie(proof: dict, prefix: str) -> dict | None:
    if proof.get("prefix") != prefix:
        raise ValueError("prefix evidence identity mismatch")
    listing = proof.get("listing", {})
    rows = (listing.get("data") or {}).get("movies")
    if listing.get("success") not in (1, True) or not isinstance(rows, list):
        raise ValueError("invalid prefix listing evidence")
    candidates = []
    for row in rows:
        try:
            code = sample_code(row["number"])
        except (KeyError, TypeError, ValueError):
            continue
        if matches_prefix(code, prefix):
            candidates.append(row)
    detail = proof.get("detail")
    if detail is None and not candidates:
        return None
    if not isinstance(detail, dict) or detail.get("success") not in (1, True):
        raise ValueError("missing or rejected movie evidence")
    movie = (detail.get("data") or {}).get("movie")
    if (not isinstance(movie, dict) or type(movie.get("type")) is not int or movie["type"] not in CATEGORIES):
        raise ValueError("invalid verified catalog type")
    code = sample_code(movie.get("number", ""))
    if not any(str(row.get("id")) == str(movie.get("id")) and sample_code(row["number"]) == code for row in candidates):
        raise ValueError("prefix sample identity differs from listing")
    return movie


def fetch(folder: Path, interval: float) -> None:
    api = Api(interval)
    try:
        hashes, seen, records = {}, {}, 0
        for page in range(1, 1001):
            path = folder / f"page-{page:04d}.json"
            content = path.read_bytes() if path.exists() else json.dumps(
                api.get("/api/v1/series/letters", page=page, limit=LIMIT), ensure_ascii=False).encode()
            rows = snapshot_rows(content)
            digest = tuple(sorted(row["letter"] for row in rows))
            atomic_write(path, bytes(content))
            hashes[path.name] = hashlib.sha256(content).hexdigest()
            if not rows or digest in seen:
                atomic_write(folder / "collection.json", json.dumps({
                    "boundary": "empty" if not rows else "repeated",
                    "repeated_page": seen.get(digest), "endpoint": "/api/v1/series/letters",
                    "limit": LIMIT, "terminal_page": page, "records": records, "sha256": hashes,
                }, indent=2).encode())
                print(f"boundary={'empty' if not rows else 'repeated'} page={page} total={records}", flush=True)
                break
            seen[digest] = page
            records += len(rows)
        else:
            raise ValueError("prefix collection did not reach a verified boundary")
        known = set().union(*map(set, SEEDS.values()))
        pending = sorted(prefix for prefix in indexed_prefixes(folder) if usable(prefix) and prefix not in known)
        for index, prefix in enumerate(pending, 1):
            target = evidence_path(folder, prefix)
            if target.exists() and json.loads(target.read_bytes()).get("detail") is not None:
                verified_movie(json.loads(target.read_bytes()), prefix)
                continue
            listing = api.get("/api/v1/movies/tags", filter_by=f"0:c:{prefix}:",
                              sort_by="release", order_by="desc", page=1, limit=3)
            movies = listing["data"].get("movies")
            if not isinstance(movies, list):
                raise ValueError("invalid prefix movie list")
            selected = None
            for movie in movies:
                try:
                    code = sample_code(movie["number"])
                except (ValueError, KeyError, TypeError):
                    continue
                if matches_prefix(code, prefix):
                    selected = movie
                    break
            detail = None
            if selected is not None:
                ident = str(selected.get("id", ""))
                if not re.fullmatch(r"[A-Za-z0-9]+", ident):
                    raise ValueError("invalid prefix sample ID")
                detail = api.get(f"/api/v4/movies/{ident}")
            proof = {"prefix": prefix, "listing": listing, "detail": detail}
            verified_movie(proof, prefix)
            atomic_write(target, json.dumps(proof, ensure_ascii=False).encode())
            kind = CATEGORIES[detail["data"]["movie"]["type"]] if detail else "unclassified"
            print(f"verified={index}/{len(pending)} prefix={prefix} category={kind}", flush=True)
        atomic_write(folder / "verification.json", json.dumps({
            "samples_per_prefix": 1,
            "sha256": {prefix: hashlib.sha256(evidence_path(folder, prefix).read_bytes()).hexdigest() for prefix in pending},
        }, indent=2).encode())
    finally:
        api.client.close()


def build(folder: Path, output: Path) -> None:
    complete = json.loads((folder / "collection.json").read_bytes())
    terminal = complete["terminal_page"]
    if (type(terminal) is not int or not 2 <= terminal <= 1000
        or complete.get("endpoint") != "/api/v1/series/letters" or complete.get("limit") != LIMIT):
        raise ValueError("invalid collection manifest")
    expected = {f"page-{page:04d}.json" for page in range(1, terminal + 1)}
    if set(complete["sha256"]) != expected:
        raise ValueError("incomplete snapshot manifest")
    verification = json.loads((folder / "verification.json").read_bytes())
    known = set().union(*map(set, SEEDS.values()))
    pending = {prefix for prefix in indexed_prefixes(folder) if usable(prefix) and prefix not in known}
    if set(verification["sha256"]) != pending or verification.get("samples_per_prefix") != 1:
        raise ValueError("incomplete prefix verification")
    owners = {}
    for category, prefixes in SEEDS.items():
        for prefix in prefixes:
            owners.setdefault(prefix, set()).add(category)
    count, rejected, seen = 0, [], {}
    native_counts = {}
    for page in range(1, terminal + 1):
        name = f"page-{page:04d}.json"
        content = (folder / name).read_bytes()
        if hashlib.sha256(content).hexdigest() != complete["sha256"][name]:
            raise ValueError(f"snapshot checksum mismatch: {name}")
        rows = snapshot_rows(content)
        digest = tuple(sorted(row["letter"] for row in rows))
        if page == terminal:
            boundary = complete.get("boundary")
            if boundary == "empty" and not rows:
                continue
            if boundary == "repeated" and rows and seen.get(digest) == complete.get("repeated_page"):
                continue
            raise ValueError("unverified collection boundary")
        if not rows or digest in seen:
            raise ValueError("unexpected empty or repeated prefix page")
        seen[digest] = page
        count += len(rows)
        for row in rows:
            prefix = row["letter"].strip().upper()
            if not usable(prefix):
                rejected.append(row["letter"])
                continue
            if any(prefix in values for values in SEEDS.values()):
                continue
            content = evidence_path(folder, prefix).read_bytes()
            if hashlib.sha256(content).hexdigest() != verification["sha256"][prefix]:
                raise ValueError("prefix evidence checksum mismatch")
            movie = verified_movie(json.loads(content), prefix)
            if movie is None:
                rejected.append(prefix)
                continue
            kind = movie["type"]
            actual = catalog_prefix(sample_code(movie["number"]))
            native_counts[str(kind)] = native_counts.get(str(kind), 0) + 1
            owners.setdefault(prefix, set()).add(CATEGORIES[kind])
            if usable(actual):
                owners.setdefault(actual, set()).add(CATEGORIES[kind])
    if count != complete["records"] or not count:
        raise ValueError("snapshot record count differs from manifest")
    conflicts = {prefix: sorted(kinds) for prefix, kinds in owners.items() if len(kinds) > 1}
    if conflicts:
        raise ValueError(f"conflicting prefix classifications: {conflicts}")
    data = {"version": 5, **{
        f"{category}_prefixes": sorted(prefix for prefix, kinds in owners.items() if kinds == {category})
        for category in ("censored", "uncensored", "fc2", "other")
    }}
    CatalogRules.model_validate(data)
    atomic_write(output, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())
    print(json.dumps({"records": count, "boundary": complete["boundary"], "native_types": native_counts,
                      "prefixes": {category: len(data[f"{category}_prefixes"]) for category in SEEDS},
                      "rejected": sorted(set(rejected)),
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "build"))
    parser.add_argument("--snapshots", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--interval", type=float, default=5, help="request interval in seconds")
    args = parser.parse_args()
    folder = args.snapshots.resolve()
    if not math.isfinite(args.interval) or args.interval < 0:
        parser.error("interval must be finite and nonnegative")
    if folder.is_relative_to(ROOT):
        parser.error("snapshots must stay outside the repository")
    if args.command == "fetch":
        if args.output is not None:
            parser.error("--output is only available for build")
        fetch(folder, args.interval)
    else:
        output = (args.output or ROOT / "caress/data/catalog_rules.json").resolve()
        if output.is_relative_to(folder):
            parser.error("output must be separate from raw snapshots")
        build(folder, output)


if __name__ == "__main__":
    main()
