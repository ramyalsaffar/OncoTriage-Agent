# Cohort Selector Diff — Read Only
##################################

"""
Cohort Selector Diff — READ ONLY. Entry point.

RENAMED IN PASS 20e, from "34- Cohort Selector Diff.py". The single most
important fact about this file is that it NEVER DELETES, MOVES OR REWRITES A
PATIENT BUNDLE, and the old name did not say so. It sits in a numbered sequence
next to "05- FHIR Clean Data.py", which unlinks bundles in place, and it runs
that file's cohort selector over the same directory -- so a reader scanning
filenames had every reason to expect it to act on what it finds. The docstring
said "READ ONLY" on its second line; a filename is what is read first, and in a
terminal history it is all that is read. The number stays 34 so that every note
and document naming File 34 still resolves; see "PIPELINE SEQUENCE.md".

Runs both cohort selectors over the patient bundles currently on disk and
records where they disagree. The diff itself is
``oncotriage/evaluation/cohort_diff.py``; item 20c pass 3d moved it there.

  LEGACY  — has_cancer_diagnosis() as File 05 shipped it: read
            Condition.code.coding[0] by array position, build a {code, display}
            dict with no "codings" key, and hand that to
            CancerCodeRegistry.is_primary_cancer(). Because "codings" is absent,
            is_primary_cancer() takes its backward-compatible single-code path:
            system_key "unknown", one code examined, and none of the
            multi-coding exclusion logic runs.

  CURRENT — ``oncotriage.fhir.clean.has_cancer_diagnosis`` as it is today:
            coding selection through ``_select_best_coding("condition")``, with
            the full annotated coding list passed under "codings" so
            is_primary_cancer() checks every coding.

THE CURRENT SELECTOR IS NOT RE-IMPLEMENTED. The module imports the live function
object out of ``oncotriage.fhir.clean``, so this measures the code that will
actually build the cohort. The LEGACY arm keeps its OWN
``_LEGACY_EXCLUDE_VERIFICATION`` set, because the pre-fix File 05 defined a copy
that overwrote File 08's under the exec chain — consolidating it would make the
legacy arm agree with the current arm wherever the two sets differ, which is
the disagreement this file exists to find.

This script NEVER deletes, moves or rewrites a patient bundle. It opens bundles
read-only and writes two report files under the FHIR Exploration results
directory.

One-sided by construction: the directory holds the survivors of a previous
LEGACY run, so every file present was LEGACY-positive. The measurable
disagreement is therefore "LEGACY kept it, CURRENT would not". Patients the
LEGACY selector deleted are gone and cannot be re-tested; whether CURRENT would
have kept any of them is answerable only by regenerating from Synthea. That
limit is restated in the report.

NO RE-EXPORT SHIM. All 15 of this file's top-level names were grepped against
every .py, .md, .toml and .yml in the tree; the only hits are the exec-bootstrap
locals every numbered file shares.

Run from terminal (or F5 in Spyder):
    python "34- Cohort Selector Diff Read Only.py"

Exit codes:
    0 -- diff completed (agreement or disagreement; both are results)
    1 -- no bundles found, or the report could not be written
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "29- Download Qdrant
# Data.py". `pip install -e .` from 03- Code/ makes it a no-op.
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

from oncotriage.evaluation.cohort_diff import main


#------------------------------------------------------------------------------


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  2 12:00:00 2026

@author: ramyalsaffar
"""
