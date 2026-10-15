"""Headless smoke tests for every Streamlit page (requires dea[ui])."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

PAGES = Path(__file__).resolve().parents[1] / "src" / "dea" / "ui" / "pages"


def _run(page: str, cohort_dir: Path | None, **state) -> AppTest:
    at = AppTest.from_file(str(PAGES / page), default_timeout=120)
    if cohort_dir is not None:
        at.session_state["data_dir"] = cohort_dir.parent
        at.session_state["cohort_path"] = str(cohort_dir)
    for k, v in state.items():
        at.session_state[k] = v
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_home_lists_cohorts(cohort_dir: Path) -> None:
    at = _run("home.py", None, data_dir=cohort_dir.parent)
    assert any("unit" in m.value for m in at.markdown)
    assert at.button(key=f"open-{cohort_dir}") is not None


def test_pages_redirect_without_cohort() -> None:
    for page in ("overview.py", "encounters.py", "profile.py", "compute.py"):
        at = _run(page, None)
        assert any("Select or import a cohort" in i.value for i in at.info), page


def test_overview(cohort_dir: Path) -> None:
    at = _run("overview.py", cohort_dir)
    assert at.title[0].value == "unit"
    assert at.metric[0].label == "Encounters" and at.metric[0].value == "6"
    assert len(at.get("vega_lite_chart")) >= 3


def test_overview_with_filter(cohort_dir: Path) -> None:
    at = _run("overview.py", cohort_dir, active_filters=["Short stay"])
    assert any("Short stay" in i.value for i in at.info)


def test_encounters(cohort_dir: Path) -> None:
    at = _run("encounters.py", cohort_dir)
    assert at.title[0].value == "Encounters"
    assert any(s.value.startswith("Encounter ICU-") for s in at.subheader)
    assert len(at.get("vega_lite_chart")) == 1


def test_profile(cohort_dir: Path) -> None:
    at = _run("profile.py", cohort_dir)
    labels = [m.label for m in at.metric]
    assert "Suggested grid" in labels
    assert any("Bed_Number" in w.value for w in at.warning)
    assert at.session_state["profile"].n_encounters == 6


def test_compute(cohort_dir: Path) -> None:
    at = _run("compute.py", cohort_dir)
    assert at.title[0].value == "Compute"
    at.button[0].click().run()
    assert not at.exception
    assert any("rows in" in s.value for s in at.success)
