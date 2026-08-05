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

`mesh` does NOT import `mesh_crosswalk_build`. That is the point of the split:
a process that wants the filter should not also carry code that opens a 1.5 GB
UMLS release.

This ``__init__`` imports no submodule. ``import oncotriage.registries`` stays
free; the caller names the module it wants, and a caller that wants the filter
must not pay for the ICD-10-CM release the cancer registry loads on first use.
"""

__all__ = ["cancer_code_registry", "mesh", "mesh_crosswalk_build"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
