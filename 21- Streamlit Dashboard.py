# Streamlit Monitoring Dashboard
################################

"""
Monitoring Dashboard

Real-time monitoring and analytics for the clinical trial matching pipeline.
Visualizes performance metrics, costs, and match quality from SQLite logs.

THIN ENTRY POINT (item 20c, pass 3c-1)
--------------------------------------
The three cached loaders, the sidebar, the tier vocabulary, all nine tabs and
``main`` moved to ``oncotriage/dashboard/``. What is left here is the bootstrap
and the call.

WHY THIS FILE HAD TO STOP EXEC-CHAINING, and it is not only tidiness.
STREAMLIT RE-RUNS THE WHOLE SCRIPT ON EVERY INTERACTION -- every button, every
filter, every tab click -- and ``exec_chain`` caches nothing: it opens and
``exec()``s each file on every call. So every interaction re-read and
re-executed "01- Imports.py" and "02- Utility Functions.py" and re-chained
"03- Config.py". Because "03- Config.py" calls the client factories at shim
load, every interaction also CONSTRUCTED AN OPENAI CLIENT AND A QDRANT CLIENT
-- for a dashboard that uses neither, and never did. Python's module cache means
each ``oncotriage.dashboard`` module body now runs exactly once per process.

THAT IS A SEMANTIC CHANGE AND IT WAS MEASURED, NOT ASSUMED. Module-level
mutable state now persists across reruns instead of being rebuilt. The
dashboard has exactly two such objects -- ``MATCH_TIERS`` and
``MATCH_TIER_COLORS`` -- and neither is mutated anywhere, the
``tier_colors = MATCH_TIER_COLORS`` alias in three tabs is never written
through, and plotly leaves the dict it is handed unchanged. Check 6a of
"tests/test_package_invariants.py" re-derives all three, so an edit that starts
mutating either one fails instead of corrupting every subsequent rerun.

THE 60-SECOND CACHE TTL IS UNAFFECTED, which is worth stating because
``@st.cache_data`` used to be re-applied on every rerun and is now applied once
at import. Streamlit keys a cached function on
``md5(__module__, __qualname__, source)`` -- read out of
``streamlit/runtime/caching/cache_utils.py:_make_function_key``, not assumed --
and NOT on the function object's identity. So the cache already survived reruns
before this pass, and moving the loaders into a module only changes the
``__module__`` component: a different key, still stable, still 60 seconds. The
sidebar's Refresh button calls ``st.cache_data.clear()``, which empties every
entry in the cache regardless of which module defined the function.

Run from terminal:
    cd ".../03- Code"
    streamlit run "21- Streamlit Dashboard.py"

``docker-compose.yml`` line 108 runs that exact command and it is unchanged.

NO RE-EXPORT SHIM. Nothing in the repository reads this file's namespace:
every top-level name it bound -- the three loaders, ``render_sidebar``, the
nine ``render_*_tab`` functions, ``MATCH_TIERS``, ``MATCH_TIER_COLORS``, the
four ``TRIAL_STATUS_*`` labels, ``classify_trial_score``,
``enrich_match_tiers`` and ``main`` -- was grepped against every .py, .md,
.toml and .yml in the tree, and every hit is inside this file itself, prose in
CLAUDE.md / "Exception and Fallback Audit.md", or the ``streamlit run`` command
above. Nothing chains it either.
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "17- FastAPI Server.py" and "11- RAG Trial Indexer.py".
# `pip install -e .` makes it a no-op; without it the code directory is added to
# sys.path and the fact is printed rather than left silent. Docker takes this
# path too: the image copies the code directory to /app, which is also
# streamlit's working directory.
#
# __file__ IS BOUND HERE. Streamlit's script runner compiles the file with its
# real path, so the __file__ branch is the one taken under `streamlit run`; the
# cwd fallback covers a bare interactive paste, and announces itself.
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

from oncotriage.dashboard.app import main


# ===========================================================================
# MAIN
# ===========================================================================

# `streamlit run` executes this file with __name__ == "__main__", so the guard
# fires exactly as it did before the split. It is kept rather than replaced by a
# bare main() call so that reading the file still does nothing.
if __name__ == "__main__":
    main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Feb 15 2026

@author: ramyalsaffar
"""
