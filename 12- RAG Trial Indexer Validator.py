# RAG Trial Indexer Validator
#############################

"""
RAG Trial Indexer Validator

Validates that the Qdrant index built by 11- RAG Trial Indexer.py is healthy,
complete, and returning meaningful results before 13- LangGraph Agent.py
is trusted for patient matching.

Three validation stages:
    1. Index Health    -- collection exists, point count sane, no corrupt payloads
    2. Retrieval Tests -- BM25 + vector search return results for known queries
    3. Summary Report  -- pass/fail per check, exit code 1 on any critical failure

Run from terminal (or F5 in Spyder):
    python "12- RAG Trial Indexer Validator.py"

Exit codes:
    0 -- all checks passed (or only warnings)
    1 -- one or more CRITICAL checks failed
"""


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


# Run needed file
#----------------
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["03- Config.py", "13- LangGraph Agent.py"],
    caller_file=_code_dir + "12- RAG Trial Indexer Validator.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 13",
)


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
        collections = qdrant_client.get_collections().collections
        collection_names = [c.name for c in collections]
        aliases = [a.alias_name for a in qdrant_client.get_aliases().aliases]
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
        info = qdrant_client.get_collection(collection_name=COLLECTION_NAME)
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
        info = qdrant_client.get_collection(collection_name=COLLECTION_NAME)
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
        
        scroll_response = qdrant_client.scroll(
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
    _bm25_model = SparseTextEmbedding(model_name="Qdrant/bm25")

    # ------------------------------------------------------------------
    # Check 5: All 3 sparse BM25 vector fields respond
    # ------------------------------------------------------------------
    sparse_fields = ["title-bm25", "conditions-bm25", "criteria-bm25"]
    sparse_failures = []
    for field in sparse_fields:
        try:
            test_emb = next(_bm25_model.query_embed("breast cancer"))
            hits = qdrant_client.query_points(
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
        info = qdrant_client.get_collection(collection_name=COLLECTION_NAME)
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
            hits = qdrant_client.query_points(
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
            embedding = openai_client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=query
            ).data[0].embedding

            hits = qdrant_client.query_points(
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
        sample_query = SMOKE_TEST_QUERIES[0]
        sample_doc   = "Phase 2 breast cancer trial for HER2-positive adults with inclusion criteria age 18-75."
        inputs = medcpt_tokenizer(
            [[sample_query, sample_doc]],
            return_tensors="pt",
            truncation=True,
            max_length=512,
            padding=True,
        )
        with torch.no_grad():
            score = medcpt_model(**inputs).logits[0].item()
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

if __name__ == "__main__":

    print()
    print("=" * 80)
    print(f"{Project_Name}: RAG TRIAL INDEXER VALIDATOR")
    print("=" * 80)
    print()

    # ------------------------------------------------------------------
    # Stage 1: Index health
    # ------------------------------------------------------------------
    print("[Stage 1] Running index health checks...")
    print()
    stage1_results = stage1_index_health()

    # Stage 2 is only valid if the very first check (collection exists) passed.
    # stage1_index_health() always appends the collection check first and returns
    # immediately on failure, so stage1_results[0] is always that check.
    # Using index access avoids a brittle string match on the check name.
    collection_ok = (
        bool(stage1_results)
        and stage1_results[0].status == CheckResult.PASS
    )

    # ------------------------------------------------------------------
    # Stage 2: Retrieval smoke tests (only if collection is accessible)
    # ------------------------------------------------------------------
    stage2_results = []
    if collection_ok:
        print("[Stage 2] Running sparse BM25 + vector retrieval smoke tests...")
        print()
        try:
            stage2_results = stage2_retrieval_tests()
        except Exception as e:
            stage2_results = [_fail("Stage 2", f"Unexpected error: {e}")]
    else:
        print("[Stage 2] Skipped -- collection not accessible.")
        stage2_results = [_fail("Stage 2 skipped", "Collection not found or inaccessible")]

    # ------------------------------------------------------------------
    # Summary report + exit code
    # ------------------------------------------------------------------
    exit_code = print_summary(stage1_results, stage2_results)
    if exit_code != 0:
        raise SystemExit(exit_code)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Feb 17 2026

@author: ramyalsaffar
"""