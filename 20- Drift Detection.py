# Drift Detection for OncoMatch Agent
#####################################

"""
Monitors data drift, retrieval drift, and performance drift in the clinical trial
matching pipeline. Uses statistical tests (KS, PSI, z-score) to detect distribution
shifts and performance degradation.

RE-EXPORT SHIM (item 20c, pass 3b)
----------------------------------
Every definition moved to ``oncotriage/monitoring/drift.py``. This file
re-exports the fifteen names File 20 bound and keeps its ``__main__`` block.

IT KEEPS A SHIM BECAUSE ONE FILE CHAINS IT.
``tests/test_monitoring_ecog_availability_drift.py`` line 80 exec-chains this file and then
reads ``ecog_unavailable_rate``, ``detect_data_availability``,
``log_drift_metrics``, ``print_drift_details``, ``run_drift_detection``,
``z_score_drift``, ``ks_test_drift``, ``calculate_psi`` and
``ECOG_UNAVAILABLE_RATE_THRESHOLD`` out of the shared namespace with no import
of its own. Files 15, 16, 17 and 25 have no such consumer and became thin entry
points; this one did not.

`python "20- Drift Detection.py"` WORKS NOW, AND IT NEVER DID BEFORE.
File 20 contained ZERO import statements. Not "few" -- zero. It reached for
numpy, pandas, sqlite3, datetime, timezone, Tuple, Dict, traceback, ks_2samp,
inferences_path and eight config constants, and every one of them resolved only
because some OTHER file had exec'd "01- Imports.py" and "03- Config.py" into the
namespace first. Run directly, it died on ``PSI_BINS`` at the first ``def``
statement -- while the ``__main__`` block below told the user to run exactly
that command, and "21- Streamlit Dashboard.py" line 3609 told them the same.
Both instructions are true for the first time.

THE OTHER THREE CHANGES are argued in full in the module's docstring:
``log_drift_metrics`` and ``get_baseline_and_current_data`` take ``db_path``
(File 41 rebound the global instead, which a module function cannot see -- the
last writer in the repository that did); ``log_drift_metrics`` returns the path
it wrote to, so an isolation test can assert on it; and ``SCIPY_AVAILABLE`` is a
real ``ImportError`` guard rather than a ``NameError`` guard on somebody else's
namespace.

Run from terminal:
    python "20- Drift Detection.py"
"""


# Make the oncotriage package importable
#---------------------------------------
# THIS FILE IS STILL EXEC'D, unlike Files 15, 16, 17 and 25:
# "tests/test_monitoring_ecog_availability_drift.py" exec-chains it INTO an already-populated
# namespace, and the names re-exported below have to land in the CALLER's globals
# -- which only an exec'd file can arrange. Under exec_chain __name__ is
# "_exec_chain_", so the __main__ block at the bottom does not fire.
#
# But it does NOT exec "01- Imports.py" and "02- Utility Functions.py", and it
# never did -- File 20 had no bootstrap of any kind, which is exactly why it
# could not be run directly. The six-line block below is what Files 04, 06, 11,
# 12, 15, 16 and 17 carry: import the package, falling back to putting this
# directory on sys.path and PRINTING that it did. `pip install -e .` makes it a
# no-op, and when File 41 chains this file the import already succeeds because
# File 41 exec'd 01 first, so the whole block is a no-op there too.
#
# Deliberately NOT the 01/02 exec bootstrap. Running drift detection would
# otherwise import torch, transformers, streamlit, matplotlib and langgraph, and
# build an OpenAI and a Qdrant client, in order to run three statistical tests
# over a SQLite table. Same three candidates, in the same order, as
# _ensure_oncotriage_importable() in "01- Imports.py".
import os
import sys

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


# The re-exports
#---------------
# Explicit, never `import *`. A star import over a module whose surface changes
# would silently change what this file puts into the shared exec namespace, and
# the shared namespace is precisely what File 41 reads.
#
# This is the full runtime surface File 20 bound: fifteen names, captured by
# exec'ing the file into a throwaway namespace with its free names pre-seeded
# and recording every binding, before the move.
#
# TWO NAMES ARE ADDED, and both are stated here rather than left to be noticed,
# because a name a shim adds is a name the next file in a chain silently
# inherits. File 41 is the only chainer; it defines neither of these itself and
# now reads both:
#
#   main                   holds the body of the __main__ block below, which is
#                          what makes that block three lines rather than a copy
#                          of the module's error handling.
#   resolve_drift_db_path  the resolver behind the new db_path argument. File 41
#                          calls it to establish that the DEFAULT is the
#                          production database and is not its own scratch file,
#                          which is what makes "it wrote where I told it to" a
#                          discriminating assertion rather than a tautology.
from oncotriage.monitoring.drift import (
    ECOG_UNAVAILABLE_DIAGNOSIS,
    SCIPY_AVAILABLE,
    calculate_psi,
    detect_data_availability,
    detect_data_drift,
    detect_performance_drift,
    detect_retrieval_drift,
    ecog_unavailable_rate,
    get_baseline_and_current_data,
    ks_test_drift,
    log_drift_metrics,
    main,
    print_drift_details,
    resolve_drift_db_path,
    run_drift_detection,
    z_score_drift,
)


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

    main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 16 21:09:14 2026

@author: ramyalsaffar
"""
