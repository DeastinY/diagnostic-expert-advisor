"""Clinical coding standards: ICD-9/ICD-10 diagnosis groups, LOINC/UCUM via the dictionary, FHIR.

The dictionary (:mod:`dea.suggest.vocab`) carries a LOINC and UCUM code per concept, so
measurements harmonize through it. Diagnoses harmonize through :mod:`dea.standards.icd`,
which maps both ICD-9-CM and ICD-10(-CM) codes onto chapters and clinical groups. FHIR R4
bundles and bulk exports are read and written by :mod:`dea.standards.fhir`.
"""

from dea.standards.icd import (
    DIAGNOSIS_GROUPS,
    IcdCode,
    chapter,
    diagnosis_table,
    groups_for,
    parse_icd,
)

__all__ = ["DIAGNOSIS_GROUPS", "IcdCode", "chapter", "diagnosis_table", "groups_for", "parse_icd"]
