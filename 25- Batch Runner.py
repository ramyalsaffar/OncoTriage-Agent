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

SIGTERM HAS A DISPOSITION (see the block above the __main__ guard). `docker
stop`, systemd and a bare `kill` used to run NOTHING here: no crash record, no
health flush, no finalized run row, and every in-flight billed request abandoned
unrecorded. It is now a SystemExit that main()'s own crash handler is already
written for -- crash blocks, a KILLED run row, exit 143 -- and queued patients
are cancelled rather than drained. SIGINT is untouched.

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
# AND ONE MORE THAT DOES NOT COME FROM THE RECONCILIATION AT ALL:
#
#   143  SIGTERM. Raised as SystemExit by the handler installed below, so
#        sys.exit(reconciliation_exit_code()) is never reached -- a shutdown the
#        operator asked for is not a statement about database completeness. 128
#        + SIGTERM, the shell convention. See the block above the guard.
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

# SIGTERM IS GIVEN A DISPOSITION, AND IT IS THIS GUARD'S JOB RATHER THAN main()'s
# ------------------------------------------------------------------------------
# MEASURED FIRST: Python's default SIGTERM disposition is SIG_DFL, so `docker
# stop`, `kubectl delete pod`, systemd's stop and a bare `kill <pid>` ran NOTHING
# here -- no handler, no exception, no `finally`, no `except BaseException`. The
# process died at exit -15 with: no crash record on the console, no health flush,
# no `runs` row finalized (left at RUNNING with a NULL finished_at, which is the
# shape reserved for a process that had no chance to run a handler), and every
# in-flight Stage 5 request abandoned mid-read while still billed, with no ledger
# row recording it. SIGTERM is the FIRST signal every orchestrator sends.
#
# IT IS INSTALLED HERE AND NOT IN main(), on --fresh's precedent and for the same
# reason, one level sharper: a signal disposition is PROCESS-WIDE. An embedder
# calling oncotriage.batch.runner.main() programmatically has its own shutdown
# contract, and a library function that silently rebound its caller's SIGTERM
# would be reaching outside its own process boundary. `signal.signal` also
# refuses to run anywhere but the main thread of the main interpreter, which is
# another reason it belongs to an entry point rather than to a callable.
#
# WHY SystemExit AND NOT KeyboardInterrupt -- MEASURED, AND IT OVERTURNS THE
# OBVIOUS CHOICE. `run_batch` and `run_resample` each carry their own
# `except KeyboardInterrupt` that SWALLOWS the interrupt: they shut the pool
# down, print "[INTERRUPTED] Checkpoint saved. Safe to resume." and then RETURN
# NORMALLY. So converting SIGTERM to KeyboardInterrupt would have been absorbed
# by the batch pass, the run would have carried on into the RESAMPLE pass at one
# billed call per patient, and it would have finalized FINISHED -- an
# interrupted campaign recorded as a completed one, which is the class of defect
# this project exists to remove. Not a hypothetical: it is what those two
# handlers do today, by construction.
#
# SystemExit is a BaseException that is NOT an Exception and NOT a
# KeyboardInterrupt, so:
#   * neither pool handler catches it -- it propagates;
#   * `main()`'s `except BaseException` catches it, and that handler's own
#     comment ALREADY names "a SystemExit" as one of its KILLED cases: the
#     health record is flushed, both crash blocks print, the `runs` row is
#     finalized KILLED, the tracking run is closed FAILED, and the exception is
#     re-raised unchanged;
#   * every `except Exception` on the way -- `_on_done`, `_issue` in Stage 5's
#     wave, `finalize_run_record` -- leaves it alone, so nothing swallows it;
#   * it exits the interpreter with its own code and NO TRACEBACK, which is what
#     an orchestrator-requested shutdown should look like.
#
# THE CODE IS 128 + SIGTERM = 143, the shell convention for "terminated by
# signal 15", so a supervisor reading the exit status sees what it asked for
# rather than this file's reconciliation verdict (0/1/2), which is a statement
# about database completeness and has nothing to say about a shutdown.
#
# QUEUED PATIENTS ARE CANCELLED RATHER THAN DRAINED, and that is a change in
# oncotriage/batch/runner.py rather than here -- see the note at run_batch's
# executor. Without it this handler would have been a COST REGRESSION: any
# exception-based interruption drained the entire remaining corpus at one live
# billed call each before the process could exit.
#
# SIGINT IS NOT TOUCHED. Not "left equivalent" -- not touched: no handler is
# installed for it, so Ctrl-C keeps CPython's default KeyboardInterrupt and the
# two pool handlers keep absorbing it exactly as before. The two signals now
# have DIFFERENT dispositions on purpose, because they are different requests: a
# human at a terminal saying "stop, I will resume" versus an orchestrator saying
# "you have N seconds before SIGKILL".
#
# WHAT IT CANNOT DO. SIGKILL is uncatchable, so a `docker stop` whose grace
# period expires still leaves the NULL-finished_at shape -- correctly, and that
# is what that shape is for. A request already in flight is not interruptible
# either; `wait=True` bounds the delay at one patient's remaining work.

if __name__ == "__main__":
    import argparse
    import signal

    _EXIT_SIGTERM = 128 + int(signal.SIGTERM)

    def _terminate_on_sigterm(_signum, _frame):
        """Turn SIGTERM into the SystemExit main()'s crash handler is written for.

        Runs on the main thread by definition (CPython delivers signals there),
        so the raise lands wherever the main thread is -- inside
        `future.result()` during a batch pass -- and propagates through the
        pool's `finally`, which cancels what has not started.

        THE DISPOSITION IS RESET FIRST, AND THAT IS RE-ENTRANCY RATHER THAN
        TIDINESS. Everything the crash handler then does -- the health flush,
        both crash blocks, the KILLED row -- catches ``Exception`` and not
        ``BaseException``, so a SECOND SIGTERM arriving during those few lines
        would raise SystemExit straight through them and leave the record
        half-written, which is the one thing this handler exists to produce.
        Restoring the default means a second signal terminates immediately: the
        operator asked twice and gets what they asked for, and the first
        record's loss is their decision rather than an accident. ``SIG_IGN``
        was the alternative and is worse -- it would make the process look
        unresponsive to an orchestrator that is about to SIGKILL it anyway.

        ONE LINE OF RECORD BEFORE THE RAISE, because the crash blocks below it
        say WHAT was lost and nothing else would say WHY. IT IS A RAW
        ``os.write`` TO fd 2, and there are three reasons, of which only the
        first is theoretical:

          1. ``print`` and ``console.out`` both take a Python-level lock, and a
             signal handler that re-enters a lock the main thread is already
             holding -- which is exactly where a SIGTERM landing mid-progress-bar
             puts it -- DEADLOCKS the process on the path whose whole job is to
             work when things are going wrong;
          2. ``os.write`` IS UNBUFFERED, and the alternative was MEASURED to
             lose the line. Reverting this call to ``print`` in a copy and
             driving a real SIGTERM produced a run whose log contained ZERO
             occurrences of this message at the moment the crash record was
             being written -- Python block-buffers stdout when it is not a tty,
             so the line sat in the buffer and only appeared at interpreter
             exit. A record that arrives after the thing it explains, or not at
             all if the process is then SIGKILLed, is not a record;
          3. fd 2 is the stream every line it explains is written to, so no
             redirection can separate the cause from its consequences.

        The cost is that it bypasses the bar-aware writer and may land
        mid-redraw; that is cosmetic, once, at shutdown.
        """
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.write(2, (f"\n[SIGTERM] Termination requested (signal {_signum}). "
                     f"Cancelling queued patients, finishing those in flight, "
                     f"and recording the run as KILLED. Send it again to give "
                     f"up on the record.\n").encode("utf-8", "replace"))
        raise SystemExit(_EXIT_SIGTERM)

    signal.signal(signal.SIGTERM, _terminate_on_sigterm)

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
