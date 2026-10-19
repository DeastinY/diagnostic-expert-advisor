from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from pathlib import Path  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from dea.api import create_app  # noqa: E402
from dea.synth import generate_cohort  # noqa: E402


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    data = tmp_path_factory.mktemp("api")
    generate_cohort(n_encounters=6, seed=42, name="unit", max_hours=72).save(data / "unit")
    return TestClient(create_app(data_dir=data, frontend_dir=data / "no-frontend"))


def test_list_and_detail(client: TestClient) -> None:
    r = client.get("/api/cohorts")
    assert r.status_code == 200 and r.json()[0]["name"] == "unit"
    d = client.get("/api/cohorts/unit").json()
    assert d["summary"]["n_encounters"] == 6 and "HR" in d["variables"]
    assert d["event_variables"] == ["Note", "Ventilation_Mode"]
    assert client.get("/api/cohorts/nope").status_code == 404


def test_filters_count_and_bad_filter(client: TestClient) -> None:
    r = client.get("/api/cohorts/unit/count", params={"where": ["los_hours < 1000"]})
    assert r.json() == {"n": 6, "n_total": 6}
    r = client.get("/api/cohorts/unit/count", params={"where": ["nonsense > 1"]})
    assert r.status_code == 400


def test_catalog_rules_and_derive(client: TestClient) -> None:
    cat = {c["name"]: c for c in client.get("/api/cohorts/unit/catalog").json()}
    assert cat["age"]["kind"] == "numeric" and cat["HR__max"]["source"] == "aggregate"
    r = client.post(
        "/api/cohorts/unit/rules", json={"kind": "filter", "label": "Old", "where": "age >= 60"}
    )
    assert r.status_code == 201
    assert any(
        f["label"] == "Old" for f in client.get("/api/cohorts/unit").json()["config"]["filters"]
    )
    assert (
        client.post(
            "/api/cohorts/unit/rules", json={"kind": "tag", "label": "x", "where": "nope > 1"}
        ).status_code
        == 400
    )
    r = client.post(
        "/api/cohorts/unit/derive",
        json={"name": "old", "where": ["age >= 60"], "description": "elderly"},
    )
    assert r.status_code == 201
    names = [c["name"] for c in client.get("/api/cohorts").json()]
    assert "old" in names
    old = client.get("/api/cohorts/old").json()
    assert old["config"]["parent"].endswith("unit") and old["config"]["derived_by"] == "age >= 60"
    assert (
        client.post(
            "/api/cohorts/unit/derive", json={"name": "old", "where": ["age >= 60"]}
        ).status_code
        == 409
    )
    assert client.delete("/api/cohorts/unit/rules/filter/Old").status_code == 204
    assert client.delete("/api/cohorts/old").status_code == 204


def test_charts(client: TestClient) -> None:
    r = client.get(
        "/api/cohorts/unit/charts/overview",
        params={"static_column": "age", "variable": "HR", "by": "sex"},
    )
    body = r.json()
    assert {"los", "coverage_heatmap", "tags", "static", "variable"} <= set(body)
    assert body["coverage_heatmap"]["mark"]["type"] == "rect"
    assert body["variable"]["layer"][1]["mark"]["type"] == "boxplot"
    prof = client.get("/api/cohorts/unit/charts/profile", params={"dark": True}).json()
    assert prof["gallery"]["$schema"] and "observations" in prof
    assert body["los"]["$schema"].startswith("https://vega.github.io/schema/vega-lite")
    t = client.get(
        "/api/cohorts/unit/charts/trajectory", params={"variable": "HR", "every": "4h", "by": "sex"}
    ).json()
    assert t["rows"] > 0 and t["spec"]["$schema"]
    assert (
        client.get("/api/cohorts/unit/charts/trajectory", params={"variable": "nope"}).status_code
        == 404
    )


def test_encounters(client: TestClient) -> None:
    page = client.get(
        "/api/cohorts/unit/encounters", params={"size": 4, "sort": "los_hours", "desc": True}
    ).json()
    assert page["total"] == 6 and len(page["rows"]) == 4
    assert page["rows"][0]["los_hours"] >= page["rows"][1]["los_hours"]
    assert "Severe ARDS" in page["tags"]
    eid = page["rows"][0]["encounter_id"]
    e = client.get(f"/api/cohorts/unit/encounters/{eid}").json()
    assert e["id"] == eid and "HR" in e["variables"] and e["events"]
    spec = client.get(
        f"/api/cohorts/unit/encounters/{eid}/chart",
        params={"variables": ["HR", "MAP"], "every": "1h"},
    ).json()
    assert spec["spec"]["$schema"]
    wide = client.get(f"/api/cohorts/unit/encounters/{eid}/wide", params={"every": "4h"}).json()
    assert "hours" in wide["columns"] and wide["total"] == len(wide["rows"])
    assert client.get("/api/cohorts/unit/encounters/zzz").status_code == 404


def test_profile_suggestions_report(client: TestClient) -> None:
    prof = client.get("/api/cohorts/unit/profile").json()
    assert prof["n_encounters"] == 6 and prof["events"]
    sugg = client.get("/api/cohorts/unit/suggestions").json()
    ids = [s["id"] for s in sugg]
    assert "rename:HR" in ids
    r = client.post("/api/cohorts/unit/suggestions/apply", json={"ids": ["rename:HR"]})
    assert r.json()["applied"] == ["rename:HR"]
    assert "heart_rate" in client.get("/api/cohorts/unit").json()["variables"]
    html = client.get("/api/cohorts/unit/report").text
    assert "<title>unit · DEA report</title>" in html
    assert client.get("/api/cohorts/unit/entities").json()["available"] is False


def test_demo_and_processing(client: TestClient) -> None:
    r = client.post("/api/cohorts/demo", json={"name": "d1", "n": 4})
    assert r.status_code == 201
    r = client.put(
        "/api/cohorts/d1/processing",
        json={"resample_every": "1h", "fill": "forward", "aggregate": "mean"},
    )
    assert r.json()["resample_every"] == "1h"
    assert (
        client.post("/api/cohorts/demo", json={"name": "m1", "n": 3, "mimic": True}).status_code
        == 201
    )
    assert "Heart Rate" in client.get("/api/cohorts/m1").json()["variables"]
    assert client.get("/api/health").json()["status"] == "ok"


def test_advise_status_and_apply(client: TestClient) -> None:
    st = client.get("/api/cohorts/unit/advise").json()
    assert set(st) >= {"installed", "configured", "model", "payload"}
    assert "sampling" in st["payload"]
    rec = {
        "summary": "s",
        "resample_every": "30m",
        "aggregate": "mean",
        "default_fill": "forward",
        "variables": [
            {
                "variable": "MAP",
                "label": "Mean arterial pressure",
                "canonical_name": "map",
                "role": "vital_sign",
                "unit_guess": "mmHg",
                "imputation": "interpolate",
                "rationale": "r",
            }
        ],
        "suggested_filters": [{"label": "Elderly", "where": "age >= 80", "description": ""}],
        "plot_variables": ["MAP"],
        "storage_notes": [],
        "warnings": [],
    }
    r = client.post("/api/cohorts/unit/advise/apply", json={"recommendation": rec, "rename": True})
    assert r.status_code == 200 and "map" in r.json()["variables"]
    cfg = client.get("/api/cohorts/unit").json()["config"]
    assert cfg["processing"]["resample_every"] == "30m" and cfg["variable_map"]["MAP"] == "map"
    assert any(f["label"] == "Elderly" for f in cfg["filters"])


def test_sites_endpoint(client: TestClient) -> None:
    r = client.post("/api/cohorts/demo", json={"name": "multi", "n": 24, "sites": 3})
    assert r.status_code == 201
    r = client.get("/api/cohorts/multi/sites")
    body = r.json()
    assert body["column"] == "site" and body["configured"] == "site"
    assert body["charts"]["distributions"] and body["charts"]["coverage"]
    assert any(c["id"].startswith("site:unit:Temp") for c in body["checks"])
    assert {row["site"] for row in body["profile"]} == {"Hospital A", "Hospital B", "Hospital C"}
    r = client.put("/api/cohorts/multi/site", json={"column": "outcome"})
    assert r.json()["site_column"] == "outcome"
    assert client.get("/api/cohorts/multi/sites").json()["column"] == "outcome"
    assert client.put("/api/cohorts/multi/site", json={"column": "nope"}).status_code == 400


def test_result_cache_invalidates_on_save(client: TestClient) -> None:
    from pathlib import Path

    data_dir = Path(client.get("/api/health").json()["data_dir"])
    cache = data_dir / "unit" / "derived" / "cache"
    r1 = client.get("/api/cohorts/unit/profile").json()
    files = sorted(cache.glob("profile-*.json"))
    assert len(files) == 1
    r2 = client.get("/api/cohorts/unit/profile").json()
    assert r2 == r1 and sorted(cache.glob("profile-*.json")) == files
    client.get("/api/cohorts/unit/profile?where=los_hours+%3E+0")
    assert len(list(cache.glob("profile-*.json"))) == 2  # different params, different entry
    # a save changes the signature; the next call recomputes and drops the stale entries
    client.put("/api/cohorts/unit/processing", json={"resample_every": "30m"})
    client.get("/api/cohorts/unit/profile").json()
    names = [f.name for f in cache.glob("profile-*.json")]
    assert len(names) == 1 and names[0] not in {f.name for f in files}


def test_token_auth(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path_factory.mktemp("auth")
    generate_cohort(4, seed=1, max_hours=24).save(data / "c")
    monkeypatch.setenv("DEA_TOKEN", "s3cret")
    c = TestClient(create_app(data_dir=data, frontend_dir=data / "no-frontend"))
    assert c.get("/api/health").json()["auth"] is True
    assert c.get("/api/cohorts").status_code == 401
    assert c.get("/api/cohorts", headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert c.get("/api/cohorts", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/api/cohorts?token=s3cret").status_code == 200


def test_encounter_chart_band_and_report_sections(client: TestClient) -> None:
    eid = client.get("/api/cohorts/unit/encounters").json()["rows"][0]["encounter_id"]
    var = client.get("/api/cohorts/unit").json()["variables"][0]  # earlier tests may rename
    spec = client.get(f"/api/cohorts/unit/encounters/{eid}/chart?variables={var}").json()["spec"]
    panel = spec["vconcat"][-1]
    assert any(layer.get("mark", {}).get("type") == "area" for layer in panel["layer"])  # band
    spec = client.get(
        f"/api/cohorts/unit/encounters/{eid}/chart?variables={var}&band=false"
    ).json()["spec"]
    assert not any(
        layer.get("mark", {}).get("type") == "area" for layer in spec["vconcat"][-1]["layer"]
    )
    client.post("/api/cohorts/demo", json={"name": "multi2", "n": 18, "sites": 3})
    client.post(
        "/api/cohorts/multi2/derive", json={"name": "multi2_sub", "where": ["los_hours > 0"]}
    )
    html = client.get("/api/cohorts/multi2/report").text
    assert "Across sites" in html and "Data quality checks" in html and "Sub-cohorts" in html
    assert "multi2_sub" in html


def test_folder_upload_in_batches(client, tmp_path):
    """A dropped folder arrives as files with relative paths; the result is a detected source."""
    import io

    import polars as pl

    df = pl.DataFrame(
        {
            "encounter_id": ["a", "a", "b"],
            "time": [0, 60, 0],
            "variable": ["HR", "HR", "HR"],
            "value": [80.0, 82.0, 70.0],
        }
    )
    buf = io.BytesIO()
    df.write_csv(buf)
    up = client.post("/api/uploads")
    assert up.status_code == 201
    uid = up.json()["id"]
    r = client.post(
        f"/api/uploads/{uid}/files",
        files=[("files", ("obs.csv", buf.getvalue(), "text/csv"))],
        data={"paths": ["export/obs.csv"]},
    )
    assert r.status_code == 200 and r.json()["received"] == 1
    r = client.post(
        f"/api/uploads/{uid}/files",
        files=[("files", ("junk.txt", b"ignore me", "text/plain"))],
        data={"paths": ["export/../../escape.txt"]},
    )
    assert r.status_code == 200
    done = client.post(f"/api/uploads/{uid}/done").json()
    assert done["name"] == "export"
    assert done["n_files"] == 2
    staged = Path(done["staged"])  # the one table in the folder, not the folder
    assert staged.name == "obs.csv" and staged.parent.name == "export"
    assert (staged.parent / "escape.txt").exists()  # the escaping path stayed inside the upload
    assert done["detection"]["format"] == "table"
    assert done["detection"]["table_kind"] == "long"
    assert client.post("/api/uploads/nope/done").status_code == 404
    # the staged folder imports like any path
    r = client.post("/api/cohorts/import", json={"path": str(staged), "name": "dropped"})
    assert r.status_code == 201, r.text
    assert r.json()["report"]["n_encounters"] == 2
    assert not staged.exists()  # the staging folder is gone once the cohort is written


def test_preview_with_fixes_and_static_table(client, tmp_path):
    """Re-checking with fixes reuses the checked import; a static table joins in."""
    import polars as pl

    rows = []
    for i in range(30):
        enc = "ab"[i % 2]
        rows += [(enc, i, "HR", 80.0), (enc, i, "HR", 80.0)]
    pl.DataFrame(
        {
            "encounter_id": [r[0] for r in rows],
            "time": [r[1] for r in rows],
            "variable": [r[2] for r in rows],
            "value": [r[3] for r in rows],
        }
    ).write_csv(tmp_path / "obs.csv")
    pl.DataFrame({"encounter_id": ["a", "b"], "age": [61, 72], "site": ["X", "Y"]}).write_csv(
        tmp_path / "patients.csv"
    )
    body = {
        "path": str(tmp_path / "obs.csv"),
        "name": "pv",
        "static_path": str(tmp_path / "patients.csv"),
    }
    first = client.post("/api/cohorts/preview", json=body).json()["report"]
    assert first["summary"]["recommended"] == 1 and first["applied"] == []
    second = client.post("/api/cohorts/preview", json={**body, "fixes": ["duplicates"]})
    second = second.json()["report"]
    assert second["applied"] == ["duplicates"]
    assert not any(c["id"] == "duplicates" for c in second["checks"])
    assert second["n_observations"] == 30
    third = client.post("/api/cohorts/preview", json=body).json()["report"]  # unfixed one intact
    assert third["applied"] == [] and third["n_observations"] == 60
    done = client.post("/api/cohorts/import", json={**body, "fixes": ["duplicates"]})
    assert done.status_code == 201
    detail = client.get("/api/cohorts/pv").json()
    assert "age" in detail["static_columns"] and "site" in detail["static_columns"]
    bad = client.post("/api/cohorts/preview", json={**body, "static_path": "/nope.csv"})
    assert bad.status_code == 404


def test_jobs_hist_previous_and_checks_apply(client, tmp_path):
    """A background import reports its stage and ends with the same result as the sync
    route; a histogram comes with the concept range; the last import's choices are
    remembered; fixes can be applied to a saved cohort."""
    import time

    import polars as pl

    rows = []
    for i in range(40):
        enc = "abcd"[i % 4]
        rows += [(enc, i, "HR", 80.0 + i % 7), (enc, i, "HR", 80.0 + i % 7)]
    pl.DataFrame(
        {
            "encounter_id": [r[0] for r in rows],
            "time": [r[1] for r in rows],
            "variable": [r[2] for r in rows],
            "value": [r[3] for r in rows],
        }
    ).write_csv(tmp_path / "obs.csv")
    body = {"path": str(tmp_path / "obs.csv"), "name": "jobby"}
    job = client.post("/api/jobs/preview", json=body)
    assert job.status_code == 202
    jid = job.json()["id"]
    for _ in range(200):
        state = client.get(f"/api/jobs/{jid}").json()
        if state["status"] != "running":
            break
        time.sleep(0.05)
    assert state["status"] == "done", state
    assert state["result"]["previous"] is None
    assert {c["id"] for c in state["result"]["report"]["checks"]} >= {"duplicates"}
    assert client.get("/api/jobs/nope").status_code == 404

    hist = client.post("/api/cohorts/preview/hist", json={**body, "variable": "HR"}).json()
    assert hist["n"] == 80 and hist["range"] == [20, 250] and hist["bins"]
    assert hist["concept"] == "Heart rate"

    # import without the fix, declined is remembered; the next preview reports it
    client.post("/api/cohorts/import", json=body)
    prev = client.post("/api/cohorts/preview", json=body).json()["previous"]
    assert prev["applied"] == [] and prev["declined"] == ["duplicates"]
    assert prev["n_observations"] == 80 and prev["when"]

    # apply the fix on the saved cohort
    saved = client.get("/api/cohorts/jobby/hist", params={"variable": "HR"}).json()
    assert saved["n"] == 80
    r = client.post("/api/cohorts/jobby/checks/apply", json={"ids": ["duplicates"]})
    assert r.json()["applied"] == ["duplicates"]
    assert client.get("/api/cohorts/jobby/hist", params={"variable": "HR"}).json()["n"] == 40
    assert not any(c["id"] == "duplicates" for c in client.get("/api/cohorts/jobby/checks").json())


def test_hist_before_after_and_series(client: TestClient) -> None:
    variables = client.get("/api/cohorts/unit").json()["variables"]
    v = "HR" if "HR" in variables else "heart_rate"  # an earlier test may have renamed it
    base = client.get("/api/cohorts/unit/hist", params={"variable": v}).json()
    assert "after" not in base and base["n"] > 0
    h = client.get(
        "/api/cohorts/unit/hist", params={"variable": v, "where": "value > 100", "expr": "100"}
    ).json()
    after = h["after"]
    assert after["n"] == h["n"] and after["n_changed"] + after["n_unchanged"] == h["n"]
    assert after["n_changed"] > 0 and len(after["bins"]) == len(h["bins"])
    assert after["bins"][0]["lo"] <= after["bins"][-1]["hi"]  # its own window, 30 bins
    assert len(after["bins"]) == 30
    dropped = client.get(
        "/api/cohorts/unit/hist", params={"variable": v, "where": "value > 100"}
    ).json()
    assert dropped["after"]["n"] == h["n"] - after["n_changed"]

    encounters = client.get("/api/cohorts/unit/encounters").json()
    rows = (
        encounters
        if isinstance(encounters, list)
        else encounters.get("rows", encounters.get("encounters"))
    )
    eid = rows[0]["encounter_id"]
    s = client.get("/api/cohorts/unit/series", params={"variable": v, "encounter": eid}).json()
    assert s["encounter"] == eid and len(s["hours"]) == len(s["values"]) > 10
    assert s["hours"] == sorted(s["hours"])


def test_dashboard_default_panels_and_every_kind_draws(client: TestClient) -> None:
    import json

    d = client.get("/api/cohorts/unit/dashboard").json()
    assert d["default"] is True and {k["kind"] for k in d["catalog"]} >= {"los", "trajectory"}
    kinds = [p["kind"] for p in d["panels"]]
    assert kinds[:3] == ["los", "tags", "coverage"] or kinds[:3] == [
        "los",
        "observations",
        "coverage",
    ]
    assert "trajectory" in kinds and all(1 <= p["size"] <= 4 for p in d["panels"])
    for p in d["panels"]:
        r = client.get(
            "/api/cohorts/unit/panel", params={"kind": p["kind"], "params": json.dumps(p["params"])}
        )
        assert r.status_code == 200, (p, r.text)
        body = r.json()
        assert "spec" in body and (body["spec"] is not None or body["note"]), p
    # kinds the default may not include
    detail = client.get("/api/cohorts/unit").json()
    sc = detail["static_columns"]
    v = detail["variables"][0]
    for kind, params in (
        ("crosstab", {"column": sc[0], "by": sc[1 % len(sc)], "normalize": True}),
        ("variable", {"variable": v}),
        ("observations", {"n": 5}),
        ("gallery", {"n": 6}),
        ("sites", {}),
        ("site_coverage", {}),
        ("selection", {}),
    ):
        r = client.get(
            "/api/cohorts/unit/panel", params={"kind": kind, "params": json.dumps(params)}
        )
        assert r.status_code == 200, (kind, r.text)
    assert client.get("/api/cohorts/unit/panel", params={"kind": "nope"}).status_code == 400
    r = client.get(
        "/api/cohorts/unit/panel",
        params={"kind": "static", "params": json.dumps({"column": "zzz"})},
    )
    assert r.status_code == 400


def test_dashboard_is_saved_and_reset(client: TestClient) -> None:
    panels = [
        {"id": "a", "kind": "los", "params": {}, "size": 4, "title": "Stays"},
        {"id": "b", "kind": "observations", "params": {"n": 5}, "size": 2},
    ]
    r = client.put("/api/cohorts/unit/dashboard", json={"panels": panels})
    assert r.status_code == 200 and r.json()["default"] is False
    d = client.get("/api/cohorts/unit/dashboard").json()
    assert d["default"] is False and [p["id"] for p in d["panels"]] == ["a", "b"]
    assert d["panels"][0]["title"] == "Stays" and d["panels"][0]["size"] == 4
    assert client.get("/api/cohorts/unit").json()["config"]["dashboard"][0]["id"] == "a"
    bad = client.put("/api/cohorts/unit/dashboard", json={"panels": [{"id": "x", "kind": "nope"}]})
    assert bad.status_code == 400
    r = client.put("/api/cohorts/unit/dashboard", json={"panels": []})
    assert r.json()["default"] is True
    assert client.get("/api/cohorts/unit/dashboard").json()["default"] is True


def test_pre_admission_window_and_panel(client: TestClient) -> None:
    import json

    r = client.put("/api/cohorts/unit/processing", json={"pre_admission_lead_hours": 12})
    assert r.status_code == 200 and r.json()["pre_admission_lead_hours"] == 12
    r = client.put("/api/cohorts/unit/processing", json={"aggregate": "median"})
    assert r.json()["pre_admission_lead_hours"] == 12  # untouched when not sent
    assert (
        client.get("/api/cohorts/unit").json()["config"]["processing"]["pre_admission_lead_hours"]
        == 12
    )
    r = client.get(
        "/api/cohorts/unit/panel", params={"kind": "pre_admission", "params": json.dumps({})}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["spec"] is not None or body["note"]  # the synthetic cohort may start at admission


def test_detect_manual_file_tree_override(client: TestClient, tmp_path):
    import sys

    sys.path.insert(0, "tests")
    from test_importers import _patient_tree

    root = _patient_tree(tmp_path / "export")
    d = client.post("/api/detect", json={"path": str(root), "overrides": {}}).json()
    assert d["format"] == "file_tree" and d["n_files"] == 6 and d["preview"]
    assert d["static_file"] == "andere_variablen_patienten.csv" and "HF" in d["columns"]
    forced = client.post(
        "/api/detect", json={"path": str(root), "overrides": {"pattern": "dynamische_*.csv"}}
    ).json()
    assert (
        forced["format"] == "file_tree"
        and forced["pattern"] == "dynamische_variablen_patient_{id}.csv"
    )
    bad = client.post(
        "/api/detect", json={"path": str(root), "overrides": {"pattern": "nope_*.csv"}}
    )
    assert bad.status_code == 400
    r = client.post(
        "/api/cohorts/import", json={"path": str(root), "name": "tree", "overrides": {}}
    )
    assert r.status_code == 201, r.text
    assert r.json()["report"]["n_encounters"] == 7 and r.json()["report"]["empty_columns"] == [
        "Leer"
    ]
