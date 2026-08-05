"""FHIR bundle parsing.

Item 20c, pass 2b.

    parser   "07- FHIR Parser.py" whole — ``parse_fhir_bundle`` and the
             per-resource helpers behind it, plus the four corpus-wide Counters
             (``BIRTH_DATE_PRECISION_COUNTS``, ``DEMOGRAPHIC_SOURCE_COUNTS``,
             ``ECOG_VALUE_SHAPE_COUNTS``, ``ECOG_SELECTION_COUNTS``) that
             ``load_all_patients`` clears per call and reports.

``parser`` imports ``oncotriage.constants`` and ``oncotriage.utils``. It does
NOT import ``oncotriage.paths``: the only path File 07 ever named is
``data_fhir_path``, and it named it once, inside the ``__main__`` block. That
block is a script, so it stayed behind in the shim, and the parser reached the
package with no path dependency at all.

This ``__init__`` imports no submodule. ``import oncotriage.fhir`` stays free;
the caller names the module it wants.
"""

__all__ = ["parser"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
