# Full-Scale Batch Runner
#########################

"""
Direct Batch Pipeline Runner

Runs the full matching pipeline on all FHIR patients directly in Python
without HTTP overhead. Faster and more reliable than FastAPI for bulk
evaluation runs.

Architecture:
    - Exec chain: 01 -> 02 -> 03 -> 07 -> 13 -> 14
    - BM25 index and LangGraph graph built ONCE, shared across all patients
    - Checkpoint/resume: crash-safe, restarts from last completed patient
    - Resample pass: re-runs a random subset of already-processed patients
      to simulate real-world repeat submissions

Execution flow:
    1. Load exec chain (all imports, config, clients, pipeline functions)
    2. Build BM25 index + compile LangGraph graph
    3. Load all FHIR patient files
    4. Skip patients already in checkpoint (resume support)
    5. Process patients in configurable batch sizes with progress reporting
    6. Run resample pass on randomly selected already-processed patients
    7. Print final summary report

Run from terminal:
    cd "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"
    python "25- Batch Runner.py"
"""


# ===========================================================================
# EXEC CHAIN: 01 -> 02 -> 03 -> 07 -> 13 -> 14
# ===========================================================================

_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["03- Config.py", "07- FHIR Parser.py", "13- LangGraph Agent.py", "14- Database Logger.py"],
    caller_file=_code_dir + "25- Batch Runner.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 07 → 13 → 14",
)

# ===========================================================================
# THREAD SAFETY
# ===========================================================================

_db_lock = threading.Lock()

_original_log_inference = log_inference

def _thread_safe_log_inference(*args, **kwargs):
    with _db_lock:
        return _original_log_inference(*args, **kwargs)

log_inference = _thread_safe_log_inference

_checkpoint_lock = threading.Lock()


#------------------------------------------------------------------------------


# ===========================================================================
# CHECKPOINT HELPERS
# ===========================================================================

def _checkpoint_path() -> Path:
    return Path(checkpoint_path) / CHECKPOINT_FILENAME


def _results_path() -> Path:
    return Path(checkpoint_path) / RESULTS_FILENAME


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
        print(f"[Checkpoint] Resuming: {len(completed)} patients already completed.")
        return completed
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[Checkpoint] WARNING: Could not read checkpoint ({e}). Starting fresh.")
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
            print(f"[Checkpoint] WARNING: Could not write checkpoint ({e}). Continuing.")
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
        print("[Checkpoint] Cleared.")


def clear_results() -> None:
    """Delete results file to start a fresh run."""
    rp = _results_path()
    if rp.exists():
        rp.unlink()
        print("[Results] Cleared.")


def clear_all() -> None:
    """Delete both checkpoint and results files for a completely fresh run."""
    clear_checkpoint()
    clear_results()
    print("[State] All batch runner state cleared. Ready for fresh run.")


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
    with _db_lock:
        results_list.append(entry)
        rp = _results_path()
    
        tmp_path = rp.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(results_list, f, indent=2)
            os.replace(tmp_path, rp)
        except OSError as e:
            print(f"[Results] WARNING: Could not write results file ({e}). Continuing.")
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
        Never raises -- all exceptions are caught and returned as error entries.
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

        print(
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

    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}"
        print(f"  {run_label} {patient_id_hint} | EXCEPTION: {error_msg}")
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

    print("=" * 80)
    print("MAIN BATCH PASS")
    print("=" * 80)
    print(f"Total patient files:    {total_files}")
    print(f"Already completed:      {already_done}")
    print(f"Remaining to process:   {total_pending}")
    print(f"Concurrent workers:     {MAX_WORKERS}")
    print()

    if not pending_files:
        print("All patients already completed. Skipping main pass.")
        return completed_ids, True

    batch_success = 0
    batch_error = 0

    # Keep the progress bar prominent
    print()
    print("*" * 80)
    progress = tqdm(
        total=len(pending_files),
        desc="🔬 MAIN PASS PROGRESS",
        unit="patient",
        bar_format="{desc}: {percentage:3.0f}%|{bar:40}| {n_fmt}/{total_fmt} [Elapsed: {elapsed} | ETA: {remaining} | {rate_fmt}] {postfix}",
        ncols=120,
        smoothing= 0.1 # to reduce the eta fluctuation 
    )

    def _on_done(future, fhir_path):

        nonlocal batch_success, batch_error
        try:
            entry = future.result()
        except Exception as e:
            batch_error += 1
            progress.update(1)
            tqdm.write(f"  [CALLBACK ERROR] {type(e).__name__}: {e}")
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
    
    _original_print = builtins.print
    
    def _tqdm_print(*args, **kwargs):
        """Route all print() calls through tqdm.write() to keep progress bar at bottom."""
        text = " ".join(str(a) for a in args)
        tqdm.write(text)
    
    builtins.print = _tqdm_print
    
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
        print("\n[INTERRUPTED] Waiting for active threads to finish...")
        executor.shutdown(wait=True, cancel_futures=True)
        print("[INTERRUPTED] Checkpoint saved. Safe to resume.")

    finally:
        progress.close()
        builtins.print = _original_print

    print()
    print("=" * 80)
    print(f"MAIN BATCH COMPLETE: {batch_success} success, {batch_error} errors")
    print("=" * 80)
    print()

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
    print("=" * 80)
    print("RESAMPLE PASS")
    print("=" * 80)

    completed_files = [
        f for f in fhir_files
        if Path(f).stem in completed_ids
    ]

    if not completed_files:
        print("No completed patients available for resampling. Skipping.")
        return

    actual_resample = min(RESAMPLE_COUNT, len(completed_files))

    random.seed(RESAMPLE_SEED)
    resample_files = random.sample(completed_files, actual_resample)

    print(f"Resampling {actual_resample} patients (seed={RESAMPLE_SEED}).")
    print(f"Concurrent workers: {MAX_WORKERS}")
    print()

    resample_success = 0
    resample_error = 0

    progress = tqdm(total=actual_resample, desc="Resample", unit="patient")

    _original_print = builtins.print
    
    def _tqdm_print(*args, **kwargs):
        text = " ".join(str(a) for a in args)
        tqdm.write(text)
    
    builtins.print = _tqdm_print


    def _on_done(future):
        nonlocal resample_success, resample_error
        try:
            entry = future.result()
        except Exception as e:
            resample_error += 1
            progress.update(1)
            tqdm.write(f"  [CALLBACK ERROR] {type(e).__name__}: {e}")
            return

        append_result(results_list, entry)

        if entry["status"] == "success":
            resample_success += 1
        else:
            resample_error += 1

        progress.set_postfix(ok=resample_success, err=resample_error)
        progress.update(1)

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
        print("\n[INTERRUPTED] Waiting for active threads to finish...")
        executor.shutdown(wait=True, cancel_futures=True)
        print("[INTERRUPTED] Resample interrupted. Results saved.")

    finally:
        progress.close()
        builtins.print = _original_print

    print()
    print("=" * 80)
    print(f"RESAMPLE COMPLETE: {resample_success} success, {resample_error} errors")

# ===========================================================================
# SUMMARY REPORT
# ===========================================================================

def print_summary(results_list: list, total_wall_time: float) -> None:
    """
    Print a concise final summary report for the entire batch run.

    Args:
        results_list:     Full list of per-patient result dicts.
        total_wall_time:  Total elapsed seconds for the entire run.
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

    print()
    print("=" * 80)
    print(f"{Project_Name}: BATCH RUN SUMMARY")
    print("=" * 80)
    print(f"Total wall time:  {total_wall_time/60:.1f} min")
    print(f"Results file:     {_results_path()}")
    print(f"Checkpoint file:  {_checkpoint_path()}")
    print(f"Database:         {inferences_path}")
    print()

    print("--- MAIN PASS ---")
    if main_stats:
        for k, v in main_stats.items():
            print(f"  {k:<30} {v}")
    else:
        print("  No main pass records.")
    print()

    print("--- RESAMPLE PASS ---")
    if resample_stats:
        for k, v in resample_stats.items():
            print(f"  {k:<30} {v}")
    else:
        print("  No resample records.")
    print()

    if error_counts:
        print("--- ERROR BREAKDOWN ---")
        for msg, count in sorted(error_counts.items(), key=lambda x: -x[1]):
            print(f"  [{count}x] {msg}")
        print()

    print("=" * 80)
    print("Run complete.")
    print("=" * 80)
    print()


# ===========================================================================
# MAIN
# ===========================================================================

if __name__ == "__main__":
    
    with CaffeinateSession("Batch Runner"):

        run_start = time.time()
    
        print()
        print("=" * 80)
        print(f"{Project_Name}: BATCH RUNNER")
        print("=" * 80)
        print()
    
        # ------------------------------------------------------------------
        # 1. Build shared pipeline resources (once, reused for all patients)
        # ------------------------------------------------------------------
        print("[Setup] Building BM25 index from Qdrant...")
        try:
            bm25_index, nct_ids = build_bm25_index_from_qdrant()
            if not nct_ids:
                raise ValueError("Qdrant returned 0 trials. Run 11- RAG Trial Indexer first.")
        except Exception as e:
            print(f"[FATAL] Could not build BM25 index: {e}")
            raise SystemExit(1)
    
        print(f"[Setup] BM25 index ready: {len(nct_ids)} trials.\n")
    
        print("[Setup] Compiling LangGraph pipeline...")
        try:
            graph = build_matching_graph()
        except Exception as e:
            print(f"[FATAL] Could not compile LangGraph graph: {e}")
            raise SystemExit(1)
    
        print("[Setup] LangGraph pipeline ready.\n")
    
        # ------------------------------------------------------------------
        # 2. Load FHIR patient files
        # ------------------------------------------------------------------
        fhir_files = sorted(glob.glob(data_fhir_path + "*.json"))
    
        if not fhir_files:
            print(f"[FATAL] No FHIR files found in: {data_fhir_path}")
            raise SystemExit(1)
    
        print(f"[Setup] Found {len(fhir_files)} FHIR patient files.\n")
    
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
            print("[Resample] Skipped: no successfully completed patients.")
    
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
            print("[Checkpoint] Cleared for next fresh run.")
        else:
            print(f"[Checkpoint] Kept: {len(main_errors)} patients errored. Re-run to retry failures.")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""