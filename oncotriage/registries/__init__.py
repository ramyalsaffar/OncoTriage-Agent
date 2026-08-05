"""Clinical terminology registries.

Item 20c, pass 2a.

    cancer_code_registry   "08- Cancer Code Registry.py" — primary-cancer
                           detection over SNOMED / ICD-10-CM / display terms,
                           plus the LOINC oncology lab filter.
    mesh                   the runtime half of "09- MeSH Cancer Site Relevance
                           Filter.py" — MeSH C04 ancestry matching.
    mesh_crosswalk_build   the offline half of File 09 — the five builders that
                           parse desc2026.xml and MRCONSO and write the JSON
                           lookups `mesh` reads back. Called from File 09's
                           __main__ block and nowhere else.
    primary_cancer         (pass 2c) _resolve_primary_cancer — which of a
                           patient's conditions is THE cancer. It consumes
                           cancer_code_registry through load_registry(); it is
                           a separate module because both the agent's terminal
                           nodes and the storage logger call it, and because
                           cancer_code_registry's source text is read verbatim
                           by Files 42 and 43 and should stay a code table.

`mesh` does NOT import `mesh_crosswalk_build`. That is the point of the split:
a process that wants the filter should not also carry code that opens a 1.5 GB
UMLS release.

This ``__init__`` imports no submodule. ``import oncotriage.registries`` stays
free; the caller names the module it wants, and a caller that wants the filter
must not pay for the ICD-10-CM release the cancer registry loads on first use.
"""

__all__ = ["cancer_code_registry", "mesh", "mesh_crosswalk_build",
           "primary_cancer"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
