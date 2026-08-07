"""Stages 1, 2 and 3: expansion, hybrid retrieval, cross-encoder rerank.

Item 20c, pass 2c. Two slices of "13- LangGraph Agent.py":

    1077-2112  node_query_expansion, node_hybrid_retrieval, the MeSH relevance
               boost and the quality gate, node_cross_encoder_rerank
    5224-5281  build_bm25_index_from_qdrant

The BM25 index builder is here rather than in ``graph`` because it is a
retrieval concern that happens to be called once at startup; it scrolls the same
collection through the same client as every query in Stage 2.

WHAT CHANGED, and all of it is the dependency seam:

    _bm25_query_model   -> deps.get_bm25_query_model()
    qdrant_client       -> deps.get_qdrant_client()
    _CANCER_REGISTRY    -> deps.get_cancer_registry()
    _MESH_FILTER        -> deps.get_mesh_filter()
    medcpt_score_pairs  -> models.score_pairs()

Each was a module global File 13 bound at exec time, and each was a name Files
35, 36, 45 and 46 rebound in the shared namespace to redirect the pipeline. A
module function cannot see a caller's globals, so without the seam every one of
those redirects would have gone quiet -- File 46's replay would have called the
real OpenAI endpoint while reporting that the fixtures passed.

``_MESH_FILTER is None`` IS A REAL BRANCH, not a not-yet-loaded check: it means
the MeSH JSON lookups were absent, and the run records
MESH_FILTER_SKIP_NO_FILTER. ``deps.get_mesh_filter()`` caches None as an answer
for exactly that reason.

One stale banner did not come across: lines 1072-1074 carried
"REPLACEMENT node_query_expansion -- paste over the existing function", an
instruction to a human from an earlier edit, not documentation.
"""

import time
from typing import Dict, List, Tuple

import numpy as np
from concurrent.futures import ThreadPoolExecutor
from qdrant_client.models import SparseVector
from rank_bm25 import BM25Okapi

from oncotriage.agent import deps, models
from oncotriage.agent.mesh_expansion import (
    expand_query_from_mesh,
    resolve_patient_mesh,
)
from oncotriage.agent.patient import extract_genomic_variant_terms
from oncotriage.agent.readiness import require_populated_index
from oncotriage.agent.state import (
    CHANNEL_ABLATED,
    CHANNEL_EMPTY_QUERY,
    CHANNEL_FAILED,
    CHANNEL_OK,
    EXPANSION_PATH_FALLBACK,
    EXPANSION_PATH_MESH,
    RETRIEVAL_CHANNELS,
    TrialMatchState,
    _EmptySparseQuery,
)
from oncotriage.agent.text import tokenize_for_bm25
from oncotriage.config import (
    BM25_RETRIEVAL_SIZE,
    COLLECTION_NAME,
    MAX_VARIANT_TERMS,
    MESH_BOOST_DIRECT_FLOOR,
    MESH_BOOST_DIRECT_FRACTION,
    MESH_BOOST_PAN_FLOOR,
    MESH_BOOST_PAN_FRACTION,
    QUALITY_THRESHOLD_PERCENTILE,
    RERANK_SCORE_THRESHOLD,
    RRF_POOL_SIZE,
    TOP_K_CANDIDATES,
    VECTOR_RETRIEVAL_SIZE,
)
from oncotriage.observability import get_logger
from oncotriage.registries.mesh import specific_cancer_trees
from oncotriage.utils import qdrant_retry


log = get_logger(__name__)


#------------------------------------------------------------------------------


def node_query_expansion(state: dict) -> dict:
    """
    Stage 1: Deterministic query expansion via MeSH C04 hierarchy lookup.

    Replaces the previous GPT-4o-mini LLM call with a pure lookup against
    the MeSH neoplasm tree. This eliminates the primary source of pipeline
    non-determinism: LLM-generated search terms that vary across runs.

    How it works:
      1. Resolve patient cancer → MeSH tree numbers (SNOMED crosswalk or fuzzy)
      2. Walk the C04 tree: collect self + child + sibling + parent descriptors
      3. Build expanded_query from MeSH descriptor names (exact ClinicalTrials.gov vocabulary)
      4. Build rerank queries R1/R2/R3 deterministically

    Rerank query strategy:
      R1 — Primary MeSH descriptor (e.g., "Colonic Neoplasms")
           Best for: BM25 exact match against trial conditions field
      R2 — Patient's FHIR display text, capped to 8 words (e.g., "Non-small cell
           carcinoma of lung"). This IS the histological/molecular subtype when
           the EHR provides one. Best for: cross-encoder semantic matching.
      R3 — Parent MeSH descriptor (e.g., "Colorectal Neoplasms") or repeat R1
           Best for: Broader recall for related trials

    Fallback behavior:
      - If MeSH resolution fails entirely → falls back to base_query only
        (same as the previous GPT-4o-mini API-failure fallback)
      - If primary_mesh is None but mesh_terms exist → uses first mesh_term
      - If no parent exists → R3 repeats R1 (deterministic, no invention)

    Properties:
      - 100% deterministic: same patient record → same output, every time
      - Zero API cost: no LLM call, no tokens consumed
      - Near-zero latency: pure dictionary lookups + one O(T) tree scan
      - Consistent with Stages 3 and 4: same resolve_patient_mesh() call

    Outputs (unchanged contract from previous LLM-based version):
      - expanded_query:  str  — base_query + comma-separated MeSH terms
      - rerank_queries:  list — 3 short queries for MedCPT cross-encoder
      - expansion_prompt: str — diagnostic string (replaces LLM prompt)
      - expansion_input_tokens:  int — always 0 (no LLM)
      - expansion_output_tokens: int — always 0 (no LLM)
    """
    # MedCPT was trained on 2-10 word PubMed queries. Cap R2 to prevent
    # wasting the cross-encoder's 512-token budget on long EHR display texts
    # (real-world EHRs can have 20+ word diagnosis strings with staging,
    # receptor status, laterality, etc.).
    RERANK_QUERY_MAX_WORDS = 8

    start = time.time()

    # Resolved through the dependency seam, ONCE per call. File 13 read these
    # as module globals bound at exec time, which is what Files 35, 36, 45 and
    # 46 rebound to redirect the pipeline; a module function cannot see a
    # caller's globals, so the seam is what keeps those redirects working.
    # Once per call rather than per use so one invocation cannot see two
    # different objects if an override is installed mid-flight.
    cancer_registry = deps.get_cancer_registry()
    mesh_filter = deps.get_mesh_filter()

    patient_data = state["patient_data"]
    demographics = patient_data["demographics"]
    conditions = patient_data["conditions"]

    # ── Build base query ──────────────────────────────────────────────────
    age = demographics.get("age")
    age = age if age is not None else "unknown"
    
    sex = demographics.get("sex", "unknown")

    primary_diagnosis = "cancer"
    if conditions:
        valid = [c for c in conditions
                 if (c.get("verification_status") or "unknown")
                 not in cancer_registry.exclude_verification]
        if not valid:
            valid = conditions
        cancer_conditions = [c for c in valid if cancer_registry.is_primary_cancer(c)]
        if cancer_conditions:
            primary_condition = sorted(
                cancer_conditions, key=cancer_registry.sort_key
            )[0]
            primary_diagnosis = primary_condition["display"]

    # ── Extract genetic variant from observations ─────────────────────────
    # Precision oncology trials are indexed by gene/variant names (EGFR, BRAF,
    # IDH1, KIT, PIK3CA, etc.). Including the gene in the retrieval query is
    # critical for matching gene-specific trials via BM25 and vector search.
    # Structural detection, with a word-bounded text path as fallback — see
    # extract_genomic_variant_terms for what each path is and why the previous
    # substring test both over- and under-matched.
    observations = patient_data.get("observations") or []
    variant_result = extract_genomic_variant_terms(patient_data)
    gene_parts = variant_result["terms"]
    gene_string = ", ".join(gene_parts) if gene_parts else ""

    if gene_string:
        base_query = f"{age} year old {sex} patient with {primary_diagnosis}, {gene_string}"
    else:
        base_query = f"{age} year old {sex} patient with {primary_diagnosis}"
        
    # Append pan-cancer retrieval terms. "Solid tumor" and "solid neoplasm"
    # appear in basket/umbrella trial titles and conditions. Including them
    # boosts recall for pan-cancer trials that accept any solid tumor patient.
    # All TREC PM top systems included these terms.
    base_query += ", solid tumor, solid neoplasm"

    # ── Deterministic MeSH expansion ──────────────────────────────────────
    mesh_result = expand_query_from_mesh(conditions, cancer_registry, mesh_filter)

    if mesh_result["mesh_terms"]:
        # ── SUCCESS: Build expanded_query from MeSH terms ─────────────────
        expansion_path = EXPANSION_PATH_MESH
        expanded_terms = ", ".join(mesh_result["mesh_terms"])
        expanded_query = f"{base_query}, {expanded_terms}"

        # ── Build rerank queries R1 / R2 / R3 ────────────────────────────

        # R1: Primary MeSH descriptor (exact ClinicalTrials.gov vocabulary).
        #     Falls back to first mesh_term if primary_mesh is None (edge case:
        #     tree numbers resolved via crosswalk but not found in tree_to_name,
        #     so self_names was empty but children/siblings/parents populated
        #     mesh_terms). mesh_terms[0] is guaranteed to exist here because
        #     we're inside the `if mesh_result["mesh_terms"]` branch.
        r1 = mesh_result["primary_mesh"] or mesh_result["mesh_terms"][0]

        # R2: Patient's FHIR display text — the most specific clinical
        #     description available. Contains histological/molecular subtype
        #     when the EHR provides one (e.g., "Non-small cell carcinoma of
        #     lung", "Infiltrating duct carcinoma of breast").
        #     Capped to RERANK_QUERY_MAX_WORDS words for MedCPT (trained on
        #     2-10 word PubMed queries; real-world EHRs can be 20+ words).
        #     Falls back to R1 if display is generic or missing.
        if primary_diagnosis != "cancer":
            r2_words = primary_diagnosis.split()
            r2 = " ".join(r2_words[:RERANK_QUERY_MAX_WORDS])
        else:
            r2 = r1

        # R3: Parent MeSH descriptor — broader category for recall.
        #     Falls back to R1 if no parent exists (e.g., tree is at root).
        r3 = mesh_result["parent_mesh"] if mesh_result["parent_mesh"] else r1

        rerank_queries = [r1, r2, r3]

        # R4: Genetic variant query for precision medicine matching.
        # Dedicated cross-encoder pass scores trials by gene relevance.
        # RRF fusion rewards trials matching BOTH cancer type (R1-R3)
        # and genetic variant (R4), which is the precision medicine signal.
        if gene_string:
            rerank_queries.append(gene_string)

        # Diagnostic string (replaces LLM prompt in output state)
        expansion_info = (
            f"MeSH deterministic expansion ({mesh_result['resolution']} resolution)\n"
            f"  Patient trees: {mesh_result['patient_trees']}\n"
            f"  Self: {mesh_result['primary_mesh']}\n"
            f"  Parent: {mesh_result['parent_mesh']}\n"
            f"  Total MeSH terms: {len(mesh_result['mesh_terms'])}"
        )

    else:
        # ── FALLBACK: MeSH resolution failed ──────────────────────────────
        # Same behavior as the previous GPT-4o-mini API-failure fallback.
        # Uses base_query only (demographics + primary diagnosis display).
        #
        # Recorded, not only printed: this WARNING was the sole trace of a
        # degraded query, so the rate at which the pipeline searched without
        # any MeSH expansion was unknowable from the stored records.
        expansion_path = EXPANSION_PATH_FALLBACK
        log.warning("MeSH expansion failed; falling back to the base query "
                    "(degraded)", stage=1, degraded=True,
                    expansion_path=expansion_path,
                    mesh_resolution=mesh_result["resolution"])
        expanded_query = base_query
        
        rerank_queries = [primary_diagnosis] * 3
        if gene_string:
            rerank_queries.append(gene_string)
        
        expansion_info = (
            f"MeSH expansion FAILED — fallback to base query\n"
            f"  Resolution: {mesh_result['resolution']}\n"
            f"  Primary diagnosis display: {primary_diagnosis}"
        )

    # ── Logging (same format as previous version) ─────────────────────────
    elapsed = time.time() - start
    # THE QUERIES THEMSELVES ARE NOT LOGGED, and this is the single largest
    # redaction in the pass. `expanded_query` is built from the patient's
    # primary diagnosis display -- "Malignant neoplasm of breast", a stage, a
    # histology, gene symbols -- and each `rerank_queries` entry is a
    # diagnosis string. Printed to a terminal they were transient. As
    # structured fields keyed by a correlation ID they are a durable,
    # searchable statement of what this patient has, which is precisely what
    # LOGGABLE_FIELDS refuses. The shape of the expansion is what diagnoses a
    # retrieval problem, and the shape is what is kept: the path taken, how
    # many queries came out, and how long each is.
    log.info("query expansion complete (MeSH deterministic)", stage=1,
             duration_s=round(elapsed, 3), expansion_path=expansion_path,
             query_count=len(rerank_queries),
             query_length=len(expanded_query))
    # Which detector found the variants, not just how many there were. A run
    # whose only variants came from the free-text path is searching on weaker
    # evidence than one backed by mCODE records, and the counts are the only
    # thing that distinguishes them.
    _vc = variant_result["counts"]
    if gene_parts or any(_vc.values()):
        # Counts per detector, never the gene symbols: a variant term names a
        # somatic finding in this patient's tumour. Which DETECTOR found them
        # is the operational fact -- a run whose variants came only from the
        # free-text path is searching on weaker evidence -- and it survives.
        log.info("genomic variant terms resolved", stage=1,
                 variant_count=len(gene_parts),
                 variants_mcode=_vc["mcode"],
                 variants_structured=_vc["structured"],
                 variants_free_text=_vc["free_text"],
                 dropped=variant_result["truncated"],
                 threshold=MAX_VARIANT_TERMS)
    if mesh_result["mesh_terms"]:
        log.info("MeSH resolution", stage=1,
                 mesh_resolution=mesh_result["resolution"],
                 trees_count=len(mesh_result["patient_trees"]),
                 count=len(mesh_result["mesh_terms"]))

    return {
        "expanded_query": expanded_query,
        "rerank_queries": rerank_queries,
        "expansion_prompt": expansion_info,
        "expansion_input_tokens": 0,
        "expansion_output_tokens": 0,
        # Which branch above ran. Paired with mesh_resolution: that says why
        # the MeSH walk produced nothing, this says the query was degraded.
        "query_expansion_path": expansion_path,
        # Which layer resolved the patient's MeSH identity, or why none did.
        # Stage 3 re-resolves from the same conditions with the same helper,
        # so this one string describes the trees Stage 4 filters on too.
        "mesh_resolution": mesh_result["resolution"],
        "stage_timings": {
            **state.get("stage_timings", {}),
            "query_expansion": round(elapsed, 3),
        },
    }


def node_hybrid_retrieval(state: TrialMatchState) -> dict:
    """
    Stage 2: Multi-field BM25 sparse + dense vector hybrid retrieval.

    Replaces the previous in-memory BM25Okapi with Qdrant-native sparse
    vector BM25 search across 3 independently indexed fields:

      title-bm25:      Searched with disease query (R1).
                        Highest-weight signal. A disease name in the trial
                        title is the strongest relevance indicator.
                        Weight: 2.0x in RRF fusion.

      conditions-bm25:  Searched with disease query (R1).
                        MeSH conditions + keywords + interventions.
                        Weight: 1.5x in RRF fusion.

      criteria-bm25:    Searched with full expanded query.
                        Contains gene names, biomarkers, staging.
                        Weight: 1.0x in RRF fusion.

      dense vector:     Searched with full expanded query.
                        Semantic similarity via OpenAI embeddings.
                        Weight: 1.0x in RRF fusion.

    All 4 Qdrant queries run in parallel via ThreadPoolExecutor.
    Total latency = max(single query time), not sum.

    Field-level BM25 with weighted RRF fusion is the production-grade
    equivalent of ElasticSearch dis_max with per-field boosting. This is
    the same architecture that JULIE Lab (TREC PM 2019 #1) used.

    Ablation flags:
      retrieval_mode="hybrid"      (default) all 4 queries
      retrieval_mode="bm25_only"   3 sparse queries, no dense
      retrieval_mode="vector_only" 1 dense query, no sparse
    """
    start = time.time()

    # Resolved through the dependency seam, ONCE per call. File 13 read these
    # as module globals bound at exec time, which is what Files 35, 36, 45 and
    # 46 rebound to redirect the pipeline; a module function cannot see a
    # caller's globals, so the seam is what keeps those redirects working.
    # Once per call rather than per use so one invocation cannot see two
    # different objects if an override is installed mid-flight.
    bm25_query_model = deps.get_bm25_query_model()
    qdrant = deps.get_qdrant_client()

    # AN EMPTY INDEX MUST NOT LOOK LIKE A PATIENT WHO MATCHED NOTHING.
    #
    # Every Qdrant call below SUCCEEDS against a collection with zero points and
    # returns an empty list. The graph's conditional edge then routes to
    # node_no_candidates, the API answers 200 with "no eligible trials found",
    # and the stored inference row is well-formed -- the same output a genuinely
    # unmatchable patient produces. Nothing raises and no counter moves.
    #
    # This is BEFORE the channel machinery on purpose. Each channel below is
    # wrapped in `except Exception` and records itself as failed, so a raise
    # from inside that region would be absorbed into "one channel was
    # unavailable" -- the report that hides this exact fault.
    #
    # Cost: one `collection_exists` + one `count` per PROCESS, not per patient
    # (see readiness.require_populated_index for why only the good verdict is
    # cached). An unverifiable probe is counted and does not block; the policy
    # and its reasons are at that function.
    require_populated_index(client=qdrant)

    query = state["expanded_query"]
    rerank_queries = state.get("rerank_queries", [])

    # R1 = primary MeSH descriptor or disease name (best for title/conditions)
    # Full expanded_query = disease + gene + MeSH terms (best for criteria + dense)
    disease_query = rerank_queries[0] if rerank_queries else query

    # --- Ablation: retrieval mode ---
    _ablation = state.get("ablation_flags") or {}
    _retrieval_mode = _ablation.get("retrieval_mode", "hybrid")

    # --- RRF weights per retrieval channel ---
    # Title and conditions get higher weight because disease name match
    # in these fields is the strongest relevance signal.
    # Weights are applied as multipliers on the RRF contribution.
    WEIGHT_TITLE      = 2.0
    WEIGHT_CONDITIONS  = 1.5
    WEIGHT_CRITERIA    = 1.0
    WEIGHT_DENSE       = 1.0
    RRF_K              = 60

    # ------------------------------------------------------------------
    # Helper: run a single Qdrant sparse BM25 query
    # ------------------------------------------------------------------
    def _sparse_query(sparse_vector_name: str, query_text: str, limit: int):
        """Generate sparse query vector and search Qdrant.

        Raises _EmptySparseQuery when the text carries no BM25 terms. Measured
        behaviour, not a defensive guess (see
        tests/test_agent_retrieval_observability.py, which reproduces both
        halves against real components):

          - FastEmbed Qdrant/bm25 returns zero indices for an empty string,
            whitespace, punctuation-only text and stopword-only text. It
            lowercases, strips punctuation and drops stopwords, so a query is
            not required to be empty to tokenize to nothing.
          - Qdrant does NOT reject an empty SparseVector. A real server
            (v1.18.3, three IDF sparse fields) accepts the query and returns
            zero points, exactly as a well-formed query matching nothing does.

        So the failure mode is not a crash; it is a channel that returns an
        empty list for a reason no stored record could distinguish from "this
        query legitimately matched no trial". The raise exists to give that
        outcome its own status, and it fires before the network call because
        there is nothing to ask Qdrant.
        """
        sparse_emb = next(bm25_query_model.query_embed(query_text))
        indices = sparse_emb.indices.tolist()
        if not indices:
            raise _EmptySparseQuery(
                f"{sparse_vector_name}: query text {query_text!r} carries no "
                f"BM25 terms after tokenization"
            )
        return qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=SparseVector(
                indices=indices,
                values=sparse_emb.values.tolist(),
            ),
            using=sparse_vector_name,
            limit=limit,
            with_payload=True,
        ).points

    # ------------------------------------------------------------------
    # Run all retrieval channels in parallel
    # ------------------------------------------------------------------
    title_results = []
    conditions_results = []
    criteria_results = []
    vector_results = []

    # Per-channel outcome record. Every channel is present on every run: the
    # ones this retrieval mode does not call for keep CHANNEL_ABLATED, the ones
    # it submits are overwritten below with what actually happened. A channel
    # is never silently absent from the record.
    retrieval_channels = {
        name: {"status": CHANNEL_ABLATED, "count": 0, "error": ""}
        for name in RETRIEVAL_CHANNELS
    }

    futures = {}

    with ThreadPoolExecutor(max_workers=4) as executor:
        # Sparse BM25 queries (3 fields)
        if _retrieval_mode != "vector_only":
            futures["title"] = executor.submit(
                _sparse_query, "title-bm25", disease_query, BM25_RETRIEVAL_SIZE
            )
            futures["conditions"] = executor.submit(
                _sparse_query, "conditions-bm25", disease_query, BM25_RETRIEVAL_SIZE
            )
            futures["criteria"] = executor.submit(
                _sparse_query, "criteria-bm25", query, BM25_RETRIEVAL_SIZE
            )
        else:
            log.info("BM25 sparse search skipped by ablation flag", stage=2,
                     channel="bm25", ablation_flag="retrieval_mode",
                     mode=_retrieval_mode)

        # Dense vector query
        if _retrieval_mode != "bm25_only":
            def _dense_query():
                query_embedding = models.get_embedding(query)
                return qdrant.query_points(
                    collection_name=COLLECTION_NAME,
                    query=query_embedding,
                    limit=VECTOR_RETRIEVAL_SIZE,
                    with_payload=True,
                ).points
            futures["dense"] = executor.submit(_dense_query)
        else:
            log.info("dense vector search skipped by ablation flag", stage=2,
                     channel="dense", ablation_flag="retrieval_mode",
                     mode=_retrieval_mode)

    # Collect results, recording the outcome of every channel this mode ran.
    #
    # Fusion below proceeds on whatever came back, which is the right behaviour
    # — a dense outage should still return BM25 results rather than nothing —
    # but it is only defensible if the run says it happened. The status written
    # here is what reaches inferences.retrieval_channels.
    #
    # Error text is truncated: this column is a signal that a channel dropped
    # out and which one, not a place to store a stack trace.
    _CHANNEL_ERROR_MAX_CHARS = 200

    for channel_name, future in futures.items():
        try:
            results = future.result(timeout=30)
            if channel_name == "title":
                title_results = results
            elif channel_name == "conditions":
                conditions_results = results
            elif channel_name == "criteria":
                criteria_results = results
            elif channel_name == "dense":
                vector_results = results
            retrieval_channels[channel_name] = {
                "status": CHANNEL_OK,
                "count": len(results),
                "error": "",
            }
        except _EmptySparseQuery as e:
            # The channel ran on a query with no BM25 terms. Qdrant would have
            # accepted it and returned nothing (verified against a real server
            # — see _sparse_query), so without this branch the channel would
            # report a clean zero.
            retrieval_channels[channel_name] = {
                "status": CHANNEL_EMPTY_QUERY,
                "count": 0,
                "error": str(e)[:_CHANNEL_ERROR_MAX_CHARS],
            }
            log.warning("retrieval channel skipped: the query carried no "
                        "BM25 terms", stage=2, channel=channel_name,
                        status=CHANNEL_EMPTY_QUERY, error_message=str(e))
        except Exception as e:
            retrieval_channels[channel_name] = {
                "status": CHANNEL_FAILED,
                "count": 0,
                "error": f"{type(e).__name__}: {e}"[:_CHANNEL_ERROR_MAX_CHARS],
            }
            log.warning("retrieval channel failed", stage=2,
                        channel=channel_name, status=CHANNEL_FAILED,
                        error_type=type(e).__name__, error_message=str(e))

    # ------------------------------------------------------------------
    # Weighted RRF fusion across all channels
    # ------------------------------------------------------------------
    # Convert each channel's ranked list to {nct_id: rank} dict
    def _to_rank_dict(results):
        seen = {}
        for rank, r in enumerate(results):
            nct_id = r.payload["nct_id"]
            if nct_id not in seen:
                seen[nct_id] = rank
        return seen

    title_ranks      = _to_rank_dict(title_results)
    conditions_ranks = _to_rank_dict(conditions_results)
    criteria_ranks   = _to_rank_dict(criteria_results)
    vector_ranks     = _to_rank_dict(vector_results)

    all_nct_ids = (
        set(title_ranks.keys()) | set(conditions_ranks.keys())
        | set(criteria_ranks.keys()) | set(vector_ranks.keys())
    )

    fusion_scores = {}
    for nct_id in all_nct_ids:
        score = 0.0
        if nct_id in title_ranks:
            score += WEIGHT_TITLE * (1.0 / (RRF_K + title_ranks[nct_id]))
        if nct_id in conditions_ranks:
            score += WEIGHT_CONDITIONS * (1.0 / (RRF_K + conditions_ranks[nct_id]))
        if nct_id in criteria_ranks:
            score += WEIGHT_CRITERIA * (1.0 / (RRF_K + criteria_ranks[nct_id]))
        if nct_id in vector_ranks:
            score += WEIGHT_DENSE * (1.0 / (RRF_K + vector_ranks[nct_id]))
        fusion_scores[nct_id] = score

    ranked_nct_ids = sorted(
        fusion_scores.items(),
        key=lambda x: (x[1], x[0]),
        reverse=True,
    )[:RRF_POOL_SIZE]

    # ------------------------------------------------------------------
    # Retrieve full trial data from payload
    # ------------------------------------------------------------------
    # Build payload map from all channels that returned payload
    payload_map = {}
    for results in (title_results, conditions_results, criteria_results, vector_results):
        for r in results:
            nct_id = r.payload.get("nct_id", "")
            if nct_id and nct_id not in payload_map:
                full_json = r.payload.get("full_trial_json")
                if full_json:
                    payload_map[nct_id] = full_json

    trials = []
    missing_nct_ids = []
    # Trials that were ranked into the fusion pool but whose payload could not
    # be recovered, so they never reached Stage 3. Counted rather than only
    # printed: this is a second way the pool silently shrinks.
    trials_lost = 0

    for nct_id, fusion_score in ranked_nct_ids:
        trial_data = payload_map.get(nct_id)
        if trial_data:
            trials.append({"trial": trial_data, "fusion_score": fusion_score})
        else:
            missing_nct_ids.append(nct_id)

    # Batch-fetch missing trials
    if missing_nct_ids:
        try:
            scroll_filter = {
                "should": [
                    {"key": "nct_id", "match": {"value": nct_id}}
                    for nct_id in missing_nct_ids
                ]
            }

            @qdrant_retry
            def _batch_scroll():
                return qdrant.scroll(
                    collection_name=COLLECTION_NAME,
                    scroll_filter=scroll_filter,
                    limit=len(missing_nct_ids),
                    with_payload=True,
                    timeout=20,
                )

            fetched_points, _ = _batch_scroll()
            fetched_map = {
                p.payload["nct_id"]: p.payload["full_trial_json"]
                for p in fetched_points
            }
            for nct_id in missing_nct_ids:
                trial_data = fetched_map.get(nct_id)
                if trial_data:
                    trials.append({
                        "trial": trial_data,
                        "fusion_score": fusion_scores[nct_id],
                    })
                else:
                    # Ranked in, but the backfill did not return it either.
                    trials_lost += 1
        except Exception as e:
            trials_lost += len(missing_nct_ids)
            log.warning("payload backfill scroll failed; ranked trials were "
                        "lost from the retrieval pool", stage=2,
                        event="payload_backfill_failed",
                        lost=len(missing_nct_ids),
                        error_type=type(e).__name__, error_message=str(e))

    elapsed = time.time() - start

    # Logging
    channel_counts = {
        "title": len(title_results),
        "conditions": len(conditions_results),
        "criteria": len(criteria_results),
        "dense": len(vector_results),
    }
    active_channels = [f"{k}={v}" for k, v in channel_counts.items() if v > 0]

    # ── Observed per-channel counts (logged to inferences) ────────────────
    #
    # BM25 runs as THREE field queries of BM25_RETRIEVAL_SIZE each, so summing
    # them would triple-count any trial that matched on more than one field.
    # The union of the per-channel rank dicts is the number of distinct trials
    # the sparse side actually contributed to fusion, which is the quantity a
    # fusion-efficiency ratio needs as its denominator.
    #
    # Both counts are observations, not configuration: under retrieval_mode
    # "bm25_only" the dense count is 0 and under "vector_only" the sparse count
    # is 0, and a channel whose query raised above (caught per channel, logged
    # as a WARNING) lands here as 0 as well.
    bm25_retrieved = len(
        set(title_ranks) | set(conditions_ranks) | set(criteria_ranks)
    )
    vector_retrieved = len(vector_ranks)

    # ── Channel-level degradation, as counts rather than as printed lines ──
    #
    # "Expected" is the number of channels this retrieval mode called for, so
    # an ablated channel is never counted as a loss: under bm25_only the run is
    # not degraded for having no dense results, it is configured that way.
    # Anything else that did not return — a raise, or a query with no BM25
    # terms — is the real thing, and degraded=1 makes it a WHERE clause.
    channels_expected = len(futures)
    channels_ok = sum(
        1 for c in retrieval_channels.values() if c["status"] == CHANNEL_OK
    )
    retrieval_degraded = int(channels_ok < channels_expected)

    if _retrieval_mode != "hybrid":
        mode_label = f"{_retrieval_mode} (ablation)"
    elif vector_results and title_results:
        mode_label = "multi-field hybrid"
    elif title_results:
        mode_label = "BM25-only (dense fallback)"
    else:
        mode_label = "dense-only (BM25 fallback)"

    # `disease_query` is NOT logged: it is the patient's primary diagnosis
    # display, verbatim. Its length is, because a suspiciously short one is
    # what a degraded expansion looks like from the outside.
    log.info("hybrid retrieval complete", stage=2, mode=mode_label,
             duration_s=round(elapsed, 3), trials_out=len(trials),
             channels={name: retrieval_channels[name]["status"]
                       for name in RETRIEVAL_CHANNELS},
             channels_ok=channels_ok, channels_expected=channels_expected,
             degraded=retrieval_degraded,
             query_length=len(disease_query),
             fusion_pool=len(all_nct_ids), ranked=len(ranked_nct_ids),
             bm25_retrieved=bm25_retrieved,
             bm25_requested=3 * BM25_RETRIEVAL_SIZE,
             vector_retrieved=vector_retrieved,
             vector_requested=VECTOR_RETRIEVAL_SIZE,
             lost=trials_lost)

    return {
        "hybrid_results": trials,
        "bm25_retrieved": bm25_retrieved,
        "vector_retrieved": vector_retrieved,
        # Per-channel outcome + the three scalars derived from it. These are
        # the record that the run used the retrieval it was configured for.
        "retrieval_channels": retrieval_channels,
        "retrieval_channels_expected": channels_expected,
        "retrieval_channels_ok": channels_ok,
        "retrieval_degraded": retrieval_degraded,
        "retrieval_trials_lost": trials_lost,
        "stage_timings": {
            **state.get("stage_timings", {}),
            "hybrid_retrieval": round(elapsed, 3),
        },
    }


# RRF constant for cross-encoder fusion (same as Stage 2 hybrid retrieval)
RERANK_RRF_K = 60


# Shape of the boost report when no boost pass ran at all. 'path' names which
# branch was taken so the ablation and the production runs are distinguishable
# in the logs.
_EMPTY_BOOST_STATS = {
    "path":            "not_run",
    "direct_boosted":  0,
    "pan_boosted":     0,
    "unboosted":       0,
    "boost_direct":    0.0,
    "boost_pan":       0.0,
    "rrf_spread":      0.0,
}


def apply_mesh_relevance_boost(top_trials: List[Dict],
                               patient_trees: set,
                               mesh_filter) -> Dict:
    """Add the MeSH relevance boost to each trial's rerank_score, in place.

    The cross-encoder ranks by text similarity, which treats a trial
    explicitly targeting "Prostatic Neoplasms" the same as a generic trial
    that mentions prostate in passing. MeSH ancestry is an authoritative
    clinical signal that identifies disease-specific trials.

    Applied at the end of Stage 3 so the boosted order propagates to the
    Stage 4 rule filter, benchmark Tier 3 ranking, and Streamlit display.

    Boost tiers (a FRACTION of the RRF spread, from 03- Config.py):
        DIRECT MATCH:  shares MeSH C04 ancestry with the patient
        PAN-CANCER:    targets a broad neoplasm category (depth <= 2)
        UNMAPPABLE:    no MeSH C04 trees -> boost 0 (neutral)

    Each trial keeps three fields, so ranking and gating stay separable:
        rerank_score_raw  unboosted fused RRF score (Stage 4 gates on this)
        mesh_boost        the additive boost, 0.0 when none applied
        mesh_boost_tier   "direct" | "pan_cancer" | "none"

    Returns a report dict (same keys as _EMPTY_BOOST_STATS). Mutates
    top_trials and re-sorts it by boosted score.
    """
    stats = dict(_EMPTY_BOOST_STATS)

    if not top_trials:
        stats["path"] = "no_trials"
        return stats

    if not patient_trees:
        # Patient side unmappable — the same conservative stance the MeSH
        # filter takes. Every trial keeps its raw score.
        stats["path"] = "no_patient_trees"
        stats["unboosted"] = len(top_trials)
        return stats

    # Stage 3 guard: a pan-cancer node is not a cancer identity.
    # C04 is a prefix of every descriptor in the tree, so a patient carrying
    # only C04 / a depth-2 node shares "ancestry" with every mapped trial:
    # specific trials would take the full direct boost while the genuine
    # basket trials take only the smaller pan boost — the ranking signal
    # inverted. resolve_patient_trees() already drops those trees; this is the
    # second gate, and it reports its own path so the run is distinguishable
    # from an unmappable patient.
    specific_patient_trees = specific_cancer_trees(patient_trees)
    if not specific_patient_trees:
        stats["path"] = "pan_cancer_only_patient_trees"
        stats["unboosted"] = len(top_trials)
        return stats
    patient_trees = specific_patient_trees

    # Calibrate boost from the batch's own RRF score distribution
    rr_scores = [t.get("rerank_score_raw", t.get("rerank_score", 0.0))
                 for t in top_trials]
    rr_spread = max(rr_scores) - min(rr_scores)

    if rr_spread > 1e-6:
        stats["path"] = "spread"
        boost_direct = rr_spread * MESH_BOOST_DIRECT_FRACTION
        boost_pan    = rr_spread * MESH_BOOST_PAN_FRACTION
    else:
        # Degenerate distribution (every trial tied): a fraction of the
        # spread would be exactly 0, so fall back to absolute floors.
        stats["path"] = "degenerate_spread_floor"
        boost_direct = MESH_BOOST_DIRECT_FLOOR
        boost_pan    = MESH_BOOST_PAN_FLOOR

    for trial_obj in top_trials:
        trial = trial_obj["trial"]
        trial_trees = mesh_filter.trial_mesh_trees(trial)

        if not trial_trees:
            stats["unboosted"] += 1
            continue

        if mesh_filter._is_pan_cancer(trial_trees):
            trial_obj["rerank_score"] += boost_pan
            trial_obj["mesh_boost"] = boost_pan
            trial_obj["mesh_boost_tier"] = "pan_cancer"
            stats["pan_boosted"] += 1
            continue

        has_ancestry = False
        for pt in patient_trees:
            for tt in trial_trees:
                if pt.startswith(tt) or tt.startswith(pt):
                    has_ancestry = True
                    break
            if has_ancestry:
                break

        if has_ancestry:
            trial_obj["rerank_score"] += boost_direct
            trial_obj["mesh_boost"] = boost_direct
            trial_obj["mesh_boost_tier"] = "direct"
            stats["direct_boosted"] += 1
        else:
            stats["unboosted"] += 1

    # Re-sort after boost to update ranking order
    top_trials.sort(
        key=lambda x: (x.get("rerank_score", 0), x["trial"]["nct_id"]),
        reverse=True,
    )

    stats["boost_direct"] = float(boost_direct)
    stats["boost_pan"]    = float(boost_pan)
    stats["rrf_spread"]   = float(rr_spread)
    return stats


def unboosted_score(trial_obj: Dict, default: float = -999.0) -> float:
    """Rerank score with the MeSH boost excluded.

    rerank_score_raw is written by Stage 3 for every trial. The fallback to
    rerank_score covers trial dicts built elsewhere (older rows replayed
    through the filter, hand-built fixtures) — in those the two are equal
    because no boost was ever added.
    """
    raw = trial_obj.get("rerank_score_raw")
    if raw is None:
        raw = trial_obj.get("rerank_score", default)
    return raw


def apply_quality_gate(trials: List[Dict],
                       percentile: float = None,
                       floor: float = None) -> tuple:
    """Drop weak trials using a percentile of the UNBOOSTED rerank score.

    Gating on the boosted score would measure whether a trial received a
    MeSH boost rather than whether it is any good: with a boost of 0.25 of
    the spread the whole boosted cohort sits above the 25th percentile by
    construction, so the survivors are the boosted set and the trials the
    MeSH filter deliberately KEPT as unmappable get cut here instead. That
    would be a second, uncounted MeSH filter. Gating on rerank_score_raw
    keeps the boost a ranking signal only.

    Returns (kept, threshold). Input order is preserved; callers sort by the
    boosted score before calling.
    """
    if percentile is None:
        percentile = QUALITY_THRESHOLD_PERCENTILE
    if floor is None:
        floor = RERANK_SCORE_THRESHOLD

    if not trials:
        return [], floor

    raw_scores = [unboosted_score(t) for t in trials]
    threshold = max(float(np.percentile(raw_scores, percentile)), floor)
    kept = [t for t in trials if unboosted_score(t) >= threshold]
    return kept, threshold


def node_cross_encoder_rerank(state: dict) -> dict:
    """
    Stage 3: Multi-query cross-encoder reranking with RRF fusion.

    Runs MedCPT Cross-Encoder once per rerank query (typically 3 passes),
    then fuses the per-query rankings via Reciprocal Rank Fusion (RRF).

    Why multi-query:
      MedCPT was trained on 2-10 word PubMed queries. Each rerank query is
      3-8 words targeting a different vocabulary dimension (MeSH, molecular,
      disease state). Three short queries give MedCPT native-format input
      and cover more matching surfaces than a single query.

    Why RRF:
      MedCPT raw scores are not normalized across queries (different queries
      produce different score ranges). RRF converts scores to ranks first,
      making fusion scale-independent. Trials ranked highly by multiple
      queries get the strongest boost — exactly the desired behavior.
    """
    start = time.time()

    rerank_queries = state.get("rerank_queries", [])
    trials = state["hybrid_results"]

    # Guard: no trials to rerank — pass through empty
    if not trials:
        log.info("cross-encoder rerank: nothing to rerank", stage=3,
                 trials_in=0, trials_out=0)
        return {
            "reranked_trials": [],
            # Declared on every Stage 3 exit so no downstream reader has to
            # distinguish "not resolved" from "key never written". Empty here
            # is correct: there is no pool for Stage 4 to filter.
            "patient_trees": set(),
            "stage_timings": {
                **state.get("stage_timings", {}),
                "cross_encoder": 0.0,
            },
        }
    
    # Resolved through the dependency seam, ONCE per call. File 13 read these
    # as module globals bound at exec time, which is what Files 35, 36, 45 and
    # 46 rebound to redirect the pipeline; a module function cannot see a
    # caller's globals, so the seam is what keeps those redirects working.
    # Once per call rather than per use so one invocation cannot see two
    # different objects if an override is installed mid-flight.
    cancer_registry = deps.get_cancer_registry()
    mesh_filter = deps.get_mesh_filter()

    # --- Ablation flags (read once) ---
    _ablation = state.get("ablation_flags") or {}
    # skip_mesh_filter removes BOTH MeSH uses: the Stage 3 boost here and the
    # Stage 4 hard drop. Disabling only the drop would leave the ablation row
    # confounded, since the boost still reorders (and re-gates) the pool.
    _skip_mesh_boost = _ablation.get("skip_mesh_filter", False)

    # -----------------------------------------------------------------
    # Patient MeSH resolution (must precede the skip_cross_encoder guard)
    # -----------------------------------------------------------------
    # Stage 3 is the only producer of state["patient_trees"], and Stage 4's
    # cancer site filter is its only consumer. Resolving inside the reranking
    # body meant the skip_cross_encoder early return handed Stage 4 an empty
    # set, silently disabling the MeSH filter as well and confounding the
    # no_cross_encoder ablation row with no_mesh_filter.
    #
    # skip_mesh_filter is the one flag that must leave the trees empty: that
    # ablation removes BOTH MeSH uses (this boost and the Stage 4 hard drop),
    # so it deliberately does not resolve. Every other path resolves.
    patient_trees = set()

    if _skip_mesh_boost:
        log.info("MeSH patient resolution skipped by ablation flag", stage=3,
                 mesh_path="ablation_skipped",
                 ablation_flag="skip_mesh_filter")
    elif mesh_filter is None:
        log.warning("MeSH patient resolution skipped: the filter is "
                    "unavailable", stage=3, mesh_path="no_mesh_filter",
                    degraded=True)
    else:
        # Same helper, same conditions and same verification filter as Stage 1,
        # so the trees handed to Stage 4 match the identity the expanded query
        # was built from — and the layer that produced them is the one already
        # recorded in mesh_resolution.
        mesh_resolution = resolve_patient_mesh(
            state["patient_data"].get("conditions", []),
            cancer_registry,
            mesh_filter,
        )
        patient_trees = mesh_resolution["trees"]
        # This replaces format_mesh_resolution(), which rendered the same
        # facts into one string and is deleted with this call site -- its
        # counts survive as fields, which is strictly more queryable, and the
        # TREES it also rendered do not, because a MeSH C04 tree number names
        # the patient's cancer site.
        log.info("MeSH patient resolution", stage=3,
                 mesh_resolution=mesh_resolution["resolution"],
                 trees_count=len(patient_trees),
                 conditions_resolved=mesh_resolution["conditions_resolved"],
                 conditions_total=mesh_resolution["conditions_total"],
                 conditions_pan_only=mesh_resolution["conditions_pan_only"],
                 conditions_unmapped=mesh_resolution["conditions_unmapped"],
                 pan_only_layers=mesh_resolution["pan_only_layers"])

    # --- Ablation: skip cross-encoder ---
    if _ablation.get("skip_cross_encoder", False):
        # Pass hybrid results through to rule filter without reranking.
        # Uses fusion_score (from Stage 2 RRF) as stand-in for rerank_score.
        #
        # Sort by fusion_score descending before capping at TOP_K_CANDIDATES.
        # hybrid_results is in insertion order (not score order) because
        # batch-scrolled trials are appended at the end. Without sorting,
        # [:TOP_K_CANDIDATES] would take the first 40 by insertion order,
        # potentially missing high-scoring scroll-fetched trials.
        #
        # The rule filter's dynamic quality threshold (25th percentile) and
        # MAX_TRIALS_FOR_EVALUATION cap still apply downstream. This only
        # removes the cross-encoder's contribution to ranking quality.
        log.info("cross-encoder rerank skipped by ablation flag", stage=3,
                 ablation_flag="skip_cross_encoder", trials_in=len(trials))
        sorted_trials = sorted(
            trials,
            key=lambda t: t.get("fusion_score", 0.0),
            reverse=True,
        )
        # No boost is applied on this path, so raw == boosted and the Stage 4
        # quality gate reads the same number it always did.
        passthrough = [
            {
                "trial":            t["trial"],
                "rerank_score":     t.get("fusion_score", 0.0),
                "rerank_score_raw": t.get("fusion_score", 0.0),
                "mesh_boost":       0.0,
                "mesh_boost_tier":  "none",
            }
            for t in sorted_trials[:TOP_K_CANDIDATES]
        ]
        return {
            "reranked_trials": passthrough,
            # Carried through: Stage 4 reads this and nothing else writes it.
            "patient_trees": patient_trees,
            "stage_timings": {
                **state.get("stage_timings", {}),
                "cross_encoder": 0.0,
            },
        }
    
    # Fallback: if no rerank queries, use expanded_query (degraded, old behavior)
    if not rerank_queries:
        log.warning("no rerank queries available; falling back to the "
                    "expanded query (degraded)", stage=3, degraded=True,
                    query_count=0)
        rerank_queries = [state["expanded_query"]]

    # -----------------------------------------------------------------
    # Build trial texts once (shared across all query passes)
    # -----------------------------------------------------------------
    trial_texts = []
    for trial_obj in trials:
        trial = trial_obj["trial"]
        # MedCPT max 512 tokens. With 3-8 token queries, ~500 tokens for
        # trial text ≈ 1850 chars. Keep 1600 char cap for safety margin.
        trial_text = (
            f"{trial['title']} {trial['eligibility']['criteria_text'][:1600]}"
        )
        trial_texts.append(trial_text)

    # -----------------------------------------------------------------
    # Score trials per query, collect per-query rankings
    # -----------------------------------------------------------------
    # per_query_ranks[trial_index] = list of ranks across queries
    per_query_ranks: Dict[int, List[int]] = {
        i: [] for i in range(len(trials))
    }

    per_query_stats = []  # for logging

    for q_idx, query in enumerate(rerank_queries):
        scores = models.score_pairs(query, trial_texts)

        # Log per-query score distribution
        per_query_stats.append({
            "query": query[:80],
            "min": float(scores.min()),
            "max": float(scores.max()),
            "mean": float(scores.mean()),
            "spread": float(scores.max() - scores.min()),
            "positive": int((scores > 0).sum()),
        })

        # Convert scores → ranks (0 = best)
        # NOTE: kind='stable' uses mergesort, which preserves input order for
        # tied values. Input order is the order trials appear in the hybrid
        # results list, which is now deterministic (from Edit C2). So the
        # cross-encoder ranking becomes fully deterministic.
        ranked_indices = np.argsort(-scores, kind='stable')  # descending, stable tiebreak
        
        for rank, trial_idx in enumerate(ranked_indices):
            per_query_ranks[trial_idx].append(rank)

    # -----------------------------------------------------------------
    # RRF fusion across queries
    # -----------------------------------------------------------------
    rrf_scores = {}
    for trial_idx, ranks in per_query_ranks.items():
        rrf_scores[trial_idx] = sum(
            1.0 / (RERANK_RRF_K + rank) for rank in ranks
        )

    # Sort by fused RRF score, keep top-K
    sorted_by_rrf = sorted(
         rrf_scores.items(),
         key=lambda x: (x[1], trials[x[0]]["trial"]["nct_id"]),  # tiebreak: NCT ID descending
         reverse=True
     )

    # rerank_score is the ranking score and may be boosted below.
    # rerank_score_raw is the untouched fused score the Stage 4 quality gate
    # is computed on; mesh_boost carries the difference between the two.
    top_trials = [
        {
            "trial":            trials[trial_idx]["trial"],
            "rerank_score":     float(rrf_score),
            "rerank_score_raw": float(rrf_score),
            "mesh_boost":       0.0,
            "mesh_boost_tier":  "none",
        }
        for trial_idx, rrf_score in sorted_by_rrf[:TOP_K_CANDIDATES]
    ]

    # -----------------------------------------------------------------
    # MeSH Relevance Boost (see apply_mesh_relevance_boost)
    # -----------------------------------------------------------------
    # patient_trees was resolved above, before the skip_cross_encoder guard.
    if _skip_mesh_boost:
        # The MeSH ablation must remove BOTH uses of MeSH, otherwise the
        # no_mesh_filter row still carries the boost's effect on ranking.
        log.info("MeSH relevance boost skipped by ablation flag", stage=3,
                 boost_path="ablation_skipped",
                 ablation_flag="skip_mesh_filter")
    elif mesh_filter is None:
        log.warning("MeSH relevance boost skipped: the filter is unavailable",
                    stage=3, boost_path="no_mesh_filter", degraded=True)
    elif top_trials:
        boost_stats = apply_mesh_relevance_boost(
            top_trials, patient_trees, mesh_filter
        )
        log.info("MeSH relevance boost applied", stage=3,
                 boost_path=boost_stats["path"],
                 boosted_direct=boost_stats["direct_boosted"],
                 boosted_pan=boost_stats["pan_boosted"],
                 unboosted=boost_stats["unboosted"],
                 boost_direct=round(boost_stats["boost_direct"], 5),
                 boost_pan=round(boost_stats["boost_pan"], 5))

    # -----------------------------------------------------------------
    # Logging
    # -----------------------------------------------------------------
    elapsed = time.time() - start

    rrf_values = [s for _, s in sorted_by_rrf]
    # The per-query score spread is what says whether the cross-encoder
    # discriminated at all, so it is kept -- as an aggregate across the
    # queries, with the QUERY TEXT dropped. Each stats["query"] is a patient
    # diagnosis string, and a per-query breakdown that carried it would put
    # one line per diagnosis into the record.
    log.info("multi-query cross-encoder rerank complete", stage=3,
             duration_s=round(elapsed, 3), query_count=len(rerank_queries),
             trials_in=len(trials), trials_out=len(top_trials),
             score_min=round(min((st["min"] for st in per_query_stats),
                                 default=0.0), 3),
             score_max=round(max((st["max"] for st in per_query_stats),
                                 default=0.0), 3),
             positive=sum(st["positive"] for st in per_query_stats),
             rrf_min=round(rrf_values[-1], 5) if rrf_values else None,
             rrf_max=round(rrf_values[0], 5) if rrf_values else None)

    return {
        "reranked_trials": top_trials,
        # Already empty when the MeSH filter is None or skip_mesh_filter is set.
        "patient_trees": patient_trees,
        "stage_timings": {
            **state.get("stage_timings", {}),
            "cross_encoder": round(elapsed, 3),
        },
    }


#------------------------------------------------------------------------------


# ===========================================================================
# BM25 INDEX BUILDER (called once before matching)
# ===========================================================================

def build_bm25_index_from_qdrant() -> Tuple[BM25Okapi, List[str]]:
    """
    Build BM25 index from trials stored in Qdrant.

    Uses pagination via scroll offset to handle collections
    larger than a single scroll batch.

    Returns:
        Tuple of (BM25Okapi index, list of NCT IDs in same order)
    """
    log.info("building the BM25 index from Qdrant",
             event="bm25_index_build_started", collection=COLLECTION_NAME)

    all_trials = []
    offset = None

    # Through the seam, resolved once. See node_hybrid_retrieval.
    qdrant = deps.get_qdrant_client()

    @qdrant_retry
    def _scroll_page(current_offset):
        return qdrant.scroll(
            collection_name=COLLECTION_NAME,
            limit=1000,
            offset=current_offset,
            with_payload=True,
            with_vectors=False
        )

    while True:
        scroll_response = _scroll_page(offset)

        points, next_offset = scroll_response
        all_trials.extend(points)

        if next_offset is None:
            break
        offset = next_offset

    # Extract texts and NCT IDs
    trial_texts = []
    nct_ids = []

    for trial in all_trials:
        bm25_text = trial.payload.get("bm25_text", "")
        nct_id = trial.payload.get("nct_id", "")

        tokenized = tokenize_for_bm25(bm25_text)
        trial_texts.append(tokenized)
        nct_ids.append(nct_id)

    bm25_index = BM25Okapi(trial_texts)

    log.info("BM25 index built", event="bm25_index_build_finished",
             collection=COLLECTION_NAME, count=len(trial_texts))

    return bm25_index, nct_ids


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
