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
import threading
import time
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
    log_inference,
    resolve_inference_db_path,
)
from oncotriage.utils import CaffeinateSession
from oncotriage.observability import console


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


def load_checkpoint() -> set:
    """
    Load set of already-completed filename stems from checkpoint file.

    The checkpoint key is always the FHIR filename stem (without extension),
    e.g. "Firstname_Lastname_UUID". This is consistent with how pending_files
    and completed_files are filtered, avoiding UUID vs. stem mismatch bugs.

    Returns:
        Set of filename stem strings that have been successfully processed.
        Empty set if no checkpoint file exists.
    """
    cp = _checkpoint_path()
    if not cp.exists():
        return set()
    try:
        with open(cp, "r") as f:
            data = json.load(f)
        completed = set(data.get("completed_stems", []))
        console.out(f"[Checkpoint] Resuming: {len(completed)} patients already completed.")
        return completed
    except (json.JSONDecodeError, KeyError) as e:
        console.out(f"[Checkpoint] WARNING: Could not read checkpoint ({e}). Starting fresh.")
        return set()


def save_checkpoint(completed_stems: set) -> None:
    """
    Atomically persist completed filename stems to checkpoint file.

    Uses a temp file + os.replace() so a crash mid-write never corrupts
    the checkpoint -- the exact file that protects against crashes.
    Called after every successfully processed patient so a crash
    loses at most MAX_WORKERS patients (one per active thread).

    Args:
        completed_stems: Full set of completed filename stem strings so far.
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

def load_results() -> list:
    """
    Load existing per-patient results from results file.

    Returns:
        List of result summary dicts. Empty list if file does not exist.
    """
    rp = _results_path()
    if not rp.exists():
        return []
    try:
        with open(rp, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


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
# CORE: PROCESS A SINGLE PATIENT
# ===========================================================================

def process_patient(
    fhir_path: str,
    graph: object,
    is_resample: bool = False,
) -> dict:
    """
    Run the full pipeline for one patient file.

    Mirrors FastAPI's _run_matching_pipeline() exactly:
        parse_fhir_bundle -> match_patient_to_trials -> log_inference

    Args:
        fhir_path:   Absolute path to patient FHIR JSON bundle file.
        graph:       Compiled LangGraph StateGraph (shared, read-only).
        is_resample: True when this is a resample re-run of an existing patient.

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

        log_inference(result, patient_data)

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


def run_batch(fhir_files: list, bm25_index: object, nct_ids: list, graph: object, completed_ids: set, results_list: list,) -> tuple:
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


def run_resample(fhir_files: list, completed_ids: set, bm25_index: object, nct_ids: list, graph: object, results_list: list,) -> None:
    """
    Re-run a random subset of already-processed patients using concurrent threads.

    Simulates real-world repeat submissions where the same patient
    may be evaluated more than once. All re-runs are logged to the
    same SQLite DB so drift detection and DB queries see duplicates.

    Does NOT update the checkpoint (resample entries are supplemental,
    not required for resume logic).

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

def print_summary(results_list: list, total_wall_time: float, db_path=None) -> None:
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
    """
    main_results = [r for r in results_list if not r.get("is_resample")]
    resample_results = [r for r in results_list if r.get("is_resample")]

    def _stats(records: list) -> dict:
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

    main_stats = _stats(main_results)
    resample_stats = _stats(resample_results)

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

    console.out("=" * 80)
    console.out("Run complete.")
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
    """
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

        # ------------------------------------------------------------------
        # 3. Load checkpoint and results (resume support)
        # ------------------------------------------------------------------
        completed_ids = load_checkpoint()
        results_list = load_results()

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
            )
        else:
            console.out("[Resample] Skipped: no successfully completed patients.")

        # ------------------------------------------------------------------
        # 6. Final summary
        # ------------------------------------------------------------------
        total_wall_time = time.time() - run_start
        print_summary(results_list, total_wall_time)

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

        return results_list


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""
