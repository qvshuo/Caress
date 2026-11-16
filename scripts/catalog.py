"""Manually collect public catalog evidence and build the static routing whitelist."""

import argparse
import asyncio
import json
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from lxml import html

from caress.catalog import normalize_code


def build(rows):
    evidence = {}
    labels = defaultdict(set)
    for row in rows:
        category = row["category"]
        url = urlsplit(row["url"])
        if (category, url.hostname) not in {
            ("censored", "javdb.com"), ("uncensored", "avsox.click")
        }:
            raise ValueError("Evidence must come from JavDB censored or AVSOX uncensored records")
        try:
            code = normalize_code(row["code"])
        except ValueError:
            continue
        if code.startswith("FC2PPV-") or re.fullmatch(r"[0-9]+[-_][0-9]+|HEYZO-\d{4}", code):
            continue
        prefix = re.sub(r"-?\d+$", "", code)
        if not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z]+)?", prefix):
            continue
        labels[prefix].add(category)
        evidence.setdefault((prefix, category), {"code": code, "category": category, "url": row["url"]})
    conflicts = sorted(p for p, categories in labels.items() if len(categories) > 1)
    rules = {
        category: sorted(p for p, categories in labels.items() if categories == {category})
        for category in ("censored", "uncensored")
    }
    return rules, [evidence[key] for key in sorted(evidence)], conflicts


async def collect(source, pages):
    rows = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        for index, page in enumerate(pages):
            if index:
                await asyncio.sleep(2)
            if source == "javdb":
                response = await client.get(f"https://javdb.com/censored?vft=0&page={page}")
            else:
                response = await client.post(
                    "https://avsox.click/javu/data/api/getMovies",
                    json=[{"lang": "cn"}, 60, page],
                )
            if response.status_code != 200:
                raise ValueError(f"Collection stopped: HTTP {response.status_code}; no output written")
            if source == "javdb":
                tree = html.fromstring(response.content)
                selected = tree.xpath('//div[contains(@class,"main-tabs")]//li[contains(@class,"is-active")]/a/@href')
                if "/censored" not in selected or not tree.xpath('//div[contains(@class,"movie-list")]'):
                    raise ValueError("JavDB censored listing unavailable; no output written")
                current = []
                for box in tree.xpath('//div[contains(@class,"movie-list")]//a[contains(@class,"box")]'):
                    codes = box.xpath('.//div[contains(@class,"video-title")]/strong/text()')
                    if codes:
                        current.append({"code": codes[0].strip(), "category": "censored", "url": "https://javdb.com" + box.attrib["href"]})
            else:
                data = response.json()
                if data.get("code") != 200 or not isinstance(data.get("data"), list):
                    raise ValueError("AVSOX listing unavailable; no output written")
                current = [
                    {"code": work["movieFanHao"], "category": "uncensored", "url": "https://avsox.click/cn/movies/" + work["movieId"]}
                    for work in data["data"]
                ]
            print(f"{source} page {page}: {len(current)} records", flush=True)
            rows.extend(current)
            if not current:
                break
    return rows


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("collect")
    fetch.add_argument("source", choices=["javdb", "avsox"])
    fetch.add_argument("--pages", type=int, nargs="+", required=True)
    fetch.add_argument("--output", type=Path, required=True)
    generate = commands.add_parser("build")
    generate.add_argument("inputs", type=Path, nargs="+")
    generate.add_argument("--rules", type=Path, required=True)
    generate.add_argument("--samples", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "collect":
        if any(page < 1 for page in args.pages):
            parser.error("pages must be positive")
        write(args.output, asyncio.run(collect(args.source, args.pages)))
    else:
        rows = [row for path in args.inputs for row in json.loads(path.read_text())]
        rules, samples, conflicts = build(rows)
        write(args.rules, rules)
        write(args.samples, samples)
        print(json.dumps({"counts": {k: len(v) for k, v in rules.items()}, "conflicts": conflicts}))


if __name__ == "__main__":
    main()
