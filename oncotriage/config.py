"""The project's main configuration values.

Moved out of ``03- Config.py`` by item 20c. ``03- Config.py`` survived as a shim
that re-exported every name below and additionally bound the eager client names
(``openai_client``, ``qdrant_client``) that Files 04 to 46 expected to find in
the shared exec namespace. IT IS DELETED AS OF PASS 20e: the last three files
that chained it — 05, 09 and 13 — became thin entry points in that pass, and
nothing else in the repository loaded it.

WHAT THE SHIM'S EAGERNESS WAS FOR, recorded here because the trade-off is about
this module. The chain's contract was that after ``03- Config.py`` the clients
EXIST, so the shim called ``get_keys()``, ``get_sdk_default_timeout()``,
``get_openai_client()`` and ``get_qdrant_client()`` at load and bound their
results. Loading it therefore required a readable .env with all three keys, and
that was deliberate: half-loading would have moved the failure to a random later
line instead of that one. Nothing does that now — every consumer calls a factory
— so a process that never needs a client never reads the .env, and a missing key
surfaces at the first call rather than at import. That is strictly better and it
is a real behavioural difference, stated rather than discovered.

``DATA_SNAPSHOT_DATE`` LIVES HERE AND ONLY HERE.
``tests/test_config_snapshot_date_rot.py`` rewrites the assignment as TEXT in
this file and re-runs two tests as subprocesses at several dates. The shim
re-exported the name, which never gave it back — a name bound by an import
cannot be patched by editing the file that imported it — and that is why the
rot test has always targeted this file.

WHAT CHANGED, and why
---------------------
File 03 did four things at load time that a module must not do at import:

  1. ``keys = load_env_keys()``       — read a .env off disk, and raised if the
                                        file was absent;
  2. ``OpenAI(api_key=...).timeout``  — built a throwaway client purely to read
                                        the SDK's default connect timeout;
  3. ``openai_client = OpenAI(...)``  — the live OpenAI client;
  4. ``qdrant_client = QdrantClient(...)`` — the live Qdrant client.

All four are now LAZY, cached factories: ``get_keys()``,
``get_sdk_default_timeout()``, ``get_openai_client()``, ``get_qdrant_client()``.
Everything derived from (2) — ``SDK_DEFAULT_CONNECT_TIMEOUT_SECONDS`` and the
two structured ``httpx.Timeout`` objects — became lazy with it, because a value
derived at import from a lazy source is not lazy at all.

Each factory builds at most one object and returns the same one thereafter, so
every caller sees one OpenAI client and one Qdrant client per process, however
many times it asks.

NOTHING REDIRECTS A CLIENT BY REBINDING A NAME ANY MORE. ``fixture_capture.py``
and ``fixture_replay.py`` used to rebind ``openai_client`` / ``qdrant_client``
in the shared exec namespace to recording proxies, which worked only because the
pipeline resolved the NAME at call time inside one shared dict; both install
``deps.set_override(...)`` since pass 20c-3d. ``36- Logging Contract Test.py``
stopped at pass 20c-2c and is now
``tests/test_storage_inference_logging_contract.py``. The seam is
``oncotriage/agent/deps.py`` and nothing else — see its docstring for why an
implicit seam that a caller can reach around is the defect this project exists
to remove.

WHICH CLIENT SOURCE A MODULE USES IS A DECISION, NOT A HABIT. The agent reaches
clients through ``oncotriage.agent.deps``; ``oncotriage/retrieval/indexer.py``
and ``oncotriage/retrieval/qdrant_backup.py`` deliberately use the factories
HERE instead, because an index build and a backup must not be redirected by a
stub installed for an agent test. ``oncotriage/retrieval/index_validator.py``
goes through ``deps``, because the question it answers is "is this index healthy
for the AGENT to query".

Imports ``oncotriage.paths`` and nothing else from the project. It must never
import ``oncotriage.utils``: that is the cycle item 20c removed.

The one thing it needs is ``load_env_keys``, which lived in ``settings`` for
pass 20c-1 and moved to ``paths`` in pass 20c-2a, beside the ``keys_path`` it
defaults to. Importing ``paths`` therefore resolves the directory tree as a side
effect of importing this module — globs and prints, no file read, no client. It
did not before, when the import was ``settings``; that is the cost of removing a
deferred import, and it is stated here rather than discovered from a stray
"[Paths] Project root" line in a log.
"""

import httpx
from openai import OpenAI
from qdrant_client import QdrantClient

from oncotriage import paths
from oncotriage import settings
from oncotriage.observability import console


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

# --- Index-build throughput: how an index build is CHUNKED -----------------
#
# These three bound the SIZE OF A REQUEST, and nothing else. They were
# function-local literals in oncotriage/retrieval/indexer.py:index_trials()
# under a comment describing values none of them had; the values here are the
# ones that code has actually been running.
#
# THEY CHANGE HOW LONG AN INDEX BUILD TAKES AND NOT ONE VECTOR IT WRITES.
# Embedding is per-text, so the same trial text embeds to the same vector
# whether it arrived in a request of 1 input or 750, and Qdrant stores the same
# point whether it was upserted alone or with 99 others. That is why they are
# DELIBERATELY ABSENT from tracking.CONFIGURATION_PARAM_NAMES and from the
# fixture tunables block in oncotriage/fixtures/capture.py, on those two
# declarations' own doctrine: a parameter that cannot explain a difference
# between two results is noise in a comparison of them. Do not "fix" either
# omission -- MAX_WORKERS and the SQLite tunables are excluded for the same
# reason and are named in tracking.py's argument.
#
# WHAT EACH BOUNDS. The batch sizer takes the smaller of the two embedding
# bounds, so whichever binds first is the one in force:
#   * EMBED_TARGET_TOKENS_PER_REQUEST -- the token budget per embeddings
#     request, measured with the CHARS_PER_TOKEN proxy below over a sample of
#     the corpus. Deliberately conservative, because the proxy is an estimate
#     and the cost of over-shooting is a request the endpoint rejects part-way
#     through a build.
#   * EMBED_MAX_INPUTS_PER_REQUEST -- the cap on inputs in one request, which
#     binds instead of the token budget whenever the corpus's trials are short.
#
#     NEITHER NUMBER IS A VENDOR LIMIT QUOTED HERE, and that is deliberate:
#     both are OUR request policy, they are the values this code has been
#     running, and this pass moved them without re-deriving them. The comment
#     they replaced quoted an OpenAI ceiling ("800K tokens", "2048 inputs")
#     that the code beneath it had not matched for as long as anyone can tell.
#     Whoever raises one of these should check the endpoint's current limits
#     themselves rather than trusting a figure written down in a config file.
#   * QDRANT_UPSERT_BATCH_SIZE -- points per upsert call. This is Qdrant
#     sizing, not OpenAI: it bounds one HTTP body and one checkpoint interval,
#     because index_trials() confirms nct_ids and saves its checkpoint only
#     after a successful upsert. Raising it makes a crash cost more re-embedding.
EMBED_TARGET_TOKENS_PER_REQUEST = 100_000
EMBED_MAX_INPUTS_PER_REQUEST = 750
QDRANT_UPSERT_BATCH_SIZE = 100

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

# CROSS_ENCODER_MODEL is the Stage 3 re-ranker's HuggingFace checkpoint, and
# before pass 20f-2 it was WRITTEN OUT SIX TIMES with no constant and no check
# (oncotriage/agent/deps.py twice, oncotriage/api/server.py,
# oncotriage/storage/database_logger.py, oncotriage/fixtures/capture.py, and a
# row seed in tests/test_storage_query_layer.py).
#
# THE PAIR THAT MATTERS IS THE TOKENIZER AND THE CHECKPOINT. deps.py loads
# AutoTokenizer.from_pretrained(...) and
# AutoModelForSequenceClassification.from_pretrained(...) independently. A
# cross-encoder scores a (query, document) pair by tokenizing both with the
# tokenizer that was trained with the weights: give it a tokenizer from another
# checkpoint and the token IDs address a different vocabulary than the
# embedding matrix was trained on. transformers raises nothing for that -- it
# will happily run a BERT tokenizer into a BERT-shaped model -- so the run
# produces scores, node_cross_encoder_rerank sorts them, the Stage 4 quality
# gate drops some, and the only symptom is that the ranking is noise. That is the
# same shape as the BM25 vocabulary hazard oncotriage/embedding.py exists for,
# with the same absence of any error to notice.
#
# WHY IT IS HERE AND NOT BESIDE ITS LOADER, which is where the BM25 precedent
# put BM25_SPARSE_MODEL_NAME. Two reasons, and the first one is decisive rather
# than aesthetic:
#
#   1. LAYERING. oncotriage/storage/database_logger.py writes this string into
#      inferences.cross_encoder_model on every row, and `storage` may not import
#      `agent` -- the dependency direction in oncotriage/__init__.py has storage
#      below the agent, and an accessor module for the agent's models is the
#      wrong place for a column value the logger needs. `config` is already
#      imported by all four package readers (deps, server, database_logger,
#      fixtures.capture) and imports none of them.
#   2. FAMILY. This is a model IDENTITY, the same kind of fact as
#      EMBEDDING_MODEL and MATCHING_MODEL directly above it: a string naming
#      which model answers, reported by GET /pipeline/info beside those two and
#      logged beside them per row. Three model identities in three places would
#      be the arrangement that let this one rot in the first place.
#
# BM25_SPARSE_MODEL_NAME STAYS IN oncotriage/embedding.py, and the asymmetry is
# argued rather than tolerated: that name has exactly one consumer that matters
# -- the single construction site in the same file -- and its comment carries
# the "changing it rebuilds the index" warning, which belongs against the line
# that builds the encoder. This one has readers in four subpackages that must
# not import one another, which is a constraint that name does not have.
#
# CHANGING IT INVALIDATES NOTHING ON DISK, unlike the BM25 name: the
# cross-encoder scores at query time and writes no vectors. It DOES change every
# ranking, so the twelve characterization fixtures would replay as misses on
# recordings.cross_encoder and would have to be recaptured -- which costs money.
CROSS_ENCODER_MODEL = "ncbi/MedCPT-Cross-Encoder"

# CROSS_ENCODER_MAX_LENGTH is the sequence limit every tokenizer call in this
# project passes as `max_length`, and it belongs TO THE CHECKPOINT NAMED
# DIRECTLY ABOVE. MedCPT is BERT-shaped: its weights carry 512 learned position
# embeddings and there is no position 512. So these two constants change
# TOGETHER OR NOT AT ALL -- a checkpoint edit that leaves this number behind is
# the defect this pairing exists to prevent, one level below the tokenizer /
# weights pairing argued above.
#
# TRUNCATION AT THIS LIMIT IS REQUESTED BEHAVIOUR, WHICH IS WHY THE DRIFT IS
# SILENT. Every call site passes `truncation=True` beside it, so transformers
# does exactly what it was told: it cuts the pair to this many tokens and
# returns. Set it BELOW the checkpoint's real budget and nothing raises, no
# counter moves, the cross-encoder keeps scoring and keeps ranking -- it is
# simply reading less of every trial than it could, and the only symptom is
# that Stage 3's ordering is worse. That is the same absence-of-any-error the
# comment above records for a mismatched tokenizer/weights pair, and the same
# one oncotriage/embedding.py exists for on the BM25 side. Set it ABOVE and the
# failure is loud but late -- an IndexError out of the embedding lookup, per
# patient, thirty frames inside Stage 3.
#
# WHAT VERIFIES IT, AND WHAT THE CHECKPOINT ACTUALLY DECLARES. Both MedCPT
# factories in oncotriage/agent/deps.py call
# _verify_cross_encoder_sequence_limit() on whatever the loaded object declares,
# and a declared value that DIFFERS from this constant raises
# CrossEncoderLimitMismatchError. Measured against the cached checkpoint on
# 2026-08-21 rather than assumed, because it decides which half is load-bearing:
#
#   tokenizer_config.json  "model_max_length": 1000000000000000019884624838656
#                          -- transformers' VERY_LARGE_INTEGER, i.e. the
#                          tokenizer declares NO limit at all
#   config.json            "max_position_embeddings": 512
#                          -- the weights declare 512, and this is the fact
#                          that makes 512 correct
#
# So the WEIGHTS are what verify this number and the tokenizer half is expected
# to report "undeclared" on this checkpoint. That asymmetry is recorded at the
# verifier, and "undeclared" is COUNTED rather than raised on
# (CROSS_ENCODER_LIMIT_DEGRADATIONS) because a checkpoint that declines to
# declare a limit is not a checkpoint that contradicts one.
#
# IF A DELIBERATELY SMALLER BUDGET IS EVER WANTED -- truncating harder than the
# model allows, for latency -- that is a SECOND named constant here with the
# measurement that justifies it, on the RRF_K precedent below. Do not lower this
# one: its declared contract is that it IS the checkpoint's limit, and the
# verifier enforces that.
#
# CHANGING IT CHANGES EVERY RANKING, exactly as CROSS_ENCODER_MODEL does, so the
# twelve characterization fixtures would replay as misses on
# recordings.cross_encoder and would have to be recaptured, which costs money.
CROSS_ENCODER_MAX_LENGTH = 512

# Matching parameters
TOP_K_CANDIDATES = 40  # Top N of trials to evaluate initially with cross encoder
BM25_RETRIEVAL_SIZE = 75  # Trials from BM25 search
VECTOR_RETRIEVAL_SIZE = 100  # Trials from vector search
RRF_POOL_SIZE = 100 # Maximum candidates passed from RRF fusion to cross-encoder input

# --- Reciprocal Rank Fusion: the one owner of every fusion constant ---------
#
# RRF_K is read by BOTH fusion sites, and that single ownership is the point of
# moving it here. Stage 2 (`node_hybrid_retrieval`) fuses four retrieval
# CHANNELS; Stage 3 (`node_cross_encoder_rerank`) fuses the per-query rankings
# of three RERANK QUERIES. Both are RRF over rank lists, both used 60, and the
# module-level constant in `oncotriage/agent/retrieval.py` carried a comment
# asserting it was "same as Stage 2 hybrid retrieval" -- a claim two independent
# literals could only keep by hand. One name makes it true by construction.
#
# IF THE TWO STAGES EVER NEED TO DIVERGE, add a second NAMED constant here with
# the measurement that justifies it (a rank-fusion k governs how fast the
# contribution of a lower-ranked item decays, and the two stages fuse different
# numbers of lists over different pool sizes, so a divergence is arguable). Do
# not reintroduce a literal at either call site: that is the state this block
# replaced.
#
# CHANGING ANY OF THESE CHANGES EVERY RANKING and therefore every downstream
# verdict. Nothing on disk is invalidated -- fusion happens at query time and
# writes no vectors -- but the twelve characterization fixtures would replay
# with a different Stage 2 pool, so they would have to be recaptured, which
# costs money. They are recorded in each fixture's environment "tunables" block
# for exactly that reason: a replay difference caused by editing one of these is
# reported as CONFIG MOVED SINCE CAPTURE rather than hunted as a refactor bug.
RRF_K = 60  # Rank-fusion constant, both stages (Cormack et al. 2009)

# Per-channel multipliers on the Stage 2 RRF contribution. Title and conditions
# are weighted higher because a disease-name match in those fields is the
# strongest relevance signal a trial record carries; criteria and the dense
# vector are the broader, noisier channels and stay at parity. These four are
# Stage 2 only -- Stage 3 fuses queries, not fields, and weights none of them.
RRF_WEIGHT_TITLE      = 2.0   # title-bm25       (disease query)
RRF_WEIGHT_CONDITIONS = 1.5   # conditions-bm25  (disease query)
RRF_WEIGHT_CRITERIA   = 1.0   # criteria-bm25    (full expanded query)
RRF_WEIGHT_DENSE      = 1.0   # dense vector     (full expanded query)

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
#
# EXPANSION_TEMPERATURE WAS HERE AND IS DELETED (pass 20f-2). It read
# `EXPANSION_TEMPERATURE = 0  # Deterministic query expansion (Stage 1 uses no
# LLM)`, and the comment was the whole finding: a temperature for a stage that
# issues no LLM call cannot have an effect, and its own line said so. Nothing in
# the repository read it -- checked by tests/test_package_invariants.py check
# 2h, which is what surfaced it once pass 20e deleted the shim whose
# re-export counted as a read.
#
# IT IS A DOCUMENTATION DEFECT RATHER THAN A LEFTOVER, which is why deleting it
# is the fix rather than a tidy-up. CLAUDE.md tells an operator that every
# tunable lives in this file; an operator who sets a value here is entitled to
# an effect, and this one silently had none. Compare MATCHING_TEMPERATURE
# immediately above, which is ALSO not sent to the API and is deliberately kept:
# it is recorded into every fixture's environment block, so its None is the
# honest record of a parameter a recorded run did not set. That is a reader.
# EXPANSION_TEMPERATURE had none, in any fixture, in any row, in any report.


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
#
# THIS ASSIGNMENT IS REWRITTEN AS TEXT BY "tests/test_config_snapshot_date_rot.py",
# which regex-matches `DATA_SNAPSHOT_DATE = "..."`, patches it to several
# dates, re-runs Files 38 and 39 as subprocesses at each, and restores this
# file byte-for-byte. Before item 20c it patched "03- Config.py", which is
# where the literal used to live. Keep the assignment on one line, with a
# double-quoted YYYY-MM-DD literal, or that test can no longer set it -- and it
# fails loudly rather than silently when it cannot.
DATA_SNAPSHOT_DATE = "2026-08-03"


# Clinical trials main characteristics for scraping
# Used at the RAG Trial Indexer
#--------------------------------------------------
trial_dict = {"condition": "neoplasms",
              "status": "RECRUITING",
              "study_type": "INTERVENTIONAL",
              "age": "ADULT",
              "max_trials": 25000}

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


# Stage 4 dynamic quality gate — TWO INDEPENDENT KNOBS
#-----------------------------------------------------
# A trial is kept only if it passes BOTH. They measure different things and
# each reports its own drop count (quality_dropped_percentile /
# quality_dropped_floor), so a later measurement can never confuse them.
#
# 1. RELATIVE. Percentile of the UNBOOSTED rerank score below which a trial is
#    dropped. Computed on rerank_score_raw, never on the MeSH-boosted score:
#    gating on the boosted score would make the gate a second, uncounted MeSH
#    filter.
QUALITY_THRESHOLD_PERCENTILE = 25

# 2. ABSOLUTE. Floor on the trial's best MedCPT cross-encoder score across the
#    rerank queries (`medcpt_score_max`, written by Stage 3).
#
# WHY IT IS NOT A FLOOR ON THE RERANK SCORE, which is what the deleted
# RERANK_SCORE_THRESHOLD = -10 was. That constant dated from when Stage 3
# reported a raw MedCPT score, which runs roughly -25 .. +10 — so -10 was a
# meaningful "weak relevance" line. Stage 3 has since moved to multi-query RRF
# fusion, and an RRF value is a function of POOL SIZE AND QUERY COUNT, not of
# quality: a trial ranked first by all three queries scores 3/(60+0) ~= 0.050
# however good or bad it is, and the whole fused range is about 0.01 .. 0.06.
# The gate took max(percentile, floor), so a floor of -10 could never be
# reached — not rarely, NEVER — and the relative percentile was doing 100% of
# the filtering. A patient whose four surviving trials are all excellent still
# lost one. An absolute gate has to read the calibrated per-query score, which
# is what medcpt_score_max retains.
#
# A trial whose medcpt_score_max is None is NOT dropped here: absence of a
# score (the skip_cross_encoder ablation, or a trial no query scored) is not a
# low score.
#
# PROVISIONAL, MEASURED, NOT TUNED.
#
# It is the 5th percentile of the observed medcpt_score_max distribution over
# 1,200 reranked trials from 30 patients -- 10 breast + 10 colon + 10 lung,
# classified by oncotriage/evaluation/sampling.py:classify_cancer over the
# primary condition registries/primary_cancer.py resolves, drawn with
# random.Random(42).sample from each filename-sorted group of the 1,000-bundle
# FHIR corpus. Run through Stages 1-3 only -- no rule filter, no billed Stage 5
# call -- against the live 14,324-trial index on 2026-08-07.
#
# OBSERVED, and the whole shape is recorded so a reader can disagree with the
# choice of percentile from the same numbers rather than from this sentence:
#
#              per patient   per distinct pool
#     p0        -12.1689         -12.1689
#     p1        -10.6643         -10.3281
#     p5         -8.4173          -8.4035     <- the floor
#     p10        -7.4909          -7.4909
#     p25        -4.6214          -4.4771
#     p50        -2.1574          -2.1574
#     p75        +2.1062          +2.1062
#     p95        +4.6252          +4.9323
#     p100      +13.9987         +13.9987
#     mean       -1.6813   std 4.3548
#
#     1,200 of 1,200 reranked trials carried a score; 0 came back None.
#     800 were scored by 3 rerank queries and 400 by 4.
#
# THE SECOND COLUMN IS NOT DECORATION. The 30 patients produce only 19 DISTINCT
# reranked pools -- 760 distinct trials counted 1,200 times -- because Synthea
# patients within one cancer type carry near-identical condition lists, so
# Stage 1 builds the same expanded query and Stage 2 retrieves the same trials.
# "1,200 trials" is a sample size this measurement does not have, and the
# per-patient column is weighted by how often each pool RECURS. Both are
# reported so neither can be mistaken for the other.
#
# WHICH ONE WINS IS A RULE, NOT A JUDGEMENT: the LOWER of the two, always. A
# floor set too low drops nothing, which is the state it replaced, so the cost
# is zero and the error is visible as floor_only == 0. Set too high it silently
# removes trials that would have been evaluated, and that loss appears in no
# counter and no stored row. Here the per-patient figure is lower, so it wins.
#
# IT WAS NOT ADJUSTED AFTERWARDS TO HIT A DROP COUNT. Measured impact on those
# same 30 pools, by RUNNING the gate rather than by arguing from the value:
# the relative percentile dropped 300 trials, the floor dropped 60, and the
# floor dropped 10 THAT THE PERCENTILE DID NOT -- 6 once duplicate pools are
# removed, across 10 of the 30 patients. Six distinct trials out of 760 is the
# honest size of this knob's effect on this sample, and it is recorded whether
# or not it flatters the change.
#
# STALE AS SOON AS ANY OF THREE THINGS MOVES: the indexed corpus, the rerank
# queries, or the cross-encoder checkpoint. Re-measure with
# `python measure_medcpt_scores.py`.
MEDCPT_SCORE_FLOOR = -8.4173


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


# ---------------------------------------------------------------------------
# Credentials and clients: LAZY, CACHED, and never built at import
# ---------------------------------------------------------------------------
#
# Everything in this block ran at module load in "03- Config.py". Item 20c made
# it lazy for three reasons, in order of how much they matter:
#
#   1. IMPORTING A CONFIG MODULE MUST NOT NEED CREDENTIALS. `keys =
#      load_env_keys()` raised FileNotFoundError on any machine without a .env,
#      so a file that wanted MAX_TRIALS_FOR_EVALUATION could not be loaded at
#      all on a checkout that had not been given keys yet.
#   2. IMPORTING MUST NOT OPEN A CLIENT. Neither constructor opens a socket
#      today, but that is a property of two third-party libraries, not a
#      promise either makes. A lazy factory does not depend on it.
#   3. IT MAKES THE IMPORT GRAPH TESTABLE. The proof that no package import
#      touches the network patches socket.socket to raise BEFORE importing;
#      that proof is only meaningful if there is nothing eager left to catch.
#
# Each factory caches in a module-level slot and returns the same object on
# every later call. "03- Config.py" calls them once at shim load and binds the
# eager names, so a caller going through the exec chain sees exactly what it
# saw before, and it is the SAME object the package hands out -- there is no
# second client.
#
# The caches are deliberately NOT resettable. A pipeline that swapped its
# OpenAI client halfway through a run would make every cost and latency figure
# in inferences.db ambiguous about which client produced it. Files 45 and 46
# redirect calls by rebinding the NAME in the exec namespace, which leaves this
# cache alone and is the seam that is meant to be used.

_KEYS_CACHE = None
_OPENAI_CLIENT_CACHE = None
_BEDROCK_CLIENT_CACHE = None
_BEDROCK_ANTHROPIC_CLIENT_CACHE = None
_QDRANT_CLIENT_CACHE = None
_SDK_DEFAULT_TIMEOUT_CACHE = None
_MATCHING_REQUEST_TIMEOUT_CACHE = None
_EMBEDDING_REQUEST_TIMEOUT_CACHE = None


def get_keys() -> dict:
    """The three credentials from the .env, read once and cached.

    Returns the dict ``load_env_keys()`` returns:
    ``{'openai': ..., 'qdrant_url': ..., 'qdrant_key': ...}``.

    Raises FileNotFoundError / ValueError from
    ``oncotriage.paths.load_env_keys`` — an absent or incomplete .env is a
    configuration error and is not recovered from here.
    """
    global _KEYS_CACHE
    if _KEYS_CACHE is None:
        _KEYS_CACHE = paths.load_env_keys()
    return _KEYS_CACHE


def get_openai_api_key() -> str:
    return get_keys()['openai']


# ---------------------------------------------------------------------------
# The Qdrant endpoint, and the one deliberate way to move it
# ---------------------------------------------------------------------------
#
# THE DEFECT THIS CLOSES, measured inside the running container on 2026-08-06
# rather than read off the source:
#
#     os.environ QDRANT_URL before load_env_keys -> http://qdrant:6333
#     config.get_qdrant_url()                    -> https://bd717e5f-....qdrant.io
#     os.environ QDRANT_URL after  load_env_keys -> https://bd717e5f-....qdrant.io
#
# `paths.load_env_keys()` POPS all three key names out of os.environ and reloads
# them from the .env with `override=True`, so NO environment variable and no
# compose setting could redirect Qdrant. docker-compose.yml had carried
# `QDRANT_URL: http://qdrant:6333` for exactly that purpose, and the `qdrant`
# service it named started, went healthy, held zero collections and was queried
# by nothing while /pipeline/info reported 12,067 trials from the cloud.
#
# THE POP IS NOT THE BUG AND IS NOT TOUCHED. It exists so a stale exported
# credential cannot shadow the credentials file -- the direction that quietly
# sends a live key to the wrong endpoint. `load_env_keys` is unchanged, and
# `get_keys()` still returns exactly what the .env says.
#
# What is added is a SECOND, deliberately-named tier that beats it. See
# settings.ENV_QDRANT_URL for why the accidental route stays closed while this
# one is open, and settings.ENV_QDRANT_API_KEY for why the key does not
# automatically follow the URL.
#
# RESOLVED ONCE PER PROCESS AND ANNOUNCED ONCE. The announcement is not
# decoration: with two possible endpoints and a client that reports neither, a
# run against the wrong index looks exactly like a run against the right one
# that retrieved badly. Every process that opens a Qdrant client now says which
# source answered, on one line, before the first request.

_QDRANT_SOURCE_ENV_FILE = "keys/.env"
"""``source`` reported when the .env decided the endpoint. Not an environment
variable name, which is why it is spelled as a path: it is what a reader has to
open to change the answer."""

_QDRANT_KEY_SOURCE_NONE = "none (URL overridden, no key named)"
"""``source`` reported when no key is sent at all. A distinct string rather than
None so the log line reads as a decision rather than a missing field."""

_QDRANT_ENDPOINT_CACHE = None


def _resolve_qdrant_endpoint():
    """(url, url_source, api_key, key_source). Resolved once, logged once.

    The three rows of settings.ENV_QDRANT_API_KEY's table, in order. Nothing
    here opens a socket: this is string resolution, and the client is built by
    ``get_qdrant_client()``.
    """
    global _QDRANT_ENDPOINT_CACHE
    if _QDRANT_ENDPOINT_CACHE is not None:
        return _QDRANT_ENDPOINT_CACHE

    override_url, url_source = settings.resolve_qdrant_url()

    if override_url is None:
        # Row 1: no override. `get_keys()` reads the .env -- and raises if it is
        # absent or incomplete, which is the pre-existing behaviour and the
        # right one: with no override in force, the .env is the only answer.
        keys = get_keys()
        resolved = (keys['qdrant_url'], _QDRANT_SOURCE_ENV_FILE,
                    keys['qdrant_key'], _QDRANT_SOURCE_ENV_FILE)
    else:
        override_key, key_source = settings.resolve_qdrant_api_key()
        if override_key is None:
            # Row 3. The .env is deliberately NOT consulted -- not even for the
            # key -- so that a container pointed at its own Qdrant never opens
            # the credentials file for this purpose and never forwards a cloud
            # credential to an environment-named host.
            resolved = (override_url, url_source, None, _QDRANT_KEY_SOURCE_NONE)
        else:
            # Row 2.
            resolved = (override_url, url_source, override_key, key_source)

    _QDRANT_ENDPOINT_CACHE = resolved
    console.out(f"[Qdrant] endpoint {resolved[0]} (from {resolved[1]}); "
          f"api key from {resolved[3]}")
    return resolved


def get_qdrant_url() -> str:
    """The Qdrant endpoint: ONCOTRIAGE_QDRANT_URL if set, else the .env."""
    return _resolve_qdrant_endpoint()[0]


def get_qdrant_api_key():
    """The Qdrant API key, or None when the URL was overridden without one.

    Returns None rather than raising, and ``QdrantClient(api_key=None)`` sends
    no auth header -- which is what a local Qdrant with no configured key wants.
    See settings.ENV_QDRANT_API_KEY for why this does not fall back to the .env.
    """
    return _resolve_qdrant_endpoint()[2]


def qdrant_endpoint_sources() -> dict:
    """Which source decided the endpoint and the key. Opens nothing.

    Exists so a report -- GET /pipeline/info, a bring-up log, a test -- can
    state where the client is pointed without reading the credential. The key
    itself is never returned by this function, only the name of what supplied
    it.
    """
    url, url_source, _key, key_source = _resolve_qdrant_endpoint()
    return {"url": url, "url_source": url_source, "api_key_source": key_source}


# How many times the OpenAI SDK may retry ONE request by itself.
#
# DEFINED HERE, ABOVE THE CLIENT, because it can only be applied at
# construction. It is logically part of the Stage 5 request budget documented
# beside MATCHING_REQUEST_TIMEOUT_SECONDS below, and it is here instead of
# there for two hard reasons:
#
#   1. the SDK has no per-request retry option. create() has no max_retries
#      parameter and no **kwargs, so passing it as a call kwarg raises
#      TypeError on every call;
#   2. openai_client.with_options(max_retries=...) IS the SDK's supported
#      per-call override, and using it would silently disable the fixture
#      harness. File 45 wraps this client in an OpenAIProxy whose __getattr__
#      forwards unknown attributes to the real inner client, so with_options()
#      returns an UNWRAPPED client: a capture would spend a real call and
#      record nothing, and a replay would hit the network instead of serving
#      its recording.
#
# So it is client-wide, and that means it also governs the embedding call in
# File 13. As of item 29d that is not merely tolerable, it is the ONLY retry
# either call has: get_embedding()'s tenacity decorator was removed and this is
# what replaced it. See the budget reconciliation below. The name says OPENAI
# rather than MATCHING for exactly that reason.
#
# WHY 1 RATHER THAN THE SDK's DEFAULT OF 2. See the budget reconciliation
# beside MATCHING_REQUEST_TIMEOUT_SECONDS: anything that fails twice in a row
# is not transient, and MAX_LLM_CLASSIFIER_RETRIES is the budget that should see it.
# Kept at 1 rather than 0 because a single transient blip is the common case
# and recovering it in-SDK is far cheaper than re-entering the node.
OPENAI_SDK_MAX_RETRIES = 1


# ---------------------------------------------------------------------------
# Request timeouts: STRUCTURED, not a bare number
# ---------------------------------------------------------------------------
#
# THE REGRESSION THIS FIXES. Item 29b bounded the Stage 5 call by passing
# timeout=300 -- a plain float. httpx does not treat that as "the read budget";
# it expands it into Timeout(connect=300, read=300, write=300, pool=300). The
# SDK's own default is Timeout(connect=5.0, read=600, write=600, pool=600), so
# the CONNECT phase went from 5 seconds to 300. An unreachable host took five
# minutes to fail instead of five seconds, which is worse than the behaviour
# before item 29b touched anything.
#
# It applied to BOTH calls, and to the client itself, because every one of them
# was handed a bare number. A per-request timeout REPLACES the client's Timeout
# object rather than merging with it, so fixing only the client would have left
# both call sites flat.
#
# READ FROM THE SDK, NOT TRANSCRIBED. The connect phase below is whatever the
# installed SDK ships, obtained by constructing a throwaway client and reading
# its resolved .timeout. Constructing a client opens no socket. Hard-coding 5.0
# would silently drift the day the SDK changes its default, which is the class
# of defect this project treats as a bug.
#
# ITEM 20c MADE THIS LAZY. It used to be a module-level statement:
#
#     _sdk_default_timeout = OpenAI(api_key=openai_api_key).timeout
#
# which needed the credentials at import purely to read a constant the SDK
# already knows. It now builds on first use, and everything derived from it --
# SDK_DEFAULT_CONNECT_TIMEOUT_SECONDS and the two structured Timeout objects --
# is lazy with it, because a value computed at import from a lazy source is not
# lazy at all.
#
# The api_key is still passed because the SDK refuses to construct without one.
# It is never sent anywhere: this client is discarded on the next line.

def get_sdk_default_timeout():
    """The installed OpenAI SDK's own resolved httpx.Timeout. Cached."""
    global _SDK_DEFAULT_TIMEOUT_CACHE
    if _SDK_DEFAULT_TIMEOUT_CACHE is None:
        _SDK_DEFAULT_TIMEOUT_CACHE = OpenAI(api_key=get_openai_api_key()).timeout
    return _SDK_DEFAULT_TIMEOUT_CACHE


def get_sdk_default_connect_timeout_seconds() -> float:
    """The SDK's default CONNECT phase, in seconds. This is the number item 29b
    destroyed by passing a bare float, and the reason _structured_timeout()
    exists."""
    return get_sdk_default_timeout().connect


def _structured_timeout(read_seconds: float) -> httpx.Timeout:
    """Build a four-phase httpx.Timeout around one measured read budget.

    ALL FOUR PHASES ARE SET EXPLICITLY. httpx refuses a partial spec --
    Timeout(connect=..., read=...) raises ValueError("must either include a
    default, or set all four parameters explicitly") -- so there is no way to
    name two phases and leave the others alone. The choice below is therefore
    deliberate rather than inherited:

      connect   the SDK's own default (5.0 at the time of writing, read at
                runtime above). This is the phase item 29b destroyed and the
                whole reason this function exists. A host that cannot be
                reached should fail in seconds; nothing about this pipeline
                justifies waiting longer to learn that.

      read      the measured budget -- the only phase that covers "the model is
                thinking", and the one every number in this file was derived
                from.

      write     set EQUAL to read. The request body is the Stage 5 prompt, on
                the order of 40 KB, which uploads in well under a second on any
                working link. No measurement of upload time exists, so rather
                than invent a tighter number, this matches read: it can never
                be the binding constraint on a request whose read budget is
                already generous, and it introduces no figure nobody chose.

      pool      set EQUAL to read. This is not a network phase at all -- it is
                how long to wait for a free connection from the local pool. The
                SDK ships Limits(max_connections=1000), read at runtime and far
                above this project's MAX_WORKERS of 12, so pool waits are
                structurally near zero and this phase should never fire. It is
                set generously on purpose: a tight pool timeout would convert a
                future increase in MAX_WORKERS into spurious request failures,
                which is a worse failure than waiting.

    HONEST NOTE ON WHAT "A 300 SECOND TIMEOUT" MEANS. httpx budgets each phase
    SEPARATELY, so one attempt's theoretical worst case is the SUM of the
    phases, not the read value: 5 + 300 + 300 + 300 = 905s here. That was true
    of the bare number too, and worse (4 x 300 = 1,200s). In practice read
    dominates and the others are microseconds. The per-patient arithmetic below
    uses the read budget, which is the honest practical figure; the phase sum is
    stated here so nobody mistakes it for a hard per-attempt bound.

    CALLING THIS RESOLVES THE CREDENTIALS, because the connect phase is read
    off a throwaway SDK client. It is therefore not import-safe and is not
    called at import.
    """
    return httpx.Timeout(
        connect=get_sdk_default_connect_timeout_seconds(),
        read=read_seconds,
        write=read_seconds,
        pool=read_seconds,
    )


# The two measured read budgets. THE VALUES LIVE HERE, ABOVE THE CLIENT,
# because the client construction below consumes one of them; the DERIVATION of
# each -- what was measured, over how many runs, and why the number is what it
# is -- stays in the Stage 5 request-shape section further down, which is where
# a reader looking for it will go. Same split, for the same reason, as
# OPENAI_SDK_MAX_RETRIES above.
MATCHING_REQUEST_TIMEOUT_SECONDS = 300   # derivation: Stage 5 section below
EMBEDDING_REQUEST_TIMEOUT_SECONDS = 30   # derivation: Stage 5 section below


def get_matching_request_timeout() -> httpx.Timeout:
    """The Stage 5 structured timeout. Cached; built on first use."""
    global _MATCHING_REQUEST_TIMEOUT_CACHE
    if _MATCHING_REQUEST_TIMEOUT_CACHE is None:
        _MATCHING_REQUEST_TIMEOUT_CACHE = _structured_timeout(MATCHING_REQUEST_TIMEOUT_SECONDS)
    return _MATCHING_REQUEST_TIMEOUT_CACHE


def get_embedding_request_timeout() -> httpx.Timeout:
    """The embedding structured timeout. Cached; built on first use."""
    global _EMBEDDING_REQUEST_TIMEOUT_CACHE
    if _EMBEDDING_REQUEST_TIMEOUT_CACHE is None:
        _EMBEDDING_REQUEST_TIMEOUT_CACHE = _structured_timeout(EMBEDDING_REQUEST_TIMEOUT_SECONDS)
    return _EMBEDDING_REQUEST_TIMEOUT_CACHE


def get_openai_client() -> OpenAI:
    """The one OpenAI client this process uses. Built on first call, cached.

    The client's own timeout is the STRUCTURED object, not a bare number, so
    that a call which does not pass its own timeout still gets a 5-second
    connect phase rather than a 300-second one. Both of File 13's OpenAI calls
    do pass their own -- a per-request timeout replaces this one outright
    rather than merging with it -- so this is the safety net for anything added
    later that forgets to.
    """
    global _OPENAI_CLIENT_CACHE
    if _OPENAI_CLIENT_CACHE is None:
        _OPENAI_CLIENT_CACHE = OpenAI(api_key=get_openai_api_key(),
                                      max_retries=OPENAI_SDK_MAX_RETRIES,
                                      timeout=get_matching_request_timeout())
    return _OPENAI_CLIENT_CACHE


def get_qdrant_client() -> QdrantClient:
    """The one Qdrant client this process uses. Built on first call, cached."""
    global _QDRANT_CLIENT_CACHE
    if _QDRANT_CLIENT_CACHE is None:
        _QDRANT_CLIENT_CACHE = QdrantClient(url=get_qdrant_url(),
                                            api_key=get_qdrant_api_key(),
                                            timeout=120)
    return _QDRANT_CLIENT_CACHE


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Configuration Constants (retry behavior) for LangGraph Agent
# ---------------------------------------------------------------------------

MAX_LLM_CLASSIFIER_RETRIES = 3
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


# ---------------------------------------------------------------------------
# WHICH PROVIDER SERVES STAGE 5
# ---------------------------------------------------------------------------
#
# THE DEFAULT IS "openai" AND FLIPPING IT IS THE ONLY THING THAT CHANGES ANY
# BEHAVIOUR IN THIS FILE. With MATCHING_PROVIDER == "openai" every accessor
# below is unreachable, no Bedrock client is constructed, no Bedrock module is
# imported for its side effects, and `call_matching_model` issues byte-for-byte
# the request it issued before the adapter existed. That is asserted three ways
# by tests/test_agent_bedrock_adapter.py -- structurally (the dispatch is one
# `if` above the unchanged return), behaviourally (the kwargs the OpenAI client
# is handed are compared field by field against a pinned expectation), and by
# the twelve characterization fixtures replaying clean without recapture.
#
# ONE FLAG, TWO NAMES, AND THE PROVENANCE COLUMN. `MATCHING_MODEL` above stays
# the PRICED and CONFIGURED identity of the judge; `BEDROCK_MATCHING_MODEL`
# below is the string that goes on the wire when the provider is Bedrock, and
# `matching_wire_model()` is the one function that answers "what will actually
# be sent". `inferences.matching_provider` records which of the two branches a
# row was produced by, so no stored row has to be dated to be interpreted.

MATCHING_PROVIDER_OPENAI = "openai"
MATCHING_PROVIDER_BEDROCK = "bedrock"
MATCHING_PROVIDER_BEDROCK_ANTHROPIC = "bedrock_anthropic"

MATCHING_PROVIDERS = (MATCHING_PROVIDER_OPENAI,
                      MATCHING_PROVIDER_BEDROCK,
                      MATCHING_PROVIDER_BEDROCK_ANTHROPIC)
"""The closed vocabulary. `deps.OVERRIDE_KEYS`' shape and for the same reason:
a provider name nobody recognises must raise rather than being read as "not
bedrock, so openai" -- a typo that silently keeps billing the incumbent while
an operator believes they have migrated is the failure this tuple prevents.

THREE MEMBERS, AND THE THIRD IS A THIRD PROVIDER RATHER THAN A MODE OF THE
SECOND. `bedrock` and `bedrock_anthropic` are both Amazon Bedrock and are
billed on one AWS account, which is exactly the argument for making them one
member with a sub-selector -- and it is the wrong axis. What differs between
them is the CLIENT LIBRARY (the OpenAI SDK against a base URL, versus boto3),
the credential chain, the request shape, the response shape, the error classes,
the degradation vocabulary and the model. A sub-selector would leave every
consumer that today asks "is this bedrock?" answering yes for a configuration
its code cannot serve -- `get_bedrock_client()` would build an OpenAI-SDK
client for a boto3 branch -- and it would introduce combinations
(`MATCHING_PROVIDER="openai"` with a Bedrock API selected) that are nonsense
and would each need their own refusal. A third member of one closed tuple keeps
the "a typo fails loudly" property with no new mechanism and no new state
space."""

# THERE IS DELIBERATELY NO `MATCHING_PROVIDERS_BEDROCK` SUBSET TUPLE. One was
# written and then removed: nothing in the pipeline asks "is this provider
# served by Bedrock" -- every consumer asks a sharper question ("is it THIS
# branch", "is it openai") and answers it against the members above. A tuple
# whose only reader was the test asserting it existed is the dead declaration
# `tests/test_package_invariants.py` check 2h exists to report, and the shape
# pass 20f-2 deleted BATCH_SIZE and EXPANSION_TEMPERATURE for. Write it when a
# second site needs it, not before.

MATCHING_PROVIDER = MATCHING_PROVIDER_OPENAI
"""Which provider Stage 5 calls. THE FLAG. Values: MATCHING_PROVIDERS."""


# --- The Bedrock endpoint ---------------------------------------------------
#
# TWO ENDPOINTS SERVE THE SAME OpenAI-COMPATIBLE APIs AND THIS PROJECT CANNOT
# YET KNOW WHICH ONE ITS QUOTA WILL LAND ON, which is why the endpoint is
# configuration rather than a constant folded into a URL. Measured against the
# live documentation on 2026-08-21:
#
#   bedrock-runtime  https://bedrock-runtime.{region}.amazonaws.com/openai/v1
#                    AWS's recommended endpoint. Responses + Chat Completions +
#                    Converse + InvokeModel + Anthropic Messages. Cross-Region
#                    inference profiles ONLY for the GPT-5.6 family -- in-Region
#                    inference is "Not supported" on this endpoint for Terra, so
#                    the model MUST be named `us.openai.gpt-5.6-terra` or
#                    `global.openai.gpt-5.6-terra`.
#                    (model-card-openai-gpt-56-terra.html, "Programmatic Access")
#
#   bedrock-mantle   https://bedrock-mantle.{region}.api.aws/openai/v1
#                    Responses + Chat Completions + Anthropic Messages. In-Region
#                    only; the model is the bare `openai.gpt-5.6-terra`.
#
# NOTE THE PATH, BECAUSE TWO AWS PAGES DISAGREE AND THE MODEL CARD IS THE ONE
# THAT IS RIGHT FOR THIS MODEL. `bedrock-mantle.html` gives the mantle base URL
# as `.../api.aws/v1`; the GPT-5.6 Terra model card carries an explicit
# footnote -- "On bedrock-mantle, this model is served at /openai/v1/responses,
# not the default /v1/responses" -- and its own Programmatic Access table gives
# `https://bedrock-mantle.{region}.api.aws/openai/v1`. The model card wins
# because it is the page about this model. If a mantle call 404s, this is the
# first line to look at.

BEDROCK_ENDPOINT_RUNTIME = "bedrock-runtime"
BEDROCK_ENDPOINT_MANTLE = "bedrock-mantle"

BEDROCK_BASE_URL_TEMPLATES = {
    BEDROCK_ENDPOINT_RUNTIME: "https://bedrock-runtime.{region}.amazonaws.com/openai/v1",
    BEDROCK_ENDPOINT_MANTLE: "https://bedrock-mantle.{region}.api.aws/openai/v1",
}
"""Base URL per endpoint. The keys ARE the closed vocabulary of
BEDROCK_ENDPOINT -- one declaration rather than a tuple beside a dict that can
disagree with it."""

BEDROCK_ENDPOINT = BEDROCK_ENDPOINT_RUNTIME
"""Which of the two endpoints to call. Values: BEDROCK_BASE_URL_TEMPLATES keys."""

BEDROCK_REGION_DEFAULT = "us-east-1"
"""The AWS Region in the base URL, before ONCOTRIAGE_BEDROCK_REGION.

THE DEFAULT LIVES HERE AND THE OVERRIDE LIVES IN THE ENVIRONMENT, which is the
split the hardcoding audit asked for: a Region is deployment-varying -- Bedrock
quota is granted per Region, and an operator whose grant is not in us-east-1
had to edit this tracked file before the judge could answer a single request.
A tracked file edited for one machine is a tracked file committed for every
machine.

NOT gated by the resume fingerprint -- see the note at `matching_wire_model()`,
which now records that the follow-up is one export away from being triggered by
accident rather than by a source edit."""

BEDROCK_REGION, BEDROCK_REGION_SOURCE = settings.resolve_bedrock_region(
    BEDROCK_REGION_DEFAULT)
# The resolved Region and WHERE IT CAME FROM: the variable name, or None when
# BEDROCK_REGION_DEFAULT applied. `validate_matching_provider_config()` renders
# the source, so a refusal about a Region says whether the operator has an
# export to fix or a constant to edit -- two different remedies that an
# un-sourced value sends to the same page.
#
# RESOLVED AT IMPORT, NOT AT CALL TIME, and both halves of that are deliberate.
# It is a module ATTRIBUTE because that is the seam every existing test uses:
# tests/test_agent_bedrock_adapter.py's `provider()` context manager sets
# `config.BEDROCK_REGION = ""` to drive the validator's empty-Region arm, and an
# accessor function would take that seam away for a value nothing resolves
# lazily anyway. Reading os.environ at import opens no client, loads no model,
# touches no database and reads no file, so section 2 of
# tests/test_package_invariants.py is unaffected -- verified by running it, not
# by reading. The cost is that the override is process-global and is therefore
# driven by SUBPROCESS in the standing test, on
# tests/test_docker_qdrant_override_and_readiness.py's precedent for the Qdrant
# override, which is the same shape for the same reason.

BEDROCK_RUNTIME_PROFILE_PREFIXES = ("us.", "global.", "in.", "us-gov.")
"""Cross-Region inference profile prefixes accepted on `bedrock-runtime`.

A GPT-5.6 model named on that endpoint WITHOUT one of these is rejected: the
model card's Programmatic Access table reads "Not supported" in the In-Region
column for bedrock-runtime, and its note reads "This model is not available for
in-Region inference on that endpoint." Checked locally by
`validate_matching_provider_config()` so the failure names the constant to edit
rather than arriving as a 400 from a request that has already been signed."""

BEDROCK_MATCHING_MODEL = "us.openai.gpt-5.6-terra"
"""The model id SENT when MATCHING_PROVIDER is "bedrock".

`us.` is the geographic profile: it routes within the US geography, which is a
data-residency property rather than a performance one. `global.` routes to any
commercial Region and is ~10% cheaper per token (model card, Pricing). Both are
priced in PRICING_CONFIG; whichever is set here is the key that will be looked
up, because `inferences.matching_model` records the model that ANSWERED."""


# --- Request-shape knobs that exist only for Bedrock ------------------------
#
# EVERY ONE OF THESE IS A ONE-LINE GO-LIVE EDIT, and that is why each is a
# constant rather than a literal in the adapter. The Responses API translation
# is built from documentation that no call has yet confirmed; the adapter's
# VERIFY-AT-GO-LIVE list names the probe check behind each of them.

BEDROCK_SYSTEM_ROLES = ("developer", "system")
BEDROCK_SYSTEM_ROLE = "developer"
"""Which role carries the Stage 5 system prompt in the Responses `input` array.

THE CHAT CALL SENDS "system" AND THIS SENDS "developer", which is a real
difference and is chosen rather than inherited. AWS's own GPT-5.6 Responses
examples use `"role": "developer"` (prompt-caching.html, and the explicit
prompt-caching blog post), and for the GPT-5 family `developer` is the
successor of `system`. Set this to "system" if the probe shows the two are not
equivalent for this prompt; it is one edit, which is the whole reason it is
here."""

BEDROCK_SERVICE_TIERS_ALLOWED = (None, "default")
BEDROCK_SERVICE_TIER = None
"""Service tier. None means OMIT the field, which is Standard.

TERRA SUPPORTS STANDARD ONLY. The model card's Service Tiers table marks
Priority, Flex and Reserved "not supported", and its pricing note reads
"Pricing shown is for the Standard tier. Priority and Flex tiers are not
supported for this model." So "priority" and "flex" are refused locally by
`validate_matching_provider_config()` rather than sent and 400'd."""

BEDROCK_STORE = False
"""`store` on the Responses request. FALSE, and the default it overrides is TRUE.

THIS IS A DATA-RETENTION DECISION AND IT IS NOT THE VENDOR DEFAULT. AWS:
"When `store` is `true` (the default), Amazon Bedrock retains the response,
including the input and output, for 30 days." The Stage 5 input is a rendered
patient record -- conditions, medications, labs, stage, ECOG. Retaining that
server-side for 30 days is a decision nobody in this project has made, and a
default is not a decision. `store=False` costs nothing here: the only feature
it disables is `previous_response_id` multi-turn chaining, and Stage 5 is
single-turn by construction.

Set it True only with an explicit retention argument written beside it."""

BEDROCK_PROMPT_CACHE_KEY = None
"""Optional `prompt_cache_key`. None means omit.

CACHING IS DELIBERATELY LEFT AT THE VENDOR DEFAULT (implicit) FOR THE FIRST
RUN. Implicit mode places an automatic breakpoint and costs nothing extra;
explicit mode DISABLES the automatic breakpoint, so a mistake there means a
run with no caching at all and a 90% discount silently not taken. The Stage 5
prefix is already stable by construction (the packer's whole claim is that N
requests share one prefix), so implicit mode should hit it. Measure with the
probe, then turn on explicit mode if the numbers say so."""

BEDROCK_PROMPT_CACHE_MODES = (None, "implicit", "explicit")
BEDROCK_PROMPT_CACHE_MODE = None
"""`prompt_cache_options.mode`. None means omit the whole object.

Sent through `extra_body`, because the installed OpenAI SDK has no such
parameter -- AWS's own example does the same."""

BEDROCK_SEND_SEED_IN_EXTRA_BODY = False
"""Whether to smuggle MATCHING_SEED into the Responses request via extra_body.

FALSE, AND THE FLAG EXISTS SO THE ANSWER IS ONE EDIT RATHER THAN A GUESS. The
Responses API has no `seed` parameter: it is absent from the installed OpenAI
SDK's `responses.create` signature (openai 1.99.9, measured) while present on
`chat.completions.create`. Sending it anyway through extra_body is a bet that
Bedrock ignores an unknown field rather than rejecting the request with a 400 --
and a 400 here fails EVERY Stage 5 call of the run. So the default is to drop
it, RECORD the drop (bedrock_adapter.BEDROCK_ADAPTER_DEGRADATIONS carries
`seed_not_expressible`, which reaches the run-end degradation report), and let
the go-live probe settle it."""


# --- The Bedrock Anthropic branch: Claude Sonnet 4.6 over Converse ---------
#
# A SECOND BEDROCK BRANCH WITH ITS OWN KNOBS, and none of the BEDROCK_* names
# above reach it. That is deliberate rather than duplicative: every one of them
# describes the OpenAI-compatible Responses API (a base URL, a system ROLE, a
# `store` flag, an extra_body seed escape hatch), and Converse has none of
# those concepts. Sharing a constant between the two would mean one edit
# changing two request shapes in ways nobody could reason about.
#
# WHY CONVERSE AND NOT THE ANTHROPIC MESSAGES API: the Claude Sonnet 4.6 model
# card (read 2026-08-30) marks the `bedrock-mantle` endpoint NOT SUPPORTED for
# this model, and on `bedrock-runtime` marks Messages, Responses and Chat
# Completions all NOT SUPPORTED, leaving Converse and Invoke. Independently,
# `structured-output.html` marks the Messages API "No" for structured outputs
# with a 400. The full argument, and why Converse beat Invoke, is at the top of
# `oncotriage/agent/bedrock_anthropic_adapter.py`.
#
# THERE IS NO BASE-URL TABLE HERE, and its absence is the point: boto3 resolves
# the `bedrock-runtime` endpoint for a Region itself. The Responses branch
# needs BEDROCK_BASE_URL_TEMPLATES because the OpenAI SDK must be told a URL;
# this one would be inventing a string the AWS SDK already knows.

BEDROCK_ANTHROPIC_PROFILE_PREFIXES = ("us.", "eu.", "au.", "jp.", "global.")
"""Inference-profile prefixes the Claude Sonnet 4.6 model card publishes.

A SEPARATE TUPLE FROM `BEDROCK_RUNTIME_PROFILE_PREFIXES`, and the two genuinely
differ: that one is GPT-5.6 Terra's ("us.", "global.", "in.", "us-gov."), read
off Terra's own card, and this one is Sonnet 4.6's -- whose Programmatic Access
table publishes `us.` / `eu.` / `au.` / `jp.` geo ids and one `global.` id and
no others. Merging them into a union would let each model be named with the
other's prefix and refused by the service instead of by this file."""

BEDROCK_ANTHROPIC_MATCHING_MODEL = "us.anthropic.claude-sonnet-4-6"
"""The model id SENT when MATCHING_PROVIDER is "bedrock_anthropic".

`us.` IS THE GEOGRAPHIC PROFILE AND IS CHOSEN OVER THE ~10%-CHEAPER `global.`
FOR THE REASON `BEDROCK_MATCHING_MODEL` ALREADY RECORDS: it routes within the
US geography, which is a data-residency property rather than a performance one,
and this project has already made that trade once. Nothing here silently flips
a residency decision to make a pricing table cleaner.

THE BARE `anthropic.claude-sonnet-4-6` IS NOT USABLE ON MOST REGIONS, WHICH IS
WHY THE VALIDATOR REFUSES IT. The model card's regional table marks In-Region
inference "not supported" in us-east-1 -- this project's default Region -- and
in every US, APAC, Middle East and African Region it lists; the ONLY Region
where In-Region is supported is eu-west-2 (London). So on the shipped
configuration the bare id is a 400 waiting to happen.

PRICING: whichever value is set here is the key `get_model_cost()` looks up,
because `inferences.matching_model` records the model that answered. See
PRICING_CONFIG, and read A6 in the adapter's VERIFY-AT-GO-LIVE list before
trusting the geo rows -- they are INFERRED, not measured."""

BEDROCK_ANTHROPIC_CONNECT_TIMEOUT_SECONDS = 5.0
"""Connect-phase budget for the boto3 client, in seconds.

FIVE RATHER THAN botocore's OWN DEFAULT OF 60, and rather than reading the
OpenAI SDK's default the way `_structured_timeout()` does. The argument is
`_structured_timeout`'s, restated for a different SDK: "a host that cannot be
reached should fail in seconds; nothing about this pipeline justifies waiting
longer to learn that". It is written as a number here rather than borrowed from
`get_sdk_default_connect_timeout_seconds()` because that function builds a
throwaway OpenAI client to read it -- which resolves OpenAI credentials, on a
branch that has nothing to do with OpenAI."""

BEDROCK_ANTHROPIC_CACHE_TTLS = (None, "5m", "1h")
BEDROCK_ANTHROPIC_CACHE_TTL = "5m"
"""`cachePoint.ttl` on the system breakpoint. None omits the breakpoint entirely.

5m RATHER THAN 1h, ON THE VENDOR'S OWN ADVICE. `prompt-caching.html`: "If you
have prompts that are used at a regular cadence (i.e., system prompts that are
used more frequently than every 5 minutes), continue to use the 5-minute cache,
since this will continue to be refreshed at no additional charge." A per-trial
wave issues all of its calls at once behind one warmup, so 5m covers it, and 1h
pays a higher write rate ($6.00/1M against $3.75/1M, AWS Marketplace, read
2026-08-30) for headroom nothing uses.

THE MINIMUM PREFIX IS 1,024 TOKENS for this model (prompt-caching.html's
explicit-caching table). A checkpoint placed before that is not an error --
"your inference still succeeds, but your prefix isn't cached" -- which is
exactly the silent no-op A2 exists to catch.

None IS A REAL OPTION rather than a placeholder: implicit caching is on for
Anthropic models on Bedrock whatever this says, so omitting the breakpoint
falls back to best-effort prefix reuse rather than to no caching at all."""

BEDROCK_ANTHROPIC_THINKING_MODES = (None, "disabled", "adaptive")
BEDROCK_ANTHROPIC_THINKING = "disabled"
"""`thinking.type`, sent through `additionalModelRequestFields`. None omits it.

THIS IS WHERE MATCHING_REASONING_EFFORT GOES TO DIE, AND THE SUBSTITUTION IS
DECLARED HERE RATHER THAN COMPUTED. The two vocabularies do not overlap:
OpenAI's is none|minimal|low|medium|high and this project is calibrated at
'none'; Anthropic's controls are `thinking` (adaptive or disabled) and `effort`
(low|medium|high|max). 'none' is a member of neither, so it is dropped and
counted (see the adapter) and this constant states what is sent instead.

"disabled" is the honest translation of "spend no tokens on reasoning" and is
accepted by Sonnet 4.6. It is NOT a claim that the two produce the same
verdicts: config's note on MATCHING_REASONING_EFFORT records a measured 69.1%
agreement behind the 'none' choice ON ANOTHER MODEL, and nothing carries that
across. Read A4 before a campaign."""

BEDROCK_ANTHROPIC_EFFORTS = (None, "low", "medium", "high", "max")
BEDROCK_ANTHROPIC_EFFORT = None
"""`outputConfig.effort`. None OMITS the field, which is the model's default.

None BY DEFAULT because effort governs how deeply the model thinks and
BEDROCK_ANTHROPIC_THINKING ships "disabled" -- so a value here would be a knob
turned on a mechanism that is off. Set them together or not at all."""

BEDROCK_ANTHROPIC_SERVICE_TIERS_ALLOWED = (None, "default")
BEDROCK_ANTHROPIC_SERVICE_TIER = None
"""`serviceTier.type`. None means OMIT the field, which is Standard.

SONNET 4.6 SUPPORTS STANDARD AND RESERVED ONLY. Its model card's Service Tiers
table marks Priority and Flex not supported, and says Reserved "is set at the
account level rather than per request (contact your AWS account team to
enable)". So the only two values that can be correct in a REQUEST are omission
and "default", and boto3's own enum members `priority` / `flex` / `reserved`
are refused here rather than sent and 400'd -- or, worse for `reserved`,
accepted as a per-request value for something that is not one."""

BEDROCK_ANTHROPIC_REQUEST_MODEL_ECHO = True
"""Whether to ask Converse for the underlying model's `model` field.

TRUE, AND IT COSTS NOTHING TO ASK. Converse's response carries no `model`, so
`MatchingModelMismatchError` -- the check that says WHICH judge answered --
has nothing to compare on this branch. `additionalModelResponseFieldPaths`
lifts a named field out of the underlying model response, and the API reference
says a valid pointer naming a field the model response does not carry "is
ignored by Converse". So this is free if unsupported and a genuine attestation
if it works. Whether it works is the adapter's VERIFY-AT-GO-LIVE (A3).

A KNOB RATHER THAN A LITERAL because the reference also says an INVALID pointer
is a 400: if `/model` is ever refused outright, one edit turns it off rather
than a source change under time pressure."""

BEDROCK_ANTHROPIC_SCHEMA_DESCRIPTION = None
"""Optional `outputConfig.textFormat.structure.jsonSchema.description`.

None OMITS IT, and that is the shipped choice. The field is documented as
optional and nothing in Stage 5 has ever sent a schema description; adding one
would put text in front of the judge that the prompt did not put there, which
is a prompt change wearing the costume of a plumbing field. It exists as a knob
only so that a probe finding the field REQUIRED is one edit rather than a
source change."""

# ---------------------------------------------------------------------------
# Per-trial mode on the Converse branch
# ---------------------------------------------------------------------------
#
# ALL FOUR DEFAULT TO "WHAT THE SHIPPED PATH ALREADY DOES", so importing this
# file changes nothing for anybody until an operator sets one.

BEDROCK_ANTHROPIC_WARMUP_SEND_OUTPUT_CONFIG = True
"""Whether the per-trial cache warmup carries `outputConfig`.

**TRUE, AND IT WAS FALSE UNTIL A LIVE PROBE REFUTED THE ARGUMENT FOR FALSE.**
That argument is kept here verbatim, because a reader is entitled to see what
was believed and why it was wrong: "Converse's cache checkpoints are processed
`tools` -> `system` -> `messages` (`prompt-caching.html`, read 2026-08-30), and
`outputConfig` is in NONE of those three -- so dropping the structured-output
block from the warmup cannot change the prefix the warmup writes."

**THE CITATION IS ACCURATE AND THE INFERENCE FROM IT IS FALSE.** Measured
2026-09-01 with `bedrock_probe.py --provider bedrock_anthropic
--probe-per-trial --per-trial-prefix-file <a real rendered Stage 5 prompt>`
against `us.anthropic.claude-sonnet-4-6`, with the warmup and the trial calls
carrying a system block asserted byte-identical:

    warmup WITHOUT outputConfig (the old default)
        warmup   cacheWrite = 11,749   cacheRead = 0
        trial 1  cacheWrite = 12,416   cacheRead = 0       <- wrote AGAIN
        trial 2  cacheWrite = 0        cacheRead = 12,416

    warmup WITH outputConfig (this default), on a second, unseen prefix
        warmup   cacheWrite = 11,328   cacheRead = 0
        trial 1  cacheWrite = 0        cacheRead = 11,328  <- reads the warmup
        trial 2  cacheWrite = 0        cacheRead = 11,328

So the structured-output block DOES take part in the cached prefix -- almost
certainly as a `tools` entry, which is FIRST in the very ordering the old
argument quoted. The two shapes cache separately, and under the old default the
warmup warmed a prefix no trial call ever used.

**WHAT IT COST WAS MONEY, NOT CORRECTNESS.** Nothing raised and no verdict
changed: `classify_cache_write` saw the warmup's write, answered `wrote` and
released the wave; trial 1 then paid a full cache WRITE instead of a read and
was counted in `PER_TRIAL_CACHE_READ_MISSES`; trials 2..N read normally. Per
15-trial patient that is one wasted warmup write plus one write-instead-of-read
-- about $0.05 at the documented cache dimensions, or roughly $54 per
1,000-patient campaign, invisible except in a counter nobody had read yet.

**THE OLD DEFAULT'S OTHER ARGUMENT SURVIVES AND IS NOW A MEASUREMENT.** It ran:
"every constraint the warmup drops is one fewer reason for a provider to refuse
its request SHAPE ... a json_schema demand against `maxTokens = 1` is a
plausible thing to be refused." Plausible, and it does not happen: the warmup
above carried the full Stage 5 schema at `maxTokens = 1`, was ACCEPTED, and
returned `stopReason "max_tokens"` in 1.5s. (A11) is confirmed for BOTH warmup
shapes.

**AND THE COMPILE COST IT WORRIED ABOUT POINTS THE OTHER WAY.**
`structured-output.html` warns that a first-time schema "compiles the grammar,
which may take up to a few minutes". With the warmup carrying the schema the
WARMUP pays that compile once, alone and awaited, instead of N wave requests
meeting it at once. Measured first-call latency with the schema was 6.6s against
a 300s read budget, so at this schema's size the compile is not the multi-minute
event the page allows for -- and carrying it in the warmup is the safe side of
that either way.

IT REMAINS A KNOB. Set it False to reproduce the old behaviour; the split prefix
is the only thing that changes, and `PER_TRIAL_CACHE_READ_MISSES` is where it
shows up."""

BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS = None
"""Per-trial parallelism for THIS provider, or None to follow the shared bound.

WHY A SECOND NAME FOR A NUMBER THAT ALREADY HAS ONE.
`MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS` is derived from an estimated OpenAI
single-trial latency and is bounded, in the end, by an OpenAI account's
requests-per-minute allowance. Neither fact is a fact about an AWS account. A
Bedrock account has its own Amazon Bedrock service quotas, per Region and per
model, and this project has one whose requests-per-minute allowance is
currently applied far below the default with an increase requested and pending.
One number cannot be right for both, and the alternative -- editing the shared
constant -- would silently re-pace the SHIPPED OpenAI arm to suit a provider it
does not use.

None MEANS FOLLOW THE SHARED BOUND, so nothing moves until this is set, and
`config.per_trial_parallel_bound()` is the ONE place the two are reconciled.

WHAT HAPPENS WHEN THE QUOTA IS HIT, AND IT IS NOT SILENT. Converse answers
`ThrottlingException` with HTTP 429 (`API_runtime_Converse.html`, read
2026-08-30). botocore's `standard` retry mode classifies that as a THROTTLING
error and retries it with a 1,000 ms base delay, exponential backoff and full
jitter, capped at 20 s, honouring any `x-amz-retry-after` header
(`feature-retry-behavior.html`, read 2026-08-30) -- so a burst that clips the
limit degrades to a slower campaign rather than to failed patients, up to
`bedrock_anthropic_max_attempts()` TOTAL attempts. Past that the exception
reaches the node: on a TRIAL call the trial is recorded `per_trial_call_failed`
and the patient completes without it; on the WARMUP the patient fails cleanly
and the batch checkpoint resumes it, which is cache-or-nothing working.

AND THERE IS A SECOND, LESS OBVIOUS FLOOR. Standard mode also carries a retry
QUOTA -- a 500-token bucket charged 5 tokens per throttling retry, refunded on
success -- and "when the available tokens are exhausted, the SDK returns the
error without retrying". Sustained throttling above roughly 32% of requests
drains it, and at that point retries stop entirely and patients start failing
fast. On a quota-restricted account that is the mechanism to expect, and the
remedy is a SMALLER value here rather than a larger retry budget.

SET IT TO 1 FOR SEQUENTIAL, which is the honest way to turn the scheduling off
without turning the mode off. 0 or a negative value is refused at import."""

BEDROCK_ANTHROPIC_MAX_ATTEMPTS = None
"""botocore's TOTAL attempt budget, or None to follow the OpenAI derivation.

None RESOLVES TO `OPENAI_SDK_MAX_RETRIES + 1`, which is what shipped and which
is 2 -- one initial request and ONE retry. The +1 is not arithmetic decoration:
botocore counts TOTAL attempts where the OpenAI SDK counts retries after the
first, and passing one library's number to the other is how a transport budget
silently halves or doubles.

WHY IT IS SEPARABLE HERE AND NOWHERE ELSE. Two on a healthy endpoint is
generous. On an account whose requests-per-minute allowance is applied far
below the default it is one retry, with at most one second of jittered backoff,
against a limit that is being hit systematically -- and the retry quota above
means raising this cannot compensate for a parallel bound set too wide. Raise
it when the probe's throttling behaviour says the 429s are BURSTY (a bigger
budget rides them out) and lower the parallel bound when they are SUSTAINED.

MUST BE >= 1: botocore's own documentation is that "a max attempts value of 3
means the SDK makes one initial request and up to two retries. Set max attempts
to 1 to disable retries entirely"."""

BEDROCK_ANTHROPIC_RETRY_MODES = ("standard", "adaptive", "legacy")
BEDROCK_ANTHROPIC_RETRY_MODE = "standard"
"""botocore's retry mode for the Converse client.

"standard" IS WHAT SHIPPED, promoted from a literal in the client builder
because a value that decides how a throttled campaign behaves is a tunable and
this file's rule is that tunables live here.

"adaptive" IS THE DOCUMENTED ANSWER FOR A THROTTLED ACCOUNT AND IS NOT THE
DEFAULT. AWS describes exactly this workload -- "your client targets a single
resource ... and you expect frequent throttling responses. This is common in
automated workflows, batch processors, or AI workloads that call a single API
operation at high volume" -- and adaptive mode adds a client-side rate limiter
that slows the run down BEFORE the service rejects it. It is not the default
for the reason AWS gives on the same page: adaptive mode "can delay or block
the INITIAL request", so a campaign's wall time becomes a function of a
limiter's internal state rather than of this pipeline's own arithmetic, and
"adaptive mode is not recommended as a general default". Switch to it
deliberately, after the probe, and expect the campaign to take longer.

"legacy" is offered only because botocore offers it; AWS documents it as
backward-compatibility only and its retryable error set is not standardized.

ONE THING NEITHER MODE SETTLES, AND IT IS NAMED RATHER THAN ASSUMED: AWS's
retry-behavior page opens by saying the behaviour it documents "requires opting
in until it becomes the default behavior. Set `AWS_NEW_RETRIES_2026=true` in
your environment. Without this setting, your SDK uses pre-2026 retry behavior,
which differs in backoff timing, retry quota costs, and service-specific
defaults." This project does not set that variable, so the numbers quoted at
`BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS` describe the OPTED-IN behaviour and the
installed botocore may be doing something slightly different. It is an
environment decision rather than a code one, which is why it is recorded here
instead of being set on anyone's behalf."""


def per_trial_parallel_bound():
    """How many Stage 5 trial calls may be in flight for one patient. ONE OWNER.

    Resolves the provider override, then the shared bound. A FUNCTION rather
    than a constant on `matching_call_mode()`'s footing: the values it reads can
    move WITHIN a process -- a probe sets one, a test sets the other -- and a
    consumer that read a module constant through a from-import would move
    nothing.

    THE PROVIDER GATE IS INSIDE, NOT AT THE CALL SITE, which is what makes the
    override impossible to forget. `oncotriage/agent/evaluation.py` calls this
    once and gets the right number for whichever provider is configured.

    Returns:
        int: >= 1. Validated at import for both constants, so this cannot
        return a value `ThreadPoolExecutor` would refuse.
    """
    if (MATCHING_PROVIDER == MATCHING_PROVIDER_BEDROCK_ANTHROPIC
            and BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS is not None):
        return BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS
    return MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS


def bedrock_anthropic_max_attempts():
    """botocore's `retries.max_attempts` for the Converse client. ONE OWNER.

    The override, or the OpenAI derivation. See both constants; the +1 is the
    retries-versus-attempts conversion and is the whole reason this is a
    function rather than a subtraction repeated at the client builder.
    """
    if BEDROCK_ANTHROPIC_MAX_ATTEMPTS is not None:
        return BEDROCK_ANTHROPIC_MAX_ATTEMPTS
    return OPENAI_SDK_MAX_RETRIES + 1



def _validate_bedrock_region(interpolated_into):
    """Refuse a Region that cannot produce a working endpoint. One owner.

    TWO CALLERS, TWO CONSEQUENCES, ONE CHECK. The Responses branch interpolates
    the Region into `BEDROCK_BASE_URL_TEMPLATES` by hand; the Converse branch
    hands it to `boto3.client(region_name=...)` and botocore builds the
    endpoint. The FAILURE is the same in both -- a hostname with a hole in it
    -- and only the sentence naming where it lands differs, which is why that
    clause is the argument and the rest is shared. Two copies of this would be
    two places to forget the whitespace guard.

    THE REGION IS VALIDATED HERE RATHER THAN AT RESOLUTION, and that is where
    the settings resolver's "it never raises" argument is discharged. This
    check is LAZY (top of every Bedrock request) and PROVIDER-GATED (nothing
    reaches it on an OpenAI run), so a malformed Region costs the operator one
    refusal naming the two places it could have come from instead of making
    `import oncotriage.config` fail for every process in the project.

    THE MESSAGE NAMES THE SOURCE because the remedies differ: an exported
    ONCOTRIAGE_BEDROCK_REGION is unset with `unset`, and a wrong
    BEDROCK_REGION_DEFAULT is a source edit. An un-sourced value sends both to
    the same page, and the export is the one that is invisible in a diff.

    IT DOES NOT CHECK THAT THE REGION EXISTS. Nothing here can, without a
    network call this module does not make. A well-formed wrong Region still
    arrives as a 4xx from the endpoint, which names the host.

    Raises:
        RuntimeError: naming BEDROCK_REGION, the offending value and the source.
    """
    _region_origin = (f"{BEDROCK_REGION_SOURCE} (an environment variable)"
                      if BEDROCK_REGION_SOURCE
                      else "BEDROCK_REGION_DEFAULT in oncotriage/config.py")

    if not BEDROCK_REGION or not str(BEDROCK_REGION).strip():
        raise RuntimeError(
            f"BEDROCK_REGION is empty. It reaches {interpolated_into} and an "
            f"empty value produces an endpoint that resolves nowhere. It was "
            f"resolved from {_region_origin}.")

    # WHITESPACE OR A SLASH INSIDE THE VALUE, and this guard exists because the
    # override made a new failure reachable. A Region lands inside a HOSTNAME,
    # so either character produces a URL whose failure names neither the
    # character nor the variable -- which is precisely the corruption
    # `_from_env`'s trailing separator would have caused and the reason the
    # resolver declines that helper. Refusing one shape of it while tolerating
    # the other two would be inconsistent.
    if any(c.isspace() for c in str(BEDROCK_REGION)) or "/" in str(BEDROCK_REGION):
        raise RuntimeError(
            f"BEDROCK_REGION is {BEDROCK_REGION!r}, which carries whitespace "
            f"or a '/'. It lands inside a HOSTNAME by way of "
            f"{interpolated_into}, so the resulting failure names neither. It "
            f"was resolved from {_region_origin}.")


def _validate_bedrock_anthropic_config():
    """Refuse a Converse-branch configuration that cannot work.

    Split out of `validate_matching_provider_config()` rather than inlined as a
    fourth arm of one long function, because the two Bedrock branches share
    exactly one check -- the Region -- and interleaving the rest would produce
    a function in which every `if` had to be read twice to find out which
    provider it belonged to.

    Raises:
        RuntimeError: naming the constant to edit and the documented rule it
            violates, on this file's standing footing.
    """
    _validate_bedrock_region("boto3.client(region_name=...)")

    if not BEDROCK_ANTHROPIC_MATCHING_MODEL or not str(
            BEDROCK_ANTHROPIC_MATCHING_MODEL).strip():
        raise RuntimeError(
            "BEDROCK_ANTHROPIC_MATCHING_MODEL is empty. Edit it in "
            "oncotriage/config.py.")

    if not any(BEDROCK_ANTHROPIC_MATCHING_MODEL.startswith(p)
               for p in BEDROCK_ANTHROPIC_PROFILE_PREFIXES):
        raise RuntimeError(
            f"BEDROCK_ANTHROPIC_MATCHING_MODEL is "
            f"{BEDROCK_ANTHROPIC_MATCHING_MODEL!r}, which names no inference "
            f"profile. Claude Sonnet 4.6's model card marks In-Region "
            f"inference NOT SUPPORTED in every Region this project is likely "
            f"to run in -- including us-east-1, the default -- so a bare model "
            f"id would be refused by the service. Prefix it with one of "
            f"{', '.join(BEDROCK_ANTHROPIC_PROFILE_PREFIXES)} (for example "
            f"'us.{BEDROCK_ANTHROPIC_MATCHING_MODEL}'). The one Region where "
            f"the bare id is correct is eu-west-2; if that is genuinely where "
            f"this runs, widen BEDROCK_ANTHROPIC_PROFILE_PREFIXES with the "
            f"measurement written beside it.")

    # AN UNPRICED MODEL IS REFUSED HERE RATHER THAN AFTER THE CALL, and this
    # check exists because the standing test found the hole rather than because
    # anybody predicted it: BEDROCK_ANTHROPIC_PROFILE_PREFIXES is read off the
    # model card and has five members, and a pricing table missing one of them
    # would have let a `jp.`-prefixed configuration pass validation, issue a
    # live billed Stage 5 call, and only then raise UnknownModelPricingError
    # from inside the writer -- after the money was spent and with no row to
    # show for it. `get_model_cost()`'s refusal is the right one and it is in
    # the wrong PLACE for a value that is known before anything is sent.
    #
    # IT READS PRICING_CONFIG DIRECTLY RATHER THAN CALLING get_model_cost(),
    # which lives in oncotriage/utils.py -- and `oncotriage.config` must never
    # import `oncotriage.utils`. That is the cycle
    # tests/test_package_invariants.py fails on. The table is in this file, so
    # the membership question needs no import at all.
    if BEDROCK_ANTHROPIC_MATCHING_MODEL not in PRICING_CONFIG["models"]:
        raise RuntimeError(
            f"BEDROCK_ANTHROPIC_MATCHING_MODEL is "
            f"{BEDROCK_ANTHROPIC_MATCHING_MODEL!r}, which has no row in "
            f"PRICING_CONFIG. get_model_cost() RAISES on an unpriced model by "
            f"design, so this configuration would spend a live Stage 5 call "
            f"and then fail to write the row it paid for. Add a row for it in "
            f"oncotriage/config.py -- and read VERIFY-AT-GO-LIVE (A6) first: "
            f"only the 'global.' row is measured, the rest are inferred at a "
            f"+10% geo premium.")

    if BEDROCK_ANTHROPIC_CACHE_TTL not in BEDROCK_ANTHROPIC_CACHE_TTLS:
        raise RuntimeError(
            f"BEDROCK_ANTHROPIC_CACHE_TTL is {BEDROCK_ANTHROPIC_CACHE_TTL!r}. "
            f"Accepted: {BEDROCK_ANTHROPIC_CACHE_TTLS} (None omits the cache "
            f"breakpoint). Edit it in oncotriage/config.py.")

    if BEDROCK_ANTHROPIC_THINKING not in BEDROCK_ANTHROPIC_THINKING_MODES:
        raise RuntimeError(
            f"BEDROCK_ANTHROPIC_THINKING is {BEDROCK_ANTHROPIC_THINKING!r}. "
            f"Accepted: {BEDROCK_ANTHROPIC_THINKING_MODES} (None omits the "
            f"object). NOTE that MATCHING_REASONING_EFFORT is NOT this "
            f"vocabulary and is not expressible here -- see the constant's "
            f"note. Edit it in oncotriage/config.py.")

    if BEDROCK_ANTHROPIC_EFFORT not in BEDROCK_ANTHROPIC_EFFORTS:
        raise RuntimeError(
            f"BEDROCK_ANTHROPIC_EFFORT is {BEDROCK_ANTHROPIC_EFFORT!r}. "
            f"Accepted: {BEDROCK_ANTHROPIC_EFFORTS} (None omits the field). "
            f"Edit it in oncotriage/config.py.")

    if BEDROCK_ANTHROPIC_SERVICE_TIER not in BEDROCK_ANTHROPIC_SERVICE_TIERS_ALLOWED:
        raise RuntimeError(
            f"BEDROCK_ANTHROPIC_SERVICE_TIER is "
            f"{BEDROCK_ANTHROPIC_SERVICE_TIER!r}. Claude Sonnet 4.6 supports "
            f"Standard and Reserved only, and Reserved is an ACCOUNT-level "
            f"setting rather than a per-request one -- its model card says so "
            f"-- so the only correct per-request values are "
            f"{BEDROCK_ANTHROPIC_SERVICE_TIERS_ALLOWED} (None omits the field, "
            f"which IS Standard). Edit it in oncotriage/config.py.")

    if BEDROCK_ANTHROPIC_RETRY_MODE not in BEDROCK_ANTHROPIC_RETRY_MODES:
        raise RuntimeError(
            f"BEDROCK_ANTHROPIC_RETRY_MODE is "
            f"{BEDROCK_ANTHROPIC_RETRY_MODE!r}. botocore accepts "
            f"{BEDROCK_ANTHROPIC_RETRY_MODES}; anything else raises inside the "
            f"client constructor, which is AFTER this branch has been "
            f"selected and is a worse place to learn it. 'adaptive' is the "
            f"documented answer for an account that is being throttled; read "
            f"the constant before switching. Edit it in oncotriage/config.py.")

    if not isinstance(BEDROCK_ANTHROPIC_WARMUP_SEND_OUTPUT_CONFIG, bool):
        raise RuntimeError(
            f"BEDROCK_ANTHROPIC_WARMUP_SEND_OUTPUT_CONFIG is "
            f"{BEDROCK_ANTHROPIC_WARMUP_SEND_OUTPUT_CONFIG!r}; it must be a "
            f"bool. It decides whether the per-trial cache warmup carries the "
            f"structured-output block, and a truthy non-bool would make that "
            f"decision by accident. Edit it in oncotriage/config.py.")


def validate_matching_provider_config():
    """Refuse a provider configuration that cannot work, naming the constant.

    Pure, cheap, and called at the TOP of every Bedrock request rather than at
    import: this module promises that importing it resolves nothing, and a
    validation that ran at import would need the provider decided before the
    file finished loading.

    Raises:
        RuntimeError: naming the constant to edit and the documented rule it
            violates. RuntimeError rather than ValueError, on the
            `UnknownModelPricingError` / `IndexVerificationError` /
            `CrossEncoderLimitMismatchError` precedent -- a stray
            ``except ValueError`` around a model call must not eat it.
    """
    if MATCHING_PROVIDER not in MATCHING_PROVIDERS:
        raise RuntimeError(
            f"MATCHING_PROVIDER is {MATCHING_PROVIDER!r}, which is not a "
            f"provider this pipeline knows. Accepted: "
            f"{', '.join(MATCHING_PROVIDERS)}. Edit MATCHING_PROVIDER in "
            f"oncotriage/config.py.")

    if MATCHING_PROVIDER == MATCHING_PROVIDER_BEDROCK_ANTHROPIC:
        _validate_bedrock_anthropic_config()
        return

    if MATCHING_PROVIDER != MATCHING_PROVIDER_BEDROCK:
        return

    if BEDROCK_ENDPOINT not in BEDROCK_BASE_URL_TEMPLATES:
        raise RuntimeError(
            f"BEDROCK_ENDPOINT is {BEDROCK_ENDPOINT!r}. Accepted: "
            f"{', '.join(sorted(BEDROCK_BASE_URL_TEMPLATES))}. Edit "
            f"BEDROCK_ENDPOINT in oncotriage/config.py.")

    _validate_bedrock_region("BEDROCK_BASE_URL_TEMPLATES")

    if not BEDROCK_MATCHING_MODEL or not str(BEDROCK_MATCHING_MODEL).strip():
        raise RuntimeError(
            "BEDROCK_MATCHING_MODEL is empty. Edit it in "
            "oncotriage/config.py.")

    if BEDROCK_ENDPOINT == BEDROCK_ENDPOINT_RUNTIME and not any(
            BEDROCK_MATCHING_MODEL.startswith(p)
            for p in BEDROCK_RUNTIME_PROFILE_PREFIXES):
        raise RuntimeError(
            f"BEDROCK_MATCHING_MODEL is {BEDROCK_MATCHING_MODEL!r}, which "
            f"names no cross-Region inference profile, and BEDROCK_ENDPOINT "
            f"is {BEDROCK_ENDPOINT_RUNTIME!r}. The GPT-5.6 models are not "
            f"available for in-Region inference on that endpoint: the request "
            f"would be rejected. Prefix it with one of "
            f"{', '.join(BEDROCK_RUNTIME_PROFILE_PREFIXES)} (for example "
            f"'us.{BEDROCK_MATCHING_MODEL}'), or set BEDROCK_ENDPOINT to "
            f"{BEDROCK_ENDPOINT_MANTLE!r}, where the bare model id is correct.")

    if BEDROCK_SERVICE_TIER not in BEDROCK_SERVICE_TIERS_ALLOWED:
        raise RuntimeError(
            f"BEDROCK_SERVICE_TIER is {BEDROCK_SERVICE_TIER!r}. gpt-5.6-terra "
            f"supports the Standard tier only -- Priority, Flex and Reserved "
            f"are marked not supported on its model card. Accepted here: "
            f"{BEDROCK_SERVICE_TIERS_ALLOWED} (None omits the field, which IS "
            f"Standard). Edit BEDROCK_SERVICE_TIER in oncotriage/config.py.")

    if BEDROCK_SYSTEM_ROLE not in BEDROCK_SYSTEM_ROLES:
        raise RuntimeError(
            f"BEDROCK_SYSTEM_ROLE is {BEDROCK_SYSTEM_ROLE!r}. Accepted: "
            f"{', '.join(BEDROCK_SYSTEM_ROLES)}. Edit it in "
            f"oncotriage/config.py.")

    if BEDROCK_PROMPT_CACHE_MODE not in BEDROCK_PROMPT_CACHE_MODES:
        raise RuntimeError(
            f"BEDROCK_PROMPT_CACHE_MODE is {BEDROCK_PROMPT_CACHE_MODE!r}. "
            f"Accepted: {BEDROCK_PROMPT_CACHE_MODES} (None omits the object). "
            f"Edit it in oncotriage/config.py.")


def matching_wire_model():
    """The model id Stage 5 will actually SEND, for the configured provider.

    THE ONE FUNCTION THAT ANSWERS THAT QUESTION, and it is what
    `oncotriage/run_fingerprint.py` stamps as `matching_model_configured`.

    WHY THE FINGERPRINT READS THIS RATHER THAN `MATCHING_MODEL`. That field is
    GATED: a resume whose value differs refuses. `MATCHING_MODEL` does not move
    when the provider flips -- it is the priced identity of the judge and
    "gpt-5.6-terra" is the same judge on either provider -- so a checkpoint
    written against OpenAI would have been resumed against Bedrock with the gate
    answering FP_MATCH, and one artifact would hold two providers' rows with
    nothing in it saying so. Reading the WIRE id closes that with no
    FINGERPRINT_VERSION bump and therefore no blast radius: with the flag off
    this returns `MATCHING_MODEL` exactly, so every v2 stamp already on disk
    still matches.

    WHAT IS STILL NOT GATED, stated rather than glossed: BEDROCK_ENDPOINT and
    BEDROCK_REGION. Two runs against `us.openai.gpt-5.6-terra` in different
    Regions, or one against mantle and one against runtime with the same model
    id, are indistinguishable to the resume gate. Closing that means a seventh
    gated field and a FINGERPRINT_VERSION bump, whose cost is that every
    v2-stamped artifact refuses once. Recorded as a follow-up rather than taken
    here.

    Raises:
        RuntimeError: through `validate_matching_provider_config()`, on an
            unrecognised provider. Never returns a default for one.
    """
    if MATCHING_PROVIDER == MATCHING_PROVIDER_BEDROCK:
        return BEDROCK_MATCHING_MODEL
    if MATCHING_PROVIDER == MATCHING_PROVIDER_BEDROCK_ANTHROPIC:
        return BEDROCK_ANTHROPIC_MATCHING_MODEL
    if MATCHING_PROVIDER == MATCHING_PROVIDER_OPENAI:
        return MATCHING_MODEL
    validate_matching_provider_config()          # raises, naming the constant
    raise RuntimeError(                          # unreachable; belt and braces
        f"MATCHING_PROVIDER is {MATCHING_PROVIDER!r}")


def get_bedrock_api_key():
    """The Bedrock API key, from the settings tiers. Raises when absent.

    Two tiers, first match wins, reported by SOURCE and never by value:
    ONCOTRIAGE_BEDROCK_API_KEY, then AWS's own AWS_BEARER_TOKEN_BEDROCK. See
    `oncotriage/settings.py:resolve_bedrock_api_key` for why the second is
    read at all and why it loses.

    Raises:
        RuntimeError: naming both variables and the console page that mints a
            key. Deliberately not a silent empty string: an empty api_key sends
            `Authorization: Bearer ` and gets a 401 that names nothing.
    """
    key, source = settings.resolve_bedrock_api_key()
    if key is None:
        raise RuntimeError(
            f"MATCHING_PROVIDER is {MATCHING_PROVIDER_BEDROCK!r} but no "
            f"Bedrock API key is set.\n"
            f"  Set {settings.ENV_BEDROCK_API_KEY} (this project's name, which "
            f"wins) or {settings.ENV_AWS_BEARER_TOKEN_BEDROCK} (AWS's own).\n"
            f"  Mint one in the Bedrock console under 'API keys'. A short-term "
            f"key lasts at most 12 hours.")
    return key, source


def get_bedrock_base_url():
    """The base URL for the configured endpoint and Region. Pure."""
    return BEDROCK_BASE_URL_TEMPLATES[BEDROCK_ENDPOINT].format(
        region=BEDROCK_REGION)


def get_bedrock_client() -> OpenAI:
    """The one Bedrock client this process uses. Built on first call, cached.

    `get_openai_client()`'s precedent in every respect that matters, and the
    three arguments it carries are inherited rather than re-decided:

      max_retries   OPENAI_SDK_MAX_RETRIES, the TRANSPORT budget. The same
                    number for the same reason -- anything that fails twice in
                    a row is not transient. Note it is what makes a Bedrock 429
                    or 5xx retried in-SDK exactly as an OpenAI one is.
      timeout       get_matching_request_timeout(), the STRUCTURED httpx
                    Timeout. A bare float here would flatten the connect phase
                    from the SDK's 5 seconds to 300, which is the regression
                    _structured_timeout() exists to prevent; an unreachable
                    Bedrock endpoint must fail in seconds.
      api_key       the settings-tier key. Sent as `Authorization: Bearer`,
                    which is exactly what Bedrock's OpenAI-compatible surface
                    accepts.

    NOT RESETTABLE, and not resolved at import -- both for the reasons written
    above the cache globals.

    Constructing this opens no socket. Reaching it at all is gated on
    MATCHING_PROVIDER: with the flag off nothing in the package calls it.
    """
    global _BEDROCK_CLIENT_CACHE
    if _BEDROCK_CLIENT_CACHE is None:
        # REFUSES WHILE THE FLAG IS OFF, and that is a guarantee rather than a
        # tidiness. "With MATCHING_PROVIDER = 'openai' no Bedrock client is
        # constructed and no Bedrock credential is resolved" is otherwise a
        # property of the CALL GRAPH -- true today because
        # `evaluation.call_matching_model` dispatches above the call, and
        # untrue the moment anything else reaches this function. Here it is a
        # property of the function, so no future caller can break it silently.
        if MATCHING_PROVIDER != MATCHING_PROVIDER_BEDROCK:
            raise RuntimeError(
                f"get_bedrock_client() was called while MATCHING_PROVIDER is "
                f"{MATCHING_PROVIDER!r}. Building this client resolves a "
                f"Bedrock credential and opens a second endpoint, and nothing "
                f"in the pipeline should reach it unless Stage 5 is configured "
                f"to use it. Set MATCHING_PROVIDER to "
                f"{MATCHING_PROVIDER_BEDROCK!r} in oncotriage/config.py, or "
                f"install a stand-in through "
                f"oncotriage.agent.deps.set_override(deps.BEDROCK_CLIENT, ...) "
                f"if this is a test.")
        validate_matching_provider_config()
        key, source = get_bedrock_api_key()
        base_url = get_bedrock_base_url()
        console.out(f"🔐 Bedrock endpoint: {base_url}")
        console.out(f"🔐 Bedrock API key from: {source}")
        _BEDROCK_CLIENT_CACHE = OpenAI(api_key=key,
                                       base_url=base_url,
                                       max_retries=OPENAI_SDK_MAX_RETRIES,
                                       timeout=get_matching_request_timeout())
    return _BEDROCK_CLIENT_CACHE

def get_bedrock_anthropic_client():
    """The one boto3 ``bedrock-runtime`` client this process uses. Cached.

    `get_bedrock_client()`'s precedent in every respect that matters, with
    three differences that are forced by the SDK rather than chosen.

    **boto3 IS IMPORTED INSIDE THIS FUNCTION**, the same third-party-in-a-
    function-body exemption `import icd10` and `import torch` carry, and the
    same one `oncotriage/staging/s3_sync.py` already uses for boto3 so that the
    half of that tool which runs today works with boto3 absent. Hoisting it
    would make `import oncotriage.config` -- which every entry point in the
    project does -- require boto3, and `tests/test_package_invariants.py`
    section 2 would have a new module-scope third-party import to account for.

    **THE TIMEOUT IS A `botocore.config.Config`, NOT AN httpx.Timeout.**
    `get_matching_request_timeout()` returns an httpx object that means nothing
    to botocore, so the two phases are passed as numbers:

      connect_timeout  BEDROCK_ANTHROPIC_CONNECT_TIMEOUT_SECONDS (5.0),
                       replacing botocore's own default of 60.
      read_timeout     MATCHING_REQUEST_TIMEOUT_SECONDS (300), the SAME
                       measured budget the OpenAI path uses, so a Stage 5 call
                       is bounded identically on either provider.

    **`max_attempts` IS `OPENAI_SDK_MAX_RETRIES + 1`, AND THE +1 IS THE WHOLE
    POINT.** botocore counts TOTAL attempts; the OpenAI SDK counts RETRIES
    after the first. Passing the retry count straight through would silently
    halve the transport budget -- or, read the other way, passing it as-is to a
    library that meant the other thing is how a budget doubles. `"standard"`
    mode is named explicitly because botocore's default (`legacy`) retries a
    narrower error set and is documented as deprecated.

    NOT RESETTABLE, and not resolved at import. Constructing it opens no
    socket.

    Raises:
        RuntimeError: when the provider flag is not this branch; when boto3 is
            absent; or when a credential is set that boto3 cannot see.
    """
    global _BEDROCK_ANTHROPIC_CLIENT_CACHE
    if _BEDROCK_ANTHROPIC_CLIENT_CACHE is None:
        # REFUSES WHILE THE FLAG IS OFF, on `get_bedrock_client()`'s argument:
        # "no Bedrock client is constructed and no Bedrock credential is
        # resolved" is otherwise a property of the CALL GRAPH, true today and
        # untrue the moment anything else reaches this function. Here it is a
        # property of the function, so no future caller can break it silently.
        if MATCHING_PROVIDER != MATCHING_PROVIDER_BEDROCK_ANTHROPIC:
            raise RuntimeError(
                f"get_bedrock_anthropic_client() was called while "
                f"MATCHING_PROVIDER is {MATCHING_PROVIDER!r}. Building this "
                f"client resolves AWS credentials and opens a second "
                f"endpoint, and nothing in the pipeline should reach it unless "
                f"Stage 5 is configured to use it. Set MATCHING_PROVIDER to "
                f"{MATCHING_PROVIDER_BEDROCK_ANTHROPIC!r} in "
                f"oncotriage/config.py, or install a stand-in through "
                f"oncotriage.agent.deps.set_override("
                f"deps.BEDROCK_ANTHROPIC_CLIENT, ...) if this is a test.")
        validate_matching_provider_config()

        try:
            import boto3
            from botocore.config import Config as _BotoConfig
        except ImportError as exc:
            raise RuntimeError(
                f"MATCHING_PROVIDER is "
                f"{MATCHING_PROVIDER_BEDROCK_ANTHROPIC!r}, which reaches "
                f"Bedrock through the Converse API and therefore needs boto3. "
                f"It is declared in pyproject.toml and is not importable here: "
                f"{exc}. Run `pip install -e .` from 03- Code/.") from exc

        _assert_bedrock_anthropic_credential_is_visible()

        console.out(f"🔐 Bedrock Converse region: {BEDROCK_REGION}")
        _BEDROCK_ANTHROPIC_CLIENT_CACHE = boto3.client(
            "bedrock-runtime",
            region_name=BEDROCK_REGION,
            config=_BotoConfig(
                connect_timeout=BEDROCK_ANTHROPIC_CONNECT_TIMEOUT_SECONDS,
                read_timeout=MATCHING_REQUEST_TIMEOUT_SECONDS,
                # BOTH READ THROUGH THEIR OWN OWNERS rather than written out
                # here. `bedrock_anthropic_max_attempts()` carries the
                # retries-versus-attempts conversion, and the mode is a knob
                # because "adaptive" is AWS's own documented answer for an
                # account whose requests-per-minute allowance is being hit --
                # see BEDROCK_ANTHROPIC_RETRY_MODE for why it is not the
                # default. This client is built ONCE per process and cached, so
                # both values are read at that moment and a later edit does not
                # move them; that is the same contract every other field on
                # this constructor already has.
                retries={"max_attempts": bedrock_anthropic_max_attempts(),
                         "mode": BEDROCK_ANTHROPIC_RETRY_MODE},
            ),
        )
    return _BEDROCK_ANTHROPIC_CLIENT_CACHE


def _assert_bedrock_anthropic_credential_is_visible():
    """Refuse a credential boto3 cannot see, rather than ignoring it.

    **boto3 DOES NOT READ `ONCOTRIAGE_BEDROCK_API_KEY`.** It reads AWS's own
    `AWS_BEARER_TOKEN_BEDROCK` -- the model card's own getting-started sample
    sets exactly that variable and then constructs a `bedrock-runtime` client
    -- plus the ordinary SigV4 chain (profiles, instance roles, SSO).
    `settings.resolve_bedrock_api_key()` exists so this project's own name WINS
    over AWS's on the Responses branch, where the key is handed to the OpenAI
    SDK by hand. Here there is no hand to hand it to.

    THE THREE OPTIONS WERE: mutate `os.environ` so boto3 finds it, which is a
    process-wide side effect this file has no business having and which some
    botocore versions read lazily at signing time; silently ignore the
    project's variable, which is the silently-ignored-credential failure this
    project removes on sight; or REFUSE and name the one-line fix. The third is
    what ships.

    IT DOES NOT VERIFY THAT ANY CREDENTIAL EXISTS. boto3's chain has half a
    dozen sources -- a profile, an instance role, SSO, a container role -- and
    a check that demanded an environment variable would refuse a perfectly
    ordinary IAM deployment. What it refuses is the ONE state that is
    unambiguously a mistake: this project's variable set, AWS's not.

    Raises:
        RuntimeError: naming both variables and the one-line fix.
    """
    key, source = settings.resolve_bedrock_api_key()
    if key is not None and source == settings.ENV_BEDROCK_API_KEY:
        import os as _os
        if not _os.environ.get(settings.ENV_AWS_BEARER_TOKEN_BEDROCK):
            raise RuntimeError(
                f"{settings.ENV_BEDROCK_API_KEY} is set but "
                f"{settings.ENV_AWS_BEARER_TOKEN_BEDROCK} is not, and this "
                f"provider reaches Bedrock through boto3, which reads only the "
                f"second. Continuing would ignore the key you set and fall "
                f"through to whatever else is in the AWS credential chain -- "
                f"possibly a different account.\n"
                f"  Fix: export {settings.ENV_AWS_BEARER_TOKEN_BEDROCK} with "
                f"the same value, or unset {settings.ENV_BEDROCK_API_KEY} if "
                f"you meant to use the ordinary AWS credential chain.")


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
# THIS BOUNDS ONE ATTEMPT, NOT ONE PATIENT. See
# the three-budget reconciliation below for the arithmetic that turns it into a
# per-patient bound, and OPENAI_SDK_MAX_RETRIES near the top of this file for
# the retry half of it.
#
# THIS IS THE READ PHASE ONLY, and it is applied through a structured
# httpx.Timeout, not as a bare number. Item 29b passed the bare number and so
# flattened the connect phase from the SDK's 5 seconds to 300; item 29d undid
# that. The value assignment and the phase choices are at the top of this file,
# beside the client construction that consumes them (_structured_timeout); this
# is where the number came FROM.
#
# RE-DERIVE THIS IF MATCHING_REASONING_EFFORT CHANGES. The worst single call
# was 94.6s at 'none' and 107.5s at 'medium'; the higher tiers were not
# measured and could be far slower.
#
# (No assignment here -- see MATCHING_REQUEST_TIMEOUT_SECONDS at the top.)

# THE RETRY BUDGETS, RECONCILED.
#
# The SDK retry constant itself is OPENAI_SDK_MAX_RETRIES, defined ABOVE the
# client construction near the top of this file, because it can only be applied
# at construction -- the reason is written there. It belongs to this block
# conceptually, so the reconciliation lives here.
#
# THESE BUDGETS COVER DIFFERENT FAILURES. Keeping them separate is the same
# reasoning item 19c used when it split the truncation budget out of the parse
# budget: a counter shared between unrelated failures fails a patient for the
# wrong reason. What is NOT allowed is two budgets covering the SAME failure,
# which is what item 29d removed from the embedding call -- see below.
#
#   OPENAI_SDK_MAX_RETRIES (1, set on the client above)
#       TRANSPORT, and since item 29d the ONLY transport budget for either
#       OpenAI call. The request never produced a usable HTTP response -- a
#       timeout, a connection reset, a 429, a 5xx. Retried inside the SDK, so
#       it costs one more HTTP attempt and nothing else. Kept at 1 rather than
#       0 because the common case is a single transient blip and recovering it
#       in-SDK is far cheaper than re-entering the node; kept at 1 rather than
#       the SDK's default 2 because anything that fails twice in a row is not
#       transient and MAX_LLM_CLASSIFIER_RETRIES is the budget that should see it.
#
#       WHY THIS ONE AND NOT TENACITY, for get_embedding(). That function used
#       to carry @retry(stop_after_attempt(5), wait_exponential(min=2, max=60),
#       retry_if_exception_type((RateLimitError, InternalServerError,
#       APIConnectionError))). APITimeoutError SUBCLASSES APIConnectionError, so
#       a timeout was retried by tenacity AND by the SDK: up to 5 x 2 = 10
#       attempts for one embedding, a number nobody chose and nothing justified.
#       The decorator was removed and the SDK retry kept, for three reasons:
#
#         1. IT IS THE ONLY ONE THAT CAN BE SCOPED CORRECTLY. Turning the SDK
#            retry off for one call needs with_options(), which item 29c proved
#            silently unwraps File 45's OpenAIProxy and disables fixture
#            capture. Turning it off globally would strip Stage 5's transport
#            retry as a side effect of an embedding decision -- exactly the
#            cross-coupling this change exists to remove.
#         2. IT HONOURS Retry-After ON A 429. Tenacity's exponential backoff is
#            blind to the header. With MAX_WORKERS = 12 and
#            ENABLE_RATE_LIMITING = False, 429 is the most likely transport
#            failure in a batch run, and the server's own advice beats a guess.
#         3. IT RETRIES THE HTTP REQUEST, not the Python function, so it cannot
#            re-run anything the caller already did.
#
#       WHAT WAS GIVEN UP: an attempt count visible in the source at the call
#       site, and a longer backoff ceiling (60s vs the SDK's 8s). Both are
#       recorded here instead, which is the trade.
#
#       Attempts for one embedding call after the change: 1 + 1 = 2.
#       Attempts for one Stage 5 request: UNCHANGED at 1 + 1 = 2, so the
#       per-patient arithmetic below is unaffected by the embedding decision.
#
#   MAX_LLM_CLASSIFIER_RETRIES (3)
#       THE RESPONSE CAME BACK AND WAS UNUSABLE. Malformed JSON, a non-list
#       payload, or an exception that escaped the call. Retried by re-entering
#       node_llm_classifier_evaluation, which rebuilds and re-sends the whole prompt.
#       Note this budget ALSO absorbs a transport failure that survived the SDK
#       retry above, which is why the two multiply in the arithmetic below.
#
#   MAX_TRUNCATION_SPLITS (3)
#       THE RESPONSE WAS FINE BUT TOO LONG. Not a retry at all: it is depth of
#       halving, and it only happens after a call that SUCCEEDED and reported
#       finish_reason='length'. It multiplies the NUMBER of requests, not the
#       number of attempts at one request.
#
# WORST-CASE WALL TIME PER PATIENT, stated because a bound nobody has computed
# is not a bound:
#
#   one request, all attempts timing out
#       (1 + OPENAI_SDK_MAX_RETRIES) x MATCHING_REQUEST_TIMEOUT_SECONDS
#       = 2 x 300 = 600s
#
#   one patient, every node attempt dying at transport  <-- THE REAL BOUND
#       MAX_LLM_CLASSIFIER_RETRIES x 600 = 3 x 600 = 1,800s = 30 MINUTES
#       (3 node attempts, not 4: retry_count is incremented before the router
#        compares it, so the loop runs 3 times and then routes to the error
#        handler.)
#
#       The same figure at each earlier state of this file, so the direction of
#       travel is checkable rather than asserted:
#           before item 29b   no timeout, 2 SDK retries: 3 x 3 x 600 = 90 min
#           after  item 29b   300s timeout, 2 SDK retries: 3 x 3 x 300 = 45 min
#           now               300s timeout, 1 SDK retry:  3 x 2 x 300 = 30 min
#
#   one patient whose responses all succeed and all truncate
#       a 15-trial batch split to depth 3 issues at most 1+2+4+8 = 15 requests.
#       At the MEASURED per-call latency that is ~15 x 67s = 17 minutes of real
#       work -- not a failure mode, and not additive with the 30 minutes above:
#       a call cannot both succeed-and-truncate and die at transport.
#
#   the one embedding call each patient makes
#       (1 + OPENAI_SDK_MAX_RETRIES) x EMBEDDING_REQUEST_TIMEOUT_SECONDS
#       + SDK backoff <= 2 x 30 + 8 = 68s, against ~5.5 minutes while the
#       tenacity decorator was still stacked on top of it.
#
# So: 30 minutes is the ceiling for a patient stuck against a broken endpoint,
# ~17 minutes is the ceiling for one doing an unusual amount of real work, and
# ~1 minute for its embedding. None is unbounded, which is the property that did
# not hold before.
#
# NOT INCLUDED IN ANY OF THESE FIGURES: the httpx phase structure. Each attempt
# is bounded per PHASE, so its theoretical worst case is connect + read + write
# + pool, not the read budget alone -- 905s rather than 300s for Stage 5. The
# figures above use the read budget because read is the only phase that can
# plausibly stall for its full allowance; see _structured_timeout() at the top
# of this file for why the others are set where they are.
#
# (No constant is assigned here on purpose -- OPENAI_SDK_MAX_RETRIES is defined
#  above the client construction, where it has to be.)


# Wall-clock ceiling on ONE embedding request, in seconds.
#
# WHY THIS IS NOT MATCHING_REQUEST_TIMEOUT_SECONDS. get_embedding() (File 13)
# runs once per patient at inference time and had been left on the same 600s
# SDK default the Stage 5 call was moved off. Reusing the Stage 5 number would
# have been the easy thing and the wrong one: 300s is sized for a request that
# GENERATES thousands of tokens, and an embedding generates none.
#
# NOT A MEASUREMENT. No embedding-latency figure exists anywhere in this
# codebase or in the item 29a data -- the bake-off timed Stage 5 calls only,
# and the fixtures record embedding VECTORS with no timing beside them. This
# value is therefore derived from the CALL'S SHAPE, and that basis is stated
# rather than dressed up as evidence:
#
#   - the input is one short string (the expanded query, a few hundred
#     characters), against Stage 5's ~11,000 input tokens;
#   - there is no autoregressive generation at all: the response is a single
#     fixed-size 1,536-float vector, so latency is one forward pass plus
#     round-trip, not a token-by-token stream;
#   - Stage 5, which does vastly more work, has a measured median of 66.5s.
#
# 30s is therefore generous for the shape of the call while still being a real
# bound. If it proves too tight the symptom is LOUD, not silent: an
# APITimeoutError, retried once by the SDK and then raised. Replace this with a
# measured value the first time anyone instruments the call.
#
# It is applied as the READ phase of a structured httpx.Timeout, the same way
# the Stage 5 budget is, so an unreachable host still fails on the SDK's
# 5-second connect phase rather than waiting 30 seconds for it.
#
# WORST CASE FOR ONE EMBEDDING CALL, after item 29d removed the tenacity
# decorator (see the four-budget reconciliation above):
#
#     (1 + OPENAI_SDK_MAX_RETRIES) x 30s + SDK backoff <= 2 x 30 + 8 = 68s
#
# against ~5.5 minutes with the decorator still in place, and 5 x 3 x 600 =
# 2.5 HOURS before any of these constants existed.
#
# (No assignment here -- see EMBEDDING_REQUEST_TIMEOUT_SECONDS at the top,
#  beside the client construction that consumes the structured object.)

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
# MAX_LLM_CLASSIFIER_RETRIES: a patient that hits one malformed response and then needs
# two splits must not be failed for exhausting a shared counter. Three levels
# takes a 15-trial batch to 2 trials.
MAX_TRUNCATION_SPLITS = 3

# ---------------------------------------------------------------------------
# The serving endpoint, and the harness client's budget for talking to it
# ---------------------------------------------------------------------------
#
# THESE ARE NOT PIPELINE TUNABLES. Everything above this block describes what
# the pipeline does; this block describes how something OUTSIDE the pipeline
# reaches it over HTTP. It is here rather than in the entry points for the
# reason every other constant in this file is here: a number written out at
# three call sites is three numbers that can disagree, and these three did.


# The TCP port the API binds and every local client targets.
#
# ONE OWNER FOR A VALUE THAT WAS WRITTEN OUT THREE TIMES: "17- FastAPI
# Server.py"'s uvicorn.run(), and the BASE_URL literal in each of
# "18- FastAPI Server Test.py" and "19- FastAPI Server Batch Test.py". The
# value does not change -- 8000 was and is the port -- so nothing about how
# this project is run moves; what changes is that a future port change is one
# edit rather than three, and a harness pointed at the wrong port becomes
# impossible rather than merely unlikely.
#
# DOCKER CANNOT READ THIS AND IS NOT EXPECTED TO. docker-compose.yml names
# 8000 in a port mapping, in the uvicorn argument vector and in the healthcheck
# URL, and a YAML file cannot import a Python module. Those literals agree with
# this constant BY DISCIPLINE, and the compose file's own comments are where a
# reader is told so. The precedent for closing that gap exists -- APP_VERSION
# arrives in the image as a build ARG derived by docker/app_version.py, with a
# `RUN --check` failing the build on disagreement -- and applying it to the
# port is a follow-up, not this block's job. NOTE that the container does not
# run this file's uvicorn.run() at all: its command is an explicit
# `uvicorn ... --port 8000` argument vector, so changing this constant changes
# the LOCAL `python "17- FastAPI Server.py"` bind and the two harnesses, and
# leaves the container exactly where it was.
API_PORT = 8000


# How long the harness waits for the CONNECT phase of a request, in seconds.
#
# THE FIRST TIER OF A TWO-TIER BUDGET, and it exists because the second tier is
# necessarily long. "The server is not there" and "the server is working and
# has not finished" are different failures, and a single scalar timeout cannot
# tell a harness which one it met: pointed at a port nothing is listening on,
# a one-number budget waits out the whole read allowance to learn something the
# kernel knew in microseconds.
#
# requests takes (connect, read) as a tuple, so the split costs nothing. This
# is the same argument _structured_timeout() makes above about httpx, reached
# from the client side: a host that cannot be reached should fail in seconds,
# and nothing about this pipeline justifies waiting longer to learn that.
#
# 5.0 rather than a value read off the SDK. get_sdk_default_connect_timeout_
# seconds() is the analogous number for the OpenAI client and it is deliberately
# not reused: reading it CONSTRUCTS a throwaway OpenAI client and therefore
# resolves the credentials, and a harness that POSTs to localhost must not need
# an OpenAI key to decide how long to wait for a TCP handshake. 5.0 is the value
# that SDK ships, transcribed with the reason stated, and the loopback
# connections these harnesses actually make either succeed or are refused
# immediately -- this tier is a guard against a misconfigured host, not a
# latency budget.
HARNESS_CONNECT_TIMEOUT_SECONDS = 5.0


# Wall-clock allowance for ONE Stage 5 request as observed, in seconds.
#
# NOT A TIMEOUT AND NOT MATCHING_REQUEST_TIMEOUT_SECONDS. That constant bounds
# a STALLED request and is 3.2x the worst call ever measured, deliberately.
# This one is an estimate of what a WORKING call costs, and it is summed
# fifteen times below -- so using the stall bound here would multiply 300 by 15
# and produce a number describing nothing.
#
# 95 is one second above the worst single call in the item 29a bake-off
# (median 66.5s, max 94.6s over 27 single-call runs at the shipped
# configuration; the figures and their provenance are beside
# MATCHING_REQUEST_TIMEOUT_SECONDS above). Using the observed MAX rather than
# the median is deliberate: it is summed, and a per-draw upper bound makes the
# sum a genuine bound rather than an expectation. RE-DERIVE IT WHENEVER
# MATCHING_REASONING_EFFORT OR MATCHING_MODEL CHANGES -- it is a measurement of
# one judge at one effort, and the same sentence is written above the numbers
# it comes from.
HARNESS_MATCHING_CALL_ALLOWANCE_SECONDS = 95


# Wall-clock allowance for everything in one request that is NOT Stage 5.
#
# MEASURED, over the 1,106 rows in the production inferences.db on 2026-08-20,
# as total_time minus the Stage 5 evaluation time:
#
#     median 204.3s    p95 295.3s    p99 327.6s    max 356.1s
#
# 420 is above the observed maximum with roughly 18% of headroom. THOSE ROWS
# ARE BATCH-RUNNER ROWS, twelve threads deep, so they are contended harder than
# a single API request: the one measured single request through the container
# (DOCKER CLEAN BRING-UP.md) was 159.0s end to end. Using the contended figure
# is the conservative direction and is chosen on purpose, because the API also
# serves concurrently -- POST /match runs the graph on the event loop's thread
# pool, so two overlapping requests contend exactly the way two batch threads do.
#
# WHAT IT DOES NOT COVER, stated rather than left to be discovered: the ONE-TIME
# cold load of MedCPT (~110 MB) and FastEmbed on the first retrieval after a
# server start. No measured row contains it -- every row above came from a
# process whose models were already resident -- so putting a number on it here
# would be an invention. The symptom if it bites is a single timeout on the
# first POST of a freshly started server, which is loud, one-off, and correctly
# diagnosed by the operator who just started the server.
HARNESS_NON_LLM_ALLOWANCE_SECONDS = 420


# The READ tier: how long the harness waits for a server that has accepted the
# connection, in seconds. DERIVED, and the derivation is the point of this
# block.
#
# THE VALUE IT REPLACES WAS 180, AND 180 WAS WRONG BY MEASUREMENT RATHER THAN BY
# TASTE. It sat below MATCHING_REQUEST_TIMEOUT_SECONDS -- so a single Stage 5
# call allowed to run its full budget outlived the client waiting for it -- and
# it sat below the MEDIAN of every request this pipeline has ever recorded
# (total_time median 281.3s over those same 1,106 rows). More than half of a
# measured population would have been reported by the harness as a TIMEOUT
# against a server that was working correctly, after the money for that patient
# had already been spent and the row already written. The comment above it
# argued for a Stage 5 bound and the number was not one; this block is that
# argument finally agreeing with its value.
#
# WHAT THIS BUDGET COVERS: a server doing REAL WORK, to the deepest legitimate
# extent this configuration allows. That is the truncation path -- a batch
# halved to depth MAX_TRUNCATION_SPLITS issues 1 + 2 + 4 + 8 requests, which is
# 2**(MAX_TRUNCATION_SPLITS + 1) - 1, and every one of them SUCCEEDS. The
# expression below is written over MAX_TRUNCATION_SPLITS rather than the
# number 15 so that raising the split depth moves the client budget with it.
#
# WHAT IT DELIBERATELY DOES NOT COVER, and this is the whole design decision:
# the STUCK-ENDPOINT case. The reconciliation beside MAX_LLM_CLASSIFIER_RETRIES
# above computes that ceiling exactly --
#
#     MAX_LLM_CLASSIFIER_RETRIES x (1 + OPENAI_SDK_MAX_RETRIES)
#         x MATCHING_REQUEST_TIMEOUT_SECONDS = 3 x 2 x 300 = 1,800s = 30 minutes
#
# -- and covering it here as well would take this constant to 1,800 + 420 =
# 2,220s, 37 minutes of silence per POST. It is not covered for a reason this
# file already states about itself: two budgets covering the SAME failure is
# what item 29d removed from the embedding call. The server ALREADY bounds the
# stuck-endpoint case, at 30 minutes, on purpose. A client that also bounds it
# is the second budget, and the only thing it buys by waiting the extra seven
# minutes is the server's own error-shaped 200 -- which says "this failed",
# which is precisely what the client's timeout says, sooner, with the same
# actionable content and the same exit code.
#
# So the line is drawn at "is the server doing real work or is it stuck", the
# client owns the first and the server owns the second, and neither duplicates
# the other. The honest cost of that line is stated rather than hidden: a
# server stuck against a broken model endpoint is abandoned by the harness at
# the budget below instead of answering later, and the harness reports a
# timeout rather than the server's own error body.
#
#     (2**(3 + 1) - 1) x 95 + 420 = 15 x 95 + 420 = 1,845s = 30.75 minutes
#
# THAT IS STILL A LONG WAIT AND IT IS NOT AN OVERSIGHT. It is what a server
# permitted to issue fifteen successive model calls on one connection costs,
# and the two files that pay it POST two patients each, by hand, while a human
# watches. The asymmetry is the one this file already ruled on beside
# MATCHING_REQUEST_TIMEOUT_SECONDS: "the cost of being too tight is a failed
# patient, while the cost of being too loose is only that a stall takes longer
# to surface." Too tight here is worse still, because the patient is failed
# AFTER it has been paid for.
HARNESS_POST_READ_TIMEOUT_SECONDS = (
    (2 ** (MAX_TRUNCATION_SPLITS + 1) - 1) * HARNESS_MATCHING_CALL_ALLOWANCE_SECONDS
    + HARNESS_NON_LLM_ALLOWANCE_SECONDS
)


# What a harness actually passes to requests.post(timeout=...). The two tiers
# above, as the (connect, read) tuple requests expects. Assembled here so that
# no call site can pass one tier and forget the other, which is the mistake a
# pair of loose scalars invites.
HARNESS_POST_TIMEOUT = (HARNESS_CONNECT_TIMEOUT_SECONDS,
                        HARNESS_POST_READ_TIMEOUT_SECONDS)


# The same two tiers for a GET that does not run the pipeline. /health touches
# nothing and /pipeline/info makes one Qdrant metadata call, so neither has any
# reason to approach the POST budget; 30 is the value "18- FastAPI Server
# Test.py" has always used and it is transcribed rather than re-derived, since
# nothing measured says it is wrong. It gains the connect tier for the same
# reason the POST budget does.
HARNESS_GET_TIMEOUT_SECONDS = 30
HARNESS_GET_TIMEOUT = (HARNESS_CONNECT_TIMEOUT_SECONDS,
                       HARNESS_GET_TIMEOUT_SECONDS)


# Characters per token. ONE OWNER, READ BY BOTH USERS: the Stage 5 input packer
# in oncotriage/agent/evaluation.py and the embedding batch sizer in
# oncotriage/retrieval/indexer.py, which held its own local copy of this value
# until the two were joined here. "Kept identical so the two agree" was the old
# arrangement and it agreed by coincidence; an import agrees by construction.
# Kept crude on purpose — tiktoken would be a dependency and an import cost for
# an estimate whose job is to be roughly right before a call that is about to
# measure it exactly. (The indexer's estimate_embedding_cost() DOES use tiktoken
# when it is importable, because that number gates spend; this proxy is its
# fallback, and the method it reports is derived from this constant so the two
# cannot disagree.)
#
# IT IS ALSO THE INPUT-PACKING DIVISOR (see MATCHING_INPUT_TOKEN_BUDGET below),
# and there it is deliberately CONSERVATIVE rather than accurate. Measured on
# this project's own Stage 5 prompts the true ratio is 4.2-4.4 characters per
# token, so dividing by 4 over-states the token count by 5-10% and the packer
# closes a chunk slightly early. That is the direction a budget guard must err
# in: an under-estimate ships a chunk over the threshold the packing exists to
# stay under, and the threshold is where the measured degradation begins.
CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# Stage 5 INPUT packing
# ---------------------------------------------------------------------------
#
# A SECOND SPLITTER, ON A DIFFERENT AXIS FROM THE THREE BUDGETS ABOVE. Those
# are all about the RESPONSE -- MATCHING_OUTPUT_SPLIT_FRACTION pre-splits on an
# output estimate and MAX_TRUNCATION_SPLITS halves reactively when the response
# was cut off. Nothing looked at the size of the REQUEST, and the request is
# where the fault measured for this pass lives:
#
#   * output quality degrades above roughly 12,000 input tokens -- verdicts get
#     thinner and criteria arrays shorter as the prompt grows;
#   * trials are silently omitted from the response, which the reconciliation
#     block records as NOT_EVALUABLE_MODEL_OMITTED but cannot prevent;
#   * reasoning demonstrably leaks between trials inside one prompt, which is
#     the thing constraint C4 asks the model not to do and cannot enforce.
#
# None of the three raises, none moves a counter on its own, and all three get
# worse as MAX_TRIALS_FOR_EVALUATION or the criteria text grows.
#
# THE TWO MECHANISMS COMPOSE AND ARE NOT ALTERNATIVES. Packing bounds what goes
# IN; the pre-split and the reactive split bound what comes OUT. A packed chunk
# is still subject to both -- see node_llm_classifier_evaluation, which seeds the
# pre-split loop with the packed chunks rather than with the whole batch.

# Master switch. OFF reproduces the pre-packing behaviour EXACTLY: the node
# seeds its pending queue with the whole batch, as it always did, and the two
# output budgets are untouched. It is a switch rather than a threshold of
# infinity because the validation run needs both arms, and because "packing did
# not run" and "packing ran and produced one chunk" are different facts that the
# provenance record has to be able to state apart.
MATCHING_INPUT_PACKING_ENABLED = True

# The per-chunk input ceiling, in estimated tokens, counting the WHOLE request:
# the system message (instructions + this patient's record) plus the user
# message carrying that chunk's fenced trials. Not the trials alone -- the model
# reads one prompt, and a budget over half of it is not a budget.
#
# 12,000 is where the degradation above was measured to begin. It is not derived
# from the model's context window, which is far larger; a context limit says
# what will be REFUSED and this says where the answers get worse.
MATCHING_INPUT_TOKEN_BUDGET = 12000

# Ceiling on how many chunks INPUT packing may produce for one patient. The
# output splitters keep their own budgets and are not counted here.
#
# WHAT HAPPENS AT THE CEILING IS THE POINT: the per-chunk budget is RAISED
# uniformly to the smallest value that fits within this many chunks, and no
# trial is ever dropped. A false keep -- a chunk larger than the ideal budget --
# costs some answer quality on that chunk. A false drop costs a patient a trial
# they may be eligible for, silently, with no counter that could report it. The
# two are not comparable, so the packer has exactly one degree of freedom and it
# is the budget.
#
# 5 x 12,000 = 60,000 input tokens of headroom against a 15-trial batch that
# runs ~20,000, so this does not bite as configured; it exists so that a corpus
# whose criteria text grows, or a raised MAX_TRIALS_FOR_EVALUATION, produces a
# bounded number of billed calls rather than one per trial.
MATCHING_MAX_INPUT_PACKED_CHUNKS = 5

# ---------------------------------------------------------------------------
# Stage 5 PER-TRIAL calls
# ---------------------------------------------------------------------------
#
# A THIRD AXIS, AND IT IS THE LIMIT OF THE PACKER RATHER THAN A COMPETITOR TO
# IT. Packing bounds how big a request may get; it does not stop two trials
# sharing one prompt, and sharing a prompt is where the measured fault lives:
# the input-packing block above records that "reasoning demonstrably leaks
# between trials inside one prompt, which is the thing constraint C4 asks the
# model not to do and cannot enforce". A budget cannot remove that. Only a
# partition of one trial per request can, and the reason it was not the first
# answer is price -- fifteen requests per patient re-send the whole system
# message fifteen times.
#
# WHAT CHANGED IS THE PRICE, NOT THE ARGUMENT. PROMPT_VERSION 1.6.0 moved the
# patient record INTO the system message, so every request of one patient now
# shares a byte-identical prefix and the provider discounts it from the second
# request on -- PRICING_CONFIG's gpt-5.6-terra note records cached input at
# $0.20/1M against $2.00/1M. Whether that discount actually lands is not
# assumed here: it is measured, per call, in
# ``inferences.llm_classifier_call_details[].cached_tokens``, and the
# scheduling below exists to give the cache a chance to warm before it is
# leaned on.
#
# THIS IS THE PIPELINE'S DESIGN, AND IT SHIPS ON. Which mode a published
# number is computed under is a decision, and it has been taken: per-trial is
# the arm the pipeline runs, because it is the only one that removes the fault
# by CONSTRUCTION rather than bounding it. Grouped is RETAINED, behind this
# same switch, as the migration's documented comparison arm -- verdict
# agreement, omission rate and cost per patient are comparable only if both
# arms are runnable from one build, and they are.

# Master switch, and it is a SWITCH rather than a threshold for the reason
# MATCHING_INPUT_PACKING_ENABLED already gives about itself: the migration
# needs both arms, and "one call per trial" and "the packer happened to emit
# one trial per chunk" are different facts that the provenance record has to be
# able to state apart -- which is what inferences.matching_call_mode is for.
#
# ═══════════════════════════════════════════════════════════════════════════
#  NO PAID PER-TRIAL RUN BEFORE `python bedrock_probe.py`-STYLE PROBING OF
#  THE WARMUP.  THE THREE-CALL PROBE IS THE MIGRATION WINDOW'S FIRST COMMAND.
# ═══════════════════════════════════════════════════════════════════════════
#
# TWO FACTS THIS ARM RESTS ON HAVE NEVER BEEN OBSERVED AGAINST THE LIVE
# PROVIDER, and both fail in the expensive direction rather than the loud one:
#
#   1. WARMUP ACCEPTANCE. MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS is 1,
#      and a reasoning model bills reasoning against that same ceiling. A
#      provider that refuses the shape answers 400; evaluation.py classifies
#      exactly that and falls back to the retired one-then-rest schedule per
#      patient, so the campaign RUNS -- and pays one refused warmup and one
#      serialised full-price cache writer for every patient, forever, with no
#      process memo. Measured cost of that state at 1,000 patients: $0 in
#      refused warmups (a 400 is refused before generation) and roughly 22
#      minutes of added wall time at MAX_WORKERS = 12.
#   2. PREFIX WARMING. The whole price argument is that the shared prefix is
#      billed at the cached rate from the second request of a patient on. If
#      the provider does not cache this prefix, per-trial mode costs
#      MAX_TRIALS_FOR_EVALUATION times the grouped input price and NOTHING
#      RAISES -- every request succeeds, every verdict is produced, and the
#      only trace is `cached_tokens` reading 0 in
#      inferences.llm_classifier_call_details.
#
# THE PROBE IS THREE CALLS AND IT ANSWERS BOTH: one warmup (does the 1-token
# ceiling come back 200 or 400?), then two identical-prefix trial calls (does
# call 3 report cached_tokens > 0?). Read the answer out of the usage block,
# not out of the wall clock. Until it has been run, per-trial mode is a
# configuration nobody has seen serve a request.
#
# ON (the shipped arm), the packer is BYPASSED rather than reconfigured:
# initial_chunks becomes one single-trial chunk per trial and
# llm_classifier_packing records enabled=False with bypassed_by naming this
# mode. Setting MATCHING_INPUT_TOKEN_BUDGET to 1 would produce nearly the same
# partition and would be the wrong mechanism -- it would report a packer that
# ran, and it would still group two trials whenever one trial alone exceeded
# the budget.
#
# OFF REPRODUCES THE GROUPED ARM EXACTLY, and that is a stronger promise than
# "equivalently": with this False the node takes the identical branch it took
# before this constant existed, issues the identical requests field for field,
# spawns no thread, and creates no executor. THAT PROMISE IS WHAT MAKES THE
# COMPARISON ARM WORTH ANYTHING -- a grouped number measured today has to be
# comparable with every grouped number this pipeline has ever produced, and
# with the twelve characterization fixtures, which record the grouped arm.
# tests/test_agent_stage5_per_trial_calls.py section 8 compares an explicitly-
# OFF run request by request against a copy of the module with the per-trial
# branch compiled out, and its own check 8h shows that comparison SEPARATING
# the two arms, so 8c is a measurement rather than a tautology. Note the
# section numbers: this comment said "section 6 ... section 7's control" and
# was stale by two before the flip, which is why it now names a check as well.
#
# THE OUTPUT SPLITTERS STAY ARMED. A single trial can still overflow the output
# ceiling, and when it does the reactive splitter finds len(chunk) == 1 and
# records NOT_EVALUABLE_TRUNCATION_FLOOR -- which is the correct, already-built
# handling. Nothing about that machinery is disabled by this switch; it simply
# has no halving left to do.
#
# THE FIXTURE GATE DOES NOT FOLLOW THIS FLAG, BY DESIGN.
# `python fixture_replay.py` and `python fixture_capture.py` PIN themselves to
# the grouped arm for their own process and print that they did -- see
# oncotriage/fixtures/capture.py:pin_call_mode_for_fixture_process. So the free
# twelve-fixture replay gate survives this flip and keeps characterizing the
# GROUPED arm. PER-TRIAL FIXTURES ARE THE STANDING MIGRATION ITEM: RecordingSink
# numbers Stage 5 recordings by ARRIVAL, so a per-trial capture's
# "deterministic" prefix would be ordered by the thread scheduler. Closing it
# needs a trial-stable ordering for the chat_completions bucket plus a paid
# re-capture of all twelve -- a fixture-FORMAT change with a SCHEMA_VERSION
# bump. Until then the shipped arm's Stage 5 behaviour is covered by
# tests/test_agent_stage5_per_trial_calls.py alone.
MATCHING_PER_TRIAL_CALLS_ENABLED = True

# How many per-trial requests of ONE patient may be in flight at once, AFTER
# the warmup call has completed. Read only when the switch above is True.
#
# THE WARMUP-THEN-WAVE SHAPE IS THE WHOLE POINT AND IT IS NOT A PERFORMANCE
# TWEAK. The prefix discount exists only once a prefix has been SEEN, so firing
# all fifteen requests simultaneously would race every one of them against an
# empty cache and pay full input price on all fifteen -- the exact cost this
# mode is only viable because of. A dedicated warmup request (see
# MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS below) is therefore issued alone
# and awaited; ALL of the trial calls then go out behind it, bounded by this
# number, with none of them held back as a cache writer. Whether the discount
# then lands is recorded per call rather than assumed.
#
# THE PRODUCT WITH MAX_WORKERS IS THE NUMBER THAT MATTERS, because the node
# runs inside oncotriage/batch/runner.py's pool of MAX_WORKERS = 12 patients:
#
#     12 patients x 4 in-node = 48 Stage 5 requests in flight at peak
#
# WHY 48 IS NOT EXPECTED TO TRIP A PROVIDER LIMIT, stated as arithmetic rather
# than as reassurance. Per-trial mode multiplies the number of requests by
# MAX_TRIALS_FOR_EVALUATION (15) whatever this constant is; what this constant
# decides is only how fast a patient gets through its fifteen, and therefore
# the request RATE. At a 15-second single-trial call -- see the honest note on
# that figure below -- a patient completes a warmup plus ceil(15/4) = 4 waves.
# The warmup asks for ONE output token, so it is far cheaper in wall time than
# a trial call; taking it at a full 15s as the pessimistic bound gives 5 waves
# in ~75s, which is exactly what the retired 1 + ceil(14/4) schedule produced,
# so a full pool still sustains 12 x 16 / 75 = 2.6 requests per second, about
# 154 per minute. That is an order of magnitude under the requests-per-minute allowance
# of any paid tier, and the binding limit on a paid tier is tokens per minute
# rather than requests -- which per-trial mode moves far LESS than 15x, because
# the shared prefix is the bulk of every request and is billed at the cached
# rate from the second call of each patient on.
#
# AND THE FALLBACK IS THE SDK'S, NOT A GUESS. A 429 is retried inside the
# OpenAI SDK honouring Retry-After (OPENAI_SDK_MAX_RETRIES, argued at length in
# the request-shape section above), so a burst that does clip a limit degrades
# to a slower campaign rather than to failed patients. That is why the number
# below can be chosen from wall time rather than from a limit nobody here can
# read.
#
# 4 IS AN UNCALIBRATED HOLDING VALUE AND IS LABELLED ONE, on the footing
# ECOG_SCORE_DISTRIBUTION and MESH_BOOST_DIRECT_FRACTION are: it is derived
# from an ESTIMATED single-trial latency of ~15s, and per-trial mode has never
# been run, so that latency is not measured. It was chosen as the smallest
# value at which a per-patient wall time (~75s) does not regress against the
# grouped mode's measured ~68s median, so that turning the mode on does not by
# itself make a campaign take longer. RE-DERIVE IT FROM
# inferences.llm_classifier_call_details AFTER THE FIRST REAL PER-TRIAL RUN --
# that column carries one row per call and is the measurement this number is
# missing.
#
# 1 IS LEGAL AND MEANS "SEQUENTIAL", which is the honest way to turn the
# scheduling off without turning the mode off. 0 or a negative value is a
# configuration defect and raises at the node rather than silently meaning one
# of the two.
MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 4


# THE CEILING ON HOW MANY TRIALS ONE PATIENT MAY BE BILLED FOR.
#
# WHY A SECOND NAME FOR A NUMBER THAT ALREADY HAS ONE. It is DERIVED from
# MAX_TRIALS_FOR_EVALUATION and is not a second copy of it -- the assignment is
# the tie, so the two cannot drift and raising Stage 4's cap raises this
# automatically, which is correct: this ceiling exists to catch a trial set that
# BYPASSED that cap, not to second-guess its value. What the two names carry is
# different. MAX_TRIALS_FOR_EVALUATION is Stage 4's cost cap and is applied with
# a SLICE, in oncotriage/agent/filtering.py; it is a decision about which trials
# are worth evaluating. This is Stage 5's REFUSAL ceiling, read in
# oncotriage/agent/evaluation.py; it is a statement that a set larger than the
# cap did not come through the stage that caps it, and the only safe thing to do
# with it is to stop.
#
# WHAT IT CLOSES, AND ONLY THE PER-TRIAL ARM HAS IT. In per-trial mode the
# request count IS len(trials): one billed call per trial, plus one warmup, all
# dispatched before any of them is inspected. Nothing else in the pipeline
# bounds that number -- the packer is bypassed, the reactive splitter's floor is
# a single trial, and the parallelism bound limits how many are IN FLIGHT and
# not how many are SENT. So a caller that reaches Stage 5 with an uncapped set
# -- a direct graph.invoke with a seeded filtered_trials, a harness, or an edit
# that drops filtering.py's slice -- pays N times the price and NOTHING RAISES:
# every request succeeds, every verdict is produced, and the only trace is a
# bill. The grouped arm is a different shape and is deliberately left alone:
# its request count is bounded by MATCHING_MAX_INPUT_PACKED_CHUNKS whatever N
# is, so it already has a request-count bound that this would duplicate. Its
# INPUT tokens still scale with N; that is a real residual and it is named here
# rather than half-closed.
#
# EQUALITY RATHER THAN HEADROOM. Stage 4 emits at most MAX_TRIALS_FOR_EVALUATION
# trials, so `len(trials) > this` cannot fire for a set that came through it and
# no margin is needed. A multiplier would be a magic number bounding nothing in
# particular.
MATCHING_MAX_TRIALS_PER_PATIENT = MAX_TRIALS_FOR_EVALUATION


# ---------------------------------------------------------------------------
# The per-trial cache warmup
# ---------------------------------------------------------------------------
#
# WHAT REPLACED WHAT, AND WHY THE FIRST DESIGN WAS NOT GOOD ENOUGH. Per-trial
# mode shipped with a one-then-rest schedule: the first REAL trial call was
# awaited alone so that it would write the shared prefix into the provider's
# cache, and the remaining N-1 went out in parallel behind it. That made one
# trial's request double as cache infrastructure, and it has two consequences
# neither of which is acceptable once the mode is actually run:
#
#   * IF THE FIRST TRIAL CALL FAILS ITS TRANSPORT RETRIES, the remaining N-1
#     fire against a cache nothing ever wrote. Every one of them pays full
#     input price for a prefix the mode exists to have discounted, and nothing
#     in the record says the discount was lost for a scheduling reason rather
#     than because the provider does not cache. A cost leak that reports as a
#     successful patient.
#   * THE FAILURE SEMANTICS OF ONE TRIAL AND THE SCHEDULING OF ALL OF THEM ARE
#     ENTANGLED. "this trial could not be evaluated" and "the cache was never
#     established" are different findings with different remedies, and the old
#     design made them the same event.
#
# THE RULE NOW IS CACHE-OR-NOTHING: no trial call is ever issued without a warm
# cache ahead of it, and if the cache cannot be established the patient fails
# cleanly so that the checkpoint resumes it. A dedicated warmup request carries
# the identical system message -- the shared prefix, instructions plus patient
# record, byte for byte -- and the smallest user message and output budget the
# provider permits, so it writes the cache for a few cents of a cent and
# evaluates nothing.
#
# THE OUTPUT BUDGET IS 1 AND THAT IS A REQUEST FOR THE SMALLEST ANSWER, NOT A
# PARSE TARGET. The warmup's response is never parsed: only its usage block and
# its answering-model echo are read. The reply will almost certainly stop at
# the ceiling with finish_reason "length", which is the intended outcome and
# not a truncation this pipeline has to recover from -- the reactive splitter
# never sees it.
#
# 1 IS A FLOOR THE PROVIDER MAY REFUSE, AND THAT IS DETECTED RATHER THAN
# ASSUMED. Reasoning models bill reasoning tokens against this same ceiling and
# some providers therefore refuse a value this small outright with a 400.
# oncotriage/agent/evaluation.py classifies exactly that rejection and falls
# back to the one-then-rest schedule for the patient, recording the reason in
# PER_TRIAL_WARMUP_DEGRADATIONS -- which reaches the run-end degradation report
# -- rather than failing the patient over an infrastructure request. Raise this
# number if the probe says the floor is higher; it is the whole cost of the
# warmup and every token of it is billed at the uncached rate.
MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS = 1

# The warmup's user message. The SHARED PREFIX IS THE SYSTEM MESSAGE, so this
# string is everything the warmup does NOT share with a trial call, and the
# only requirement on it is that it be non-empty -- an empty content field is
# rejected by more than one provider. One character is the smallest thing that
# satisfies that.
#
# IT IS DELIBERATELY NOT A PROMPT. Nothing reads the answer, so anything that
# reads as an instruction would be a request for work this pipeline then throws
# away.
MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE = "."

# Whether per-trial mode sends the provider's cache-ROUTING hint.
#
# WHAT IT IS AND WHAT IT IS NOT. `prompt_cache_key` does not turn caching on --
# automatic prefix caching is on by default and needs nothing -- it asks the
# provider to route requests carrying the same key to the same machine, which
# is what raises the hit rate when N requests of one patient go out at once.
# The value is derived per patient from the system prompt's sha256 by
# oncotriage/agent/evaluation.py, so two patients never share a key and the
# same patient's warmup and wave always do.
#
# SENT ONLY IN PER-TRIAL MODE, and that is a correctness constraint rather than
# a preference: grouped mode's request is the one the twelve characterization
# fixtures recorded field for field, and a new kwarg there is a fixture diff
# and a re-capture at live model prices for a routing hint a single-request
# patient cannot use.
#
# TRUE, WITH THE PRECEDENT FOR THAT DEFAULT NAMED. BEDROCK_SEND_SEED_IN_EXTRA_
# BODY defaults False because it smuggles a field the installed SDK does not
# declare, and an unknown field is a 400 that fails every call of a run. This
# is the opposite case, measured rather than assumed: `prompt_cache_key` IS a
# declared parameter of `chat.completions.create` in the installed SDK (openai
# 1.99.9). A provider that still refuses it is detected -- evaluation.py
# classifies a 400 naming this parameter, drops the key for the rest of the
# patient and records `prompt_cache_key_rejected` -- so the failure mode is a
# recorded degradation rather than a dead run. Set False to stop sending it in
# one edit.
MATCHING_PER_TRIAL_PROMPT_CACHE_KEY_ENABLED = True

# THE SAME GUARD ITS WARMUP SIBLING BELOW ALREADY HAS, AND FOR A SHARPER
# REASON: this number becomes `ThreadPoolExecutor(max_workers=...)`.
#
# `oncotriage/agent/evaluation.py` tests `_parallel_bound < 1` and raises
# `PerTrialParallelismError`, which is right and is not enough, because a bare
# `<` comparison is not a type check. Every non-int this constant can plausibly
# be mistyped as gets PAST that test, and each fails differently and late:
#
#   * `True` -- `True < 1` is False, so the guard passes, and `max_workers=True`
#     is `max_workers=1`. A campaign silently runs per-trial mode SEQUENTIALLY
#     while every report says it ran at the configured concurrency. This is the
#     dangerous one: nothing raises, ever.
#   * `4.5` -- passes the guard, then `ThreadPoolExecutor` raises inside the
#     node, per patient, after the warmup has already been issued and billed.
#   * `"4"` -- `"4" < 1` raises `TypeError`, which is NOT
#     `PerTrialParallelismError`: it escapes the node as an unrelated failure
#     with no mention of the constant that caused it.
#
# AT IMPORT AND UNCONDITIONALLY, unlike the node's check, which is deliberately
# reached only in per-trial mode. The two ask different questions and both are
# kept: this one asks "is this constant a usable integer at all", which is a
# fact about the declaration and is true or false whether the mode is on or
# not; the node's asks "is this bound usable FOR THE MODE THAT IS ABOUT TO
# RUN", and names the mode in its message because that is the operator's other
# way out. Validating the type here means the mode can be turned on without
# discovering a typo one live warmup per patient at a time.
#
# 0 AND NEGATIVES ARE REFUSED HERE TOO, not left to the node. They are a
# configuration defect in either mode -- there is no reading of
# `MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 0` that is a request for anything --
# and refusing at load costs a process that has spent nothing. The node keeps
# its own `< 1` test regardless: it is the one that names the mode, and a check
# that exists only in the file it is checking is a check somebody deletes with
# the import.
#
# A RuntimeError AND NOT AN `assert`, on this file's own standing rule, and
# NOT `PerTrialParallelismError`: that class lives in
# `oncotriage/agent/evaluation.py`, and `config` importing the agent is the
# layering violation this project's rules forbid outright.
if (not isinstance(MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS, int)
        or isinstance(MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS, bool)
        or MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS < 1):
    raise RuntimeError(
        "MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS must be an int >= 1 (1 means "
        "sequential); it is "
        f"{MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS!r}")


# A ceiling below 1 is not a smaller request, it is a request for no answer at
# all, and providers differ on whether that is a 400 or an empty completion. A
# RuntimeError AND NOT AN `assert`, on this file's own standing rule: `python
# -O` deletes assert statements, and this is the only thing between a mistyped
# constant and a warmup that fails every patient of a campaign.
if (not isinstance(MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS, int)
        or isinstance(MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS, bool)
        or MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS < 1):
    raise RuntimeError(
        "MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS must be an int >= 1; it "
        f"is {MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS!r}")

if not MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE:
    raise RuntimeError(
        "MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE must be a non-empty string; "
        f"it is {MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE!r}")

# THE SAME TRIPLE, FOR THE TWO CONVERSE-BRANCH OVERRIDES, AND FOR THE SAME
# REASON THE BLOCK ABOVE GIVES: one becomes `ThreadPoolExecutor(max_workers=)`
# and the other becomes botocore's `retries.max_attempts`, and a bare `< 1`
# comparison is not a type check. `True` would pass one and mean "sequential"
# while every report said otherwise; `2.5` would pass and raise inside the node
# or inside botocore, per patient, after money had been spent.
#
# UNCONDITIONAL, NOT PROVIDER-GATED. These run at import on every machine
# whatever MATCHING_PROVIDER says, because a value that is not a number is a
# typo rather than a configuration choice, and the file that names the typo is
# worth more than the microsecond. `None` is the documented "follow the shared
# value" and is checked first.
if BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS is not None and (
        not isinstance(BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS, int)
        or isinstance(BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS, bool)
        or BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS < 1):
    raise RuntimeError(
        "BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS must be None (follow "
        "MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS) or an int >= 1 (1 means "
        "sequential). `True` is refused explicitly: it is an int to Python, it "
        "would mean max_workers=1, and a campaign would run per-trial mode "
        f"sequentially while every report said otherwise. It is "
        f"{BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS!r}")

if BEDROCK_ANTHROPIC_MAX_ATTEMPTS is not None and (
        not isinstance(BEDROCK_ANTHROPIC_MAX_ATTEMPTS, int)
        or isinstance(BEDROCK_ANTHROPIC_MAX_ATTEMPTS, bool)
        or BEDROCK_ANTHROPIC_MAX_ATTEMPTS < 1):
    raise RuntimeError(
        "BEDROCK_ANTHROPIC_MAX_ATTEMPTS must be None (follow "
        "OPENAI_SDK_MAX_RETRIES + 1) or an int >= 1. It is botocore's TOTAL "
        "attempt count, not a retry count -- 1 disables retries entirely -- "
        f"and it is {BEDROCK_ANTHROPIC_MAX_ATTEMPTS!r}")


# ---------------------------------------------------------------------------
# How Stage 5 partitioned its work, as ONE scalar
# ---------------------------------------------------------------------------
#
# WHAT THIS EXISTS TO SEPARATE. With per-trial mode ON the packer does not run,
# so llm_classifier_packing.enabled is False -- and it is ALSO False when the
# packing switch alone is off. Those are different runs with different request
# shapes and, if the leakage measurement is right, different verdicts. This is
# the column that tells them apart, and it is the reason a boolean was not
# enough.
#
# A CLOSED TWO-MEMBER VOCABULARY, deliberately not three. It records ONE fact
# -- whether a request carried one trial or several -- and says nothing about
# packing, which llm_classifier_packing already reports in full beside it.
# Folding both knobs into one scalar would make a value like "packed" a
# statement about two constants at once and would lose the third combination
# entirely.
MATCHING_CALL_MODE_PER_TRIAL = "per_trial"
MATCHING_CALL_MODE_GROUPED = "grouped"
MATCHING_CALL_MODES = (MATCHING_CALL_MODE_GROUPED, MATCHING_CALL_MODE_PER_TRIAL)

# THE TUPLE AND THE CONSTANTS MUST CORRESPOND, checked at import on the footing
# oncotriage/dashboard/tiers.py already uses for PATIENT_OUTCOME_LABELS: a
# closed vocabulary written twice -- once as named constants a caller branches
# on, once as a tuple a reader iterates -- is two statements of one fact, and
# the failure mode of their disagreeing is silent. A member missing from the
# tuple is a value inferences.matching_call_mode can hold that no consumer
# enumerating the vocabulary will ever look for.
#
# A RuntimeError AND NOT AN `assert`: `python -O` deletes assert statements,
# and this is the only thing standing between a mistyped constant and a column
# whose stored values are outside its own documented vocabulary.
if set(MATCHING_CALL_MODES) != {MATCHING_CALL_MODE_GROUPED,
                                MATCHING_CALL_MODE_PER_TRIAL} or \
        len(MATCHING_CALL_MODES) != 2:
    raise RuntimeError(
        "MATCHING_CALL_MODES must hold exactly MATCHING_CALL_MODE_GROUPED and "
        f"MATCHING_CALL_MODE_PER_TRIAL; it holds {MATCHING_CALL_MODES!r}")


# THE PROCESS PIN, AND WHY IT LIVES HERE RATHER THAN AT ITS CALLER.
#
# `None` means "no pin: follow MATCHING_PER_TRIAL_CALLS_ENABLED", which is
# every ordinary process. A member of MATCHING_CALL_MODES means some caller has
# declared that THIS process runs that arm whatever the constant says.
#
# THE ONE CALLER TODAY IS THE FIXTURE HARNESS, and its need is not a
# preference. oncotriage/fixtures/capture.py's RecordingSink stamps
# `call_index = len(bucket)` under its lock, so a Stage 5 recording's index is
# its ARRIVAL ordinal -- deterministic while the stage is sequential and
# decided by the thread scheduler the moment it is not. The twelve
# characterization fixtures therefore characterize the GROUPED arm and can
# characterize no other, until the sink learns a trial-stable ordering. Before
# this pin the harness REFUSED per-trial mode outright, which was right while
# grouped was the default and becomes a self-inflicted outage the day the
# default flips: the free twelve-fixture replay gate -- the one thing that says
# the pipeline still does what it did -- would stop running at exactly the
# moment a large behaviour change landed.
#
# WHY THE PIN IS A NAME IN THIS MODULE AND NOT A WRITE TO THE CONSTANT. The
# harness could set MATCHING_PER_TRIAL_CALLS_ENABLED = False on this module for
# its own process and every consumer would follow, because they all read
# through matching_call_mode(). That is the shape this project keeps removing:
# a second writer of a declared configuration value, indistinguishable
# afterwards from the declaration itself, so `config.MATCHING_PER_TRIAL_CALLS_
# ENABLED` read anywhere -- a report, a log line, a future reader of this file
# -- would say the campaign was configured grouped when it was configured
# per-trial and overridden. The pin keeps the two facts apart: the constant
# still says what the project is configured to do, the pin says what this
# process was forced to do, and matching_call_mode() -- the ONE owner both
# consumers already read -- resolves them in one place with one rule.
#
# NOT AN ENVIRONMENT VARIABLE. Every ONCOTRIAGE_* name in oncotriage/settings.py
# is a deployment knob an operator sets; this is a declaration a PROGRAM makes
# about itself, and exporting it would let it leak into a batch run that never
# asked for it -- which is the campaign-corrupting direction.
_MATCHING_CALL_MODE_PIN = None


def matching_call_mode() -> str:
    """Which call mode Stage 5 runs in, read LIVE off this module.

    ONE OWNER, TWO CONSUMERS, WHICH IS THE WHOLE REASON THIS IS A FUNCTION.
    ``oncotriage/agent/evaluation.py`` decides how to partition the batch and
    ``oncotriage/storage/database_logger.py`` writes
    ``inferences.matching_call_mode`` on every row; if each read the constant
    for itself they could disagree, and the row would name a mode the node did
    not run. ``config.matching_wire_model()`` is the same shape for the same
    reason, and ``matching_provider``'s note in INFERENCE_COLUMN_ADDITIONS
    argues the general case: a constant that can move WITHIN a process must not
    be reached through a bound name.

    READ AT CALL TIME, NEVER CACHED. The writer calls it once per row and the
    node once per patient; both are far off any hot path, and caching would
    reintroduce exactly the staleness the function removes.

    THE PIN OUTRANKS THE CONSTANT, and that ordering is the only one that can
    be correct: a process that has pinned an arm is going to RUN that arm, so
    every consumer reporting on it -- the node's partition, the stored
    ``inferences.matching_call_mode``, the resume fingerprint, the tracking
    parameter -- must name the arm that ran and not the one that was
    configured. See ``pin_matching_call_mode``.
    """
    if _MATCHING_CALL_MODE_PIN is not None:
        return _MATCHING_CALL_MODE_PIN
    return (MATCHING_CALL_MODE_PER_TRIAL if MATCHING_PER_TRIAL_CALLS_ENABLED
            else MATCHING_CALL_MODE_GROUPED)


def pin_matching_call_mode(mode: str) -> "str | None":
    """Force ``matching_call_mode()`` for the rest of THIS process.

    Returns the pin that was in force before, so a caller can restore it --
    ``None`` when there was none, which is why the annotation admits it. (A
    QUOTED annotation and not a bare ``str | None``: this module declares
    ``requires-python = ">=3.10"``, where PEP 604 holds, but a quoted form
    costs nothing and cannot become the one line in config.py that refuses to
    import on an older interpreter -- and this file is imported by every entry
    point in the project.) The fixture harness never restores it: it
    pins once, for the life of the process, before anything reads the mode.

    RAISES ON AN UNRECOGNISED MODE rather than storing it. A pin is the one
    value in this module that no import-time check can validate -- it is set at
    run time by a caller -- and a typo stored here would put a string outside
    the closed vocabulary into ``inferences.matching_call_mode``, into the
    resume fingerprint and into the tracking index at once, where every
    consumer that enumerates ``MATCHING_CALL_MODES`` would silently fail to
    match it. A RuntimeError and not a ValueError, on the
    ``UnknownModelPricingError`` precedent, so a stray ``except ValueError``
    around a pipeline call cannot eat it.

    NOT THREAD-SAFE AND DELIBERATELY NOT LOCKED. A pin is a statement about a
    whole process, made once before its work starts -- the fixture harness
    calls this as the first statement of ``main()``. A lock here would suggest
    it is safe to flip mid-run, and it is not: the node reads the mode once per
    patient, so a flip between two patients of one campaign would put two arms
    into one artifact with nothing in it saying so. That is the fault the
    resume fingerprint's ``matching_call_mode`` field exists to catch BETWEEN
    runs, and nothing catches it within one.
    """
    global _MATCHING_CALL_MODE_PIN
    if mode not in MATCHING_CALL_MODES:
        raise RuntimeError(
            f"pin_matching_call_mode: {mode!r} is not a Stage 5 call mode. "
            f"MATCHING_CALL_MODES is {MATCHING_CALL_MODES!r}.")
    previous = _MATCHING_CALL_MODE_PIN
    _MATCHING_CALL_MODE_PIN = mode
    return previous


def clear_matching_call_mode_pin() -> "str | None":
    """Drop the pin, returning what it was (``None`` when there was none).

    Exists so a caller that pinned can put the process back -- a test does,
    inside ``try``/``finally``. Nothing in the pipeline calls it.
    """
    global _MATCHING_CALL_MODE_PIN
    previous = _MATCHING_CALL_MODE_PIN
    _MATCHING_CALL_MODE_PIN = None
    return previous


def matching_call_mode_pin() -> "str | None":
    """What is pinned right now, or ``None``. A DIAGNOSTIC, not an access path.

    ``matching_call_mode()`` is what a consumer reads. This answers the
    different question a REPORT asks -- "was the mode chosen or forced" -- which
    ``matching_call_mode()`` cannot, because it deliberately returns the same
    two strings either way. ``deps.peek`` is the same distinction one module
    over.
    """
    return _MATCHING_CALL_MODE_PIN



# ---------------------------------------------------------------------------
# Stage 1 query expansion
# ---------------------------------------------------------------------------

# Ceiling on distinct genomic variant terms that reach the expanded query and
# the R4 rerank query. R4 is scored by MedCPT, which was trained on 2-10 word
# PubMed queries, so an unbounded list of variants is a worse query, not a
# better one.
MAX_VARIANT_TERMS = 8


# ---------------------------------------------------------------------------
# Stage 5 patient record rendering
# ---------------------------------------------------------------------------

# How old a lab reading has to be before _create_patient_summary states its age
# in words as well as its date.
#
# The section is headed "Relevant Lab Values (most recent)" and holds exactly
# one row per lab concept, so a reading from 1997 sits in it beside readings
# from 2026 with nothing to separate them but a date in parentheses. Whether
# that date is stale is date arithmetic against DATA_SNAPSHOT_DATE, which the
# renderer can do for free and deterministically and the model demonstrably
# does not always do.
#
# 365 DAYS RATHER THAN A CALENDAR YEAR, and the difference is deliberate: the
# comparison is on whole days so it cannot depend on which side of a leap day
# the reading falls, while the PHRASE is in completed years, which is what a
# reader wants. One year is the threshold because essentially every haematology
# and organ-function bound in an oncology protocol is written against a current
# value -- "within 14 days of registration" is the usual window -- so a reading
# older than a year is never the current value whatever the exact cutoff, and a
# tighter cutoff would start annotating readings that are merely a few months
# old and say nothing useful by doing it.
STALE_LAB_AGE_DAYS = 365


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
        # ---- The same judge, served by Amazon Bedrock (MATCHING_PROVIDER) ---
        #
        # THREE KEYS BECAUSE THE WIRE MODEL ID IS THE PRICING KEY, and it is
        # the wire id that reaches here: inferences.matching_model records the
        # model that ANSWERED, read off the response, and get_model_cost() is
        # called with that value. So a Bedrock run prices against whichever of
        # these its BEDROCK_MATCHING_MODEL named. An id absent from this table
        # raises UnknownModelPricingError before a row is written, which is the
        # loud failure this project requires of an unpriced model -- it is not
        # a defect to be worked around by adding a fallback.
        #
        # RATES READ 2026-08-21 from the GPT-5.6 Terra model card
        # (docs.aws.amazon.com/bedrock/latest/userguide/
        #  model-card-openai-gpt-56-terra.html), Pricing section, per 1M tokens,
        # STANDARD tier -- the only tier this model supports. The card lists two
        # context-window bands and TWO ROUTING OPTIONS, and the choice of row is
        # argued rather than assumed:
        #
        #   * SHORT CONTEXT (272K) is what applies. A 15-trial Stage 5 prompt
        #     runs ~20k input tokens; MATCHING_INPUT_TOKEN_BUDGET is the packer's
        #     ceiling and is far below 272,000. The Long Context (1M) band --
        #     $4.40/$19.80 in-Region, $4.00/$18.00 global -- would apply to a
        #     request over the 272K threshold and IS NOT MODELLED, exactly as
        #     the note above records OpenAI's own 272K cliff not being modelled.
        #   * IN-REGION and GEO CRIS are the same price ($2.20/$13.20); GLOBAL
        #     CRIS is ~10% cheaper ($2.00/$12.00) because it may route anywhere.
        #
        # NOT MODELLED, for the same reason as the OpenAI row above: cache
        # reads bill at $0.22/1M (geo) and cache WRITES at 1.25x input, and
        # get_model_cost() takes an {input, output} pair with no cached term.
        # Turning explicit prompt caching on (BEDROCK_PROMPT_CACHE_MODE) makes
        # estimated_cost_usd an OVER-estimate rather than a wrong number, which
        # is the safe direction, but it stops being exact.
        #
        # bedrock-mantle, in-Region: the bare model id.
        "openai.gpt-5.6-terra": {
            "input": 2.20,
            "output": 13.20
        },
        # bedrock-runtime, geographic cross-Region profile.
        "us.openai.gpt-5.6-terra": {
            "input": 2.20,
            "output": 13.20
        },
        # bedrock-runtime, global cross-Region profile.
        "global.openai.gpt-5.6-terra": {
            "input": 2.00,
            "output": 12.00
        },
        # ---- Claude Sonnet 4.6, served by Amazon Bedrock Converse ---------
        #      (MATCHING_PROVIDER = "bedrock_anthropic")
        #
        # THE WIRE MODEL ID IS THE PRICING KEY, exactly as for the three rows
        # above: `inferences.matching_model` records the model this branch
        # reports, `get_model_cost()` is called with that value, and an id
        # absent from this table raises UnknownModelPricingError before a row
        # is written.
        #
        # READ THIS BEFORE TRUSTING A COST ON THIS BRANCH: ONE ROW IS MEASURED
        # AND THE OTHERS ARE INFERRED, and they are labelled individually
        # rather than as a block.
        #
        # MEASURED, 2026-08-30, from the AWS Marketplace listing the Claude
        # Sonnet 4.6 model card names as its own product (prod-ffvjxvh4ltq64),
        # per 1M tokens, all dimensions published as GLOBAL:
        #
        #     Input                $3.00      Response            $15.00
        #     Cache read           $0.30      Cache write (5m)     $3.75
        #     Cache write (1h)     $6.00      Batch in/out   $1.50 / $7.50
        #
        # NOT MODELLED, for the same reason as every row above: get_model_cost()
        # takes an {input, output} pair with no cached term, so a run with
        # prompt caching on prices its cached input at the FULL input rate.
        # That makes estimated_cost_usd an OVER-estimate rather than a wrong
        # number, which is the safe direction, and it stops being exact. It
        # matters more on this branch than on any other, because the per-trial
        # design's whole affordability rests on the cache: at $0.30 against
        # $3.00 the modelled figure can be ~10x the billed one for the cached
        # portion. Closing it means a third pricing term and is a change to a
        # 29-call-site signature.
        #
        # INFERRED, NOT MEASURED -- the geo and In-Region rows below. That
        # listing publishes Global dimensions only. The +10% premium is carried
        # over from the pattern this project already recorded for GPT-5.6 Terra
        # (geo $2.20/$13.20 against global $2.00/$12.00 on a $2.00/$12.00 base)
        # and is corroborated only by secondary sources. It is here rather than
        # absent because an absent row makes get_model_cost() raise and the
        # branch unable to write a row at all; it is labelled because a number
        # nobody measured must not read like one somebody did. VERIFY-AT-GO-LIVE
        # (A6) is the item that settles it against a console bill.
        #
        # bedrock-runtime, global cross-Region profile. MEASURED.
        "global.anthropic.claude-sonnet-4-6": {
            "input": 3.00,
            "output": 15.00
        },
        # bedrock-runtime, US geographic profile. THE SHIPPED DEFAULT. INFERRED.
        "us.anthropic.claude-sonnet-4-6": {
            "input": 3.30,
            "output": 16.50
        },
        # bedrock-runtime, EU geographic profile. INFERRED.
        "eu.anthropic.claude-sonnet-4-6": {
            "input": 3.30,
            "output": 16.50
        },
        # bedrock-runtime, AU geographic profile. INFERRED.
        "au.anthropic.claude-sonnet-4-6": {
            "input": 3.30,
            "output": 16.50
        },
        # bedrock-runtime, JP geographic profile. INFERRED.
        "jp.anthropic.claude-sonnet-4-6": {
            "input": 3.30,
            "output": 16.50
        },
        # bedrock-runtime, In-Region. Reachable in eu-west-2 alone. INFERRED.
        "anthropic.claude-sonnet-4-6": {
            "input": 3.30,
            "output": 16.50
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


# Pricing for the independent LLM rater (oncotriage/evaluation/rater.py), which
# calls a DIFFERENT vendor over the Message Batches API. It is a separate table
# from PRICING_CONFIG on purpose, and the reason is a contract rather than a
# preference: get_model_cost() takes an {input, output} pair per model and is
# read by 29 call sites plus the inferences.estimated_cost_usd column. Batch
# pricing needs four more terms -- a batch discount and three cache multipliers
# -- and widening that shape would change what every existing caller computes.
#
# Rates are Anthropic's published per-million-token list prices for the Claude
# API, read 2026-08-11. The multipliers are the documented cache economics: a
# 5-minute cache write costs 1.25x base input, a 1-hour write 2x, and a cache
# read 0.1x. batch_discount is the flat 50% the Message Batches API applies to
# ALL token usage, cached and uncached alike.
#
# An unpriced model RAISES in rater_pricing() rather than defaulting to zero,
# for the reason get_model_cost() gives: a zero-cost row cannot be told apart
# from a genuinely free run, and every aggregate over it under-reports by
# exactly the amount nobody noticed.
RATER_PRICING = {
    "last_updated": "2026-08-11",
    "batch_discount": 0.50,
    "cache_write_5m_multiplier": 1.25,
    "cache_write_1h_multiplier": 2.00,
    "cache_read_multiplier": 0.10,
    "models": {
        "claude-sonnet-4-6": {
            "input_per_mtok": 3.00,
            "output_per_mtok": 15.00,
        },
        "claude-opus-4-8": {
            "input_per_mtok": 5.00,
            "output_per_mtok": 25.00,
        },
        "claude-haiku-4-5": {
            "input_per_mtok": 1.00,
            "output_per_mtok": 5.00,
        },
    },
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

# BATCH_SIZE WAS HERE AND IS DELETED (pass 20f-2). It read
# `BATCH_SIZE = 200  # Patients per progress-reporting batch (does NOT limit
# total patients)`, and no reader existed anywhere in the repository -- check 2h
# of tests/test_package_invariants.py is what surfaced it, once pass 20e removed
# the shim whose re-export counted as a read.
#
# DELETED RATHER THAN WIRED IN, and the choice was made by reading the runner
# rather than by preference. oncotriage/batch/runner.py has no batch: it submits
# every pending patient to ONE ThreadPoolExecutor of MAX_WORKERS threads and
# reports progress through a tqdm bar that advances once per patient, in
# `run_batch` and again in `run_resample`. There is nothing for a batch size to
# size. Wiring it in would mean INVENTING a chunking layer whose only effect is
# to make the progress report coarser than the one that exists -- new behaviour,
# in a pass whose job was to make the configuration surface honest.
#
# The runner's own module docstring made the same promise ("Process patients in
# configurable batch sizes with progress reporting") and was corrected in the
# same commit. Every other constant in this section has a reader -- check 2h
# says so for the whole file, which is the only reason this one could be found.
#
# DO NOT NAME THE DELETED CONSTANT IN A DOCSTRING if you write about it
# elsewhere. Check 2h counts a name appearing in any STRING LITERAL as a read,
# deliberately, so that getattr(module, "NAME") is not mistaken for dead code.
# The first draft of the runner's docstring said `config.<the name>`, and the
# revert harness measured the consequence: putting the constant back with no
# reader and no exemption was NOT reported, because that sentence looked like
# its reader. This block is a COMMENT, which no AST walk sees.

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
# THE RUN SPEND GATE  (the spend-gate pass)
# ===========================================================================
#
# WHAT IT IS AND WHAT IT REPLACES. AWS Budgets, and every other provider-side
# budget alarm, is MONITORING: it observes a bill that has already been
# incurred and notifies 8 to 24 hours later. Nothing in this pipeline stopped a
# defect, a mis-set constant or a runaway loop from spending an account's whole
# balance inside one campaign, and the operator's only brake was the stop
# sentinel -- which requires a human who already knows something is wrong.
#
# THIS IS A PRE-CALL GATE. `oncotriage/spend.py` accumulates the MEASURED cost
# of every Stage 5 response as it arrives, and every billed call site checks the
# accumulated total against this cap BEFORE issuing. Crossing it behaves exactly
# as the operator stop switch behaves -- in-flight work completes and is
# written, nothing new starts, the checkpoint is current, the run is recorded
# STOPPED with a machine-readable reason, and a resume continues under the
# REMAINING budget.
#
# THE CAP IS A CAMPAIGN BUDGET AND NOT A PER-INVOCATION ALLOWANCE, which is the
# whole reason `oncotriage/storage/database_logger.py:campaign_spend_before`
# exists: a resumed run seeds its ledger with what its predecessors already
# spent, read out of `inferences.estimated_cost_usd`. Without that, a run that
# tripped the cap and was restarted by a supervisor would get a fresh $300 every
# time, which is the failure mode a per-run cap has and a campaign cap does not.

SPEND_CAP_USD = 300.00
"""The most one campaign may spend on billed Stage 5 calls, in US dollars.

THE VALUE IS AN OPERATOR RULING, RECORDED HERE WITH ITS REASONING. The ruled
evaluation program is a 300-patient campaign, a 50-patient k=2 stability re-run,
and a 100-patient judge pass. The cap sits above that program with headroom
while bounding a runaway well inside the account's credit balance.

THE PROGRAM'S COST, DERIVED RATHER THAN ASSUMED -- and the derivation is here
rather than in a note somewhere because the cap is only defensible beside it.

    MEASURED INPUTS, all from artifacts in this repository, none invented:

      S  = 8,575  tokens   the shared system prefix, mean over the ELEVEN
                           characterization fixtures that carry a Stage 5
                           exchange (prompt_tokens minus the user block at the
                           4.15 chars/token this corpus actually measures).
      u  =   372  tokens   the per-trial user block: 1,544 characters mean per
                           trial across the same eleven, at the same ratio.
      o  =   696  tokens   output per trial, mean over the same eleven
                           (completion_tokens / distinct NCT ids in the call).
      N  =  MAX_TRIALS_FOR_EVALUATION.
      rates: $2.00 / $12.00 per 1M in/out from PRICING_CONFIG's gpt-5.6-terra
             row, and $0.20 per 1M for CACHED input, which that row records as
             published and deliberately NOT modelled by get_model_cost().

    PER PATIENT, PER-TRIAL MODE, CACHE WORKING:

      warmup            S x $2.00/1M                       = $0.017150
      each trial        S x $0.20/1M                          0.001715
                      + u x $2.00/1M                          0.000744
                      + o x $12.00/1M                         0.008352   = $0.010811
      15 trials                                                          = $0.162240
      TOTAL                                                              = $0.179390

    PER PATIENT, CACHE **NOT** WORKING -- the failure the three-call probe
    exists to settle, which raises nothing and is visible only as a zero in
    `inferences.llm_classifier_call_details`:

      each trial      (S + u) x $2.00/1M + o x $12.00/1M     = $0.026246
      TOTAL           warmup + 15 trials                     = $0.410840   (2.29x)

    THE RULED PROGRAM, both arms. THE RESAMPLE PASS IS INCLUDED and is easy to
    forget: `25- Batch Runner.py` re-runs RESAMPLE_COUNT (100) already-completed
    patients after the main pass, at full price, so a "300-patient campaign" is
    400 patient-runs.

                                      cache working    cache absent
      300-patient campaign               $53.79          $123.25
      + resample pass (100)              $17.93           $41.08
      50-patient k=2 re-run (100 runs)   $17.93           $41.08
      100-patient judge pass              $7.50            $7.50
      ------------------------------------------------------------
      PROGRAM                            $97.15          $212.91

    So the cap sits at ~3.1x the expected program and ~1.4x the program's own
    worst case. It is NOT exceeded by the derived estimate in either arm, which
    is the finding rather than the assumption -- and the second column is why
    the number is 300 and not 150.

    **AND THE JUDGE ROW IS NO LONGER INSIDE WHAT *THIS* CONSTANT BOUNDS.** The
    operator ruling that split the budgets moved it to RATER_SPEND_CAP_USD, so
    the program THIS cap governs is $89.65 / $205.41 -- the table above less
    the judge -- and the multiples are ~3.3x expected and ~1.5x worst case. The
    table is left whole because it is the PROGRAM's cost and a reader deciding
    what to spend needs all four rows; what changed is which of them this
    number is compared against. `spend.report_lines()` prints both budgets on
    every run, so the split cannot be read out of one figure and assumed.

WHAT THE JUDGE PASS ROW IS AND WHY IT IS THE SOFTEST NUMBER HERE. The rater
(`oncotriage/evaluation/rater.py`) calls a DIFFERENT vendor through the Message
Batches API, priced from RATER_PRICING rather than PRICING_CONFIG, at
claude-sonnet-4-6's $3.00/$15.00 with the flat 50% batch discount. The row above
assumes 25,000 input and 5,000 output tokens per patient; at 40,000/8,000 it is
$12.00. Either way it is under 10% of the program and it moves no decision here.

**THE JUDGE PASS IS COVERED, AND THIS PARAGRAPH ONCE SAID THE OPPOSITE.** It
read: "the gate instruments Stage 5, which is the batch runner's spend and
nothing else. `rater_run.py` and `ragas_run.py` bill through their own
harnesses, do not write `inferences.estimated_cost_usd`, and are not gated."
Every clause was true when it was written and the LAST one was the hole: a
budget that covers one door of a building with four is not a budget. The
spend-coverage pass closed it. Four billed paths now charge one ledger and are
declined by a cap -- Stage 5, Stage 2's dense query embedding, the independent
rater and the ragas harness -- each PRICED BY ITS OWN TABLE at its own call
site and LIMITED here. `spend.SPEND_SOURCES` enumerates them and
`spend.BILLED_SITES` maps every billed call site in the repository to `gated
here` / `gated upstream` / `exempt, and here is why`.

**"A CAP" AND NOT "ONE CAP", WHICH IS WHAT THIS PARAGRAPH SAID.** The judge is
bound by `RATER_SPEND_CAP_USD` and everything else by this constant -- an
operator ruling, argued at `spend.SPEND_BUDGETS`. So THIS number bounds the
campaign and the things that run beside it (Stage 5, the query embedding, both
ragas paths) and it does NOT bound the rater. `spend.report_lines()` prints
every budget, spent and remaining, on every run, so the split cannot be read
out of one figure and assumed.

WHAT IT STILL DOES NOT BOUND, and it is named rather than implied: an index
build, the index validator's one diagnostic embedding, `bedrock_probe.py`'s
deliberate flagged spend, and the rater's free `count_tokens` call. Each is
argued at `spend.BILLED_SITES` and PRINTED by `spend.report_lines()` on every
run, so a reader handed "$300 cap" is told in the same block what it does not
reach.

AND IT BINDS WITHIN ONE PROCESS. A campaign's ledger is seeded from the `runs`
chain and a rater session's from its own state file, so each RESUMES under its
remainder -- but the two are separate processes with no shared store, so the
judge's spend is not netted against the campaign's. That is a real gap and the
only honest place to record it is here.

UNSET SEMANTICS, AND THE CHOICE IS ARGUED RATHER THAN DEFAULTED. `None` here
means NO CAP. That is the shape a silently-unlimited default would take, and
this project removes exactly that class of defect -- so the DEFAULT IS A NUMBER,
not None, and the unlimited state is reachable only by an explicit edit that
`oncotriage/spend.py:describe_cap()` PRINTS on the run banner of every run that
takes it. The alternative -- refusing to run without a cap -- was rejected by
measurement rather than by taste: every offline test harness, every fixture
replay and every embedder that calls `main()` would then have to set one, and a
gate that makes the free paths fail is a gate somebody disables.

A CAP OF ZERO IS NOT "UNSET". It is a cap of zero dollars and it stops the run
before its first billed call, which is a legitimate thing to ask for (a dry
rehearsal of the whole pipeline's non-billed path). `spend.spend_cap()` refuses
a negative value at import rather than reading it as unlimited.

THE WORST-CASE OVERSHOOT IS BOUNDED AND IS STATED HERE, because a cap with an
unstated edge is a promise nobody can rely on.

    Every billed call is bracketed: the gate is checked immediately BEFORE the
    request and the ledger is charged immediately AFTER the response. So the
    only spend a trip cannot prevent is what is already past the gate and not
    yet charged, which is exactly the set of requests in flight:

        overshoot_requests  <=  MAX_WORKERS x per_trial_parallel_bound()
                            =   12 x 4  =  48                (per-trial mode)
        overshoot_requests  <=  MAX_WORKERS x 1  =  12       (grouped mode:
                                the send loop is sequential per patient)

    At the per-request costs derived above:

        per-trial, cache working   48 x $0.010811 = $0.52
        per-trial, cache absent    48 x $0.026246 = $1.26
        grouped                    12 x one packed chunk

    So the cap is honoured to within about a dollar and a half, on a $300 cap.
    THIS IS THE BOUND THE DESIGN BUYS BY CHARGING AT THE RESPONSE RATHER THAN
    AT THE PATIENT. Charging only where the node folds its accumulators would
    make the bound MAX_WORKERS whole patients (~$5), because per-trial mode
    dispatches a patient's entire wave before the node reads any of it.
"""

RATER_SPEND_CAP_USD = 50.00
"""The most one JUDGE SESSION may spend, in US dollars. Its OWN budget.

THE VALUE IS AN OPERATOR RULING AND SO IS THE SPLIT ITSELF. Budgets are per
billed PROGRAM, not one number for all: `SPEND_CAP_USD` bounds the campaign and
everything that runs beside it, and this bounds the independent rater. The
ruled judge pass is 100 patients and is estimated at UNDER $10 at
`SPEND_CAP_USD`'s own softest row -- $7.50 at 25,000 in / 5,000 out per
patient, $12.00 at 40,000 / 8,000 -- so $50 bounds it with headroom while
stopping a runaway inside a fifth of the campaign's cap.

WHY THIS IS A SEPARATE NUMBER AND NOT A SHARE OF THE CAMPAIGN'S, and the
argument is about what the two bound rather than about tidiness:

  * THEY ARE DIFFERENT PROGRAMS AN OPERATOR RUNS AND STOPS SEPARATELY. The
    judge is a `rater_run.py` invocation against a finished campaign's runs; a
    campaign is `25- Batch Runner.py`. Neither's budget should be able to
    starve the other, and under one shared number a campaign that ran long
    would silently leave the judge nothing -- a stop whose cause is in another
    program's ledger, which is the hardest kind to diagnose.
  * THEY ARE DIFFERENT VENDORS ON DIFFERENT PRICE TABLES. Stage 5 is priced
    from `PRICING_CONFIG`; the rater is priced from `RATER_PRICING` at
    claude-sonnet-4-6's $3.00/$15.00 with the flat 50% batch discount. One
    number over two tables is a number whose meaning depends on which of them
    moved.
  * THEY ALREADY RESUME FROM DIFFERENT STORES. A campaign seeds its ledger
    from the `runs` chain (`database_logger.campaign_spend_before`); a rater
    session seeds from its own `rater_state.json`
    (`rater.rater_spend_before`). Two chains were already being compared
    against one cap, which is the conflation `spend.SPEND_BUDGETS` removes.

WHAT THE SPLIT DOES **NOT** BUY, stated so nobody reads more into it than is
there: it does not net the judge's spend against the campaign's either. It
never did -- the two are separate processes with no shared store -- and the
split makes that honest instead of accidental. See `spend.SPEND_BUDGETS`.

WHAT THE OVERSHOOT IS HERE, and it is COARSER than Stage 5's. The Batches API
puts the gate and the charge further apart: one `batches.create` commits up to
`rater.MAX_REQUESTS_PER_BATCH` requests and reports no usage until they are
collected, so the smallest unit this cap can decline is a whole batch. Stage
5's bound is "the requests in flight"; this one's is "one batch", and it is the
number an operator needs when choosing this value.

None means NO RATER CAP -- `SPEND_CAP_USD`'s unset semantics, for its reason,
and `spend.describe_rater_cap()` prints the state it takes on the banner of
every judge session including the unlimited one. A cap of zero is a cap of zero
and stops the session before its first batch; a negative value RAISES rather
than being read as unlimited.
"""

SERVING_SPEND_CAP_USD = 25.00
"""The rolling-window budget a LONG-LIVED SERVING PROCESS runs under, in USD.

**A CAMPAIGN CAP CANNOT BOUND A SERVER AND THE SHIPPED GATE PROVED IT IN BOTH
DIRECTIONS.** `oncotriage/api/server.py` and `mcp_server.py` charge the same
ledger `25- Batch Runner.py` does, and neither writes a `runs` row -- so
nothing seeds the ledger, nothing resets it, and `SPEND_CAP_USD` compared
against a monotone total gives a server no brake at all until it has spent a
whole campaign's budget by itself, and then declines every request it will ever
serve. The remedy an operator reaches for is a restart, which empties the
ledger and hands the process a fresh unbounded budget -- the brake is off
exactly when it was working. See `spend.SPEND_POLICIES`.

So a server is bounded by a RATE: at most this many dollars inside any
`SERVING_SPEND_WINDOW_SECONDS`. It is bounded (a runaway request loop stops
within one window's spend), it self-heals (a server recovers on its own as the
window rolls, with no restart and no operator), and it cannot be defeated by a
restart loop, because restarting empties the window -- which is what waiting
would have done anyway.

WHERE 25.00 COMES FROM, and it is a RULING rather than a derivation, stated as
one. The measured cost of one served patient is $0.179 with the prompt cache
working and $0.411 without it (the derivation is at SPEND_CAP_USD). So this
window admits roughly

    $25 / $0.411  =  60 patients per hour   worst case
    $25 / $0.179  = 139 patients per hour   cache working

which is far above any demonstration load this project has ever served and far
below a runaway: a defect issuing requests as fast as the event loop's pool can
dispatch them is stopped inside one window. It is deliberately NOT derived from
an expected request rate, because nobody has measured one -- an operator
serving real traffic must set it from their own, and the banner prints it on
every start so the number cannot be inherited silently.

None here means NO SERVING CAP, reachable only by an explicit edit that
`spend.describe_serving_cap()` prints on the startup banner of every server that
takes it -- `SPEND_CAP_USD`'s unset semantics, for its reason.
"""

SERVING_SPEND_WINDOW_SECONDS = 3600.0
"""The width of the rolling window `SERVING_SPEND_CAP_USD` is measured over.

ONE HOUR, AND THE TWO FAILURE MODES OF THE OBVIOUS ALTERNATIVES ARE WHY. A
window much SHORTER than one served patient's latency (a patient is ~78s of
Stage 5 today) would let a handful of concurrent requests fill it, so a server
would decline for normal load; a window much LONGER approaches the monotone
total this design exists to escape -- a 24-hour window on a server that spent
its budget at 09:00 declines until 09:00 tomorrow, which an operator
experiences as the broken shape rather than the self-healing one.

MEASURED IN SECONDS, AND THE CLOCK IS `time.monotonic`: an NTP step or a DST
change must neither empty the window (a free budget) nor fill it (refusals for
money nobody spent). See `spend.SpendLedger._commit`.

ITS SECOND JOB IS TO BOUND MEMORY. `spend.SpendLedger` keeps one small tuple
per charge and prunes against this value on every write, so a server that runs
for months holds one window's worth of events rather than one process
lifetime's.
"""

SPEND_CAP_ENFORCED = True
"""Whether the cap above stops a run, or is only measured and reported.

FALSE IS A MEASUREMENT MODE, NOT A DISABLE. The ledger still accumulates, the
run banner prints the cap and says it is measured only, and the closing spend
block still reports the campaign total against it -- so an operator can read
whether the run WOULD have been stopped, and by how much. What changes is that
no request is declined.

**AND `spend.SPEND_GATE_SKIPS` IS THEREFORE EMPTY IN THIS MODE, WHICH THIS
PARAGRAPH ONCE CLAIMED THE OPPOSITE OF.** That counter counts requests the gate
DECLINED; with nothing declined there is nothing to count, and a counter that
moved here would be counting a decline that did not happen. The closing block is
the reader for this mode, not the counter. The claim was wrong and is corrected
rather than deleted, because "the counter still moves" is exactly what somebody
would rely on when deciding this mode is safe to run a campaign under.

It exists so a first campaign can be run with the gate OBSERVING before it is
trusted to stop one, which is the only honest way to calibrate a threshold
nobody has yet seen fire.

IT DEFAULTS TO TRUE because the reverse default is the one that reads as a
working brake and is not one.
"""

SPEND_CALL_CEILING_ENFORCED = True
"""Whether the per-invocation billed-call ceiling stops a run. See
``spend.stage5_call_ceiling()`` for what the ceiling is and how it is derived,
and the block below for why this is a call CEILING rather than the
calls-per-minute detector the brief asked for.

WHY THERE IS NO CALLS-PER-MINUTE BREAKER, WITH THE ARITHMETIC.

  A rate detector needs a threshold in calls per minute. Every candidate
  derivation of one from this configuration needs a LATENCY term, and this
  project owns no latency constant -- MATCHING_REQUEST_TIMEOUT_SECONDS is a
  ceiling, not an expectation, and the only measured figure (78.5s per patient
  over 205 recorded evaluation runs) is a GROUPED-arm number for a whole
  patient. A threshold built on an assumed per-request latency is a literal
  wearing a derivation's clothes, and it fires on a fast provider day.

  AND THE RATE IS ALREADY BOUNDED, STRUCTURALLY. Every billed Stage 5 request
  goes through one of three call sites, and each runs either on the node's own
  thread (sequential) or on a wave pool of at most `per_trial_parallel_bound()`
  workers. With MAX_WORKERS patients in flight the process cannot hold more than

      MAX_WORKERS x per_trial_parallel_bound()  =  12 x 4  =  48

  requests in flight at any instant, whatever a defect does. A retry loop does
  not raise that number -- it raises the COUNT, which is what the cap governs --
  and `MAX_LLM_CLASSIFIER_RETRIES` bounds node re-entry at 4 anyway.

  SO A RATE BREAKER COULD NOT SAVE MONEY THE CAP DOES NOT ALREADY SAVE. It
  would trip at the same three sites with the same 48-request overshoot bound;
  the only thing it buys is stopping SOONER in wall-clock, and the money spent
  in the meantime is bounded by the cap the operator has already ruled
  acceptable.

  WHAT IS *NOT* REDUNDANT, and is what this constant governs instead. The named
  failure mode is "a defect that re-issues calls" -- a loop that appends to the
  send queue without popping, a splitter that never converges. Against THAT the
  cap is a poor instrument, because it lets one patient spend the entire
  campaign budget. The number of billed calls one Stage 5 invocation can
  LEGITIMATELY make is exactly derivable from this file -- see
  `spend.stage5_call_ceiling()` -- so the breaker is a ceiling on that count
  rather than on a rate, it needs no latency term, and it bounds a runaway at
  one patient's worth of calls instead of the cap's.
"""




#------------------------------------------------------------------------------


# ===========================================================================
# SQLITE WRITE DURABILITY (the write-durability pass)
# ===========================================================================
#
# WHAT THESE ARE FOR. oncotriage/storage/database_logger.py catches
# sqlite3.Error, prints "Database logging failed (non-critical)" and continues,
# because a logging fault must not destroy a ~70-second pipeline result. The
# consequence is that a lost row is invisible: the patient is recorded as
# successful and the run reports complete. _WRITE_LOCK closes the IN-PROCESS
# race; these four constants are about everything the lock cannot reach, which
# is every OTHER process writing the same file.
#
# WHO ELSE WRITES IT, established by reading rather than assumed:
#   - oncotriage/batch/runner.py, MAX_WORKERS threads, one process;
#   - oncotriage/api/server.py, from loop.run_in_executor(...), one process;
#   - NOT the Airflow DAG. Its three tasks are scrape_and_save, rebuild_index
#     and verify_index (oncotriage/orchestration/dag_generator.py); all three
#     delegate to oncotriage.retrieval.indexer, which touches Qdrant and never
#     opens inferences.db.
# So the multi-process case is one batch run and one live API server on one
# machine, which is exactly the configuration the paper's final run uses.

# The journal mode applied to the inference database, verified after it is set.
#
# WAL, because a threading.Lock serializes one process and this file has two
# writers. Under the default rollback journal a reader blocks a writer and a
# writer blocks a reader, so a dashboard refresh or a File 16 query landing on
# the same file as a batch write is a "database is locked" away from a lost row.
# WAL lets one writer and any number of readers proceed at once.
#
# IT IS A PROPERTY OF THE FILE, NOT OF THE CONNECTION, so setting it once
# converts the database permanently -- and it can silently fail to take, which
# is why database_logger reads the pragma back and records a degradation naming
# the mode it actually got. The two cases that fail are a network filesystem
# (WAL needs shared memory the mount cannot provide) and a read-only directory.
#
# SET IT TO "DELETE" TO OPT OUT. That is the escape hatch for a network share,
# and it is why this is a tunable rather than a literal in the writer.
SQLITE_JOURNAL_MODE = "WAL"

# The page size a database this project CREATES is built with, in bytes.
#
# 16384 rather than SQLite's 4096 default. `inferences` is a wide row -- 60-odd
# columns, several of them long TEXT (the rendered Stage 5 prompt, the call
# ledger, the criterion blobs) -- so at 4096 a single row spills across an
# overflow chain and every read of it is several page fetches. A larger page
# holds the row inline, and it holds proportionally more index entries per page,
# which is what the child-lookup index and the three added on `inferences` walk.
#
# TWO ORDERING FACTS DECIDE WHERE IT IS APPLIED, and both were MEASURED rather
# than read off the documentation (see the comment at the pragma in
# oncotriage/storage/database_logger.py):
#
#   1. IT MUST BE ISSUED BEFORE `journal_mode = WAL`. Issued after, it is
#      SILENTLY IGNORED -- the pragma returns no error and the database keeps
#      4096. Measured both orders on sqlite 3.45.3: page_size-then-WAL gives
#      16384, WAL-then-page_size gives 4096.
#   2. IT IS INERT ON A DATABASE THAT ALREADY HAS PAGES. That is the designed
#      outcome, not a failure: changing an existing file's page size needs a
#      full VACUUM, which rewrites the whole database, and this project's
#      migrations are additive and never rewrite. So the production file keeps
#      whatever it was created with until it is replaced -- which is what the
#      fresh-campaign-database procedure is for.
#
# A TUNABLE RATHER THAN A LITERAL for the reason SQLITE_JOURNAL_MODE is one: an
# operator on unusual storage may want the default back, and 4096 here restores
# it exactly.
SQLITE_PAGE_SIZE = 16384

# Seconds a connection waits for a lock held by another connection before
# raising "database is locked".
#
# Python's sqlite3 defaults to 5.0, which tests/test_package_invariants.py
# section 5e records measuring -- but a default nobody chose is not a decision,
# and it is the wrong size here. The competing writer is the API server, whose
# critical section is a multi-row INSERT + commit, and the waiting writer is a
# batch worker that has already spent ~70 seconds and one paid Stage 5 call on
# the row it is trying to store. Waiting 30 seconds to save that is cheap; the
# only thing this bounds is how long a genuinely stuck database is tolerated
# before it is reported.
SQLITE_BUSY_TIMEOUT_SECONDS = 30.0

# Total attempts at the inference write, including the first.
#
# Small on purpose: the retry is for CONTENTION, which is transient by nature,
# and database_logger only retries the transient class (see _is_retryable there
# -- a schema or integrity error is retried zero times). Four attempts with the
# backoff below spans about a second and a half of contention on top of the
# 30-second busy timeout each attempt already carries.
SQLITE_WRITE_MAX_ATTEMPTS = 4

# Base seconds for the exponential backoff between those attempts: the Nth
# retry sleeps BASE * 2**(N-1), so 0.05 gives 0.05, 0.1, 0.2.
SQLITE_WRITE_RETRY_BASE_DELAY = 0.05


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


# The maximum number of alive cancer patients the cohort is capped at.
#
# THIS IS THE SIZE EVERY PUBLISHED NUMBER IS COMPUTED OVER. File 05 deletes
# non-cancer patients, then deceased cancer patients, then samples this many of
# the survivors to KEEP and unlinks the rest -- irreversibly, in place. So a
# reader asking "how large was the cohort" and a reviewer asking "what did the
# ablation study draw from" are asking about this number, and it lived as a
# module-level literal in oncotriage/fhir/clean.py where neither could see it
# and where oncotriage/tracking.py could not log it.
#
# The value is unchanged at 1,000. The manifest File 05 writes records it under
# `cap`, and the per-file deletion reason it writes still reads
# "alive cancer patient beyond CAP=1000 (seed=42)" -- the manifest is a
# historical record format and its strings are byte-identical for an unchanged
# value, which is why the reason string keeps the word CAP even though the
# identifier it once named now lives here under a different one.
#
# PROVENANCE OF THE NUMBER ITSELF: 1,000 IS THE OPERATOR-RULED CAMPAIGN SIZE.
# It is a decision about how much money one campaign spends -- at the measured
# $0.13-$0.17 per patient it is roughly $130-$170 of Stage 5 per full run --
# and it is NOT derived from a power calculation, a coverage target or any
# statistic. There is nothing outstanding to re-derive it against, and that is
# what makes it different from the two sample sizes below: those two feed
# significance tests whose resolving power depends on n, so they carry the
# uncalibrated label and an instrument to re-derive them with. This one is the
# ruling, and the honest thing a reader needs is to know that it is one.
COHORT_CAP = 1000

# Seed for the reproducible down-sample to COHORT_CAP patients.
#
# NOT File 04's Synthea seed and NOT the ablation study's: this one decides
# WHICH alive cancer patients survive the cap, so two corpora generated from
# one Synthea seed still differ if this moves. clean.py seeds a local
# random.Random instance with it rather than random.seed(), so it shifts no
# other consumer of `random` in the same session.
#
# It is not CLI-overridable -- "05- FHIR Clean Data.py" takes only --dry-run --
# which is why it, and COHORT_CAP, are in tracking.CONFIGURATION_PARAM_NAMES
# while the two sample sizes below are not.
COHORT_SELECTION_SEED = 42


#------------------------------------------------------------------------------


# ===========================================================================
# ABLATION STUDY CONFIGURATION (File 26)
# ===========================================================================

# Default patient count for one ablation study, before --sample-size.
#
# NAMED WITH ITS PREFIX RATHER THAN AS A BARE `SAMPLE_SIZE_DEFAULT`, which is
# what it was called inside oncotriage/ablation/study.py. This file is one flat
# namespace shared by every module in the project, and three unrelated things
# here sample: the cohort cap above, the evaluation slice below, and this. A
# bare name that reads unambiguously inside one module reads as "the" sample
# size here, and the next person to want one takes the name.
#
# IT IS DELIBERATELY ABSENT FROM tracking.CONFIGURATION_PARAM_NAMES. See the
# argument written at that tuple: `--sample-size` overrides it, and logging a
# default the run did not use is a false record.
#
# PROVENANCE OF THE NUMBER ITSELF: UNCALIBRATED, AND THE INSTRUMENT EXISTS.
# 75 is a holding value. It was not solved for a target effect size, and the
# project HAS the calculation that would solve for one -- the Power block that
# `python "27- Ablation Analysis.py"` prints, which reports the smallest effect
# this design resolves at the n it actually paired, at ABLATION_POWER_TARGET,
# under both the loosest and the strictest BH thresholds in the family.
#
# RE-DERIVE IT AGAINST THAT BLOCK BEFORE PUBLISHING ANY ABLATION COMPARISON.
# The reason this is not deferred quietly is that an n chosen by nobody has a
# specific failure mode: the study runs, every configuration produces a mean,
# the deltas are printed, and the ones that do not reach significance are
# indistinguishable from effects that are genuinely absent. The MDE block is
# what separates those two, and reading it AFTER the money is spent is reading
# it too late to change n.
ABLATION_SAMPLE_SIZE_DEFAULT = 75

# Seed for the ablation study's stratified draw.
#
# It is IN tracking.CONFIGURATION_PARAM_NAMES, because no flag overrides it --
# "26- Ablation Study.py" has --sample-size, --summary-only, --configs,
# --fresh-start and --db, and no --seed. If a --seed flag is ever added, this
# constant must LEAVE that tuple on the same day, for the same reason
# ABLATION_SAMPLE_SIZE_DEFAULT is not in it.
ABLATION_SEED = 42


#------------------------------------------------------------------------------


# ===========================================================================
# EVALUATION RUN CONFIGURATION (evaluation_run.py)
# ===========================================================================

# Default patient count for one evaluation slice, before --select.
#
# Prefixed for the same reason ABLATION_SAMPLE_SIZE_DEFAULT is: it was
# `DEFAULT_SELECTION_SIZE` inside oncotriage/evaluation/run_harness.py, which
# is unambiguous in that module and says nothing at all here.
#
# ALSO DELIBERATELY ABSENT FROM tracking.CONFIGURATION_PARAM_NAMES: --select
# overrides it. (The evaluation harness does not open a tracking run today; the
# omission is stated so that adding one cannot quietly make this a false
# record.)
#
# PROVENANCE OF THE NUMBER ITSELF: UNCALIBRATED, AND ITS INSTRUMENT IS NOT THE
# ABLATION ONE. 10 is a holding value, chosen as a slice a human rater can
# actually work through by hand rather than solved for anything. The ablation
# study's MDE block does not apply to it: that solves a PAIRED test across
# configurations over one cohort, and this slice feeds rating and Ragas, whose
# statistic is an agreement RATE over patient-trial decisions -- a different
# unit, a different n, and a different calculation.
#
# RE-DERIVE IT BEFORE PUBLISHING ANY RATE COMPUTED OVER THE SLICE, against the
# precision that rate needs: an agreement figure over ten patients carries a
# confidence interval wide enough to contain most of the values anyone would
# argue about, and `oncotriage/evaluation/rater.py` already records the
# neighbouring version of this warning in its own words -- that an agreement
# figure at one n cannot be used to argue about a value at another. Nothing in
# the project computes that interval today, which is exactly why the number
# carries the label rather than a citation.
EVALUATION_SELECTION_SIZE_DEFAULT = 10


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


# Seed for the percentile-bootstrap confidence intervals in the comparison
# table.
#
# ONLY THE SEED MOVES HERE. The 1,000 resamples and the 2.5/97.5 percentile
# pair stay local to oncotriage/ablation/analysis.py: they are statistical
# convention (a two-sided 95% percentile interval), not knobs an operator
# tunes, and promoting them would invite exactly the tuning that makes an
# interval mean nothing. The seed is different in kind -- it is the reason two
# runs of the analysis over one database produce byte-identical intervals, so
# it is a reproducibility fact and belongs where a reviewer can read it.
#
# It is in tracking.CONFIGURATION_PARAM_NAMES: nothing overrides it, and
# "27- Ablation Analysis.py" has no seed flag.
ABLATION_BOOTSTRAP_SEED = 42


#------------------------------------------------------------------------------
# S3 STAGING
#------------------------------------------------------------------------------
# The knobs the staging pass reads: the region (with its default and its
# resolved source), the scan prefix bound, and the two prices under S3_PRICING.
# They are here rather than beside the code that reads them because this file
# is where CLAUDE.md tells an operator every tunable lives, and because the two
# prices are the kind of number that goes stale on a vendor's schedule rather
# than on this project's.
#
# THIS COMMENT USED TO SAY "the four knobs" AND COUNTED THEM. A prose count of
# a set that grows every pass is a guaranteed staleness site -- the exec-chain
# note in CLAUDE.md went stale three times exactly that way -- so it names them
# instead.


# The target region. us-east-1, per the brief.
#
# NOT DERIVED FROM THE AMBIENT AWS CONFIGURATION, deliberately. boto3 will
# happily take a region from ~/.aws/config or AWS_DEFAULT_REGION, so a bucket
# created without an explicit region lands wherever the operator's shell
# happened to point -- and a bucket's region is fixed for its lifetime. The
# staging preflight compares the resolved session region against this constant
# and refuses a mismatch rather than creating a bucket in the wrong continent.
#
# THE OVERRIDE IS THE REMEDY THAT REFUSAL DID NOT HAVE. Refusing is right and
# it is a dead end when the only way past it is editing a tracked file: an
# operator whose account, data-residency rule or existing bucket is outside
# us-east-1 could not run the tool at all without a commit that then follows
# every other machine. ONCOTRIAGE_S3_STAGING_REGION is that remedy, and the
# refusal now names which of the two decided the expected value.
#
# THE OVERRIDE DOES NOT DISSOLVE THE REFUSAL -- it moves the comparison's
# expected side, and the comparison still happens. Setting the variable to the
# session's region is a DECLARATION that this is the intended region, which is
# exactly what the check wants and is not the same as reading the ambient
# configuration and agreeing with whatever it says.
S3_STAGING_REGION_DEFAULT = "us-east-1"

S3_STAGING_REGION, S3_STAGING_REGION_SOURCE = settings.resolve_s3_staging_region(
    S3_STAGING_REGION_DEFAULT)
# The resolved region and WHERE IT CAME FROM: the variable name, or None when
# S3_STAGING_REGION_DEFAULT applied. `oncotriage/staging/s3_sync.py` renders
# the source in its wrong-region refusal. Resolved at import for the reason
# written at BEDROCK_REGION above, and driven by subprocess in the standing
# test for the same reason.


# How much of each file the secrets scan reads.
#
# 64 KiB. THIS IS A STATED LIMIT AND NOT A GUARANTEE: a key pasted 100 KB into
# a 21 MB FHIR bundle is not seen. The bound is what makes a scan of ~60 GB
# take seconds rather than an hour, and it is here so the trade can be changed
# without editing oncotriage/staging/secrets_scan.py. Raising it to 0 would
# mean "read the whole file" and is the honest setting for a paranoid run over
# a small tree.
S3_STAGING_SCAN_PREFIX_BYTES = 65536


# THE TWO S3 RATES AND THE DATE THEY WERE READ, ON PRICING_CONFIG'S SHAPE.
#
# THEY WERE TWO BARE SCALARS AND A DATE IN A COMMENT, and the date being in a
# comment is the whole defect: a cost estimate whose inputs nobody can date is
# a number somebody trusts a year later, and a date no program can read is a
# date no report can print. PRICING_CONFIG solved this for the model rates in
# exactly this shape -- a `last_updated` FIELD beside the rates -- and
# `manifest.render_report` now prints it beside the dollar figures, so the age
# of the number is in front of the reader at the moment the decision is made
# rather than in the source file they are not reading.
#
# ONE DATE FOR BOTH RATES because both were read from the same page in one
# sitting. If a future pass updates one rate and not the other, the honest move
# is a per-rate date, not a stale shared one -- and the standing test's shape
# check is what makes that a decision rather than an accident.
#
# QUOTED, NOT COMPUTED. AWS S3 pricing page, us-east-1, read 2026-08-22:
#   standard_usd_per_gb_month  S3 Standard storage, first 50 TB, per GB-month
#   put_usd_per_1000           PUT / COPY / POST / LIST, per 1,000 requests
#
# Every object costs one PUT on the first sync. That is a ONE-TIME charge and
# the report labels it as such -- folding it into a monthly figure would report
# a recurring cost that does not recur, which is why the two rates stay
# separate here and are never summed downstream.
S3_PRICING = {
    "last_updated": "2026-08-22",
    # THE REGION THE RATES WERE QUOTED FOR, which is not necessarily the region
    # this run stages to now that S3_STAGING_REGION is overridable. S3 prices
    # differ by region, so the two being able to disagree is a new fact and the
    # report states it rather than pricing one region under another's name.
    # Re-quoting the rates for a different region is a source edit with a new
    # `last_updated`; this field is what makes the gap visible until somebody
    # does.
    "quoted_region": "us-east-1",
    "standard_usd_per_gb_month": 0.023,
    "put_usd_per_1000": 0.005,
}


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 21:47:10 2026

@author: ramyalsaffar
"""
