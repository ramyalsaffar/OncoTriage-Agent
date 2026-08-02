# Ablation Study
################


#------------------------------------------------------------------------------


"""
Ablation Study
==============

Measures the contribution of each pipeline stage by running the full
matching pipeline with one stage disabled at a time on a stratified
patient sample.

Purpose
-------
Produce a comparison table showing how each stage affects:
  - Candidate funnel  (retrieved → reranked → rule-filtered → evaluated)
  - Match quality     (eligible count, average match score)
  - Cost              (GPT-4o tokens, estimated USD via get_model_cost())
  - Latency           (per-stage and total seconds per patient)
  - Filter activity   (MeSH / stage / histology drops per patient)

This data supports the "Ablation Study" section of the paper,
demonstrating that each pipeline stage earns its place.

Ablation Configurations (7)
----------------------------
  1. full_pipeline       — all stages active (baseline)
  2. no_mesh_filter      — skip MeSH cancer site relevance filter
  3. no_stage_filter     — skip cancer stage mismatch filter
  4. no_histology_filter — skip histology mismatch filter
  5. no_cross_encoder    — skip MedCPT cross-encoder reranking
  6. bm25_only           — disable vector search (BM25 retrieval only)
  7. vector_only         — disable BM25 (vector retrieval only)

Each config is run on the SAME stratified patient sample. Only one
variable changes per config — all other stages remain active.

Integration with File 13
------------------------
This file passes an 'ablation_flags' dict into the LangGraph initial
state. File 13 nodes check these flags via:

    state.get("ablation_flags") or {}

and skip specific logic when flagged. Default is {} (all stages active),
so production pipeline, FastAPI server, and batch evaluation are unaffected.

Required edits to File 13 (5 locations):
  Edit 1:  TrialMatchState — add 'ablation_flags: Dict'
  Edit 1b: match_patient_to_trials() — add '"ablation_flags": {}' to initial state
  Edit 2:  node_hybrid_retrieval() — wrap BM25/vector blocks in retrieval_mode check
  Edit 3:  node_cross_encoder_rerank() — add skip guard after empty-trials guard
  Edit 4:  node_rule_based_filter() — wrap MeSH/stage/histology in skip guards

Output
------
  - ablation_results.db   — SQLite database in results_path (separate from
                            production inferences.db to avoid polluting drift
                            detection and the Reproducibility dashboard)
  - ablation_summary.json — Machine-readable summary for paper figures
  - Console summary table with per-config averages and deltas vs baseline

Sampling
--------
Stratified by cancer type to ensure the sample covers lung, breast,
colorectal, hematologic, and other cancers proportionally.
Seed 42 for reproducibility. Default N=75.

Cost Estimate
-------------
7 configs × 75 patients = 525 pipeline runs.
At ~15 trials evaluated per patient × ~$0.005/evaluation:
Estimated total: ~$2.50-$4.00 GPT-4o cost, ~3-5 hours runtime.

Usage
-----
    python "26- Ablation Study.py"                   # Full run (75 patients)
    python "26- Ablation Study.py" --sample-size 20  # Quick test
    python "26- Ablation Study.py" --summary-only    # Reprint last results
    python "26- Ablation Study.py" --configs full_pipeline no_mesh_filter
"""


#------------------------------------------------------------------------------


# ===========================================================================
# EXEC CHAIN: 01 → 02 → 03 → 07 → 13 → 14
# ===========================================================================
# File 13's header loads 08 (Cancer Code Registry), 09 (MeSH Filter),
# and 10 (Structured Eligibility Extractor) via its internal exec chain.
# Do NOT include 08/09/10 here — they would double-load.
# ===========================================================================

_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    [
        "03- Config.py",
        "07- FHIR Parser.py",
        "13- LangGraph Agent.py",
        "14- Database Logger.py",
    ],
    caller_file=_code_dir + "26- Ablation Study.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 07 → 13 → 14",
)


#------------------------------------------------------------------------------


# ===========================================================================
# THREAD SAFETY
# ===========================================================================

_ablation_db_lock = threading.Lock()
_ablation_checkpoint_lock = threading.Lock()


# ===========================================================================
# CONSTANTS
# ===========================================================================

SAMPLE_SIZE_DEFAULT = 75
ABLATION_SEED = 42
ABLATION_DB = Path(result_ablation_path) / "ablation_results.db"
ABLATION_SUMMARY_JSON = Path(result_ablation_path) / "ablation_summary.json"
ABLATION_CHECKPOINT_FILENAME = "ablation_checkpoint.json"


# ===========================================================================
# CHECKPOINT HELPERS (crash-safe resume)
# ===========================================================================
# Tracks completed (config_name, patient_id) pairs. If the run crashes at
# config 5 of 7, resume skips configs 1-4 entirely and picks up mid-config-5.
# Uses atomic temp+replace writes (same pattern as File 25 Batch Runner).

def _ablation_checkpoint_path() -> Path:
    return Path(checkpoint_path) / ABLATION_CHECKPOINT_FILENAME


def load_ablation_checkpoint() -> set:
    """Load set of completed (config_name, patient_id) tuples."""
    cp = _ablation_checkpoint_path()
    if not cp.exists():
        return set()
    try:
        with open(cp, "r") as f:
            data = json.load(f)
        completed = set(tuple(pair) for pair in data.get("completed", []))
        print(f"[Checkpoint] Resuming: {len(completed)} patient-config pairs already completed.")
        return completed
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[Checkpoint] WARNING: Could not read checkpoint ({e}). Starting fresh.")
        return set()


def save_ablation_checkpoint(completed: set) -> None:
    """Atomically persist completed set to checkpoint file."""
    with _ablation_checkpoint_lock:
        cp = _ablation_checkpoint_path()
        tmp_path = cp.with_suffix(".tmp")
        try:
            with open(tmp_path, "w") as f:
                json.dump(
                    {
                        "completed": list(completed),
                        "last_updated": datetime.now().isoformat(),
                        "count": len(completed),
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
                

def clear_ablation_checkpoint() -> None:
    """Delete checkpoint file to start a fresh run."""
    cp = _ablation_checkpoint_path()
    if cp.exists():
        cp.unlink()
        print("[Checkpoint] Cleared.")


# ===========================================================================
# ABLATION CONFIGURATIONS
# ===========================================================================
# Each dict is passed into the LangGraph initial state as 'ablation_flags'.
# File 13 nodes read flags via state.get("ablation_flags") or {}.
# Default {} = all stages active (production behavior).

ABLATION_CONFIGS = [
    {
        "name": "full_pipeline",
        "description": "All stages active (baseline)",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "no_mesh_filter",
        # skip_mesh_filter removes BOTH MeSH uses: the Stage 3 relevance boost
        # and the Stage 4 hard drop. Disabling only the drop left this row
        # confounded, because the boost still reordered the pool.
        "description": "MeSH cancer site filter disabled (Stage 3 boost + Stage 4 drop)",
        "flags": {
            "skip_mesh_filter": True,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "no_stage_filter",
        "description": "Cancer stage filter disabled",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": True,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "no_histology_filter",
        "description": "Histology mismatch filter disabled",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": True,
            "skip_cross_encoder": False,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "no_cross_encoder",
        "description": "Cross-encoder reranking disabled (fusion score passthrough)",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": True,
            "retrieval_mode": "hybrid",
        },
    },
    {
        "name": "bm25_only",
        "description": "BM25 retrieval only (vector search disabled)",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "bm25_only",
        },
    },
    {
        "name": "vector_only",
        "description": "Vector retrieval only (BM25 disabled)",
        "flags": {
            "skip_mesh_filter": False,
            "skip_stage_filter": False,
            "skip_histology_filter": False,
            "skip_cross_encoder": False,
            "retrieval_mode": "vector_only",
        },
    },
]

# Lookup for --configs filter validation
_VALID_CONFIG_NAMES = {c["name"] for c in ABLATION_CONFIGS}


# ===========================================================================
# STRATIFIED SAMPLING
# ===========================================================================

def _cancer_group_key(display: str) -> str:
    """Map a cancer display name to a broad anatomical group for sampling."""
    display_lower = display.lower()

    groups = [
        ("lung",        ["lung", "pulmonary", "bronch", "nsclc", "sclc"]),
        ("breast",      ["breast"]),
        ("colorectal",  ["colon", "rectal", "rectum", "colorectal"]),
        ("prostate",    ["prostate"]),
        ("pancreatic",  ["pancrea"]),
        ("ovarian",     ["ovary", "ovarian"]),
        ("uterine",     ["uterus", "uterine", "cervix", "cervical"]),
        ("hematologic", ["leukemia", "leukaemia", "lymphoma", "myeloma"]),
        ("melanoma",    ["melanoma"]),
        ("liver",       ["liver", "hepato", "hepatic"]),
        ("kidney",      ["kidney", "renal"]),
        ("bladder",     ["bladder"]),
        ("thyroid",     ["thyroid"]),
        ("brain",       ["brain", "glioma", "glioblastoma"]),
        ("head_neck",   ["oropharyn", "oral cavity", "head and neck"]),
    ]

    for group_name, keywords in groups:
        if any(kw in display_lower for kw in keywords):
            return group_name

    return "other"


def _get_patient_group(patient, registry):
    """Get the cancer group key for a single patient."""
    conditions = patient.get("conditions", [])
    cancer_conditions = [c for c in conditions if registry.is_primary_cancer(c)]
    if cancer_conditions:
        primary = sorted(cancer_conditions, key=registry.sort_key)[0]
        return _cancer_group_key(primary.get("display", "Unknown"))
    return "unknown"


def stratified_sample(patients, sample_size, seed):
    """
    Select a stratified sample covering diverse cancer types.

    Groups patients by primary cancer (via CancerCodeRegistry 3-layer
    detection + tiebreaker sort), then samples proportionally. At least
    1 patient per group. Sorted by patient_id for deterministic ordering.

    Args:
        patients:    Parsed FHIR patient dicts from load_all_patients()
        sample_size: Target count
        seed:        Random seed

    Returns:
        List of patient dicts, length = min(sample_size, len(patients))
    """
    if len(patients) <= sample_size:
        print(f"  Population ({len(patients)}) <= sample ({sample_size}). Using all.")
        return sorted(patients, key=lambda p: p["patient_id"])

    random.seed(seed)
    registry = _CANCER_REGISTRY

    # Group by cancer type
    cancer_groups = defaultdict(list)
    for patient in patients:
        cancer_groups[_get_patient_group(patient, registry)].append(patient)

    # Proportional sampling, minimum 1 per group
    total = len(patients)
    sampled = []

    for group_name in sorted(cancer_groups):
        group = cancer_groups[group_name]
        share = max(1, round(len(group) / total * sample_size))
        share = min(share, len(group))
        sampled.extend(random.sample(group, share))

    # Trim if rounding + min-1 caused oversampling
    if len(sampled) > sample_size:
        random.seed(seed)
        random.shuffle(sampled)
        sampled = sampled[:sample_size]

    # Deterministic processing order
    sampled.sort(key=lambda p: p["patient_id"])

    # Report
    print(f"\nStratified sample: {len(sampled)} patients, "
          f"{len(cancer_groups)} cancer groups")
    
    sampled_ids = {p["patient_id"] for p in sampled}
    for gname in sorted(cancer_groups):
        n_sample = sum(1 for p in cancer_groups[gname] if p["patient_id"] in sampled_ids)
        n_pop = len(cancer_groups[gname])
        print(f"  {gname:15s}: {n_sample:3d} sampled / {n_pop:4d} total")

    return sampled


# ===========================================================================
# DATABASE
# ===========================================================================

def init_ablation_db():
    """Create ablation database tables (idempotent)."""
    conn = sqlite3.connect(str(ABLATION_DB))
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS ablation_runs (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            run_timestamp       TEXT NOT NULL,
            config_name         TEXT NOT NULL,
            config_description  TEXT,
            sample_size         INTEGER,
            total_time_seconds  REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS ablation_results (
            id                              INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id                          INTEGER NOT NULL,
            config_name                     TEXT NOT NULL,
            patient_id                      TEXT NOT NULL,
            cancer_group                    TEXT,
            primary_condition               TEXT,
            bm25_retrieved                  INTEGER DEFAULT 0,
            vector_retrieved                INTEGER DEFAULT 0,
            candidates_retrieved            INTEGER DEFAULT 0,
            candidates_reranked             INTEGER DEFAULT 0,
            candidates_after_rule_filter    INTEGER DEFAULT 0,
            candidates_after_quality_filter INTEGER DEFAULT 0,
            candidates_evaluated            INTEGER DEFAULT 0,
            eligible_count                  INTEGER DEFAULT 0,
            not_eligible_count              INTEGER DEFAULT 0,
            not_evaluable_count             INTEGER DEFAULT 0,
            avg_match_score                 REAL,
            eligible_nct_ids                TEXT DEFAULT '',
            near_miss_nct_ids               TEXT DEFAULT '',
            mesh_dropped                    INTEGER DEFAULT 0,
            stage_dropped                   INTEGER DEFAULT 0,
            histology_dropped               INTEGER DEFAULT 0,
            query_expansion_time            REAL DEFAULT 0,
            hybrid_retrieval_time           REAL DEFAULT 0,
            cross_encoder_time              REAL DEFAULT 0,
            rule_filter_time                REAL DEFAULT 0,
            gpt4o_evaluation_time           REAL DEFAULT 0,
            total_time                      REAL DEFAULT 0,
            gpt4o_input_tokens              INTEGER DEFAULT 0,
            gpt4o_output_tokens             INTEGER DEFAULT 0,
            estimated_cost_usd              REAL DEFAULT 0,
            error                           TEXT DEFAULT '',
            FOREIGN KEY (run_id) REFERENCES ablation_runs(id)
        )
    """)

    # Columns added after the table was first created. CREATE TABLE IF NOT
    # EXISTS is a no-op on an existing ablation_results.db, so the INSERT below
    # would fail against a database built before the column was introduced.
    _existing = {row[1] for row in c.execute("PRAGMA table_info(ablation_results)")}
    for _column, _sql_type in {"not_evaluable_count": "INTEGER DEFAULT 0"}.items():
        if _column not in _existing:
            c.execute(f"ALTER TABLE ablation_results ADD COLUMN {_column} {_sql_type}")
            print(f"Schema migration: added ablation_results.{_column}")

    conn.commit()
    conn.close()
    print(f"Ablation database: {ABLATION_DB}")


def _create_run(config_name, config_description, sample_size):
    """Insert a new ablation_runs row, return run_id."""
    with _ablation_db_lock:
        conn = sqlite3.connect(str(ABLATION_DB))
        c = conn.cursor()
        c.execute(
            "INSERT INTO ablation_runs "
            "(run_timestamp, config_name, config_description, sample_size) "
            "VALUES (?, ?, ?, ?)",
            (datetime.now().isoformat(), config_name, config_description, sample_size),
        )
        run_id = c.lastrowid
        conn.commit()
        conn.close()
        return run_id
    

def _finalize_run(run_id, elapsed_seconds):
    """Update run with total elapsed time."""
    with _ablation_db_lock:
        conn = sqlite3.connect(str(ABLATION_DB))
        conn.execute(
            "UPDATE ablation_runs SET total_time_seconds = ? WHERE id = ?",
            (round(elapsed_seconds, 2), run_id),
        )
        conn.commit()
        conn.close()
        

def log_ablation_result(run_id, config_name, patient_data, result, ablation_flags):
    """
    Log one patient's ablation result.

    Uses get_model_cost() from File 02/03 for cost consistency with
    File 14's production logging. Derives bm25_retrieved/vector_retrieved
    from ablation_flags retrieval_mode. Non-critical: errors are printed
    but do not crash the study.
    """
    
    conn = None
    
    with _ablation_db_lock:
        
        try:
            # Counts. Tracked separately so the three buckets still sum to
            # candidates_evaluated: a trial that could not be evaluated is
            # neither a match nor a rejection.
            matches = result.get("matches", [])
            near_misses = result.get("near_misses", [])
            not_evaluable = result.get("not_evaluable", [])

            # Average match score (eligible only; None if no matches)
            avg_score = None
            if matches:
                avg_score = round(
                    sum(m.get("match_score", 0) for m in matches) / len(matches), 4
                )
    
            # Cost via same pricing function as File 14
            input_tok = result.get("gpt4o_input_tokens", 0)
            output_tok = result.get("gpt4o_output_tokens", 0)
            cost = get_model_cost(MATCHING_MODEL, input_tok, output_tok)
    
            # Timings
            timings = result.get("stage_timings", {})
    
            # Cancer group
            cancer_group = _get_patient_group(patient_data, _CANCER_REGISTRY)
            
            # BM25 / vector retrieval counts (derived from retrieval_mode)
            _mode = ablation_flags.get("retrieval_mode", "hybrid")
            bm25_retrieved = 0 if _mode == "vector_only" else BM25_RETRIEVAL_SIZE
            vector_retrieved = 0 if _mode == "bm25_only" else VECTOR_RETRIEVAL_SIZE
    
            # Eligible / near-miss NCT IDs for trial-level overlap analysis
            eligible_nct_ids = ",".join(
                m.get("nct_id", "") for m in matches if m.get("nct_id")
            )
            near_miss_nct_ids = ",".join(
                m.get("nct_id", "") for m in near_misses if m.get("nct_id")
            )
    
            conn = sqlite3.connect(str(ABLATION_DB))
            conn.execute("""
                INSERT INTO ablation_results (
                    run_id, config_name, patient_id, cancer_group, primary_condition,
                    bm25_retrieved, vector_retrieved,
                    candidates_retrieved, candidates_reranked,
                    candidates_after_rule_filter, candidates_after_quality_filter,
                    candidates_evaluated,
                    eligible_count, not_eligible_count, not_evaluable_count, avg_match_score,
                    eligible_nct_ids, near_miss_nct_ids,
                    mesh_dropped, stage_dropped, histology_dropped,
                    query_expansion_time, hybrid_retrieval_time, cross_encoder_time,
                    rule_filter_time, gpt4o_evaluation_time, total_time,
                    gpt4o_input_tokens, gpt4o_output_tokens,
                    estimated_cost_usd, error
                ) VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
            """, (
                run_id,
                config_name,
                patient_data["patient_id"],
                cancer_group,
                result.get("primary_condition", ""),
                bm25_retrieved,
                vector_retrieved,
                result.get("candidates_retrieved", 0),
                result.get("candidates_reranked", 0),
                result.get("candidates_after_rule_filter", 0),
                result.get("candidates_after_quality_filter", 0),
                result.get("candidates_evaluated", 0),
                len(matches),
                len(near_misses),
                len(not_evaluable),
                avg_score,
                eligible_nct_ids,
                near_miss_nct_ids,
                result.get("mesh_dropped", 0),
                result.get("stage_dropped", 0),
                result.get("histology_dropped", 0),
                round(timings.get("query_expansion", 0), 3),
                round(timings.get("hybrid_retrieval", 0), 3),
                round(timings.get("cross_encoder", 0), 3),
                round(timings.get("rule_filter", 0), 3),
                round(timings.get("gpt4o_evaluation", 0), 3),
                round(sum(timings.values()), 3),
                input_tok,
                output_tok,
                round(cost, 6),
                result.get("error", ""),
            ))
            conn.commit()
    
        except Exception as e:
            print(f"  WARNING: Failed to log result: {e}")
    
        finally:
            if conn is not None:
                conn.close()


# ===========================================================================
# PIPELINE INVOCATION WITH ABLATION FLAGS
# ===========================================================================

def match_patient_ablation(patient_data, bm25_index, nct_ids, graph, ablation_flags):
    """
    Run the matching pipeline with ablation flags in the LangGraph state.

    Identical to match_patient_to_trials() except:
      - Injects 'ablation_flags' into initial state
      - Does NOT call log_inference() (no writes to production inferences.db)

    Args:
        patient_data:   Parsed FHIR patient dict
        bm25_index:     Pre-built BM25Okapi index
        nct_ids:        NCT IDs aligned with BM25 index
        graph:          Compiled LangGraph StateGraph
        ablation_flags: Dict with keys: skip_mesh_filter, skip_stage_filter,
                        skip_histology_filter, skip_cross_encoder (all bool),
                        retrieval_mode ("hybrid"|"bm25_only"|"vector_only")

    Returns:
        Result dict with: patient_id, matches, near_misses, stage_timings,
        candidates_*, gpt4o_*_tokens, mesh/stage/histology_dropped, error
    """
    initial_state = {
        "patient_data":                     patient_data,
        "bm25_index":                       bm25_index,
        "nct_ids":                          nct_ids,
        "expanded_query":                   "",
        "hybrid_results":                   [],
        "reranked_trials":                  [],
        "filtered_trials":                  [],
        "candidates_after_rule_filter":     0,
        "candidates_after_quality_filter":  0,
        "evaluations":                      [],
        "gpt4o_retries":                    0,
        "gpt4o_raw_response":               "",
        "result":                           {},
        "error":                            "",
        "stage_timings":                    {},
        "patient_trees":                    set(),
        "patient_histology":                set(),
        "mesh_resolution":                  "",
        "ablation_flags":                   ablation_flags,
    }

    final_state = graph.invoke(initial_state)
    result = final_state["result"]
    result["qdrant_collection"] = resolve_qdrant_collection()
    result["patient_data_hash"] = compute_patient_hash(patient_data)
    return result


# ===========================================================================
# SUMMARY REPORTING
# ===========================================================================

def generate_summary():
    """
    Query ablation database and produce summary table + deltas + JSON export.

    Uses the most recent run per config_name, so re-running a single config
    updates its row without affecting others. Returns DataFrame or None.
    """
    if not ABLATION_DB.exists():
        print("No ablation database found.")
        return None

    conn = sqlite3.connect(str(ABLATION_DB))
    try:
        df = pd.read_sql_query("""
            SELECT
                r.config_name,
                COUNT(*)                                            AS n,
                ROUND(AVG(r.candidates_retrieved), 1)               AS avg_retrieved,
                ROUND(AVG(r.candidates_reranked), 1)                AS avg_reranked,
                ROUND(AVG(r.candidates_after_rule_filter), 1)       AS avg_after_rules,
                ROUND(AVG(r.candidates_evaluated), 1)               AS avg_evaluated,
                ROUND(AVG(r.eligible_count), 2)                     AS avg_eligible,
                ROUND(AVG(r.not_eligible_count), 2)                 AS avg_not_eligible,
                ROUND(AVG(r.avg_match_score), 3)                    AS avg_score,
                ROUND(AVG(r.mesh_dropped), 1)                       AS avg_mesh_drop,
                ROUND(AVG(r.stage_dropped), 1)                      AS avg_stage_drop,
                ROUND(AVG(r.histology_dropped), 1)                  AS avg_hist_drop,
                ROUND(AVG(r.total_time), 2)                         AS avg_time_s,
                ROUND(AVG(r.estimated_cost_usd), 4)                 AS avg_cost,
                ROUND(SUM(r.estimated_cost_usd), 4)                 AS total_cost,
                SUM(CASE WHEN r.error != '' THEN 1 ELSE 0 END)      AS errors
            FROM ablation_results r
            INNER JOIN (
                SELECT config_name, id AS max_run_id
                FROM ablation_runs
                WHERE (config_name, run_timestamp) IN (
                    SELECT config_name, MAX(run_timestamp)
                    FROM ablation_runs
                    GROUP BY config_name
                )
            ) latest ON r.config_name = latest.config_name
                     AND r.run_id    = latest.max_run_id
            GROUP BY r.config_name
        """, conn)
    finally:
        conn.close()

    if df.empty:
        print("No ablation results found.")
        return None

    # Reorder to match ABLATION_CONFIGS
    order = {c["name"]: i for i, c in enumerate(ABLATION_CONFIGS)}
    df["_sort"] = df["config_name"].map(order).fillna(999)
    df = df.sort_values("_sort").drop(columns=["_sort"]).reset_index(drop=True)

    # --- Print compact table ---
    print("\n" + "=" * 130)
    print("  ABLATION STUDY RESULTS")
    print("=" * 130 + "\n")
    print(df.to_string(index=False))

    # --- Deltas vs baseline ---
    bl_rows = df[df["config_name"] == "full_pipeline"]
    if not bl_rows.empty:
        bl = bl_rows.iloc[0]

        print("\n" + "-" * 130)
        print("  DELTAS vs FULL PIPELINE (baseline)")
        print("-" * 130)
        print(f"  {'Config':25s} | {'Δevaluated':>11s} | {'Δeligible':>10s} | "
              f"{'Δscore':>8s} | {'Δcost/pt':>10s} | {'Δtime/pt':>9s} | "
              f"{'Δmesh_drop':>11s} | {'Δstage_drop':>12s}")
        print("  " + "-" * 107)

        for _, row in df.iterrows():
            if row["config_name"] == "full_pipeline":
                continue
            print(
                f"  {row['config_name']:25s} | "
                f"{row['avg_evaluated']  - bl['avg_evaluated']:+11.1f} | "
                f"{row['avg_eligible']   - bl['avg_eligible']:+10.2f} | "
                f"{row['avg_score']      - bl['avg_score']:+8.3f} | "
                f"${row['avg_cost']      - bl['avg_cost']:+9.4f} | "
                f"{row['avg_time_s']     - bl['avg_time_s']:+9.2f} | "
                f"{row['avg_mesh_drop']  - bl['avg_mesh_drop']:+11.1f} | "
                f"{row['avg_stage_drop'] - bl['avg_stage_drop']:+12.1f}"
            )

    # --- JSON export ---
    summary_records = df.to_dict(orient="records")
    with open(ABLATION_SUMMARY_JSON, "w") as f:
        json.dump(summary_records, f, indent=2)
    print(f"\n  Summary exported: {ABLATION_SUMMARY_JSON}")

    return df


# ===========================================================================
# MAIN
# ===========================================================================

def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="OncoMatch Ablation Study")
    parser.add_argument(
        "--sample-size", type=int, default=SAMPLE_SIZE_DEFAULT,
        help=f"Number of patients to sample (default: {SAMPLE_SIZE_DEFAULT})"
    )
    parser.add_argument(
        "--summary-only", action="store_true",
        help="Skip pipeline runs; print summary from existing database"
    )
    parser.add_argument(
        "--configs", nargs="+", default=None,
        help="Run only specific configs (e.g., --configs full_pipeline no_mesh_filter)"
    )
    return parser.parse_args()


def main():
    """Run the ablation study."""

    args = parse_args()

    print()
    print("=" * 70)
    print(f"{Project_Name}: ABLATION STUDY")
    print("=" * 70)
    print()

    # --- Summary-only mode ---
    if args.summary_only:
        generate_summary()
        return

    # --- Validate --configs if provided ---
    if args.configs:
        invalid = set(args.configs) - _VALID_CONFIG_NAMES
        if invalid:
            print(f"ERROR: Unknown config(s): {invalid}")
            print(f"Valid configs: {sorted(_VALID_CONFIG_NAMES)}")
            sys.exit(1)
        configs = [c for c in ABLATION_CONFIGS if c["name"] in args.configs]
    else:
        configs = ABLATION_CONFIGS

    with CaffeinateSession("Ablation Study"):

        # --- Step 1: Initialize ---
        init_ablation_db()

        print("\n[Step 1] Building BM25 index...")
        bm25_index, nct_ids = build_bm25_index_from_qdrant()
        print(f"  {len(nct_ids)} trials indexed")

        if not nct_ids:
            print("ERROR: No trials in Qdrant. Run File 11 first.")
            sys.exit(1)

        print("[Step 1] Compiling LangGraph pipeline...")
        graph = build_matching_graph()

        # --- Step 2: Load and sample patients ---
        print(f"\n[Step 2] Loading patients from {data_fhir_path}...")
        all_patients = load_all_patients(data_fhir_path)
        print(f"  {len(all_patients)} patients loaded")

        if not all_patients:
            print("ERROR: No patients found. Run Files 04-07 first.")
            sys.exit(1)

        sample = stratified_sample(all_patients, args.sample_size, ABLATION_SEED)

        # --- Step 3: Resume support ---
        completed = load_ablation_checkpoint()

        # --- Step 4: Run each config ---
        total_configs = len(configs)
        total_runs = total_configs * len(sample)
        already_done = len(completed)
        remaining = total_runs - already_done
        study_start = time.time()

        print(f"\n  Total runs:     {total_runs} ({total_configs} configs × {len(sample)} patients)")
        print(f"  Already done:   {already_done}")
        print(f"  Remaining:      {remaining}")
        print()

        # --- tqdm progress bar ---
        print("*" * 70)
        progress = tqdm(
            total=total_runs,
            initial=already_done,
            desc="🔬 ABLATION PROGRESS",
            unit="run",
            bar_format="{desc}: {percentage:3.0f}%|{bar:40}| {n_fmt}/{total_fmt} "
                       "[Elapsed: {elapsed} | ETA: {remaining} | {rate_fmt}] {postfix}",
            ncols=120,
            smoothing=0.1,
        )

        run_success = 0
        run_error = 0
        interrupted = False

        # Redirect print through tqdm.write to keep progress bar clean
        _original_print = builtins.print

        def _tqdm_print(*args, **kwargs):
            text = " ".join(str(a) for a in args)
            tqdm.write(text)

        builtins.print = _tqdm_print

        def _process_one(patient_data, config_name, ablation_flags, run_id):
            """Run pipeline + log for one patient-config pair. Never raises."""
            pid = patient_data["patient_id"]
            try:
                result = match_patient_ablation(
                    patient_data, bm25_index, nct_ids, graph, ablation_flags
                )
            except Exception as e:
                traceback.print_exc()
                result = {
                    "error": str(e),
                    "matches": [],
                    "near_misses": [],
                    "not_evaluable": [],
                    "stage_timings": {},
                    "primary_condition": "",
                    "candidates_retrieved": 0,
                    "candidates_reranked": 0,
                    "candidates_after_rule_filter": 0,
                    "candidates_after_quality_filter": 0,
                    "candidates_evaluated": 0,
                    "mesh_dropped": 0,
                    "stage_dropped": 0,
                    "histology_dropped": 0,
                    "gpt4o_input_tokens": 0,
                    "gpt4o_output_tokens": 0,
                }

            log_ablation_result(run_id, config_name, patient_data, result, ablation_flags)
            return pid, result

        try:
            for config_idx, config in enumerate(configs, 1):
                config_name = config["name"]
                ablation_flags = config["flags"]

                # Skip entirely completed configs
                config_pairs = {(config_name, p["patient_id"]) for p in sample}
                
                if config_pairs.issubset(completed):
                    print(f"\n  [SKIP] Config '{config_name}' already completed.")
                    progress.update(len(config_pairs))
                    continue

                print(f"\n{'#' * 70}")
                print(f"# CONFIG {config_idx}/{total_configs}: {config_name}")
                print(f"# {config['description']}")
                print(f"# Flags: {ablation_flags}")
                print(f"{'#' * 70}")

                run_id = _create_run(config_name, config["description"], len(sample))
                config_start = time.time()

                # Filter to pending patients for this config
                pending_patients = [
                    p for p in sample
                    if (config_name, p["patient_id"]) not in completed
                ]
                # Update progress for already-completed patients in this config
                already_done_in_config = len(sample) - len(pending_patients)
                if already_done_in_config > 0:
                    progress.update(already_done_in_config)

                def _on_done(future, _config_name=config_name):
                    nonlocal run_success, run_error
                    try:
                        pid, result = future.result()
                    except Exception as e:
                        run_error += 1
                        progress.set_postfix(ok=run_success, err=run_error)
                        progress.update(1)
                        tqdm.write(f"  [CALLBACK ERROR] {_config_name}: {type(e).__name__}: {e}")
                        return

                    if result.get("error"):
                        run_error += 1
                    else:
                        run_success += 1

                    completed.add((_config_name, pid))
                    save_ablation_checkpoint(completed)

                    progress.set_postfix(ok=run_success, err=run_error)
                    progress.update(1)

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    futures = []
                    for patient_data in pending_patients:
                        future = executor.submit(
                            _process_one,
                            patient_data=patient_data,
                            config_name=config_name,
                            ablation_flags=ablation_flags,
                            run_id=run_id,
                        )
                        future.add_done_callback(_on_done)
                        futures.append(future)

                    # Wait for all to complete (callbacks handle progress)
                    for future in futures:
                        future.result()

                config_elapsed = time.time() - config_start
                _finalize_run(run_id, config_elapsed)
                print(f"\n  Config '{config_name}' done: {config_elapsed / 60:.1f} min")

        except KeyboardInterrupt:
            interrupted = True
            print("\n[INTERRUPTED] Waiting for active threads to finish...")
            # ThreadPoolExecutor's with-block handles shutdown

        finally:
            progress.close()
            builtins.print = _original_print

        # --- Step 5: Summary ---
        study_elapsed = time.time() - study_start

        print()
        print("=" * 70)
        print(f"{Project_Name}: ABLATION STUDY SUMMARY")
        print("=" * 70)
        print(f"  Wall time:       {study_elapsed / 60:.1f} min")
        print(f"  Completed:       {run_success + run_error}")
        print(f"  Success:         {run_success}")
        print(f"  Errors:          {run_error}")
        print(f"  Database:        {ABLATION_DB}")

        if interrupted:
            print(f"  Status:          INTERRUPTED (resume with same command)")
        else:
            generate_summary()
            clear_ablation_checkpoint()
            print(f"  Summary:         {ABLATION_SUMMARY_JSON}")
            print(f"  Status:          COMPLETE")

        print("=" * 70)
        print()


#------------------------------------------------------------------------------


if __name__ == "__main__":
    main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 02 2026

@author: ramyalsaffar
"""



#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Mar  3 10:18:17 2026

@author: ramyalsaffar
"""

