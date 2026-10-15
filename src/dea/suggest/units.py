"""Unit normalization: unit strings -> UCUM codes, conversion to a concept's canonical unit.

Hospital exports carry a unit per row (``valueuom`` in MIMIC, ``valueQuantity.unit`` in FHIR)
and the same variable is routinely recorded in several of them: °F next to °C, FiO2 as a
percentage next to a fraction, glucose in mmol/L next to mg/dL. :func:`normalize_units`
rewrites such rows into the canonical unit of the matched dictionary concept, lazily, and
reports what it changed so the import can warn about it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import polars as pl
import yaml

from dea.core.model import VALUE, VARIABLE

_UNITS_PATH = Path(__file__).with_name("units.yaml")
_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Conversion:
    src: str
    dst: str
    factor: float
    offset: float = 0.0
    concept: str | None = None

    def apply(self, value: float) -> float:
        return value * self.factor + self.offset


@dataclass
class UnitReport:
    """What the normalizer saw and did for one variable."""

    variable: str
    canonical: str
    label: str = ""  # display unit of the concept, when known
    seen: dict[str, int] = field(default_factory=dict)  # UCUM (or raw) unit -> rows
    converted: dict[str, int] = field(default_factory=dict)  # from unit -> rows converted
    unconvertible: dict[str, int] = field(default_factory=dict)  # unit -> rows left as they are

    @property
    def mixed(self) -> bool:
        return len(self.seen) > 1

    def message(self) -> str | None:
        parts = []
        if self.converted:
            parts.append(
                "converted " + ", ".join(f"{n} rows from {u}" for u, n in self.converted.items())
            )
        if self.unconvertible:
            parts.append(
                "no conversion for "
                + ", ".join(f"{n} rows in {u!r}" for u, n in self.unconvertible.items())
            )
        if not parts:
            return None
        return f"{self.variable} -> {self.label or self.canonical}: " + "; ".join(parts)

    def to_dict(self) -> dict:
        return {**self.__dict__, "mixed": self.mixed}


class UnitTable:
    def __init__(self, aliases: dict[str, list[str]], conversions: list[Conversion]) -> None:
        self._alias: dict[str, str] = {}
        for ucum, names in aliases.items():
            self._alias[self.key(ucum)] = ucum
            for n in names:
                self._alias.setdefault(self.key(str(n)), ucum)
        self._conv = {(c.src, c.dst, c.concept): c for c in conversions}
        self.conversions = conversions

    @staticmethod
    def key(text: str) -> str:
        return _WS.sub("", str(text).strip().lower())

    def to_ucum(self, unit: str | None) -> str | None:
        """UCUM code for a unit string; unknown strings are returned trimmed."""
        if unit is None:
            return None
        return self._alias.get(self.key(unit), unit.strip() or None)

    def ucum_expr(self, col: str) -> pl.Expr:
        """Vectorized :meth:`to_ucum` for a string column."""
        keyed = pl.col(col).cast(pl.String()).str.strip_chars().str.to_lowercase()
        keyed = keyed.str.replace_all(r"\s+", "")
        return keyed.replace(self._alias).alias(col)

    def conversion(self, src: str, dst: str, concept: str | None = None) -> Conversion | None:
        if src == dst:
            return Conversion(src, dst, 1.0)
        for key in ((src, dst, concept), (src, dst, None)):
            if key in self._conv:
                return self._conv[key]
        return None

    def conversions_into(self, dst: str, concept: str | None = None) -> list[Conversion]:
        """Every conversion that lands in ``dst`` (used to explain outlier clusters)."""
        return [c for c in self.conversions if c.dst == dst and c.concept in (None, concept)]


@lru_cache(maxsize=1)
def load_units(path: Path | None = None) -> UnitTable:
    raw = yaml.safe_load((path or _UNITS_PATH).read_text(encoding="utf-8")) or {}
    conv = [
        Conversion(
            c["from"], c["to"], float(c["factor"]), float(c.get("offset", 0.0)), c.get("concept")
        )
        for c in raw.get("conversions", [])
    ]
    return UnitTable(raw.get("aliases", {}), conv)


def normalize_units(
    lf: pl.LazyFrame,
    unit_col: str,
    targets: dict[str, tuple[str, str | None]],
    value_col: str = VALUE,
    variable_col: str = VARIABLE,
) -> tuple[pl.LazyFrame, list[UnitReport]]:
    """Convert values of the variables in ``targets`` to their canonical UCUM unit.

    ``targets`` maps a variable name to ``(canonical_ucum, concept_name)``. Rows in a unit
    without a known conversion keep their value (the report counts them). The returned frame
    has ``unit_col`` replaced by the UCUM code of the unit each row is now in. One streaming
    pass counts the (variable, unit) pairs; the conversion itself stays lazy.
    """
    table = load_units()
    lf = lf.with_columns(table.ucum_expr(unit_col))
    if not targets:
        return lf, []
    seen = (
        lf.filter(pl.col(variable_col).is_in(list(targets)))
        .group_by(variable_col, unit_col)
        .agg(pl.len().alias("n"))
        .collect(engine="streaming")
    )
    reports: dict[str, UnitReport] = {}
    for var, unit, n in seen.sort(variable_col, unit_col).iter_rows():
        canonical, concept = targets[var]
        rep = reports.setdefault(var, UnitReport(variable=var, canonical=canonical))
        rep.seen[unit or "(none)"] = int(n)
        if not unit or unit == canonical:
            continue
        conv = table.conversion(unit, canonical, concept)
        if conv is None:
            rep.unconvertible[unit] = int(n)
            continue
        rep.converted[unit] = int(n)
    value_expr, unit_expr = pl.col(value_col), pl.col(unit_col)
    for rep in reports.values():
        for unit in rep.converted:
            conv = table.conversion(unit, rep.canonical, targets[rep.variable][1])
            assert conv is not None
            hit = (pl.col(variable_col) == rep.variable) & (pl.col(unit_col) == unit)
            value_expr = (
                pl.when(hit)
                .then(pl.col(value_col) * conv.factor + conv.offset)
                .otherwise(value_expr)
            )
            unit_expr = pl.when(hit).then(pl.lit(rep.canonical)).otherwise(unit_expr)
    lf = lf.with_columns(value_expr.alias(value_col), unit_expr.alias(unit_col))
    return lf, [r for r in reports.values() if r.mixed or r.converted or r.unconvertible]


__all__ = ["Conversion", "UnitReport", "UnitTable", "load_units", "normalize_units"]
