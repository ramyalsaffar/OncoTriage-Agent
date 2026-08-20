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

from oncotriage.batch.runner import (
    clear_checkpoint,
    main,
    reconciliation_exit_code,
)


# ===========================================================================
# MAIN
# ===========================================================================
#
# THE EXIT CODE IS A CONTRACT CHANGE (the write-durability pass), stated as one
# on File 19's precedent. This file used to call main() and fall off the end,
# so it exited 0 whatever happened -- including a run that lost inference rows
# and printed "Run complete."
#
#   0  every inference this run produced is in the database
#   1  rows were lost; the reconciliation block above names them
#   2  main() never reached the reconciliation at all
#
# There is no caller reading this file's exit code today -- the filename appears
# only in prose -- which is what makes the change cheap now and expensive later.
#
# main() STILL RETURNS results_list and its return type is untouched; the
# verdict is read off the module. See last_reconciliation() for why.

# --fresh IS THE ONE FLAG, AND IT LIVES IN THIS GUARD ON PURPOSE
# ---------------------------------------------------------------
# The configuration-fingerprint pass made load_checkpoint() REFUSE a checkpoint
# it cannot vouch for -- one written by a different prompt version, model,
# collection or snapshot date, one written before fingerprinting existed, or
# one that will not parse. A refusal deletes nothing, which is the contract,
# and it therefore needs a way to say "yes, discard it".
#
# THE FLAG IS HERE AND NOT ON main(). runner.main() takes no arguments and its
# own docstring pins the fact ("THE RETURN TYPE IS UNCHANGED, deliberately");
# embedders call it programmatically, and a main() that started reading
# sys.argv would SystemExit(2) inside somebody else's process the first time
# their flags did not match ours. `05- FHIR Clean Data.py` puts --dry-run in
# its guard for the same reason and CLAUDE.md records it: argparse inside the
# __main__ block, so no name leaks and no library function grows a CLI.
#
# TWO CONTRACT CHANGES, BOTH STATED:
#   * a BARE invocation is unchanged -- fresh defaults to False, main() is
#     called with nothing, and the exit code is still the reconciliation's;
#   * an UNRECOGNISED argument now exits 2 with a usage message. It used to be
#     ignored, because nothing here read sys.argv at all -- so a mistyped flag
#     silently started a full-corpus billed run.

if __name__ == "__main__":
    import argparse

    _parser = argparse.ArgumentParser(
        description="Run the full matching pipeline over every FHIR patient. "
                    "COSTS MONEY: one live Stage 5 call per patient.")
    _parser.add_argument(
        "--fresh", action="store_true",
        help="Delete the checkpoint before running, discarding all resume "
             "state so every patient runs again. This is the remediation "
             "load_checkpoint() names when it refuses a checkpoint written by "
             "a different configuration -- and it re-bills the whole cohort, "
             "which is why it is a flag rather than a fallback.")
    _args = _parser.parse_args()

    if _args.fresh:
        # Announced before it happens, not after: this is a destructive,
        # expensive request and the operator should see the file named while
        # there is still time to interrupt. clear_checkpoint() prints
        # "[Checkpoint] Cleared." itself, or nothing when there was none.
        print("[--fresh] Discarding the batch checkpoint. Every patient will "
              "run again, at one live Stage 5 call each.")
        clear_checkpoint()

    main()
    sys.exit(reconciliation_exit_code())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""
