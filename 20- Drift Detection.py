# Drift Detection for OncoMatch Agent
#####################################

"""
Monitors data drift, retrieval drift and performance drift in the clinical trial
matching pipeline. Uses statistical tests (KS, PSI, z-score) to detect
distribution shifts and performance degradation.

THIN ENTRY POINT (item 20c pass 3b moved the logic; pass 20e removed the shim)
------------------------------------------------------------------------------
Every definition lives in ``oncotriage/monitoring/drift.py``. What is left here
is a ``__main__`` guard and one call to ``main()``.

WHY THE SIXTEEN-NAME SHIM WENT. It existed for one consumer:
``41- ECOG Availability Metric Test.py`` exec-chained this file and read nine
names out of the shared exec namespace. Pass 20d-1 moved that file to
``tests/test_monitoring_ecog_availability_drift.py``, which imports
``oncotriage.monitoring.drift`` directly -- so from that pass onward NOTHING in
the repository chained File 20. What kept the shim alive for one more pass was
the check that tested it: pass 20d-1 rewrote section 8b to exec this file into a
THROWAWAY namespace and assert the sixteen names arrived, because comparing the
test's own imported globals against the package would have been true by
construction. That is a check whose only subject is the shim, which is a
circular reason to keep one. Section 8b retired with the shim in pass 20e and
was replaced by a check on what this file actually is now: a guard that calls
``drift.main`` and re-exports nothing.

`python "20- Drift Detection.py"` WORKS, AND BEFORE PASS 3b IT NEVER DID.
File 20 contained ZERO import statements. Not "few" -- zero. It reached for
numpy, pandas, sqlite3, datetime, timezone, Tuple, Dict, traceback, ks_2samp,
inferences_path and eight config constants, and every one of them resolved only
because some OTHER file had exec'd "01- Imports.py" and "03- Config.py" into the
namespace first. Run directly, it died on ``PSI_BINS`` at the first ``def``
statement -- while the ``__main__`` block below told the user to run exactly
that command, and the dashboard's drift tab told them the same.

THREE THINGS THE MODULE'S DOCSTRING ARGUES IN FULL: ``log_drift_metrics`` and
``get_baseline_and_current_data`` take ``db_path`` (File 41 rebound the global
instead, which a module function cannot see -- it was the last writer in the
repository that did); ``log_drift_metrics`` returns the path it wrote to, so an
isolation test can assert on it; and ``SCIPY_AVAILABLE`` is a real
``ImportError`` guard rather than a ``NameError`` guard on somebody else's
namespace.

DELIBERATELY NOT THE 01/02 EXEC BOOTSTRAP, and it never was. Running drift
detection must not import torch, transformers, streamlit, matplotlib and
langgraph, and must not build an OpenAI and a Qdrant client, in order to run
three statistical tests over a SQLite table.

Run from terminal:
    python "20- Drift Detection.py"
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# The same six-line block Files 04, 06, 11, 12, 15, 16 and 17 carry: import the
# package, falling back to putting this directory on sys.path and PRINTING that
# it did. `pip install -e .` makes it a no-op. Same three candidates, in the
# same order, as the bootstrap that used to live in "01- Imports.py".
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


#------------------------------------------------------------------------------


# ===========================================================================
# COMMAND-LINE EXECUTION
# ===========================================================================

if __name__ == "__main__":
    """
    Run drift detection when script is executed directly.

    Usage:
        python "20- Drift Detection.py"

    This will:
        1. Load last 30 days as baseline
        2. Load last 7 days as comparison
        3. Detect drift across all categories
        4. Log results to drift_metrics table
        5. Print detailed analysis

    THIS COMMAND WORKS NOW. It did not before item 20c pass 3b, for the reason
    written at the top of this file: File 20 had no imports and resolved only
    inside somebody else's exec namespace. The instruction above was wrong for
    as long as it has been written down.
    """

    # Imported inside the guard, not at module scope. oncotriage.monitoring.drift
    # imports oncotriage.paths and oncotriage.config; neither resolves a
    # directory at import (pass 20c-2b made paths lazy), but this file's whole
    # remaining job is one call, and a module-scope import would be a name in
    # this namespace that nothing but the call reads -- which after pass 20e is
    # the one thing this file is not allowed to have.
    from oncotriage.monitoring.drift import main

    main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 21:09:14 2026

@author: ramyalsaffar
"""
