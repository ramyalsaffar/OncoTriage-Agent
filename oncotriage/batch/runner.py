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

import glob
import json
import os
import random
import sqlite3
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from tqdm import tqdm

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
from oncotriage.agent.evaluation import MatchingModelMismatchError
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.storage.database_logger import (
    RUN_METRICS_FLUSH_FAILURES,
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


def _refuse_checkpoint(outcome: str, detail: str, cp) -> None:
    """Count, log and raise. Never deletes, never skips, never re-runs."""
    CHECKPOINT_FAULTS[f"refused:{outcome}"] += 1
    lines = run_fingerprint.refusal_lines(
        outcome, detail, f"the batch checkpoint at {cp}",
        _checkpoint_remediation())
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
        _refuse_checkpoint(outcome, detail, cp)

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
            console.out(f"[Checkpoint] WARNING: Could not write checkpoint ({e}). Continuing.")
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


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
            console.out(f"[Results] WARNING: Could not write results file ({e}). Continuing.")
            # Clean up temp file if it was created
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


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
        # run_id ties this row to the `runs` record main() opened. It is
        # forwarded, never resolved here, and db_path is still left to
        # log_inference so the row and the reconciliation read the same
        # resolution.
        write_result = log_inference(result, patient_data, run_id=run_id)
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


def run_batch(fhir_files: list, bm25_index: object, nct_ids: list, graph: object, completed_ids: set, results_list: list, run_id=None,) -> tuple:
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

    def _on_done(future, fhir_path):

        nonlocal batch_success, batch_error
        try:
            entry = future.result()
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
        flush_health(run_id)

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
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for fhir_path in pending_files:
                future = executor.submit(
                    process_patient,
                    fhir_path=fhir_path,
                    graph=graph,
                    is_resample=False,
                    run_id=run_id,
                )
                future.add_done_callback(lambda f, fp=fhir_path: _on_done(f, fp))
                futures.append(future)

            # Wait for all to complete (callbacks handle progress)
            for future in futures:
                future.result()

    except KeyboardInterrupt:
        console.out("\n[INTERRUPTED] Waiting for active threads to finish...")
        executor.shutdown(wait=True, cancel_futures=True)
        console.out("[INTERRUPTED] Checkpoint saved. Safe to resume.")

    finally:
        progress.close()
        console.detach_bar(_bar_token)

    console.out()
    console.out("=" * 80)
    console.out(f"MAIN BATCH COMPLETE: {batch_success} success, {batch_error} errors")
    console.out("=" * 80)
    console.out()

    return completed_ids, True


# ===========================================================================
# RESAMPLE PASS
# ===========================================================================


def run_resample(fhir_files: list, completed_ids: set, bm25_index: object, nct_ids: list, graph: object, results_list: list, run_id=None,) -> None:
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

    def _on_done(future):
        nonlocal resample_success, resample_error
        try:
            entry = future.result()
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
        flush_health(run_id)

        progress.set_postfix(ok=resample_success, err=resample_error)
        progress.update(1)

    # Same submit-everything-then-drain structure as run_batch(), and therefore
    # the same behaviour on a MatchingModelMismatchError: it escapes eventually
    # but the pass does not stop early. The reasoning is written out once, at
    # the executor in run_batch(); this is the second instance of it, not a
    # different case.
    try:
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = []
            for fhir_path in resample_files:
                future = executor.submit(
                    process_patient,
                    fhir_path=fhir_path,
                    graph=graph,
                    is_resample=True,
                    run_id=run_id,
                )
                future.add_done_callback(lambda f: _on_done(f))
                futures.append(future)

            for future in futures:
                future.result()

    except KeyboardInterrupt:
        console.out("\n[INTERRUPTED] Waiting for active threads to finish...")
        executor.shutdown(wait=True, cancel_futures=True)
        console.out("[INTERRUPTED] Resample interrupted. Results saved.")

    finally:
        progress.close()
        console.detach_bar(_bar_token)

    console.out()
    console.out("=" * 80)
    console.out(f"RESAMPLE COMPLETE: {resample_success} success, {resample_error} errors")

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
                  degradation_snapshot: dict = None) -> None:
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

    with CaffeinateSession("Batch Runner"):

        run_start = time.time()

        console.out()
        console.out("=" * 80)
        console.out(f"{Project_Name}: BATCH RUNNER")
        console.out("=" * 80)
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
                tags={"resumed": "true" if completed_ids else "false"},
            )
        except BaseException:
            finalize_run_record(_run_record_id, "KILLED", db_path=_reconcile_db)
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
            completed_ids, _ = run_batch(
                fhir_files=fhir_files,
                bm25_index=bm25_index,
                nct_ids=nct_ids,
                graph=graph,
                completed_ids=completed_ids,
                results_list=results_list,
                run_id=_run_record_id,
            )

            # ------------------------------------------------------------------
            # 5. Resample pass
            #    run_batch always returns main_pass_complete=True when it returns
            #    at all (a mid-loop crash exits the process before any return).
            #    Guard on completed_ids to skip gracefully if every patient errored.
            # ------------------------------------------------------------------
            if completed_ids:
                run_resample(
                    fhir_files=fhir_files,
                    completed_ids=completed_ids,
                    bm25_index=bm25_index,
                    nct_ids=nct_ids,
                    graph=graph,
                    results_list=results_list,
                    run_id=_run_record_id,
                )
            else:
                console.out("[Resample] Skipped: no successfully completed patients.")

            # ------------------------------------------------------------------
            # 6. Reconcile the writes, then the final summary
            # ------------------------------------------------------------------
            total_wall_time = time.time() - run_start
            reconciliation = reconcile_writes(rows_before=rows_before)
            _publish_reconciliation(reconciliation)

            # ONE SNAPSHOT, TWO CONSUMERS, exactly as the reconciliation above is
            # computed once and given to both the publisher and the printer. The
            # structured event goes out FIRST because emitting it can itself move
            # EMIT_FAILURES; taking the snapshot before either consumer means the
            # printed block and the logged event agree, and a failure to emit the
            # event shows up in the NEXT run's report rather than making this one's
            # two halves disagree about their own subject.
            degradation_snapshot = degradation.snapshot()
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
                          reconciliation=reconciliation,
                          degradation_snapshot=degradation_snapshot)

            # ------------------------------------------------------------------
            # 7. Clean up checkpoint only if all main-pass patients succeeded
            # ------------------------------------------------------------------
            main_results = [r for r in results_list if not r.get("is_resample")]
            main_errors = [r for r in main_results if r["status"] != "success"]
            if not main_errors:
                clear_checkpoint()
                console.out("[Checkpoint] Cleared for next fresh run.")
            else:
                console.out(f"[Checkpoint] Kept: {len(main_errors)} patients errored. Re-run to retry failures.")

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
            tracking.end_run(
                status="FINISHED" if not main_errors else "FAILED",
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
            finalize_run_record(
                _run_record_id,
                "FINISHED" if not main_errors else "FAILED",
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
            finalize_run_record(_run_record_id, "KILLED", db_path=_reconcile_db)
            tracking.end_run(status="FAILED")
            raise



#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""
