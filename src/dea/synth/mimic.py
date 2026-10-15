"""Write a synthetic cohort in the MIMIC-IV table layout.

The real MIMIC-IV demo needs a PhysioNet download; this produces the same files, columns and
item codes (``icu/icustays``, ``icu/chartevents``, ``icu/d_items``, ``hosp/labevents``,
``hosp/d_labitems``, ``hosp/patients``, ``hosp/admissions``) from :mod:`dea.synth`, so the
MIMIC importer can be developed and tested without credentials. The data is synthetic.
"""

from __future__ import annotations

import gzip
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from dea.core.model import ENCOUNTER_ID, TIME, VALUE, VARIABLE
from dea.synth.generate import _generate_batch, _ids

# DEA synthetic variable -> (MIMIC itemid, label, unit, table)
CHART_ITEMS: dict[str, tuple[int, str, str]] = {
    "HR": (220045, "Heart Rate", "bpm"),
    "MAP": (220052, "Arterial Blood Pressure mean", "mmHg"),
    "SpO2": (220277, "O2 saturation pulseoxymetry", "%"),
    "RR": (220210, "Respiratory Rate", "insp/min"),
    "Temp": (223762, "Temperature Celsius", "°C"),
    "FiO2": (223835, "Inspired O2 Fraction", ""),
    "PEEP": (220339, "PEEP set", "cmH2O"),
    "TidalVolume_per_kg": (224685, "Tidal Volume (observed)", "mL"),
    "Compliance": (229661, "Compliance", "mL/cmH2O"),
    "paO2": (220224, "Arterial O2 pressure", "mmHg"),
    "paCO2": (220235, "Arterial CO2 Pressure", "mmHg"),
    "pH": (223830, "PH (Arterial)", "units"),
    "Norepinephrine": (221906, "Norepinephrine", "mcg/kg/min"),
}
LAB_ITEMS: dict[str, tuple[int, str, str, str]] = {
    "Lactate": (50813, "Lactate", "Blood", "mmol/L"),
    "Leukocytes": (51301, "White Blood Cells", "Blood", "K/uL"),
    "CRP": (50889, "C-Reactive Protein", "Blood", "mg/L"),
    "Creatinine": (50912, "Creatinine", "Blood", "mg/dL"),
    "Bilirubin": (50885, "Bilirubin, Total", "Blood", "mg/dL"),
    "PCT": (51652, "Procalcitonin", "Blood", "ng/mL"),
}
VENT_MODE_ITEM = (223849, "Ventilator Mode")
WEIGHT_ITEM = (226512, "Admission Weight (Kg)")
MODE_MAP = {"PSV": "CPAP/PSV", "PCV": "PCV/PSV", "VCV": "CMV/ASSIST", "none": "Standby"}


def _write(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        fh.write(df.write_csv().encode())


def write_mimic_demo(path: Path | str, n_stays: int = 50, seed: int = 0) -> Path:
    """Write MIMIC-IV-shaped tables for ``n_stays`` synthetic ICU stays under ``path``."""
    root = Path(path)
    rng = np.random.default_rng(seed)
    ids = _ids(rng, n_stays)
    static, dynamic, events = _generate_batch(rng, ids, min_hours=12, max_hours=14 * 24)
    stay_ids = {eid: 30_000_000 + i for i, eid in enumerate(ids)}
    base = datetime(2150, 1, 1)
    intimes = {
        eid: base + timedelta(days=int(rng.integers(0, 365 * 3)), hours=int(rng.integers(0, 24)))
        for eid in ids
    }

    los_hours = (
        dynamic.group_by(ENCOUNTER_ID)
        .agg(pl.col(TIME).max().dt.total_seconds() / 3600)
        .to_dict(as_series=False)
    )
    los = dict(zip(los_hours[ENCOUNTER_ID], los_hours[TIME], strict=True))
    rows = static.to_dicts()
    patients, admissions, stays = [], [], []
    for r in rows:
        eid = r[ENCOUNTER_ID]
        sid, hadm, stay = stay_ids[eid] - 20_000_000, stay_ids[eid] - 10_000_000, stay_ids[eid]
        intime = intimes[eid]
        outtime = intime + timedelta(hours=los[eid])
        died = r["outcome"] == "deceased"
        patients.append(
            {
                "subject_id": sid,
                "gender": r["sex"],
                "anchor_age": r["age"],
                "anchor_year": intime.year,
                "anchor_year_group": "2014 - 2016",
                "dod": outtime.date().isoformat() if died else None,
            }
        )
        admissions.append(
            {
                "subject_id": sid,
                "hadm_id": hadm,
                "admittime": intime - timedelta(hours=3),
                "dischtime": outtime + timedelta(hours=int(rng.integers(2, 72))),
                "deathtime": outtime if died else None,
                "admission_type": {
                    "medical": "EW EMER.",
                    "surgical": "SURGICAL SAME DAY ADMISSION",
                    "trauma": "URGENT",
                }[r["admission_type"]],
                "admission_location": "EMERGENCY ROOM",
                "discharge_location": "DIED" if died else "HOME",
                "insurance": str(rng.choice(["Medicare", "Medicaid", "Other"])),
                "language": "ENGLISH",
                "marital_status": str(rng.choice(["MARRIED", "SINGLE", "WIDOWED"])),
                "race": str(rng.choice(["WHITE", "BLACK/AFRICAN AMERICAN", "ASIAN", "OTHER"])),
                "hospital_expire_flag": int(died),
            }
        )
        stays.append(
            {
                "subject_id": sid,
                "hadm_id": hadm,
                "stay_id": stay,
                "first_careunit": str(
                    rng.choice(
                        [
                            "Medical Intensive Care Unit (MICU)",
                            "Surgical Intensive Care Unit (SICU)",
                            "Cardiac Vascular Intensive Care Unit (CVICU)",
                        ]
                    )
                ),
                "last_careunit": "Medical Intensive Care Unit (MICU)",
                "intime": intime,
                "outtime": outtime,
                "los": round(los[eid] / 24, 4),
            }
        )

    key = pl.DataFrame(
        {
            ENCOUNTER_ID: list(stay_ids),
            "stay_id": list(stay_ids.values()),
            "intime": [intimes[e] for e in stay_ids],
        }
    )
    joined = dynamic.join(key, on=ENCOUNTER_ID).with_columns(
        (pl.col("intime") + pl.col(TIME)).alias("charttime"),
        (pl.col("stay_id") - 20_000_000).alias("subject_id"),
        (pl.col("stay_id") - 10_000_000).alias("hadm_id"),
    )
    chart_map = pl.DataFrame(
        {
            VARIABLE: list(CHART_ITEMS),
            "itemid": [v[0] for v in CHART_ITEMS.values()],
            "valueuom": [v[2] for v in CHART_ITEMS.values()],
        }
    )
    chart = joined.join(chart_map, on=VARIABLE).select(
        "subject_id",
        "hadm_id",
        "stay_id",
        pl.lit(None, dtype=pl.Int64).alias("caregiver_id"),
        "charttime",
        (pl.col("charttime") + pl.duration(minutes=5)).alias("storetime"),
        "itemid",
        pl.col(VALUE).round(2).cast(pl.String).alias("value"),
        pl.col(VALUE).round(2).alias("valuenum"),
        "valueuom",
        pl.lit(0, dtype=pl.Int64).alias("warning"),
    )
    # admission weight: one row per stay at intime (a static attribute charted as a time series)
    weight = key.join(static.select(ENCOUNTER_ID, "weight_kg"), on=ENCOUNTER_ID).select(
        (pl.col("stay_id") - 20_000_000).alias("subject_id"),
        (pl.col("stay_id") - 10_000_000).alias("hadm_id"),
        "stay_id",
        pl.lit(None, dtype=pl.Int64).alias("caregiver_id"),
        pl.col("intime").alias("charttime"),
        pl.col("intime").alias("storetime"),
        pl.lit(WEIGHT_ITEM[0], dtype=pl.Int64).alias("itemid"),
        pl.col("weight_kg").cast(pl.String).alias("value"),
        pl.col("weight_kg").alias("valuenum"),
        pl.lit("kg").alias("valueuom"),
        pl.lit(0, dtype=pl.Int64).alias("warning"),
    )
    modes = events.join(key, on=ENCOUNTER_ID).select(
        (pl.col("stay_id") - 20_000_000).alias("subject_id"),
        (pl.col("stay_id") - 10_000_000).alias("hadm_id"),
        "stay_id",
        pl.lit(None, dtype=pl.Int64).alias("caregiver_id"),
        (pl.col("intime") + pl.col(TIME)).alias("charttime"),
        (pl.col("intime") + pl.col(TIME)).alias("storetime"),
        pl.lit(VENT_MODE_ITEM[0], dtype=pl.Int64).alias("itemid"),
        pl.col(VALUE).replace_strict(MODE_MAP).alias("value"),
        pl.lit(None, dtype=pl.Float64).alias("valuenum"),
        pl.lit("").alias("valueuom"),
        pl.lit(0, dtype=pl.Int64).alias("warning"),
    )
    chartevents = pl.concat([chart, weight, modes]).sort("stay_id", "charttime", "itemid")

    lab_map = pl.DataFrame(
        {
            VARIABLE: list(LAB_ITEMS),
            "itemid": [v[0] for v in LAB_ITEMS.values()],
            "valueuom": [v[3] for v in LAB_ITEMS.values()],
        }
    )
    labs = (
        joined.join(lab_map, on=VARIABLE)
        .sort("hadm_id", "charttime", "itemid")
        .with_row_index("labevent_id", 1)
    )
    labevents = labs.select(
        "labevent_id",
        "subject_id",
        "hadm_id",
        (pl.col("labevent_id") + 100_000).alias("specimen_id"),
        "itemid",
        pl.lit(None, dtype=pl.String).alias("order_provider_id"),
        "charttime",
        (pl.col("charttime") + pl.duration(minutes=40)).alias("storetime"),
        pl.col(VALUE).round(2).cast(pl.String).alias("value"),
        pl.col(VALUE).round(2).alias("valuenum"),
        "valueuom",
        pl.lit(None, dtype=pl.Float64).alias("ref_range_lower"),
        pl.lit(None, dtype=pl.Float64).alias("ref_range_upper"),
        pl.lit(None, dtype=pl.String).alias("flag"),
        pl.lit("ROUTINE").alias("priority"),
        pl.lit(None, dtype=pl.String).alias("comments"),
    )

    d_items = pl.DataFrame(
        {
            "itemid": [v[0] for v in CHART_ITEMS.values()] + [VENT_MODE_ITEM[0], WEIGHT_ITEM[0]],
            "label": [v[1] for v in CHART_ITEMS.values()] + [VENT_MODE_ITEM[1], WEIGHT_ITEM[1]],
            "abbreviation": [v[1][:20] for v in CHART_ITEMS.values()] + ["Vent Mode", "Admit Wt"],
            "linksto": ["chartevents"] * (len(CHART_ITEMS) + 2),
            "category": ["Routine Vital Signs"] * 5
            + ["Respiratory"] * 4
            + ["Labs"] * 3
            + ["Medications", "Respiratory", "General"],
            "unitname": [v[2] for v in CHART_ITEMS.values()] + ["", "kg"],
            "param_type": ["Numeric"] * len(CHART_ITEMS) + ["Text", "Numeric"],
            "lownormalvalue": [None] * (len(CHART_ITEMS) + 2),
            "highnormalvalue": [None] * (len(CHART_ITEMS) + 2),
        }
    )
    d_labitems = pl.DataFrame(
        {
            "itemid": [v[0] for v in LAB_ITEMS.values()],
            "label": [v[1] for v in LAB_ITEMS.values()],
            "fluid": [v[2] for v in LAB_ITEMS.values()],
            "category": ["Chemistry"] * len(LAB_ITEMS),
        }
    )

    _write(pl.DataFrame(patients), root / "hosp" / "patients.csv.gz")
    _write(pl.DataFrame(admissions), root / "hosp" / "admissions.csv.gz")
    _write(pl.DataFrame(stays), root / "icu" / "icustays.csv.gz")
    _write(d_items, root / "icu" / "d_items.csv.gz")
    _write(chartevents, root / "icu" / "chartevents.csv.gz")
    _write(d_labitems, root / "hosp" / "d_labitems.csv.gz")
    _write(labevents, root / "hosp" / "labevents.csv.gz")
    return root
