"""From a cohort to a modelling-ready table: label, prediction cutoff, split, leakage guard."""

from dea.ml.dataset import Dataset, DatasetSpec, make_dataset

__all__ = ["Dataset", "DatasetSpec", "make_dataset"]
