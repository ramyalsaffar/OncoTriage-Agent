"""The state schema and the degradation vocabularies that travel in it.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 118-359, verbatim.

``TrialMatchState`` is the TypedDict every node reads and writes, and the
constants above it are the fixed label sets for the three places the pipeline
can quietly run on less than it was built to run on: a lost retrieval channel,
a query expansion that fell back, a cancer-site filter that never ran. Every one
of those labels reaches a column in inferences.db, which is why they are named
constants in a module of their own rather than string literals at their use
sites -- a typo in a literal would be stored, and would read as a state that
never happened.

They are NOT tunables and do not belong in oncotriage/config.py: they are names
for pipeline states, and changing one changes what a stored row means.

Imports nothing from the project. Importing it compiles one regex and builds a
TypedDict.
"""

import re
from typing import Dict, List, Optional, TypedDict


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# State Schema
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Degradation vocabularies
# ---------------------------------------------------------------------------
# Fixed label sets for the three places where the pipeline can quietly run on
# less than it was built to run on. They are names for pipeline states, not
# tunables, so they live here rather than in 03- Config.py. Every one of them
# reaches a column in inferences.db.

# --- Stage 2, per retrieval channel ---
CHANNEL_OK = "ok"                        # query returned a (possibly empty) result list
CHANNEL_FAILED = "failed"                # query raised; the channel contributed nothing
CHANNEL_ABLATED = "ablated"              # retrieval_mode deliberately excluded it
CHANNEL_EMPTY_QUERY = "empty_query"      # query text tokenized to zero BM25 terms
                                         # (see the guard in _sparse_query below)

# Channels that must be present in retrieval_channels on every run, so a
# missing key is a bug rather than a channel that "did not happen".
RETRIEVAL_CHANNELS = ("title", "conditions", "criteria", "dense")

# --- Stage 1, which query the run actually searched with ---
EXPANSION_PATH_MESH = "mesh_expanded"          # MeSH walk produced descriptors
EXPANSION_PATH_FALLBACK = "base_query_fallback"  # degraded to demographics + display

# --- Genomic variant detection (Stages 1 and 5) ---
#
# LOINC 69548-6 is the mCODE genomic variant observation. It is a fact about an
# external standard, so it is a named constant here rather than a tunable.
# 07- FHIR Parser.py routes observations carrying it OUT of patient_data
# ["observations"] and into patient_data["cancer_genomic_variants"], the same
# way it routes ECOG — which is why a scan of ["observations"] alone can never
# find one.
GENOMIC_VARIANT_LOINC = "69548-6"

# Free-text fallback for observations that carry no structured variant fields.
# Anchored on both sides so a keyword only matches a whole word: the previous
# `"gene" in display.lower()` matched "gene" inside "Generalized anxiety
# disorder 7 item (GAD-7)" on 45,186 observations across the 1,000-patient
# cohort and inside "General activity scale [PEG]" on a further 656, against
# 295 genuine matches. Every patient in the cohort had a polluted query.
#
# The boundary is "not a letter or digit" rather than \b so that punctuation
# and hyphens still delimit: "c-MET", "MSI-H" and "PD-L1 expression" all match,
# "Generalized" does not.
_VARIANT_TEXT_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:genetic|variant|mutation|gene)(?![a-z0-9])"
)

# --- Stage 4, whether the cancer site filter ran ---
MESH_FILTER_APPLIED = "applied"
MESH_FILTER_SKIP_ABLATED = "ablation_skipped"    # skip_mesh_filter flag set
MESH_FILTER_SKIP_NO_FILTER = "no_mesh_filter"    # MeSH data files never loaded
MESH_FILTER_SKIP_NO_TREES = "no_patient_trees"   # patient never resolved to C04 trees


class _EmptySparseQuery(Exception):
    """A BM25 query text tokenized to zero terms, so there is nothing to search.

    Raised by node_hybrid_retrieval's _sparse_query and caught by its own
    channel collector, which records CHANNEL_EMPTY_QUERY. Kept distinct from a
    Qdrant failure because the two need different responses: a failed channel
    means the index or the network is unwell, an empty query means the patient
    record produced no searchable disease text.
    """


class TrialMatchState(TypedDict):
    """Shared state that flows through every node in the pipeline.

    Each node reads what it needs and writes its outputs.
    LangGraph passes this dict from node to node automatically.
    """
    # --- Inputs (set once at invocation) ---
    patient_data: Dict                          # Parsed FHIR patient dict

    # --- Stage 1: Query Expansion ---
    expanded_query: str                         # Patient query + medical synonyms
    expansion_prompt: str                       # Prompt sent to expansion model
    expansion_input_tokens: int                 # Input tokens for expansion
    expansion_output_tokens: int                # Output tokens from expansion

    # Short queries for cross-encoder (MedCPT-native format)
    rerank_queries: List[str]

    # How the patient's MeSH C04 identity resolved: the layer name(s), or the
    # reason none applied ("pan_cancer_only", "unmapped", ...). Written once
    # in Stage 1; Stage 3 re-resolves the same way. Logged to
    # inferences.mesh_resolution so an unresolved patient is a queryable fact
    # rather than an inference from an empty tree list.
    mesh_resolution: str

    # Which branch Stage 1 took: EXPANSION_PATH_MESH when the MeSH walk
    # produced terms, EXPANSION_PATH_FALLBACK when it produced none and the
    # query degraded to demographics + diagnosis display. mesh_resolution says
    # WHY resolution failed; this says WHAT the run then searched with, and the
    # two are not the same fact — a resolution can name a layer and still yield
    # no descriptors. Logged to inferences.query_expansion_path so the
    # fallback rate is a query rather than an unread WARNING line.
    query_expansion_path: str

    # --- Stage 2: Hybrid Retrieval ---
    hybrid_results: List[Dict]                  # Trials from BM25 + Vector + RRF

    # Observed per-channel retrieval counts, written by Stage 2 and logged to
    # inferences.bm25_retrieved / inferences.vector_retrieved. These are counts
    # of what the channel actually returned, NOT the configured request sizes
    # (BM25_RETRIEVAL_SIZE / VECTOR_RETRIEVAL_SIZE): a channel that failed, was
    # ablated away, or hit a collection smaller than its limit returns fewer.
    # Logging the constants instead would make the columns a record of the
    # configuration rather than of the run.
    bm25_retrieved: int                         # unique NCT IDs across the 3 sparse fields
    vector_retrieved: int                       # unique NCT IDs from the dense channel

    # Per-channel outcome for the four retrieval channels (title, conditions,
    # criteria, dense). Shape:
    #     {"title": {"status": CHANNEL_OK, "count": 75, "error": ""}, ...}
    # status is one of the CHANNEL_* constants below. Written by Stage 2 and
    # logged to inferences.retrieval_channels as JSON.
    #
    # bm25_retrieved / vector_retrieved cannot carry this: a dense outage and a
    # dense channel that legitimately matched nothing both report 0, and three
    # sparse channels collapse into one union count in which a single failed
    # field is invisible. Fusion continues on whatever channels returned, so
    # without this field a run on two channels is indistinguishable from a
    # clean run in every stored record.
    retrieval_channels: Dict

    # Derived scalars over retrieval_channels, so degradation is queryable
    # without parsing JSON in SQL:
    #   expected — channels the retrieval mode called for (4 hybrid, 3
    #              bm25_only, 1 vector_only); ablated channels are not expected
    #              and never count as degradation
    #   ok       — expected channels that returned a result list
    #   degraded — 1 when ok < expected, else 0
    retrieval_channels_expected: int
    retrieval_channels_ok: int
    retrieval_degraded: int

    # Trials that won a place in the fusion pool but whose payload could not be
    # recovered from Qdrant, so they never reached Stage 3. The batch-scroll
    # fallback that loses them printed a line and nothing else.
    retrieval_trials_lost: int

    # --- Stage 3: Cross-Encoder Re-Ranking ---
    reranked_trials: List[Dict]                 # Top-K after cross-encoder scoring

    # --- Stage 4: Rule-Based Filtering ---
    filtered_trials: List[Dict]                 # Trials surviving rule filters + cap
    candidates_after_rule_filter: int           # Count after rule filters (before quality threshold)
    candidates_after_quality_filter: int        # Count after quality threshold (before cap)
    mesh_dropped: int                           # Trials dropped by MeSH cancer site filter
    stage_dropped: int                          # Trials dropped by cancer stage filter
    histology_dropped: int                      # Trials dropped by histology filter

    # The remaining two per-trial drops in Stage 4, and the two pool-level cuts
    # that follow them. Every other reason the pool shrinks was already a named
    # counter; these four were not, so "reranked 40 -> filtered 9" left 31
    # trials removed for reasons that could only be guessed at, and the age and
    # sex drops in particular were bare `continue`s with nothing recorded.
    #
    # quality_threshold is the RELATIVE cut the gate actually used -- the
    # QUALITY_THRESHOLD_PERCENTILE of this pool's unboosted fused scores -- so
    # the configured percentile alone does not say where the cut fell.
    #
    # THE GATE IS TWO KNOBS AND THEY OVERLAP. quality_dropped is the total; the
    # three below split it. percentile + floor does NOT equal quality_dropped,
    # because a trial can fail both. floor_only is the one that answers "is the
    # absolute knob doing anything the relative knob was not already doing".
    # quality_threshold describes ONLY the relative knob; the absolute one cuts
    # at MEDCPT_SCORE_FLOOR, which is a MedCPT score and not comparable to it.
    age_dropped: int                            # Trials dropped by the age window
    sex_dropped: int                            # Trials dropped by the sex requirement
    quality_dropped: int                        # Trials dropped by the quality gate, both knobs
    quality_dropped_percentile: int             # ...of which, by the relative percentile
    quality_dropped_floor: int                  # ...of which, by the absolute MedCPT floor
    quality_dropped_floor_only: int             # ...by the floor and NOT by the percentile
    # NULL when the gate saw an EMPTY pool -- every trial was already removed
    # by the per-trial filters above, so no cut was made and any number here
    # would claim one. Same NULL convention as the degradation columns.
    quality_threshold: float                    # Unboosted fused score the relative knob cut at, or None


    patient_trees: set                           # Resolved MeSH C04 tree numbers (Stage 3 → Stage 4)
    patient_histology: set                       # Histology tags (Stage 3 → Stage 4)

    # Whether Stage 4's cancer site filter actually ran against the candidate
    # pool, and why not when it did not (one of the MESH_FILTER_SKIP_*
    # constants). The filter is conditional on _MESH_FILTER being loaded AND
    # the patient resolving to specific C04 trees, so "mesh_dropped == 0" has
    # always meant either "checked, nothing to drop" or "never checked".
    #
    # Stage 5 reads mesh_filter_applied to decide whether its system prompt may
    # assert that disease relevance was confirmed. Both are logged.
    mesh_filter_applied: bool
    mesh_filter_skip_reason: str

    # --- Stage 5: GPT-4o Evaluation ---
    evaluations: List[Dict]                     # Criterion-level match results
    gpt4o_retries: int                          # Current retry count for GPT-4o

    # Truncation control (Stage 5). A SEPARATE budget from gpt4o_retries: that
    # one counts whole-node retries for a malformed or failed response, this
    # counts levels of halving spent because a response was cut off at
    # MATCHING_MAX_TOKENS. A patient that hits one parse failure and then needs
    # two splits must not be failed for exhausting a shared counter.
    gpt4o_truncation_splits: int
    # The pre-call estimate, logged beside the actual so the calibration in
    # 03- Config.py can be re-derived from measured data rather than re-guessed.
    gpt4o_output_tokens_estimated: int
    # Trials that entered Stage 5 and left it with no verdict because of
    # truncation (the floor, or the split budget). Distinct from
    # not_evaluable_trials, which counts trials the model assessed and could
    # not conclude on.
    not_evaluable_truncated: int
    # How many model calls this stage actually made. 1 unsplit; more when a
    # batch was split. Without it a chunked run is indistinguishable from an
    # unsplit one in the token columns.
    gpt4o_calls: int
    gpt4o_raw_response: str                     # Raw GPT-4o text (for retry debugging)
    gpt4o_prompt: str                           # Prompt sent to matching model
    gpt4o_input_tokens: int
    gpt4o_output_tokens: int
    # The reasoning share OF gpt4o_output_tokens on a reasoning model, not an
    # amount on top of it. None when no response reported the breakdown; see
    # _pipeline_provenance() for why that is not 0.
    gpt4o_reasoning_tokens: Optional[int]
    # The model string the API answered with (response.model), which is not
    # necessarily MATCHING_MODEL: an alias can resolve to a dated snapshot.
    # This is what File 14 logs and prices against.
    matching_model: Optional[str]
    cross_vocab_remaps: int                     # Criterion labels resolved to not_evaluable
                                                # because the model used the other arm's
                                                # vocabulary (or returned a non-object entry)

    # --- Stage 6: Final Output ---
    result: Dict                                # Complete pipeline output
    
    # --- Pipeline Metadata ---
    error: str                                  # Error message (empty = no error)
    stage_timings: Dict                         # Latency per stage (seconds)
    
    # --- Ablation Study (optional, defaults to {} = all stages active) ---
    # Controls which pipeline stages are disabled during ablation runs.
    # Keys (all default False / "hybrid" when absent):
    #   skip_mesh_filter:      bool — skip BOTH MeSH uses: the Stage 3
    #                                 relevance boost and the Stage 4 drop
    #   skip_stage_filter:     bool — skip cancer stage mismatch filter
    #   skip_histology_filter: bool — skip histology mismatch filter
    #   skip_cross_encoder:    bool — skip MedCPT cross-encoder reranking
    #   retrieval_mode:        str  — "hybrid" (default), "bm25_only", "vector_only"
    # Populated by File 25 (Ablation Study). All other callers pass {}.
    ablation_flags: Dict


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
