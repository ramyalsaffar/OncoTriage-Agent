# Empty the SQLite Database
###########################

"""
Empty the inference database, preserving the tables.

THIN ENTRY POINT (item 20c, pass 3b)
------------------------------------
``empty_database`` moved to ``oncotriage/storage/maintenance.py``. What is left
here is the safety switch, the ``__main__`` guard and the one call -- which is
exactly what was here before, minus the function body.

NO EXEC BOOTSTRAP, and no re-export shim. Nothing in the repository reads this
file's namespace: both top-level names it bound (``Flag``, ``empty_database``)
were grepped against every .py, .md, .toml and .yml in the tree and the only hit
is a prose mention of ``Flag`` in CLAUDE.md. It used to exec "01- Imports.py"
purely to obtain two free names, ``inferences_path`` and ``sqlite3``; both now
come from an import. The consequence is that reading this file no longer pulls
in torch, transformers, streamlit and langgraph, which is a strange price to
have been paying for a file whose job is one DELETE loop.

THREE THINGS ARE UNCHANGED ON PURPOSE, and this file is the wrong place to tidy
any of them:

    Flag = False        the safety default. One line to arm, at module level,
                        where a reader opening this file finds it.
    empty_database(...) takes BOTH the path and the flag EXPLICITLY. It has no
                        default for either -- see the module docstring of
                        oncotriage/storage/maintenance.py for why `db_path=None`
                        meaning "production" would be a defect rather than a
                        convenience.
    the __main__ guard  the function runs from here and nowhere else.

Run from terminal:
    python "15- Database Empty.py"
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "11- RAG Trial
# Indexer.py". `pip install -e .` makes it a no-op; without it the code
# directory is added to sys.path and the fact is printed rather than left
# silent. Same three candidates, in the same order, as
# _ensure_oncotriage_importable() in "01- Imports.py".
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

# `from oncotriage import paths`, not `from oncotriage.paths import
# inferences_path`. The second form is an ATTRIBUTE READ, so it fires the lazy
# resolver and globs the sibling data tree at import; the module form defers
# that to the read inside __main__ below. On a file whose only job is to delete
# rows, resolving a data tree in order to be imported is work nobody asked for.
from oncotriage import paths
from oncotriage.storage.maintenance import empty_database


#------------------------------------------------------------------------------


# Empty the SQLite database
# The default is False, change to True to empty the SQLite database
Flag = False


if __name__ == "__main__":
    empty_database(paths.inferences_path, Flag)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 22:02:04 2026

@author: ramyalsaffar
"""
