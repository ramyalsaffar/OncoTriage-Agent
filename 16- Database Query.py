# Query the database
####################

"""
Run every read-only query in the project against inferences.db and print the
results.

THIN ENTRY POINT (item 20c, pass 3b)
------------------------------------
Every query moved to ``oncotriage/storage/queries.py``, where it is a record in
an ordered registry rather than a top-level statement. What is left here is a
``__main__`` guard and one call.

THIS FILE GAINED A ``__main__`` GUARD, AND THAT IS A BEHAVIOUR CHANGE. STATED
PLAINLY: File 16 had ZERO guards, so loading it -- by any means, including a
future import, a Spyder runfile, or an editor's "run selection" -- opened the
production database and executed all forty queries as a side effect. A module
that runs forty queries when you look at it is not importable in any useful
sense, and that is precisely why nothing has ever been able to reuse a line of
it. Now:

    python "16- Database Query.py"        runs the sweep, exactly as before
    from oncotriage.storage import queries   runs nothing

THE RUN COMPLETES NOW (item 38)
-------------------------------
It used not to. Pass 3b moved the SQL without altering one character of it, so
this file's two broken queries survived the move intact and the sweep died part
way through, exactly as it always had:

    Query 19 (expansion_token_efficiency) selected expansion_input_tokens and
             expansion_output_tokens, which are not columns of `inferences` and
             never were. It raised "no such column" and took the process with
             it, so NO QUERY AFTER IT HAD EVER RUN, in any invocation of this
             file, ever.
    Query 20 (pipeline_consistency) had a stray WHEN between its column list
             and its CASE, which is a syntax error. It had never run either.

Item 38 deleted Query 19 rather than repairing it -- Stage 1 is rule-based and
calls no LLM, so there are no expansion tokens to count and
`expansion_stage_stats` already asks the answerable version of the question --
and repaired Query 20, whose two hardcoded pipeline sizes now resolve from
`oncotriage/config.py`. The arguments for both are in
`oncotriage/storage/queries.py`, beside the code. Two custom renderers that
raised on an empty or partly-NULL table were fixed with them.

"tests/test_storage_query_layer.py" runs every query in the registry against a
seeded temporary database and then runs the whole report end to end, which is
the first time either has been possible.

NO EXEC BOOTSTRAP, and no re-export shim. Nothing in the repository reads this
file's namespace -- every top-level name it bound was grepped against every .py,
.md, .toml and .yml in the tree, and the hits are all coincidental same-named
locals in other files (`conn`, `cursor`, `_in`, `_out`, `df_cost` in
"21- Streamlit Dashboard.py") plus one prose mention in "02- Utility
Functions.py" line 46 noting that this file documents ``load_env_keys`` as one
of its five free names. Those five -- inferences_path, pd, sqlite3,
get_model_cost, PRICING_CONFIG -- are now imports inside the package module.

Run from terminal:
    python "16- Database Query.py"
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

from oncotriage.storage.queries import report_to_stdout


#------------------------------------------------------------------------------


if __name__ == "__main__":
    report_to_stdout()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 20:51:26 2026

@author: ramyalsaffar
"""
