from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from .models import Actor, Metadata
from .contract import NFO_FIELDS
from .scanner import Work, validate_code
from .storage import atomic_write, file_transaction


def media_stems(work: Work) -> list[str]:
    return sorted({path.stem for path in work.media_files})


def nfo_paths(output: Path, work: Work) -> list[Path]:
    return [target_folder(output, work.code) / (stem + ".nfo") for stem in media_stems(work)]


def existing_nfo(output: Path, work: Work) -> Path:
    return next((p for p in nfo_paths(output, work) if p.is_file()), nfo_paths(output, work)[0])


def write_nfo_files(output: Path, work: Work, content: bytes) -> None:
    with file_transaction(target_folder(output, work.code)):
        for path in nfo_paths(output, work):
            if not path.is_file() or path.read_bytes() != content:
                atomic_write(path, content)


def xml_text(value: object) -> str:
    return "".join(
        c
        for c in str(value)
        if c in "\t\n\r"
        or 0x20 <= ord(c) <= 0xD7FF
        or 0xE000 <= ord(c) <= 0xFFFD
        or 0x10000 <= ord(c) <= 0x10FFFF
    )


def serialize_nfo(metadata: Metadata) -> bytes:
    if not xml_text(metadata.title).strip():
        raise ValueError("movie title must not be empty")
    root = ET.Element("movie")

    def add(name: str, value: object) -> None:
        if value is not None and str(value).strip():
            ET.SubElement(root, name).text = xml_text(value)

    add("title", metadata.title)
    add("originaltitle", metadata.original_title)
    add("plot", metadata.plot)
    if metadata.release_date:
        add("year", metadata.release_date.year)
        add("premiered", metadata.release_date.isoformat())
    add("runtime", metadata.runtime_minutes)
    for actor in metadata.actors:
        if actor.name.strip():
            node = ET.SubElement(root, "actor")
            ET.SubElement(node, "name").text = xml_text(actor.name)
    for director in metadata.directors:
        add("director", director)
    add("studio", metadata.studio)
    if metadata.series:
        ET.SubElement(ET.SubElement(root, "set"), "name").text = xml_text(metadata.series)
    for genre in metadata.genres:
        add("genre", genre)
    for tag in metadata.tags:
        add("tag", tag)
    if set(node.tag for node in root) - set(NFO_FIELDS.values()) - {"year"}:
        raise ValueError("NFO fields violate the Emby contract")
    if metadata.code:
        ET.SubElement(root, "uniqueid", {"type": "erosia", "default": "true"}).text = metadata.code
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def target_folder(output: Path, key: str) -> Path:
    validate_code(key)
    output = output.resolve()
    target = output / key
    if target.is_symlink() or not target.resolve().is_relative_to(output):
        raise ValueError("metadata target escapes output root")
    return target


def read_nfo(path: Path, code: str) -> Metadata:
    if path.is_symlink():
        raise ValueError("NFO must not be a symlink")
    limit = 4 * 1024 * 1024
    with path.open("rb") as stream:
        data = stream.read(limit + 1)
    if len(data) > limit or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise ValueError("unsafe or oversized NFO")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise ValueError("invalid movie NFO") from None
    if root.tag != "movie" or not (root.findtext("title") or "").strip():
        raise ValueError("invalid movie NFO")
    identifiers = [node.text for node in root.findall("uniqueid") if node.get("type") == "erosia"]
    if not identifiers or any(value != code for value in identifiers):
        raise ValueError("NFO number differs from directory")
    return Metadata(
        code=code,
        title=root.findtext("title"),
        original_title=root.findtext("originaltitle"),
        plot=root.findtext("plot"),
        release_date=root.findtext("premiered") or None,
        runtime_minutes=root.findtext("runtime") or None,
        studio=root.findtext("studio"),
        series=root.findtext("set/name"),
        directors=[e.text for e in root.findall("director") if e.text],
        actors=[
            Actor(
                name=e.findtext("name"),
            )
            for e in root.findall("actor")
            if (e.findtext("name") or "").strip()
        ],
        tags=[e.text for e in root.findall("tag") if e.text],
        genres=[e.text for e in root.findall("genre") if e.text],
        source_url="",
    )
