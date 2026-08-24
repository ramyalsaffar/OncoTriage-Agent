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
are cancelled rather than drained.

THREE WAYS TO STOP A RUN, AND THEY ARE DIFFERENT REQUESTS
---------------------------------------------------------
    touch <checkpoint dir>/STOP   the OPERATOR STOP SWITCH. Finishes the
                                  patients already in flight, cancels the queue,
                                  skips the resample pass, records the run
                                  STOPPED and exits 0. Needs no terminal, no pid
                                  and no signal, so it works under nohup,
                                  systemd, a container or cron. The run's own
                                  setup banner prints the absolute path.
                                  Resume with --clear-stop.
    Ctrl-C                        an interrupt. Same teardown, but the run is
                                  recorded KILLED and the exit code is 130.
                                  Needs a terminal.
    SIGTERM                       an orchestrator saying "you have N seconds".
                                  Recorded KILLED, exit 143, no traceback.

Ctrl-C USED TO BE ABSORBED, and that is fixed in oncotriage/batch/runner.py
rather than here: both pool handlers swallowed the KeyboardInterrupt and
RETURNED NORMALLY, so an interrupted batch pass printed "Checkpoint saved. Safe
to resume." and then ran the whole RESAMPLE pass at one live billed call per
patient before finalizing the run FINISHED. They re-raise now. This file's job
is only to turn the resulting KeyboardInterrupt into exit 130 without a
traceback -- the SIGINT half of what the SIGTERM handler already does.

ONE RUN AT A TIME, PER CHECKPOINT DIRECTORY (the pre-migration pass). The
guard below takes an exclusive `flock` keyed on the checkpoint directory and
holds it for the process's life; a second invocation against the same directory
exits 3 naming the holder's pid, host, user and start time, having touched
nothing. Two concurrent runs both read the same resume state at start and both
paid for the SAME patients -- measured, and silent. See THE RUN LOCK in
oncotriage/batch/runner.py.

THE SENTINEL PREFLIGHT RUNS ABOVE --fresh (the pre-migration pass). It used to
live only inside main(), which is called after --fresh has already deleted the
checkpoint -- so `--fresh` with a stale sentinel present deleted the resume
state and THEN refused, printing "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN
BILLED" over a cohort the next run would re-bill in full. `--clear-stop`
satisfies the preflight rather than being blocked by it: it is the resume
gesture the refusal itself names.

Run from terminal:
    cd ".../03- Code"
    python "25- Batch Runner.py"
    python "25- Batch Runner.py" --clear-stop      # resume after a STOP
    python "25- Batch Runner.py" --fresh           # discard resume state (COSTS)

Exit codes:
    0    every inference this run produced is in the database
    1    rows were lost, or a stale stop sentinel refused the run
    2    main() never reached the reconciliation
    3    another batch run holds the lock for this checkpoint directory
    130  Ctrl-C
    143  SIGTERM
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

from oncotriage.agent.evaluation import request_stage5_shutdown
from oncotriage.batch.runner import (
    AlreadyRunning,
    EXIT_LOCKED,
    STOP_CLEAR_ABSENT,
    STOP_CLEAR_FAILED,
    StaleStopSwitch,
    assert_no_stale_stop_switch,
    clear_checkpoint,
    clear_stop_switch,
    describe_stop_switch_path,
    exclusive_run_lock,
    main,
    reconciliation_exit_code,
    run_lock_refusal_lines,
    stop_switch_path,
)
from oncotriage.observability import console


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
#   130  Ctrl-C. 128 + SIGINT, same convention and the same reasoning, caught at
#        the bottom of this guard so an operator-requested stop is not reported
#        as a crash with a traceback. NEW: before the pool handlers re-raised,
#        a Ctrl-C could not produce any exit code of its own at all.
#
# A RUN STOPPED BY THE `STOP` SENTINEL EXITS 0 AND HAS NO CODE OF ITS OWN, which
# is deliberate. A stop is a clean end, so it falls through to the
# reconciliation verdict -- which is 0 when every row this run produced is in
# the database, and that is the whole meaning of "exits 0" here. The one case
# where a stopped run does NOT exit 0 is a run that genuinely LOST rows, and
# that must not be silenced by a shutdown the operator asked for: those two
# findings are independent and the exit code reports the one this file's table
# is about.
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
# WHY SystemExit AND NOT KeyboardInterrupt. THE ORIGINAL ARGUMENT HAS EXPIRED
# AND IS KEPT AS THE RECORD OF WHY THIS IS A SystemExit; THE REASON IT STAYS ONE
# IS BELOW IT.
#
# WHAT IT SAID, and it was measured and true at the time: `run_batch` and
# `run_resample` each carried an `except KeyboardInterrupt` that SWALLOWED the
# interrupt -- they shut the pool down, printed "[INTERRUPTED] Checkpoint saved.
# Safe to resume." and RETURNED NORMALLY. So converting SIGTERM to
# KeyboardInterrupt would have been absorbed by the batch pass, the run would
# have carried on into the RESAMPLE pass at one billed call per patient, and it
# would have finalized FINISHED.
#
# THAT DEFECT IS FIXED IN THE POOL HANDLERS THEMSELVES (the stop-switch pass):
# both re-raise now, so a KeyboardInterrupt DOES reach main()'s crash handler.
# The premise above is therefore no longer true of this tree, and this comment
# says so rather than standing as a stale justification -- which is exactly the
# shape a reader would otherwise trust.
#
# IT STAYS A SystemExit FOR THREE REASONS THAT ARE STILL TRUE, none of which is
# the old one:
#
#   1. THE EXIT CODE. SystemExit carries its own, so SIGTERM exits 143 and
#      Ctrl-C exits 130 -- the shell convention for each, and two different
#      requests that a supervisor can tell apart. A SIGTERM converted to
#      KeyboardInterrupt would exit 130 and claim to be a Ctrl-C.
#   2. THE RE-ENTRANCY RESET. This handler restores SIG_DFL on entry so a
#      second SIGTERM terminates immediately rather than raising through the
#      half-written crash record. A KeyboardInterrupt path has no handler to
#      reset, deliberately (see SIGINT below).
#   3. NO TRACEBACK, WITHOUT A CATCH. SystemExit exits silently on its own;
#      making Ctrl-C do the same needed the explicit `except KeyboardInterrupt`
#      at the bottom of this guard.
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
# NO DISPOSITION IS INSTALLED FOR SIGINT, AND THAT IS STILL TRUE AND STILL
# DELIBERATE -- but "the pool handlers keep absorbing it", which this note used
# to say, IS NOT. They re-raise (the stop-switch pass), so Ctrl-C now stops the
# run and is recorded KILLED, exactly as SIGTERM is.
#
# WHAT REMAINS UNTOUCHED IS THE DISPOSITION: `signal.signal` is never called for
# SIGINT, so Ctrl-C keeps CPython's default and the KeyboardInterrupt lands
# wherever the main thread is, rather than inside a handler that might be
# holding a lock. The `try/except KeyboardInterrupt` at the bottom of this guard
# runs AFTER main()'s crash handler has already written the record, so it
# changes only what is printed and what is exited with. That distinction is
# pinned by tests/test_runner_sigterm_shutdown.py check 1d, which asserts BOTH
# that no signal.signal call targets SIGINT AND that exactly one
# `except KeyboardInterrupt` exists in this guard.
#
# THE THREE REQUESTS STILL DIFFER, and the surviving differences are the exit
# code and the disposition: a human at a terminal saying "stop, I will resume"
# (130), an orchestrator saying "you have N seconds before SIGKILL" (143), and
# an operator writing a file to say "stop cleanly and record it as STOPPED" (0)
# -- which needs no signal at all and is the only one of the three that is not
# a crash.
#
# WHAT A SECOND Ctrl-C DOES, stated because it is a real residual: SIGTERM's
# handler resets its own disposition so the second signal terminates outright,
# and SIGINT has no handler to do that. A second Ctrl-C arriving DURING main()'s
# crash handler therefore raises through it and can leave the record
# half-written -- the same outcome SIGTERM's note describes for its own second
# signal, arrived at by a different route. Closing it would mean installing a
# SIGINT disposition, which is the one thing this block is about not doing.
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
        # ── ASK STAGE 5 TO STOP ISSUING REQUESTS, BEFORE THE RAISE ────────
        #
        # THE RAISE ALONE DOES NOT REACH THE WAVE, and that is measurable
        # rather than arguable. CPython delivers a signal to the MAIN thread;
        # in a batch run the Stage 5 node executes on a WORKER thread of the
        # pool. So the SystemExit lands in `future.result()` here, the pool's
        # `finally` cancels QUEUED PATIENTS, and every patient already in
        # flight then finishes its WHOLE per-trial wave -- ceil(N / parallel)
        # rounds of live billed requests, each bounded only by
        # MATCHING_REQUEST_TIMEOUT_SECONDS and the SDK's own retries -- while
        # `shutdown(wait=True)` blocks. The wave's own `cancel_futures=True` is
        # correct and, from a real signal in a batch run, unreachable: it is in
        # the node's `finally`, on the worker thread, which nothing has
        # interrupted.
        #
        # That is minutes, against a `docker stop` grace period whose default
        # is TEN SECONDS -- so the orchestrator SIGKILLs partway through and
        # the run leaves NO crash record, NO finalized row and a set of
        # in-flight requests billed and abandoned mid-read. This one call is
        # what turns that into "one in-flight round, then exit 143 with the run
        # recorded KILLED".
        #
        # IT IS SIGNAL-SAFE, and that decided its implementation rather than
        # its placement: `request_stage5_shutdown` assigns two module globals
        # and takes no lock, for the same reason the `os.write` below is not a
        # `print`. A `threading.Event` here could deadlock against a lock the
        # main thread already holds.
        request_stage5_shutdown(f"SIGTERM (signal {_signum})")
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
    # --clear-stop IS THE RESUME GESTURE AFTER A STOP, AND IT IS A SEPARATE FLAG
    # FROM --fresh ON PURPOSE. They are opposites: --fresh DISCARDS the resume
    # state and re-bills the cohort, this discards a CONTROL FILE and costs
    # nothing. An operator resuming a stopped campaign wants exactly this and
    # must not be one keystroke from the other, which is also why the two have
    # no combined form and why this one prints nothing alarming.
    #
    # It is a flag rather than the run deleting the sentinel itself: see
    # assert_no_stale_stop_switch for why a self-clearing switch would let a
    # restart loop honour a stop nobody asked for and report success each time.
    _parser.add_argument(
        "--clear-stop", action="store_true",
        # THE PATH IS NOT INTERPOLATED HERE. stop_switch_path() reads
        # paths.checkpoint_path, which resolves the sibling data tree by glob on
        # first read and RAISES on a machine that does not have it -- so a
        # resolved path in this string would make `--help` fail on exactly the
        # checkout where somebody is reading it to find out what the flag does.
        # The run banner prints the resolved path, where resolving it is already
        # unavoidable.
        help="Delete the operator stop sentinel (STOP, in the checkpoint "
             "directory -- the run banner prints its absolute path) before "
             "running. This is how a run that was STOPPED is resumed: the "
             "sentinel is left in place by the run that honoured it, and a run "
             "refuses to start while it is there. It discards no results and "
             "re-bills nothing.")
    _args = _parser.parse_args()

    # ── THE RUN LOCK, BEFORE ANYTHING BELOW IT TOUCHES STATE ────────────────
    #
    # MEASURED, NOT ARGUED: two real invocations of this file launched
    # concurrently against one checkpoint directory both read the same resume
    # state at start, both processed the SAME patients at one live Stage 5 call
    # each, and neither reported anything wrong. The checkpoint is written
    # atomically, so it is never corrupt -- it just ends up as the LAST
    # writer's view, and the loser's completions vanish from it, so a third run
    # re-bills those too.
    #
    # IT IS FIRST, ABOVE THE SENTINEL PREFLIGHT AND ABOVE BOTH FLAGS, because
    # everything below it either mutates state (--fresh deletes the checkpoint,
    # --clear-stop deletes the sentinel) or reads state another run is writing.
    # A refusal here has therefore touched nothing.
    #
    # The mechanism, the key, and why it is not a pid file are argued at
    # oncotriage/batch/runner.py's THE RUN LOCK section. The lock is released
    # by the kernel when this process exits, however it exits.
    try:
        with exclusive_run_lock() as _lock_file:
            console.out(f"[Lock] Held for this run: {_lock_file}")

            # ── THE STALE-SENTINEL PREFLIGHT, ABOVE THE DESTRUCTIVE FLAG ────
            #
            # THIS RUNS BEFORE --fresh IS PROCESSED, AND THAT ORDERING IS THE
            # WHOLE OF THIS BLOCK. main() has always carried this same refusal
            # as its step 0 -- and step 0 is inside main(), which is called
            # AFTER --fresh has already deleted the checkpoint. So an operator
            # who typed `--fresh` while a sentinel was still there got: the
            # checkpoint DELETED, then a refusal whose own final line reads
            # "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED" -- true of the
            # billing and false of the resume state, which was gone. The next
            # invocation then re-ran the entire cohort. Driven, before it was
            # fixed: checkpoint present, `--fresh` typed, refusal printed,
            # checkpoint file absent.
            #
            # `--clear-stop` SATISFIES IT RATHER THAN BEING BLOCKED BY IT, and
            # that is not an exception to the rule but the rule's other half.
            # The refusal's own remediation names that flag, in as many words
            # ("or, in one command: python \"25- Batch Runner.py\"
            # --clear-stop"), and CLAUDE.md documents it as THE resume gesture
            # after a stop. A preflight that refused the command it tells the
            # operator to run would be a loop with no exit. The asymmetry is
            # exactly the destructive/non-destructive line: --clear-stop
            # deletes a CONTROL FILE and re-bills nothing, --fresh deletes the
            # RESUME STATE and re-bills the cohort, and only the second may not
            # happen before the run has decided it is allowed to start.
            #
            # main()'s own step 0 IS KEPT AND IS NOT REDUNDANT: main() is
            # directly callable by an embedder that never sees this guard, and
            # the check is one stat call. On this path it never fires -- this
            # block has already exited the process -- so nothing is printed
            # twice.
            if not _args.clear_stop:
                try:
                    assert_no_stale_stop_switch()
                except StaleStopSwitch as _stale:
                    console.out()
                    console.out(str(_stale))
                    sys.exit(1)

            # ── THE TWO FLAGS, ANNOUNCED ON THE CONSOLE CHANNEL ─────────────
            #
            # `console.out` AND NOT `print`, and this is a real defect rather
            # than a style edit. Everything else this run emits -- every
            # console line and every structured log record -- goes to STDERR
            # through oncotriage/observability.py, flushed per line. `print`
            # goes to STDOUT, which Python BLOCK-BUFFERS when it is not a tty.
            # So in the ordinary captured form, `python "25- Batch Runner.py"
            # --fresh > run.log 2>&1`, these two lines sat in a buffer while
            # hours of stderr went past them and surfaced at interpreter exit
            # -- putting "[--fresh] Discarding the batch checkpoint" at the
            # BOTTOM of the log, after the summary. That is the same buffering
            # trap the SIGTERM handler above documents having MEASURED, which
            # is why it uses a raw os.write.
            if _args.clear_stop:
                # ALL THREE OUTCOMES ARE BRANCHED ON, and the third is why the
                # return stopped being a bool. `--clear-stop` SKIPS the stale
                # sentinel preflight above -- deliberately; it is the gesture
                # that refusal names -- so a clear that FAILED and was reported
                # as "nothing to clear" would start the run with the sentinel
                # still there. It would then trip at the first completed
                # patient and stop again, after billing that patient, for a
                # request the operator had just withdrawn.
                #
                # SO A FAILED CLEAR REFUSES, with the same exit code and the
                # same "nothing has been billed" standing as the preflight it
                # stood in for. clear_stop_switch() has already printed the
                # diagnosis and the `rm`; this only decides not to run.
                _cleared = clear_stop_switch()
                if _cleared == STOP_CLEAR_ABSENT:
                    console.out(f"[--clear-stop] No stop sentinel at "
                                f"{stop_switch_path()}; nothing to clear.")
                elif _cleared == STOP_CLEAR_FAILED:
                    console.out("[--clear-stop] REFUSING TO RUN: the sentinel "
                                "is still there. NOTHING HAS BEEN RUN AND "
                                "NOTHING HAS BEEN BILLED.")
                    sys.exit(1)

            if _args.fresh:
                # Announced before it happens, not after: this is a
                # destructive, expensive request and the operator should see
                # the file named while there is still time to interrupt.
                # clear_checkpoint() prints "[Checkpoint] Cleared." itself, or
                # nothing when there was none.
                console.out("[--fresh] Discarding the batch checkpoint. Every "
                            "patient will run again, at one live Stage 5 call "
                            "each.")
                clear_checkpoint()

            # Ctrl-C EXITS 130 WITH NO TRACEBACK, AND THAT IS THIS GUARD'S JOB RATHER
            # THAN main()'s -- --fresh's precedent and the SIGTERM handler's, for the
            # same reason: a disposition belongs to an entry point, not to a callable an
            # embedder may have its own shutdown contract for.
            #
            # WHY IT IS NEEDED AT ALL, AND IT IS NEW. Both pool handlers used to SWALLOW
            # the KeyboardInterrupt -- see the note at run_batch's -- so a Ctrl-C
            # returned normally, ran the resample pass at one billed call per patient
            # and exited through sys.exit(reconciliation_exit_code()) below with the run
            # recorded FINISHED. They re-raise now, so the interrupt travels through
            # main()'s `except BaseException` (health flushed, both crash blocks
            # printed, `runs` row KILLED, tracking run FAILED) and then out of main().
            # Left uncaught it would reach CPython's default handler, which prints a
            # traceback -- a report of a fault, for a shutdown the operator asked for.
            # The SIGTERM handler already refuses to do that for the same reason and
            # this is the SIGINT half of it.
            #
            # 128 + SIGINT = 130, the shell convention, and NOT the reconciliation
            # verdict (0/1/2), which is a statement about database completeness and has
            # nothing to say about an interrupt. Same argument as SIGTERM's 143.
            #
            # THE RECORD IS ALREADY WRITTEN BY THE TIME THIS RUNS. main()'s crash
            # handler completed before the exception reached here, so nothing is lost by
            # not re-raising: the row, the health flush and both console blocks are on
            # disk and on the terminal.
            _EXIT_SIGINT = 128 + int(signal.SIGINT)
            try:
                main()
            except KeyboardInterrupt:
                # `console.out` FOR THE TWO FLAG ANNOUNCEMENTS' REASON, and here
                # it bites harder: this is the LAST thing an interrupted run says,
                # and on stdout it would be block-buffered behind hours of stderr
                # and surface only at interpreter exit -- below the crash blocks it
                # is meant to conclude.
                console.out(f"\n[INTERRUPTED] Stopped by Ctrl-C. The run was "
                            f"recorded KILLED and the checkpoint is intact -- run "
                            f"again to resume. For a stop that records itself as "
                            f"STOPPED and needs no terminal, use: "
                            f"touch {describe_stop_switch_path()}")
                sys.exit(_EXIT_SIGINT)
            sys.exit(reconciliation_exit_code())

    except AlreadyRunning as _held:
        # THE REFUSAL, ON THE SAME CHANNEL EVERYTHING ELSE THIS FILE SAYS GOES
        # TO. It names the holder's pid, host, user and start time so an
        # operator can act on it -- kill that process, or wait for it -- rather
        # than being told only that something is in the way.
        console.out()
        for _line in run_lock_refusal_lines(_held):
            console.out(_line)
        sys.exit(EXIT_LOCKED)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""
