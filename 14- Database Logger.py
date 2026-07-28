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
    stage_dropped INTEGER,
    histology_dropped INTEGER,
    candidates_evaluated INTEGER,
    eligible_matches INTEGER,
    near_misses INTEGER,
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
    ablation_flags TEXT
)
''')


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
    match_score REAL,
    eligible TEXT,
    explanation TEXT,
    criterion_details TEXT,
    FOREIGN KEY (inference_id) REFERENCES inferences(id)
)
''')


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
    """
    
    conn = None
    try:
        conn = sqlite3.connect(inferences_path)
        cursor = conn.cursor()
        
        demographics = patient_data.get("demographics", {})
        conditions = patient_data.get("conditions", [])
        timings = result.get("stage_timings", {})
        
        # Sum of stage durations only — excludes LangGraph routing overhead (~50-200ms)
        total_time = sum(timings.values())
        
        # Calculate cost using pricing config
        total_cost = get_model_cost(
            MATCHING_MODEL,
            result.get("gpt4o_input_tokens", 0),
            result.get("gpt4o_output_tokens", 0)
        )

        cursor.execute('''
            INSERT INTO inferences (
                patient_id, timestamp, age, sex, race, ethnicity, primary_condition,
                condition_count, medication_count, allergy_count, expanded_query,
                candidates_retrieved, candidates_reranked, 
                bm25_retrieved, vector_retrieved, 
                candidates_after_rule_filter,
                candidates_after_quality_filter,
                candidates_filtered, mesh_dropped, stage_dropped, histology_dropped,
                candidates_evaluated,
                eligible_matches, near_misses,
                query_expansion_time, hybrid_retrieval_time, cross_encoder_time,
                rule_filter_time, gpt4o_evaluation_time, total_time,
                gpt4o_prompt, gpt4o_input_tokens, gpt4o_output_tokens,
                matching_model, cross_encoder_model,
                pricing_version, estimated_cost_usd, qdrant_collection, error,
                patient_data_hash, expansion_prompt,
                gpt4o_retries, ablation_flags
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            BM25_RETRIEVAL_SIZE,
            VECTOR_RETRIEVAL_SIZE,
            result.get("candidates_after_rule_filter", 0),
            result.get("candidates_after_quality_filter", 0),
            result.get("candidates_filtered", 0),
            result.get("mesh_dropped", 0),
            result.get("stage_dropped", 0),
            result.get("histology_dropped", 0),
            result.get("candidates_evaluated", 0),
            len(result.get("matches", [])),
            len(result.get("near_misses", [])),
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
            result.get("gpt4o_retries_exhausted", 0),        # gpt4o_retries
            json.dumps(result.get("ablation_flags") or {}),  # ablation_flags
        ))
        
        inference_id = cursor.lastrowid
        
        for match in result.get("matches", []) + result.get("near_misses", []):
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
                    trial_number, rerank_score, match_score, eligible, explanation, criterion_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                inference_id,
                match.get("nct_id", ""),
                match.get("title", ""),
                match.get("phase", ""),
                match.get("trial_number"),
                match.get("rerank_score"),
                match.get("match_score", 0.0),
                match.get("eligible", "not_eligible"),
                match.get("explanation", ""),
                criterion_json
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