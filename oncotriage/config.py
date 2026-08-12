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

# Characters per token. The same crude proxy File 11 uses for its embedding
# batch sizing; kept identical so the two agree, and kept crude on purpose —
# tiktoken would be a dependency and an import cost for an estimate whose job
# is to be roughly right before a call that is about to measure it exactly.
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
