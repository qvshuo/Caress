"""Typed actor names with conservative alias lookup."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from pathlib import Path

from .models import Actor, Category


def comparison_name(name: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", name).strip()).casefold()


def actor_type(category: Category | str | None) -> int | None:
    return {Category.CENSORED: 0, Category.UNCENSORED: 1}.get(category)


def actor_record(source: dict) -> dict | None:
    name = source.get("name")
    if type(source.get("type")) is not int or source["type"] not in (0, 1) or not isinstance(name, str) or not name.strip():
        return None
    record = {"type": source["type"], "name": name.strip()}
    zht = source.get("name_zht")
    if isinstance(zht, str) and zht.strip():
        record["name_zht"] = zht.strip()
    raw = source.get("other_name")
    values = re.split("[,，]", raw) if isinstance(raw, str) else raw or []
    if not isinstance(values, list) or any(not isinstance(alias, str) for alias in values):
        raise ValueError("invalid actor aliases")
    excluded = {comparison_name(record["name"]), comparison_name(record.get("name_zht", ""))}
    aliases = {}
    for alias in sorted(values):
        key = comparison_name(alias)
        if key and key not in excluded:
            aliases.setdefault(key, alias.strip())
    if aliases:
        record["other_name"] = [aliases[key] for key in sorted(aliases)]
    return record


class ActorAliases:
    def __init__(self, actors: dict[str, dict]):
        if not isinstance(actors, dict):
            raise ValueError("actor mapping must contain an actors object")
        self.actors = actors
        self._owners = {None: {}, 0: {}, 1: {}}
        self._records = {}
        self._record_ids = {}
        self._primary_names = {None: {}, 0: {}, 1: {}}
        self.fingerprint = hashlib.sha256(json.dumps(actors, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        self.all_names = set()
        for ident, record in actors.items():
            if (
                not isinstance(ident, str)
                or not ident.strip()
                or not isinstance(record, dict)
                or set(record) - {"type", "name", "name_zht", "other_name"}
                or type(record.get("type")) is not int
                or record["type"] not in (0, 1)
                or not isinstance(record.get("name"), str)
                or not record["name"].strip()
                or ("name_zht" in record and (
                    not isinstance(record["name_zht"], str) or not record["name_zht"].strip()
                ))
                or ("other_name" in record and (
                    not isinstance(record["other_name"], list) or not record["other_name"]
                    or any(not isinstance(name, str) or not name.strip() for name in record["other_name"])
                ))
            ):
                raise ValueError("actor mapping requires ID keys and type/name records with optional name_zht/other_name")
            primary = record["name"]
            for kind in (None, record["type"]):
                self._primary_names[kind].setdefault(comparison_name(primary), set()).add(primary)
            self._records.setdefault(primary, []).append(record)
            self._record_ids.setdefault(primary, []).append(ident)
            for name in self.record_names(record):
                self.all_names.add(name)
                for kind in (None, record["type"]):
                    self._owners[kind].setdefault(comparison_name(name), set()).add(primary)

    @staticmethod
    def record_names(record):
        return [name for name in [record["name"], record.get("name_zht"), *record.get("other_name", [])] if name]

    def reload(self, path: Path) -> None:
        self.__dict__.update(ActorAliases.load(path).__dict__)

    def needs_lookup(self, name: str, kind: int | None, ident: str | None = None) -> bool:
        return ident not in self.actors if ident else not self.candidates(name, kind)

    def remember(self, records: dict[str, dict], path: Path | None) -> None:
        if not records:
            return
        from .runtime import merge_rules
        def update(data):
            actors = {**data["actors"], **records}
            ActorAliases(actors)
            return {"actors": actors}
        value = merge_rules(path, "actors", update) if path else update({"actors": self.actors})
        self.__dict__.update(ActorAliases(value["actors"]).__dict__)

    @classmethod
    def load(cls, path: Path | None = None):
        from .runtime import read_rules
        seed = Path(__file__).parent / "data/actor_aliases.json"
        value = read_rules(path, "actors", seed) if path else json.loads(seed.read_text())
        return cls(value["actors"])

    def candidates(self, name: str, kind: int | None = None) -> list[str]:
        key = comparison_name(name)
        primary = self._primary_names[kind].get(key, set())
        if name in primary:
            return [name]
        if primary:
            return sorted(primary)
        matches = self._owners[kind].get(key, set())
        # Cross-type fallback accepts primary names only, after exhausting the typed lookup.
        if not matches and kind is not None:
            matches = self._primary_names[None].get(key, set())
        if not matches and (match := re.fullmatch(r"(.+?)\(([^()]+)\)", key)):
            parts = [self.candidates(part, kind) for part in match.groups()]
            if all(parts):
                matches = set().union(*parts)
        return sorted(matches or [])

    def resolve(self, name: str, kind: int | None = None) -> str | None:
        matches = self.candidates(name, kind)
        return matches[0] if len(matches) == 1 else None

    def resolve_id(self, name: str, kind: int | None = None) -> str | None:
        primary = self.resolve(name, kind)
        if primary is None:
            return None
        ids = self._record_ids[primary]
        typed = [ident for ident in ids if self.actors[ident]["type"] == kind]
        matches = typed or ids
        return matches[0] if len(matches) == 1 else None

    def aliases_for(self, name: str, kind: int | None = None) -> list[str]:
        primary = self.resolve(name, kind)
        if primary is None:
            return []
        records = self._records[primary]
        scoped = [record for record in records if record["type"] == kind]
        return list(dict.fromkeys(alias for record in (scoped or records)
                                  for alias in self.record_names(record)
                                  if self.resolve(alias, kind) == primary))

    def pending(self, issues: dict[str, dict]) -> dict[str, dict]:
        pending = {}
        for name, issue in issues.items():
            candidates = self.candidates(name, issue.get("type"))
            if len(candidates) > 1:
                pending[name] = {**issue, "status": "ambiguous", "candidates": candidates}
        return pending


def map_actor_names(actors: list[Actor], names: ActorAliases, kind=None, pinned=()):
    previous = {row["original_name"]: row for row in pinned}
    mapped, mappings, issues, seen = [], [], [], set()
    for actor in actors:
        # Saved IDs retain identity across renames; primary-name precedence applies to lookup.
        ident = previous.get(actor.name, {}).get("actor_id")
        if ident not in names.actors:
            ident = names.resolve_id(actor.name, kind)
        record = names.actors.get(ident)
        primary = record["name"] if record else names.resolve(actor.name, kind)
        name = primary or actor.name
        mappings.append({"original_name": actor.name, "name": name, "actor_id": ident,
                         "type": record["type"] if record else kind})
        if not primary and len(candidates := names.candidates(actor.name, kind)) > 1:
            issues.append({"name": actor.name, "type": kind, "status": "ambiguous", "candidates": candidates})
        if name not in seen:
            mapped.append(Actor(name=name))
            seen.add(name)
    return mapped, mappings, issues


def library_actors(works, output: Path, names: ActorAliases, query: str = "") -> list[dict]:
    from .portraits import actor_image, actor_image_index

    image_index = actor_image_index(output)
    invalid_names = set()
    portraits = {}

    def portrait(name):
        if name not in portraits:
            try:
                portraits[name] = actor_image(output, name, index=image_index)
            except ValueError:
                invalid_names.add(name)
                portraits[name] = None
        return portraits[name]

    rows = {}
    for work in works:
        try:
            actors = [Actor(name=name) for name in work.actors]
        except (ValueError, OSError):
            continue
        for actor in actors:
            row = rows.setdefault(
                actor.name, {"codes": set(), "types": set()}
            )
            row["codes"].add(work.code)
            row["types"].add(actor_type(work.category))

    needle = comparison_name(query)
    matched = {}
    if needle:
        for record in names.actors.values():
            if any(needle in comparison_name(value) for value in names.record_names(record)):
                matched[record["name"]] = {"actors": [], "codes": set(), "types": set()}
    represented = set()
    for name, row in rows.items():
        candidates = set().union(*(names.candidates(name, kind) for kind in row["types"]))
        if needle and not candidates.intersection(matched) and needle not in comparison_name(name):
            continue
        # Saved actor names remain the library identity, including when aliases match a search.
        target = matched.setdefault(name, {"actors": [], "codes": set(), "types": set()})
        target["actors"].append(name)
        target["codes"].update(row["codes"])
        target["types"].update(row["types"])
        represented.update(candidates)

    result = []
    for name, row in matched.items():
        if not row["actors"] and name in represented:
            continue
        candidates = sorted(set().union(*(names.candidates(name, kind) for kind in (row["types"] or {None}))))
        resolved = candidates[0] if len(candidates) == 1 else None
        candidates = candidates if len(candidates) > 1 else []
        portrait_name = next((actual for actual in row["actors"] if portrait(actual)), None)
        if not row["actors"] and portrait(name):
            portrait_name = name
        image = portrait(portrait_name) if portrait_name else None
        try:
            version = image.stat() if image else None
        except FileNotFoundError:
            version = None
        result.append(
            {
                "name": name,
                "actors": row["actors"],
                "portrait_name": portrait_name or next(iter(row["actors"]), name),
                "codes": sorted(row["codes"]),
                "count": len(row["codes"]),
                "portrait": version is not None,
                "portrait_status": "ready" if version else "missing",
                "portrait_version": f"{version.st_mtime_ns}-{version.st_size}" if version else None,
                "portrait_eligible": not candidates
                and not invalid_names.intersection(row["actors"] or [name]),
                "status": "ambiguous" if candidates else "known" if resolved else "original",
                "candidates": candidates,
            }
        )
    return sorted(result, key=lambda row: (-row["count"], not row["portrait"], row["name"]))
