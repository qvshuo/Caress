from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator

from .models import Category, Classification, Evidence
from .storage import atomic_write

FC2 = re.compile(r"(?i)^FC2[-_ ]*(?:PPV[-_ ]*)?(\d{5,10})$")
STANDARD = re.compile(r"^([A-Z0-9]*[A-Z][A-Z0-9]*(?:-[A-Z]+)?)[-_](\d{2,8})$")
CONCATENATED = re.compile(r"^([0-9]*[A-Z]{2,12})(\d{2,8})$")
NUMERIC = re.compile(r"^[0-9]+[-_][0-9]+$")


def catalog_prefix(code: str) -> str:
    if NUMERIC.fullmatch(code):
        return re.split(r"[-_]", code)[0]
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
    fc2_prefixes: list[str] = Field(default_factory=list)
    _categories: dict[str, Category] = PrivateAttr(default_factory=dict)

    @model_validator(mode="after")
    def validate_prefixes(self) -> CatalogRules:
        self._categories = {}
        for category in (Category.CENSORED, Category.UNCENSORED, Category.FC2):
            for value in getattr(self, f"{category.value}_prefixes"):
                prefix = value.upper()
                if not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", prefix):
                    raise ValueError(f"invalid prefix: {value!r}")
                if prefix in self._categories and self._categories[prefix] != category:
                    raise ValueError(f"conflicting prefix: {prefix}")
                self._categories[prefix] = category
        return self

    @classmethod
    def load(cls, path: Path | None = None) -> CatalogRules:
        target = path or Path(__file__).parent / "data/catalog_rules.json"
        stamp = target.stat()
        return cls._load(target, (stamp.st_mtime_ns, stamp.st_ctime_ns, stamp.st_size))

    @classmethod
    @lru_cache(maxsize=4)
    def _load(cls, path: Path, stamp) -> CatalogRules:
        return cls.model_validate_json(path.read_bytes())

    def classify(self, code: str) -> Classification:
        if code.startswith("FC2PPV-"):
            return Classification(category=Category.FC2, evidence=[Evidence(basis="number_family", value="FC2")])
        if NUMERIC.fullmatch(code):
            return Classification(category=Category.UNCENSORED, evidence=[Evidence(basis="number_family", value="numeric")])
        prefix = catalog_prefix(self.query_code(code))
        while prefix not in self._categories and "-" in prefix:
            prefix = prefix.rsplit("-", 1)[0]
        category = self._categories.get(prefix, Category.UNKNOWN)
        basis = f"{category.value}_prefix"
        return Classification(
            category=category,
            evidence=[Evidence(basis=f"rules_v{self.version}:{basis}", value=prefix)],
        )

    def remember(self, code: str, category: Category, path: Path | None) -> None:
        if category == Category.UNKNOWN or self.classify(code).category != Category.UNKNOWN:
            return
        current = CatalogRules.load(path).model_copy(deep=True) if path else self.model_copy(deep=True)
        if current.classify(code).category == Category.UNKNOWN:
            prefixes = getattr(current, f"{category.value}_prefixes")
            prefixes.append(catalog_prefix(self.query_code(code)))
            prefixes.sort()
        current.validate_prefixes()
        if path:
            atomic_write(path, current.model_dump_json(indent=2) + "\n")
        self.version = current.version
        self.censored_prefixes = current.censored_prefixes
        self.uncensored_prefixes = current.uncensored_prefixes
        self.fc2_prefixes = current.fc2_prefixes
        self._categories = current._categories

    def query_code(self, code: str) -> str:
        if NUMERIC.fullmatch(code):
            return code.replace("_", "-")
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
