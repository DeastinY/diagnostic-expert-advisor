"""Scan gzipped CSV files lazily by converting them once, in bounded blocks, to Parquet.

Polars can read a ``.csv.gz`` only into memory, which rules out full MIMIC-IV
(``chartevents.csv.gz`` is 330 million rows). :func:`scan_compressed_csv` streams the
file through ``gzip`` in blocks of a few hundred megabytes, parses each block with the
schema of the first, writes it as one Parquet part into a cache directory, and returns a
lazy scan over the parts. The cache is keyed on the file's size and modification time, so a
second import of the same table is free. The cache lives next to the data when that is
writable, else under ``$DEA_CACHE_DIR`` or the system temp directory.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

import polars as pl

from dea.io import progress

BLOCK_BYTES = 256 * 1024 * 1024


def cache_dir_for(path: Path) -> Path:
    """Where converted parts for ``path`` go; created on first use."""
    key = hashlib.sha1(
        f"{path.resolve()}|{path.stat().st_size}|{int(path.stat().st_mtime)}".encode()
    ).hexdigest()[:16]
    candidates = [Path(os.environ["DEA_CACHE_DIR"])] if os.environ.get("DEA_CACHE_DIR") else []
    candidates += [path.parent / ".dea_cache", Path(tempfile.gettempdir()) / "dea_cache"]
    for base in candidates:
        try:
            target = base / f"{path.name}.{key}"
            target.mkdir(parents=True, exist_ok=True)
            return target
        except OSError:
            continue
    raise OSError("no writable cache directory for compressed CSV conversion")


def _read_block(block: bytes, header: bytes, schema: dict[str, Any] | None, **kwargs: Any):
    """Parse one block (header prepended); later blocks reuse the first block's schema."""
    if schema is not None:
        kwargs = {**kwargs, "schema_overrides": schema, "infer_schema_length": 0}
    return pl.read_csv(header + block, **kwargs)


def scan_compressed_csv(
    path: Path | str, block_bytes: int = BLOCK_BYTES, **read_kwargs: Any
) -> pl.LazyFrame:
    """Lazy frame over a ``.csv.gz`` file, converted to Parquet parts the first time."""
    path = Path(path)
    cache = cache_dir_for(path)
    done = cache / "_complete"
    if not done.exists():
        for old in cache.glob("part-*.parquet"):
            old.unlink()
        schema: dict[str, Any] | None = None
        header = b""
        with gzip.open(path, "rb") as fh:
            header = fh.readline()
            carry = b""
            i = 0
            while True:
                chunk = fh.read(block_bytes)
                if not chunk and not carry:
                    break
                block = carry + chunk
                if chunk:
                    cut = block.rfind(b"\n")
                    if cut < 0:  # a line longer than a block: read on
                        carry = block
                        continue
                    block, carry = block[: cut + 1], block[cut + 1 :]
                else:
                    carry = b""
                if not block.strip():
                    continue
                frame = _read_block(block, header, schema, **read_kwargs)
                if schema is None:
                    schema = dict(frame.schema)
                frame.write_parquet(cache / f"part-{i:05d}.parquet")
                progress.report("converting", f"{Path(path).name}, part {i + 1}")
                i += 1
        done.write_text(str(i))
    parts = sorted(cache.glob("part-*.parquet"))
    if not parts:
        return pl.read_csv(path, **read_kwargs).lazy()
    return pl.scan_parquet([str(p) for p in parts])


__all__ = ["scan_compressed_csv", "cache_dir_for", "BLOCK_BYTES"]
