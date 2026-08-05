# Full-Scale Batch Runner
#########################

"""
Direct Batch Pipeline Runner

Runs the full matching pipeline on all FHIR patients directly in Python
without HTTP overhead. Faster and more reliable than FastAPI for bulk
evaluation runs.

THIN ENTRY POINT (item 20c, pass 3b)
------------------------------------
Every definition moved to ``oncotriage/batch/runner.py``. What is left here is a
``__main__`` guard and one call.

NO EXEC BOOTSTRAP, and no re-export shim. Nothing in the repository reads this
file's namespace -- every top-level name it bound was grepped against every .py,
.md, .toml and .yml in the tree, and the hits are all coincidental same-named
locals elsewhere (``graph``, ``bm25_index``, ``nct_ids``, ``fhir_files``,
``print_summary`` -- File 12's is a different function of the same name in
``oncotriage.retrieval.index_validator``).

THE MONKEYPATCH THIS FILE CARRIED IS GONE, and that is the substantive change of
the pass rather than a side effect of the move. Lines 65-73 used to rebind
``log_inference`` to a lock-wrapped copy IN THIS FILE'S NAMESPACE. It protected
this file and nothing else, and the project's only other concurrent writer --
``17- FastAPI Server.py``, which calls ``log_inference`` from the event loop's
thread pool once per in-flight request -- had no lock at all. The lock now lives
in ``oncotriage/storage/database_logger.py``, beside the writes it protects, so
both callers get it. See that module for what the unserialized race cost.

Run from terminal:
    cd ".../03- Code"
    python "25- Batch Runner.py"
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "11- RAG Trial
# Indexer.py". `pip install -e .` makes it a no-op; without it the code
# directory is added to sys.path and the fact is printed rather than left silent.
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

from oncotriage.batch.runner import main


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == "__main__":
    main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""
