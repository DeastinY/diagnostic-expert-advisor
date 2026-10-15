# Compute backends and HPC

`Cohort.apply(fn, backend=..., name=...)` runs a function over every encounter and
stores the collected result under `derived/<name>.parquet`.

```python
import dea
from dea.compute import LocalBackend, SlurmBackend

cohort = dea.load_cohort("data/my_cohort")

def my_job(encounter):
    wide = encounter.wide(every="1h", fill="forward")
    return {"hours_hypotensive": float((wide["MAP"] < 65).sum())}

cohort.apply(my_job, backend=LocalBackend(), name="hypotension")
```

Rules for `fn`: it receives an `Encounter`, returns a `dict` (one row), a Polars
`DataFrame` (many rows) or `None`, and must be an importable module-level function for the
process and SLURM backends (they pickle it). Scripts using `LocalBackend` need the usual
`if __name__ == "__main__":` guard.

## Backends

| Backend | Where it runs | Install |
|---|---|---|
| `SerialBackend` | this process | - |
| `LocalBackend(max_workers)` | local process pool | - |
| `SlurmBackend(folder, partition, account, mem_gb, cpus_per_task, timeout_min, chunk_size=200, ...)` | one SLURM array job via [submitit](https://github.com/facebookincubator/submitit); `chunk_size` encounters per array task | `dea[slurm]` |

The SLURM backend submits from the machine where DEA runs (it calls `sbatch`), so run DEA
on a login node or wherever `sbatch` works, with the cohort directory on a filesystem the
compute nodes can read. **It has not yet been tested against a real cluster**; the shape
is in place so that local and cluster execution share one call.
