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
#
# MATCHING_MODEL is the string SENT to the API and the key looked up in
# PRICING_CONFIG below. It is NOT what gets written to inferences.matching_model
# any more: File 13 reads response.model off the Stage 5 response and File 14
# logs THAT, so a row records the model that answered rather than the model that
# was asked for. For this model the two are the same string -- probed live on
# 2026-08-04, the API echoes back "gpt-5.6-terra" with no dated snapshot, unlike
# gpt-4o-2024-08-06 -- but the pipeline no longer assumes it.
MATCHING_MODEL = "gpt-5.6-terra"  # For criterion-level evaluation

# Matching parameters
TOP_K_CANDIDATES = 40  # Top N of trials to evaluate initially with cross encoder
BM25_RETRIEVAL_SIZE = 75  # Trials from BM25 search
VECTOR_RETRIEVAL_SIZE = 100  # Trials from vector search
RRF_POOL_SIZE = 100 # Maximum candidates passed from RRF fusion to cross-encoder input

# Temperature settings
#
# MATCHING_TEMPERATURE is None because gpt-5.6-terra REJECTS the parameter.
# Probed live 2026-08-04:
#
#   temperature=0 -> 400 unsupported_value: "'temperature' does not support 0
#   with this model. Only the default (1) value is supported."
#
# None means "not sent", and File 13 does not send it. It is kept as a named
# constant rather than deleted because File 45 records it into every fixture's
# environment block, where None is the honest record of a parameter the run did
# not set. Do not set it to 1: that would claim the pipeline chose a sampling
# temperature, when in fact it has no say in the matter.
#
# CONSEQUENCE FOR DETERMINISM. Determinism is a deliberate property of this
# pipeline (see CLAUDE.md). Stages 1-4 are unaffected -- they are rule-based,
# stable-argsorted, and seeded. Stage 5 is no longer temperature-pinned, so
# identical input can now produce different verdicts across runs. MATCHING_SEED
# below is the only remaining lever and it is best-effort.
MATCHING_TEMPERATURE = None  # gpt-5.6-terra rejects any value but its default
EXPANSION_TEMPERATURE = 0  # Deterministic query expansion (Stage 1 uses no LLM)


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
#
# 2026-08-03: the corpus was regenerated on this date by
# "04- FHIR Generate Data.py" -- seed 20260805, clinician seed 20260806,
# -p 12500, no -m, ages 18-100, generate.only_alive_patients=true. This is the
# SECOND regeneration on this date; the first (seeds 20260803 / 20260804) was
# discarded because File 08 classified SNOMED 408512008 "Body mass index 40+"
# as a primary lung cancer and File 05 did not yet drop deceased patients, so
# that cohort held 48 obese non-cancer patients and was 57.7% dead. Runs against
# the two are NOT separable by this date -- they share it. They are separable by
# inferences.qdrant_collection / patient_data_hash and by the run manifest's
# seeds, and any run logged before 2026-08-03 20:49 PDT is against the discarded
# corpus.
#
# Synthea simulates up to the moment of the run, so the newest observation is
# dated on this day and nothing in the corpus postdates the reference; verified
# empirically -- 0 patients resolve to 'all_after_reference_date'.
DATA_SNAPSHOT_DATE = "2026-08-03"


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


# ---------------------------------------------------------------------------
# Stage 5 request shape and truncation control
# ---------------------------------------------------------------------------
#
# MODEL MIGRATION NOTE. MATCHING_MAX_TOKENS used to be 16,000 — GPT-4o's own
# output ceiling, not a number anyone chose — and every threshold below was
# calibrated against it. gpt-5.6-terra allows 128,000, so the ceiling is now a
# CHOSEN budget rather than a hardware limit, and the thresholds below were
# re-derived against it (see MATCHING_OUTPUT_TOKENS_PER_TRIAL).

# Reasoning effort for the Stage 5 judge.
#
# gpt-5.6-terra is a reasoning model. Probed live 2026-08-04, the values IT
# accepts are 'none', 'low', 'medium', 'high', 'xhigh' -- note that the general
# reasoning guide also lists 'minimal' and 'max', which this model rejects:
#
#   reasoning_effort='banana' -> 400 unsupported_value: "Supported values are:
#   'none', 'low', 'medium', 'high', and 'xhigh'."
#
# CHOSEN BY MEASUREMENT, not by taste. 'none', 'low' and 'medium' were run on
# 2026-08-04 against a FIXED candidate set: 30 patients (11 characterization
# fixtures, whose Stage 5 requests and GPT-4o answers are recorded verbatim,
# plus 19 corpus patients whose Stages 1-4 were executed ONCE and whose
# filtered trial list was then sent to each judge unchanged). 376 trial
# verdicts per arm. The only variable was the judge.
#
#   level    agreement with GPT-4o   $/patient   s/patient   reasoning tok/pt
#   none              69.1%            0.1882       68.0            0
#   low               64.9%            0.1679       64.2        1,066 (median)
#   medium            61.2%            0.1896       79.7        1,580 (median)
#   (gpt-4o)             --            0.1551      156.6           --
#
# 'none' WINS, and the ordering is not noise: exact McNemar on the paired
# per-trial agreement indicator gives none > low p = 0.017, none > medium
# p < 0.0001, low > medium p = 0.024. Reasoning moves this judge AWAY from the
# incumbent monotonically; it does not refine it.
#
# The direction of the drift is what settles it. Every disagreement class is
# dominated by not_eligible -> eligible (75 / 96 / 108 flips at none / low /
# medium), and the eligible rate over the same 376 trials rises from GPT-4o's
# 25.5% to 43.9% / 51.1% / 54.0%. This is a PRE-SCREENING tool: a false
# "eligible" is a trial a clinician has to read and reject. Reasoning buys more
# of them. The disagreements cluster on lab/organ-function, prior-therapy,
# staging and histology criteria -- the ones needing a judgement about whether
# absent data may be inferred -- not on the mechanical ones (performance status
# and demographics barely move).
#
# 'none' also spends exactly 0 reasoning tokens on all 30 cases, which is why
# MATCHING_MAX_TOKENS below has to cover visible output only.
#
# READ THIS BEFORE TREATING 69.1% AS A QUALITY SCORE. Agreement is against
# GPT-4o, which is the incumbent, NOT ground truth: this corpus carries no
# adjudicated eligibility labels. It is entirely possible that Terra is right
# and GPT-4o was over-rejecting. 'none' is the choice that minimises
# behavioural change across the migration, which is the defensible conservative
# position with no labels -- it is not evidence that 'none' is the most
# accurate. Settling that needs a labelled set, and it is the single largest
# open risk in this migration.
MATCHING_REASONING_EFFORT = "none"

# Output ceiling for one Stage 5 call, sent as max_completion_tokens.
#
# NOT the model's limit. gpt-5.6-terra's limit is 128,000; this is a CHOSEN
# budget, unlike the 16,000 it replaces, which was GPT-4o's own hard ceiling.
#
# DERIVED FROM THE STEP 7 RUNS at reasoning_effort='none'. The largest single
# call across the 30-patient set produced 17,077 output tokens (a full 15-trial
# batch), and the worst per-trial figure was 1,138. 32,000 is:
#   - 1.9x the largest single response actually observed;
#   - 15 trials x 2,133 tokens/trial, i.e. ~1.9x the worst per-trial rate, so a
#     batch of unusually verbose trials still fits;
#   - a quarter of the model's limit, so the number is a budget rather than the
#     model's edge;
#   - a bound of 32,000 x $12/1M = $0.38 on what one runaway call can cost.
#
# IT COUNTS REASONING TOKENS. On a reasoning model max_completion_tokens caps
# reasoning + visible output together, and a call can burn the entire ceiling
# on reasoning and return finish_reason='length' with EMPTY content. Verified
# live: at max_completion_tokens=64 with reasoning_effort='high' the response
# came back completion_tokens=64, reasoning_tokens=64, content=''. At the
# chosen effort of 'none' the reasoning term is 0 on every one of the 30
# measured runs, so this ceiling currently covers visible output alone -- but
# it stops being true the moment MATCHING_REASONING_EFFORT changes, and at
# 'medium' the reasoning share was already 12.9% of output.
MATCHING_MAX_TOKENS = 32000

# Best-effort determinism. gpt-5.6-terra ACCEPTS seed (probed live 2026-08-04:
# no error), which is why it is still sent -- but it returns no
# system_fingerprint, so there is no attestation that the same backend served
# two calls, and temperature is pinned at the provider's default (see
# MATCHING_TEMPERATURE). Stage 5 is therefore best-effort reproducible and no
# longer exactly reproducible. Stages 1-4 are unchanged and still exact.
MATCHING_SEED = 42

# Wall-clock ceiling on ONE Stage 5 request, in seconds.
#
# REPLACES AN SDK DEFAULT OF 600s. The OpenAI client in this file is built with
# no timeout argument, so every call inherits the SDK's 600-second default and
# its automatic retries (max_retries defaults to 2). One stalled request
# therefore occupies up to 30 minutes and is indistinguishable, from the
# outside, from a model taking its time. That is not hypothetical: it happened
# during the item 29a measurement runs, and the measurement data itself caught
# a second instance -- one GPT-4o call in the 30-patient bake-off returned
# after 960.2 seconds, which is the 600s timeout plus a retry, not a slow
# answer.
#
# CHOSEN FROM MEASURED PER-CALL LATENCY, not from taste. Over the 27
# single-call cases of the item 29a bake-off at the shipped configuration
# (gpt-5.6-terra, reasoning_effort='none'), one Stage 5 call took:
#
#     median 66.5s      max 94.6s
#
# Single-call cases are used deliberately: a timeout applies per REQUEST, and a
# patient whose batch splits makes several requests, so a per-patient figure
# (median 68.0s, max 193.1s over 3 calls) would overstate what one call needs.
#
# 300 seconds is 3.2x the worst single call observed and 4.5x the median, and
# half the SDK default it replaces. The headroom is deliberately generous: the
# cost of being too tight is a failed patient, while the cost of being too
# loose is only that a stall takes longer to surface.
#
# THIS DOES NOT BOUND TOTAL WALL TIME BY ITSELF. max_retries is left at the
# SDK default of 2, so a request that times out three times still occupies
# 3 x 300 = 15 minutes -- better than 30, not fixed. Bounding that properly
# means setting max_retries too, which interacts with the pipeline's own
# MAX_GPT4O_RETRIES budget and is deliberately left for a separate change.
#
# RE-DERIVE THIS IF MATCHING_REASONING_EFFORT CHANGES. The worst single call
# was 94.6s at 'none' and 107.5s at 'medium'; the higher tiers were not
# measured and could be far slower.
MATCHING_REQUEST_TIMEOUT_SECONDS = 300

# Expected output tokens per trial evaluated, used to decide whether a batch
# should be split BEFORE it is sent.
#
# RE-DERIVED 2026-08-04 for gpt-5.6-terra at reasoning_effort='none'. The
# previous value of 900 was calibrated on 1,094 GPT-4o rows (median 712, p95
# 861 per trial) and no longer describes this model, which is ~35% more verbose
# per verdict.
#
# The new figure comes from the step 7 runs: 27 of the 30 cases issued exactly
# ONE call, so output_tokens / trials_evaluated is the per-trial cost exactly,
# with no split double-counting. Over those 27 runs:
#
#     mean 959   median 974   p75 1,032   p90 1,048   p95 1,073   max 1,138
#     sd 92      (15 of the 27 were at the full 15-trial cap)
#
# Reasoning tokens are INCLUDED in these figures by construction --
# usage.completion_tokens already contains them -- and at 'none' they are 0 on
# every run, so the count is visible output. At a higher effort this constant
# must be re-derived, not scaled.
#
# 1,100 sits between p95 and the max, deliberately:
#   - above the median, because a guard that uses the average case is not a
#     guard;
#   - not at the max, because calibrating to the single worst run makes the
#     estimate a description of one patient.
#   As a predictor of the whole response it errs high, which is what a guard
#   should do: over the 27 runs the count term alone is >= the actual output in
#   26 of them, the one under-estimate is 577 tokens, and the residual sd is
#   1,398 tokens.
#
# WHERE THE THRESHOLD BITES — AND IT NO LONGER DOES. The pre-split threshold is
# 0.90 x 32,000 = 28,800. The largest estimate this function can produce at
# MAX_TRIALS_FOR_EVALUATION = 15 is the count term plus the capped criteria
# term, 1.25 x 1,100 x 15 = 20,625, which is under it by 8,175. So the PROACTIVE
# splitter cannot fire at the current batch cap: it is dead code until either
# MAX_TRIALS_FOR_EVALUATION rises above 20 or the ceiling comes down. That is a
# consequence of moving from a model whose 16,000-token ceiling the batch nearly
# filled to one with 128,000, and it is stated here rather than discovered later
# from a splitter whose counter never moves.
#
# The REACTIVE path and the single-trial floor stay, and are still the only
# thing standing between a runaway response and a lost patient. Neither fired in
# the 30-run measurement either: the worst single call reached 17,077 of 32,000.
# Both are exercised by the truncation_split fixture, which injects a truncation
# rather than waiting for one (see File 45).
MATCHING_OUTPUT_TOKENS_PER_TRIAL = 1100

# Fraction of MATCHING_MAX_TOKENS the estimate may reach before a batch is
# split pre-emptively. 0.90 leaves 3,200 tokens of headroom for the estimate's
# own error, which is ~1,398 tokens (1 sd) over the 27-run calibration set —
# 2.3 sd of margin, against 0.8 sd under the previous model and ceiling.
MATCHING_OUTPUT_SPLIT_FRACTION = 0.90

# How many times a batch may be HALVED because of truncation. This is depth,
# not repetition, and it is deliberately a separate budget from
# MAX_GPT4O_RETRIES: a patient that hits one malformed response and then needs
# two splits must not be failed for exhausting a shared counter. Three levels
# takes a 15-trial batch to 2 trials.
MAX_TRUNCATION_SPLITS = 3

# Characters per token. The same crude proxy File 11 uses for its embedding
# batch sizing; kept identical so the two agree, and kept crude on purpose —
# tiktoken would be a dependency and an import cost for an estimate whose job
# is to be roughly right before a call that is about to measure it exactly.
CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# Stage 1 query expansion
# ---------------------------------------------------------------------------

# Ceiling on distinct genomic variant terms that reach the expanded query and
# the R4 rerank query. R4 is scored by MedCPT, which was trained on 2-10 word
# PubMed queries, so an unbounded list of variants is a worse query, not a
# better one.
MAX_VARIANT_TERMS = 8


#------------------------------------------------------------------------------


# OpenAI Pricing Configuration
#------------------------------
# Last verified: 2026-08-04
# Update quarterly from: https://openai.com/api/pricing/
# Prices are per 1M tokens (USD)
#
# KEYED ON WHAT THE API RETURNS, not on what was requested. File 13 reads
# response.model off the Stage 5 response and File 14 prices against that
# string, so an alias that resolves to a dated snapshot must have the SNAPSHOT
# in this table or every row it produces raises UnknownModelPricingError.
# gpt-5.6-terra was probed live on 2026-08-04 and echoes back "gpt-5.6-terra"
# verbatim -- one string, one entry. gpt-4o-2024-08-06 is already a snapshot.
#
# The GPT-4o entry stays. It is not the judge any more, but 1,000+ historical
# rows in inferences.db carry it in matching_model, and File 16 / File 21 now
# price each row against its OWN model; removing it would make every one of
# those rows unpriceable.

PRICING_CONFIG = {
    "last_updated": "2026-08-04",
    "models": {
        # Stage 5 judge as of 2026-08-04. Priced from OpenAI's model page
        # (developers.openai.com/api/docs/models/gpt-5.6-terra), read
        # 2026-08-04. Reasoning tokens bill at the OUTPUT rate and are already
        # inside usage.completion_tokens, so they are priced by this row with
        # no separate term -- see File 13's note at the token accumulator.
        #
        # NOT MODELLED HERE: cached input is $0.20/1M, and a request over
        # 272,000 input tokens is billed at 2x input / 1.5x output for the
        # whole request. Neither applies to this pipeline as configured -- a
        # 15-trial Stage 5 prompt runs ~20k input tokens, two orders of
        # magnitude under the threshold, and get_model_cost() has no cached-
        # token input. Both would need adding if MAX_TRIALS_FOR_EVALUATION grew
        # by ~15x or prompt caching were turned on.
        "gpt-5.6-terra": {
            "input": 2.00,
            "output": 12.00
        },
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


# ECOG availability alert (File 20)
#----------------------------------
# Fraction of reporting rows whose patient HAD an ECOG observation on file that
# could not be used -- inferences.ecog_selection is not NULL and is not
# 'none_recorded', while inferences.ecog_value IS NULL. Above this fraction,
# ecog_unavailable_rate alerts.
#
# This is a THRESHOLD alert, not a comparison against the baseline window, and
# the difference is the whole point. The failure it exists to catch is a corpus
# regenerated with a DATA_SNAPSHOT_DATE older than its own observations: every
# patient then resolves to 'all_after_reference_date', every ECOG criterion
# becomes not_evaluable, and eligible-match counts fall across the board. A
# z-score against baseline would read ~0 if the baseline window were itself
# captured after that regeneration -- the metric would go quiet in exactly the
# case it was added for. A rate is alarming at 1.0 whatever the baseline was.
#
# HOLDING VALUE, NOT CALIBRATED. The only corpus measured so far (the 3,000-
# patient scratch run) sits at 1/141 = 0.007, so 0.20 is roughly thirty times
# the observed rate: high enough not to fire on a handful of genuinely late
# observations in a short window, low enough that a systematic mismatch cannot
# hide under it. It is not fitted to anything, and no false-positive or
# false-negative rate has been measured for it. Same status as
# MESH_BOOST_DIRECT_FRACTION and ECOG_SCORE_DISTRIBUTION.
ECOG_UNAVAILABLE_RATE_THRESHOLD = 0.20


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
# ECOG PERFORMANCE STATUS SYNTHESIS (File 04, Synthea module)
# ===========================================================================
#
# Synthea ships no performance-status module, so no bundle in the corpus
# carries one. Nearly every interventional oncology trial gates on ECOG
# (typically 0-1 or 0-2), which means that whole class of criterion is
# unevaluable for every patient: Stage 5 can only answer "not stated", and a
# criterion that is always "not stated" contributes nothing to the verdict.
# File 04 writes a custom Generic Module Framework module that supplies one.
#
# BOTH VALUES BELOW ARE UNCALIBRATED HOLDING VALUES. Neither was fitted to a
# registry, a trial screening log, or any published cohort. They are stated
# here, restated in the module's own remarks block, and copied verbatim into
# the run manifest so that any corpus generated from them carries the numbers
# it was built with. Replace them with fitted values before any claim is made
# about eligibility rates -- the eligible/not-eligible split the pipeline
# reports moves directly with these two numbers.


# Score distribution over ECOG 0-4, applied to every cancer patient that
# receives a documented score.
#
# Grade 5 is "dead" and is deliberately absent: it is a valid ECOG value but a
# dead patient is not a trial candidate, so emitting it would create rows that
# can only ever be excluded. Absence here is what keeps 5 out of the corpus --
# there is no separate suppression step.
#
# Skewed toward 0 and 1 rather than flat across 0-4 because the modelled
# population is patients reaching trial screening, who are selected for
# function: a flat 0-4 would put 60% of the cohort at ECOG >= 2 and make the
# corpus look far less eligible than any real screening population. The exact
# split (0.35 / 0.40 / 0.15 / 0.07 / 0.03) is a plausible shape, not a
# measurement.
#
# Keys are the integer scores. Values must sum to 1.0; File 04 asserts this
# before writing the module, because Synthea's distributed_transition
# normalises silently and a set of weights that does not sum to 1 would
# produce a distribution nobody chose.
ECOG_SCORE_DISTRIBUTION = {
    0: 0.35,   # Fully active, no restriction
    1: 0.40,   # Restricted in strenuous activity, ambulatory
    2: 0.15,   # Ambulatory, up >50% of waking hours, no work
    3: 0.07,   # Limited self-care, confined to bed/chair >50% of waking hours
    4: 0.03,   # Completely disabled, totally confined
}

# Fraction of cancer patients who carry NO ECOG observation at all.
#
# Real oncology records frequently lack a documented performance status, and a
# corpus where every patient has one would let the pipeline evaluate an ECOG
# criterion for 100% of patients -- an accuracy the source data does not have.
# Modelling the gap keeps "criterion not evaluable" on the table as an outcome.
# These patients carry no observation rather than a default score, because a
# defaulted 0 is indistinguishable from a measured 0 downstream.
#
# This is the fraction drawn *deliberately*. The observed missingness in a
# generated corpus is always HIGHER, because a patient who dies or reaches the
# end of the simulation before the next encounter after diagnosis also ends up
# with no observation. Both numbers are written to the run manifest
# (ecog.missingness_fraction_configured vs ecog.missingness_fraction_observed)
# so the gap is visible rather than inferred.
ECOG_MISSINGNESS_FRACTION = 0.25


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