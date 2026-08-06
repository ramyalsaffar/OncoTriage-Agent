# The Project Main Configuration Values
#######################################
#
# ITEM 20c: THIS FILE IS A SHIM.
#
# Every constant, every comment explaining where a number came from, and the
# two client constructions moved into oncotriage/config.py. This file
# re-exports them so that Files 04 to 46, which are exec'd into one shared
# namespace and read these names with no import statement, keep working
# unchanged.
#
# TWO THINGS ARE DIFFERENT IN THE PACKAGE, and this file is where the
# difference is absorbed:
#
#   1. THE CLIENTS ARE LAZY THERE AND EAGER HERE. oncotriage.config exposes
#      get_openai_client() and get_qdrant_client(), each building at most one
#      object and caching it, so importing the package opens nothing. This file
#      calls them once, at the bottom, and binds `openai_client` /
#      `qdrant_client` — the names File 11, 12, 13, 17, 23, 29, 36, 37, 39, 45
#      and 46 use. They are the SAME objects the package hands out; there is no
#      second client.
#
#   2. SO ARE THE CREDENTIALS AND THE SDK-DERIVED TIMEOUTS. `keys`,
#      `openai_api_key`, `qdrant_url`, `qdrant_api_key`,
#      `SDK_DEFAULT_CONNECT_TIMEOUT_SECONDS`, `MATCHING_REQUEST_TIMEOUT` and
#      `EMBEDDING_REQUEST_TIMEOUT` are all resolved from the .env or from a
#      throwaway SDK client, and all of them are bound eagerly here for the
#      same reason.
#
# Loading this file therefore still requires a readable .env with all three
# keys, exactly as before. That is deliberate: the exec chain's contract is
# that after "03- Config.py" the clients exist, and half-loading it would move
# the failure to a random later line instead of this one.
#
# DATA_SNAPSHOT_DATE MOVED. "tests/test_config_snapshot_date_rot.py" rewrites the
# assignment as TEXT and re-runs Files 38 and 39 as subprocesses at several
# dates; it now targets oncotriage/config.py, which is where the literal is.
# Re-exporting it here does not give it back — a name bound by an import cannot
# be patched by editing this file.
#
# Re-exported EXPLICITLY, by name. A star import would bind httpx, OpenAI,
# QdrantClient, `settings` and every private cache slot into the shared
# namespace, and the next constant added to the package would join the chain's
# surface without anyone deciding it should.


#------------------------------------------------------------------------------


from oncotriage.config import (
    Project_Name,
    COLLECTION_NAME,
    EMBEDDING_MODEL,
    EMBEDDING_DIM,
    MATCHING_MODEL,
    TOP_K_CANDIDATES,
    BM25_RETRIEVAL_SIZE,
    VECTOR_RETRIEVAL_SIZE,
    RRF_POOL_SIZE,
    MATCHING_TEMPERATURE,
    EXPANSION_TEMPERATURE,
    DATA_SNAPSHOT_DATE,
    trial_dict,
    RERANK_SCORE_THRESHOLD,
    MESH_BOOST_DIRECT_FRACTION,
    MESH_BOOST_PAN_FRACTION,
    MESH_BOOST_DIRECT_FLOOR,
    MESH_BOOST_PAN_FLOOR,
    QUALITY_THRESHOLD_PERCENTILE,
    MAX_TRIALS_FOR_EVALUATION,
    ENABLE_RATE_LIMITING,
    RATE_LIMIT,
    AIRFLOW_DAG_SCHEDULE,
    OPENAI_SDK_MAX_RETRIES,
    _structured_timeout,
    MATCHING_REQUEST_TIMEOUT_SECONDS,
    EMBEDDING_REQUEST_TIMEOUT_SECONDS,
    MAX_GPT4O_RETRIES,
    RETRY_BASE_DELAY,
    MATCHING_REASONING_EFFORT,
    MATCHING_MAX_TOKENS,
    MATCHING_SEED,
    MATCHING_OUTPUT_TOKENS_PER_TRIAL,
    MATCHING_OUTPUT_SPLIT_FRACTION,
    MAX_TRUNCATION_SPLITS,
    CHARS_PER_TOKEN,
    MAX_VARIANT_TERMS,
    PRICING_CONFIG,
    BASELINE_WINDOW_DAYS,
    COMPARISON_WINDOW_DAYS,
    MIN_SAMPLES_BASELINE,
    MIN_SAMPLES_COMPARISON,
    KS_TEST_THRESHOLD,
    PSI_THRESHOLD,
    Z_SCORE_THRESHOLD,
    PSI_BINS,
    ECOG_UNAVAILABLE_RATE_THRESHOLD,
    BATCH_SIZE,
    RESAMPLE_COUNT,
    RESAMPLE_SEED,
    CHECKPOINT_FILENAME,
    RESULTS_FILENAME,
    MAX_WORKERS,
    COHORT_MANIFEST_FILENAME,
    COHORT_MANIFEST_FLUSH_EVERY,
    ECOG_SCORE_DISTRIBUTION,
    ECOG_MISSINGNESS_FRACTION,
    ABLATION_FDR_ALPHA,
    ABLATION_OUTCOME_METRICS,
    ABLATION_DESCRIPTIVE_METRICS,
    ABLATION_POWER_TARGET,
    ABLATION_MIN_PAIRED,
)

from oncotriage.config import (
    get_keys,
    get_openai_api_key,
    get_qdrant_url,
    get_qdrant_api_key,
    get_sdk_default_timeout,
    get_sdk_default_connect_timeout_seconds,
    get_matching_request_timeout,
    get_embedding_request_timeout,
    get_openai_client,
    get_qdrant_client,
)


#------------------------------------------------------------------------------


# Initialize clients
#
# The five statements below are what the package's laziness is traded back for.
# Each factory is cached, so calling them here builds one OpenAI client, one
# Qdrant client and one throwaway SDK client (for its default connect timeout)
# — the same count this file always built — and every later call anywhere
# returns those same objects.
keys = get_keys()
openai_api_key = keys['openai']
qdrant_url = keys['qdrant_url']
qdrant_api_key = keys['qdrant_key']

# Kept because "03- Config.py" defined it and this pass drops no name it
# defined. It is the whole httpx.Timeout the installed SDK ships, read off a
# throwaway client; SDK_DEFAULT_CONNECT_TIMEOUT_SECONDS is its connect phase.
_sdk_default_timeout = get_sdk_default_timeout()
SDK_DEFAULT_CONNECT_TIMEOUT_SECONDS = get_sdk_default_connect_timeout_seconds()

MATCHING_REQUEST_TIMEOUT = get_matching_request_timeout()
EMBEDDING_REQUEST_TIMEOUT = get_embedding_request_timeout()

openai_client = get_openai_client()
qdrant_client = get_qdrant_client()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 21:47:10 2026

@author: ramyalsaffar
"""
