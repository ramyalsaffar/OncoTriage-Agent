# Ablation Study
################

"""
Ablation Study — entry point.

Measures the contribution of each pipeline stage by running the full matching
pipeline with one stage disabled at a time on a stratified patient sample.

The study itself is ``oncotriage/ablation/study.py``. Item 20c pass 3d moved it
there; this file is a ``__main__`` block and the one import it needs.

Ablation configurations (7)
---------------------------
  1. full_pipeline       — all stages active (baseline)
  2. no_mesh_filter      — skip MeSH cancer site relevance filter
  3. no_stage_filter     — skip cancer stage mismatch filter
  4. no_histology_filter — skip histology mismatch filter
  5. no_cross_encoder    — skip MedCPT cross-encoder reranking
  6. bm25_only           — disable vector search (BM25 retrieval only)
  7. vector_only         — disable BM25 (vector retrieval only)

Each config runs on the SAME stratified patient sample; only one variable
changes per config. Flags ride in the LangGraph state as ``ablation_flags`` and
are read at three points (nodes 2, 3 and 4). Default is ``{}`` — all stages
active — so the production pipeline, the FastAPI server and the batch runner are
unaffected.

Output
------
  ablation_results.db     SQLite, in result_ablation_path, separate from the
                          production inferences.db so an ablation run never
                          reaches drift detection or the Reproducibility tab.
  ablation_summary.json   machine-readable summary for the paper figures.
  a console table with per-config averages and deltas against the baseline.

``--db PATH`` (pass 20f-1) writes the study to a different SQLite file, with
``ablation_summary.json`` beside it. Until that pass this was the last database
writer in the project whose path could not be overridden, which is why it was
also the only one with no isolation test;
``tests/test_ablation_db_isolation.py`` is that test. PASS 20f-3 CLOSED THE TWO
THINGS THAT PASS RECORDED AS FOLLOW-UPS: the CHECKPOINT follows ``--db`` now
(beside the database, named after it -- before, an isolated run read the
production resume file, skipped every pair a production run had done, wrote
nothing for them and still printed ``Status: COMPLETE``), and a ``--db`` whose
PARENT DIRECTORY is missing is refused by name instead of reaching sqlite3 and
coming back as "unable to open database file", which names neither the path nor
the flag. Both are argued in ``oncotriage/ablation/study.py``.

THIS COSTS MONEY. 7 configs × 75 patients = 525 live pipeline runs, each with a
Stage 5 call. Roughly $2.50–$4.00 and 3–5 hours at the default sample size. The
run is checkpointed per (config, patient), so an interrupted study resumes with
the same command and pays nothing for what it already did.

NO RE-EXPORT SHIM. Nothing in the repository chained this file or read a name out
of it: all 28 of its top-level names were grepped against every .py, .md, .toml
and .yml in the tree, and the only hits are File 27's own ``ABLATION_DB`` over
the same directory, a prose mention of ``ABLATION_CONFIGS`` in File 27's comment,
two prose mentions of ``log_ablation_result``, and the exec-bootstrap locals
(``_code_dir``, ``_bootstrap``, ``_fh``, ``_os_boot``) that every numbered file
shares.

OPERATOR CONTROLS (the operator-control pass). This file used to be a bare
``main()`` call: no lock, no stop switch, no SIGTERM disposition, a
``with ThreadPoolExecutor`` that DRAINED the rest of a configuration on any
exception, and a ``KeyboardInterrupt`` that was caught, not re-raised, and let
the study carry on to the NEXT configuration. All five are closed; the table
above the ``__main__`` guard is the summary and
``oncotriage/ablation/study.py`` carries the arguments.

Usage
-----
    python "26- Ablation Study.py"                   # full run (75 patients)
    python "26- Ablation Study.py" --sample-size 20  # quick test
    python "26- Ablation Study.py" --summary-only    # reprint from the database
    python "26- Ablation Study.py" --configs full_pipeline no_mesh_filter
    python "26- Ablation Study.py" --db /tmp/scratch/ablation_results.db
    python "26- Ablation Study.py" --clear-stop      # resume after a STOP
    touch <the sentinel the run banner names>        # STOP THIS STUDY CLEANLY
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "29- Download Qdrant
# Data.py". `pip install -e .` from 03- Code/ makes it a no-op.
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

from oncotriage.agent.evaluation import request_stage5_shutdown
from oncotriage.ablation.study import (
    AlreadyRunning,
    EXIT_LOCK_UNAVAILABLE,
    EXIT_LOCKED,
    LockUnavailable,
    exclusive_run_lock,
    lock_unavailable_lines,
    main,
    parse_args,
    run_lock_refusal_lines,
)
from oncotriage.observability import console


#------------------------------------------------------------------------------


# ===========================================================================
# MAIN
# ===========================================================================
#
# THREE WAYS TO STOP A STUDY, AND THEY ARE DIFFERENT REQUESTS -- the same three
# `25- Batch Runner.py` documents, with this file's own state and its own exit
# codes.
#
#   touch <checkpoint dir>/ablation_checkpoint_STOP
#       needs a shared filesystem, no terminal and no pid. No further
#       (config, patient) pair is STARTED, those in flight finish and are
#       written, the checkpoint stays current, the configuration in progress is
#       recorded STOPPED in ablation_runs.status, NO SUMMARY IS GENERATED and
#       the checkpoint is KEPT. Exit 0. The sentinel is NOT deleted by the
#       study that honoured it and the next study refuses to start while it is
#       there -- `--clear-stop` is the resume gesture.
#
#   Ctrl-C
#       needs a terminal. Stage 5 is asked to stop issuing requests, queued
#       pairs are cancelled, the configuration in progress is recorded KILLED,
#       the closing block prints and the study ENDS rather than continuing to
#       the next configuration. Exit 130.
#
#   SIGTERM (`docker stop`, systemd, a bare `kill`)
#       needs a pid. The same, through a SystemExit rather than a
#       KeyboardInterrupt. Exit 143.
#
# ONE STUDY AT A TIME, PER CHECKPOINT. The guard below takes an exclusive
# `flock` keyed on this study's own checkpoint FILE -- not its directory, which
# is where it diverges from the batch runner and why -- and holds it for the
# process's life. A second invocation against the same state exits 3 naming the
# holder's pid, host, user and start time, having touched nothing. See THE
# STUDY RUN LOCK in oncotriage/ablation/study.py.
#
# THE LOCK IS TAKEN HERE AND NOT IN main(), on `25- Batch Runner.py`'s
# precedent: `main()` is directly callable, and an embedder driving several
# studies in one process -- or a test driving two passes -- would be refusing
# itself. A process-wide exclusion belongs to the process's entry point.
#
# THE STALE-SENTINEL PREFLIGHT IS IN main() AND NOT HERE, which is the other
# divergence from `25- Batch Runner.py` and it is forced by where argparse
# lives. That file's guard owns its parser, so it can run the preflight above
# `--fresh`; this file's `main()` owns its own, so the preflight sits at the top
# of `main()` -- above `--fresh-start`, which is the ordering that matters. The
# guard calls `parse_args()` only to learn `--db`, which the lock key depends
# on; argparse is a pure function of argv, so parsing twice cannot disagree with
# itself, and a bad argv exits 2 here having touched nothing.
#
# Exit codes:
#     0    the study finished, or was stopped cleanly by the sentinel
#     1    a refusal -- an unknown config, a refused checkpoint, a stale
#          sentinel, a --clear-stop that could not remove the file
#     2    argparse rejected the command line
#     3    another study holds the lock
#     130  Ctrl-C   (128 + SIGINT)
#     143  SIGTERM  (128 + SIGTERM)

if __name__ == "__main__":
    import signal

    _EXIT_SIGTERM = 128 + int(signal.SIGTERM)
    _EXIT_SIGINT = 128 + int(signal.SIGINT)

    def _terminate_on_sigterm(_signum, _frame):
        """Turn SIGTERM into the SystemExit this study's handlers are written for.

        MEASURED FIRST, in `25- Batch Runner.py` and true here unchanged:
        Python's default SIGTERM disposition is SIG_DFL, so `docker stop`,
        systemd's stop and a bare `kill <pid>` ran NOTHING -- no handler, no
        exception, no `finally`. The process died at exit -15 with the
        configuration in progress left RUNNING in ablation_runs.status, the
        parent tracking run left open (which MLflow's atexit hook then records
        FINISHED -- a study that was killed, indexed as one that completed), and
        every in-flight Stage 5 request abandoned mid-read while still billed.

        IT IS INSTALLED HERE AND NOT IN main() because a signal disposition is
        PROCESS-WIDE: a library function that silently rebound its caller's
        SIGTERM would be reaching outside its own process boundary, and
        `signal.signal` refuses to run anywhere but the main thread of the main
        interpreter anyway.

        THE DISPOSITION IS RESET FIRST, AND THAT IS RE-ENTRANCY RATHER THAN
        TIDINESS. Everything the handlers then do -- finalizing the open
        configuration, printing the closing block, closing the tracking run --
        catches `Exception` and not `BaseException`, so a SECOND SIGTERM
        arriving during those few lines would raise SystemExit straight through
        them and leave the record half-written, which is the one thing this
        handler exists to produce. Restoring the default means a second signal
        terminates immediately: the operator asked twice.

        WHY SystemExit AND NOT KeyboardInterrupt. It carries its own exit code,
        so SIGTERM exits 143 and Ctrl-C exits 130 -- two different requests a
        supervisor can tell apart. It is a BaseException that is not an
        Exception and not a KeyboardInterrupt, so the study's own
        `except KeyboardInterrupt` leaves it alone and it propagates to
        `except BaseException`, which closes the tracking run FAILED and
        re-raises. And it exits with no traceback, which is what an
        orchestrator-requested shutdown should look like.

        ONE LINE OF RECORD BEFORE THE RAISE, AS A RAW `os.write` TO fd 2, for
        the three reasons `25- Batch Runner.py` records and of which only the
        first is theoretical: `print` and `console.out` both take a Python-level
        lock, and a signal handler re-entering a lock the main thread holds --
        which is exactly where a SIGTERM landing mid-progress-bar puts it --
        deadlocks the process on the path whose job is to work when things are
        going wrong; `os.write` is UNBUFFERED, and that file MEASURED `print`
        losing the line entirely because Python block-buffers stdout when it is
        not a tty; and fd 2 is the stream every line it explains goes to.
        """
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        # ── ASK STAGE 5 TO STOP ISSUING REQUESTS, BEFORE THE RAISE ────────
        #
        # THE RAISE ALONE DOES NOT REACH THE PIPELINE. CPython delivers a signal
        # to the MAIN thread; the pipeline runs on WORKER threads of the study's
        # pool. So the SystemExit lands in `future.result()`, the pool's
        # `finally` cancels QUEUED pairs, and every pair already in flight then
        # finishes its WHOLE Stage 5 exchange -- each request bounded only by
        # MATCHING_REQUEST_TIMEOUT_SECONDS and the SDK's own retries -- while
        # `shutdown(wait=True)` blocks. This one call is what turns that into
        # "one in-flight request per worker, then exit 143 with the open
        # configuration recorded KILLED".
        #
        # IT IS SIGNAL-SAFE, and that decided its implementation rather than its
        # placement: `request_stage5_shutdown` assigns two module globals and
        # takes no lock, for the same reason the `os.write` below is not a
        # `print`.
        request_stage5_shutdown(f"SIGTERM (signal {_signum})")
        os.write(2, (f"\n[SIGTERM] Termination requested (signal {_signum}). "
                     f"Cancelling queued pairs, finishing those in flight, and "
                     f"recording the open configuration as KILLED. Send it "
                     f"again to give up on the record.\n"
                     ).encode("utf-8", "replace"))
        raise SystemExit(_EXIT_SIGTERM)

    signal.signal(signal.SIGTERM, _terminate_on_sigterm)

    # ── THE RUN LOCK, BEFORE ANYTHING BELOW IT TOUCHES STATE ────────────────
    #
    # `parse_args()` IS CALLED HERE FOR ONE FIELD, AND main() PARSES AGAIN.
    # The lock key is this study's checkpoint file, which depends on `--db`, and
    # the lock has to be held before main() runs its preflight and its
    # `--fresh-start`. argparse is a pure function of argv -- no state, no side
    # effect beyond `--help` and a usage error, both of which exit -- so the
    # second parse cannot disagree with the first. The alternative was hoisting
    # the whole parser into this guard and changing `main()`'s signature, which
    # is a redesign of a file whose contract is that it takes no arguments.
    _db_path = parse_args().db

    try:
        with exclusive_run_lock(db_path=_db_path) as _lock_file:
            console.out(f"[Lock] Held for this study: {_lock_file}")
            try:
                main()
            except KeyboardInterrupt:
                # Ctrl-C EXITS 130 WITH NO TRACEBACK, AND THAT IS THIS GUARD'S
                # JOB RATHER THAN main()'s -- a disposition belongs to an entry
                # point, not to a callable an embedder may have its own shutdown
                # contract for.
                #
                # THE RECORD IS ALREADY WRITTEN BY THE TIME THIS RUNS. The
                # study's own handler finalized the open configuration KILLED
                # and printed the closing block, and `except BaseException`
                # closed the tracking run, before the exception reached here.
                # Left uncaught it would reach CPython's default handler, which
                # prints a traceback -- a report of a fault, for a shutdown the
                # operator asked for.
                #
                # 128 + SIGINT = 130, the shell convention. NO DISPOSITION IS
                # INSTALLED FOR SIGINT: `signal.signal` is never called for it,
                # so Ctrl-C keeps CPython's default and the KeyboardInterrupt
                # lands wherever the main thread is, rather than inside a
                # handler that might be holding a lock.
                console.out("\n[INTERRUPTED] Stopped by Ctrl-C. The open "
                            "configuration was recorded KILLED and the "
                            "checkpoint is intact -- run again to resume. For a "
                            "stop that records itself as STOPPED and needs no "
                            "terminal, touch the sentinel the run banner "
                            "names.")
                sys.exit(_EXIT_SIGINT)

    except AlreadyRunning as _held:
        # THE REFUSAL, ON THE SAME CHANNEL EVERYTHING ELSE THIS FILE SAYS GOES
        # TO. It names the holder's pid, host, user and start time so an
        # operator can act on it -- kill that process, or wait for it -- rather
        # than being told only that something is in the way.
        console.out()
        for _line in run_lock_refusal_lines(_held):
            console.out(_line)
        sys.exit(EXIT_LOCKED)

    except LockUnavailable as _lock_error:
        # THE LOCK COULD NOT BE ATTEMPTED, WHICH IS A DIFFERENT FINDING FROM
        # "another study holds it" AND EXITS DIFFERENTLY. Before this clause the
        # only outcome was an uncaught OSError traceback -- no diagnosis, no
        # path, no statement that nothing had been billed.
        #
        # `except LockUnavailable` AND NOT `except OSError`: main() runs INSIDE
        # the `with` above, so an OSError clause here would catch every OSError
        # a multi-hour study can raise and report it as a lock problem while
        # discarding the study's real diagnosis. The conversion happens at the
        # acquisition site, where the only OSError reachable is the lock's own.
        console.out()
        for _line in lock_unavailable_lines(_lock_error):
            console.out(_line)
        sys.exit(EXIT_LOCK_UNAVAILABLE)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 02 2026

@author: ramyalsaffar
"""
