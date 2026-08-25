# Full-Scale Batch Runner
#########################

"""
Direct Batch Pipeline Runner

Runs the full matching pipeline on all FHIR patients directly in Python
without HTTP overhead. Faster and more reliable than FastAPI for bulk
evaluation runs.

Architecture:
    - BM25 index and LangGraph graph built ONCE, shared across all patients
    - Checkpoint/resume: crash-safe, restarts from last completed patient
    - Resample pass: re-runs a random subset of already-processed patients
      to simulate real-world repeat submissions

Execution flow:
    1. Build BM25 index + compile LangGraph graph
    2. Load all FHIR patient files
    3. Skip patients already in checkpoint (resume support)
    4. Process every pending patient through ONE MAX_WORKERS thread pool, with
       a tqdm bar that advances once per patient (pass 20f-2 corrected this
       line: it used to say "in configurable batch sizes", and there is no
       batch and no size -- the tunable that claimed to set one is deleted, and
       the argument is in the BATCH RUNNER section of oncotriage/config.py)

       THE DELETED TUNABLE IS DELIBERATELY NOT NAMED IN THIS DOCSTRING, and
       that is not fussiness. tests/test_package_invariants.py check 2h counts
       a name appearing in any STRING LITERAL as a read -- deliberately, so
       that getattr(module, "NAME") is not mistaken for dead code -- and this
       docstring is a string literal. The first draft of this line said
       `config.<the name>`, and the revert harness measured the consequence:
       reinstating that constant with no reader and no exemption was NOT
       reported, because this sentence looked like its reader. Prose that names
       a deleted constant is prose that hides its return.
    5. Run resample pass on randomly selected already-processed patients
    6. Print final summary report

STOPPING A RUN: THE `STOP` SENTINEL
------------------------------------
A file named **STOP** in the CHECKPOINT DIRECTORY -- the same directory that
holds ``batch_runner_checkpoint.json`` and ``batch_runner_results.json``, and
whose location ``stop_switch_path()`` is the one owner of. The runner prints the
absolute path in its own setup banner on every run, so the log of a running
campaign always says where to put it::

    touch "<checkpoint dir>/STOP"

The file may be empty -- ``touch`` is the documented gesture -- or may contain a
note, which is recorded in the log and printed in the run's closing block. It is
polled between patients, at the same point the checkpoint is written, in BOTH
passes. When it is seen:

    * no further patient is STARTED -- every queued one is cancelled before it
      can issue a billed call, and unsubmitted ones are never submitted;
    * patients already in flight run to completion and their rows are written;
    * the checkpoint is current, so a resume skips exactly what was done;
    * the RESAMPLE pass does not run at all;
    * the ``runs`` row is finalized **STOPPED** -- a terminal status that is
      neither KILLED (the process died) nor FINISHED (the cohort was covered);
    * the summary and both console report blocks print, and the process exits 0.

The sentinel is NOT deleted by the run that honoured it. That is deliberate: the
next invocation REFUSES to start while it is there (``assert_no_stale_stop_switch``),
because a switch that cleaned up after itself would let a cron entry or a restart
loop honour a stop nobody asked for that day and report success every time.
Deleting it is the resume gesture, and ``--clear-stop`` on the entry point does
it in the same command.

Ctrl-C IS A DIFFERENT REQUEST AND IS RECORDED DIFFERENTLY. Both pool handlers
now RE-RAISE the ``KeyboardInterrupt`` after tearing the pool down and saving the
checkpoint, so it reaches ``main()``'s crash handler: the run row is finalized
KILLED, both crash blocks print, and the resample pass does not run. Before this
they SWALLOWED it -- the run carried on into the resample pass at one billed call
per patient and finalized FINISHED.

Moved out of ``25- Batch Runner.py`` by item 20c, pass 3b. That file is now a
thin entry point holding a ``__main__`` guard and one call.

THE MONKEYPATCH IS GONE, AND THAT IS THE POINT OF THIS MOVE
-----------------------------------------------------------
File 25 lines 65-73 did this, at module level, immediately after chaining
File 14::

    _db_lock = threading.Lock()
    _original_log_inference = log_inference

    def _thread_safe_log_inference(*args, **kwargs):
        with _db_lock:
            return _original_log_inference(*args, **kwargs)

    log_inference = _thread_safe_log_inference

It worked -- FOR FILE 25. It is a rebinding in ONE CALLER's namespace, so every
other concurrent caller of log_inference had no lock at all, and there is one:
``17- FastAPI Server.py`` calls it from ``loop.run_in_executor(...)``, once per
in-flight request, on the event loop's thread pool. Two overlapping POST /match
requests were writing to one SQLite file through two connections with nothing
serializing them.

Worse, the rebinding worked only because everything was exec'd into one dict.
The moment this file became a module, ``log_inference`` here would be a name in
THIS module's globals and ``process_patient`` would resolve it here -- so the
patch would still have "worked", silently, for this file and no other, while
looking like a project-wide guarantee. That is the same class of defect the
deps seam was built for.

The lock moved into ``oncotriage/storage/database_logger.py``, beside the writes
it protects. Every caller gets it; no caller has to know it exists. See the
block above ``initialize_database`` there for what the unserialized race
actually cost, which is a lost row reported as a success.

``_db_lock`` STILL EXISTS IN THIS MODULE, and it is a DIFFERENT lock doing a
DIFFERENT job: File 25 used the same lock object for the database AND for
``append_result``'s read-modify-write of the results list and its temp-file
rename. Those two have nothing to do with each other; sharing one lock made
every results-file write wait behind every database write. It is renamed
``_results_lock`` here to say what it guards.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing observable: no connection, no client, no model, no path resolution, no
checkpoint read. ``_checkpoint_path()`` and ``_results_path()`` were already
functions in File 25 and stay functions, so ``paths.checkpoint_path`` resolves
on first call rather than at import.
"""

import contextlib
import glob
import json
import os
import random
import sqlite3
import threading
import time
from collections import Counter
from concurrent.futures import CancelledError, ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

# `control` HOLDS THE RUN LOCK AND THE STOP SWITCH, and importing it at MODULE
# SCOPE is what preserves a guarantee this file used to state itself. It imports
# `fcntl`, which is POSIX-only and whose absence is a REFUSAL rather than a
# degradation: running a 22,000-patient billed campaign UNLOCKED because the
# locking primitive was missing would be precisely the failure the lock exists
# to prevent, and it would be silent. At module scope the failure is at import,
# not four hours in -- and because this import is at module scope, that holds
# transitively without a second `import fcntl` here to keep in step.
from oncotriage import control
from oncotriage import paths
from oncotriage.agent.graph import build_matching_graph, match_patient_to_trials
from oncotriage.agent.retrieval import build_bm25_index_from_qdrant
from oncotriage.config import (
    CHECKPOINT_FILENAME,
    MAX_WORKERS,
    Project_Name,
    RESAMPLE_COUNT,
    RESAMPLE_SEED,
    RESULTS_FILENAME,
)
from oncotriage.agent.evaluation import (
    MatchingModelMismatchError,
    clear_stage5_shutdown,
    request_stage5_shutdown,
)
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.storage.database_logger import (
    RUN_METRICS_FLUSH_FAILURES,
    # THE FOUR TERMINAL STATUSES BY NAME. `runs.status` is a CLOSED vocabulary
    # owned by the storage layer, and this module writes every one of its
    # terminal members. Written out as literals they were eight strings in
    # three places -- and two of those places derived the SAME verdict
    # independently, under a comment arguing that they must not disagree. See
    # `_terminal_status` in main().
    RUN_RECORD_STATUS_FAILED,
    RUN_RECORD_STATUS_FINISHED,
    RUN_RECORD_STATUS_KILLED,
    RUN_RECORD_STATUS_STOPPED,
    RUN_RECORD_TERMINAL_STATUSES,
    finalize_run_record,
    flush_run_metrics,
    log_inference,
    resolve_inference_db_path,
    start_run_record,
)
from oncotriage import utils
from oncotriage.utils import CaffeinateSession, preserve_corrupt_file
from oncotriage import run_fingerprint
from oncotriage.observability import console, get_logger
from oncotriage import degradation
from oncotriage import tracking


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# THREAD SAFETY
# ===========================================================================
#
# ONE LOCK, GUARDING ONE THING: the results file.
#
# File 25 had a single `_db_lock` doing two unrelated jobs -- serializing
# log_inference (by monkeypatch, see the module docstring) AND serializing
# append_result's read-modify-write of the shared results list plus its
# temp-file rename. Those two have nothing in common except that both are
# reached from worker threads. Sharing one lock meant every results-file write
# queued behind every database write, and the name said "db" while half its
# call sites were about JSON.
#
# The database half moved into oncotriage/storage/database_logger.py, where
# every caller gets it. What is left is renamed for what it actually protects.
#
# _checkpoint_lock is unchanged and separate, because save_checkpoint() writes a
# different file and must not queue behind the results write either.
_results_lock = threading.Lock()

_checkpoint_lock = threading.Lock()


# ===========================================================================
# THE WRITE LEDGER (the write-durability pass)
# ===========================================================================
#
# WHAT IT IS FOR. log_inference does not raise when a row is lost -- it must
# not, because a logging fault cannot be allowed to destroy a ~70-second
# pipeline result that cost a live Stage 5 call. So the loss has to be carried
# out of the worker some other way, and this is it: one entry per attempted
# write, appended by the worker thread that made it.
#
# WHY NOT COUNT ROWS AND COMPARE WITH PATIENTS, which is the obvious thing. Two
# facts about this runner make that number wrong, and they pull in opposite
# directions:
#
#   1. THE RESAMPLE PASS deliberately re-runs a seeded subset of already-
#      processed patients (RESAMPLE_COUNT = 100). Each re-run writes ANOTHER
#      inference row, so rows are not one per patient and a run that lost
#      nothing would look like a run with 100 extra rows.
#   2. A CHECKPOINT RESUME skips every patient an earlier process completed.
#      Those rows are in the table and this process did not write them, so a
#      run that lost nothing would look like a run with hundreds of extra rows
#      -- and, symmetrically, a resumed run that lost 50 rows could still show
#      a total far above its own patient count and read as healthy.
#
# The ledger sidesteps both by construction rather than by correcting for them:
# it records CALLS, so a resample re-run is a second entry and a skipped patient
# is no entry at all. Neither case needs arithmetic.
#
# AND IT IS EXACT RATHER THAN STATISTICAL, which a before/after row count cannot
# be. log_inference reports the `inferences.id` it was assigned, so
# reconcile_writes asks the database whether THOSE ROWS are present, by id. A
# count delta would be inflated by any other process writing the same file
# during the run -- and there is one, "17- FastAPI Server.py", which resolves to
# the same production database unless it was started with
# ONCOTRIAGE_INFERENCES_DB set. The delta is still reported, because it is the
# cheap cross-check File 19 uses and because a discrepancy between it and the
# ledger is itself informative; it just does not decide the verdict.
INVOCATION_SOURCE = "batch_runner"
"""What this module records in ``runs.invocation_source``.

A CONSTANT AND NOT A LITERAL AT THE CALL SITE, so a query grouping campaigns by
their entry point can be written against a name rather than against a string
somebody may retype. The value is the MODULE, not the numbered file: "25- Batch
Runner.py" is one way in and ``runner.main()`` is directly callable by an
embedder, and both are this runner.
"""


_write_ledger_lock = threading.Lock()

_WRITE_LEDGER = []
"""One dict per log_inference call this process made: patient_id, db_path, ok,
error, inference_id, attempts, is_resample."""


def clear_write_ledger() -> None:
    """Empty the ledger. Called by main() before the run, and by tests."""
    with _write_ledger_lock:
        _WRITE_LEDGER.clear()


def record_write(patient_id: str, write_result, is_resample: bool) -> None:
    """Append one log_inference outcome to the ledger.

    Args:
        patient_id:   The patient the write was for.
        write_result: What log_inference returned -- an InferenceWriteResult,
                      which IS the database path string and also carries `.ok`,
                      `.error`, `.attempts` and `.inference_id`.
        is_resample:  Whether this was the resample pass's re-run.

    TOLERATES A PLAIN STRING. A caller running against a pre-durability
    log_inference, or a test that stubs it, gets a `str` with no `.ok`. That is
    recorded as ok=None -- "this writer did not report" -- and reconcile_writes
    counts those separately rather than assuming success. Defaulting an absent
    report to True would reintroduce the exact defect this pass removes, one
    layer up.
    """
    entry = {
        "patient_id":   patient_id,
        "db_path":      str(write_result) if write_result is not None else None,
        "ok":           getattr(write_result, "ok", None),
        "error":        getattr(write_result, "error", None),
        "inference_id": getattr(write_result, "inference_id", None),
        "attempts":     getattr(write_result, "attempts", None),
        "is_resample":  is_resample,
    }
    with _write_ledger_lock:
        _WRITE_LEDGER.append(entry)



# ===========================================================================
# CHECKPOINT HELPERS
# ===========================================================================

# `paths.checkpoint_path`, not `from oncotriage.paths import checkpoint_path`.
# The second form is an ATTRIBUTE READ and would fire the lazy resolver at
# import, globbing the sibling data tree just to load this module. These two
# were already functions in File 25, so the resolution has always happened on
# first call; keeping the module form is what preserves that.
def _checkpoint_path() -> Path:
    return Path(paths.checkpoint_path) / CHECKPOINT_FILENAME


def _results_path() -> Path:
    return Path(paths.checkpoint_path) / RESULTS_FILENAME


CORRUPT_CHECKPOINT_SUFFIX = ".corrupt"
"""Suffix a checkpoint that could not be read is COPIED to.

Copied, not renamed, and the difference is the whole point -- see
``oncotriage/utils.py:preserve_corrupt_file``. A renamed checkpoint is gone
from its own path, so the refusal below would be loud once and silent
afterwards: the next invocation would find no checkpoint, start fresh, and
re-bill the entire cohort. Copying leaves the refusal STICKY until an operator
clears the checkpoint deliberately.
"""

CHECKPOINT_FAULTS = Counter()
"""Checkpoint faults, keyed ``{phase}:{detail}``.

Module-level, following ``RESULTS_FILE_FAILURES`` above and
``CHECKPOINT_WRITE_FAILURES`` in ``oncotriage/ablation/study.py``, and NOT a
column: the fault happens before the first patient of a resumed run, so there
is no inference row it belongs to.

Phases:
    ``load:``      the file existed and could not be read back
    ``shape:``     it parsed and was not a checkpoint (no dict, or no
                   ``completed_stems`` list)
    ``preserve:``  the unreadable file could not even be copied aside
    ``refused:``   a readable checkpoint was REFUSED, keyed by the
                   ``FP_OUTCOMES`` member that refused it
    ``write:``     the checkpoint could not be WRITTEN. The most expensive key
                   here and the last to be added: it fires once per completed
                   patient for as long as the condition lasts, and every one of
                   them is a patient the next invocation will re-bill. See
                   ``checkpoint_write_failures()``, which the closing block
                   reads so it cannot claim a checkpoint was kept when the
                   writes failed.
    ``tmp_unlink:`` a failed write left its temporary file behind
"""

degradation.register(
    "CHECKPOINT_FAULTS", CHECKPOINT_FAULTS,
    "the batch checkpoint could not be read, or was refused because the "
    "configuration that produced it is not this one; NO patient was skipped "
    "and NO patient was re-run -- the run stopped instead")


def _checkpoint_remediation() -> tuple:
    """The two commands that clear a refused checkpoint. One text, two printers.

    WHY A COMMAND AND NOT A FLAG ON ``main()``. This module's ``main()`` takes
    no arguments and its docstring pins that ("THE RETURN TYPE IS UNCHANGED,
    deliberately"); an embedder calls it programmatically, and a ``main()`` that
    started parsing ``sys.argv`` would exit(2) inside somebody else's process
    the first time their flags did not match ours. So the flag lives in
    ``25- Batch Runner.py``'s ``__main__`` guard -- exactly where
    ``05- FHIR Clean Data.py`` puts ``--dry-run``, and for the reason recorded
    there -- and the module-level function stays the mechanism it always was.
    """
    return (
        "To start fresh (this DISCARDS the resume state and re-runs every "
        "patient, at cost):",
        "    python \"25- Batch Runner.py\" --fresh",
        "or, equivalently, from anywhere:",
        "    python -c \"from oncotriage.batch.runner import clear_checkpoint; "
        "clear_checkpoint()\"",
        "NOTHING HAS BEEN DELETED. The checkpoint is exactly as it was.",
    )


def _refuse_checkpoint(outcome: str, detail: str, cp, recorded=None) -> None:
    """Count, log and raise. Never deletes, never skips, never re-runs.

    ``recorded`` IS THE STORED STAMP, forwarded so ``refusal_lines`` can tell
    which DIRECTION a version mismatch runs in. Without it a checkpoint written
    by a NEWER build is refused with this file's own remediation -- `--fresh` --
    printed underneath it, which would discard an artifact that build can still
    continue.
    """
    CHECKPOINT_FAULTS[f"refused:{outcome}"] += 1
    lines = run_fingerprint.refusal_lines(
        outcome, detail, f"the batch checkpoint at {cp}",
        _checkpoint_remediation(), recorded=recorded)
    log.error("batch checkpoint refused", event="checkpoint_refused",
              status="error", error_type=outcome)
    raise run_fingerprint.ResumeRefusal("\n".join(lines), outcome=outcome)


def load_checkpoint(fingerprint: dict = None) -> set:
    """
    Load set of already-completed filename stems from checkpoint file.

    The checkpoint key is always the FHIR filename stem (without extension),
    e.g. "Firstname_Lastname_UUID". This is consistent with how pending_files
    and completed_files are filtered, avoiding UUID vs. stem mismatch bugs.

    Args:
        fingerprint: the configuration to compare the stored stamp against.
            ``None`` -- what ``main()`` passes -- takes
            ``run_fingerprint.current()``.

            THIS ARGUMENT IS WHY THIS FUNCTION STILL WORKS OFFLINE, and it is
            here because the first version of this pass did not have it and
            widened a library contract by accident: resolving the current
            stamp asks Qdrant, so ``load_checkpoint()`` and
            ``save_checkpoint()`` -- which had touched no network in their
            lives -- suddenly needed a live endpoint, and a caller without one
            got FP_UNRESOLVED and a refusal for a reason that had nothing to
            do with its checkpoint. ``main()`` is unaffected either way (it
            resolves after ``build_bm25_index_from_qdrant()``, so Qdrant is
            already proven live and the stamp is already cached), which is
            exactly what made the widening invisible until a test that runs
            with no keys was measured.

    Returns:
        Set of filename stem strings that have been successfully processed.
        Empty set if no checkpoint file exists.

    Raises:
        run_fingerprint.ResumeRefusal: the checkpoint exists and this run may
            not continue it -- it could not be read, or it was produced by a
            different configuration, or by an unknown one.

    THE TWO THINGS THIS USED TO DO SILENTLY, AND WHAT EACH COST
    -----------------------------------------------------------
    1. AN UNREADABLE CHECKPOINT WARNED AND RETURNED AN EMPTY SET. On a resume
       that is not a degraded read: it is a silent decision to re-run every
       patient an earlier process completed, at ~$0.15 each. A truncated tail
       on a 19,000-patient checkpoint cost about $2,850 and printed one WARNING
       line above a bar that then looked like a normal fresh run.
    2. A CHECKPOINT SAID WHAT WAS DONE AND NEVER WHAT IT WAS DONE UNDER. So a
       resume after a prompt edit, a model change or an index rebuild skipped
       every patient the OLD configuration had completed and ran the rest under
       the new one, into ONE inferences table, with nothing anywhere saying the
       run held two eras.

    Both are refusals now, and a refusal DELETES NOTHING. That is the whole
    contract: the operator is told, the state is intact, and the two things
    that may not happen -- silently skipping and silently re-billing -- both
    require an explicit command.

    A CHECKPOINT WITH NO FINGERPRINT IS UNKNOWN PROVENANCE, NOT A PASS. Every
    checkpoint written before this pass is in that class. It is refused with
    that stated, rather than adopted: an artifact that does not say what
    produced it cannot be shown to have been produced by this.
    """
    cp = _checkpoint_path()
    if not cp.exists():
        return set()

    try:
        with open(cp, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError, UnicodeDecodeError) as e:
        _unreadable_checkpoint(cp, f"{type(e).__name__}: {e}",
                               f"load:{type(e).__name__}")

    # A payload that parses and is not a checkpoint. Separated from the decode
    # failure, with its own phase key, because "truncated file" and "somebody
    # wrote a different shape here" want different fixes -- load_results'
    # arrangement. `data.get(...)` on a list raises AttributeError, which the
    # pre-pass code did not catch at all.
    if not isinstance(data, dict):
        _unreadable_checkpoint(
            cp, f"expected a JSON object, found {type(data).__name__}",
            f"shape:{type(data).__name__}")
    stems = data.get("completed_stems")
    if not isinstance(stems, list):
        # NOT the same as an empty checkpoint. `set(data.get(k, []))` used to
        # turn this into "nothing completed", which is a silent full re-run
        # wearing the clothes of a successful read.
        _unreadable_checkpoint(
            cp, f"'completed_stems' is {type(stems).__name__}, not a list",
            f"shape:completed_stems={type(stems).__name__}")

    outcome, detail = run_fingerprint.compare(
        data.get("fingerprint"),
        fingerprint if fingerprint is not None else run_fingerprint.current())
    if outcome != run_fingerprint.FP_MATCH:
        _refuse_checkpoint(outcome, detail, cp, recorded=data.get("fingerprint"))

    completed = set(stems)
    console.out(f"[Checkpoint] Resuming: {len(completed)} patients already completed.")
    console.out(f"[Checkpoint] Configuration matches: {detail}")
    return completed


def _unreadable_checkpoint(cp, error: str, counter_key: str) -> None:
    """Count, COPY aside, log, and raise. Always raises; never returns.

    Shared by all three unreadable branches so the counter key is the only
    thing that differs between them -- ``_unreadable_results``' arrangement,
    and for its reason: three copies of this sequence is three chances for one
    of them to stop preserving the file.
    """
    CHECKPOINT_FAULTS[counter_key] += 1
    preserved, preserve_error, preserve_key = utils.preserve_corrupt_file(
        cp, CORRUPT_CHECKPOINT_SUFFIX, keep_original=True)
    if preserved:
        where = f"A copy has been preserved as {preserved}."
    else:
        CHECKPOINT_FAULTS[f"preserve:{preserve_key}"] += 1
        where = (f"It could NOT be copied aside ({preserve_error}), so the only "
                 f"copy is the one still at {cp}.")

    log.error("batch checkpoint unreadable", event="checkpoint_unreadable",
              status="error", error_message=error)
    raise run_fingerprint.ResumeRefusal("\n".join(
        [f"REFUSED (unreadable): the batch checkpoint at {cp}",
         f"    {error}",
         f"    {where}",
         "    Continuing would silently re-run every patient an earlier run "
         "completed, at a live Stage 5 call each. THE CHECKPOINT IS INTACT: "
         "nothing was deleted and no patient was re-run."]
        + [f"    {line}" for line in _checkpoint_remediation()]),
        outcome=run_fingerprint.FP_ABSENT)


def save_checkpoint(completed_stems: set, fingerprint: dict = None) -> None:
    """
    Atomically persist completed filename stems to checkpoint file.

    Uses a temp file + os.replace() so a crash mid-write never corrupts
    the checkpoint -- the exact file that protects against crashes.
    Called after every successfully processed patient so a crash
    loses at most MAX_WORKERS patients (one per active thread).

    Args:
        completed_stems: Full set of completed filename stem strings so far.
        fingerprint: the configuration stamp to record. ``None`` -- what every
            call site passes -- takes ``run_fingerprint.current()``, which is
            resolved ONCE per process and cached. A stamp resolved per write
            would be one live Qdrant round trip per patient, and worse than the
            cost: a run is ONE configuration, so a per-write stamp that
            straddled the weekly alias swap would put two collections into one
            checkpoint and the file would then refuse itself.
    """
    with _checkpoint_lock:
        cp = _checkpoint_path()
        tmp_path = cp.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(
                    {
                    "completed_stems": list(completed_stems),
                    "last_updated": datetime.now().isoformat(),
                    "count": len(completed_stems),
                    # WHAT PRODUCED THIS SET. Last in the object rather than
                    # first only because the three keys above are what File 25
                    # has always written and a diff of two checkpoints should
                    # read as an addition.
                    "fingerprint": (fingerprint if fingerprint is not None
                                    else run_fingerprint.current()),
                    "collection_identity": run_fingerprint.COLLECTION_IDENTITY,
                },
                f,
                indent=2,
                )
            os.replace(tmp_path, cp)
        except OSError as e:
            # COUNTED, NOT ONLY PRINTED, and until this line existed it was
            # only printed. A read-only checkpoint directory -- a filled disk,
            # a remounted share, a permission change -- makes EVERY write here
            # fail, once per completed patient, and the run then finished with
            # the degradation block reporting CLEAN and the closing line
            # claiming the checkpoint had been kept. The next invocation
            # re-billed every patient this one had already paid for. The
            # counter is registered, so it reaches the run-end block AND
            # `run_metrics` for free.
            #
            # `write:` IS THE PHASE KEY, following the four this counter
            # already documents and `CHECKPOINT_WRITE_FAILURES` in
            # oncotriage/ablation/study.py, which is the same fault in the same
            # shape one module over.
            CHECKPOINT_FAULTS[f"write:{type(e).__name__}"] += 1
            console.out(f"[Checkpoint] WARNING: Could not write checkpoint ({e}). Continuing.")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError as unlink_exc:
                    # COUNTED TOO, on the ablation study's precedent and for
                    # its stated reason: a `tmp_unlink:` can only follow a
                    # `write:`, so a count of the second without the first is
                    # uninterpretable -- and the leftover .tmp file is a real
                    # thing an operator finds and wonders about.
                    CHECKPOINT_FAULTS[
                        f"tmp_unlink:{type(unlink_exc).__name__}"] += 1
                    console.out(f"[Checkpoint] WARNING: the temporary file "
                                f"{tmp_path} could not be removed "
                                f"({unlink_exc}).")


def checkpoint_write_failures() -> int:
    """How many checkpoint WRITES failed this process. A reader, not a counter.

    Sums the ``write:`` keys of ``CHECKPOINT_FAULTS`` and nothing else --
    ``load:``, ``shape:``, ``preserve:`` and ``refused:`` all describe a
    checkpoint that could not be READ, which is a different finding with a
    different remedy and which stops the run before it starts.

    IT EXISTS BECAUSE THE CLOSING BLOCK MADE A CLAIM IT COULD NOT CHECK. "the
    checkpoint is kept; the next run resumes from it" is exactly false when
    every write failed, and that is precisely the run in which an operator most
    needs to be told: the file on disk is either absent or stale at whatever
    the last successful write left, so a resume re-runs -- and re-bills --
    every patient completed since.

    A PREFIX MATCH RATHER THAN AN EXACT KEY, because the key carries the
    exception type; ``write:PermissionError`` and ``write:OSError`` are the
    same finding for this purpose.
    """
    return sum(count for key, count in CHECKPOINT_FAULTS.items()
               if key.startswith("write:"))


def describe_checkpoint_state(kept_reason: str) -> str:
    """One line saying what the checkpoint on disk is actually worth.

    ``kept_reason`` is the caller's sentence for the ordinary case, so the two
    places that report a kept checkpoint keep their own wording and share only
    the correction.

    THE FILE'S EXISTENCE AND THE WRITE FAILURES ARE BOTH READ, because they are
    two different bad outcomes: no file at all (every write failed and there
    was nothing there before), and a file that is real but STALE (the writes
    started failing partway). Telling them apart is what decides whether a
    resume re-runs everything or only the tail.
    """
    failures = checkpoint_write_failures()
    if not failures:
        return kept_reason
    try:
        present = _checkpoint_path().exists()
    except Exception as exc:                                    # noqa: BLE001
        return (f"UNKNOWN: {failures} checkpoint write(s) FAILED and the path "
                f"could not even be checked ({type(exc).__name__}: {exc}). "
                f"Treat the resume state as untrustworthy.")
    if not present:
        return (f"ABSENT: all {failures} checkpoint write(s) FAILED and no "
                f"checkpoint exists. THE NEXT RUN RE-RUNS AND RE-BILLS EVERY "
                f"PATIENT. Fix the checkpoint directory before re-running.")
    return (f"STALE: {failures} checkpoint write(s) FAILED, so the file on "
            f"disk is whatever the last successful write left. The next run "
            f"RE-RUNS AND RE-BILLS every patient completed after that point. "
            f"Fix the checkpoint directory before re-running.")


def clear_checkpoint() -> None:
    """Delete checkpoint file to start a fresh run."""
    cp = _checkpoint_path()
    if cp.exists():
        cp.unlink()
        console.out("[Checkpoint] Cleared.")


def clear_results() -> None:
    """Delete results file to start a fresh run."""
    rp = _results_path()
    if rp.exists():
        rp.unlink()
        console.out("[Results] Cleared.")


def clear_all() -> None:
    """Delete both checkpoint and results files for a completely fresh run."""
    clear_checkpoint()
    clear_results()
    console.out("[State] All batch runner state cleared. Ready for fresh run.")


# ===========================================================================
# THE RUN LOCK  (the pre-migration pass)
# ===========================================================================
#
# TWO BATCH RUNS AGAINST ONE CHECKPOINT DIRECTORY IS A SILENT DOUBLE BILL, AND
# NOTHING STOPPED IT. Measured by driving two real invocations of
# `25- Batch Runner.py` concurrently against one directory: both read the same
# checkpoint at start, so both saw the same "already completed" set and both
# processed the SAME patients -- one live Stage 5 call each, twice, for a
# cohort that needed one. Neither run reported anything wrong. The checkpoint
# itself is written atomically (temp file + os.replace), so it is never
# corrupt; it is simply the LAST writer's view, and the loser's completions
# vanish from it, which then makes a third invocation re-bill THOSE too.
#
# THE SHAPE IS tests/run_serial_tests.py's, DELIBERATELY AND VERBATIM, down to
# the exit code. That file solved the identical problem one layer up -- two
# copies of a suite that rewrites source in place -- and its argument transfers
# without amendment:
#
#   `flock(LOCK_EX | LOCK_NB)` on a file outside the repository, held for the
#   whole run, RELEASED BY THE KERNEL when this process exits, however it
#   exits: a clean return, an uncaught exception, SIGKILL, a panic, a laptop
#   lid. That property is what rules out the alternative shape. A pid file
#   written and deleted by this program leaves a stale lock behind every time
#   it dies badly -- which for a batch runner is every SIGKILL after a `docker
#   stop` grace period -- and the "is that pid still alive" repair
#   re-introduces a check-then-act race of its own, on a pid the OS may already
#   have recycled.
#
# TWO TERMINALS IS THE OBVIOUS WAY IN AND IT IS NOT THE LIKELY ONE. A cron
# entry whose previous run has not finished, a systemd unit with Restart=,
# a container restarted by an orchestrator that could not read its health
# check, a re-run started because the first "looked stuck" during a 20-minute
# index build -- none of those involves anybody deciding to overlap.
#
# THE KEY IS THE CHECKPOINT DIRECTORY AND NOT THE CODE DIRECTORY, which is
# where this diverges from the serial runner and it is the whole of the
# divergence. That file protects a source tree, so its key is the tree. This
# protects a RUN's state, and the checkpoint directory is what identifies it:
# two checkouts pointed at one deployment must block each other, and one
# checkout driven at two deployments (ONCOTRIAGE_MAIN_PATH moved) must not.
#
# IT IS TAKEN IN `25- Batch Runner.py`'s __main__ GUARD AND NOT IN `main()`, on
# --fresh's and the SIGTERM handler's precedent and for a sharper version of
# their reason: `main()` is directly callable and an embedder driving several
# configurations in one process, or a test driving two passes, would be
# refusing itself. A process-wide exclusion belongs to the process's entry
# point.


TRACKING_STATUS_FOR = {
    RUN_RECORD_STATUS_FINISHED: "FINISHED",
    RUN_RECORD_STATUS_FAILED:   "FAILED",
    RUN_RECORD_STATUS_KILLED:   "KILLED",
    # THE ONE ROW THAT IS NOT AN IDENTITY, AND THE WHOLE REASON THIS EXISTS.
    RUN_RECORD_STATUS_STOPPED:  "KILLED",
}
"""How a `runs.status` verdict is stated in MLflow's three-member vocabulary.

TWO VOCABULARIES, ONE VERDICT, AND THE TRANSLATION WRITTEN DOWN. `runs.status`
has four terminal members and `oncotriage/tracking.py:RUN_STATUSES` has three;
`RUN_RECORD_STATUSES_BEYOND_TRACKING` names the difference and this names what
to do about it. Before this, `main()` derived the MLflow status from
`_stopped_mid_cohort` and `main_errors` in a SEPARATE three-way conditional
from the one that produced the row's status -- two expressions that had to stay
in step by hand, under a comment declaring a "divergence" that was therefore
not a mapping anybody could read.

STOPPED -> KILLED IS THE CLOSEST TRUE STATEMENT, NOT A ROUNDING. MLflow's own
definition of KILLED is "run killed by user", which is literally what a stop
switch is. Passing "STOPPED" through unmapped would be worse than either:
`tracking.end_run` REPLACES an unrecognised status with FAILED, so every
stopped campaign would be indexed as a failure -- true of nothing.

IT IS NOT INVERTIBLE AND MUST NOT BE READ AS IF IT WERE. Two distinct row
statuses map onto KILLED, so an MLflow run reading KILLED is either a crash or
a clean operator stop and only `runs.status` can say which. That is the cost of
the narrower vocabulary and it is why the row, not the index, is the authority
on how a campaign ended.
"""

if set(TRACKING_STATUS_FOR) != set(RUN_RECORD_TERMINAL_STATUSES):
    # A `RuntimeError` AND NOT AN `assert`, on this project's standing rule:
    # `python -O` deletes assert statements, and this is the only thing between
    # a fifth terminal status and an unmapped verdict reaching `end_run`, which
    # substitutes FAILED for what it does not recognise and says nothing.
    #
    # AT IMPORT, NOT AT THE CALL SITE. The call site is reached once, after a
    # whole campaign has been billed; a KeyError there would replace the run's
    # own closing report with a traceback about bookkeeping. At import it is a
    # load failure with nothing spent.
    raise RuntimeError(
        "TRACKING_STATUS_FOR must map every RUN_RECORD_TERMINAL_STATUSES "
        f"member and nothing else; it maps {sorted(TRACKING_STATUS_FOR)} "
        f"against {sorted(RUN_RECORD_TERMINAL_STATUSES)}")


EXIT_LOCK_UNAVAILABLE = 1
"""The entry point's exit code when the lock could not be ATTEMPTED at all.

DELIBERATELY NOT ``control.EXIT_LOCKED``. 3 means "another copy of this program
is already running", which a supervisor may reasonably treat as benign and retry
later; this means "the lock file could not be opened", which no amount of
waiting fixes and which needs a person. 1 is what every other refusal in this
entry point returns -- the stale sentinel, the failed ``--clear-stop`` -- and
it carries the same standing: nothing has been run and nothing has been billed.

IT STAYS HERE RATHER THAN MOVING TO ``oncotriage/control.py`` WITH ITS SIBLING,
and that asymmetry is the point rather than an oversight. ``EXIT_LOCKED`` is 3
in all three programs and they agree on why, so it is a fact about the
mechanism. This one's VALUE is read off THIS entry point's own vocabulary -- 1
is what its other refusals return -- and the serial runner, whose 1 already
means "a test failed", uses 4. One shared constant cannot carry that, and a
shared constant that is wrong for one of its consumers is worse than three
right ones.

THE RESIDUAL AMBIGUITY IS STATED RATHER THAN GLOSSED: 1 is ALSO the
reconciliation verdict's "rows were lost", so a supervisor reading the exit code
alone cannot tell "the lock would not open" from "a campaign ran and lost rows".
That collision is not introduced here -- the stale-sentinel refusal and the
failed ``--clear-stop`` already exit 1 -- and giving THIS refusal a fourth code
while leaving those two on 1 would make the vocabulary less legible, not more.
Separating "refused before running" from "ran and something was wrong" across
all three is a contract change with its own argument; the console line is
unambiguous either way.
"""


LOCK_FILE_PREFIX = "oncotriage-batch-run-"
"""The lock file's name prefix, and it is load-bearing rather than cosmetic.

All three of this project's run locks live in ONE per-user directory (see
``control.lock_directory`` for why one directory is the right shape), so the
prefix is the only thing that keeps them apart. A batch run and an ablation
study guard different things and must not refuse each other; a serial test run
guards a third. If two prefixes ever collide, the symptom is a refusal naming a
holder that has nothing to do with the thing being started.
"""


class AlreadyRunning(control.AlreadyRunning):
    """Another process holds the BATCH run lock. Carries its record.

    A subclass of the shared class rather than the shared class itself, and the
    argument moved when ``oncotriage/control.py`` was written. What this
    docstring used to say -- that a shared class would drag another program's
    module into this one's import graph -- is no longer true of anything:
    ``control`` imports nothing from the project. What survives is that the
    three refusals are raised by different programs, name different
    consequences and are remediated with different commands, and a caller that
    holds more than one lock (this suite already has a test that drives two)
    must be able to tell from the TYPE which one it caught. See
    ``control.AlreadyRunning`` for the full argument.
    """


class LockUnavailable(control.LockUnavailable):
    """The BATCH run lock could not be ATTEMPTED. Carries the path and the errno.

    A DIFFERENT FINDING FROM ``AlreadyRunning`` AND NOT A SUBCLASS OF IT -- they
    are siblings under ``control``'s two base classes, so ``except
    LockUnavailable`` cannot catch a held lock and vice versa. That one means
    another copy of this program holds the lock, which is benign and
    self-clearing; this means the lock file could not be opened at all, and no
    amount of waiting fixes it. They exit differently for that reason.

    Both bases are ``RuntimeError`` and deliberately NOT ``OSError``; the whole
    argument is at ``control.LockUnavailable``.
    """


def run_lock_path(checkpoint_dir=None) -> str:
    """Where the run lock for ``checkpoint_dir`` lives.

    THE KEY IS THIS PROGRAM'S AND STAYS HERE; the derivation from a key to a
    lock file -- the temp directory, the ``realpath``, the sha256, the
    truncation -- is ``control.lock_file_path``, which is where the argument for
    each of those now lives.

    ``paths.checkpoint_path`` is read as a MODULE ATTRIBUTE for the reason
    ``stop_switch_path`` records: a from-import fires the lazy resolver at
    import and globs the sibling data tree just to load this module.
    """
    root = (checkpoint_dir if checkpoint_dir is not None
            else paths.checkpoint_path)
    return control.lock_file_path(LOCK_FILE_PREFIX, root)


def lock_unavailable_lines(exc) -> list:
    """The diagnosis, as the lines the entry point prints. One text, one caller.

    A FUNCTION RATHER THAN A BLOCK IN THE GUARD, on ``run_lock_refusal_lines``'
    footing: it can be driven by a test without arranging an unopenable path in
    a subprocess, and the entry point's ``__main__`` block stays a guard.

    THE MECHANICAL HALF IS ``control.lock_unavailable_lines`` -- the symbolic
    errno, the ``at:`` line when the failing filename differs from the lock's,
    the causes list and the nothing-was-billed line, all of which are the same
    question in every program. What is passed in is the half that is not: what
    running WITHOUT the guarantee would cost THIS program.
    """
    return control.lock_unavailable_lines(
        exc,
        header="[Batch] REFUSING TO RUN: the run lock could not be taken.",
        consequence=[
            "        This is NOT 'another run holds the lock' -- that is a "
            "different",
            "        refusal with a different exit code. The lock file could "
            "not be",
            "        opened at all, so this run cannot establish that it is "
            "the only",
            "        one, and running without that guarantee is how two "
            "campaigns",
            "        bill the same cohort twice.",
        ])


@contextlib.contextmanager
def exclusive_run_lock(path=None, checkpoint_dir=None):
    """Hold an exclusive, non-blocking flock for the duration of the block.

    Yields the lock file's path. The mechanism -- the 0700 directory, the
    ``O_NOFOLLOW`` open, the non-blocking flock, the UTC record written only
    after the lock is held, the kernel release -- is
    ``control.hold_exclusive_lock``; what is decided here is this program's key,
    its two exception classes and the field its record names.

    THE DECORATOR IS NOT DECORATION. Without it ``with exclusive_run_lock():``
    raises ``AttributeError`` on a generator at the top of a batch run, and
    ``tests/test_package_invariants.py``'s decorator inventory is what makes
    that loss visible in bucket A rather than only when somebody runs one.

    THE DIRECTORY IS RESOLVED ONCE AND THE RECORD IS BUILT FROM WHAT WAS
    ACTUALLY LOCKED. The first version read ``paths.checkpoint_path`` a SECOND
    time when writing the record, so a caller that passed an explicit ``path``
    -- a test, a second deployment -- got a holder record naming a directory it
    had nothing to do with. A lock record that names the wrong directory is
    worse than one that names none: an operator acts on it.
    """
    derived = path is None
    if derived:
        if checkpoint_dir is None:
            checkpoint_dir = paths.checkpoint_path
        path = run_lock_path(checkpoint_dir)
    with control.hold_exclusive_lock(
            path,
            already_running=AlreadyRunning,
            lock_unavailable=LockUnavailable,
            record_key="checkpoint_dir",
            # ``realpath``, matching the KEY. The lock is keyed on the resolved
            # path, so a record naming the unresolved one could show an operator
            # a different string from the one the refused run derived its digest
            # from -- two names for the one thing this refusal is about.
            record_value=(os.path.realpath(str(checkpoint_dir))
                          if checkpoint_dir is not None
                          else "<not supplied: the lock file was named "
                               "directly>"),
            # ONLY WHEN WE DERIVED THE PATH. A caller who named the lock file
            # directly owns its directory -- creating one under a path this
            # function was handed would be a side effect nobody asked for, and
            # the in-process tests name files inside directories they made
            # themselves.
            ensure_directory=derived) as held:
        yield held


def run_lock_refusal_lines(exc) -> list:
    """The refusal, as the lines the entry point prints. One text, one caller.

    A FUNCTION RATHER THAN A BLOCK IN THE GUARD so it can be driven by a test
    without starting two processes, and so the entry point's ``__main__`` block
    stays what this project's rule says it is.
    """
    return control.already_running_lines(
        exc,
        header="[Batch] REFUSING TO RUN: another batch run holds the lock.",
        record_keys=("pid", "host", "user", "started", "checkpoint_dir",
                     "record"),
        key_width=15,
        body=[
            "",
            "        Two runs against one checkpoint directory both read the "
            "same",
            "        resume state at start, so both process the SAME patients "
            "at one",
            "        live Stage 5 call each -- and the loser's completions are "
            "then",
            "        dropped from the checkpoint by the winner's next write, "
            "so a",
            "        third run re-bills those too. Nothing reports it.",
            "",
            "        Wait for the other run, or stop it cleanly:",
            f"            touch {describe_stop_switch_path()}",
            "",
            "        NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED.",
        ])


# ===========================================================================
# THE OPERATOR STOP SWITCH
# ===========================================================================
#
# WHAT IT IS FOR, AND WHY IT IS NOT A SIGNAL. A batch run is hours long and
# costs one live Stage 5 call per patient, so "stop this cleanly, I will resume"
# is an ordinary operational request. The two ways to make it before this
# existed were both wrong:
#
#   * Ctrl-C -- which needs a terminal the process is attached to, so it is
#     unavailable to anything running under nohup, screen, systemd, a
#     container or a cron entry; and
#   * SIGTERM -- which IS available to all of those and is deliberately an
#     ABRUPT stop: it is what an orchestrator sends when it is about to SIGKILL,
#     so it records the run KILLED, abandons in-flight billed requests mid-read,
#     and returns 143.
#
# Neither expresses "finish what you started, write it all down, and stop
# before the next patient". That is what this is: a file, so any user who can
# write to the checkpoint directory can ask for it, from any machine that
# shares the volume, with no pid to find and no signal to route.
#
# WHY A FILE AND NOT A DATABASE ROW OR A SOCKET. A row would put a poll on the
# hot write path and require the switch to be reachable through whatever
# ONCOTRIAGE_INFERENCES_DB currently resolves to -- so an operator would have
# to know which database this run is writing to in order to stop it. A socket
# is a port to allocate, a firewall to argue with and a second failure mode. A
# file in a directory this runner already owns and already writes to needs
# nothing that is not already true.
#
# WHY THE CHECKPOINT DIRECTORY SPECIFICALLY. It is the directory whose OTHER
# two files are this run's resumable state, so an operator who has just been
# told "safe to resume" is already looking at it; it is guaranteed writable,
# because the run writes a checkpoint into it after every patient; and it is
# per-deployment rather than per-repository, so a stop asked for on one host
# does not stop a run on another that happens to share a checkout.

STOP_FILENAME = "STOP"
"""The sentinel filename. Upper case so it cannot be mistaken for state.

The two files beside it in that directory are written BY the runner and read by
it; this one is the only file in the project an operator is expected to CREATE
by hand, and the name is shouted so that a directory listing says which is
which.
"""


def stop_switch_path() -> Path:
    """THE ONE OWNER of where the stop sentinel lives.

    ``_checkpoint_path()``'s shape, deliberately, and for a sharper version of
    its reason: an operator creates this file by hand, and every message that
    tells them where to put it -- the run banner, the refusal, the stop
    announcement, the entry point's ``--help`` -- has to name the SAME path. Two
    expressions of it is an operator writing a file the runner never reads,
    which looks exactly like a switch that does not work.

    ``paths.checkpoint_path`` as a MODULE ATTRIBUTE, not a from-import: the
    second form fires the lazy resolver at import and globs the sibling data
    tree just to load this module.
    """
    return Path(paths.checkpoint_path) / STOP_FILENAME


def describe_stop_switch_path() -> str:
    """The sentinel's path as a string, or a description when it cannot resolve.

    A RENDERER OVER ``stop_switch_path()``, NOT A SECOND EXPRESSION OF IT. The
    path still has exactly one owner; this only decides what to PRINT when
    asking the owner would raise.

    IT EXISTS BECAUSE TWO OF ITS CALLERS ARE ON SHUTDOWN PATHS.
    ``paths.checkpoint_path`` resolves the sibling data tree by glob on first
    read and RAISES on a machine that does not have it -- so an interrupt that
    arrived before anything had resolved a path would have had this message
    raise INSIDE the handler that was trying to explain the interrupt,
    replacing a clean exit with a traceback about globbing. An exception in an
    exception handler on a shutdown path is the one place a helpful message
    must not be able to fire from.

    The banner and the refusal deliberately do NOT use this: they run before
    anything has been spent, where a path that cannot resolve is a
    configuration defect that should reach the operator as itself.
    """
    try:
        return str(stop_switch_path())
    except Exception as exc:                                    # noqa: BLE001
        return (f"<the {STOP_FILENAME} file in the checkpoint directory; its "
                f"path could not be resolved here: {type(exc).__name__}>")


STOP_SWITCH_FAULTS = Counter()
"""Stop-switch faults, keyed ``{phase}:{ExceptionType}``.

Module-level, following ``CHECKPOINT_FAULTS`` and ``RESULTS_FILE_FAILURES``
above rather than becoming a column: this is a property of the RUN's control
files, and a patient row is the wrong place for it.

Phases:
    ``poll:``     the existence check itself raised. The switch is NOT tripped
                  by one of these -- see ``_StopSwitch.poll``.
    ``message:``  the file existed and its text could not be read. The switch IS
                  tripped; only the note is lost.
    ``preflight:`` the start-of-run check could not be made.
"""

degradation.register(
    "STOP_SWITCH_FAULTS", STOP_SWITCH_FAULTS,
    "the operator stop switch could not be read; a `poll:` key means the run "
    "may have kept going through a stop request, a `message:` key means only "
    "the operator's note was lost")




class StaleStopSwitch(RuntimeError):
    """The stop sentinel was already present before the run began.

    A ``RuntimeError`` subclass and deliberately not a ``ValueError`` or an
    ``OSError``, on ``UnknownModelPricingError``'s and
    ``IndexVerificationError``'s precedent: a stray ``except OSError`` around a
    path check must not be able to eat a refusal.
    """


class _StopSwitch(control.StopSwitch):
    """The batch runner's stop switch. ONE THING IS DECIDED HERE: the path.

    Everything else -- the latch, the lock, the "a poll that raises does not
    trip the switch" direction, the fault phases, the announcement written
    outside the lock -- is ``control.StopSwitch``, which is where each of those
    arguments now lives.

    THE PATH IS RESOLVED PER POLL RATHER THAN BOUND AT ``arm()``, which is the
    opposite of what the ablation study does and is right for both. This
    sentinel is a FIXED NAME in a FIXED directory, so resolving it costs one
    call to an owner that is already the single source of truth; the study's
    location depends on ``--db``, so it binds once in ``main()`` to keep the
    path an operator was TOLD to write and the path the study watches one
    reading rather than two.

    ``stop_switch_path()`` MAY RAISE -- it reads ``paths.checkpoint_path``,
    which globs the sibling data tree on first read -- and that is handled by
    ``control.StopSwitch.poll``'s ``except``, counted under ``poll:``, with the
    run continuing. Treating an unreadable directory as a stop request would
    silently cancel a paid campaign because a mount hiccuped.
    """

    def __init__(self):
        super().__init__(
            STOP_SWITCH_FAULTS,
            unit="patient",
            subject="the run",
            # "Noticed during the run." / "Noticed during the resample pass."
            # -- this program names a PASS, so it needs the article. The study
            # names a moment ("between configurations") and passes "".
            noticed_prefix="during the ",
            banner_width=80,
            default_where="run")

    def _resolve_path(self):
        return stop_switch_path()

    def arm(self, path):
        """REFUSED. This switch resolves its own path; there is nothing to bind.

        THE BASE CLASS OFFERS ``arm`` BECAUSE THE ABLATION STUDY NEEDS IT, and
        an inherited no-op here would be a REGRESSION rather than a harmless
        extra: before the consolidation this class had no ``arm`` at all, so
        ``STOP_SWITCH.arm(p)`` raised ``AttributeError`` -- loudly, at the call
        site. Inheriting it would have made the same call succeed, store a path
        this subclass's ``_resolve_path`` never reads, and leave the caller
        believing the switch was watching a file it was not. A silent no-op
        replacing a loud failure is the exact shape this project removes, so
        the loud failure is restored explicitly.
        """
        raise TypeError(
            "the batch runner's stop switch resolves its own path through "
            "stop_switch_path(); it cannot be armed. The ablation study's is "
            "armed because its sentinel's location depends on --db.")

    # `console.out` and `log.warning` are looked up HERE, at call time, rather
    # than captured in the constructor -- see `control.StopSwitch._emit` for the
    # measurement that made that the shipped shape.
    def _emit(self, line=""):
        if line:
            console.out(line)
        else:
            console.out()

    def _warn(self, message, **fields):
        log.warning(message, **fields)


def _read_stop_message(path) -> str:
    """The operator's note, capped, or None. NEVER RAISES.

    A THIN BINDING OF ``control.read_stop_message`` TO THIS PROGRAM'S COUNTER,
    and the binding is the whole of what is decided here: the bounded read, the
    tail probe and the truncation guard are one implementation in ``control``
    because they were written twice and hardened twice, while
    ``STOP_SWITCH_FAULTS`` stays per program because a batch fault and a study
    fault are different findings and one number covering both would report them
    as one.
    """
    return control.read_stop_message(path, STOP_SWITCH_FAULTS)


STOP_SWITCH = _StopSwitch()
"""The one instance. See ``control.StopSwitch`` for why it is module-level and
reset, and ``_StopSwitch`` below for the one thing this program decides."""




def clear_stop_switch() -> str:
    """Delete the sentinel. Returns a ``control.STOP_CLEAR_*`` member. Used by
    ``--clear-stop``.

    A SEPARATE FUNCTION FROM ``clear_checkpoint`` AND NOT FOLDED INTO
    ``clear_all``, because the two clear opposite things: ``clear_all`` discards
    a run's RESULTS and re-bills the cohort, and this discards a CONTROL FILE
    and costs nothing. An operator who wants to resume after a stop wants
    exactly this and must not be within one flag of the other.

    THE MECHANISM IS ``control.clear_stop_switch`` -- the never-raises contract,
    the ``Exception``-rather-than-``OSError`` clause, the ``clear:`` counter
    phase and the re-described path all have their arguments there. What is
    supplied here is this program's four facts: where the sentinel is, how to
    describe it when resolving would raise, whose counter to charge, and what a
    permission error usually means for THIS state directory.
    """
    return control.clear_stop_switch(
        stop_switch_path, describe_stop_switch_path, STOP_SWITCH_FAULTS,
        unit="patient", out=console.out,
        remediation="[STOP]   A permission error here usually means the "
                    "checkpoint directory is read-only or owned by another "
                    "user; `ls -ld` it.")


def assert_no_stale_stop_switch() -> None:
    """Refuse to start while the sentinel is already there.

    Raises:
        StaleStopSwitch: it is present.

    WHY THIS IS A REFUSAL AND NOT A NO-OP. Without it the switch is a trap that
    fires once and then silently every time: the run that honoured it leaves the
    file behind (deliberately -- see ``clear_stop_switch``), so the NEXT
    invocation would trip on the first completed patient, cancel the rest, and
    report a campaign that stopped for a reason nobody asked for that day. On a
    cron entry or a restart loop that is a cohort that never advances while
    every run reports success.

    Stopping BEFORE the first patient rather than after one is what makes the
    message actionable: nothing has been billed, nothing has been written, and
    the fix is one ``rm``.

    A CHECK THAT RAISES IS NOT COUNTED AS A STOP. If the existence test itself
    fails the run proceeds -- ``_StopSwitch.poll``'s direction, for its reason --
    with the failure counted under ``preflight:``.
    """
    try:
        path = stop_switch_path()
        present = path.exists()
    except Exception as exc:                                    # noqa: BLE001
        STOP_SWITCH_FAULTS[f"preflight:{type(exc).__name__}"] += 1
        console.out(f"[STOP] WARNING: the stop switch could not be checked "
                    f"({type(exc).__name__}: {exc}). Continuing.")
        return
    if not present:
        return

    note = _read_stop_message(path)
    raise StaleStopSwitch("\n".join(
        [f"REFUSED (stop switch present): {path}",
         "    A stop sentinel is already in the checkpoint directory, so this "
         "run would stop again at its first completed patient -- for a request "
         "that was made before it started.",
         f"    Note in the file: {note}" if note else
         "    The file is empty, which is the ordinary `touch STOP` form.",
         "    NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED.",
         "    To run: delete it and start again.",
         f"        rm {path}",
         "        python \"25- Batch Runner.py\"",
         "    or, in one command:",
         "        python \"25- Batch Runner.py\" --clear-stop"]))


def _start_patient_unless_stopped(**kwargs):
    """The submitted callable. Refuses to begin work once the switch has tripped.

    WHY THIS EXISTS WHEN ``_cancel_queued`` ALREADY DOES THE JOB. Cancellation
    is a sweep, and a sweep has an edge: the switch latches inside a
    done-callback on a worker thread, and the submit loop on the MAIN thread
    polls once per patient -- so between the latch and the loop's next poll
    exactly ONE more future can be submitted, and it is submitted AFTER the
    sweep that would have cancelled it. A worker that picks it up in that
    window starts a patient, and one live billed Stage 5 call, after the
    operator asked the run to stop.

    ONE PATIENT IS A SMALL BOUND AND IT IS NOT THE POINT. "No further patient
    is started" is the contract this switch is documented by; a contract with
    an unstated edge is the class of defect this project exists to remove, and
    closing it costs one attribute read per patient.

    ``STOP_SWITCH.requested`` AND NOT ``poll()``: a plain attribute read, so
    this adds no filesystem call to the hot path. It cannot miss a stop that
    matters either -- the value it reads is set by the sweep that is already
    cancelling this future's siblings.

    IT RAISES ``CancelledError`` RATHER THAN RETURNING A RESULT, because that
    is what already means "this patient was never attempted" at BOTH consumers:
    ``_on_done``'s own CancelledError branch counts it as cancelled rather than
    as an error and advances the bar, and the drain loop tolerates it. A
    returned entry would be appended to the results list and counted as a
    patient that ran.
    """
    if STOP_SWITCH.requested:
        raise CancelledError(
            "the operator stop switch tripped before this patient started")
    return process_patient(**kwargs)


# ===========================================================================
# RESULTS HELPERS
# ===========================================================================

RESULTS_FILE_FAILURES = Counter()
"""Results-file faults, keyed ``{phase}:{ExceptionType}``.

Module-level, following ``CHECKPOINT_WRITE_FAILURES`` in
``oncotriage/ablation/study.py`` and ``INFERENCE_WRITE_FAILURES`` in the storage
layer rather than becoming a column: this is a property of the RUN's state files,
and there is no patient row it belongs to -- the whole point is that the fault
happens BEFORE the first patient of a resumed run.

Phases:
    ``load:``      the file existed and could not be read back
    ``shape:``     it parsed, and was not a JSON array
    ``preserve:``  the unreadable file could not be renamed out of the way, so
                   the next write WILL destroy it. The most serious key here.
    ``write:``     the results file could not be WRITTEN, so the report on disk
                   and the artifact ``tracking.end_run`` attaches are stale or
                   absent. Fires once per patient while the condition lasts.
    ``tmp_unlink:`` a failed write left its temporary file behind
"""

CORRUPT_RESULTS_SUFFIX = ".corrupt"
"""Suffix the unreadable results file is renamed to before anything can replace it."""


# THE COUNTER JOINS THE REGISTRY HERE, not in oncotriage/degradation.py, and the
# direction of the import is why: this module CALLS the degradation report, so
# that module cannot import this one back without a cycle. Registration at the
# owner's module scope is the documented second route, and it is a dict insert
# -- no file, no client, no process -- so "importing a package module does
# nothing" is intact. It sits immediately below the counter it registers, so a
# reader cannot find one without the other.
degradation.register(
    "RESULTS_FILE_FAILURES", RESULTS_FILE_FAILURES,
    "the per-patient results FILE could not be read, was not an array, or "
    "could not be preserved; the checkpoint is unaffected and no patient was "
    "re-run because of it")


class ResultsLoad(list):
    """The loaded results, plus whether they were loaded.

    A ``list`` SUBCLASS, and forced rather than clever -- the same shape and the
    same reason as ``InferenceWriteResult`` being a ``str`` subclass in the
    storage layer. ``main()`` hands this object to ``run_batch``,
    ``run_resample`` and ``print_summary``, and ``append_result`` MUTATES IT IN
    PLACE, so the object identity is the thread the whole run hangs on. A tuple
    return would have meant unpacking at one call site and threading a second
    variable through three signatures to carry one fact.

    Because it is mutated in place, ``print_summary`` reads the outcome off the
    same object it is already given -- the fact travels with the data rather
    than as a parallel argument that can be forgotten.

    ``ok`` is FALSE ONLY FOR "COULD NOT LOAD", never for "loaded nothing". An
    empty results file, a first run with no file at all, and a file of two
    hundred entries are all ``ok=True``; only a decode error, an OS error or a
    non-array payload is ``ok=False``. That distinction is the return-contract
    half of this fix: before it, all four cases returned ``[]``.
    """

    __slots__ = ("ok", "error", "preserved_path", "source_path")

    def __new__(cls, entries, **kwargs):
        return super().__new__(cls, entries)

    def __init__(self, entries, ok=True, error=None, preserved_path=None,
                 source_path=None):
        super().__init__(entries)
        self.ok = ok
        self.error = error
        self.preserved_path = preserved_path
        self.source_path = source_path


def _preserve_corrupt_results(rp) -> tuple:
    """Rename an unreadable results file out of the way. Returns (path, error, key).

    BEFORE ANY WRITE CAN REPLACE IT, which is the point. ``append_result`` does
    write-temp-then-``os.replace``, so the FIRST patient of a resumed run
    overwrote the unreadable file with a one-entry list and every prior
    patient's results were gone -- irrecoverably, silently, and while the run
    reported success.

    THE MECHANISM MOVED TO ``oncotriage/utils.py:preserve_corrupt_file`` and
    this is the results file's name for it. It moved because the batch and
    ablation CHECKPOINTS need the identical treatment for the identical reason
    (``save_checkpoint`` also does temp-then-replace), and three copies of a
    find-a-free-suffix-then-rename loop is two more chances for one of them to
    stop preserving. What is left here is the suffix this file owns, so a
    reader of the results path still finds the sidecar named where it is used.
    The numbered-collision rule, the returned-not-raised rename failure and the
    counter key are argued there.
    """
    return preserve_corrupt_file(rp, CORRUPT_RESULTS_SUFFIX)


def load_results() -> ResultsLoad:
    """
    Load existing per-patient results from results file.

    Returns:
        A ``ResultsLoad`` -- a list of result summary dicts carrying ``ok``,
        ``error`` and ``preserved_path``. Empty AND ``ok=True`` when the file
        does not exist; empty and ``ok=False`` when it existed and could not be
        read, in which case the unreadable file has been renamed to a
        ``.corrupt`` sidecar and ``preserved_path`` names it.

    WHAT THIS USED TO DO, AND THE FULL CONSEQUENCE. It caught
    ``(json.JSONDecodeError, OSError)`` and returned ``[]`` with no log, no
    counter and no trace. On a RESUME that is not a degraded read, it is
    permanent data loss in three steps: the results list comes back empty, the
    final summary is then computed over this session's patients only (so a run
    that resumed at patient 19,000 reports statistics for the last 3,000 and
    looks like a small clean run), and the first ``append_result`` REPLACES the
    unreadable file with a one-entry list -- destroying every prior patient's
    results, including the ones that were still readable JSON before whatever
    truncated the tail.

    WHAT DOES NOT CHANGE: the checkpoint. ``load_checkpoint()`` is untouched and
    is still the only thing that decides what re-runs. The results file is a
    report; the checkpoint is the ledger. A corrupt report must not cause 19,000
    patients to be re-run at ~$0.15 each, and this function deliberately does
    not touch the thing that would.

    WHAT THE CALLER DOES DIFFERENTLY, per case:

        file absent           ok=True,  []           -- unchanged; fresh run
        file is []            ok=True,  []           -- unchanged; nothing to resume
        file is [...]         ok=True,  [...]        -- unchanged
        unreadable            ok=False, []           -- NEW: the file is renamed
                                                        to .corrupt, the fault is
                                                        logged and counted, and
                                                        print_summary states that
                                                        prior results were not
                                                        loaded, so the per-pass
                                                        statistics below it are
                                                        read as partial
    """
    rp = _results_path()
    if not rp.exists():
        return ResultsLoad([], source_path=str(rp))

    try:
        with open(rp, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        return _unreadable_results(rp, f"{type(e).__name__}: {e}",
                                   f"load:{type(e).__name__}")

    # A payload that parses and is not a list is unreadable for this purpose:
    # `results_list.append(...)` on a dict raises inside the thread pool, and
    # `[r for r in results_list ...]` in print_summary would iterate its keys.
    # Same treatment, its own phase key, because "truncated file" and "somebody
    # wrote a different shape here" want different fixes.
    if not isinstance(data, list):
        return _unreadable_results(
            rp, f"expected a JSON array, found {type(data).__name__}",
            f"shape:{type(data).__name__}")

    return ResultsLoad(data, source_path=str(rp))


def _unreadable_results(rp, error: str, counter_key: str) -> ResultsLoad:
    """Count, preserve, log, and return the could-not-load answer.

    Shared by both unreadable branches so the counter key is the only thing
    that differs between them -- two copies of this sequence is two chances for
    one of them to stop preserving the file.
    """
    RESULTS_FILE_FAILURES[counter_key] += 1
    preserved, preserve_error, preserve_key = _preserve_corrupt_results(rp)

    if preserved:
        # Named like load_checkpoint's warning, which is the shape this branch
        # should always have had.
        console.out(f"[Results] WARNING: Could not read results file ({error}). "
                    f"The unreadable file has been preserved as {preserved} and "
                    f"this run starts its results list empty. THE CHECKPOINT IS "
                    f"UNAFFECTED: no patient will be re-run because of this.")
    else:
        RESULTS_FILE_FAILURES[f"preserve:{preserve_key}"] += 1
        console.out(f"[Results] WARNING: Could not read results file ({error}), "
                    f"AND could not preserve it ({preserve_error}). The next "
                    f"write WILL overwrite it and its contents will be lost. "
                    f"Copy {rp} elsewhere now if it matters. THE CHECKPOINT IS "
                    f"UNAFFECTED.")

    log.warning("results file could not be loaded", event="results_load_failed",
                status="degraded", error_message=error, db_path=str(rp))

    return ResultsLoad([], ok=False, error=error, preserved_path=preserved,
                       source_path=str(rp))


def append_result(results_list: list, entry: dict) -> None:
    """
    Append one patient result entry and atomically persist to disk.

    Uses a temp file + os.replace() so a crash mid-write never leaves
    a partially written or corrupt results file.

    Args:
        results_list: In-memory list of result dicts (mutated in-place).
        entry:        Single patient result summary dict.
    """
    with _results_lock:
        results_list.append(entry)
        rp = _results_path()
    
        tmp_path = rp.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(results_list, f, indent=2)
            os.replace(tmp_path, rp)
        except OSError as e:
            # COUNTED, NOT ONLY PRINTED. The results file is the run's own
            # report and `tracking.end_run` attaches it as an artifact, so a
            # run whose every write failed indexes an artifact that is stale or
            # absent while reporting nothing wrong. Same phase key as the
            # checkpoint's, for the same reason: one convention across the two
            # state files this module owns.
            RESULTS_FILE_FAILURES[f"write:{type(e).__name__}"] += 1
            console.out(f"[Results] WARNING: Could not write results file ({e}). Continuing.")
            # Clean up temp file if it was created
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError as unlink_exc:
                    RESULTS_FILE_FAILURES[
                        f"tmp_unlink:{type(unlink_exc).__name__}"] += 1
                    console.out(f"[Results] WARNING: the temporary file "
                                f"{tmp_path} could not be removed "
                                f"({unlink_exc}).")


# ===========================================================================
# THE HEALTH FLUSH (the health-persistence pass)
# ===========================================================================
#
# WHAT IT IS FOR. oncotriage/degradation.py's counters are per-PROCESS and are
# read in exactly one place -- the block main() prints at the end. So a campaign
# that CRASHES prints nothing, and the health record of everything it did before
# it died dies with it; and nothing outside the process can ask a LIVE run how
# it is going. Persisting the totals per run turns both of those into a query.
#
# WHERE IT IS HOSTED, AND WHY NOT WHERE THE BRIEF SUGGESTED. The natural-looking
# host is save_checkpoint() in _on_done -- it is already the per-patient
# completion point. It is the wrong one, and measurably so: save_checkpoint is
# inside `if entry["status"] == "success":`, so a pass in which every patient
# ERRORED would flush nothing at all. Errors are exactly when REFUSALS_OBSERVED,
# MALFORMED_EVALUATION_ENTRIES and INFERENCE_WRITE_FAILURES move, so hanging the
# health record off the success branch would make it emptiest precisely when it
# matters most -- silence looking like health, which is the defect the whole
# feature exists to remove. It is called from _on_done for BOTH outcomes
# instead, immediately after the entry is recorded.
#
# EVERY PATIENT, NOT EVERY N. MEASURED rather than assumed, on this machine,
# against a scratch database in the production database's own directory (so the
# filesystem and the WAL behaviour are the real ones), with all 24 counters
# non-zero and a 50,000-row history for the DELETE to scan past:
#
#     0.492 ms per flush, mean over 300     with idx_run_metrics_run_id
#     1.212 ms per flush, mean over 200     with the index dropped
#
# 0.492 ms against a per-patient pipeline whose measured median is ~68 seconds
# of Stage 5 alone is seven parts in a million. save_checkpoint() already writes
# a JSON file on the same path for the same patients and costs more than this
# does. A cadence knob was considered and rejected: its only safe value is 1,
# and a knob whose other values silently reduce the fidelity of a crash record
# is a way to lose the record.
#
# WHAT IT DOES NOT COVER, stated: _on_done's two early returns. A callback that
# fails at future.result() -- a MatchingModelMismatchError, or any other
# exception escaping the worker -- returns before this line, so a run in which
# EVERY patient failed that way flushes nothing until main()'s final flush. That
# final flush always runs, on the success path and on the crash path alike, so
# nothing is lost; only the liveness of the record is.


def flush_health(run_id, snapshot=None, db_path=None) -> bool:
    """Persist the degradation registry's totals for ``run_id``. Never raises.

    Args:
        run_id:   the ``runs.id`` this run is recorded under. ``None`` is
                  tolerated and counted by the writer -- a caller driving a pass
                  outside main() has no run row, and a health flush must not be
                  the thing that stops it.
        snapshot: ``degradation.snapshot()``'s dict, or ``None`` to take one
                  now. THE ARGUMENT IS THE WHOLE POINT AT THE END OF A RUN: the
                  final flush, the printed report and the logged summary must
                  describe one instant, and the only way to guarantee that is
                  for one snapshot to be taken once and handed to all three --
                  exactly as the reconciliation dict already is.
        db_path:  which database. ``None`` resolves the configured one, which is
                  what ``log_inference`` does from the same worker threads; the
                  two therefore name one file by construction.

    THE TOTALS, NEVER THE SNAPSHOT. ``degradation.totals()`` is
    ``{counter name: int}``; ``snapshot()`` is the nested form and its KEYS carry
    clinical and third-party text -- a patient's recorded sex, a capped copy of
    an observation display -- which must not reach a durable, run-keyed table.
    The writer refuses the nested form rather than trusting this call site; see
    ``_run_metric_rows``.

    ``len(registered_names())`` IS THE SECOND ARGUMENT, and it is what makes a
    clean run distinguishable in SQL from a run that never flushed. ``totals()``
    drops every zero counter, so a healthy run contributes no `degradation` rows
    at all -- which is what "nothing was ever wired up" also looks like.

    WHAT CONCURRENCY GUARANTEES HERE, AND WHAT IT DOES NOT -- stated rather than
    left for a reader to assume. The WRITE is atomic: ``_WRITE_LOCK`` plus one
    transaction means a reader sees the previous flush or this one and never a
    mixture, and two workers cannot duplicate or half-erase a run's rows. The
    SNAPSHOT is taken HERE, outside that lock, so the freshness is weaker: two
    workers can snapshot at 5 and 6 events and land in the other order, leaving
    the table reading 5 for as long as it takes the next patient to finish
    (~68 seconds at this pipeline's measured median). It self-heals on the next
    flush, and main()'s final flush is authoritative.
    That is a bounded staleness, not a corruption, and it is the deliberate
    trade for not holding a database lock across a registry read that every
    worker thread is writing to. A reader that needs to know how current a row
    is has ``run_metrics.written_at`` for exactly that.
    """
    # THE REGISTRY READ IS INSIDE THE GUARD TOO, and that is not padding.
    # flush_run_metrics never raises by contract, but the two calls that build
    # its arguments are OUTSIDE it -- and this whole function runs in a
    # done-callback on a worker thread, where an exception is swallowed by
    # concurrent.futures and logged to a logger nothing in this project reads.
    # "The flush never raises" has to mean the call site, not one frame of it.
    #
    # IT COUNTS INTO THE STORAGE LAYER'S COUNTER RATHER THAN A NEW ONE OF ITS
    # OWN, deliberately: the fact an operator acts on is "this run's health
    # record did not land", and splitting it across two counters by which half
    # of the call failed would put one event in two places on the report. The
    # KEY says which half.
    try:
        snap = degradation.snapshot() if snapshot is None else snapshot
        totals = degradation.totals(snap)
        registered = len(degradation.registered_names())
    except Exception as exc:                                   # noqa: BLE001
        RUN_METRICS_FLUSH_FAILURES[f"flush:registry_read:{type(exc).__name__}"] += 1
        log.error("the degradation registry could not be read, so this run's "
                  "health record was not updated",
                  event="run_metrics_registry_read_failed",
                  inference_run_id=run_id,
                  error_type=type(exc).__name__, error_message=str(exc))
        return False

    return flush_run_metrics(run_id, totals, registered, db_path=db_path)


# ===========================================================================
# CORE: PROCESS A SINGLE PATIENT
# ===========================================================================

def process_patient(
    fhir_path: str,
    graph: object,
    is_resample: bool = False,
    run_id=None,
    db_path=None,
) -> dict:
    """
    Run the full pipeline for one patient file.

    Mirrors FastAPI's _run_matching_pipeline() exactly:
        parse_fhir_bundle -> match_patient_to_trials -> log_inference

    Args:
        fhir_path:   Absolute path to patient FHIR JSON bundle file.
        graph:       Compiled LangGraph StateGraph (shared, read-only).
        is_resample: True when this is a resample re-run of an existing patient.
        run_id:      The `runs.id` main() opened for THIS invocation, threaded
                     down as an argument and never read off a module global.

                     A DEFAULT OF None IS A REAL CASE, not a convenience: a
                     caller driving one patient outside a campaign has no run
                     to attach to, and NULL in the column says exactly that.
                     What the default must NOT become is a fallback that looks
                     one up -- see log_inference's `run_id` docstring for why a
                     module-level "current run" would attribute a second
                     campaign's rows to the first one's row.

    Returns:
        Dict with keys: patient_id, status, eligible_matches, near_misses,
        not_evaluable, total_time, error, is_resample.

    Raises:
        MatchingModelMismatchError: and ONLY that. Every other exception is
            caught and returned as an error entry, which is what lets one bad
            patient not take the batch with it. This one is a condition of the
            run rather than of the patient -- the judging model changed
            underneath it -- so it is allowed through. See the handler for the
            full reasoning, and the executor block in run_batch() for what
            "allowed through" actually achieves, which is less than it sounds.
    """
    patient_file = Path(fhir_path)
    patient_id_hint = patient_file.stem  # filename without extension as fallback ID

    run_label = "[RESAMPLE]" if is_resample else "[PATIENT]"

    # Assigned before try so except block can always reference them
    # without guarding against NameError or using unreliable locals() checks.
    start = time.time()
    timestamp = datetime.now().isoformat()

    try:
        patient_data = parse_fhir_bundle(str(fhir_path))

        if not patient_data or not patient_data.get("patient_id"):
            return {
                "patient_id": patient_id_hint,
                "status": "error",
                "eligible_matches": 0,
                "near_misses": 0,
                "not_evaluable": 0,
                "total_time": round(time.time() - start, 3),
                "timestamp": timestamp,
                "error": "parse_fhir_bundle returned no patient_id",
                "is_resample": is_resample,
            }

        patient_id = patient_data["patient_id"]

        result = match_patient_to_trials(
            patient_data=patient_data,
            graph=graph,
        )

        # THE RETURN VALUE IS READ NOW, and before the write-durability pass it
        # was discarded. log_inference does not raise when the row is lost -- by
        # design, so a logging fault cannot destroy a result that cost a live
        # Stage 5 call -- so the outcome it reports is the only channel by which
        # the loss leaves this worker thread. record_write puts it in the ledger
        # that reconcile_writes reads at the end of the run.
        # run_id AND db_path are both forwarded and NEITHER is resolved here.
        #
        # THE COMMENT THIS REPLACES CLAIMED THE PROPERTY IT BROKE. It read
        # "db_path is still left to log_inference so the row and the
        # reconciliation read the same resolution" -- and leaving it to
        # log_inference is exactly what made that false. `resolve_inference_db_path`
        # consults ONCOTRIAGE_INFERENCES_DB at CALL time, so this call resolved
        # once per patient while main() resolved once at the top; change the
        # variable mid-run and the patient rows land in one file, the `runs` row
        # and the health flushes in another, and every `run_id` in the new file
        # names a run that is not there. The reconciliation would then report
        # every row of the run as lost, which is a true statement about the file
        # it read and a false one about the work.
        #
        # `dangling_run_references` in oncotriage/storage/queries.py is the
        # standing detector for the rows a run in that state leaves behind.
        write_result = log_inference(result, patient_data, run_id=run_id,
                                     db_path=db_path)
        record_write(patient_id, write_result, is_resample)

        # DELIBERATELY NOT FOLDED INTO `status`. A lost row means the DATABASE is
        # incomplete; it does not mean the pipeline failed for this patient, and
        # the two are separately actionable. Conflating them would also change
        # what gets checkpointed -- a non-"success" status skips
        # save_checkpoint(), so a database hiccup would silently re-queue a
        # patient for a second paid Stage 5 call on the next resume. The
        # reconciliation block reports it instead, and main() makes the RUN
        # report incomplete.
        db_row_written = getattr(write_result, "ok", None)

        elapsed = round(time.time() - start, 3)

        eligible_count = len(result.get("matches", []))
        near_miss_count = len(result.get("near_misses", []))
        # Reported separately: a trial that could not be evaluated is not a
        # rejection, so it must not be folded into the near-miss count.
        not_evaluable_count = len(result.get("not_evaluable", []))
        error_msg = result.get("error", "")

        status = "error" if error_msg else "success"

        console.out(
            f"  {run_label} {patient_id} | "
            f"eligible={eligible_count} near_miss={near_miss_count} "
            f"not_evaluable={not_evaluable_count} | "
            f"{elapsed:.1f}s | {status}"
            + ("" if db_row_written is not False else " | DB WRITE LOST")
        )

        return {
            "patient_id": patient_id,
            "status": status,
            "eligible_matches": eligible_count,
            "near_misses": near_miss_count,
            "not_evaluable": not_evaluable_count,
            "total_time": elapsed,
            "timestamp": timestamp,
            "error": error_msg,
            "is_resample": is_resample,
            # None means the writer did not report -- see record_write. Written
            # into the results JSON so a run's per-patient record says whether
            # its row landed, which is otherwise only in the ledger and dies
            # with the process.
            "db_row_written": db_row_written,
        }

    except MatchingModelMismatchError:
        # NOT a per-patient failure, so not absorbed into a per-patient error
        # row. Stage 5 raises this when the string the API answers with differs
        # from MATCHING_MODEL -- an alias that has resolved to a new snapshot
        # mid-run. Every patient after that point is judged by a different
        # model than every patient before it, and a corpus split down the
        # middle by an invisible model change is the confound this project
        # exists to remove: the rows are individually well-formed and
        # collectively meaningless.
        #
        # Catching it here would produce exactly the failure the guard was
        # written to prevent -- a batch grinding on, printing one EXCEPTION per
        # patient, for as long as the run had left. Files 26 and 13 carry the
        # same carve-out, File 26's alongside the older one for
        # UnknownModelPricingError, which is not absorbed for the same reason:
        # it is a condition of the RUN, not of the patient.
        #
        # Re-raised bare rather than wrapped: File 13's message already carries
        # both model strings and what to do about them.
        #
        # READ THE COMMENT AT THE EXECUTOR BELOW BEFORE ASSUMING THIS STOPS THE
        # BATCH. It does not, and where it stops instead is written down there.
        raise

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        console.out(f"  {run_label} {patient_id_hint} | EXCEPTION: {error_msg}")
        return {
            "patient_id": patient_id_hint,
            "status": "exception",
            "eligible_matches": 0,
            "near_misses": 0,
            "not_evaluable": 0,
            "total_time": round(time.time() - start, 3),
            "timestamp": timestamp,
            "error": error_msg,
            "is_resample": is_resample,
        }


# ===========================================================================
# MID-RUN MODEL CHANGE: ANNOUNCING IT ONCE
# ===========================================================================


class _DriftAnnouncer:
    """One-shot, thread-safe announcement that the judging model changed.

    WHY THIS EXISTS. Item 29c established that a MatchingModelMismatchError
    raised inside a worker does NOT stop the batch: every future is submitted
    up front, and ThreadPoolExecutor.__exit__ calls shutdown(wait=True), which
    drains all of them before the exception propagates out of run_batch(). So
    on a full corpus the operator watches a drain that may run for hours. Both
    done-callbacks used to render that as a stream of generic
    "[CALLBACK ERROR] MatchingModelMismatchError: ..." lines, one per remaining
    patient -- which is the same as no message at all, because a line repeated
    nine hundred times is scrolled past, not read.

    WHY ONCE. Repetition is the failure mode being fixed, so suppressing the
    repeat is the whole point. The count of affected patients is not lost: they
    are counted as errors in the pass summary, and no result row is appended for
    any of them.

    WHY A LOCK. The callback runs on worker threads. A bare `if not flag: flag =
    True; print(...)` is a read-modify-write across threads, so two workers that
    hit the mismatch in the same instant can both pass the test and both print.
    A duplicate warning would be harmless -- this is a message, not a mutation --
    but the lock costs nothing on a path that fires at most once per run, and it
    makes the behaviour deterministic, which is what lets it be TESTED. An
    assertion that "the message appears exactly once" against a racy flag would
    be a flaky test, and a flaky test is worse than no test.

    WHAT THIS IS NOT. It is not a cancellation mechanism and must not become
    one. Nothing reads `_announced` to decide whether to do work; it gates
    printing and nothing else. Stopping the drain needs a cooperative flag that
    submitted-but-not-started tasks check, and that design belongs to item 44.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._announced = False

    def announce(self, exc) -> bool:
        """Print the drift banner if it has not been printed. Returns whether
        this call was the one that printed it."""
        with self._lock:
            if self._announced:
                return False
            self._announced = True

        # Written through console.banner() rather than a bare write: while a
        # progress bar is live every line has to go through tqdm.write or the
        # bar's redraw overwrites it. That used to be arranged by hijacking
        # builtins.print; it is now a property of the console channel, which
        # the bar registers itself with in run_batch() and run_resample().
        #
        # .requested and .returned are attributes File 13 puts on the exception
        # (item 29b). Read defensively anyway: this runs on the failure path of
        # a failure path, and an AttributeError here would replace the message
        # with a traceback nobody asked for.
        requested = getattr(exc, "requested", "<not recorded>")
        returned = getattr(exc, "returned", "<not recorded>")
        console.banner(
            "",
            "!" * 80,
            "!!  JUDGING MODEL CHANGED MID-RUN — THIS RUN'S RESULTS MUST NOT BE USED",
            "!" * 80,
            f"!!  requested : {requested}",
            f"!!  answered  : {returned}",
            "!!",
            "!!  Every patient judged before this point used a different model than",
            "!!  every patient judged after it, so the corpus is split down the middle",
            "!!  by a change nothing in the request asked for.",
            "!!",
            "!!  The batch is now DRAINING: all remaining patients were queued before",
            "!!  this was detected and will still run. They will fail the same way and",
            "!!  write no rows. This banner is printed ONCE; the per-patient failures",
            "!!  are counted in the pass summary.",
            "!!",
            f"!!  Set MATCHING_MODEL in 'oncotriage/config.py' to {returned!r} only after",
            "!!  reviewing what changed, add it to PRICING_CONFIG, and re-baseline.",
            "!" * 80,
            "",
        )
        return True


# ===========================================================================
# BATCH LOOP
# ===========================================================================


def run_batch(fhir_files: list, bm25_index: object, nct_ids: list, graph: object, completed_ids: set, results_list: list, run_id=None, db_path=None,) -> tuple:
    """
    Process all patients not already in completed_ids using concurrent threads.

    Patients are processed in parallel with MAX_WORKERS threads. After each
    successful patient, the checkpoint is updated so a crash loses at most
    a few patients' work (one per active thread).

    Args:
        fhir_files:    List of absolute FHIR file path strings.
        bm25_index:    Pre-built BM25Okapi index (read-only, shared).
        nct_ids:       NCT ID list aligned with BM25 index (read-only, shared).
        graph:         Compiled LangGraph StateGraph (read-only, shared).
        completed_ids: Set of already-completed filename stem strings (mutated).
        results_list:  In-memory results list (mutated via append_result).
        run_id:        The `runs.id` this pass's writes belong to, forwarded to
                       every worker. None means the writes carry NULL, which is
                       what a caller driving this function outside main() gets.

    Returns:
        Tuple of (completed_ids, main_pass_complete).
    """
    pending_files = [
        f for f in fhir_files
        if Path(f).stem not in completed_ids
    ]

    total_pending = len(pending_files)
    total_files = len(fhir_files)
    already_done = total_files - total_pending

    console.out("=" * 80)
    console.out("MAIN BATCH PASS")
    console.out("=" * 80)
    console.out(f"Total patient files:    {total_files}")
    console.out(f"Already completed:      {already_done}")
    console.out(f"Remaining to process:   {total_pending}")
    console.out(f"Concurrent workers:     {MAX_WORKERS}")
    console.out()

    if not pending_files:
        console.out("All patients already completed. Skipping main pass.")
        return completed_ids, True

    batch_success = 0
    batch_error = 0
    # Cancelled != errored: see the CancelledError branch below.
    batch_cancelled = 0

    # Keep the progress bar prominent
    console.out()
    console.out("*" * 80)
    progress = tqdm(
        total=len(pending_files),
        desc="🔬 MAIN PASS PROGRESS",
        unit="patient",
        bar_format="{desc}: {percentage:3.0f}%|{bar:40}| {n_fmt}/{total_fmt} [Elapsed: {elapsed} | ETA: {remaining} | {rate_fmt}] {postfix}",
        ncols=120,
        smoothing= 0.1 # to reduce the eta fluctuation 
    )

    # Local to this pass, so run_batch() and run_resample() each announce once
    # rather than sharing a flag across two passes. NOT read by any worker --
    # see the class docstring.
    _drift = _DriftAnnouncer()

    # THE FUTURES LIST IS BOUND BEFORE `_on_done` RATHER THAN AT THE SUBMIT
    # LOOP, and that is required rather than tidy: `_on_done` closes over it to
    # cancel the queue when the stop switch trips, and a name bound later in the
    # enclosing scope would be an UnboundLocalError the first time a patient
    # completed before the loop had run -- which is to say, never in testing and
    # once in production.
    futures = []

    # THE STOP SWEEP HAPPENS ONCE, and this is what makes that true across
    # MAX_WORKERS done-callbacks arriving at the same instant. The switch itself
    # latches, so every worker after the first sees `requested` and would sweep
    # again; a second sweep cancels nothing new (`Future.cancel()` on an
    # already-cancelled future returns True without changing it) but would
    # report the same count a second time, so the count would read as twice the
    # work that was actually cancelled.
    _stop_sweep_lock = threading.Lock()
    stop_sweep_done = False

    def _on_done(future, fhir_path):

        nonlocal batch_success, batch_error, batch_cancelled
        nonlocal stop_sweep_done
        try:
            entry = future.result()
        except CancelledError:
            # A CANCELLED PATIENT WAS NEVER ATTEMPTED, AND IS NOT AN ERROR.
            # `concurrent.futures.CancelledError` subclasses Exception (it is
            # `futures.Error`, not asyncio's BaseException-derived one --
            # MEASURED, because the two are different classes with the same
            # name), so the generic handler below used to absorb it: an
            # interrupt printed one "[CALLBACK ERROR] CancelledError:" line per
            # queued patient and reported them all as failures. On a 22,000-
            # patient corpus interrupted early that is 22,000 lines and a
            # summary claiming 22,000 errors for work nobody ran.
            #
            # IT BECAME REACHABLE WHEN THE POOL STARTED CANCELLING. Before the
            # executor lifecycle changed, the `with` form drained every queued
            # future, so no future was ever cancelled and this branch would
            # have been dead code -- which is why it is added in the same pass
            # rather than earlier.
            #
            # The bar is still advanced: it is sized to the whole pass, and a
            # cancelled patient that never advances it leaves the run looking
            # stalled at the moment it is shutting down.
            batch_cancelled += 1
            progress.update(1)
            return
        except MatchingModelMismatchError as e:
            # Counted and progressed exactly like any other failure -- the bar
            # must not stall during the drain -- but announced once, loudly,
            # instead of once per patient as a generic callback error.
            batch_error += 1
            progress.update(1)
            _drift.announce(e)
            return
        except Exception as e:
            batch_error += 1
            progress.update(1)
            console.out(f"  [CALLBACK ERROR] {type(e).__name__}: {e}")
            return
        
        append_result(results_list, entry)

        if entry["status"] == "success":
            batch_success += 1
            completed_ids.add(Path(fhir_path).stem)
            save_checkpoint(completed_ids)
        else:
            batch_error += 1

        # THE HEALTH FLUSH, OUTSIDE THE SUCCESS BRANCH ON PURPOSE. See
        # flush_health: hanging it off save_checkpoint() would leave a pass in
        # which every patient errored with no persisted health record, and the
        # error path is exactly where the counters move. It never raises -- an
        # exception here would be swallowed by concurrent.futures and logged
        # where nothing in this project reads it -- and it runs on MAX_WORKERS
        # threads at once, which _WRITE_LOCK inside the writer is what makes
        # safe.
        #
        # db_path IS FORWARDED, for the reason written at process_patient's
        # log_inference call: this runs once per completed patient, so a
        # per-call resolution here is the one that can split a run's health
        # record across two files WHILE IT IS BEING WRITTEN.
        flush_health(run_id, db_path=db_path)

        # ── THE OPERATOR STOP SWITCH, AT THE CHECKPOINT'S OWN CADENCE ───────
        #
        # HERE, AND NOT INSIDE THE `status == "success"` BRANCH ABOVE. Hanging
        # it off save_checkpoint() would leave a pass in which every patient
        # errored unable to be stopped at all -- which is precisely the pass an
        # operator most wants to stop, because it is burning a live Stage 5 call
        # per patient and producing nothing. That is flush_health's argument,
        # one line up, and it applies unchanged.
        #
        # AFTER the checkpoint and after the flush, so the state on disk is
        # already current for the patient that just finished before the queue is
        # torn down. The order is what makes "the checkpoint is current" true at
        # the moment the stop is announced rather than one patient later.
        if STOP_SWITCH.poll(where="main pass"):
            with _stop_sweep_lock:
                first = not stop_sweep_done
                stop_sweep_done = True
            if first:
                # A PLAIN LOCAL, NOT A `nonlocal`. It is assigned and read
                # inside this one callback; declaring it nonlocal would say it
                # outlives the call, and a later reader would then be reading a
                # count from whichever callback swept -- a number about a
                # different moment.
                stop_cancelled = control.cancel_queued(futures)
                console.out(f"[STOP] {stop_cancelled} queued patients "
                            f"cancelled before they started (never billed, "
                            f"never checkpointed -- a resume runs them).")

        progress.set_postfix(ok=batch_success, err=batch_error)
        
        progress.update(1)
    
    # THE builtins.print MONKEY-PATCH USED TO BE HERE, AND IT IS DELETED.
    #
    # It rebound builtins.print to a replacement that took **kwargs and threw
    # them away, so for the whole of a 22,000-patient run every print(end=""),
    # print(sep=...), print(file=...) and print(flush=...) in this PROCESS --
    # in every module and every dependency, not just this one -- silently did
    # something else. It existed for one real reason: while a tqdm bar is live,
    # a line written straight to stderr is overwritten by the bar's redraw.
    #
    # That reason is served by registering the bar with the console channel.
    # oncotriage.observability then routes every console line AND every
    # structured log record through tqdm.write for as long as this registration
    # stands, which is strictly more than the patch covered -- a patch on
    # builtins.print cannot see a logging handler at all.
    _bar_token = console.attach_bar()
    
    # ── WHAT A MatchingModelMismatchError ACTUALLY DOES HERE ────────────────
    #
    # MEASURED, not assumed. Driving this exact function with 40 patients, 4
    # workers, and a mismatch raised by the 4th patient submitted:
    #
    #     the exception DOES escape run_batch
    #     but all 40 of 40 patients ran to completion first
    #
    # So process_patient re-raising does NOT stop the batch. Three things
    # compound to produce that:
    #
    #   1. every future is submitted up front, in the loop below, before any
    #      result is read — so the whole remaining corpus is already queued
    #      before the first failure is visible (item 44 records the same fact
    #      about crash recovery);
    #   2. _on_done is a done-CALLBACK and catches Exception around
    #      future.result(), so it absorbs the mismatch, prints
    #      "[CALLBACK ERROR]" and returns. A raise inside a callback would not
    #      help either: concurrent.futures logs and discards those;
    #   3. `for future in futures: future.result()` does re-raise — but that
    #      exception exits the `with` block, and ThreadPoolExecutor.__exit__
    #      calls shutdown(wait=True), which drains every queued future before
    #      the exception propagates any further.
    #
    # WHAT IS STILL GAINED, and it is not nothing: the failing patients get no
    # result row and are never checkpointed (status != "success" skips
    # save_checkpoint), so a resume re-runs them rather than treating them as
    # done; and the process now ends by RAISING instead of printing a summary
    # and exiting 0, so a mid-run model change cannot be mistaken for a clean
    # run. What is not gained is early termination.
    #
    # FIXING IT PROPERLY needs a cooperative cancellation flag that submitted-
    # but-not-started tasks check before doing work. That mechanism belongs to
    # item 44 and is deliberately NOT built here — a second, competing
    # shutdown path would be worse than the honest gap.
    # ── THE EXECUTOR IS NOT A CONTEXT MANAGER, AND THAT IS THE INTERRUPT FIX ──
    #
    # `with ThreadPoolExecutor(...) as executor:` calls `shutdown(wait=True)` --
    # WITHOUT cancel_futures -- from `__exit__`, which runs BEFORE any `except`
    # clause below it. Every future is submitted up front by the loop below, so
    # `__exit__` DRAINS THE WHOLE REMAINING CORPUS at one live billed Stage 5
    # call each, and only then is the `except KeyboardInterrupt` handler
    # entered -- where its `cancel_futures=True` has nothing left to cancel.
    # That handler's cancellation was DEAD CODE for as long as the `with` form
    # stood, and the file's own note above (item 3) had already reasoned to the
    # same line for the exception case.
    #
    # MEASURED, not argued: a KeyboardInterrupt raised on the main thread at
    # `future.result()` with 2 workers and 20 queued tasks completed 20 OF 20
    # under the `with` form and 2 OF 20 under this one. On a 22,000-patient run
    # interrupted at patient 100 that is the difference between re-billing the
    # remaining ~21,900 patients and paying for the handful already in flight.
    #
    # WHAT IS NOT CANCELLED, stated: a request already in flight. `wait=True`
    # blocks for at most one patient's remaining work, which is correct -- those
    # calls are already paid for and their rows and checkpoint entries are worth
    # having. What changes is that QUEUED patients are no longer started; they
    # are never checkpointed, so a resume runs them.
    #
    # THE `finally` SHUTDOWN IS WHAT MAKES THIS TOTAL. The `except` clause only
    # covers KeyboardInterrupt; the shutdown has to happen on every exit path,
    # including the SystemExit that "25- Batch Runner.py"'s SIGTERM handler
    # raises and including an ordinary exception. On the NORMAL path every
    # future has already completed by the time it runs, so `cancel_futures=True`
    # cancels nothing and the behaviour is byte-identical to the `with` form.
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    stop_unsubmitted = 0
    try:
        for index, fhir_path in enumerate(pending_files):
            # THE SUBMIT LOOP HONOURS THE SWITCH TOO, and this is not
            # redundant with the sweep in `_on_done`. Submission of a
            # 22,000-patient corpus takes milliseconds, so in production the
            # loop is long finished before any patient completes and this
            # branch never fires. It fires on a SMALL corpus, or a slow
            # filesystem, or a machine under load -- exactly the conditions a
            # test runs under -- and without it a patient submitted after the
            # sweep would be neither cancelled nor accounted for, and would run
            # and bill after the stop was announced.
            #
            # `poll()` here is free once tripped and one stat call before that;
            # the alternative, reading `STOP_SWITCH.requested` directly, would
            # not notice a stop asked for BEFORE the first patient completes.
            if STOP_SWITCH.poll(where="main pass submit"):
                stop_unsubmitted = len(pending_files) - index
                break
            future = executor.submit(
                _start_patient_unless_stopped,
                fhir_path=fhir_path,
                graph=graph,
                is_resample=False,
                run_id=run_id,
                db_path=db_path,
            )
            future.add_done_callback(lambda f, fp=fhir_path: _on_done(f, fp))
            futures.append(future)

        if stop_unsubmitted:
            # THE BAR IS RESIZED TO WHAT WILL ACTUALLY BE ACCOUNTED FOR.
            # `_on_done` advances it once per future, cancelled ones included,
            # and a patient that was never submitted has no future and no
            # callback -- so a bar still sized to the whole pending set would
            # stop short and read as a run that hung at the moment it was
            # shutting down cleanly.
            progress.total = len(futures)
            progress.refresh()
            console.out(f"[STOP] {stop_unsubmitted} patients were never "
                        f"submitted.")

        # Wait for all to complete (callbacks handle progress)
        for future in futures:
            try:
                future.result()
            except CancelledError:
                # A CANCELLED FUTURE IS NOT AN ERROR AND MUST NOT ESCAPE HERE.
                # This is new with the stop switch and it is reachable ONLY on
                # that path: the two signal paths cancel from inside an `except`
                # clause, by which point this loop has already stopped running,
                # so no cancelled future was ever handed to it before. The stop
                # switch cancels while the loop is still draining, and an
                # uncaught CancelledError here would leave run_batch by
                # exception -- into main()'s `except BaseException`, which
                # records the run KILLED. A clean operator stop would then be
                # indistinguishable from a crash, which is the one thing the new
                # status exists to distinguish.
                #
                # `_on_done` has already counted it (see its own CancelledError
                # branch), so nothing is lost by continuing.
                continue

    except KeyboardInterrupt:
        # THE POOL IS TORN DOWN AND THE INTERRUPT IS RE-RAISED. It used to be
        # SWALLOWED -- these three lines ran and the function RETURNED NORMALLY
        # -- and the two consequences were both silent:
        #
        #   1. main() carried straight on into the RESAMPLE pass, at ONE LIVE
        #      BILLED CALL PER PATIENT, immediately after printing that the run
        #      had been interrupted. On the shipped RESAMPLE_COUNT of 100 that
        #      is ~100 paid calls after the operator asked the run to stop.
        #   2. main() then finalized the `runs` row FINISHED and closed the
        #      tracking run FINISHED, so an interrupted campaign was indexed as
        #      a completed one -- and every number computed over its rows was a
        #      number about a cohort prefix, presented as a number about the
        #      cohort.
        #
        # tests/test_runner_sigterm_shutdown.py section 3 pinned that divergence
        # as an executable fact, and "25- Batch Runner.py"'s own SIGTERM note
        # cites it as the reason SIGTERM had to be a SystemExit rather than a
        # KeyboardInterrupt. Re-raising is what makes Ctrl-C reach main()'s
        # `except BaseException`: the health record is flushed, both crash
        # blocks print, the `runs` row is finalized KILLED and the tracking run
        # FAILED.
        #
        # RESUME-SAFE, UNCHANGED. Every completed patient is already in the
        # checkpoint -- save_checkpoint runs per patient, in `_on_done`, above
        # this -- and a cancelled patient was never added to it. Nothing here
        # deletes anything.
        #
        # THE MESSAGE STOPPED CLAIMING THE RUN CONTINUES. "Safe to resume" was
        # true and "Checkpoint saved." was true; what the pair implied -- that
        # this was a tidy pause the run would carry on from -- was not.
        # ── THE FIRST STATEMENT, BEFORE THE POOL IS EVEN TOUCHED ────────
        #
        # `executor.shutdown(wait=True)` below blocks until every RUNNING
        # patient returns, and in per-trial mode a running patient is a whole
        # Stage 5 wave: ceil(MAX_TRIALS_FOR_EVALUATION /
        # MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS) rounds of live requests, each
        # bounded only by MATCHING_REQUEST_TIMEOUT_SECONDS and the SDK's own
        # retries. Every one of those is issued AFTER the operator pressed
        # Ctrl-C. The wave's own `finally` cannot help: it runs on the WORKER
        # thread, and the KeyboardInterrupt was delivered to the main thread --
        # which is this one.
        #
        # Setting the flag here bounds the wait at ONE in-flight round: the
        # requests already in the air cannot be interrupted, and every queued
        # one returns immediately without being sent. The patients affected are
        # failed rather than partially completed, so none of them is
        # checkpointed and a resume re-runs them whole.
        request_stage5_shutdown("Ctrl-C during the main batch pass")
        console.out("\n[INTERRUPTED] Waiting for active threads to finish...")
        executor.shutdown(wait=True, cancel_futures=True)
        console.out("[INTERRUPTED] Checkpoint saved: every completed patient "
                    "is in it and a resume will skip them.")
        # THE PASS TALLY IS PRINTED HERE BECAUSE RE-RAISING SKIPS THE SUMMARY
        # LINE BELOW, and losing it would be an information regression bought
        # by a correctness fix. The counts are final at this point: the
        # shutdown above joined every worker, and a done-callback runs on the
        # worker that completed the item (or, for a cancelled one, inside
        # shutdown on this thread), so nothing can still be counting.
        #
        # The wording is the summary line's, deliberately, so an operator
        # reading a scrolled-back log sees the same three numbers in the same
        # shape whether the pass ended or was interrupted.
        console.out(f"[INTERRUPTED] MAIN BATCH INTERRUPTED: {batch_success} "
                    f"success, {batch_error} errors"
                    + (f", {batch_cancelled} cancelled (never attempted)"
                       if batch_cancelled else ""))
        console.out("[INTERRUPTED] STOPPING THE RUN. The resample pass will "
                    "NOT run and this run will be recorded KILLED. For a "
                    "clean stop that records itself as STOPPED, use the stop "
                    f"switch: touch {describe_stop_switch_path()}")
        raise

    finally:
        # SHUTDOWN FIRST, so no worker is still writing when the bar is
        # detached -- which is the order the `with` form produced, since
        # `__exit__` ran before this block. Idempotent: on the interrupt path
        # the handler above has already shut it down and this is a no-op.
        executor.shutdown(wait=True, cancel_futures=True)
        progress.close()
        console.detach_bar(_bar_token)

    console.out()
    console.out("=" * 80)
    # THE CANCELLED COUNT IS NAMED ONLY WHEN IT IS NON-ZERO, so a clean run's
    # line is byte-identical to what it has always printed and an interrupted
    # one cannot report work nobody ran as work that failed.
    console.out(("MAIN BATCH STOPPED: " if STOP_SWITCH.requested
                 else "MAIN BATCH COMPLETE: ")
                + f"{batch_success} success, {batch_error} errors"
                + (f", {batch_cancelled} cancelled (never attempted)"
                   if batch_cancelled else "")
                + (f", {stop_unsubmitted} never submitted"
                   if stop_unsubmitted else ""))
    console.out("=" * 80)
    console.out()

    # THE SECOND MEMBER IS `main_pass_complete`, AND IT ASKS ABOUT THE COHORT
    # RATHER THAN ABOUT THE SWITCH.
    #
    # It was a literal `True` on every path, which was defensible while the only
    # way out of this function was "every patient ran" or "the process died".
    # The stop switch made that a false statement, and the first correction --
    # `not STOP_SWITCH.requested` -- replaced it with a DIFFERENT false
    # statement, measured rather than reasoned about: drive a 40-patient corpus
    # to completion and then write the sentinel while the RESAMPLE pass is
    # running, and the switch latches; but this pass had already covered every
    # patient. `not STOP_SWITCH.requested` reads False, main() records the run
    # STOPPED, the checkpoint is KEPT "because patients remain" -- and none do.
    # A reviewer then reads a STOPPED row as a campaign covering a PREFIX of the
    # cohort, which is what that status means, about a campaign that covered all
    # of it.
    #
    # THE HONEST TEST IS WHAT THIS PASS ACTUALLY DID: was every pending patient
    # submitted, and was none of them cancelled. Those two counts are the only
    # ways a patient can be left unattempted here, they are maintained by the
    # submit loop and by `_on_done` respectively, and they are both zero on a
    # stop that arrived after the last patient completed -- which is exactly the
    # case the switch alone gets wrong.
    #
    # It is deliberately NOT "no patient errored": an errored patient WAS
    # attempted, and main() reads that separately off the results list to decide
    # FINISHED against FAILED. Two different questions, two different readers.
    return completed_ids, (stop_unsubmitted == 0 and batch_cancelled == 0)


# ===========================================================================
# RESAMPLE PASS
# ===========================================================================


def run_resample(fhir_files: list, completed_ids: set, bm25_index: object, nct_ids: list, graph: object, results_list: list, run_id=None, db_path=None,) -> None:
    """
    Re-run a random subset of already-processed patients using concurrent threads.

    Simulates real-world repeat submissions where the same patient
    may be evaluated more than once. All re-runs are logged to the
    same SQLite DB so drift detection and DB queries see duplicates.

    Does NOT update the checkpoint (resample entries are supplemental,
    not required for resume logic).

    ``run_id`` is the ``runs.id`` main() opened, forwarded to every worker so a
    resample row is attributed to the SAME run as the main-pass row for that
    patient. That is deliberate and it is what makes the ledger's design read
    correctly at the SQL level too: a resample re-run is a second `inferences`
    row of one campaign, not a second campaign, so `COUNT(*) ... WHERE run_id =
    ?` is rows-of-this-run and never patients-of-this-run.

    Args:
        fhir_files:    Full list of FHIR file path strings.
        completed_ids: Set of successfully completed filename stem strings.
        bm25_index:    Pre-built BM25Okapi index (read-only, shared).
        nct_ids:       NCT ID list aligned with BM25 index (read-only, shared).
        graph:         Compiled LangGraph StateGraph (read-only, shared).
        results_list:  In-memory results list (mutated via append_result).
    """
    console.out("=" * 80)
    console.out("RESAMPLE PASS")
    console.out("=" * 80)

    # ── THE STOP SWITCH IS HONOURED BEFORE THE FIRST RESAMPLE CALL ─────────
    #
    # main() ALREADY SKIPS THIS PASS WHEN THE SWITCH HAS TRIPPED, so on the
    # shipped path this branch is unreachable. It is here anyway, for two
    # reasons that are about correctness rather than defence in depth:
    #
    #   1. THIS FUNCTION IS PUBLIC and is called directly by embedders and by
    #      tests. A caller that reaches it after a stop must not pay
    #      RESAMPLE_COUNT live Stage 5 calls because the guard lived in its
    #      caller;
    #   2. THE SWITCH CAN TRIP BETWEEN THE TWO PASSES. main()'s check runs
    #      once, immediately after run_batch returns; an operator who writes
    #      the file a second later would otherwise have their stop honoured
    #      only after the whole resample pass had been billed.
    #
    # `poll()` and not `.requested`, so a stop asked for after run_batch
    # finished is seen here rather than being missed for the life of the pass.
    if STOP_SWITCH.poll(where="resample pass"):
        console.out("Resample pass SKIPPED: an operator stop is in effect. "
                    "No resample call was issued.")
        console.out("=" * 80)
        console.out()
        return

    completed_files = [
        f for f in fhir_files
        if Path(f).stem in completed_ids
    ]

    if not completed_files:
        console.out("No completed patients available for resampling. Skipping.")
        return

    actual_resample = min(RESAMPLE_COUNT, len(completed_files))

    # Local Random instance rather than random.seed(): seeding the
    # process-wide state would shift the draw of every other consumer of
    # `random` in the same session.
    rng = random.Random(RESAMPLE_SEED)
    resample_files = rng.sample(completed_files, actual_resample)

    console.out(f"Resampling {actual_resample} patients (seed={RESAMPLE_SEED}).")
    console.out(f"Concurrent workers: {MAX_WORKERS}")
    console.out()

    resample_success = 0
    resample_error = 0
    # Cancelled != errored; see run_batch's CancelledError branch for the whole
    # argument. Both callbacks needed it because they are two functions rather
    # than a copy -- the same reason the drift branch had to be added to each.
    resample_cancelled = 0

    progress = tqdm(total=actual_resample, desc="Resample", unit="patient")

    # Same registration as run_batch(); see the note there for what the deleted
    # builtins.print monkey-patch did and why this replaces it.
    _bar_token = console.attach_bar()


    # This pass gets its OWN announcer. Note the callback below takes (future)
    # while run_batch()'s takes (future, fhir_path) -- they are two different
    # functions, not a copy, and the drift branch had to be added to each. The
    # shared _DriftAnnouncer is what guarantees the two behave identically
    # despite that, rather than two hand-written messages drifting apart.
    _drift = _DriftAnnouncer()

    # Bound before `_on_done` for the reason written at run_batch's: the
    # callback closes over it to cancel the queue, and a name bound at the
    # submit loop would be an UnboundLocalError on any patient that completed
    # first.
    futures = []

    _stop_sweep_lock = threading.Lock()
    stop_sweep_done = False

    def _on_done(future):
        nonlocal resample_success, resample_error, resample_cancelled
        nonlocal stop_sweep_done
        try:
            entry = future.result()
        except CancelledError:
            resample_cancelled += 1
            progress.update(1)
            return
        except MatchingModelMismatchError as e:
            resample_error += 1
            progress.update(1)
            _drift.announce(e)
            return
        except Exception as e:
            resample_error += 1
            progress.update(1)
            console.out(f"  [CALLBACK ERROR] {type(e).__name__}: {e}")
            return

        append_result(results_list, entry)

        if entry["status"] == "success":
            resample_success += 1
        else:
            resample_error += 1

        # Same call, same position and the same reasoning as run_batch's --
        # written out once, at flush_health. Note the counters are CUMULATIVE
        # across both passes and are never cleared between them, so what this
        # writes includes everything the main pass moved; that is the same
        # property degradation.clear_all()'s docstring forbids breaking.
        # db_path is forwarded here too; see run_batch's _on_done.
        flush_health(run_id, db_path=db_path)

        # The same check, in the same position, as run_batch's -- the argument
        # for both the placement and the once-only sweep is written out there.
        # This pass writes no checkpoint (resample entries are supplemental), so
        # "the checkpoint's cadence" here means the health flush's, which is the
        # same per-completed-patient boundary.
        if STOP_SWITCH.poll(where="resample pass"):
            with _stop_sweep_lock:
                first = not stop_sweep_done
                stop_sweep_done = True
            if first:
                stop_cancelled = control.cancel_queued(futures)
                console.out(f"[STOP] {stop_cancelled} queued resample patients "
                            f"cancelled before they started (never billed).")

        progress.set_postfix(ok=resample_success, err=resample_error)
        progress.update(1)

    # Same submit-everything-then-drain structure as run_batch(), and therefore
    # the same behaviour on a MatchingModelMismatchError: it escapes eventually
    # but the pass does not stop early. The reasoning is written out once, at
    # the executor in run_batch(); this is the second instance of it, not a
    # different case.
    # THE EXECUTOR LIFECYCLE IS run_batch's, for the reason written out there:
    # the `with` form's __exit__ drains every queued future before any `except`
    # clause is entered, which made the cancellation below dead code. Second
    # instance of one fix, not a different case.
    executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
    stop_unsubmitted = 0
    try:
        for index, fhir_path in enumerate(resample_files):
            # run_batch's submit-loop guard, second instance, same argument.
            if STOP_SWITCH.poll(where="resample pass submit"):
                stop_unsubmitted = len(resample_files) - index
                break
            future = executor.submit(
                _start_patient_unless_stopped,
                fhir_path=fhir_path,
                graph=graph,
                is_resample=True,
                run_id=run_id,
                db_path=db_path,
            )
            future.add_done_callback(lambda f: _on_done(f))
            futures.append(future)

        if stop_unsubmitted:
            progress.total = len(futures)
            progress.refresh()
            console.out(f"[STOP] {stop_unsubmitted} resample patients were "
                        f"never submitted.")

        for future in futures:
            try:
                future.result()
            except CancelledError:
                # run_batch's branch, second instance. The argument for why a
                # cancelled future must not escape this loop is written there.
                continue

    except KeyboardInterrupt:
        # RE-RAISED, exactly as run_batch's is and for the reasons written out
        # there. This handler's swallow was the cheaper of the two -- the
        # resample pass is the last thing main() runs before its summary, so
        # what it bought was a finalization of FINISHED on a run the operator
        # had interrupted, rather than a further ~100 billed calls.
        # run_batch's first statement, second instance. The argument is
        # written there and applies unchanged: this handler is on the main
        # thread, the waves are on worker threads, and without this the
        # shutdown below waits for every one of them to finish buying
        # responses nobody will read.
        request_stage5_shutdown("Ctrl-C during the resample pass")
        console.out("\n[INTERRUPTED] Waiting for active threads to finish...")
        executor.shutdown(wait=True, cancel_futures=True)
        console.out(f"[INTERRUPTED] RESAMPLE INTERRUPTED: {resample_success} "
                    f"success, {resample_error} errors"
                    + (f", {resample_cancelled} cancelled (never attempted)"
                       if resample_cancelled else ""))
        console.out("[INTERRUPTED] Resample interrupted. Results already "
                    "written are saved; the run will be recorded KILLED.")
        raise

    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        progress.close()
        console.detach_bar(_bar_token)

    console.out()
    console.out("=" * 80)
    console.out(("RESAMPLE STOPPED: " if STOP_SWITCH.requested
                 else "RESAMPLE COMPLETE: ")
                + f"{resample_success} success, {resample_error} errors"
                + (f", {resample_cancelled} cancelled (never attempted)"
                   if resample_cancelled else "")
                + (f", {stop_unsubmitted} never submitted"
                   if stop_unsubmitted else ""))

# ===========================================================================
# SUMMARY REPORT
# ===========================================================================

# ===========================================================================
# RECONCILIATION (the write-durability pass)
# ===========================================================================

def inference_row_count(db_path):
    """Rows in db_path's inferences table, or None if it cannot be read.

    READ-ONLY URI, on File 19's precedent and for its reason: a plain
    ``sqlite3.connect`` on an absent path CREATES the file, so a guard run
    against a mistyped path would bring an empty database into existence, count
    0 twice and report success.

    None is a real answer and callers must treat it as one -- ``reconcile_writes``
    reports the baseline as unavailable rather than comparing None with None,
    which passes whatever the run did.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return conn.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


def print_crash_record(where="crash") -> None:
    """Print the census and degradation blocks on a path that is about to die.

    WHAT WAS LOST WITHOUT IT. Both of ``main()``'s ``except BaseException``
    handlers finalize the run row and re-raise, and neither printed anything. So
    a campaign that crashed at patient 19,000 left NO console record at all --
    every counter it had moved went out with the process. The periodic health
    flushes cover part of that: ``run_metrics`` holds the DEGRADATION totals as
    of the last completed patient. They do not cover the CENSUS, which is
    excluded from ``run_metrics`` by a closed-category ruling (see
    ``degradation._CENSUS_SPEC``), so on a crash the census survived NOWHERE.
    This is the only place it can.

    FRESH SNAPSHOTS, AND THAT BREAKS NO PROMISE. The success path takes one
    snapshot and hands it to three consumers precisely so the rows, the logged
    event and the printed block describe one instant. There is no such
    obligation here, and the reason is written at the crash handler's own health
    flush: nothing else printed on this path, so there is no second report for
    these to disagree with. Taking them fresh makes them as late as possible,
    which is what a reader of a crash wants.

    CENSUS ABOVE DEGRADATION, the same order ``print_summary`` uses and for its
    reason -- severity ascending. The reconciliation is deliberately absent:
    it is a VERDICT about whether the data is whole, it needs a baseline count
    and a completed ledger, and on a crash the ledger describes an unfinished
    run. A verdict computed over a partial run would be a false one.

    IT NEVER RAISES, WHICH IS THE WHOLE CONTRACT. It runs while an exception is
    in flight, and anything escaping here would REPLACE that exception with one
    about printing -- destroying the diagnosis the operator actually needs and
    the traceback that names where the run died.

    THE TWO BLOCKS HAVE SEPARATE GUARDS, so a formatting failure in one still
    lets the other print. One guard around both would let the census take the
    degradation block with it, which is the more valuable of the two.

    IT CATCHES ``BaseException``, AND THAT DIVERGES FROM THIS MODULE'S USUAL
    RULE ON PURPOSE. ``finalize_run_record`` catches ``Exception`` so a Ctrl-C
    still escapes -- correct there, because a finalizer that swallowed one would
    leave an operator holding a key down against a process that will not stop.
    Here the goal is the opposite and is stated in the contract above: a
    KeyboardInterrupt arriving during these few lines must not become the
    exception that propagates, because it would displace the original. The
    window is two print calls wide and the original ``raise`` follows
    immediately.
    """
    try:
        degradation.print_census_report(degradation.census_snapshot())
    except BaseException as exc:                       # noqa: BLE001 -- noted
        _crash_record_note("census", where, exc)
    try:
        degradation.print_report(degradation.snapshot())
    except BaseException as exc:                       # noqa: BLE001 -- noted
        _crash_record_note("degradation", where, exc)


def _crash_record_note(block, where, exc) -> None:
    """One line saying a crash-path block could not be printed. Never raises.

    A SILENT FAILURE HERE IS INDISTINGUISHABLE FROM A RUN THAT HAD NOTHING TO
    REPORT, which on a crash path is the reading that costs the most. The note
    names the block so a reader knows which one is missing rather than wondering
    whether the run was clean.

    The bare guard around the note itself is the end of the line: if the console
    channel cannot take one line, there is nowhere left to report that, and
    raising would displace the exception this whole function exists not to
    displace.
    """
    try:
        console.out(f"[{where}] The {block} block could not be printed: "
                    f"{type(exc).__name__}: {exc}")
    except BaseException:                              # noqa: BLE001, S110
        pass


def reconcile_writes(db_path=None, rows_before=None) -> dict:
    """Check that every row this process was told it wrote is actually there.

    THE SHAPE IS FILE 19's -- read the count before the run, read it after,
    report a shortfall -- with one deliberate strengthening. File 19 asks
    whether a count MOVED, which is the right question for a guard whose whole
    subject is "did the server write anywhere near this file". This asks whether
    SPECIFIC ROWS ARE PRESENT, by the ids log_inference reported, because the
    question here is the opposite one and a count cannot answer it: another
    process writing the same database during the run inflates the delta, and a
    delta inflated by exactly as many rows as this run lost reconciles perfectly
    while the data is gone.

    So the delta is REPORTED and the id check DECIDES.

    Args:
        db_path:     The database to check. None means whatever the ledger's
                     entries name, which is what log_inference actually
                     resolved -- not a re-resolution here, which could differ if
                     ONCOTRIAGE_INFERENCES_DB changed mid-run.
        rows_before: The count read before the run started, for the delta line.
                     None means it was not read, and the delta is reported as
                     unavailable rather than as zero.

    Returns a dict; ``complete`` is the verdict.

    WHAT THIS COVERS AND WHAT IT DOES NOT, stated because a reconciliation that
    overstates itself is worse than none:

      COVERS  every log_inference call THIS PROCESS made, including the resample
              pass's re-runs (each is its own ledger entry) and excluding
              patients skipped by a checkpoint resume (which make no call).
      COVERS  a row that was reported written and is not in the table.
      DOES NOT cover rows written by an earlier process, or by another process
              running concurrently. It is not a statement about the table's
              total contents; it is a statement about this run's writes.
      DOES NOT cover trial_matches children. The FK-less schema means an
              inference row can be present with its children missing, and the
              writer commits both in one transaction so that cannot happen from
              a partial write -- but this function does not prove it.
    """
    with _write_ledger_lock:
        entries = list(_WRITE_LEDGER)

    attempted    = len(entries)
    reported_ok  = [e for e in entries if e["ok"] is True]
    reported_bad = [e for e in entries if e["ok"] is False]
    unreported   = [e for e in entries if e["ok"] is None]

    # The path the WRITER used, never a re-resolution -- see the docstring.
    paths_seen = sorted({e["db_path"] for e in entries if e["db_path"]})
    target = db_path or (paths_seen[0] if len(paths_seen) == 1 else None)

    ids = [e["inference_id"] for e in reported_ok
           if e["inference_id"] is not None]
    ok_without_id = len(reported_ok) - len(ids)

    verified = 0
    verify_error = None
    if ids and target:
        try:
            conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
            try:
                # Chunked: SQLite's default SQLITE_MAX_VARIABLE_NUMBER is 999 on
                # older builds and a 22,000-patient run has far more ids than
                # that. Chunking is not an optimisation, it is what stops the
                # query raising "too many SQL variables" on exactly the corpus
                # size this runner exists for.
                for start in range(0, len(ids), 500):
                    chunk = ids[start:start + 500]
                    placeholders = ",".join("?" * len(chunk))
                    verified += conn.execute(
                        f"SELECT COUNT(*) FROM inferences WHERE id IN ({placeholders})",
                        chunk).fetchone()[0]
            finally:
                conn.close()
        except sqlite3.Error as exc:
            # Recorded, never swallowed: an unverifiable reconciliation is not a
            # clean one, and `complete` below is False when this is set.
            verify_error = f"{type(exc).__name__}: {exc}"
            verified = None

    rows_after = inference_row_count(target) if target else None
    delta = (rows_after - rows_before
             if rows_after is not None and rows_before is not None else None)

    missing = (len(ids) - verified) if verified is not None else None

    complete = (
        attempted > 0
        and not reported_bad
        and not unreported
        and ok_without_id == 0
        and verify_error is None
        and missing == 0
    )

    return {
        "attempted":     attempted,
        "reported_ok":   len(reported_ok),
        "reported_lost": len(reported_bad),
        "unreported":    len(unreported),
        "ok_without_id": ok_without_id,
        "verified":      verified,
        "missing":       missing,
        "verify_error":  verify_error,
        "rows_before":   rows_before,
        "rows_after":    rows_after,
        "delta":         delta,
        "db_paths":      paths_seen,
        "target":        target,
        "complete":      complete,
        "failures":      [f"{e['patient_id']}: {e['error']}" for e in reported_bad],
        "retried":       sum(1 for e in reported_ok if (e["attempts"] or 1) > 1),
    }


_LAST_RECONCILIATION = {"value": None}


def _publish_reconciliation(rec: dict) -> None:
    """Record the run's verdict where the entry point can read it."""
    _LAST_RECONCILIATION["value"] = rec


def last_reconciliation():
    """What the most recent main() reconciled, or None if it has not run.

    WHY THIS EXISTS RATHER THAN A CHANGED RETURN TYPE. main() returns
    results_list and its docstring promises that to embedders; widening it to a
    pair would break every one of them to serve one caller. The one caller is
    "25- Batch Runner.py", which needs a process exit code.

    None is a REAL answer and reconciliation_exit_code() treats it as one: it
    means main() never got as far as reconciling, which is a failure, not a
    clean run.
    """
    return _LAST_RECONCILIATION["value"]


def reconciliation_exit_code() -> int:
    """0 only if the last run stored everything it processed.

    2 -- not 1 -- when there is no reconciliation at all, so "the run lost rows"
    and "the run never reached the reconciliation" are distinguishable from a
    shell. A caller that saw neither would otherwise read an exception during
    setup as a data-loss finding.
    """
    rec = last_reconciliation()
    if rec is None:
        return 2
    return 0 if rec["complete"] else 1


def print_reconciliation(rec: dict) -> None:
    """Print the reconciliation block, and say plainly whether the run is whole.

    Nothing here decides an exit code; main() reads ``rec["complete"]``. Kept
    separate from print_summary so a caller embedding the runner can reconcile
    without printing, and so this block can be printed on its own.
    """
    console.out("--- DATABASE WRITE RECONCILIATION ---")
    console.out(f"  {'writes attempted (this process)':<34} {rec['attempted']}")
    console.out(f"  {'reported written':<34} {rec['reported_ok']}")
    console.out(f"  {'rows verified present by id':<34} "
                f"{'UNKNOWN' if rec['verified'] is None else rec['verified']}")
    console.out(f"  {'reported LOST by the writer':<34} {rec['reported_lost']}")
    if rec["unreported"]:
        console.out(f"  {'writer did not report outcome':<34} {rec['unreported']}")
    if rec["ok_without_id"]:
        console.out(f"  {'reported ok with no row id':<34} {rec['ok_without_id']}")
    if rec["retried"]:
        console.out(f"  {'succeeded only after a retry':<34} {rec['retried']}")

    console.out(f"  {'database':<34} {rec['target']}")
    if len(rec["db_paths"]) > 1:
        console.out(f"  {'!! writes went to SEVERAL files':<34} {rec['db_paths']}")

    # The count delta is a CROSS-CHECK, not the verdict -- see reconcile_writes.
    if rec["delta"] is None:
        console.out(f"  {'row count before -> after':<34} unavailable "
                    f"(could not read the table both times)")
    else:
        console.out(f"  {'row count before -> after':<34} "
                    f"{rec['rows_before']} -> {rec['rows_after']} "
                    f"(delta {rec['delta']:+d}, attempted {rec['attempted']})")
        if rec["delta"] != rec["attempted"]:
            console.out("      NOTE: a delta unequal to the attempts is not "
                        "itself a fault. Another process writing this file "
                        "during the run inflates it, and the id check above is "
                        "what decides.")

    if rec["verify_error"]:
        console.out(f"  !! the verification query failed: {rec['verify_error']}")

    console.out()
    if rec["complete"]:
        console.out("  ✓ COMPLETE: every inference this run wrote is in the "
                    "database.")
    else:
        console.out("  ✗ INCOMPLETE: this run did NOT store everything it "
                    "processed.")
        if rec["attempted"] == 0:
            console.out("      No writes were attempted at all. If patients "
                        "were processed, log_inference was never reached; if "
                        "none were, the run tested nothing.")
        for line in rec["failures"][:20]:
            console.out(f"      lost: {line}")
        if len(rec["failures"]) > 20:
            console.out(f"      ... and {len(rec['failures']) - 20} more")
        if rec["missing"]:
            console.out(f"      {rec['missing']} row(s) the writer reported as "
                        f"written are NOT in the table.")
        console.out("      Any analysis over this database is computed over "
                    "fewer patients than were processed. Re-run the missing "
                    "patients before using these numbers.")
    console.out()


#------------------------------------------------------------------------------


def pass_stats(records: list) -> dict:
    """The per-pass statistics block. ``{}`` for an empty pass.

    THE BODY IS ``print_summary``'s ``_stats`` CLOSURE, MOVED OUT UNCHANGED
    (the tracking pass). It closed over nothing -- ``records`` was its only
    input -- so hoisting it is a relocation and not a rewrite, and
    ``print_summary`` still calls it for exactly the two blocks it printed
    before.

    WHY IT HAD TO MOVE. ``oncotriage/tracking.py`` logs the run's summary
    numbers, and the brief for that pass is explicit that it may log only
    numbers the summary ALREADY computes. A second computation beside this one
    is the shape this project has removed twice already -- ``cost_by_model``
    against the dashboard's pandas groupby, ``analysis.ablation_db()`` against
    ``study.ablation_db()`` -- and both times the two copies had already
    diverged before anyone noticed. One function, two readers.

    THE RETURN SHAPE IS UNCHANGED, INCLUDING THE FORMATTED STRINGS. Five values
    here are numbers and six are pre-formatted display strings ("12.3s",
    "4.2 min", "31.0%"). Converting them to numbers would be a rewrite of what
    ``print_summary`` prints, which this pass does not do -- so
    ``tracking_metrics`` below takes the five numeric members and computes
    nothing from the other six. Parsing a number back out of "12.3s" to log it
    would be inventing a metric out of a display string, which is worse than
    not logging it.
    """
    if not records:
        return {}
    success = [r for r in records if r["status"] == "success"]
    errors  = [r for r in records if r["status"] != "success"]
    # Include ALL records with a meaningful total_time in timing stats
    # (not just status=="success"). A patient whose pipeline ran but
    # returned an error_msg still has a valid elapsed time worth tracking.
    times   = [r["total_time"] for r in records if r.get("total_time", 0) > 0]
    # Eligible match counts are only meaningful for successful runs
    eligible = [r["eligible_matches"] for r in success]
    # Trials the model could not assess, surfaced so a run that quietly
    # stops evaluating is visible instead of looking like a run with fewer
    # matches. Older checkpoint records predate the key.
    unevaluable = [r.get("not_evaluable", 0) for r in success]
    return {
        "total":               len(records),
        "success":             len(success),
        "errors":              len(errors),
        "error_rate":          f"{len(errors)/len(records)*100:.1f}%",
        "avg_time":            f"{sum(times)/len(times):.1f}s" if times else "N/A",
        "min_time":            f"{min(times):.1f}s"            if times else "N/A",
        "max_time":            f"{max(times):.1f}s"            if times else "N/A",
        "total_time":          f"{sum(times)/60:.1f} min"      if times else "N/A",
        "avg_eligible":        f"{sum(eligible)/len(eligible):.2f}" if eligible else "N/A",
        "patients_with_match": sum(1 for e in eligible if e > 0),
        "not_evaluable":       sum(unevaluable),
    }


# The five members of pass_stats() that are numbers rather than display
# strings. Named rather than filtered by isinstance at call time, because
# "whatever happens to be an int today" is not a contract -- adding a numeric
# member to pass_stats() would silently start logging it under a name nobody
# chose, and REMOVING one would silently stop logging it.
_TRACKED_PASS_STATS = ("total", "success", "errors", "patients_with_match",
                       "not_evaluable")


def tracking_metrics(results_list: list, total_wall_time: float,
                     reconciliation: dict = None,
                     degradation_snapshot: dict = None) -> dict:
    """The run's summary numbers, as ``{metric name: number}`` for the index.

    SELECTS, NEVER COMPUTES. Every value here is read from ``pass_stats``, from
    ``reconcile_writes``' verdict or from ``degradation.totals`` -- the three
    things ``print_summary`` prints. This function does arithmetic on none of
    them, so a number in the tracking store and the number in the printed
    summary cannot disagree.

    ``total_wall_time`` is the one value passed in rather than derived, exactly
    as ``print_summary`` takes it, and it is logged in SECONDS while the
    summary prints minutes. That is a unit, not a second computation: a metric
    store wants a base unit, and `/60` at the print site is the display.

    Args:
        results_list: the run's per-patient records.
        total_wall_time: seconds, as handed to ``print_summary``.
        reconciliation: ``reconcile_writes``' dict, or None. None means nobody
            reconciled, and NO reconciliation metric is emitted -- an absent
            metric is "not asked", while ``reconciliation_complete=0`` would
            assert that rows were lost.
        degradation_snapshot: ``degradation.snapshot()``'s dict, or None. Its
            TOTALS are logged, keyed by counter NAME -- never the counter's own
            keys, which carry third-party and clinical text. That is the
            distinction ``degradation.totals()`` exists to make, and it is the
            reason this is loggable at all.
    """
    metrics = {"wall_time_seconds": total_wall_time}

    for label, records in (
        ("main", [r for r in results_list if not r.get("is_resample")]),
        ("resample", [r for r in results_list if r.get("is_resample")]),
    ):
        stats = pass_stats(records)
        for key in _TRACKED_PASS_STATS:
            # An EMPTY PASS EMITS NOTHING, rather than five zeros. pass_stats
            # returns {} for no records, and a run with no resample pass is not
            # a run whose resample pass found nothing -- logging zeros would
            # make those two indistinguishable in every comparison.
            if key in stats:
                metrics[f"{label}_{key}"] = stats[key]

    if reconciliation is not None:
        for key in ("attempted", "verified", "missing"):
            metrics[f"reconciliation_{key}"] = reconciliation[key]
        metrics["reconciliation_complete"] = reconciliation["complete"]

    if degradation_snapshot is not None:
        for name, total in degradation.totals(degradation_snapshot).items():
            metrics[f"degradation_{name}"] = total

    return metrics


def print_summary(results_list: list, total_wall_time: float, db_path=None,
                  reconciliation: dict = None,
                  degradation_snapshot: dict = None,
                  census_snapshot: dict = None) -> None:
    """
    Print a concise final summary report for the entire batch run.

    Args:
        results_list:     Full list of per-patient result dicts.
        total_wall_time:  Total elapsed seconds for the entire run.
        db_path:          The database the run wrote to, for the "Database:"
                          line. None means the configured production one --
                          resolved through the SAME function log_inference uses,
                          so this line names the file that was actually written
                          to rather than a global that happened to be nearby.
                          File 25 read a bare `inferences_path` here, which was
                          correct only as long as nothing had rebound it.
        reconciliation:   What reconcile_writes returned, or None to skip the
                          block. None is NOT "nothing to report" -- it is "not
                          asked" -- and the closing line says which, because a
                          summary that reads "Run complete." with no
                          reconciliation is exactly the report this pass exists
                          to stop being trusted.
        degradation_snapshot:
                          What oncotriage/degradation.py:snapshot() returned, or
                          None to skip the block. TAKEN BY THE CALLER, not read
                          here, for the reason reconciliation is passed rather
                          than recomputed: main() takes ONE snapshot and gives it
                          to both the printed block and the logged event, so the
                          two describe the same instant. Reading the live
                          counters here would let a line the report itself emits
                          move a counter (EMIT_FAILURES) between the event and
                          the block.

                          {} is a REAL VALUE and is not None: it means every
                          counter was read and every one was zero, and the block
                          says so. None means nobody asked.
        census_snapshot:  What oncotriage/degradation.py:census_snapshot()
                          returned, or None to skip the block. Same
                          taken-by-the-caller rule and same {}-is-a-value rule
                          as degradation_snapshot above.

                          A SECOND PARAMETER RATHER THAN A SECOND KEY IN THE
                          FIRST. The two registries are separate because a
                          census counter moving is not a fault, and merging
                          them here would put the reader one dict lookup away
                          from re-conflating exactly what the split exists to
                          keep apart.
    """
    main_results = [r for r in results_list if not r.get("is_resample")]
    resample_results = [r for r in results_list if r.get("is_resample")]

    main_stats = pass_stats(main_results)
    resample_stats = pass_stats(resample_results)
    # Error detail: list unique error messages and their counts
    error_records = [r for r in results_list if r["status"] != "success" and r.get("error")]
    error_counts: dict = {}
    for r in error_records:
        key = r["error"][:120]  # truncate very long error strings
        error_counts[key] = error_counts.get(key, 0) + 1

    console.out()
    console.out("=" * 80)
    console.out(f"{Project_Name}: BATCH RUN SUMMARY")
    console.out("=" * 80)
    console.out(f"Total wall time:  {total_wall_time/60:.1f} min")
    console.out(f"Results file:     {_results_path()}")
    console.out(f"Checkpoint file:  {_checkpoint_path()}")
    console.out(f"Database:         {resolve_inference_db_path(db_path)}")
    console.out()

    # THE RESUMED-RUN CAVEAT GOES ABOVE THE STATISTICS IT QUALIFIES, which is
    # the opposite of where the reconciliation block sits and for the same
    # reason: the reconciliation is a VERDICT on the run and belongs at the
    # bottom where a reader stops, while this changes how every number below it
    # must be read. A run whose prior results could not be loaded reports
    # per-pass statistics over THIS SESSION's patients only, and without this
    # line a resumed run that lost 19,000 prior entries looks like a small clean
    # run. The fact is read off the results object itself, which append_result
    # mutated in place, so it cannot be forgotten at the call site.
    if getattr(results_list, "ok", True) is False:
        console.out("--- PRIOR RESULTS WERE NOT LOADED ---")
        console.out(f"  the results file could not be read: "
                    f"{getattr(results_list, 'error', 'unknown')}")
        _preserved = getattr(results_list, "preserved_path", None)
        if _preserved:
            console.out(f"  it was preserved as: {_preserved}")
        else:
            console.out("  it could NOT be preserved, and has been overwritten "
                        "by this run's writes.")
        console.out("  EVERY STATISTIC BELOW COVERS THIS SESSION'S PATIENTS "
                    "ONLY. Patients completed by an earlier run are absent from "
                    "them.")
        console.out("  The checkpoint was NOT affected, so no patient was "
                    "re-run because of this, and the database still holds every "
                    "earlier row.")
        console.out()

    console.out("--- MAIN PASS ---")
    if main_stats:
        for k, v in main_stats.items():
            console.out(f"  {k:<30} {v}")
    else:
        console.out("  No main pass records.")
    console.out()

    console.out("--- RESAMPLE PASS ---")
    if resample_stats:
        for k, v in resample_stats.items():
            console.out(f"  {k:<30} {v}")
    else:
        console.out("  No resample records.")
    console.out()

    if error_counts:
        console.out("--- ERROR BREAKDOWN ---")
        for msg, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            console.out(f"  [{count}x] {msg}")
        console.out()

    # THE RECONCILIATION IS THE LAST BLOCK BEFORE THE VERDICT, on File 19's
    # ordering argument: the thing a reader looks for is the bottom of the
    # output, so the statement about whether the data is whole belongs there
    # rather than above the per-pass statistics it qualifies.
    # THE CENSUS SITS ABOVE THE DEGRADATION BLOCK, which sits above the
    # reconciliation. Severity ascending, verdict last: observations about what
    # this run rendered and flagged, then the faults, then whether the data is
    # whole. A reader scanning UP from the bottom -- which is how the tail of a
    # long run is read -- meets the conclusion, then the reasoning, then the
    # background.
    if census_snapshot is not None:
        degradation.print_census_report(census_snapshot)

    # THE DEGRADATION BLOCK SITS ABOVE THE RECONCILIATION, and the order is
    # argued rather than arbitrary. The reconciliation is the run's VERDICT and
    # File 19's rule puts a verdict last; this is evidence, and one of its
    # counters (INFERENCE_WRITE_FAILURES) is evidence FOR that verdict, so it
    # reads as the reasoning above the conclusion rather than as an appendix
    # below it.
    if degradation_snapshot is not None:
        degradation.print_report(degradation_snapshot)

    if reconciliation is not None:
        print_reconciliation(reconciliation)

    console.out("=" * 80)
    if reconciliation is None:
        # "Run complete." with nothing behind it is what this pass removes. If
        # nobody reconciled, the summary says only that the run ENDED.
        console.out("Run ended. (No write reconciliation was performed, so this "
                    "says nothing about whether every row was stored.)")
    elif reconciliation["complete"]:
        console.out("Run complete: every inference this run produced is in the "
                    "database.")
    else:
        console.out("RUN INCOMPLETE: rows were lost. See the reconciliation "
                    "block above.")
    console.out("=" * 80)
    console.out()

#------------------------------------------------------------------------------


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    """Run the full batch: main pass, resample pass, summary, checkpoint cleanup.

    THE BODY OF "25- Batch Runner.py"'s ``__main__`` BLOCK, moved here so that
    block can be three lines. Nothing was reordered and nothing was dropped; the
    only edits are the two free names the exec chain used to supply
    (``data_fhir_path``, now ``paths.data_fhir_path``) and ``SystemExit`` where
    the original wrote ``raise SystemExit(1)`` -- which is unchanged and still
    exits the process, because a batch runner that cannot build its index has
    nothing to fall back to.

    Returns:
        The results list, so a caller embedding this has the run's outcome
        rather than only its printed summary.

        THE RETURN TYPE IS UNCHANGED, deliberately. The write-durability pass
        needed the run's completeness to reach the process exit code, and the
        obvious move -- returning a (results, reconciliation) pair -- is a
        contract change for every embedder to buy one caller a value. The
        verdict is published on the module instead, through
        ``last_reconciliation()``, which "25- Batch Runner.py" reads. See the
        note there: that file's exit code IS a contract change and is stated as
        one.
    """
    clear_write_ledger()
    # ONE CONFIGURATION PER RUN, RESOLVED ONCE. Dropped here so a second
    # main() in one process (a test, an embedder looping) resolves the
    # collection again rather than stamping its checkpoint with the first
    # run's -- clear_write_ledger()'s precedent, immediately above, and for
    # the same reason: per-run state that survives into the next run is state
    # that describes the wrong run.
    run_fingerprint.clear_cache()
    # THE THIRD PIECE OF PER-RUN MODULE STATE, cleared for the reason the two
    # above it are: a stop honoured by an earlier main() in this process
    # describes THAT run, and inheriting it would make the next one cancel its
    # cohort at the first completed patient having been asked nothing.
    STOP_SWITCH.reset()
    # THE FOURTH PIECE OF PER-RUN MODULE STATE, cleared here for the reason the
    # three above it are. A shutdown asked of an EARLIER main() in this process
    # would make every patient of this one fail with no request sent -- the
    # loudest possible version of "state that describes the wrong run".
    clear_stage5_shutdown()

    with CaffeinateSession("Batch Runner"):

        run_start = time.time()

        console.out()
        console.out("=" * 80)
        console.out(f"{Project_Name}: BATCH RUNNER")
        console.out("=" * 80)
        console.out()

        # ------------------------------------------------------------------
        # 0. The stop switch: refuse a stale one, and say where it goes
        # ------------------------------------------------------------------
        # FIRST, BEFORE THE BM25 INDEX AND BEFORE THE GRAPH. A stale sentinel
        # makes this run stop at its first completed patient, so the refusal is
        # worth nothing spent -- and it is the cheapest refusal available: one
        # stat call, before a client is opened, a model is loaded or a corpus is
        # globbed. Putting it beside the CHECKPOINT refusal would have been the
        # tidier home and would have cost the ~30 seconds of index build first.
        try:
            assert_no_stale_stop_switch()
        except StaleStopSwitch as exc:
            console.out()
            console.out(str(exc))
            raise SystemExit(1)

        # THE PATH IS ANNOUNCED ON EVERY RUN, not only when it is used. An
        # operator who needs to stop a run that is already going has no way to
        # discover this file from the process -- there is no CLI to ask -- so it
        # is printed where the run's own log will carry it.
        console.out(f"[Setup] To stop this run cleanly between patients: "
                    f"touch {stop_switch_path()}")
        console.out()

        # ------------------------------------------------------------------
        # 1. Build shared pipeline resources (once, reused for all patients)
        # ------------------------------------------------------------------
        console.out("[Setup] Building BM25 index from Qdrant...")
        try:
            bm25_index, nct_ids = build_bm25_index_from_qdrant()
            if not nct_ids:
                raise ValueError("Qdrant returned 0 trials. Run 11- RAG Trial Indexer first.")
        except Exception as e:
            console.out(f"[FATAL] Could not build BM25 index: {e}")
            raise SystemExit(1)

        console.out(f"[Setup] BM25 index ready: {len(nct_ids)} trials.\n")

        console.out("[Setup] Compiling LangGraph pipeline...")
        try:
            graph = build_matching_graph()
        except Exception as e:
            console.out(f"[FATAL] Could not compile LangGraph graph: {e}")
            raise SystemExit(1)

        console.out("[Setup] LangGraph pipeline ready.\n")

        # ------------------------------------------------------------------
        # 2. Load FHIR patient files
        # ------------------------------------------------------------------
        fhir_files = sorted(glob.glob(paths.data_fhir_path + "*.json"))

        if not fhir_files:
            console.out(f"[FATAL] No FHIR files found in: {paths.data_fhir_path}")
            raise SystemExit(1)

        console.out(f"[Setup] Found {len(fhir_files)} FHIR patient files.\n")

        # THE BASELINE ROW COUNT, read BEFORE the first write and never after
        # it -- File 19's ordering, and for its reason: read after the first
        # POST and it measures nothing. It resolves through the same function
        # log_inference uses, so it names the file that will actually be
        # written rather than a global that happened to be nearby.
        #
        # It is a CROSS-CHECK and not the verdict. See reconcile_writes.
        # ONE RESOLUTION, HERE, THREADED EVERYWHERE. THE NAME IS NOW TOO
        # NARROW AND IS KEPT, because it is what the reconciliation reads and
        # renaming it would touch every line below for no behaviour.
        #
        # WHAT IT FIXES. `resolve_inference_db_path` consults
        # ONCOTRIAGE_INFERENCES_DB at CALL time, and FIVE call sites used to
        # resolve independently: `log_inference` once per patient, `flush_health`
        # once per completed patient in each of the two pools,
        # `reconcile_writes` at the end, and `print_summary`'s reported path.
        # Change the variable while a 22,000-patient run is in flight and those
        # five answer differently from this one -- so the `runs` row is in file
        # A, the patient rows in file B, every `run_id` in B names a run that is
        # not there, the health record is split across both, and the
        # reconciliation reads whichever it resolved and reports the other
        # file's rows as lost. Nothing raises; the run reports a verdict about a
        # file it did not write.
        #
        # WHY ONE RESOLUTION RATHER THAN PER-CALL EVERYWHERE, which was the
        # other option. Three reasons, in the order they decided it:
        #
        #   1. THE CODEBASE ALREADY MADE THIS CHOICE FOR THE SAME CALL. `run_id`
        #      is threaded down through run_batch -> process_patient ->
        #      log_inference as an argument, and its docstring says in as many
        #      words that it is "threaded down as an argument and never read off
        #      a module global". `db_path` rides the same three functions to the
        #      same call. Two arguments to one call resolved two different ways
        #      is the inconsistency, not the fix.
        #   2. THE RECONCILIATION REQUIRES IT. `reconcile_writes` asks whether
        #      the specific ids the ledger recorded are present. That question
        #      is only meaningful against the file they were written to, so
        #      per-call resolution does not merely risk a split -- it makes the
        #      run's own verdict unanswerable.
        #   3. IT IS THE `run_fingerprint` ARGUMENT, one layer down. That module
        #      caches its stamp per process on the reasoning that a run is ONE
        #      configuration, and a stamp taken per write could straddle a
        #      change. A run is one DESTINATION for the same reason.
        #
        # The cost is stated: a deliberate mid-run redirect is no longer
        # possible. Nothing wants one -- it would split a campaign across two
        # files, which is the defect above, not a feature.
        _reconcile_db = resolve_inference_db_path(None)
        rows_before = inference_row_count(_reconcile_db)
        console.out(f"[Guard] Inference rows before this run: "
                    f"{'UNREADABLE' if rows_before is None else rows_before} "
                    f"({_reconcile_db})")
        if rows_before is None:
            console.out("[Guard] The table could not be read. That is normal on "
                        "a database that does not exist yet; the id-based "
                        "reconciliation below does not depend on it.\n")

        # ------------------------------------------------------------------
        # 3. Load checkpoint and results (resume support)
        # ------------------------------------------------------------------
        # THE CONFIGURATION STAMP IS TAKEN ON THIS THREAD, BEFORE THE POOL.
        # save_checkpoint() is called from _on_done, a done-CALLBACK, which
        # runs on a WORKER thread -- so without this, MAX_WORKERS threads would
        # reach an unwarmed cache at once on the first successful patient.
        # run_fingerprint holds a lock for that, and this is the first line of
        # defence: one resolution, on the main thread, before anything can
        # race for it. It is also the value load_checkpoint compares against
        # one line below, so what gates the resume and what stamps the writes
        # are the same object rather than two readings that can straddle an
        # alias swap.
        _fingerprint = run_fingerprint.current()
        # THE SENTENCE IS run_fingerprint's, NOT THIS FILE'S. It used to be
        # five hand-written fields here, a sixth copy in the ablation study and
        # a seventh inside compare(); a field added to the gate then left every
        # banner naming one fewer fact than the gate compares.
        console.out(f"[Config] {run_fingerprint.summary(_fingerprint)}")

        # A REFUSAL HERE COSTS NOTHING AND IS THE POINT. It is above
        # tracking.start_run and above the first billed call, so a checkpoint
        # this run may not continue stops the process with no money spent, no
        # tracking run opened and -- above all -- nothing deleted.
        try:
            completed_ids = load_checkpoint(fingerprint=_fingerprint)
        except run_fingerprint.ResumeRefusal as exc:
            console.out()
            console.out(str(exc))
            raise SystemExit(1)
        results_list = load_results()

        # WAS THIS A RESUME. ONE BOOLEAN, TAKEN ONCE, HANDED TO BOTH RECORDS.
        #
        # Two things record this fact: the `runs.resumed` column and the MLflow
        # tag below. The tag read `completed_ids` directly and the column could
        # have done the same -- and TODAY THE TWO READS WOULD AGREE, because
        # both sit above `run_batch`. That is stated plainly rather than dressed
        # up as a bug that exists: nothing is currently wrong.
        #
        # WHAT IS WRONG IS THAT THE AGREEMENT IS POSITIONAL AND NOTHING ENFORCES
        # IT. `completed_ids` is MUTATED by the run -- `_on_done` adds every
        # completed stem to it -- so it stops meaning "what the checkpoint
        # handed us" the moment the first patient finishes. Two reads of it are
        # equal only while both stay above that line, and the edit that breaks
        # it is an ordinary one: moving the tag to `end_run`, or recording
        # `resumed` at finalize time beside `finished_at`. Either would make a
        # FRESH campaign report `resumed=0` in the column and `resumed=true` in
        # the index, from the same variable, with nothing to say which is right
        # and no test asking.
        #
        # Taking the boolean HERE -- the last point at which the set means only
        # what the checkpoint returned -- removes the positional dependence
        # instead of documenting it.
        _resumed = bool(completed_ids)

        # ------------------------------------------------------------------
        # 3b. Open the RUN ROW (the run-identity pass)
        # ------------------------------------------------------------------
        # BEFORE THE FIRST PATIENT, so a process that dies mid-campaign leaves
        # a row whose finished_at is NULL and whose status still reads RUNNING.
        # That row is the honest record of a crash and it is queryable as one:
        #
        #     SELECT * FROM runs WHERE finished_at IS NULL AND status='RUNNING'
        #
        # Creating it at the END instead would mean a crashed campaign left no
        # trace at all and its rows' run_id pointed at nothing -- which is the
        # state this pass exists to remove, arrived at from the other side.
        #
        # IT IS A LOCAL OF THIS FUNCTION AND IS THREADED DOWN AS AN ARGUMENT.
        # There is no module-level "current run", and that is the whole
        # mechanism behind "a second main() in one process creates a new run
        # row": there is no state to survive into the next call and therefore
        # nothing to clear. Compare clear_write_ledger() and
        # run_fingerprint.clear_cache() at the top of this function -- both
        # exist because their state IS module-level, and both are one forgotten
        # line away from describing the wrong run.
        #
        # IT RAISES, like tracking.start_run below it and for the same reason:
        # this is before the first billed call, so a run that could not be
        # recorded stops here having cost nothing. Every alternative is worse --
        # a whole campaign of rows carrying NULL run_id is indistinguishable
        # from API traffic, which is precisely what the timestamp heuristic
        # could not separate.
        #
        # THE STAMP IS THE ONE _fingerprint RESOLVED ABOVE, on this thread,
        # which also gated the resume and stamps the checkpoint. One reading,
        # three consumers; a second resolution here could straddle an alias
        # swap and record a configuration this run never had.
        #
        # db_path IS PASSED EXPLICITLY, and it is the same string
        # reconcile_writes reads, so the run row and the reconciliation cannot
        # end up describing two different files.
        _run_record_id = start_run_record(
            INVOCATION_SOURCE,
            db_path=_reconcile_db,
            fingerprint=_fingerprint,
            resumed=_resumed,
        )

        # ------------------------------------------------------------------
        # 3c. Open the tracking run (the tracking pass)
        # ------------------------------------------------------------------
        # WHY HERE AND NOT HIGHER. It is after every preflight -- the BM25
        # index, the graph, the corpus, the baseline row count and now the
        # checkpoint -- and BEFORE the first billed call, which is the first
        # patient of step 4. Two of the values it logs are only known at this
        # point: how many bundles the corpus holds, and whether this is a
        # resumed run. A start_run three blocks higher would have to log
        # neither or guess both.
        #
        # It RAISES if tracking is unavailable, and that is the point: this is
        # the last line before the run starts spending, so a campaign that
        # would not be indexed stops here having cost nothing.
        #
        # A RESUMED RUN IS A NEW TRACKING RUN, TAGGED `resumed=true`. There is
        # deliberately no run-continuation machinery: MLflow can reopen a run
        # by id, which would mean persisting that id beside the checkpoint,
        # deciding what happens when the two disagree, and merging two sets of
        # parameters that may have been produced by different code. The
        # question a reviewer asks is "which configuration produced this
        # number", and two runs sharing a checkpoint answer it as well as one
        # run reopened -- the tag is what joins them.
        #
        # MAIN THREAD ONLY. This and the two calls at the end of main() are
        # the only tracking calls in this module, and all three run before the
        # pool is created or after it has been joined; no worker touches
        # oncotriage/tracking.py.
        #
        # WRAPPED, BECAUSE THE RUN ROW IS ALREADY OPEN BY THIS LINE. start_run
        # raises when tracking is unavailable -- which is the design -- and
        # between step 3b and the `try` below there is no handler, so an
        # unwrapped raise here would leave a `runs` row at RUNNING with a NULL
        # finished_at forever, describing a campaign that never started. That is
        # the one shape this pass reserves for a process that was killed outright
        # and it must not be produced by an ordinary configuration failure.
        #
        # NOT SOLVED BY REORDERING. Putting start_run FIRST moves the orphan to
        # the other side and makes it worse: MLflow's atexit hook closes an open
        # run as FINISHED, so a tracking run orphaned by a failure below it is
        # indexed as a campaign that COMPLETED. A NULL finished_at at least reads
        # as unfinished.
        #
        # NOT SOLVED BY MOVING IT INTO THE `try` EITHER: that handler calls
        # tracking.end_run, which with no active run counts
        # `end_run:NoActiveRun` into TRACKING_DEGRADATIONS -- a degradation that
        # did not happen, reported by the code that was meant to report the one
        # that did.
        try:
            tracking.start_run(
                kind="batch",
                params={
                    "patient_count": len(fhir_files),
                    "resample_count": RESAMPLE_COUNT,
                    "resample_seed": RESAMPLE_SEED,
                },
                # THE SAME BOOLEAN THE `runs.resumed` COLUMN WAS WRITTEN
                # FROM. `completed_ids` is still untouched at this line, so a
                # direct read would give the same answer today -- and would
                # stop doing so the moment this tag moved below `run_batch`,
                # which mutates it. See `_resumed`.
                tags={"resumed": "true" if _resumed else "false"},
            )
        except BaseException:
            # THE CONSOLE RECORD FIRST, then the row, then re-raise.
            #
            # THIS HANDLER GETS IT TOO, and that is a decision rather than a
            # copy. It fires before the pool exists, so no patient has been
            # billed -- but the counters are NOT empty by then: the BM25 index
            # build, the graph compile, the corpus load, the index probe and the
            # checkpoint have all run and each can move one. Nothing else will
            # ever print them, because this path does not reach print_summary
            # and -- unlike the handler below -- it does not flush health
            # either, so the console is the ONLY record this failure can leave.
            print_crash_record(where="crash/tracking")
            finalize_run_record(_run_record_id, RUN_RECORD_STATUS_KILLED,
                                db_path=_reconcile_db)
            raise

        # THE RUN IS CLOSED ON EVERY EXIT PATH, and this try exists only for
        # that. MEASURED, not assumed: a process that opens an MLflow run and
        # then dies on an uncaught exception has that run recorded as
        # **FINISHED** -- MLflow's own atexit hook ends it and does not know the
        # process was failing. So without this, a campaign that crashed halfway
        # is indexed as a campaign that completed, which is worse than an orphan
        # left at RUNNING and is exactly the "quietly wrong record" this module
        # exists to remove.
        #
        # `except BaseException` + `raise` rather than `finally`: the normal
        # path closes the run with its real status a few lines down, and a bare
        # finally would need a flag to avoid closing it twice.
        #
        # IT CHANGES NO PIPELINE BEHAVIOUR. The exception is re-raised
        # unchanged, so every caller sees exactly what it saw before; the only
        # thing that happens first is one FAILED status write, which cannot
        # raise (see oncotriage/tracking.py).
        try:
            # ------------------------------------------------------------------
            # 4. Main batch pass
            # ------------------------------------------------------------------
            # THE SECOND MEMBER IS READ NOW, and it used to be discarded.
            # See run_batch's return: it answers "did this pass attempt every
            # patient", which `STOP_SWITCH.requested` cannot -- a stop that
            # lands during the RESAMPLE pass latches the switch over a cohort
            # the main pass had already covered.
            completed_ids, _main_pass_complete = run_batch(
                fhir_files=fhir_files,
                bm25_index=bm25_index,
                nct_ids=nct_ids,
                graph=graph,
                completed_ids=completed_ids,
                results_list=results_list,
                run_id=_run_record_id,
                db_path=_reconcile_db,
            )

            # ------------------------------------------------------------------
            # 5. Resample pass
            #    run_batch always returns main_pass_complete=True when it returns
            #    at all (a mid-loop crash exits the process before any return).
            #    Guard on completed_ids to skip gracefully if every patient errored.
            # ------------------------------------------------------------------
            # THE STOP SWITCH IS CHECKED BEFORE THE RESAMPLE PASS, AND THIS
            # IS THE MONEY. RESAMPLE_COUNT is 100, so a stop honoured only
            # INSIDE run_resample would still pay for however many of those got
            # scheduled before its first callback fired. Checking here means a
            # stopped run issues exactly zero resample calls.
            #
            # `STOP_SWITCH.requested` and NOT `poll()`: run_batch has just
            # returned, so if it stopped the switch is already latched, and a
            # poll here would additionally trip on a sentinel written in the
            # seconds since -- which run_resample's own entry gate handles,
            # with its own message. One announcement per stop.
            if STOP_SWITCH.requested:
                console.out("[Resample] SKIPPED: an operator stop is in "
                            "effect. Zero resample calls were issued.")
            elif completed_ids:
                run_resample(
                    fhir_files=fhir_files,
                    completed_ids=completed_ids,
                    bm25_index=bm25_index,
                    nct_ids=nct_ids,
                    graph=graph,
                    results_list=results_list,
                    run_id=_run_record_id,
                    db_path=_reconcile_db,
                )
            else:
                console.out("[Resample] Skipped: no successfully completed patients.")

            # ── WHAT A STOP ACTUALLY COST THIS CAMPAIGN ────────────────────
            #
            # ONE BOOLEAN, TAKEN ONCE, READ BY FOUR CONSUMERS: the checkpoint
            # decision, the closing console block, `tracking.end_run` and
            # `runs.status`. They were four independent reads of
            # `STOP_SWITCH.requested`, which is a question about whether a
            # sentinel was seen and not about whether the cohort was covered.
            #
            # STOPPED MEANS THE CAMPAIGN COVERS A PREFIX OF THE COHORT. That is
            # the whole reason the status exists and the whole reason a
            # reviewer must not sum rates over it. A stop that arrives during
            # the RESAMPLE pass costs the resample pass and nothing else: every
            # patient ran, every row is written, the checkpoint is complete.
            # Recording that as STOPPED is a claim about coverage that is not
            # true, and it is not a cosmetic one -- `campaign_summary` and
            # every reader of `runs.status` act on it. Driven end to end: a
            # 40-patient cohort covered in full, the sentinel written during
            # the resample pass, the run recorded STOPPED and the checkpoint
            # KEPT "because patients remain" with none remaining.
            #
            # HERE, AFTER THE RESAMPLE PASS, AND NOT AT run_batch's RETURN.
            # The two are equivalent in VALUE -- an incomplete main pass
            # implies the switch had already latched, and a complete one makes
            # this False whatever the switch says later -- and they are NOT
            # equivalent as a place to stand: this is where the four reads it
            # replaces were, so a revert of this one line reproduces exactly
            # the behaviour that shipped. Computed three blocks higher, the
            # obvious revert reproduces nothing, because the switch has not
            # latched yet at that point. `tests/test_runner_stop_switch.py`
            # section 5b is that control, and it is what found this.
            #
            # A STOP IS STILL ANNOUNCED EITHER WAY. What changes is only which
            # of the two things it is reported as having cut short.
            _stopped_mid_cohort = (STOP_SWITCH.requested
                                   and not _main_pass_complete)

            # ------------------------------------------------------------------
            # 6. Reconcile the writes, then the final summary
            # ------------------------------------------------------------------
            total_wall_time = time.time() - run_start
            reconciliation = reconcile_writes(db_path=_reconcile_db,
                                              rows_before=rows_before)
            _publish_reconciliation(reconciliation)

            # ONE SNAPSHOT, TWO CONSUMERS, exactly as the reconciliation above is
            # computed once and given to both the publisher and the printer. The
            # structured event goes out FIRST because emitting it can itself move
            # EMIT_FAILURES; taking the snapshot before either consumer means the
            # printed block and the logged event agree, and a failure to emit the
            # event shows up in the NEXT run's report rather than making this one's
            # two halves disagree about their own subject.
            degradation_snapshot = degradation.snapshot()
            # THE CENSUS SNAPSHOT IS TAKEN HERE TOO, and immediately after the
            # degradation one so the two blocks describe the same instant --
            # both pools are joined by this line, so no worker can move a
            # counter between them. It is NOT logged and NOT flushed: see
            # print_census_report and the block above _CENSUS_SPEC for why a
            # census stays on the console channel and out of `run_metrics`.
            census_snapshot = degradation.census_snapshot()
            degradation.log_summary(degradation_snapshot)
            # THE FINAL FLUSH, FROM THAT SAME SNAPSHOT. Three outputs describe
            # one instant: the persisted rows, the structured event above and
            # the printed block below. Taking a fresh snapshot here instead
            # would let the table and the report disagree about their own
            # subject -- and they would disagree exactly when something moved
            # between the two calls, which is when a reader most needs them to
            # agree.
            #
            # AFTER log_summary AND BEFORE print_summary is not arbitrary
            # either: emitting the event can itself move EMIT_FAILURES, and a
            # flush taken before it would omit that. Neither ordering is
            # perfect -- a counter moved by the flush cannot be in the flush --
            # and this one puts the more likely mover first.
            #
            # It cannot raise. A failure lands in RUN_METRICS_FLUSH_FAILURES,
            # which this run's own block has not printed yet at this line, so
            # the console report still names it; the TABLE cannot, for the
            # reason written at that counter.
            flush_health(_run_record_id, snapshot=degradation_snapshot,
                         db_path=_reconcile_db)

            print_summary(results_list, total_wall_time,
                          db_path=_reconcile_db,
                          reconciliation=reconciliation,
                          degradation_snapshot=degradation_snapshot,
                          census_snapshot=census_snapshot)

            # ------------------------------------------------------------------
            # 7. Clean up checkpoint only if all main-pass patients succeeded
            # ------------------------------------------------------------------
            main_results = [r for r in results_list if not r.get("is_resample")]
            main_errors = [r for r in main_results if r["status"] != "success"]

            # HOW THIS RUN ENDED, DERIVED ONCE.
            #
            # IT USED TO BE DERIVED TWICE, and the second copy sat inside a
            # console block whose own text argues that the row and the console
            # must agree ("run row FINISHED -- NOT STOPPED, because ..."). Two
            # copies of one three-way conditional, in one function, sixty lines
            # apart, keeping a promise about each other by hand. They agreed
            # only because the console copy is reached under
            # `not _stopped_mid_cohort`, which collapses the STOPPED arm -- so
            # the shorter expression there was correct BY COINCIDENCE OF ITS
            # GUARD rather than by construction, and any future edit to either
            # branch had to be made in both places or the console would state a
            # status the row does not carry. `tests/test_storage_run_identity.py`
            # pins that both now read this one name.
            #
            # STOPPED OUTRANKS BOTH, and the precedence is a decision. A run
            # that was asked to stop AND had errored patients is recorded
            # STOPPED, because the question `runs.status` answers is "how did
            # this run END" and it ended because an operator asked it to; the
            # errors are in `run_metrics`, in the summary and in
            # `inferences.status`, none of which this displaces. The other
            # ordering would report a stopped campaign as FAILED and send
            # somebody looking for a fault that is not there.
            #
            # It also cannot be conflated with FINISHED, which is the reading
            # that matters most to a reviewer: a STOPPED row means the campaign
            # covers a PREFIX of the cohort, so no rate computed over it is a
            # rate about the cohort.
            #
            # NAMED CONSTANTS RATHER THAN LITERALS: `runs.status` is a closed
            # vocabulary owned by oncotriage/storage/database_logger.py, and
            # `finalize_run_record` REFUSES a value outside it -- counting the
            # refusal and leaving the row RUNNING. A typo in a literal here
            # would therefore lose the campaign's verdict silently at the one
            # line whose whole job is to record it.
            _terminal_status = (
                RUN_RECORD_STATUS_STOPPED if _stopped_mid_cohort
                else RUN_RECORD_STATUS_FINISHED if not main_errors
                else RUN_RECORD_STATUS_FAILED)
            # THE STOP GUARD IS THE FIRST TEST AND ITS ABSENCE WOULD HAVE BEEN
            # THE MOST EXPENSIVE DEFECT IN THIS ITEM. A stopped run's cancelled
            # patients produce NO result entry at all -- `_on_done`'s
            # CancelledError branch returns before `append_result` -- so
            # `main_errors` is EMPTY on a stop in which every patient that ran
            # succeeded. The old condition would therefore have read "no errors,
            # clear the checkpoint", deleted the resume state for a cohort that
            # was deliberately only half run, and re-billed every remaining
            # patient on the next invocation, silently, at ~$0.15 each.
            #
            # An empty `main_errors` means "nothing that ran failed", which is
            # not "everything ran". Only a run that was not stopped can conclude
            # the second from the first.
            if _stopped_mid_cohort:
                console.out("[Checkpoint] " + describe_checkpoint_state(
                    "KEPT: the run was stopped, so patients remain. The next "
                    "run resumes from it."))
            elif not main_errors:
                clear_checkpoint()
                # THROUGH THE SAME READER AS THE OTHER TWO BRANCHES. On a
                # healthy run the sentence passes through unchanged; on a run
                # whose writes failed, "Cleared for next fresh run" is a claim
                # about a file that was never written, and the operator is
                # entitled to know their next invocation re-bills the cohort.
                console.out("[Checkpoint] " + describe_checkpoint_state(
                    "Cleared for next fresh run."))
            else:
                # SAME READER, AND THIS BRANCH NEEDED IT MOST OF THE THREE:
                # "Re-run to retry failures" promises a re-run that retries the
                # FAILURES, and with the checkpoint stale or absent a re-run
                # retries everything, at a live Stage 5 call each.
                console.out("[Checkpoint] " + describe_checkpoint_state(
                    f"Kept: {len(main_errors)} patients errored. Re-run to "
                    f"retry failures."))

            # ------------------------------------------------------------------
            # 8. Close the tracking run (the tracking pass)
            # ------------------------------------------------------------------
            # The metrics come from the SAME objects print_summary was handed --
            # the same results list, the same reconciliation dict and the same
            # degradation snapshot -- so the index and the printed block describe
            # one instant and cannot disagree. That is why the snapshot is taken
            # once above and passed twice, rather than re-read here.
            #
            # THE STATUS IS THE MAIN PASS'S, as the brief specifies, and it is the
            # same fact the checkpoint decision above is made on: main-pass errors
            # remaining means the run did not finish its job, whatever the resample
            # pass did. Note it is deliberately NOT the reconciliation verdict --
            # a run whose patients all succeeded but whose rows were lost is
            # FINISHED with `reconciliation_complete=0`, and collapsing those two
            # into one status would delete the distinction the write-durability
            # pass exists to surface.
            #
            # Neither call raises. See oncotriage/tracking.py: by this line the run
            # has spent its money and written its rows, and an index failure must
            # not take those with it. It lands in TRACKING_DEGRADATIONS instead --
            # which the NEXT run's degradation block reports, since this one has
            # already printed.
            tracking.log_run_metrics(
                tracking_metrics(results_list, total_wall_time,
                                 reconciliation=reconciliation,
                                 degradation_snapshot=degradation_snapshot))
            # THE TRACKING STATUS IS MLflow's VOCABULARY, NOT THIS PROJECT'S,
            # and a stop maps to KILLED there. MLflow's terminal set is
            # FINISHED / FAILED / KILLED and its own definition of KILLED is
            # "run killed by user", which is literally what a stop switch is --
            # so this is the closest TRUE statement the index can carry, not a
            # rounding. The divergence from `runs.status` (STOPPED) is stated
            # rather than smoothed over, exactly as the crash handler below
            # states its own KILLED-here / FAILED-there divergence.
            #
            # `end_run` replaces an unrecognised status with FAILED, so passing
            # "STOPPED" straight through would have indexed every stopped
            # campaign as a failure -- true of nothing, and worse than KILLED.
            tracking.end_run(
                # TRANSLATED FROM `_terminal_status`, NOT RE-DERIVED FROM
                # `_stopped_mid_cohort` AND `main_errors`. This used to be a
                # THIRD independent copy of the same three-way conditional, so
                # the "divergence" the comment above declares was a property of
                # two expressions that happened to differ in one arm rather
                # than a mapping anybody had written down. `TRACKING_STATUS_FOR`
                # is that mapping, and its keys are checked at import against
                # `RUN_RECORD_TERMINAL_STATUSES` -- so a fifth run status added
                # to the storage vocabulary fails at load here instead of
                # silently reaching `end_run`, which replaces what it does not
                # recognise with FAILED.
                #
                # The boolean it ultimately rests on is still
                # `_stopped_mid_cohort` AND NOT `STOP_SWITCH.requested`: a stop
                # that only cut the resample pass short left a campaign that
                # covered its cohort, and indexing it KILLED would tell every
                # later reader the numbers cover a prefix.
                status=TRACKING_STATUS_FOR[_terminal_status],
                artifacts=[
                    # The results file, as it stands on disk after this run.
                    _results_path(),
                    # The degradation block, which exists NOWHERE as a file -- it
                    # is a list of lines print_summary emitted. See end_run's
                    # `artifacts` argument for why the (filename, text) form is
                    # accepted rather than the caller writing a temp file to hand
                    # over a path.
                    ("degradation_summary.txt",
                     "\n".join(degradation.report_lines(degradation_snapshot))),
                ])

            # ------------------------------------------------------------------
            # 9. Close the run row (the run-identity pass)
            # ------------------------------------------------------------------
            # THE STATUS IS THE MAIN PASS'S, the same fact the checkpoint
            # decision and tracking.end_run above are both made on -- one run,
            # one verdict, stated once. It is deliberately NOT the
            # reconciliation's: a campaign whose patients all succeeded and
            # whose rows were lost is FINISHED here and incomplete there, and
            # collapsing the two would delete the distinction the
            # write-durability pass exists to surface.
            #
            # LAST, AND THE POSITION IS LOAD-BEARING RATHER THAN TIDY. Every
            # other statement in this `try` can raise -- tracking_metrics walks
            # the results list, _results_path resolves a path, report_lines
            # formats a snapshot -- and the handler below finalizes to KILLED.
            # With this call anywhere ABOVE them, a raise in between would
            # OVERWRITE a FINISHED row with KILLED and report a completed
            # campaign as a crashed one. Being the last statement makes the two
            # finalize paths mutually exclusive by construction, which is
            # stronger than a "have I finalized yet" flag somebody has to
            # remember to set.
            #
            # THE COST IS STATED: finished_at now includes the seconds the
            # tracking store spent attaching artifacts. That is the honest end
            # of the run rather than the end of its patients, and
            # `inferences.timestamp` is what bounds the patients.
            #
            # IT CANNOT RAISE. By this line the run has spent one live Stage 5
            # call per patient and written its rows; a bookkeeping failure must
            # not take those with it. A failure lands in RUN_RECORD_FAILURES,
            # which the NEXT run's degradation block reports -- this one has
            # already printed, exactly as the tracking calls above already
            # accept.
            # THE STOP REPORT IS PRINTED **ABOVE** finalize_run_record, AND
            # THE ORDER IS AN INVARIANT RATHER THAN A PREFERENCE. The finalize
            # call is required to be the LAST statement of this `try` -- see the
            # note on it, and `tests/test_storage_run_identity.py`, which pins
            # exactly that and CAUGHT the first version of this block sitting
            # below it. With console lines after the finalize, a formatting
            # failure in any of them would raise into the crash handler and
            # OVERWRITE a STOPPED row with KILLED: a clean operator stop
            # recorded as a crash, by the code whose whole job is to say they
            # are different.
            #
            # Printed first, a failure here leaves the row UNFINALIZED and the
            # crash handler finalizes it KILLED -- which is then the truth: the
            # run did die, in the reporting.
            if STOP_SWITCH.requested and not _stopped_mid_cohort:
                # THE STOP LANDED AFTER THE COHORT WAS COVERED. It is still
                # announced -- an operator asked for something and got it, and
                # the resample pass really was skipped -- but the run is NOT
                # recorded STOPPED, and this block says so in as many words
                # rather than leaving the console and the row disagreeing.
                console.out()
                console.out("=" * 80)
                console.out("STOP REQUESTED AFTER THE COHORT WAS COVERED.")
                console.out(f"  requested by   {STOP_SWITCH.path}")
                if STOP_SWITCH.message:
                    console.out(f"  note           {STOP_SWITCH.message}")
                console.out(f"  noticed in     the {STOP_SWITCH.detected_in}")
                console.out("  main pass      COMPLETE: every patient was "
                            "attempted, none was cancelled and none was left "
                            "unsubmitted")
                console.out("  what it cost   the RESAMPLE pass, and nothing "
                            "else")
                console.out("  run row        "
                            + _terminal_status
                            + " -- NOT STOPPED, because STOPPED means the "
                              "campaign covers a PREFIX of the cohort and this "
                              "one does not")
                console.out("  sentinel       still present; delete it before "
                            "the next run:")
                console.out(f"      rm {STOP_SWITCH.path}")
                console.out("=" * 80)

            if _stopped_mid_cohort:
                console.out()
                console.out("=" * 80)
                console.out("RUN STOPPED AT THE OPERATOR'S REQUEST.")
                console.out(f"  requested by   {STOP_SWITCH.path}")
                if STOP_SWITCH.message:
                    console.out(f"  note           {STOP_SWITCH.message}")
                console.out(f"  noticed in     the {STOP_SWITCH.detected_in}")
                console.out("  run row        STOPPED (not KILLED, not "
                            "FINISHED)")
                # NOT A LITERAL "kept". See describe_checkpoint_state: with the
                # checkpoint directory unwritable this line used to promise a
                # resume that would have re-billed the whole cohort.
                console.out("  checkpoint     " + describe_checkpoint_state(
                    "kept; the next run resumes from it"))
                console.out("  TO RESUME      delete the sentinel and run "
                            "again:")
                console.out(f"      rm {STOP_SWITCH.path}")
                console.out("      python \"25- Batch Runner.py\"")
                console.out("=" * 80)

            # THE VERDICT IS `_terminal_status`, DERIVED ONCE where
            # `main_errors` is bound -- the precedence argument (STOPPED
            # outranks both) lives there, beside the expression, rather than
            # here beside one of its two readers. This call and the console
            # block above read the SAME name, which is what makes the promise
            # that block's own text makes ("run row FINISHED -- NOT STOPPED")
            # structural instead of a convention.
            finalize_run_record(
                _run_record_id,
                _terminal_status,
                db_path=_reconcile_db,
            )

            return results_list
        except BaseException:
            # KILLED, NOT FAILED, AND THE TWO ARE DIFFERENT FINDINGS. FAILED on
            # the success path above means "the campaign ran to the end and some
            # patients errored"; KILLED here means "the process did not get to
            # the end" -- an unhandled exception, a Ctrl-C, a SystemExit. An
            # operator reading `runs` needs to tell a campaign that finished
            # badly from one that stopped, because only the second has patients
            # that were never attempted.
            #
            # tracking.end_run stays FAILED because MLflow's vocabulary is what
            # it is and this module does not get to widen it; the divergence is
            # stated rather than smoothed over.
            #
            # AND IT IS DELIBERATELY *NOT* `TRACKING_STATUS_FOR[KILLED]`, which
            # would be "KILLED". This is the one place the row and the index
            # are meant to disagree, so routing it through the mapping would
            # silently change what a crashed campaign is indexed as, in a pass
            # whose whole subject is removing UNINTENDED copies of a verdict.
            # The mapping exists for the SUCCESS path, where the two derivations
            # were meant to agree and were kept in step by hand. This literal is
            # a decision; that one was a duplicate.
            #
            # THIS IS REACHABLE ONLY WHEN THE SUCCESS-PATH FINALIZE DID NOT
            # RUN, because that call is the LAST statement of the `try`. So the
            # two never both fire and this one can never overwrite a FINISHED
            # row -- see the note there.
            #
            # A FINALIZED KILLED IS BETTER THAN AN UNFINALIZED ROW, which is
            # why this is attempted at all rather than left to the NULL. The
            # NULL shape survives for what it is actually for: a process that
            # had no chance to run any handler -- SIGKILL, a power loss, an OOM
            # kill. Both are queryable and they are not the same event.
            #
            # finalize_run_record does not raise, so it cannot displace the
            # exception on its way out; `raise` re-raises the original,
            # unchanged, exactly as before this pass.
            #
            # THE HEALTH RECORD IS FLUSHED FIRST, AND THIS IS THE PATH THE
            # WHOLE FEATURE IS FOR. A crashed campaign prints no degradation
            # block, so before this pass everything its counters held was lost
            # at exit. The periodic flushes already left a record that is at
            # most one patient stale; this one closes the gap, so the row set
            # is current at the moment the run is marked KILLED rather than at
            # the last patient that completed.
            #
            # IT TAKES ITS OWN SNAPSHOT, and no "same instant" promise is
            # broken by that: there is no printed report on this path for it to
            # disagree with. It cannot raise, so it cannot displace the
            # exception either.
            flush_health(_run_record_id, db_path=_reconcile_db)
            # THE CONSOLE RECORD, AND THE CENSUS HALF OF IT SURVIVES NOWHERE
            # ELSE. The flush above persists the DEGRADATION totals into
            # `run_metrics`; the census is excluded from that table by a closed
            # -category ruling, so on a crash this print is the only place its
            # counts have ever existed.
            #
            # AFTER the flush, so a formatting failure cannot cost the persisted
            # record -- the durable write is the one worth protecting, and the
            # ordering is what makes that true rather than the guard alone.
            #
            # It cannot raise and cannot displace the exception; see
            # print_crash_record. The `raise` below is still the original.
            print_crash_record(where="crash")
            finalize_run_record(_run_record_id, RUN_RECORD_STATUS_KILLED,
                                db_path=_reconcile_db)
            tracking.end_run(status="FAILED")
            raise



#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""
