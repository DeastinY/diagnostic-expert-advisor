"""A small clinical dictionary (with LOINC and UCUM codes) and fuzzy matching against it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path

import yaml

_DICT_PATH = Path(__file__).with_name("dictionary.yaml")
_NORMALIZE = re.compile(r"[\s_\-./()\[\],:]+")
_PAREN = re.compile(r"\(.*?\)")
_SUFFIX = re.compile(r"\b(infusion|rate|drip|dose|dosage|administration|continuous)\b", re.I)


@dataclass(frozen=True)
class Concept:
    name: str
    label: str
    unit: str
    range: tuple[float, float] | None
    role: str
    fill: str
    synonyms: tuple[str, ...] = field(default_factory=tuple)
    loinc: str = ""
    ucum: str = ""
    rxnorm: str = ""
    atc: str = ""


def normalize(text: str) -> str:
    text = text.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    return _NORMALIZE.sub(" ", text).strip()


class Dictionary:
    def __init__(self, concepts: list[Concept]) -> None:
        self.concepts = concepts
        self.by_name = {c.name: c for c in concepts}
        self.by_loinc = {c.loinc: c for c in concepts if c.loinc}
        self._index: dict[str, Concept] = {}
        for c in concepts:
            for s in (c.name, c.label, c.loinc, c.rxnorm, c.atc, *c.synonyms):
                if s:
                    self._index.setdefault(normalize(s), c)

    def match(self, variable: str, threshold: float = 0.86) -> tuple[Concept, float] | None:
        """Best concept for a source variable name, with a similarity score in [0, 1]."""
        key = normalize(variable)
        if key in self._index:
            return self._index[key], 1.0
        bare = normalize(_PAREN.sub("", variable))
        if bare in self._index:
            return self._index[bare], 0.97
        stripped = _SUFFIX.sub("", bare).strip()  # "norepinephrine infusion rate" -> drug
        if stripped and stripped != bare and stripped in self._index:
            return self._index[stripped], 0.95
        best: tuple[Concept, float] | None = None
        for syn, concept in self._index.items():
            score = SequenceMatcher(None, key, syn).ratio()
            if score >= threshold and (best is None or score > best[1]):
                best = (concept, score)
        return best


@lru_cache(maxsize=1)
def load_dictionary(path: Path | None = None) -> Dictionary:
    raw = yaml.safe_load((path or _DICT_PATH).read_text(encoding="utf-8")) or []
    concepts = [
        Concept(
            name=e["name"],
            label=e["label"],
            unit=e.get("unit", "") or "",
            range=tuple(e["range"]) if e.get("range") else None,
            role=e.get("role", "other"),
            fill=e.get("fill", "forward"),
            synonyms=tuple(str(s) for s in e.get("synonyms", [])),
            loinc=str(e.get("loinc", "") or ""),
            ucum=str(e.get("ucum", "") or ""),
            rxnorm=str(e.get("rxnorm", "") or ""),
            atc=str(e.get("atc", "") or ""),
        )
        for e in raw
    ]
    return Dictionary(concepts)


def match_variable(variable: str) -> tuple[Concept, float] | None:
    return load_dictionary().match(variable)
