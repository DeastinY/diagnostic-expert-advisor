from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
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
