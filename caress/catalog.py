from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .models import Category

FC2 = re.compile(r"(?i)^FC2[-_ ]*(?:PPV[-_ ]*)?(\d{5,10})$")
STANDARD = re.compile(r"^([A-Z0-9]*[A-Z][A-Z0-9]*(?:-[A-Z]+)?)[-_](\d{2,8})$")
CONCATENATED = re.compile(r"^([0-9]*[A-Z]{2,12})(\d{2,8})$")
NUMERIC = re.compile(r"^[0-9]+[-_][0-9]+$")

PREFIXES = {
    prefix: Category(category)
    for category, prefixes in json.loads(
        (Path(__file__).parent / "data/catalog_rules.json").read_text()
    ).items()
    if category in ("censored", "uncensored")
    for prefix in prefixes
}


def catalog_prefix(code: str) -> str:
    return re.sub(r"(?:-\d+)+$|(?<=[A-Z])\d+$", "", code)


def classify(code: str) -> Category:
    if code.startswith("FC2PPV-"):
        return Category.FC2
    if NUMERIC.fullmatch(code):
        return Category.UNCENSORED
    prefix = catalog_prefix(code)
    return PREFIXES.get(prefix, Category.UNKNOWN)


def query_code(code: str) -> str:
    if NUMERIC.fullmatch(code):
        return code.replace("_", "-")
    if code.startswith("FC2PPV-"):
        return code.replace("FC2PPV-", "FC2-", 1)
    return code


def matches(requested: str, returned: str) -> bool:
    try:
        return query_code(normalize_code(requested)) == query_code(normalize_code(returned))
    except ValueError:
        return False


def dmm_cid(code: str) -> str | None:
    prefix, _, digits = code.rpartition("-")
    if not re.fullmatch(r"[A-Z0-9]*[A-Z][A-Z0-9]*", prefix) or not digits.isdigit():
        return None
    return prefix.lower() + digits.zfill(5)


def normalize_code(value: str) -> str:
    code = unicodedata.normalize("NFKC", value).strip().upper()
    if match := FC2.fullmatch(code):
        return f"FC2PPV-{match[1]}"
    if code.startswith("FC2"):
        raise ValueError("FC2 番号无效")
    code = re.sub(r"\s+", "", code)
    if NUMERIC.fullmatch(code):
        return code
    if re.fullmatch(r"HEYDOUGA-\d{4}-\d{3,5}", code):
        return code
    if re.fullmatch(r"N\d{4,6}|[A-Z0-9]+-[A-Z]\d{2,8}", code):
        return code
    if match := STANDARD.fullmatch(code) or CONCATENATED.fullmatch(code):
        return f"{match[1].rstrip('-_')}-{match[2]}"
    raise ValueError(f"不支持的番号格式：{value!r}")
