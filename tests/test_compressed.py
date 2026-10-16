from __future__ import annotations

import gzip
from pathlib import Path

import polars as pl

from dea.io.compressed import cache_dir_for, scan_compressed_csv


def test_scan_compressed_csv_in_blocks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("DEA_CACHE_DIR", str(tmp_path / "cache"))
    frame = pl.DataFrame(
        {
            "id": range(5000),
            "when": [f"2150-01-01 00:{i % 60:02d}:00" for i in range(5000)],
            "value": [i * 0.5 for i in range(5000)],
            "text": ["a,b" if i % 7 == 0 else "plain" for i in range(5000)],
        }
    )
    gz = tmp_path / "events.csv.gz"
    with gzip.open(gz, "wb") as fh:
        fh.write(frame.write_csv().encode())
    lf = scan_compressed_csv(gz, block_bytes=20_000, try_parse_dates=True)
    out = lf.collect()
    assert out.height == 5000 and out["id"].to_list() == list(range(5000))
    assert out.schema["when"] == pl.Datetime("us") and out["value"].sum() == frame["value"].sum()
    assert (
        out.filter(pl.col("text") == "a,b").height == frame.filter(pl.col("text") == "a,b").height
    )
    parts = list(cache_dir_for(gz).glob("part-*.parquet"))
    assert len(parts) > 3  # really converted in several blocks
    # second scan is served from the cache
    again = scan_compressed_csv(gz, block_bytes=20_000, try_parse_dates=True).collect()
    assert again.equals(out)


def test_mimic_import_through_converter(tmp_path: Path, monkeypatch) -> None:
    import dea.io.mimic as mimic
    from dea.io import import_path
    from dea.synth import write_mimic_demo

    monkeypatch.setenv("DEA_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(mimic, "SMALL_GZ_BYTES", 0)  # force the block converter
    root = write_mimic_demo(tmp_path / "mimic", n_stays=4, seed=7)
    cohort, report = import_path(root, name="m")
    assert report.n_encounters == 4 and "Heart Rate" in cohort.variables
    assert any((tmp_path / "cache").glob("chartevents.csv.gz.*/part-00000.parquet"))
