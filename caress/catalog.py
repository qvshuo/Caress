from __future__ import annotations

import json
import re
import unicodedata
from importlib.resources import files
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .models import Category, Classification, Evidence

FC2 = re.compile(r"(?i)^FC2[-_ ]*(?:PPV[-_ ]*)?(\d{5,10})$")
STANDARD = re.compile(r"^([A-Z0-9]*[A-Z][A-Z0-9]*(?:-[A-Z]+)?)[-_](\d{2,8})$")
CONCATENATED = re.compile(r"^([0-9]*[A-Z]{2,12})(\d{2,8})$")
NUMERIC = re.compile(r"^\d{6}[-_]\d{2,5}$")


def catalog_prefix(code: str) -> str:
    if re.fullmatch(r"N\d{4,6}", code):
        return "N"
    if match := re.fullmatch(r"([A-Z0-9]+-[A-Z])\d{2,8}", code):
        return match[1]
    return code.rsplit("-", 1)[0]


class CatalogRules(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = 1
    censored_prefixes: list[str] = Field(default_factory=list)
    uncensored_prefixes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_prefixes(self) -> CatalogRules:
        censored = {p.upper() for p in self.censored_prefixes}
        uncensored = {p.upper() for p in self.uncensored_prefixes}
        if censored & uncensored:
            raise ValueError(f"conflicting prefixes: {sorted(censored & uncensored)}")
        for prefix in [*self.censored_prefixes, *self.uncensored_prefixes]:
            if not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z]+)?", prefix):
                raise ValueError(f"invalid prefix: {prefix!r}")
        return self

    @classmethod
    def load(cls, path: Path | None = None) -> CatalogRules:
        text = (
            path.read_text(encoding="utf-8")
            if path
            else files("caress").joinpath("data/catalog_rules.json").read_text(encoding="utf-8")
        )
        return cls.model_validate(json.loads(text))

    def classify(self, code: str) -> Classification:
        if code.startswith("FC2PPV-"):
            return Classification(
                category=Category.FC2,
                evidence=[Evidence(basis="number_family", value="FC2")],
            )
        prefix = catalog_prefix(code)
        for known in sorted([*self.uncensored_prefixes, *self.censored_prefixes], key=len, reverse=True):
            if code.startswith(known + "-"):
                prefix = known
                break
        category = Category.UNKNOWN
        basis = "unknown_prefix"
        if prefix in self.uncensored_prefixes:
            category, basis = Category.UNCENSORED, "uncensored_prefix"
        elif prefix in self.censored_prefixes:
            category, basis = Category.CENSORED, "censored_prefix"
        elif NUMERIC.fullmatch(code):
            category, basis = Category.UNCENSORED, "numeric_catalog_family"
        return Classification(
            category=category,
            evidence=[Evidence(basis=f"rules_v{self.version}:{basis}", value=prefix)],
        )

    def query_code(self, code: str) -> str:
        if code.startswith("FC2PPV-"):
            return code.replace("FC2PPV-", "FC2-", 1)
        return code

    def matches(self, requested: str, returned: str) -> bool:
        try:
            return self.query_code(normalize_code(requested)) == self.query_code(normalize_code(returned))
        except ValueError:
            return False

    def dmm_cid(self, code: str) -> str | None:
        prefix, _, digits = code.rpartition("-")
        if not re.fullmatch(r"[A-Z0-9]*[A-Z][A-Z0-9]*", prefix) or not digits.isdigit():
            return None
        return f"{prefix.lower()}{digits.zfill(5)}"


def normalize_code(value: str) -> str:
    code = unicodedata.normalize("NFKC", value).strip().upper()
    if match := FC2.fullmatch(code):
        return f"FC2PPV-{match[1]}"
    if code.startswith("FC2"):
        raise ValueError("invalid FC2 article number")
    code = re.sub(r"\s+", "", code)
    if NUMERIC.fullmatch(code):
        return code
    if re.fullmatch(r"HEYDOUGA-\d{4}-\d{3,5}", code):
        return code
    if re.fullmatch(r"N\d{4,6}|[A-Z0-9]+-[A-Z]\d{2,8}", code):
        return code
    if match := STANDARD.fullmatch(code) or CONCATENATED.fullmatch(code):
        return f"{match[1].rstrip('-_')}-{match[2]}"
    raise ValueError(f"unsupported catalog number: {value!r}; supply a number, not a filename")
