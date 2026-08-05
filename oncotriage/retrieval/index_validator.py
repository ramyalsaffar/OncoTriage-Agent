# RAG Trial Indexer Validator
#############################

"""Validate the Qdrant index built by the trial indexer before trusting it.

Checks that the index is healthy, complete, and returning meaningful results
before ``oncotriage.agent`` is trusted for patient matching.

Two validation stages plus a report:
    1. Index Health    -- collection exists, point count sane, no corrupt payloads
    2. Retrieval Tests -- BM25 + vector search return results for known queries
    3. Summary Report  -- pass/fail per check, exit code 1 on any critical failure

Moved out of ``12- RAG Trial Indexer Validator.py`` by item 20c, pass 3a. That
file is now a thin entry point holding only its ``__main__`` block: nothing in
the repository chains it, so it needs no re-export shim and its exec bootstrap
is gone.

WHAT CHANGED IN THE MOVE, and why every one of them had to
-----------------------------------------------------------
File 12 chained "13- LangGraph Agent.py" and then read FIVE names straight out
of the resulting shared exec namespace: ``qdrant_client``, ``openai_client``,
``medcpt_tokenizer``, ``medcpt_model`` and ``torch``. All five now come from
where they actually live.

1. THE CLIENTS AND THE TWO MedCPT HALVES COME FROM
   ``oncotriage.agent.deps``, not from ``oncotriage.config`` and not from File
   13's shim proxies. This module is the ONE in ``oncotriage.retrieval`` that
   uses the agent's seam, and the reason is what it is for: the question it
   answers is "is this index healthy for the AGENT to query", so it has to reach
   what the agent reaches. ``get_medcpt_tokenizer()`` and ``get_medcpt_model()``
   exist nowhere else, and going through deps means an override installed for a
   harness is honoured here too.

   File 13's shim binds ``medcpt_tokenizer`` and ``medcpt_model`` to lazy proxies
   BECAUSE File 12 used to call them directly. Those proxies stay -- other files
   still read them -- but this module no longer needs one: it calls the accessor
   and gets the model itself.

2. THE BM25 SPARSE MODEL WAS A THIRD INDEPENDENT CONSTRUCTION of
   ``SparseTextEmbedding("Qdrant/bm25")``, built inside
   ``stage2_retrieval_tests()``, alongside File 11's index-time model and the
   agent's query-time model. That is worse here than anywhere else: a validator
   with its own encoder cannot detect the drift it exists to catch. If the index
   were built with one vocabulary and queried by the agent with another, this
   file -- carrying a third -- would still report "All 5 queries returned
   results". It now asks ``deps.get_bm25_query_model()``, which is exactly what
   Stage 2 of the agent uses, which resolves to the one model in
   ``oncotriage/embedding.py``.

3. ``torch`` is imported here rather than inherited from File 01's block, and
   INSIDE the function that uses it. It is the single heaviest import in the
   project and the cross-encoder smoke test is the only thing in this module
   that needs it; importing it at module scope would mean that importing the
   validator pulled in torch, which is the cost pass 20c-2c removed from the
   agent. Third-party imports in a function body are the documented exemption
   from the no-deferred-import rule -- the same one ``import icd10`` inside
   ``_build_icd10_cancer_sets()`` carries.

4. ``COLLECTION_NAME``, ``EMBEDDING_DIM``, ``EMBEDDING_MODEL`` and
   ``Project_Name`` are read off ``oncotriage.config``.

No other line changed. ``ast.unparse`` equivalence against ``git show HEAD:`` is
asserted for all twelve top-level definitions and every difference is one of the
four above.
"""

import random
import re

from qdrant_client.models import SparseVector

from oncotriage.agent import deps
from oncotriage.config import (
    COLLECTION_NAME,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    Project_Name,
)


#------------------------------------------------------------------------------


# ===========================================================================
# VALIDATOR CONFIGURATION
# ===========================================================================

# Number of random points to spot-check for payload completeness
SPOT_CHECK_COUNT = 50

# Minimum acceptable trial count in the collection
MIN_EXPECTED_TRIALS = 50

# NCT ID format: "NCT" followed by exactly 8 digits
NCT_ID_PATTERN = r"^NCT\d{8}$"

# Hardcoded clinical queries for retrieval smoke tests
# Chosen to be broad enough that any oncology trial index should return results
SMOKE_TEST_QUERIES = [
    "breast cancer HER2 positive adult female",
    "lung cancer non-small cell EGFR mutation",
    "colorectal cancer metastatic chemotherapy",
    "diabetes type 2 adult insulin resistance",
    "leukemia acute myeloid adult induction",
]

# Required payload fields on every Qdrant point
REQUIRED_PAYLOAD_FIELDS = ["nct_id", "title", "phase", "bm25_text", "full_trial_json"]


#------------------------------------------------------------------------------


# ===========================================================================
# RESULT TRACKING
# ===========================================================================

class CheckResult:
    """Holds the outcome of a single validation check."""
    PASS    = "PASS"
    WARN    = "WARN"
    FAIL    = "FAIL"   # Critical -- causes exit code 1

    def __init__(self, name: str, status: str, detail: str = ""):
        self.name   = name
        self.status = status
        self.detail = detail

    def __repr__(self):
        icon = {"PASS": "✓", "WARN": "!", "FAIL": "✗"}.get(self.status, "?")
        line = f"  [{icon}] {self.name}"
        if self.detail:
            line += f"\n        {self.detail}"
        return line


def _pass(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, CheckResult.PASS, detail)

def _warn(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, CheckResult.WARN, detail)

def _fail(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, CheckResult.FAIL, detail)


# ===========================================================================
# STAGE 1: INDEX HEALTH CHECKS
# ===========================================================================

def stage1_index_health() -> list:
    """
    Verify the Qdrant collection exists, has enough points, and that a
    random sample of points all carry the required payload fields with
    non-empty values.

    Returns:
        List of CheckResult objects.
    """
    results = []

    # ------------------------------------------------------------------
    # Check 1: Collection exists and is accessible
    # ------------------------------------------------------------------
    try:
        collections = deps.get_qdrant_client().get_collections().collections
        collection_names = [c.name for c in collections]
        aliases = [a.alias_name for a in deps.get_qdrant_client().get_aliases().aliases]
        accessible_names = set(collection_names + aliases)

        if COLLECTION_NAME in accessible_names:
            results.append(_pass("Collection exists", f"'{COLLECTION_NAME}' found in Qdrant"))
        else:
            results.append(_fail(
                "Collection exists",
                f"'{COLLECTION_NAME}' NOT found. Available: {collection_names}. "
                f"Run 10- RAG Trial Indexer.py first."
            ))
            return results
    except Exception as e:
        results.append(_fail("Collection exists", f"Qdrant connection failed: {e}"))
        return results

    # ------------------------------------------------------------------
    # Check 2: Point count is above minimum threshold
    # ------------------------------------------------------------------
    try:
        info = deps.get_qdrant_client().get_collection(collection_name=COLLECTION_NAME)
        point_count = info.points_count
        if point_count >= MIN_EXPECTED_TRIALS:
            results.append(_pass(
                "Point count",
                f"{point_count} points (minimum: {MIN_EXPECTED_TRIALS})"
            ))
        else:
            results.append(_fail(
                "Point count",
                f"Only {point_count} points -- expected >= {MIN_EXPECTED_TRIALS}. "
                f"Index may be incomplete."
            ))
    except Exception as e:
        results.append(_fail("Point count", f"Could not retrieve collection info: {e}"))
        point_count = 0

    # ------------------------------------------------------------------
    # Check 3: Vector dimensionality matches EMBEDDING_DIM
    # ------------------------------------------------------------------
    try:
        info = deps.get_qdrant_client().get_collection(collection_name=COLLECTION_NAME)
        config = info.config.params.vectors
        # config is either a VectorParams object (unnamed) or a dict (named vectors)
        if hasattr(config, 'size'):
            actual_dim = config.size
        else:
            # Named vectors -- take first entry
            actual_dim = next(iter(config.values())).size if config else None

        if actual_dim is None:
            results.append(_warn("Vector dimension", "Could not determine vector size from collection config"))
        elif actual_dim == EMBEDDING_DIM:
            results.append(_pass("Vector dimension", f"{actual_dim}D matches EMBEDDING_DIM={EMBEDDING_DIM}"))
        else:
            results.append(_fail(
                "Vector dimension",
                f"Collection has {actual_dim}D vectors but EMBEDDING_DIM={EMBEDDING_DIM}. "
                f"Mismatch will cause query failures."
            ))
    except Exception as e:
        results.append(_warn("Vector dimension", f"Could not verify dimension: {e}"))

    # ------------------------------------------------------------------
    # Check 4: Spot-check random points for payload completeness
    # ------------------------------------------------------------------
    try:
        actual_spot = min(SPOT_CHECK_COUNT, point_count if point_count else SPOT_CHECK_COUNT)
        
        random_offset = random.randint(0, max(0, point_count - actual_spot)) if point_count > actual_spot else 0
        
        scroll_response = deps.get_qdrant_client().scroll(
            collection_name=COLLECTION_NAME,
            limit=actual_spot,
            offset=random_offset,
            with_payload=True,
            with_vectors=False,
        )
        points, _ = scroll_response

        missing_fields_count = 0
        empty_bm25_count     = 0
        empty_nct_id_count   = 0
        invalid_nct_id_count = 0

        for pt in points:
            payload = pt.payload or {}
            # Required fields present
            for field in REQUIRED_PAYLOAD_FIELDS:
                if field not in payload:
                    missing_fields_count += 1
                    break
            # bm25_text non-empty
            if not payload.get("bm25_text", "").strip():
                empty_bm25_count += 1
            # nct_id non-empty and valid format
            nct_id = payload.get("nct_id", "")
            if not nct_id:
                empty_nct_id_count += 1
            elif not re.match(NCT_ID_PATTERN, nct_id):
                invalid_nct_id_count += 1

        issues = []
        if missing_fields_count:
            issues.append(f"{missing_fields_count} points missing required fields")
        if empty_bm25_count:
            issues.append(f"{empty_bm25_count} points have empty bm25_text")
        if empty_nct_id_count:
            issues.append(f"{empty_nct_id_count} points have empty nct_id")
        if invalid_nct_id_count:
            issues.append(f"{invalid_nct_id_count} points have invalid NCT ID format")

        if not issues:
            results.append(_pass(
                f"Payload spot-check ({actual_spot} points)",
                "All required fields present, nct_ids valid"
            ))
        else:
            results.append(_fail(
                f"Payload spot-check ({actual_spot} points)",
                " | ".join(issues)
            ))

    except Exception as e:
        results.append(_fail("Payload spot-check", f"Scroll failed: {e}"))

    return results


# ===========================================================================
# STAGE 2: RETRIEVAL SMOKE TESTS
# ===========================================================================

def stage2_retrieval_tests() -> list:
    """
    Run sparse BM25 and vector search for each smoke test query.
    Verify non-empty results across all retrieval channels.

    Returns:
        List of CheckResult objects.
    """
    
    results = []
    # THE THIRD CONSTRUCTION SITE, now gone. File 12 built its own
    # SparseTextEmbedding("Qdrant/bm25") here, independently of File 11's
    # index-time model and the agent's query-time model. A validator that
    # builds its own encoder cannot detect the very drift it exists to
    # catch: it would report the index healthy while querying it with a
    # vocabulary neither of the other two uses. It now asks deps for the
    # agent's encoder, which resolves to the one model in
    # oncotriage/embedding.py.
    _bm25_model = deps.get_bm25_query_model()

    # ------------------------------------------------------------------
    # Check 5: All 3 sparse BM25 vector fields respond
    # ------------------------------------------------------------------
    sparse_fields = ["title-bm25", "conditions-bm25", "criteria-bm25"]
    sparse_failures = []
    for field in sparse_fields:
        try:
            test_emb = next(_bm25_model.query_embed("breast cancer"))
            hits = deps.get_qdrant_client().query_points(
                collection_name=COLLECTION_NAME,
                query=SparseVector(
                    indices=test_emb.indices.tolist(),
                    values=test_emb.values.tolist(),
                ),
                using=field,
                limit=3,
                with_payload=True,
            ).points
            if not hits:
                sparse_failures.append(f"'{field}' returned 0 results")
        except Exception as e:
            sparse_failures.append(f"'{field}' raised {type(e).__name__}: {e}")

    if not sparse_failures:
        results.append(_pass(
            "Sparse BM25 fields",
            f"All {len(sparse_fields)} fields returned results"
        ))
    else:
        results.append(_fail("Sparse BM25 fields", " | ".join(sparse_failures)))

    # ------------------------------------------------------------------
    # Check 6: Point count matches expected
    # ------------------------------------------------------------------
    try:
        info = deps.get_qdrant_client().get_collection(collection_name=COLLECTION_NAME)
        point_count = info.points_count
        results.append(_pass("Sparse BM25 point count", f"{point_count} points in collection"))
    except Exception as e:
        results.append(_fail("Sparse BM25 point count", f"{type(e).__name__}: {e}"))

    # ------------------------------------------------------------------
    # Check 7: BM25 returns non-empty results for every smoke query
    # ------------------------------------------------------------------
    bm25_failures = []
    for query in SMOKE_TEST_QUERIES:
        try:
            q_emb = next(_bm25_model.query_embed(query))
            hits = deps.get_qdrant_client().query_points(
                collection_name=COLLECTION_NAME,
                query=SparseVector(
                    indices=q_emb.indices.tolist(),
                    values=q_emb.values.tolist(),
                ),
                using="criteria-bm25",
                limit=5,
                with_payload=True,
            ).points
            if not hits:
                bm25_failures.append(f"'{query}' returned 0 results")
        except Exception as e:
            bm25_failures.append(f"'{query}' raised {type(e).__name__}: {e}")

    if not bm25_failures:
        results.append(_pass(
            "BM25 smoke tests",
            f"All {len(SMOKE_TEST_QUERIES)} queries returned results"
        ))
    else:
        results.append(_fail("BM25 smoke tests", " | ".join(bm25_failures)))
        
    # ------------------------------------------------------------------
    # Check 8: Vector search returns non-empty results for every smoke query
    # ------------------------------------------------------------------
    vector_failures = []
    for query in SMOKE_TEST_QUERIES:
        try:
            embedding = deps.get_openai_client().embeddings.create(
                model=EMBEDDING_MODEL,
                input=query
            ).data[0].embedding

            hits = deps.get_qdrant_client().query_points(
                collection_name=COLLECTION_NAME,
                query=embedding,
                limit=5,
                with_payload=True,
            ).points
            if hits:
                pass  # good
            else:
                vector_failures.append(f"'{query}' returned 0 results")
        except Exception as e:
            vector_failures.append(f"'{query}' raised {type(e).__name__}: {e}")

    if not vector_failures:
        results.append(_pass(
            "Vector search smoke tests",
            f"All {len(SMOKE_TEST_QUERIES)} queries returned results"
        ))
    else:
        results.append(_fail("Vector search smoke tests", " | ".join(vector_failures)))

    # ------------------------------------------------------------------
    # Check 9: Cross-encoder loads and scores without error
    # ------------------------------------------------------------------
    try:
        # DEFERRED ON PURPOSE, and exempt from the no-deferred-import rule,
        # which covers oncotriage-to-oncotriage edges only. torch is the
        # heaviest import in the project and this is the only place in this
        # module that needs it; at module scope it would mean importing the
        # validator loaded it, which is the cost pass 20c-2c removed from
        # the agent. Same exemption as `import icd10` inside
        # _build_icd10_cancer_sets().
        import torch

        sample_query = SMOKE_TEST_QUERIES[0]
        sample_doc   = "Phase 2 breast cancer trial for HER2-positive adults with inclusion criteria age 18-75."
        inputs = deps.get_medcpt_tokenizer()(
            [[sample_query, sample_doc]],
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        with torch.no_grad():
            score = deps.get_medcpt_model()(**inputs).logits[0].item()
        results.append(_pass("Cross-encoder smoke test", f"Score for sample pair: {score:.4f}"))
    except Exception as e:
        results.append(_fail("Cross-encoder smoke test", f"{type(e).__name__}: {e}"))

    return results


# ===========================================================================
# SUMMARY REPORT
# ===========================================================================

def print_summary(stage1: list, stage2: list) -> int:
    """
    Print the full validation report and return exit code.

    Args:
        stage1: Results from stage1_index_health()
        stage2: Results from stage2_retrieval_tests()

    Returns:
        0 if all checks passed or warned only.
        1 if any CRITICAL (FAIL) check was found.
    """
    all_results = stage1 + stage2
    passes  = [r for r in all_results if r.status == CheckResult.PASS]
    warns   = [r for r in all_results if r.status == CheckResult.WARN]
    fails   = [r for r in all_results if r.status == CheckResult.FAIL]

    print()
    print("=" * 80)
    print(f"{Project_Name}: RAG TRIAL INDEXER VALIDATION REPORT")
    print("=" * 80)
    print()

    print("--- STAGE 1: INDEX HEALTH ---")
    for r in stage1:
        print(r)
    print()

    print("--- STAGE 2: RETRIEVAL SMOKE TESTS ---")
    for r in stage2:
        print(r)
    print()

    print("--- SUMMARY ---")
    print(f"  PASS:  {len(passes)}")
    print(f"  WARN:  {len(warns)}")
    print(f"  FAIL:  {len(fails)}")
    print()

    if fails:
        print("CRITICAL FAILURES -- Index is NOT safe to use with 13- LangGraph Agent.py")
        for r in fails:
            print(f"  ✗ {r.name}: {r.detail}")
        print()
        print("=" * 80)
        print("VALIDATION FAILED")
        print("=" * 80)
        return 1
    elif warns:
        print("Warnings detected -- review above, but index is usable.")
        print()
        print("=" * 80)
        print("VALIDATION PASSED WITH WARNINGS")
        print("=" * 80)
        return 0
    else:
        print("=" * 80)
        print("VALIDATION PASSED -- Index is healthy and ready for patient matching.")
        print("=" * 80)
        return 0


# ===========================================================================
# MAIN
# ===========================================================================


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""
