# Database Schema and Logger
############################


#------------------------------------------------------------------------------


# Connect
# It will create it if deos not exist, and it won't override if it does.
conn = sqlite3.connect(inferences_path)


# Create cursor
cursor = conn.cursor()


#------------------------------------------------------------------------------


# Inferences table
# candidates_filtered INTEGER is for trials sent to GPT-4o (after quality threshold + cost cap)
cursor.execute('''
CREATE TABLE IF NOT EXISTS inferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    age INTEGER,
    sex TEXT,
    race TEXT, 
    ethnicity TEXT,
    primary_condition TEXT,
    condition_count INTEGER,
    medication_count INTEGER,
    allergy_count INTEGER,
    expanded_query TEXT,
    candidates_retrieved INTEGER,
    candidates_reranked INTEGER,
    bm25_retrieved INTEGER,
    vector_retrieved INTEGER,
    candidates_after_rule_filter INTEGER,
    candidates_after_quality_filter INTEGER,
    candidates_filtered INTEGER,
    mesh_dropped INTEGER,
    mesh_resolution TEXT,
    stage_dropped INTEGER,
    histology_dropped INTEGER,
    candidates_evaluated INTEGER,
    eligible_matches INTEGER,
    near_misses INTEGER,
    not_evaluable_trials INTEGER,
    cross_vocab_remaps INTEGER,
    query_expansion_time REAL,
    hybrid_retrieval_time REAL,
    cross_encoder_time REAL,
    rule_filter_time REAL,
    gpt4o_evaluation_time REAL,
    total_time REAL,
    gpt4o_prompt TEXT,
    gpt4o_input_tokens INTEGER,
    gpt4o_output_tokens INTEGER,
    matching_model TEXT,
    cross_encoder_model TEXT,
    pricing_version TEXT,
    estimated_cost_usd REAL,
    qdrant_collection TEXT,
    error TEXT,
    patient_data_hash TEXT,
    expansion_prompt TEXT,
    gpt4o_retries INTEGER,
    ablation_flags TEXT,
    hallucinated_trials INTEGER
)
''')


#------------------------------------------------------------------------------


# Schema migration for the inferences table.
#
# CREATE TABLE IF NOT EXISTS is a no-op once the table exists, so columns added
# after the first run must be applied explicitly. Rows written before a column
# existed keep NULL, which is the honest value: the counter was not recorded,
# as opposed to having been recorded as zero.
INFERENCE_COLUMN_ADDITIONS = {
    "not_evaluable_trials": "INTEGER",   # trials the model could not assess at all
    "cross_vocab_remaps":   "INTEGER",   # criterion labels resolved to not_evaluable
    # Which layer resolved the patient's MeSH C04 identity ("snomed",
    # "icd10+fuzzy_synonym", ...), or why none did ("pan_cancer_only",
    # "unmapped", "no_cancer_condition", "no_valid_condition",
    # "no_mesh_filter"). mesh_dropped = 0 is ambiguous on its own: it means
    # both "the filter found nothing to drop" and "the patient was never
    # resolved, so the filter never ran". This column separates the two.
    "mesh_resolution":      "TEXT",
    # Count of trials GPT-4o returned an evaluation for that were never in the
    # candidate set sent to it. The detector (item 33) writes
    # result["hallucinated_trials"]; until it does, no terminal node emits the
    # key and the column stays NULL on every row. NULL is the correct value:
    # inserting 0 would assert that the check ran and found nothing, which is
    # the exact confusion this project treats as a defect. The column exists
    # now so the detector has somewhere to write without a second migration.
    "hallucinated_trials":  "INTEGER",
    # --- Retrieval and expansion degradation (item 11b) ---------------------
    # Stage 2 runs four retrieval channels behind one try/except each. Before
    # these columns existed, a channel that raised was printed and dropped, and
    # fusion continued on the survivors: a dense-search outage produced the
    # same stored row as a clean run. bm25_retrieved / vector_retrieved cannot
    # substitute — 0 means both "returned nothing" and "never returned".
    #
    # retrieval_channels holds the per-channel record as JSON:
    #   {"title": {"status": "ok", "count": 75, "error": ""},
    #    "dense": {"status": "failed", "count": 0, "error": "..."}}
    # status is one of File 13's CHANNEL_* constants: ok | failed | ablated |
    # empty_query. The scalars beside it are the same fact in queryable form,
    # with ablated channels excluded from "expected" so a bm25_only ablation is
    # not reported as a degraded run.
    #
    # NULL on every one of them means Stage 2 did not report, which is not the
    # same as a clean run — see _pipeline_provenance() in File 13.
    "retrieval_channels":           "TEXT",
    "retrieval_channels_expected":  "INTEGER",
    "retrieval_channels_ok":        "INTEGER",
    "retrieval_degraded":           "INTEGER",  # 1 = an expected channel did not return
    # Trials ranked into the fusion pool whose payload could not be recovered,
    # so they never reached Stage 3. The batch-scroll fallback that loses them
    # used to print a line and keep going.
    "retrieval_trials_lost":        "INTEGER",
    # Which query Stage 1 searched with: "mesh_expanded" or
    # "base_query_fallback". The fallback printed a WARNING and nothing else,
    # so the rate at which the pipeline ran without any MeSH expansion was not
    # recoverable from the database. Distinct from mesh_resolution, which says
    # why resolution failed rather than what the run then did.
    "query_expansion_path":         "TEXT",
    # Whether Stage 4's cancer site filter actually ran (1/0), and why not.
    # Stage 5's system prompt asserts to the model that disease relevance was
    # confirmed; that assertion is now conditional on this flag, so the flag
    # belongs in the record of the inference it shaped.
    "mesh_filter_applied":          "INTEGER",
    "mesh_filter_skip_reason":      "TEXT",
    # --- Age provenance (item 12) -------------------------------------------
    # The date this run computed patient ages against (DATA_SNAPSHOT_DATE,
    # File 03), and how much of the patient's birthDate the record carried.
    #
    # age was previously derived from datetime.now(), so the stored age — and
    # the Stage 5 prompt built from it — moved with the clock while
    # patient_data_hash, which keys on birth_date, stayed identical. Rows
    # written before this column existed keep NULL, which is honest: their
    # reference date was whatever day they happened to run and is not
    # recoverable from the row.
    #
    # birth_date_precision is "day" for an exact age; "month"/"year" mean the
    # age was imputed from a mid-range anchor (File 02) because the record was
    # partial, which HIPAA Safe Harbor de-identification produces by design;
    # "missing"/"unparseable"/"after_reference" mean age is NULL and say why.
    # NULL here means the parser did not report — not that the date was exact.
    "age_reference_date":           "TEXT",
    "birth_date_precision":         "TEXT",
}

_existing_inference_columns = {
    row[1] for row in cursor.execute("PRAGMA table_info(inferences)")
}
for _column, _sql_type in INFERENCE_COLUMN_ADDITIONS.items():
    if _column not in _existing_inference_columns:
        cursor.execute(f"ALTER TABLE inferences ADD COLUMN {_column} {_sql_type}")
        print(f"Schema migration: added inferences.{_column}")


#------------------------------------------------------------------------------


# Trial matches table
cursor.execute('''
CREATE TABLE IF NOT EXISTS trial_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inference_id INTEGER NOT NULL,
    nct_id TEXT NOT NULL,
    trial_title TEXT,
    trial_phase TEXT,
    trial_number INTEGER,
    rerank_score REAL,
    rerank_score_raw REAL,
    mesh_boost REAL,
    mesh_boost_tier TEXT,
    match_score REAL,
    eligible TEXT,
    explanation TEXT,
    criterion_details TEXT,
    hallucinated INTEGER,
    FOREIGN KEY (inference_id) REFERENCES inferences(id)
)
''')


# Schema migration for the trial_matches table (same reasoning as above).
#
# rerank_score stays the BOOSTED ranking score, so historical rows keep their
# meaning. The unboosted score and the MeSH boost are recorded separately so
# the boost's effect on ranking can be measured rather than inferred.
#
# match_score is confirmed/denominator over APPLICABLE criteria only (File 13
# excludes criteria the model marked "Not applicable -- ..." from both). Storing
# the three inputs makes the ratio auditable: a 0.0 score on a denominator of 8
# (nothing confirmable) is a different finding from 0.0 on a denominator of 0
# (no criterion applied to this patient), and neither is visible from the
# rounded score alone.
TRIAL_MATCH_COLUMN_ADDITIONS = {
    "rerank_score_raw": "REAL",   # fused rerank score before the MeSH boost
    "mesh_boost":       "REAL",   # additive boost, 0.0 when no tier matched
    "mesh_boost_tier":  "TEXT",   # "direct" | "pan_cancer" | "none"
    "score_confirmed":         "INTEGER",  # match_score numerator
    "score_denominator":       "INTEGER",  # match_score denominator (applicable only)
    "criteria_not_applicable": "INTEGER",  # criteria excluded from both
    # Per-trial marker for the same detection as inferences.hallucinated_trials:
    # 1 = this NCT ID was not in the candidate set sent to the model, 0 = it was,
    # NULL = the check did not run for this row. Written from match["hallucinated"].
    "hallucinated":            "INTEGER",
}

_existing_trial_match_columns = {
    row[1] for row in cursor.execute("PRAGMA table_info(trial_matches)")
}
for _column, _sql_type in TRIAL_MATCH_COLUMN_ADDITIONS.items():
    if _column not in _existing_trial_match_columns:
        cursor.execute(f"ALTER TABLE trial_matches ADD COLUMN {_column} {_sql_type}")
        print(f"Schema migration: added trial_matches.{_column}")


#------------------------------------------------------------------------------


# Drift metrics table
cursor.execute('''
CREATE TABLE IF NOT EXISTS drift_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    metric_category TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL,
    baseline_mean REAL,
    baseline_std REAL,
    p_value REAL,
    z_score REAL,
    threshold REAL,
    alert INTEGER,
    baseline_window_days INTEGER,
    comparison_window_days INTEGER,
    notes TEXT
)
''')


#------------------------------------------------------------------------------


conn.commit()
conn.close()
print(f"Database initialized at: {inferences_path}")


#------------------------------------------------------------------------------


def _resolve_primary_cancer(conditions: List[Dict]) -> Optional[str]:
    """
    Identify the primary cancer condition from a patient's condition list.

    Mirrors the exact logic used by node_query_expansion (13- LangGraph Agent.py,
    lines 460-471) so the database always records the same primary diagnosis
    that drove the pipeline's query expansion and trial matching.

    Resolution order:
      1. Filter out refuted/entered-in-error conditions (verification_status)
      2. Filter to primary cancer conditions via CancerCodeRegistry (3-layer detection)
      3. Tiebreak: confirmed > unconfirmed, active > remission, most recent onset
      4. Return display text of the winning condition

    Fallback: if no cancer condition is found (edge case for non-cancer patients
    that somehow entered the pipeline), returns the first condition's display.
    If the condition list is empty, returns None.

    Requires _CANCER_REGISTRY (CancerCodeRegistry) in the module namespace,
    which is guaranteed by the exec chain: 13- LangGraph Agent.py initializes
    _CANCER_REGISTRY before 14- Database Logger.py is loaded.
    """
    if not conditions:
        return None

    # Step 1: Exclude refuted/entered-in-error
    valid = [
        c for c in conditions
        if (c.get("verification_status") or "unknown")
        not in _CANCER_REGISTRY.exclude_verification
    ]
    if not valid:
        valid = conditions  # fallback: use all if filter empties list

    # Step 2: Filter to primary cancer conditions
    cancer_conditions = [
        c for c in valid
        if _CANCER_REGISTRY.is_primary_cancer(c)
    ]

    # Step 3: Tiebreak and return
    if cancer_conditions:
        primary = sorted(cancer_conditions, key=_CANCER_REGISTRY.sort_key)[0]
        return primary.get("display")

    # Fallback: no cancer found, return first valid condition
    return valid[0].get("display") if valid else None


#------------------------------------------------------------------------------


# Logging function
def log_inference(result: Dict, patient_data: Dict):
    """
    Log inference result to SQLite database.

    Non-critical operation: Errors are logged but not raised to avoid
    breaking the main pipeline if database logging fails.

    The one exception is UnknownModelPricingError. Cost is computed BEFORE the
    try block below precisely so it cannot be caught by it: an unpriced model
    is a configuration defect, not a database failure, and swallowing it would
    either drop the row entirely (with a message blaming logging) or, before
    get_model_cost() learned to raise, write a row asserting the run was free.
    Either way the operator is not told that the cost column has stopped
    meaning anything. It propagates to the caller instead.
    """

    # Calculate cost using pricing config. Outside the try — see the docstring.
    total_cost = get_model_cost(
        MATCHING_MODEL,
        result.get("gpt4o_input_tokens", 0),
        result.get("gpt4o_output_tokens", 0)
    )

    conn = None
    try:
        conn = sqlite3.connect(inferences_path)
        cursor = conn.cursor()

        demographics = patient_data.get("demographics", {})
        conditions = patient_data.get("conditions", [])
        timings = result.get("stage_timings", {})

        # Sum of stage durations only — excludes LangGraph routing overhead (~50-200ms)
        total_time = sum(timings.values())

        cursor.execute('''
            INSERT INTO inferences (
                patient_id, timestamp, age, sex, race, ethnicity, primary_condition,
                condition_count, medication_count, allergy_count, expanded_query,
                candidates_retrieved, candidates_reranked, 
                bm25_retrieved, vector_retrieved, 
                candidates_after_rule_filter,
                candidates_after_quality_filter,
                candidates_filtered, mesh_dropped, mesh_resolution,
                stage_dropped, histology_dropped,
                candidates_evaluated,
                eligible_matches, near_misses,
                not_evaluable_trials, cross_vocab_remaps,
                query_expansion_time, hybrid_retrieval_time, cross_encoder_time,
                rule_filter_time, gpt4o_evaluation_time, total_time,
                gpt4o_prompt, gpt4o_input_tokens, gpt4o_output_tokens,
                matching_model, cross_encoder_model,
                pricing_version, estimated_cost_usd, qdrant_collection, error,
                patient_data_hash, expansion_prompt,
                gpt4o_retries, ablation_flags, hallucinated_trials,
                retrieval_channels, retrieval_channels_expected,
                retrieval_channels_ok, retrieval_degraded,
                retrieval_trials_lost, query_expansion_path,
                mesh_filter_applied, mesh_filter_skip_reason,
                age_reference_date, birth_date_precision
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            result["patient_id"],
            result["timestamp"],
            demographics.get("age"),
            demographics.get("sex"),
            demographics.get("race"),
            demographics.get("ethnicity"),
            result.get("primary_condition") or _resolve_primary_cancer(conditions),
            result.get("condition_count", len(deduplicate_by_display(patient_data.get("conditions", [])))),
            result.get("medication_count", len(deduplicate_by_display(patient_data.get("medications", [])))),
            result.get("allergy_count", len(patient_data.get("allergies", []))),
            result.get("expanded_query", ""),
            result.get("candidates_retrieved", 0),
            result.get("candidates_reranked", 0),
            # Observed per-channel counts from Stage 2, not the configured
            # request sizes. Inserting BM25_RETRIEVAL_SIZE / VECTOR_RETRIEVAL_SIZE
            # here made both columns constant across every row, so any ratio
            # built on them (File 16's fusion_efficiency) described the config
            # rather than the run, and a single-channel ablation still logged
            # both channels as full. NULL when the key is absent, which means a
            # result dict that did not come from a pipeline terminal node.
            result.get("bm25_retrieved"),
            result.get("vector_retrieved"),
            result.get("candidates_after_rule_filter", 0),
            result.get("candidates_after_quality_filter", 0),
            result.get("candidates_filtered", 0),
            result.get("mesh_dropped", 0),
            result.get("mesh_resolution", ""),
            result.get("stage_dropped", 0),
            result.get("histology_dropped", 0),
            result.get("candidates_evaluated", 0),
            len(result.get("matches", [])),
            len(result.get("near_misses", [])),
            # Non-evaluations are counted here, never folded into near_misses:
            # a trial that could not be assessed is not a rejection.
            result.get("not_evaluable_trials", len(result.get("not_evaluable", []))),
            result.get("cross_vocab_remaps", 0),
            timings.get("query_expansion", 0),
            timings.get("hybrid_retrieval", 0),
            timings.get("cross_encoder", 0),
            timings.get("rule_filter", 0),
            timings.get("gpt4o_evaluation", 0),
            total_time,
            result.get("gpt4o_prompt", ""),
            result.get("gpt4o_input_tokens", 0),
            result.get("gpt4o_output_tokens", 0),
            MATCHING_MODEL,
            "ncbi/MedCPT-Cross-Encoder",
            PRICING_CONFIG["last_updated"],
            total_cost,
            result.get("qdrant_collection", ""),
            result.get("error", ""),
            result.get("patient_data_hash", ""),
            result.get("expansion_prompt", ""),
            # Written by all three terminal nodes via _pipeline_provenance()
            # (File 13). Reading "gpt4o_retries_exhausted" here logged 0 for
            # every run that did not end in node_error_handler, because that
            # node was the only writer of the old key.
            result.get("gpt4o_retries", 0),                  # gpt4o_retries
            json.dumps(result.get("ablation_flags") or {}),  # ablation_flags
            # NULL until item 33's detector writes the key: see the migration
            # note above for why this is not defaulted to 0.
            result.get("hallucinated_trials"),               # hallucinated_trials
            # Degradation record. Every one of these is .get() with no default,
            # so a result dict that never reached the stage in question writes
            # NULL rather than a value that would read as "checked, all clean".
            # retrieval_channels is serialized only when present: json.dumps(None)
            # would store the string 'null', which is not the same as SQL NULL.
            (json.dumps(result["retrieval_channels"])
             if result.get("retrieval_channels") else None),
            result.get("retrieval_channels_expected"),
            result.get("retrieval_channels_ok"),
            result.get("retrieval_degraded"),
            result.get("retrieval_trials_lost"),
            result.get("query_expansion_path"),
            # bool -> 0/1 for SQLite, but None stays None: "the filter did not
            # report" is a third state and must not collapse into "did not run".
            (None if result.get("mesh_filter_applied") is None
             else int(bool(result["mesh_filter_applied"]))),
            result.get("mesh_filter_skip_reason"),
            # Age provenance. The reference date comes from the result, written
            # by _pipeline_provenance() (File 13) on all three terminal paths;
            # it falls back to the patient dict only for a caller that logs a
            # result it did not get from the graph. Both stay NULL when neither
            # reported: the age in this row is then not reproducible, and that
            # must not read as "computed against today".
            (result.get("age_reference_date")
             or demographics.get("age_reference_date")),
            (result.get("birth_date_precision")
             or demographics.get("birth_date_precision")),
        ))
        
        inference_id = cursor.lastrowid
        
        # not_evaluable trials are written too, with eligible = "not_evaluable",
        # so the criterion-level record exists for anything that reads back the
        # non-evaluations rather than only their count.
        all_trials = (
            result.get("matches", [])
            + result.get("near_misses", [])
            + result.get("not_evaluable", [])
        )

        for match in all_trials:
            # Build criterion details JSON from inclusion/exclusion arrays
            inclusion = match.get("inclusion_criteria", [])
            exclusion = match.get("exclusion_criteria", [])
            inclusion = inclusion if isinstance(inclusion, list) else []
            exclusion = exclusion if isinstance(exclusion, list) else []
            criterion_json = json.dumps({
                "inclusion":       inclusion,
                "exclusion":       exclusion,
            })
            
            cursor.execute('''
                INSERT INTO trial_matches (
                    inference_id, nct_id, trial_title, trial_phase,
                    trial_number, rerank_score, rerank_score_raw, mesh_boost, mesh_boost_tier,
                    match_score, eligible, explanation, criterion_details,
                    score_confirmed, score_denominator, criteria_not_applicable,
                    hallucinated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                inference_id,
                match.get("nct_id", ""),
                match.get("title", ""),
                match.get("phase", ""),
                match.get("trial_number"),
                match.get("rerank_score"),
                match.get("rerank_score_raw"),
                match.get("mesh_boost"),
                match.get("mesh_boost_tier"),
                match.get("match_score", 0.0),
                match.get("eligible", "not_eligible"),
                match.get("explanation", ""),
                criterion_json,
                match.get("score_confirmed"),
                match.get("score_denominator"),
                match.get("criteria_not_applicable"),
                match.get("hallucinated"),   # NULL until item 33's detector runs
            ))
        
        conn.commit()
        print(f"✓ Logged inference for patient {result['patient_id']} (ID: {inference_id})")
        
    except sqlite3.Error as e:
        if conn:
            conn.rollback()
        print(f"⚠ Database logging failed (non-critical): {e}")
        # DO NOT re-raise - logging failure should not break pipeline
    
    except Exception as e:
        if conn:
            conn.rollback()
        print(f"⚠ Logging error (non-critical): {e}")
        # DO NOT re-raise - logging failure should not break pipeline
    
    finally:
        if conn:
            conn.close()
            
            
#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 13:26:56 2026
@author: ramyalsaffar
"""