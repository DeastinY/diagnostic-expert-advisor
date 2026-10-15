"""FHIR R4: read bulk exports / bundles into a cohort and write a cohort back out.

Reading understands ``Patient``, ``Encounter``, ``Observation`` and ``Condition`` resources
from newline-delimited JSON files (the bulk data ``$export`` format) or from Bundle JSON
files. Observations coded in LOINC are named by the dictionary concept when DEA knows the
code; ``valueQuantity`` units are normalized to the concept's UCUM unit; string or coded
values become events; blood pressure style panels are flattened through ``component``.
Conditions coded in ICD-10(-CM) or ICD-9-CM become the diagnosis columns of the static
table. Writing produces one NDJSON file per resource type with LOINC and UCUM codes where
the dictionary provides them, which is what most interoperability tooling expects.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from dea.core.config import CohortConfig
from dea.core.model import (
    ENCOUNTER_ID,
    EVENT_SCHEMA,
    LONG_SCHEMA,
    TIME,
    VALUE,
    VARIABLE,
    Cohort,
    collect,
)
from dea.io.importers import ADMISSION_TIME, ImportReport
from dea.standards.icd import diagnosis_table
from dea.suggest.units import load_units, normalize_units
from dea.suggest.vocab import load_dictionary

LOINC = "http://loinc.org"
UCUM = "http://unitsofmeasure.org"
ICD_SYSTEMS = {
    "http://hl7.org/fhir/sid/icd-10": 10,
    "http://hl7.org/fhir/sid/icd-10-cm": 10,
    "http://fhir.de/CodeSystem/bfarm/icd-10-gm": 10,
    "http://hl7.org/fhir/sid/icd-9-cm": 9,
    "http://hl7.org/fhir/sid/icd-9": 9,
}
RESOURCES = ("Patient", "Encounter", "Observation", "Condition")


# ------------------------------------------------------------------------------- reading


def _iter_resources(path: Path) -> Iterator[dict[str, Any]]:
    files = sorted(path.glob("*.ndjson")) + sorted(path.glob("*.json")) if path.is_dir() else [path]
    for file in files:
        with file.open(encoding="utf-8") as fh:
            if file.suffix == ".ndjson":
                for line in fh:
                    if line.strip():
                        yield json.loads(line)
                continue
            data = json.load(fh)
        if data.get("resourceType") == "Bundle":
            for entry in data.get("entry", []):
                if "resource" in entry:
                    yield entry["resource"]
        elif "resourceType" in data:
            yield data


def is_fhir_source(path: Path | str) -> bool:
    p = Path(path)
    try:
        first = next(_iter_resources(p), None)
    except (OSError, ValueError):
        return False
    return bool(first) and first.get("resourceType") in RESOURCES + ("Bundle",)


def _ref_id(ref: dict[str, Any] | None) -> str | None:
    if not ref or "reference" not in ref:
        return None
    return str(ref["reference"]).split("/")[-1]


def _when(obs: dict[str, Any]) -> str | None:
    return (
        obs.get("effectiveDateTime")
        or (obs.get("effectivePeriod") or {}).get("start")
        or obs.get("issued")
    )


def _coding(resource: dict[str, Any], system: str | None = None) -> dict[str, Any] | None:
    codings = (resource.get("code") or {}).get("coding") or []
    for c in codings:
        if system is None or c.get("system") == system:
            return c
    return codings[0] if codings else None


def _variable_name(code: dict[str, Any] | None, resource: dict[str, Any]) -> tuple[str, str]:
    """(variable name, LOINC code) for an observation or component."""
    d = load_dictionary()
    loinc = code.get("code", "") if code and code.get("system") == LOINC else ""
    if loinc and loinc in d.by_loinc:
        return d.by_loinc[loinc].name, loinc
    text = (resource.get("code") or {}).get("text") or (code or {}).get("display")
    return text or loinc or "unknown", loinc


def _observation_rows(obs: dict[str, Any]) -> Iterator[dict[str, Any]]:
    base = {
        "encounter": _ref_id(obs.get("encounter") or obs.get("context")),
        "patient": _ref_id(obs.get("subject")),
        "when": _when(obs),
    }
    parts = obs.get("component") or [obs]
    for part in parts:
        name, loinc = _variable_name(_coding(part, LOINC) or _coding(part), part)
        q = part.get("valueQuantity")
        row = {**base, VARIABLE: name, "loinc": loinc, "num": None, "text": None, "unit": None}
        if q is not None and q.get("value") is not None:
            row.update(num=float(q["value"]), unit=q.get("code") or q.get("unit"))
        elif "valueString" in part:
            row["text"] = str(part["valueString"])
        elif "valueCodeableConcept" in part:
            cc = part["valueCodeableConcept"]
            row["text"] = cc.get("text") or ((cc.get("coding") or [{}])[0].get("display"))
        elif "valueBoolean" in part:
            row["num"] = float(part["valueBoolean"])
        elif "valueInteger" in part:
            row["num"] = float(part["valueInteger"])
        else:
            continue
        yield row


def import_fhir(path: Path | str, name: str | None = None) -> tuple[Cohort, ImportReport]:
    """Import FHIR R4 resources (NDJSON directory, Bundle or single-resource JSON)."""
    root = Path(path)
    report = ImportReport(source=str(root), format="fhir")
    patients: dict[str, dict[str, Any]] = {}
    encounters: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    conditions: list[dict[str, Any]] = []
    skipped: dict[str, int] = {}
    for res in _iter_resources(root):
        kind = res.get("resourceType")
        if kind == "Patient":
            patients[str(res.get("id"))] = {
                "sex": res.get("gender"),
                "birth_date": res.get("birthDate"),
            }
        elif kind == "Encounter":
            period = res.get("period") or {}
            encounters.append(
                {
                    ENCOUNTER_ID: str(res.get("id")),
                    "patient": _ref_id(res.get("subject")),
                    "start": period.get("start"),
                    "end": period.get("end"),
                    "encounter_class": (res.get("class") or {}).get("code"),
                    "status": res.get("status"),
                }
            )
        elif kind == "Observation":
            observations.extend(_observation_rows(res))
        elif kind == "Condition":
            for c in (res.get("code") or {}).get("coding") or []:
                if c.get("system") in ICD_SYSTEMS:
                    conditions.append(
                        {
                            "encounter": _ref_id(res.get("encounter") or res.get("context")),
                            "patient": _ref_id(res.get("subject")),
                            "icd_code": c.get("code"),
                            "icd_version": ICD_SYSTEMS[c["system"]],
                            "title": c.get("display"),
                            "when": res.get("recordedDate") or res.get("onsetDateTime"),
                        }
                    )
                    break
        elif kind:
            skipped[kind] = skipped.get(kind, 0) + 1
    if not encounters:
        raise ValueError("no Encounter resources found; DEA needs one encounter per stay")
    for kind, n in sorted(skipped.items()):
        report.warnings.append(f"{n} {kind} resource(s) ignored")

    enc = pl.DataFrame(encounters).with_columns(
        pl.col("start").str.to_datetime(strict=False, time_zone="UTC").dt.replace_time_zone(None),
        pl.col("end").str.to_datetime(strict=False, time_zone="UTC").dt.replace_time_zone(None),
    )
    if patients:
        pat = pl.DataFrame(
            [{"patient": k, **v} for k, v in patients.items()],
            schema={"patient": pl.String(), "sex": pl.String(), "birth_date": pl.String()},
        ).with_columns(
            # birthDate may be a year, a year-month or a full date
            pl.when(pl.col("birth_date").str.len_chars() == 4)
            .then(pl.col("birth_date") + "-07-01")
            .when(pl.col("birth_date").str.len_chars() == 7)
            .then(pl.col("birth_date") + "-15")
            .otherwise(pl.col("birth_date"))
            .str.to_date(strict=False)
            .alias("birth_date")
        )
        enc = enc.join(pat, on="patient", how="left").with_columns(
            (
                (pl.col("start") - pl.col("birth_date").cast(pl.Datetime("us"))).dt.total_days()
                / 365.25
            )
            .floor()
            .alias("age")
        )
        enc = enc.drop("birth_date")
    enc = enc.rename({"start": ADMISSION_TIME, "patient": "patient_id"})
    missing_start = enc.filter(pl.col(ADMISSION_TIME).is_null()).height
    if missing_start:
        report.warnings.append(f"{missing_start} encounter(s) without period.start")
    windows = enc.select(ENCOUNTER_ID, "patient_id", ADMISSION_TIME, "end")

    obs = (
        pl.DataFrame(
            observations,
            schema={
                "encounter": pl.String(),
                "patient": pl.String(),
                "when": pl.String(),
                VARIABLE: pl.String(),
                "loinc": pl.String(),
                "num": pl.Float64(),
                "text": pl.String(),
                "unit": pl.String(),
            },
        )
        .with_columns(
            pl.col("when").str.to_datetime(strict=False, time_zone="UTC").dt.replace_time_zone(None)
        )
        .filter(pl.col("when").is_not_null())
    )
    obs = _attach(obs, windows, report)
    obs = obs.with_columns(
        (pl.col("when") - pl.col(ADMISSION_TIME)).cast(LONG_SCHEMA[TIME]).alias(TIME)
    )
    d = load_dictionary()
    names = obs.get_column(VARIABLE).unique().to_list()
    targets = {}
    for v in names:
        m = d.match(v)
        if m and m[0].ucum:
            targets[v] = (m[0].ucum, m[0].name)
    numeric, unit_reports = normalize_units(
        obs.lazy().filter(pl.col("num").is_not_null()), "unit", targets, value_col="num"
    )
    report.unit_reports = unit_reports
    dynamic = numeric.select(
        ENCOUNTER_ID, TIME, VARIABLE, pl.col("num").cast(pl.Float64()).alias(VALUE)
    ).select(list(LONG_SCHEMA))
    events = (
        obs.lazy()
        .filter(pl.col("num").is_null() & pl.col("text").is_not_null())
        .select(ENCOUNTER_ID, TIME, VARIABLE, pl.col("text").alias(VALUE))
        .select(list(EVENT_SCHEMA))
    )
    units = (
        collect(numeric.drop_nulls("unit").group_by(VARIABLE, "unit").agg(pl.len().alias("n")))
        .sort("n", descending=True)
        .unique(subset=[VARIABLE], keep="first")
    )
    variable_units = {}
    for v, u in units.select(VARIABLE, "unit").iter_rows():
        c = d.by_name.get(targets.get(v, ("", ""))[1])
        variable_units[v] = c.unit if c and v in targets else u
    codes = {
        v: code
        for v, code in obs.filter(pl.col("loinc") != "")
        .select(VARIABLE, "loinc")
        .unique()
        .iter_rows()
    }
    for v, (_, concept) in targets.items():
        codes.setdefault(v, d.by_name[concept].loinc)

    static = enc.drop("end")
    if conditions:
        cond = _attach(
            pl.DataFrame(
                conditions,
                schema={
                    "encounter": pl.String(),
                    "patient": pl.String(),
                    "icd_code": pl.String(),
                    "icd_version": pl.Int64(),
                    "title": pl.String(),
                    "when": pl.String(),
                },
            ).with_columns(
                pl.col("when")
                .str.to_datetime(strict=False, time_zone="UTC")
                .dt.replace_time_zone(None)
            ),
            windows,
            report,
            quiet=True,
        )
        dx = diagnosis_table(cond, seq_col=None, title_col="title")
        static = static.join(dx, on=ENCOUNTER_ID, how="left")
    config = CohortConfig(
        name=name or root.stem,
        description="Imported from FHIR R4 resources; one encounter per Encounter resource.",
        source=str(root),
        variable_units=variable_units,
        variable_codes={k: v for k, v in codes.items() if v},
    )
    cohort = Cohort(static.sort(ENCOUNTER_ID), dynamic, events=events, config=config)
    report.n_encounters = len(cohort)
    counts = cohort.variable_counts()
    report.n_variables = counts.height
    report.n_observations = int(counts["n_observations"].sum()) if counts.height else 0
    report.n_events = int(collect(events.select(pl.len())).item())
    report.mapping = {
        "encounter": "Encounter.id",
        "time": "Observation.effectiveDateTime - Encounter.period.start",
        "variables": "LOINC code via dictionary, else code.text",
    }
    return cohort, report


def _attach(
    frame: pl.DataFrame, windows: pl.DataFrame, report: ImportReport, quiet: bool = False
) -> pl.DataFrame:
    """Give every row an encounter id: by reference, else by patient and time window."""
    direct = frame.filter(pl.col("encounter").is_not_null()).rename({"encounter": ENCOUNTER_ID})
    direct = direct.join(windows.drop("patient_id", "end"), on=ENCOUNTER_ID, how="inner")
    loose = frame.filter(pl.col("encounter").is_null()).drop("encounter")
    if loose.height:
        loose = (
            loose.join(windows, left_on="patient", right_on="patient_id", how="inner")
            .filter(
                (pl.col("when") >= pl.col(ADMISSION_TIME))
                & (pl.col("end").is_null() | (pl.col("when") <= pl.col("end")))
            )
            .drop("end")
        )
    out = pl.concat([direct.drop("patient"), loose.drop("patient")], how="diagonal_relaxed")
    lost = frame.height - out.height
    if lost and not quiet:
        report.warnings.append(
            f"{lost} observation(s) could not be attached to an encounter and were dropped"
        )
    return out


# ------------------------------------------------------------------------------- writing


def _iso(ts: datetime) -> str:
    return ts.replace(microsecond=0).isoformat() + "Z"


def _ndjson(path: Path, rows: Iterable[dict[str, Any]]) -> int:
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            n += 1
    return n


def export_fhir(cohort: Cohort, directory: Path | str, batch: int = 500) -> dict[str, int]:
    """Write ``Patient``, ``Encounter``, ``Observation`` (and ``Condition``) NDJSON files.

    Encounters without an ``admission_time`` are placed at 1970-01-01 so offsets stay exact.
    Returns the number of resources written per type.
    """
    out = Path(directory)
    out.mkdir(parents=True, exist_ok=True)
    static = cohort.static
    cfg = cohort.config
    d = load_dictionary()
    epoch = datetime(1970, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    starts: dict[str, datetime] = {}
    for row in static.iter_rows(named=True):
        ts = row.get(ADMISSION_TIME)
        starts[row[ENCOUNTER_ID]] = ts if isinstance(ts, datetime) else epoch
    los = {
        r[ENCOUNTER_ID]: r["los_hours"]
        for r in cohort.features().select(ENCOUNTER_ID, "los_hours").iter_rows(named=True)
    }

    def patients() -> Iterator[dict[str, Any]]:
        for row in static.iter_rows(named=True):
            eid = row[ENCOUNTER_ID]
            pid = str(row.get("patient_id") or eid)
            res: dict[str, Any] = {"resourceType": "Patient", "id": pid}
            if row.get("sex") in ("male", "female", "M", "F"):
                res["gender"] = {"M": "male", "F": "female"}.get(row["sex"], row["sex"])
            if isinstance(row.get("age"), int | float):
                born = starts[eid] - timedelta(days=365.25 * row["age"])
                res["birthDate"] = born.date().isoformat()  # exact age is not known; mid-year
            yield res

    def encounters() -> Iterator[dict[str, Any]]:
        for row in static.iter_rows(named=True):
            eid = row[ENCOUNTER_ID]
            pid = str(row.get("patient_id") or eid)
            start = starts[eid]
            yield {
                "resourceType": "Encounter",
                "id": eid,
                "status": "finished",
                "class": {
                    "system": "http://terminology.hl7.org/CodeSystem/v3-ActCode",
                    "code": "IMP",
                },
                "subject": {"reference": f"Patient/{pid}"},
                "period": {
                    "start": _iso(start),
                    "end": _iso(start + timedelta(hours=los.get(eid, 0))),
                },
            }

    units = load_units()
    codings: dict[str, dict[str, Any]] = {}
    quantities: dict[str, dict[str, Any]] = {}
    for var in cohort.variables:
        match = d.match(var)
        concept = d.by_name.get(var) or (match[0] if match else None)
        loinc = cfg.variable_codes.get(var) or (concept.loinc if concept else "")
        coding: dict[str, Any] = {"display": concept.label if concept else cfg.label(var)}
        if loinc:
            coding.update(system=LOINC, code=loinc)
        codings[var] = coding
        unit = cfg.variable_units.get(var) or (concept.unit if concept else "")
        qty: dict[str, Any] = {"unit": unit} if unit else {}
        ucum = units.to_ucum(unit) if unit else (concept.ucum if concept else None)
        if ucum:
            qty.update(system=UCUM, code=ucum)
        quantities[var] = qty

    def observations() -> Iterator[dict[str, Any]]:
        seq = 0
        ids = cohort.encounter_ids
        for i in range(0, len(ids), batch):
            chunk = ids[i : i + batch]
            frame = collect(cohort.dynamic.filter(pl.col(ENCOUNTER_ID).is_in(chunk)))
            pid_by = {
                r[ENCOUNTER_ID]: str(r.get("patient_id") or r[ENCOUNTER_ID])
                for r in static.filter(pl.col(ENCOUNTER_ID).is_in(chunk)).iter_rows(named=True)
            }
            for eid, t, var, val in frame.select(ENCOUNTER_ID, TIME, VARIABLE, VALUE).iter_rows():
                seq += 1
                coding, qty = codings[var], {"value": val, **quantities[var]}
                yield {
                    "resourceType": "Observation",
                    "id": f"{eid}-{seq}",
                    "status": "final",
                    "code": {"coding": [coding], "text": cfg.label(var)},
                    "subject": {"reference": f"Patient/{pid_by[eid]}"},
                    "encounter": {"reference": f"Encounter/{eid}"},
                    "effectiveDateTime": _iso(starts[eid] + t),
                    "valueQuantity": qty,
                }

    def conditions() -> Iterator[dict[str, Any]]:
        if "icd_codes" not in static.columns:
            return
        from dea.standards.icd import parse_icd

        for row in static.iter_rows(named=True):
            codes = row.get("icd_codes")
            if not codes:
                continue
            for k, code in enumerate(str(codes).split("|")):
                parsed = parse_icd(code)
                if parsed is None:
                    continue
                system = (
                    "http://hl7.org/fhir/sid/icd-10-cm"
                    if parsed.version == 10
                    else "http://hl7.org/fhir/sid/icd-9-cm"
                )
                yield {
                    "resourceType": "Condition",
                    "id": f"{row[ENCOUNTER_ID]}-dx{k + 1}",
                    "subject": {
                        "reference": f"Patient/{row.get('patient_id') or row[ENCOUNTER_ID]}"
                    },
                    "encounter": {"reference": f"Encounter/{row[ENCOUNTER_ID]}"},
                    "code": {"coding": [{"system": system, "code": parsed.dotted}]},
                }

    written = {
        "Patient": _ndjson(out / "Patient.ndjson", patients()),
        "Encounter": _ndjson(out / "Encounter.ndjson", encounters()),
        "Observation": _ndjson(out / "Observation.ndjson", observations()),
    }
    if "icd_codes" in static.columns:
        written["Condition"] = _ndjson(out / "Condition.ndjson", conditions())
    return written


__all__ = ["export_fhir", "import_fhir", "is_fhir_source"]
