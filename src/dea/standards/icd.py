"""ICD-9-CM and ICD-10(-CM) diagnosis codes: parsing, chapters and cross-version groups.

Hospitals changed from ICD-9 to ICD-10 in 2015 (US) and most European sites use national
ICD-10 variants, so a cohort routinely mixes both. Exact code-to-code crosswalks (the CMS
General Equivalence Mappings) are many-to-many and not bundled here; what research cohorts
need most is a stable *group* per encounter ("sepsis", "heart failure") that holds across
versions. :data:`DIAGNOSIS_GROUPS` defines such groups by code prefixes in both versions and
:func:`diagnosis_table` turns a long diagnosis list into one static row per encounter with a
boolean ``dx_<group>`` column each, ready for filters such as ``dx_sepsis = true``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import polars as pl

from dea.core.model import ENCOUNTER_ID

_CLEAN = re.compile(r"[\s.]")


@dataclass(frozen=True)
class IcdCode:
    code: str  # normalized: upper case, no dot (A419, 0389, J80)
    version: int  # 9 or 10
    chapter: str

    @property
    def dotted(self) -> str:
        c = self.code
        if self.version == 10:
            return c if len(c) <= 3 else f"{c[:3]}.{c[3:]}"
        if c[:1] in "EV":  # supplementary classification
            head = 4 if c[:1] == "E" else 3
            return c if len(c) <= head else f"{c[:head]}.{c[head:]}"
        return c if len(c) <= 3 else f"{c[:3]}.{c[3:]}"


# ICD-10 chapters by first three characters (inclusive ranges).
_ICD10_CHAPTERS: list[tuple[str, str, str]] = [
    ("A00", "B99", "Infectious diseases"),
    ("C00", "D49", "Neoplasms"),
    ("D50", "D89", "Blood and immune disorders"),
    ("E00", "E89", "Endocrine and metabolic"),
    ("F01", "F99", "Mental and behavioural"),
    ("G00", "G99", "Nervous system"),
    ("H00", "H59", "Eye"),
    ("H60", "H95", "Ear"),
    ("I00", "I99", "Circulatory system"),
    ("J00", "J99", "Respiratory system"),
    ("K00", "K95", "Digestive system"),
    ("L00", "L99", "Skin"),
    ("M00", "M99", "Musculoskeletal"),
    ("N00", "N99", "Genitourinary"),
    ("O00", "O9A", "Pregnancy and childbirth"),
    ("P00", "P96", "Perinatal"),
    ("Q00", "Q99", "Congenital"),
    ("R00", "R99", "Symptoms and abnormal findings"),
    ("S00", "T88", "Injury and poisoning"),
    ("U00", "U85", "Special purposes"),
    ("V00", "Y99", "External causes"),
    ("Z00", "Z99", "Factors influencing health status"),
]
_ICD9_CHAPTERS: list[tuple[int, int, str]] = [
    (1, 139, "Infectious diseases"),
    (140, 239, "Neoplasms"),
    (240, 279, "Endocrine and metabolic"),
    (280, 289, "Blood and immune disorders"),
    (290, 319, "Mental and behavioural"),
    (320, 389, "Nervous system and sense organs"),
    (390, 459, "Circulatory system"),
    (460, 519, "Respiratory system"),
    (520, 579, "Digestive system"),
    (580, 629, "Genitourinary"),
    (630, 679, "Pregnancy and childbirth"),
    (680, 709, "Skin"),
    (710, 739, "Musculoskeletal"),
    (740, 759, "Congenital"),
    (760, 779, "Perinatal"),
    (780, 799, "Symptoms and abnormal findings"),
    (800, 999, "Injury and poisoning"),
]


def _rng(lo: int, hi: int) -> tuple[str, ...]:
    return tuple(f"{i:03d}" for i in range(lo, hi + 1))


# group -> (ICD-10 prefixes, ICD-9 prefixes). Prefixes match normalized codes (no dots).
DIAGNOSIS_GROUPS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "sepsis": (("A40", "A41", "R652"), ("038", "99591", "99592", "78552")),
    "septic_shock": (("R6521",), ("78552",)),
    "ards": (("J80",), ("51882", "5185")),
    "pneumonia": (("J12", "J13", "J14", "J15", "J16", "J17", "J18"), _rng(480, 486)),
    "covid19": (("U071", "U072"), ()),
    "copd": (("J44",), ("491", "492", "496")),
    "asthma": (("J45",), ("493",)),
    "heart_failure": (("I50",), ("428",)),
    "myocardial_infarction": (("I21", "I22"), ("410",)),
    "atrial_fibrillation": (("I48",), ("42731",)),
    "stroke": (("I60", "I61", "I63"), ("430", "431", "433", "434", "436")),
    "hypertension": (("I10", "I11", "I12", "I13", "I15"), _rng(401, 405)),
    "diabetes": (("E10", "E11", "E13"), ("250",)),
    "chronic_kidney_disease": (("N18",), ("585",)),
    "acute_kidney_injury": (("N17",), ("584",)),
    "liver_disease": (("K70", "K72", "K74"), ("571",)),
    "cancer": (tuple(f"C{i:02d}" for i in range(0, 97)), _rng(140, 208)),
    "obesity": (("E66",), ("2780",)),
    "delirium": (("F05",), ("2930",)),
    "dementia": (("F00", "F01", "F02", "F03", "G30"), ("290", "3310")),
    "trauma": (tuple(f"S{i:02d}" for i in range(0, 100)) + ("T07", "T14"), _rng(800, 959)),
}


def parse_icd(code: str, version: int | str | None = None) -> IcdCode | None:
    """Normalize a code and infer its version when not given.

    ICD-10 codes start with a letter followed by two digits; ICD-9-CM codes are numeric or
    start with ``E``/``V`` (supplementary). ``E`` codes are ambiguous: ICD-10 ``E11.9`` versus
    ICD-9 ``E880.0`` are told apart by length (ICD-9 E codes have three digits after the E).
    """
    if code is None:
        return None
    norm = _CLEAN.sub("", str(code)).upper()
    if not norm:
        return None
    v = int(version) if version not in (None, "") else None
    if v is None:
        if norm[0].isdigit():
            v = 9
        elif norm[0] == "V" and norm[1:3].isdigit() and len(norm) <= 5:
            v = 9
        elif norm[0] == "E" and norm[1:4].isdigit() and len(norm) >= 4 and norm[3].isdigit():
            v = 9 if len(norm) >= 4 and norm[1:4].isdigit() and norm[1] in "89" else 10
        else:
            v = 10
    return IcdCode(code=norm, version=v, chapter=chapter(norm, v))


def chapter(code: str, version: int) -> str:
    if version == 10:
        head = code[:3]
        for lo, hi, name in _ICD10_CHAPTERS:
            if lo <= head <= hi:
                return name
        return "Unknown"
    if code[:1] == "E":
        return "External causes"
    if code[:1] == "V":
        return "Factors influencing health status"
    try:
        n = int(code[:3])
    except ValueError:
        return "Unknown"
    for lo, hi, name in _ICD9_CHAPTERS:
        if lo <= n <= hi:
            return name
    return "Unknown"


def groups_for(code: IcdCode) -> list[str]:
    idx = 0 if code.version == 10 else 1
    return [g for g, prefixes in DIAGNOSIS_GROUPS.items() if code.code.startswith(prefixes[idx])]


def diagnosis_table(
    diagnoses: pl.DataFrame,
    code_col: str = "icd_code",
    version_col: str | None = "icd_version",
    seq_col: str | None = "seq_num",
    title_col: str | None = None,
) -> pl.DataFrame:
    """One static row per encounter from a long diagnosis list.

    Columns: ``icd_codes`` (dotted codes joined by ``|``, ICD-10 and ICD-9 alike),
    ``n_diagnoses``, ``primary_diagnosis`` (lowest sequence number, title when available),
    ``primary_chapter`` and one boolean ``dx_<group>`` per :data:`DIAGNOSIS_GROUPS` entry.
    """
    rows = []
    for row in diagnoses.iter_rows(named=True):
        parsed = parse_icd(row[code_col], row.get(version_col) if version_col else None)
        if parsed is None:
            continue
        rows.append(
            {
                ENCOUNTER_ID: str(row[ENCOUNTER_ID]),
                "code": parsed.dotted,
                "chapter": parsed.chapter,
                "seq": row.get(seq_col, 1) if seq_col else 1,
                "title": row.get(title_col) if title_col else None,
                **{f"dx_{g}": g in groups_for(parsed) for g in DIAGNOSIS_GROUPS},
            }
        )
    flags = [f"dx_{g}" for g in DIAGNOSIS_GROUPS]
    if not rows:
        return pl.DataFrame(
            schema={
                ENCOUNTER_ID: pl.String(),
                "icd_codes": pl.String(),
                "n_diagnoses": pl.UInt32(),
                "primary_diagnosis": pl.String(),
                "primary_chapter": pl.String(),
                **{f: pl.Boolean() for f in flags},
            }
        )
    long = pl.DataFrame(rows).sort(ENCOUNTER_ID, "seq")
    return long.group_by(ENCOUNTER_ID, maintain_order=True).agg(
        pl.col("code").str.join("|").alias("icd_codes"),
        pl.len().alias("n_diagnoses"),
        pl.coalesce(pl.col("title").first(), pl.col("code").first()).alias("primary_diagnosis"),
        pl.col("chapter").first().alias("primary_chapter"),
        *[pl.col(f).any() for f in flags],
    )


__all__ = ["DIAGNOSIS_GROUPS", "IcdCode", "chapter", "diagnosis_table", "groups_for", "parse_icd"]
