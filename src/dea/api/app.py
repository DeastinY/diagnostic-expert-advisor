"""FastAPI application exposing cohorts, charts, encounters, profiles, suggestions and rules.

Charts are returned as Vega-Lite specifications built by :mod:`dea.charts`, so the frontend
renders exactly what the Python side defines. Filters are passed as repeated ``where`` query
parameters (SQL over the feature table) and combined with AND.
"""

from __future__ import annotations

import functools
import hashlib
import json
import os
import secrets
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any, Literal

import polars as pl
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dea import __version__
from dea.core import Cohort, FilterError, Rule
from dea.core.config import ProcessingConfig
from dea.core.time import format_duration
from dea.io import delete_cohort, detect, import_path, list_cohorts, load_cohort, read_table
from dea.io.storage import is_cohort_dir
from dea.profile import profile_cohort

Where = Annotated[list[str] | None, Query(description="SQL filters over the feature table")]


class CohortStore:
    """Loads cohorts from a data directory and caches them until the files change."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, tuple[float, Cohort]] = {}

    def path(self, name: str) -> Path:
        p = (self.data_dir / name).resolve()
        if self.data_dir.resolve() not in p.parents or not is_cohort_dir(p):
            raise HTTPException(404, f"unknown cohort {name!r}")
        return p

    @staticmethod
    def _signature(path: Path) -> float:
        files = [
            path / "cohort.yaml",
            path / "static.parquet",
            *(path / "dynamic").rglob("*.parquet"),
            *(path / "events").rglob("*.parquet"),
            *(path / "intervals").rglob("*.parquet"),
        ]
        return max((f.stat().st_mtime for f in files if f.exists()), default=0.0)

    def result(
        self, name: str, key: str, params: dict[str, Any], compute: Callable[[], Any]
    ) -> Any:
        """JSON result cache under ``derived/cache``: valid until the cohort's files change.

        Entries are named ``<key>-<signature>-<params>.json``; entries from an older
        signature are removed the next time a result is written.
        """
        path = self.path(name)
        sig = hashlib.sha1(str(self._signature(path)).encode()).hexdigest()[:10]
        pdig = hashlib.sha1(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()[
            :12
        ]
        folder = path / "derived" / "cache"
        file = folder / f"{key}-{sig}-{pdig}.json"
        if file.exists():
            try:
                return json.loads(file.read_text(encoding="utf-8"))
            except ValueError:
                file.unlink(missing_ok=True)
        value = compute()
        folder.mkdir(parents=True, exist_ok=True)
        for stale in folder.glob(f"{key}-*.json"):
            if f"-{sig}-" not in stale.name:
                stale.unlink(missing_ok=True)
        file.write_text(json.dumps(value, default=str), encoding="utf-8")
        return value

    def get(self, name: str) -> Cohort:
        path = self.path(name)
        sig = self._signature(path)
        hit = self._cache.get(name)
        if hit and hit[0] == sig:
            return hit[1]
        cohort = load_cohort(path)
        self._cache[name] = (sig, cohort)
        return cohort

    def invalidate(self, name: str | None = None) -> None:
        if name:
            self._cache.pop(name, None)
        else:
            self._cache.clear()


def cached(store: CohortStore, key: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Serve an endpoint's JSON result from :meth:`CohortStore.result`."""

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(name: str, **kwargs: Any) -> Any:
            return store.result(name, key, kwargs, lambda: fn(name, **kwargs))

        return wrapper

    return decorate


def _filtered(cohort: Cohort, where: list[str] | None) -> Cohort:
    if not where:
        return cohort
    try:
        return cohort.filter(where)
    except FilterError as exc:
        raise HTTPException(400, str(exc)) from exc


def _records(frame: pl.DataFrame, limit: int | None = None) -> list[dict[str, Any]]:
    if limit is not None:
        frame = frame.head(limit)
    out = []
    for row in frame.iter_rows(named=True):
        out.append(
            {k: (v.total_seconds() if hasattr(v, "total_seconds") else v) for k, v in row.items()}
        )
    return out


# ------------------------------------------------------------------------- schemas


class RuleIn(BaseModel):
    kind: Literal["filter", "tag"]
    label: str
    where: str
    description: str = ""


class DeriveIn(BaseModel):
    name: str
    where: list[str]
    description: str = ""


class ImportIn(BaseModel):
    path: str
    name: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


class DemoIn(BaseModel):
    name: str = "demo"
    n: int = 60
    mimic: bool = False
    sites: int = 1


class SiteIn(BaseModel):
    column: str | None = None


class ProcessingIn(BaseModel):
    resample_every: str | None = None
    aggregate: str = "mean"
    fill: str = "none"
    drop_variables: list[str] = Field(default_factory=list)
    fill_overrides: dict[str, str] | None = None


class ApplyIn(BaseModel):
    ids: list[str]


class RenameIn(BaseModel):
    mapping: dict[str, str]


class MappingIn(BaseModel):
    path: str | None = None
    mapping: dict[str, str] = Field(default_factory=dict)
    units: dict[str, str] = Field(default_factory=dict)


class AdviseIn(BaseModel):
    model: str | None = None
    base_url: str | None = None
    api_key: str | None = None


class AdviseApplyIn(BaseModel):
    recommendation: dict[str, Any]
    rename: bool = False


COVERAGE_VARIABLES = 40  # rows of the coverage heatmap; MIMIC has hundreds of variables

# ------------------------------------------------------------------------- app


def create_app(
    data_dir: Path | str | None = None, frontend_dir: Path | str | None = None
) -> FastAPI:
    store = CohortStore(Path(data_dir or os.environ.get("DEA_DATA_DIR", "data")))
    app = FastAPI(title="Diagnostic Expert Advisor", version="2")
    app.state.store = store
    token = os.environ.get("DEA_TOKEN")
    if token:  # optional shared-instance protection: one bearer token for the whole API

        @app.middleware("http")
        async def require_token(request: Request, call_next):  # noqa: ANN001, ANN202
            path = request.url.path
            if path.startswith("/api/") and path != "/api/health":
                given = request.headers.get("authorization", "")
                given = given[7:] if given.lower().startswith("bearer ") else given
                given = given or request.query_params.get("token", "")
                if not secrets.compare_digest(given, token):
                    return JSONResponse(
                        {"detail": "a valid DEA_TOKEN is required"}, status_code=401
                    )
            return await call_next(request)

    # ---- cohorts
    @app.get("/api/cohorts")
    def cohorts() -> list[dict[str, Any]]:
        from dea.io import ImportReport

        out = []
        for info in list_cohorts(store.data_dir):
            cohort = store.get(info.path.name)
            cfg = cohort.config
            cache = cohort._features_cache()
            los = None
            if cache is not None and cache.exists():  # only when already computed: stays cheap
                hours = pl.scan_parquet(cache).select("los_hours").collect().get_column("los_hours")
                if hours.len():
                    hist = (hours / 24).clip(0, 30).hist(bin_count=12, include_breakpoint=False)
                    los = [int(c) for c in hist.get_column("count")]
            report = ImportReport.load_dict(info.path) or {}
            checks = report.get("checks") or []
            out.append(
                {
                    "name": info.path.name,
                    "title": info.name,
                    "n_encounters": info.n_encounters,
                    "n_variables": len(cohort.variables),
                    "description": info.description,
                    "parent": cfg.parent,
                    "derived_by": cfg.derived_by,
                    "created_at": cfg.created_at.isoformat(),
                    "source": cfg.source,
                    "format": report.get("format"),
                    "site_column": cfg.site_column,
                    "n_tags": len(cfg.tags),
                    "n_filters": len(cfg.filters),
                    "n_warnings": sum(
                        1 for c in checks if c.get("severity") in ("warning", "error")
                    ),
                    "los_hist": los,
                }
            )
        return out

    @app.get("/api/cohorts/{name}")
    def cohort_detail(name: str, where: Where = None) -> dict[str, Any]:
        full = store.get(name)
        cohort = _filtered(full, where)
        s = cohort.summary()
        return {
            "name": name,
            "config": full.config.model_dump(mode="json"),
            "n_total": len(full),
            "summary": {
                **s.as_dict(),
                "median_stay": format_duration(s.los_hours_median * 3600),
                "longest_stay": format_duration(s.los_hours_max * 3600),
            },
            "variables": full.variables,
            "event_variables": full.event_variables,
            "interval_variables": full.interval_variables,
            "static_columns": full.static_columns,
            "derived": full.list_derived(),
            "defaults": {
                "static_column": full.default_static_column(),
                "variable": full.default_variable(),
            },
        }

    @app.delete("/api/cohorts/{name}", status_code=204)
    def cohort_delete(name: str) -> None:
        delete_cohort(store.path(name))
        store.invalidate(name)

    @app.post("/api/cohorts/demo", status_code=201)
    def cohort_demo(body: DemoIn) -> dict[str, Any]:
        from dea.synth import write_mimic_demo, write_synthetic

        target = store.data_dir / body.name
        if body.mimic:
            src = write_mimic_demo(Path(tempfile.mkdtemp(prefix="dea-mimic-")), n_stays=body.n)
            cohort, report = import_path(src, name=body.name)
            cohort.save(target)
            report.save(target)
            shutil.rmtree(src, ignore_errors=True)
            return {"name": body.name, "report": report.to_dict()}
        write_synthetic(target, n_encounters=body.n, name=body.name, sites=body.sites)
        return {"name": body.name}

    @app.post("/api/detect")
    def detect_source(body: ImportIn) -> dict[str, Any]:
        p = Path(body.path)
        if not p.exists():
            raise HTTPException(404, f"{p} does not exist")
        det = detect(p)
        preview = _records(read_table(p, n_rows=8)) if det.format == "table" else []
        return {
            **{k: (str(v) if isinstance(v, Path) else v) for k, v in det.__dict__.items()},
            "preview": preview,
        }

    def _import(body: ImportIn) -> tuple[Cohort, Any]:
        p = Path(body.path)
        if not p.exists():
            raise HTTPException(404, f"{p} does not exist")
        try:
            return import_path(p, name=body.name or p.stem, **body.overrides)
        except Exception as exc:  # import errors are user errors (bad mapping, bad file)
            raise HTTPException(400, str(exc)) from exc

    @app.post("/api/cohorts/preview")
    def cohort_preview(body: ImportIn) -> dict[str, Any]:
        """Import without writing: the report with unit conversions and checks."""
        cohort, report = _import(body)
        return {"name": cohort.name, "report": report.to_dict()}

    @app.post("/api/cohorts/import", status_code=201)
    def cohort_import(body: ImportIn) -> dict[str, Any]:
        cohort, report = _import(body)
        name = cohort.name
        target = store.data_dir / name
        if target.exists():
            shutil.rmtree(target)
        cohort.save(target)
        report.save(target)
        store.invalidate(name)
        return {"name": name, "report": report.to_dict()}

    @app.get("/api/cohorts/{name}/import")
    def import_report(name: str) -> dict[str, Any]:
        from dea.io import ImportReport

        return ImportReport.load_dict(store.path(name)) or {}

    @app.post("/api/cohorts/upload", status_code=201)
    async def cohort_upload(file: UploadFile, name: str | None = None) -> dict[str, Any]:
        tmp = Path(tempfile.mkdtemp(prefix="dea-upload-"))
        target = tmp / (file.filename or "upload")
        target.write_bytes(await file.read())
        if target.suffix == ".zip":
            with zipfile.ZipFile(target) as zf:
                zf.extractall(tmp / "unzipped")
            inner = list((tmp / "unzipped").iterdir())
            source = inner[0] if len(inner) == 1 and inner[0].is_dir() else tmp / "unzipped"
        else:
            source = target
        det = detect(source)
        preview = _records(read_table(source, n_rows=8)) if det.format == "table" else []
        return {
            "staged": str(source),
            "name": name or source.stem,
            "detection": {
                **{k: (str(v) if isinstance(v, Path) else v) for k, v in det.__dict__.items()},
                "preview": preview,
            },
        }

    # ---- features, filters, rules
    @app.get("/api/cohorts/{name}/catalog")
    def catalog(name: str) -> list[dict[str, Any]]:
        return store.get(name).feature_catalog()

    @app.get("/api/cohorts/{name}/count")
    def count(name: str, where: Where = None) -> dict[str, int]:
        full = store.get(name)
        return {"n": len(_filtered(full, where)), "n_total": len(full)}

    @app.post("/api/cohorts/{name}/rules", status_code=201)
    def add_rule(name: str, body: RuleIn) -> dict[str, Any]:
        cohort = store.get(name)
        try:
            cohort.evaluate(body.where)
        except FilterError as exc:
            raise HTTPException(400, str(exc)) from exc
        rules = cohort.config.filters if body.kind == "filter" else cohort.config.tags
        rules[:] = [r for r in rules if r.label != body.label]
        rules.append(Rule(label=body.label, where=body.where, description=body.description))
        cohort.config.save(cohort.root / "cohort.yaml")
        store.invalidate(name)
        return {"kind": body.kind, "label": body.label}

    @app.delete("/api/cohorts/{name}/rules/{kind}/{label}", status_code=204)
    def delete_rule(name: str, kind: Literal["filter", "tag"], label: str) -> None:
        cohort = store.get(name)
        rules = cohort.config.filters if kind == "filter" else cohort.config.tags
        rules[:] = [r for r in rules if r.label != label]
        cohort.config.save(cohort.root / "cohort.yaml")
        store.invalidate(name)

    @app.post("/api/cohorts/{name}/derive", status_code=201)
    def derive(name: str, body: DeriveIn) -> dict[str, Any]:
        cohort = store.get(name)
        target = store.data_dir / body.name
        if target.exists():
            raise HTTPException(409, f"cohort {body.name!r} exists")
        try:
            sub = cohort.derive(target, body.where, name=body.name, description=body.description)
        except FilterError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"name": body.name, "n_encounters": len(sub)}

    @app.put("/api/cohorts/{name}/processing")
    def set_processing(name: str, body: ProcessingIn) -> dict[str, Any]:
        cohort = store.get(name)
        cohort.config.processing = ProcessingConfig(
            resample_every=body.resample_every or None,
            aggregate=body.aggregate,
            fill=body.fill,
            fill_overrides=cohort.config.processing.fill_overrides,
            drop_variables=body.drop_variables,
        )
        cohort.config.save(cohort.root / "cohort.yaml")
        store.invalidate(name)
        return cohort.config.processing.model_dump()

    @app.post("/api/cohorts/{name}/rename")
    def rename(name: str, body: RenameIn) -> dict[str, Any]:
        cohort = store.get(name).rename_variables(body.mapping)
        cohort.save()
        store.invalidate(name)
        return {"variables": cohort.variables}

    # ---- charts
    @app.get("/api/cohorts/{name}/charts/overview")
    @cached(store, "overview")
    def charts_overview(
        name: str,
        where: Where = None,
        static_column: str | None = None,
        variable: str | None = None,
        by: str | None = None,
        dark: bool = False,
    ) -> dict[str, Any]:
        from dea import charts

        full = store.get(name)
        cohort = _filtered(full, where)
        cfg = cohort.config
        feats = cohort.features()
        out: dict[str, Any] = {}
        if feats.height:
            out["los"] = charts.histogram(
                feats["los_hours"] / 24, "Length of stay", "days", dark=dark
            ).to_dict()
            matrix = cohort.coverage_matrix(every="4h", max_hours=168)
            n_vars = matrix.get_column("variable").n_unique() if matrix.height else 0
            heat = charts.coverage_heatmap(
                matrix, cfg.variable_labels, 4.0, dark=dark, max_variables=COVERAGE_VARIABLES
            )
            out["coverage_heatmap"] = heat.to_dict() if heat is not None else None
            out["coverage_shown"] = {"variables": min(COVERAGE_VARIABLES, n_vars), "of": n_vars}
            if cfg.tags:
                tags = cohort.tag_matrix()
                counts = pl.DataFrame(
                    {
                        "tag": [r.label for r in cfg.tags],
                        "encounters": [int(tags[r.label].sum()) for r in cfg.tags],
                    }
                )
                out["tags"] = charts.horizontal_bars(
                    counts, "tag", "encounters", "Encounters", dark=dark
                ).to_dict()
            if static_column and static_column in cohort.static.columns:
                series = cohort.static[static_column]
                chart = (
                    charts.histogram(series, cfg.label(static_column), dark=dark)
                    if series.dtype.is_numeric() and series.n_unique() > 12
                    else charts.category_counts(series, cfg.label(static_column), dark=dark)
                )
                out["static"] = chart.to_dict()
            if where and len(cohort) < len(full):
                # how the current selection differs from the rest of the cohort
                from dea.quality.sites import site_bins

                ids = cohort.encounter_ids
                tagged = full.static.with_columns(
                    pl.when(pl.col("encounter_id").is_in(ids))
                    .then(pl.lit("Selection"))
                    .otherwise(pl.lit("Rest of cohort"))
                    .alias("_selection")
                )
                both = Cohort(
                    tagged, full.dynamic, events=full.events, config=full.config, root=full.root
                )
                top = (
                    cohort.variable_counts()
                    .sort("n_observations", descending=True)
                    .get_column("variable")
                    .to_list()[:9]
                )
                bins = site_bins(both, "_selection", top, sample=150)
                dist = charts.site_distributions(
                    bins, cfg.variable_labels, cfg.variable_units, dark=dark, columns=3
                )
                out["selection"] = dist.to_dict() if dist is not None else None
            if variable and f"{variable}__mean" in feats.columns:
                unit = cfg.variable_units.get(variable, "")
                if by and by in cohort.static.columns:
                    out["variable"] = charts.group_boxplot(
                        feats, f"{variable}__mean", by, cfg.label(variable), unit, dark=dark
                    ).to_dict()
                else:
                    out["variable"] = charts.histogram(
                        feats[f"{variable}__mean"], cfg.label(variable), unit, dark=dark
                    ).to_dict()
        return out

    @app.get("/api/cohorts/{name}/charts/profile")
    @cached(store, "charts_profile")
    def charts_profile(name: str, where: Where = None, dark: bool = False) -> dict[str, Any]:
        from dea import charts

        cohort = _filtered(store.get(name), where)
        cfg = cohort.config
        out: dict[str, Any] = {}
        gallery = charts.distribution_gallery(
            cohort.distribution_bins(), cfg.variable_labels, cfg.variable_units, dark=dark
        )
        out["gallery"] = gallery.to_dict() if gallery is not None else None
        counts = cohort.variable_counts().rename({"variable": "name"})
        if counts.height:
            out["observations"] = charts.horizontal_bars(
                counts.head(40), "name", "n_observations", "Observations", dark=dark
            ).to_dict()
        return out

    @app.get("/api/cohorts/{name}/charts/trajectory")
    @cached(store, "trajectory")
    def chart_trajectory(
        name: str,
        variable: str,
        every: str = "1h",
        by: str | None = None,
        max_hours: float = 168,
        where: Where = None,
        dark: bool = False,
    ) -> dict[str, Any]:
        from dea import charts

        cohort = _filtered(store.get(name), where)
        if variable not in cohort.variables:
            raise HTTPException(404, f"unknown variable {variable!r}")
        try:
            traj = cohort.trajectory(variable, every=every, by=by, max_hours=max_hours)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if traj.is_empty():
            return {"spec": None, "rows": 0}
        cfg = cohort.config
        return {
            "spec": charts.trajectory(
                traj, cfg.label(variable), cfg.variable_units.get(variable, ""), by=by, dark=dark
            ).to_dict(),
            "rows": traj.height,
        }

    # ---- encounters
    @app.get("/api/cohorts/{name}/encounters")
    def encounters(
        name: str,
        where: Where = None,
        q: str | None = None,
        page: int = 1,
        size: int = 25,
        sort: str = "encounter_id",
        desc: bool = False,
    ) -> dict[str, Any]:
        cohort = _filtered(store.get(name), where)
        feats = cohort.features()
        cols = ["encounter_id", "los_hours", "n_observations", *cohort.static_columns]
        table = feats.select([c for c in cols if c in feats.columns])
        if cohort.config.tags:
            table = table.join(cohort.tag_matrix(), on="encounter_id", how="left")
        if q:
            table = table.filter(pl.col("encounter_id").str.contains(q, literal=True))
        if sort in table.columns:
            table = table.sort(sort, descending=desc, nulls_last=True)
        total = table.height
        page = max(1, page)
        rows = _records(table.slice((page - 1) * size, size))
        return {
            "rows": rows,
            "total": total,
            "page": page,
            "size": size,
            "columns": table.columns,
            "tags": [r.label for r in cohort.config.tags],
        }

    @app.get("/api/cohorts/{name}/encounters/{eid}")
    def encounter(name: str, eid: str) -> dict[str, Any]:
        cohort = store.get(name)
        try:
            e = cohort.get(eid)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        events = e.events.sort("time").with_columns(
            (pl.col("time").dt.total_seconds() / 3600).alias("hours")
        )
        return {
            "id": e.id,
            "static": {
                k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in e.static.items()
            },
            "los_hours": e.los_hours,
            "n_observations": e.n_observations,
            "variables": e.variables,
            "events": _records(events.select("hours", "variable", "value"), 2000),
            "intervals": _records(
                e.intervals.sort("start").select(
                    (pl.col("start").dt.total_seconds() / 3600).alias("start_h"),
                    (pl.col("end").dt.total_seconds() / 3600).alias("end_h"),
                    "variable",
                    "value",
                ),
                500,
            ),
            "plots": [v for v in cohort.config.plots if v in e.variables] or e.variables[:6],
        }

    @app.get("/api/cohorts/{name}/encounters/{eid}/chart")
    def encounter_chart(
        name: str,
        eid: str,
        variables: Annotated[list[str] | None, Query()] = None,
        every: str | None = None,
        dark: bool = False,
        band: bool = True,
    ) -> dict[str, Any]:
        from dea import charts

        cohort = store.get(name)
        try:
            e = cohort.get(eid)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        cfg = cohort.config
        chosen = variables or [v for v in cfg.plots if v in e.variables] or e.variables[:6]
        bands: dict[str, pl.DataFrame] = {}
        if band and len(cohort) > 1:
            horizon = int(e.los_hours) + 2
            for v in chosen:
                rows = store.result(
                    name,
                    "band",
                    {"variable": v, "every": "1h", "max_hours": horizon},
                    lambda v=v: (
                        cohort.trajectory(v, every="1h", max_hours=horizon)
                        .select("hours", "lo", "median", "hi")
                        .to_dicts()
                    ),
                )
                if rows:
                    bands[v] = pl.DataFrame(rows)
        chart = charts.small_multiples(
            e,
            chosen,
            every or None,
            labels=cfg.variable_labels,
            units=cfg.variable_units,
            dark=dark,
            bands=bands,
        )
        return {"spec": chart.to_dict() if chart is not None else None}

    @app.get("/api/cohorts/{name}/encounters/{eid}/wide")
    def encounter_wide(
        name: str, eid: str, every: str | None = None, limit: int = 2000
    ) -> dict[str, Any]:
        cohort = store.get(name)
        try:
            wide = cohort.get(eid).wide(every=every or None)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        wide = wide.with_columns((pl.col("time").dt.total_seconds() / 3600).alias("hours")).drop(
            "time"
        )
        return {"columns": wide.columns, "rows": _records(wide, limit), "total": wide.height}

    # ---- profile, suggestions, report
    @app.get("/api/cohorts/{name}/profile")
    @cached(store, "profile")
    def profile(name: str, where: Where = None) -> dict[str, Any]:
        return profile_cohort(_filtered(store.get(name), where)).to_dict()

    @app.get("/api/cohorts/{name}/sites")
    @cached(store, "sites")
    def sites(
        name: str, where: Where = None, column: str | None = None, dark: bool = False
    ) -> dict[str, Any]:
        """Per-site comparison: profile table, checks and charts for one site column."""
        from dea import charts
        from dea.quality import (
            detect_site_column,
            site_bins,
            site_casemix,
            site_checks,
            site_profile,
        )

        full = store.get(name)
        cohort = _filtered(full, where)
        static = cohort.static
        candidates = [
            c
            for c in static.columns
            if c != "encounter_id"
            and (static[c].dtype == pl.String or static[c].dtype.is_integer())
            and 2 <= static[c].drop_nulls().n_unique() <= 50
        ]
        col = column or cohort.config.site_column or detect_site_column(static)
        out: dict[str, Any] = {
            "column": col,
            "candidates": candidates,
            "configured": full.config.site_column,
        }
        if not col or col not in static.columns:
            return {**out, "profile": [], "checks": [], "charts": {}}
        prof = site_profile(cohort, col)
        found = site_checks(cohort, col, prof)
        cfg = cohort.config
        by_obs = (
            prof.group_by("variable")
            .agg(pl.col("n_obs").sum().alias("n"))
            .sort("n", descending=True)
            .get_column("variable")
            .to_list()
        )
        top = by_obs[:12]
        # readable with many sites: 8 largest sites in the overlays, 12 in the matrices,
        # 30 most observed variables in the matrices
        big_sites = (
            prof.group_by("site")
            .agg(pl.col("n_site").first())
            .sort(["n_site", "site"], descending=[True, False])
            .get_column("site")
            .to_list()
        )
        shown = prof.filter(
            pl.col("site").is_in(big_sites[:12]) & pl.col("variable").is_in(by_obs[:30])
        )
        bins = site_bins(cohort, col, top).filter(pl.col("site").is_in(big_sites[:8]))
        dist = charts.site_distributions(bins, cfg.variable_labels, cfg.variable_units, dark=dark)
        cov = charts.site_matrix(
            shown,
            "coverage",
            "Share of encounters with data",
            ".0%",
            cfg.variable_labels,
            dark=dark,
        )
        feats = cohort.features()
        los = None
        if feats.height and col in feats.columns:
            feats = feats.filter(pl.col(col).cast(pl.String()).is_in(big_sites[:12]))
            # point-in-time encounters (outpatient visits) have no stay to compare
            if feats.height and (feats.get_column("los_hours") > 1).mean() >= 0.5:
                los = charts.group_boxplot(
                    feats, "los_hours", col, "Length of stay", "h", dark=dark
                )
        iv = charts.site_matrix(
            shown.with_columns((pl.col("interval_s") / 60).alias("interval_min")),
            "interval_min",
            "Typical sampling interval (min)",
            ".0f",
            cfg.variable_labels,
            dark=dark,
            domain=None,
            log=True,
        )
        return {
            **out,
            "shown": {
                "sites": min(12, len(big_sites)),
                "of": len(big_sites),
                "overlay_sites": min(8, len(big_sites)),
                "variables": min(30, len(by_obs)),
                "of_variables": len(by_obs),
            },
            "sizes": _records(
                static.group_by(col)
                .agg(pl.len().alias("n"))
                .sort(["n", col], descending=[True, False])
            ),
            "profile": _records(prof),
            "casemix": _records(
                site_casemix(cohort, col).filter(pl.col("site").is_in(big_sites[:12]))
            ),
            "checks": [c.to_dict() for c in found],
            "charts": {
                "distributions": dist.to_dict() if dist is not None else None,
                "coverage": cov.to_dict() if cov is not None else None,
                "sampling": iv.to_dict() if iv is not None else None,
                "los": los.to_dict() if los is not None else None,
            },
        }

    @app.put("/api/cohorts/{name}/site")
    def set_site(name: str, body: SiteIn) -> dict[str, Any]:
        cohort = store.get(name)
        if body.column and body.column not in cohort.static.columns:
            raise HTTPException(400, f"unknown static column {body.column!r}")
        cohort.config.site_column = body.column or None
        cohort.config.save(store.path(name) / "cohort.yaml")
        store.invalidate(name)
        return {"site_column": cohort.config.site_column}

    @app.get("/api/cohorts/{name}/checks")
    @cached(store, "checks")
    def checks(name: str) -> list[dict[str, Any]]:
        from dea.quality import run_checks

        return [c.to_dict() for c in run_checks(store.get(name))]

    @app.get("/api/cohorts/{name}/suggestions")
    @cached(store, "suggestions")
    def suggestions(name: str) -> list[dict[str, Any]]:
        from dea.suggest import suggest

        return [s.to_dict() for s in suggest(store.get(name))]

    @app.post("/api/cohorts/{name}/suggestions/apply")
    def apply(name: str, body: ApplyIn) -> dict[str, Any]:
        from dea.suggest import apply_suggestions, suggest

        cohort = store.get(name)
        chosen = [s for s in suggest(cohort) if s.id in set(body.ids) and s.applicable]
        apply_suggestions(cohort, chosen).save()
        store.invalidate(name)
        return {"applied": [s.id for s in chosen]}

    @app.get("/api/cohorts/{name}/advise")
    def advise_status(name: str, where: Where = None) -> dict[str, Any]:
        from dea.advise import Advisor, AdvisorSettings

        cohort = _filtered(store.get(name), where)
        settings = AdvisorSettings.from_env()
        try:
            import openai  # noqa: F401

            installed = True
        except ImportError:
            installed = False
        return {
            "installed": installed,
            "configured": settings.configured,
            "model": settings.model,
            "base_url": settings.base_url,
            "payload": Advisor.payload(profile_cohort(cohort)),
        }

    @app.post("/api/cohorts/{name}/advise")
    def advise(name: str, body: AdviseIn, where: Where = None) -> dict[str, Any]:
        from dea.advise import Advisor, AdvisorError

        cohort = _filtered(store.get(name), where)
        try:
            rec = Advisor(model=body.model, base_url=body.base_url, api_key=body.api_key).recommend(
                profile_cohort(cohort)
            )
        except AdvisorError as exc:
            raise HTTPException(502, str(exc)) from exc
        return rec.model_dump()

    @app.post("/api/cohorts/{name}/advise/apply")
    def advise_apply(name: str, body: AdviseApplyIn) -> dict[str, Any]:
        from dea.advise import Recommendation

        cohort = store.get(name)
        rec = Recommendation.model_validate(body.recommendation)
        cohort.config = rec.apply_to(cohort.config, cohort.variables)
        if body.rename:
            cohort = cohort.rename_variables(rec.variable_map(cohort.variables))
            cohort.save()
        else:
            cohort.config.save(cohort.root / "cohort.yaml")
        store.invalidate(name)
        return {"variables": cohort.variables, "renamed": body.rename}

    @app.post("/api/cohorts/{name}/mapping")
    def apply_site_mapping(name: str, body: MappingIn) -> dict[str, Any]:
        """Apply a site mapping (local names -> concepts) from a YAML path or an inline dict."""
        from dea.standards.mapping import apply_mapping, load_mapping, load_units, mapping_report

        cohort = store.get(name)
        if body.path:
            p = Path(body.path)
            if not p.exists():
                raise HTTPException(404, f"{p} does not exist")
            mapping, units = load_mapping(p), load_units(p)
        else:
            mapping, units = body.mapping, body.units
        info = mapping_report(cohort, mapping)
        apply_mapping(cohort, mapping, units).save()
        store.invalidate(name)
        return info

    @app.post("/api/cohorts/{name}/advise/map")
    def advise_map(name: str, body: AdviseIn, where: Where = None) -> dict[str, Any]:
        """Ask the advisor for concepts for every variable the dictionary could not match."""
        from dea.advise import Advisor, AdvisorError
        from dea.suggest.vocab import load_dictionary

        cohort = _filtered(store.get(name), where)
        prof = profile_cohort(cohort)
        d = load_dictionary()
        unmatched = [
            {
                "name": v.name,
                "unit": cohort.config.variable_units.get(v.name, ""),
                "median": v.median,
                "p01_p99": [v.p01, v.p99],
                "median_interval": v.interval_label,
            }
            for v in prof.variables
            if d.match(v.name) is None
        ]
        unmatched += [
            {"name": v, "unit": cohort.config.variable_units.get(v, ""), "kind": "interval"}
            for v in cohort.interval_variables
            if d.match(v) is None
        ]
        if not unmatched:
            return {"proposals": [], "mapping": {}, "n_unmatched": 0}
        concepts = [
            {"name": c.name, "label": c.label, "unit": c.unit, "role": c.role} for c in d.concepts
        ]
        try:
            props = Advisor(
                model=body.model, base_url=body.base_url, api_key=body.api_key
            ).map_variables(unmatched, concepts)
        except AdvisorError as exc:
            raise HTTPException(502, str(exc)) from exc
        known = {c.name for c in d.concepts}
        clean = [p for p in props.proposals if not p.concept or p.concept in known]
        return {
            "proposals": [p.model_dump() for p in clean],
            "mapping": {p.variable: p.concept for p in clean if p.concept and p.confidence >= 0.6},
            "n_unmatched": len(unmatched),
        }

    @app.get("/api/cohorts/{name}/report", response_class=HTMLResponse)
    def report(name: str, where: Where = None) -> str:
        from dea.report import render_html

        full = store.get(name)
        children = []
        for info in list_cohorts(store.data_dir):
            cfg = store.get(info.path.name).config
            if cfg.parent and Path(cfg.parent).name == name and cfg.derived_by:
                children.append((info.name, info.n_encounters, cfg.derived_by))
        return render_html(_filtered(full, where), children=children)

    @app.get("/api/cohorts/{name}/export")
    def export(
        name: str, where: Where = None, every: str | None = None, fill: str | None = None
    ) -> FileResponse:
        cohort = _filtered(store.get(name), where)
        out = Path(tempfile.mkdtemp(prefix="dea-export-")) / f"{name}_wide.parquet"
        cohort.wide(every=every, fill=fill, with_static=True).write_parquet(out)
        return FileResponse(out, filename=out.name, media_type="application/octet-stream")

    def _dataset(
        name: str,
        where: Where,
        label: str,
        cutoff: float | None,
        split: str,
        test_size: float,
        seed: int,
    ):  # noqa: ANN202
        from dea.ml import DatasetSpec, make_dataset

        cohort = _filtered(store.get(name), where)
        try:
            return make_dataset(
                cohort,
                DatasetSpec(
                    label=label, cutoff_hours=cutoff, split=split, test_size=test_size, seed=seed
                ),
            )
        except FilterError as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.get("/api/cohorts/{name}/ml/summary")
    def ml_summary(
        name: str,
        label: str,
        where: Where = None,
        cutoff: float | None = None,
        split: str = "encounter",
        test_size: float = 0.2,
        seed: int = 0,
    ) -> dict[str, Any]:
        ds = _dataset(name, where, label, cutoff, split, test_size, seed)
        return {**ds.summary(), "features": ds.feature_names[:60]}

    @app.get("/api/cohorts/{name}/ml")
    def ml_export(
        name: str,
        label: str,
        where: Where = None,
        cutoff: float | None = None,
        split: str = "encounter",
        test_size: float = 0.2,
        seed: int = 0,
    ) -> FileResponse:
        ds = _dataset(name, where, label, cutoff, split, test_size, seed)
        out = Path(tempfile.mkdtemp(prefix="dea-ml-")) / f"{name}_dataset.parquet"
        ds.frame().write_parquet(out)
        return FileResponse(out, filename=out.name, media_type="application/octet-stream")

    @app.get("/api/cohorts/{name}/export/fhir")
    def export_fhir_bundle(name: str, where: Where = None) -> FileResponse:
        from dea.standards.fhir import export_fhir

        cohort = _filtered(store.get(name), where)
        tmp = Path(tempfile.mkdtemp(prefix="dea-fhir-"))
        export_fhir(cohort, tmp / name)
        archive = shutil.make_archive(str(tmp / f"{name}_fhir"), "zip", tmp, name)
        return FileResponse(archive, filename=Path(archive).name, media_type="application/zip")

    @app.get("/api/cohorts/{name}/entities")
    def entities(name: str, min_encounters: int = 5) -> dict[str, Any]:
        from dea.nlp import entity_summary

        cohort = store.get(name)
        ents = cohort.derived("entities")
        if ents is None:
            return {"available": False, "summary": []}
        return {
            "available": True,
            "n_entities": ents.height,
            "summary": _records(entity_summary(ents, len(cohort), min_encounters), 200),
        }

    app.state.update = None  # filled in by a background check, see below

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "data_dir": str(store.data_dir),
            "auth": bool(token),
            "version": __version__,
            "update": app.state.update,
        }

    if not os.environ.get("DEA_NO_UPDATE_CHECK"):
        import threading

        from dea.update import check

        def _check() -> None:
            try:
                app.state.update = check()
            except Exception:  # noqa: BLE001 (a failed check must never affect the app)
                app.state.update = None

        threading.Thread(target=_check, name="dea-update-check", daemon=True).start()

    # ---- frontend
    static_dir = find_frontend(frontend_dir)
    if static_dir is not None:
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            target = static_dir / path
            if path and target.is_file():
                return FileResponse(target)
            return FileResponse(static_dir / "index.html")

    else:

        @app.get("/", include_in_schema=False)
        def no_frontend() -> HTMLResponse:
            return HTMLResponse(NO_FRONTEND_HTML, status_code=503)

    return app


def find_frontend(frontend_dir: Path | str | None = None) -> Path | None:
    """The built web app, or ``None`` if there is none.

    Looked up in this order: the explicit argument, ``$DEA_FRONTEND_DIR``, the copy bundled
    into the installed package (``dea/static``) and ``frontend/dist`` of a source checkout.
    """
    candidates = [
        Path(frontend_dir) if frontend_dir else None,
        Path(os.environ["DEA_FRONTEND_DIR"]) if os.environ.get("DEA_FRONTEND_DIR") else None,
        Path(__file__).resolve().parents[1] / "static",
        Path(__file__).resolve().parents[3] / "frontend" / "dist",
    ]
    for c in candidates:
        if c is not None and (c / "index.html").is_file():
            return c
    return None


NO_FRONTEND_HTML = """<!doctype html><meta charset="utf-8"><title>DEA</title>
<body style="font-family:system-ui;max-width:40rem;margin:4rem auto;line-height:1.5">
<h1>The API is running, but the web app is not built.</h1>
<p>The React frontend is missing from this installation. From a source checkout, build it once:</p>
<pre>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</pre>
<p>then restart <code>dea serve</code>. Alternatively point <code>DEA_FRONTEND_DIR</code> at a
built copy. The API itself is available under <a href="/docs">/docs</a>.</p>
</body>"""
