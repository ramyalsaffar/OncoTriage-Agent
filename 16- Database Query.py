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

WHAT DID NOT CHANGE
-------------------
NOT ONE CHARACTER OF SQL. The query bodies in the package were extracted from
this file BY AST -- read as string constants and emitted verbatim, never
retyped. Item 38 owns this file's two broken queries and has not been done, so
BOTH ARE STILL BROKEN AND THE RUN STILL DIES AT THE SAME ONE:

    Query 19 (expansion_token_efficiency) selects expansion_input_tokens and
             expansion_output_tokens, which are not columns of `inferences`. It
             raises "no such column" and takes the process with it.
    Query 20 (pipeline_consistency) has a stray WHEN outside its CASE and is a
             syntax error. It has never run, because Query 19 kills the process
             first.

The acceptance criterion for this pass was that the output before and after the
move is identical UP TO AND INCLUDING that failure, at that query, with that
message. Fixing either one here would have made that comparison meaningless.

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
