# Explore Synthea Patient Data
################################

"""
Descriptive analysis of the cancer patient dataset.
Generates statistics, distributions, and visualizations
Uses CSV files from Synthea export + our filtered JSON patient IDs

Cancer detection and cancer-stage extraction are NOT implemented in this file.
They are delegated to CancerCodeRegistry (File 08) and extract_patient_stage()
(File 10) — the same code paths File 05 uses to decide which patients stay on
disk and File 13 uses at query time. A dataset table produced here therefore
describes the same cohort the results tables describe.

THIN ENTRY POINT (item 20c, pass 3a)
------------------------------------
Every definition moved to ``oncotriage/fhir/explore.py``. What is left is the
``__main__`` block and the import it needs.

No exec() bootstrap and no re-export shim: nothing in the repository chains this
file. Every top-level name it defined was grepped against every .py, .md, .toml
and .yml in the tree; the only collisions are unrelated same-named locals in
other files (``main``, ``OUTPUT_DIR``, ``_CANCER_REGISTRY``), each of which those
files define for themselves.

Running it does exactly what it did: ``main()`` runs the full exploration and
writes its plots and summary report into the FHIR-exploration results directory.
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py". `pip install -e .` makes it
# a no-op; without it the code directory is added to sys.path and the fact is
# printed rather than left silent.
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

from oncotriage.fhir.explore import main


#------------------------------------------------------------------------------


if __name__ == "__main__":
    main()


#------------------------------------------------------------------------------


"""
Created on Wed Feb 11 12:34:51 2026

@author: ramyalsaffar
"""
