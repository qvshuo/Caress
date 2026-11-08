"""Work artwork state and protection; images.py owns byte validation and rendering.

Role keys retain the file contract: poster = Poster, fanart = Original,
fanart1..100 = ordered Fanart. Retry input is pinned independently of variants.
"""
import hashlib
import re
from pathlib import Path

from .images import image_plan, save_images
from .models import ArtworkCandidate
from .storage import image_stems, target_folder

SAMPLE_ROLE = re.compile(r"fanart[1-9][0-9]{0,2}$")


def manifest(target, work):
    return [path.name for path in target.iterdir() if path.is_file() and not path.is_symlink()
            and path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
            and any(path.stem.startswith(stem + "-") for stem in image_stems(work))] if target.is_dir() else []


def state(selection):
    return selection.setdefault("artwork", {"roles": {}, "samples_protected": False})


def stage(selection, source, candidates, *, refresh):
    artwork = state(selection)
    if refresh:
        artwork["samples_protected"] = False
        for role in artwork["roles"].values():
            role["protected"] = False
    artwork.pop("pending", None)
    if candidates:
        roles = artwork["roles"]
        for _, role in image_plan([ArtworkCandidate.model_validate(item) for item in candidates]):
            existing = roles.setdefault(role, {})
            if existing.get("protected") or (artwork["samples_protected"] and SAMPLE_ROLE.fullmatch(role)):
                continue
            existing.update(status="pending")
            existing.pop("error", None)
        artwork["pending"] = {"source": source, "candidates": candidates}


def select_manual(selection, choices, records, *, replace_samples, variants, folder):
    artwork = state(selection)
    roles = artwork["roles"]
    if replace_samples:
        artwork["roles"] = roles = {role: value for role, value in roles.items() if not SAMPLE_ROLE.fullmatch(role)}
        artwork["samples_protected"] = True
    previous_roles = dict(roles)
    for role, choice in choices.items():
        candidate = next((value.get("candidate") for value in previous_roles.values()
                          if value.get("file") == choice.get("file")), None)
        if choice.get("index") is not None:
            candidate = variants[choice["source"]]["metadata"]["artwork"][choice["index"]]
        file = next(record["file"] for record in records if Path(record["file"]).stem.endswith("-" + role))
        roles[role] = {"file": file, "choice": choice, "protected": True,
                       "origin": "crop" if choice.get("box") else "local" if choice.get("file") else "selection",
                       "status": "ready", "candidate": candidate,
                       "fingerprint": hashlib.sha256((folder / file).read_bytes()).hexdigest()}


async def write(client, work, output, selection):
    artwork = state(selection)
    request = artwork.get("pending")
    if not request:
        raise ValueError("缺少待重试的图片输入，请选择图片来源后保存")
    roles = artwork["roles"]
    protected = {role for role, value in roles.items() if value.get("protected")}
    if artwork["samples_protected"]:
        protected.update(f"fanart{i}" for i in range(1, 101))
    target = target_folder(output, work.code)
    previous = [name for name in manifest(target, work) if not any(
        Path(name).stem == f"{stem}-{role}" for stem in image_stems(work) for role in protected)]
    records = await save_images(client, [ArtworkCandidate.model_validate(item) for item in request["candidates"]],
        target, image_stems(work), previous_files=previous, protected_roles=protected)
    failures = [record for record in records if record["status"] == "error"]
    if failures:
        artwork["error"] = "; ".join(record["message"] for record in failures)
        for _, role in image_plan([ArtworkCandidate.model_validate(item) for item in request["candidates"]]):
            if role not in protected:
                roles.setdefault(role, {}).update(status="failed", error=artwork["error"])
        return records
    artwork.pop("error", None)
    written = {record["file"] for record in records}
    for role in list(roles):
        if role not in protected and roles[role].get("file") not in written:
            del roles[role]
    candidates = [ArtworkCandidate.model_validate(item) for item in request["candidates"]]
    planned = {role: candidate for candidate, role in image_plan(candidates)}
    for record in records:
        role = Path(record["file"]).stem[len(work.code) + 1:]
        roles[role] = {"file": record["file"], "choice": {"source": request["source"], "index": candidates.index(planned[role])},
                       "candidate": planned[role].model_dump(mode="json"),
                       "fingerprint": hashlib.sha256((target / record["file"]).read_bytes()).hexdigest(),
                       "origin": "automatic", "protected": False, "status": "ready"}
    artwork.pop("pending", None)
    return records
