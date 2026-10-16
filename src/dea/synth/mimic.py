"""Write a synthetic cohort in the MIMIC-IV table layout.

The real MIMIC-IV demo needs a PhysioNet download; this produces the same files, columns and
item codes (``icu/icustays``, ``icu/chartevents``, ``icu/d_items``, ``hosp/labevents``,
``hosp/d_labitems``, ``hosp/patients``, ``hosp/admissions``, ``hosp/diagnoses_icd``,
``hosp/d_icd_diagnoses``, ``icu/inputevents``, ``icu/procedureevents``, ``icu/outputevents``)
from :mod:`dea.synth`, so the MIMIC importer can be developed and
tested without credentials. Like the real thing it charts temperature in °F and °C under two
items, FiO2 in percent and codes diagnoses in ICD-9 or ICD-10. The data is synthetic.
"""

from __future__ import annotations

import gzip
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

from dea.core.model import ENCOUNTER_ID, END, START, TIME, VALUE, VARIABLE
from dea.synth.generate import _generate_batch, _ids

# DEA synthetic variable -> (MIMIC itemid, label, unit, table)
CHART_ITEMS: dict[str, tuple[int, str, str]] = {
    "HR": (220045, "Heart Rate", "bpm"),
    "MAP": (220052, "Arterial Blood Pressure mean", "mmHg"),
    "SpO2": (220277, "O2 saturation pulseoxymetry", "%"),
    "RR": (220210, "Respiratory Rate", "insp/min"),
    "Temp": (223762, "Temperature Celsius", "°C"),
    "FiO2": (223835, "Inspired O2 Fraction", "%"),
    "PEEP": (220339, "PEEP set", "cmH2O"),
    "TidalVolume_per_kg": (224685, "Tidal Volume (observed)", "mL"),
    "Compliance": (229661, "Compliance", "mL/cmH2O"),
    "paO2": (220224, "Arterial O2 pressure", "mmHg"),
    "paCO2": (220235, "Arterial CO2 Pressure", "mmHg"),
    "pH": (223830, "PH (Arterial)", "units"),
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
NOREPI_ITEM = (221906, "Norepinephrine", "mcg/kg/min")  # inputevents
VENT_ITEM = (225792, "Invasive Ventilation")  # procedureevents
URINE_ITEM = (226559, "Foley")  # outputevents
WEIGHT_ITEM = (226512, "Admission Weight (Kg)")
TEMP_F_ITEM = (223761, "Temperature Fahrenheit")  # MIMIC charts °F and °C as separate items
FAHRENHEIT_SHARE = 0.3
# (code, version, title): MIMIC-IV mixes ICD-9 and ICD-10 across admissions
DIAGNOSES: list[tuple[str, int, str]] = [
    ("A41.9", 10, "Sepsis, unspecified organism"),
    ("038.9", 9, "Unspecified septicemia"),
    ("J80", 10, "Acute respiratory distress syndrome"),
    ("518.82", 9, "Other pulmonary insufficiency, not elsewhere classified"),
    ("I50.9", 10, "Heart failure, unspecified"),
    ("428.0", 9, "Congestive heart failure, unspecified"),
    ("E11.9", 10, "Type 2 diabetes mellitus without complications"),
    ("250.00", 9, "Diabetes mellitus without mention of complication, type II"),
    ("N17.9", 10, "Acute kidney failure, unspecified"),
    ("584.9", 9, "Acute kidney failure, unspecified"),
    ("J18.9", 10, "Pneumonia, unspecified organism"),
    ("486", 9, "Pneumonia, organism unspecified"),
    ("I10", 10, "Essential (primary) hypertension"),
    ("401.9", 9, "Unspecified essential hypertension"),
]
MODE_MAP = {"PSV": "CPAP/PSV", "PCV": "PCV/PSV", "VCV": "CMV/ASSIST", "none": "Standby"}


def _write(df: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wb") as fh:
        fh.write(df.write_csv().encode())


def write_mimic_demo(
    path: Path | str, n_stays: int = 50, seed: int = 0, max_hours: float = 14 * 24
) -> Path:
    """Write MIMIC-IV-shaped tables for ``n_stays`` synthetic ICU stays under ``path``."""
    root = Path(path)
    rng = np.random.default_rng(seed)
    ids = _ids(rng, n_stays)
    static, dynamic, events, intervals = _generate_batch(
        rng, ids, min_hours=12, max_hours=max_hours
    )
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
    joined = (
        dynamic.join(key, on=ENCOUNTER_ID)
        .join(static.select(ENCOUNTER_ID, "weight_kg"), on=ENCOUNTER_ID)
        .with_columns(
            (pl.col("intime") + pl.col(TIME)).alias("charttime"),
            (pl.col("stay_id") - 20_000_000).alias("subject_id"),
            (pl.col("stay_id") - 10_000_000).alias("hadm_id"),
            # MIMIC charts the absolute tidal volume in mL, not the per-kg value
            pl.when(pl.col(VARIABLE) == "TidalVolume_per_kg")
            .then(pl.col(VALUE) * pl.col("weight_kg"))
            .otherwise(pl.col(VALUE))
            .alias(VALUE),
        )
    )
    chart_map = pl.DataFrame(
        {
            VARIABLE: list(CHART_ITEMS),
            "itemid": [v[0] for v in CHART_ITEMS.values()],
            "valueuom": [v[2] for v in CHART_ITEMS.values()],
        }
    )
    is_temp = pl.col("itemid") == CHART_ITEMS["Temp"][0]
    to_f = is_temp & (pl.int_range(pl.len()) % 100 < int(FAHRENHEIT_SHARE * 100))
    is_fio2 = pl.col("itemid") == CHART_ITEMS["FiO2"][0]
    chart = (
        joined.join(chart_map, on=VARIABLE)
        .with_columns(
            pl.when(to_f)
            .then(pl.col(VALUE) * 1.8 + 32)
            .when(is_fio2)
            .then(pl.col(VALUE) * 100)
            .otherwise(pl.col(VALUE))
            .round(2)
            .alias(VALUE),
            pl.when(to_f)
            .then(pl.lit(TEMP_F_ITEM[0], dtype=pl.Int64))
            .otherwise("itemid")
            .alias("itemid"),
            pl.when(to_f).then(pl.lit("°F")).otherwise("valueuom").alias("valueuom"),
        )
        .select(
            "subject_id",
            "hadm_id",
            "stay_id",
            pl.lit(None, dtype=pl.Int64).alias("caregiver_id"),
            "charttime",
            (pl.col("charttime") + pl.duration(minutes=5)).alias("storetime"),
            "itemid",
            pl.col(VALUE).cast(pl.String).alias("value"),
            pl.col(VALUE).alias("valuenum"),
            "valueuom",
            pl.lit(0, dtype=pl.Int64).alias("warning"),
        )
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
    modes = (
        events.filter(pl.col(VARIABLE) != "Note")
        .join(key, on=ENCOUNTER_ID)
        .select(
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
    )
    chartevents = pl.concat([chart, weight, modes]).sort("stay_id", "charttime", "itemid")

    ids_key = key.join(static.select(ENCOUNTER_ID, "weight_kg"), on=ENCOUNTER_ID).with_columns(
        (pl.col("stay_id") - 20_000_000).alias("subject_id"),
        (pl.col("stay_id") - 10_000_000).alias("hadm_id"),
    )
    spans = intervals.join(ids_key, on=ENCOUNTER_ID).with_columns(
        (pl.col("intime") + pl.col(START)).alias("starttime"),
        (pl.col("intime") + pl.col(END)).alias("endtime"),
        ((pl.col(END) - pl.col(START)).dt.total_seconds() / 60).alias("minutes"),
    )
    norepi = spans.filter(pl.col(VARIABLE) == "Norepinephrine_infusion")
    inputevents = (
        norepi.sort("stay_id", "starttime")
        .with_row_index("orderid", 1)
        .select(
            "subject_id",
            "hadm_id",
            "stay_id",
            pl.lit(None, dtype=pl.Int64).alias("caregiver_id"),
            "starttime",
            "endtime",
            pl.col("starttime").alias("storetime"),
            pl.lit(NOREPI_ITEM[0], dtype=pl.Int64).alias("itemid"),
            (pl.col(VALUE) * pl.col("weight_kg") * pl.col("minutes") / 1000)
            .round(3)
            .alias("amount"),
            pl.lit("mg").alias("amountuom"),
            pl.col(VALUE).alias("rate"),
            pl.lit(NOREPI_ITEM[2]).alias("rateuom"),
            "orderid",
            pl.col("orderid").alias("linkorderid"),
            pl.lit("Norepinephrine").alias("ordercategoryname"),
            pl.lit("Drug Push").alias("secondaryordercategoryname"),
            pl.lit("Continuous Med").alias("ordercomponenttypedescription"),
            pl.lit("Main order parameter").alias("ordercategorydescription"),
            pl.lit(0.0).alias("patientweight"),
            pl.lit(0).alias("totalamount"),
            pl.lit("ml").alias("totalamountuom"),
            pl.lit(0).alias("isopenbag"),
            pl.lit(0).alias("continueinnextdept"),
            pl.lit("FinishedRunning").alias("statusdescription"),
            pl.lit(0.0).alias("originalamount"),
            pl.col(VALUE).alias("originalrate"),
        )
    )
    vent = spans.filter(pl.col(VARIABLE) == "Ventilation")
    procedureevents = (
        vent.sort("stay_id", "starttime")
        .with_row_index("orderid", 1)
        .select(
            "subject_id",
            "hadm_id",
            "stay_id",
            pl.lit(None, dtype=pl.Int64).alias("caregiver_id"),
            "starttime",
            "endtime",
            pl.col("starttime").alias("storetime"),
            pl.lit(VENT_ITEM[0], dtype=pl.Int64).alias("itemid"),
            pl.col("minutes").round(0).alias("value"),
            pl.lit("min").alias("valueuom"),
            pl.lit(None, dtype=pl.String).alias("location"),
            pl.lit(None, dtype=pl.String).alias("locationcategory"),
            "orderid",
            pl.col("orderid").alias("linkorderid"),
            pl.lit("Ventilation").alias("ordercategoryname"),
            pl.lit("Task").alias("ordercategorydescription"),
            pl.lit(0.0).alias("patientweight"),
            pl.lit(0).alias("isopenbag"),
            pl.lit(0).alias("continueinnextdept"),
            pl.lit("FinishedRunning").alias("statusdescription"),
            pl.lit(0.0).alias("originalamount"),
            pl.lit(0.0).alias("originalrate"),
        )
    )
    # hourly urine output per stay
    urine_rows = []
    for r in stays:
        hours = int((r["outtime"] - r["intime"]).total_seconds() // 3600)
        for h in range(1, hours + 1):
            urine_rows.append(
                {
                    "subject_id": r["subject_id"],
                    "hadm_id": r["hadm_id"],
                    "stay_id": r["stay_id"],
                    "caregiver_id": None,
                    "charttime": r["intime"] + timedelta(hours=h),
                    "storetime": r["intime"] + timedelta(hours=h),
                    "itemid": URINE_ITEM[0],
                    "value": float(np.clip(rng.normal(70, 35), 0, 400).round(0)),
                    "valueuom": "ml",
                }
            )
    outputevents = pl.DataFrame(urine_rows)

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

    extra = [VENT_MODE_ITEM, WEIGHT_ITEM, TEMP_F_ITEM, NOREPI_ITEM[:2], VENT_ITEM, URINE_ITEM]
    d_items = pl.DataFrame(
        {
            "itemid": [v[0] for v in CHART_ITEMS.values()] + [e[0] for e in extra],
            "label": [v[1] for v in CHART_ITEMS.values()] + [e[1] for e in extra],
            "abbreviation": [v[1][:20] for v in CHART_ITEMS.values()]
            + ["Vent Mode", "Admit Wt", "Temp F", "Norepi", "Vent", "Foley"],
            "linksto": ["chartevents"] * (len(CHART_ITEMS) + 3)
            + ["inputevents", "procedureevents", "outputevents"],
            "category": ["Routine Vital Signs"] * 5
            + ["Respiratory"] * 4
            + ["Labs"] * 3
            + ["Respiratory", "General", "Routine Vital Signs"]
            + ["Medications", "Ventilation", "Output"],
            "unitname": [v[2] for v in CHART_ITEMS.values()]
            + ["", "kg", "°F", "mcg/kg/min", "min", "ml"],
            "param_type": ["Numeric"] * len(CHART_ITEMS)
            + ["Text", "Numeric", "Numeric", "Numeric", "Numeric", "Numeric"],
            "lownormalvalue": [None] * (len(CHART_ITEMS) + 6),
            "highnormalvalue": [None] * (len(CHART_ITEMS) + 6),
        }
    )
    diagnoses = []
    for r in rows:
        hadm = stay_ids[r[ENCOUNTER_ID]] - 10_000_000
        version = int(rng.choice([9, 10]))
        pool = [d for d in DIAGNOSES if d[1] == version]
        picks = rng.choice(len(pool), size=int(rng.integers(1, 5)), replace=False)
        for seq, k in enumerate(picks, 1):
            code, _, _ = pool[k]
            diagnoses.append(
                {
                    "subject_id": stay_ids[r[ENCOUNTER_ID]] - 20_000_000,
                    "hadm_id": hadm,
                    "seq_num": seq,
                    "icd_code": code.replace(".", ""),
                    "icd_version": version,
                }
            )
    d_icd = pl.DataFrame(
        {
            "icd_code": [c.replace(".", "") for c, _, _ in DIAGNOSES],
            "icd_version": [v for _, v, _ in DIAGNOSES],
            "long_title": [t for _, _, t in DIAGNOSES],
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

    notes = (
        events.filter(pl.col(VARIABLE) == "Note")
        .join(key, on=ENCOUNTER_ID)
        .sort("stay_id", TIME)
        .with_row_index("note_seq", 1)
        .select(
            (pl.lit("10000000-DS-") + pl.col("note_seq").cast(pl.String)).alias("note_id"),
            (pl.col("stay_id") - 20_000_000).alias("subject_id"),
            (pl.col("stay_id") - 10_000_000).alias("hadm_id"),
            pl.lit("DS").alias("note_type"),
            "note_seq",
            (pl.col("intime") + pl.col(TIME)).alias("charttime"),
            (pl.col("intime") + pl.col(TIME)).alias("storetime"),
            pl.col(VALUE).alias("text"),
        )
    )
    _write(notes, root / "note" / "discharge.csv.gz")
    _write(pl.DataFrame(patients), root / "hosp" / "patients.csv.gz")
    _write(pl.DataFrame(admissions), root / "hosp" / "admissions.csv.gz")
    _write(pl.DataFrame(stays), root / "icu" / "icustays.csv.gz")
    _write(d_items, root / "icu" / "d_items.csv.gz")
    _write(chartevents, root / "icu" / "chartevents.csv.gz")
    _write(inputevents, root / "icu" / "inputevents.csv.gz")
    _write(procedureevents, root / "icu" / "procedureevents.csv.gz")
    _write(outputevents, root / "icu" / "outputevents.csv.gz")
    _write(d_labitems, root / "hosp" / "d_labitems.csv.gz")
    _write(pl.DataFrame(diagnoses), root / "hosp" / "diagnoses_icd.csv.gz")
    _write(d_icd, root / "hosp" / "d_icd_diagnoses.csv.gz")
    _write(labevents, root / "hosp" / "labevents.csv.gz")
    return root
