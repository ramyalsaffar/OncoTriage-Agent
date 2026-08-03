# The Project Main Configuration Values
#######################################


#------------------------------------------------------------------------------


# Project Name: OncoTriage Agent
#-------------------------------
Project_Name = "OncoTriage Agent"


# Configuration
#--------------

# Qdrant collection name
COLLECTION_NAME = "trial_criteria"

# OpenAI embedding model
EMBEDDING_MODEL = "text-embedding-3-small"

# Embedding dimensionality
EMBEDDING_DIM = 1536

# LLM models
MATCHING_MODEL = "gpt-4o-2024-08-06"  # For criterion-level evaluation

# Matching parameters
TOP_K_CANDIDATES = 40  # Top N of trials to evaluate initially with cross encoder
BM25_RETRIEVAL_SIZE = 75  # Trials from BM25 search
VECTOR_RETRIEVAL_SIZE = 100  # Trials from vector search
RRF_POOL_SIZE = 100 # Maximum candidates passed from RRF fusion to cross-encoder input

# Temperature settings
MATCHING_TEMPERATURE = 0  # Deterministic matching
EXPANSION_TEMPERATURE = 0  # Deterministic query expansion


# Patient data snapshot date
#---------------------------
# The date the Synthea FHIR corpus under data_fhir_path was generated. Patient
# ages are computed against THIS date, never against the current clock.
#
# Age was derived from datetime.now() at parse time, so the same bundle parsed
# on two different days could yield two different ages, and age is printed into
# the Stage 5 system prompt. compute_patient_hash() (File 13) keys on
# birth_date and cannot observe the clock, so two runs could carry the same
# patient_data_hash -- this project's "identical input" guarantee -- while
# sending GPT-4o different prompt text. That is invisible in exactly the
# comparison the ablation study makes, where the claim is that only the model
# varied.
#
# It is the snapshot date rather than an arbitrary frozen date so the ages stay
# true to the records: every event in a bundle predates generation. Update it
# whenever "04- FHIR Generate Data.py" regenerates the corpus -- ages shift by
# the gap, which is the honest consequence of new data, and every affected run
# is identifiable by the age_reference_date column in inferences.db.
DATA_SNAPSHOT_DATE = "2026-03-11"


# Clinical trials main characteristics for scraping
# Used at the RAG Trial Indexer
#--------------------------------------------------
trial_dict = {"condition": "neoplasms",
              "status": "RECRUITING",
              "study_type": "INTERVENTIONAL",
              "age": "ADULT",
              "max_trials": 25000}

# Trials re-rank score
# dropping below threshold because they have weak relevance
# High End ~+10, Neutral (0.0), Low End ~-25.0
RERANK_SCORE_THRESHOLD = -10


# MeSH relevance boost (end of Stage 3, cross-encoder rerank)
#------------------------------------------------------------
# Expressed as a FRACTION of the RRF score spread (max - min) inside the
# reranked batch, so the boost scales with the batch's own distribution.
#
# Both tiers are equal: one graded relevance signal instead of a 3-to-1 split.
# The previous direct-match value of 0.75 moved a trial three quarters of the
# whole range, so a bottom-ranked same-site trial landed level with the
# top-ranked trial from another site and the cross-encoder stopped affecting
# order at all. At 0.25 a boost moves a trial about a quarter of the range.
#
# HOLDING VALUE: chosen to keep the boost inside the distribution, not tuned
# against a labelled benchmark. The boost is recorded per trial
# (trial_matches.mesh_boost) so its effect on ranking can be measured.
MESH_BOOST_DIRECT_FRACTION = 0.25   # trial shares MeSH C04 ancestry with the patient
MESH_BOOST_PAN_FRACTION    = 0.25   # trial targets a broad neoplasm category (depth <= 2)

# Absolute fallbacks used only when the RRF spread is degenerate (every trial
# tied), where a fraction of the spread would be exactly 0.
MESH_BOOST_DIRECT_FLOOR = 0.005
MESH_BOOST_PAN_FLOOR    = 0.005


# Stage 4 dynamic quality gate
#-----------------------------
# Percentile of the UNBOOSTED rerank score below which a trial is dropped.
# Computed on rerank_score_raw, never on the MeSH-boosted score: gating on the
# boosted score would make the gate a second, uncounted MeSH filter.
QUALITY_THRESHOLD_PERCENTILE = 25


# Limiting the number of trials sent to GPT
MAX_TRIALS_FOR_EVALUATION = 15


# API Rate Limiting Configuration
#---------------------------------
ENABLE_RATE_LIMITING = False  # Toggle: True for production, False for batch evaluation
RATE_LIMIT = "60/minute"     # Requests allowed per time window, Time window: "minute", "hour", "day"


# Airflow trial_refresh_weekly schedule
#--------------------------------------
# Cron expression for the weekly index rebuild DAG written by
# "23- Airflow DAG.py", or None for no automatic runs. The DAG stays registered
# and manually triggerable either way -- None removes the timetable, it does not
# remove the DAG.
#
# Currently None. The DAG's rebuild_index task creates its staging collection
# with vectors_config only and no sparse_vectors_config, so the collection it
# swaps the "trial_criteria" alias onto carries zero sparse vectors; it also
# skips create_payload_indexes, enrich_structured_eligibility and
# enrich_histology_tags, and it performs the alias swap inside rebuild_index
# BEFORE verify_index runs, which is swap-then-check rather than the
# check-then-swap that "11- RAG Trial Indexer.py" does. An unattended Sunday run
# would therefore point live traffic at a half-built index and only afterwards
# discover it was half-built. Cleanup keeps timestamped[2:], so the current
# collection plus one prior survive and a rollback target exists, but rollback
# is manual.
#
# The cost of None is staleness: the index holds ACTIVELY RECRUITING trials, so
# it drifts toward closed studies for as long as this stays off. Trigger
# "11- RAG Trial Indexer.py" manually rather than restoring the schedule as a
# way of refreshing.
#
# Restore to "0 2 * * 0" (Sundays 02:00 UTC) once the DAG's rebuild task builds
# the collection the way file 11 does and verifies before swapping. Changing it
# here is not enough on its own: the scheduler parses the generated file under
# {airflow_path}/dags/, and file 23 will not overwrite an existing one.
AIRFLOW_DAG_SCHEDULE = None


#------------------------------------------------------------------------------


# Initialize clients
keys = load_env_keys()
openai_api_key = keys['openai']
qdrant_url = keys['qdrant_url']
qdrant_api_key = keys['qdrant_key']


openai_client = OpenAI(api_key=openai_api_key)
qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key, timeout=120)


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Configuration Constants (retry behavior) for LangGraph Agent
# ---------------------------------------------------------------------------

MAX_GPT4O_RETRIES = 3
RETRY_BASE_DELAY = 1  # seconds, doubles each retry


#------------------------------------------------------------------------------


# OpenAI Pricing Configuration
#------------------------------
# Last verified: 2026-02-16
# Update quarterly from: https://openai.com/api/pricing/
# Prices are per 1M tokens (USD)

PRICING_CONFIG = {
    "last_updated": "2026-02-16",
    "models": {
        "gpt-4o-2024-08-06": {
            "input": 2.50,
            "output": 10.00
        },
        "text-embedding-3-small": {
            "input": 0.02,
            "output": 0.0  # Embeddings only charge input tokens
        }
    }
}


#------------------------------------------------------------------------------


# ===========================================================================
# Drift Detection CONFIGURATION
# ===========================================================================

# Baseline and comparison windows
BASELINE_WINDOW_DAYS = 30       # First 30 days as baseline
COMPARISON_WINDOW_DAYS = 7      # Compare last 7 days to baseline
MIN_SAMPLES_BASELINE = 20       # Minimum inferences for valid baseline
MIN_SAMPLES_COMPARISON = 5      # Minimum inferences for valid comparison

# Alert thresholds
KS_TEST_THRESHOLD = 0.05        # p-value < 0.05 = significant drift
PSI_THRESHOLD = 0.2             # PSI > 0.2 = moderate drift (industry standard)
Z_SCORE_THRESHOLD = 2.0         # |z| > 2.0 = alert (2 standard deviations)

# PSI bins for continuous variables
PSI_BINS = 10


#------------------------------------------------------------------------------


# ===========================================================================
# BATCH RUNNER CONFIGURATION
# ===========================================================================

# Patients per progress-reporting batch (does NOT limit total patients)
BATCH_SIZE = 200

# Number of already-processed patients to re-run after the main pass
RESAMPLE_COUNT = 100

# Random seed for reproducible resampling
RESAMPLE_SEED = 42

# Checkpoint file: tracks completed filename stems for crash recovery
# Stored alongside the batch runner file for easy access
CHECKPOINT_FILENAME = "batch_runner_checkpoint.json"

# Results output file: per-patient summary written after each patient
# (separate from SQLite DB -- lightweight JSON for quick inspection)
RESULTS_FILENAME = "batch_runner_results.json"

# API workers count
MAX_WORKERS = 12


#------------------------------------------------------------------------------


# ===========================================================================
# COHORT FILTER CONFIGURATION (File 05)
# ===========================================================================

# Deletion manifest: written to checkpoint_path BEFORE File 05 unlinks any
# patient bundle and rewritten as the deletions land, so an IO error or a kill
# mid-loop leaves a durable record of what was targeted, what was removed and
# what was not. File 05's deletion is in-place and irreversible; this file is
# the only account of it.
COHORT_MANIFEST_FILENAME = "cohort_deletion_manifest.json"

# Manifest is flushed to disk every N deletions while a phase runs, bounding
# how far the on-disk record can lag reality if the process is killed.
COHORT_MANIFEST_FLUSH_EVERY = 100


#------------------------------------------------------------------------------


# ===========================================================================
# ABLATION ANALYSIS CONFIGURATION (File 27)
# ===========================================================================

# The ablation study tests every non-baseline configuration against the
# baseline on every outcome metric, so the test family is (n_configs - 1) x
# (n_outcome_metrics) and grows multiplicatively. Uncorrected, a 18-test
# family has a 60% chance of producing at least one spurious result at
# alpha = 0.05. Benjamini-Hochberg controls the false discovery rate across
# the whole family instead.
ABLATION_FDR_ALPHA = 0.05

# Only these metrics are hypotheses and therefore only these enter the
# corrected family. Cost, latency and candidate counts are near-deterministic
# consequences of removing a pipeline stage -- testing them asks whether
# switching off the cross-encoder makes the cross-encoder cheaper. They are
# reported descriptively instead (see ABLATION_DESCRIPTIVE_METRICS).
ABLATION_OUTCOME_METRICS = ["eligible_count", "avg_match_score_all", "has_match"]

# Reported with means and paired deltas, never with a p-value.
ABLATION_DESCRIPTIVE_METRICS = ["estimated_cost_usd", "total_time",
                                "candidates_evaluated"]

# Target power for the minimum-detectable-effect calculation reported in the
# methods block. Without it a null result is ambiguous between "no effect"
# and "no power".
ABLATION_POWER_TARGET = 0.80

# A configuration with fewer than this many patients paired against the
# baseline is excluded from the test family. Exclusions are reported, never
# silent.
ABLATION_MIN_PAIRED = 10


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 21:47:10 2026

@author: ramyalsaffar
"""