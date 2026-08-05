# RAG Trial Indexer Validator
#############################

"""
RAG Trial Indexer Validator

Validates that the Qdrant index built by 11- RAG Trial Indexer.py is healthy,
complete, and returning meaningful results before the LangGraph agent is
trusted for patient matching.

Three validation stages:
    1. Index Health    -- collection exists, point count sane, no corrupt payloads
    2. Retrieval Tests -- BM25 + vector search return results for known queries
    3. Summary Report  -- pass/fail per check, exit code 1 on any critical failure

Run from terminal (or F5 in Spyder):
    python "12- RAG Trial Indexer Validator.py"

Exit codes:
    0 -- all checks passed (or only warnings)
    1 -- one or more CRITICAL checks failed

THIN ENTRY POINT (item 20c, pass 3a)
------------------------------------
Every definition moved to ``oncotriage/retrieval/index_validator.py``. What is
left is the ``__main__`` block and the imports it needs.

No exec() bootstrap and no re-export shim: nothing in the repository chains this
file. It used to chain "13- LangGraph Agent.py" and read ``qdrant_client``,
``openai_client``, ``medcpt_tokenizer``, ``medcpt_model`` and ``torch`` out of
the resulting shared namespace; the package module reaches all five through
``oncotriage.agent.deps`` instead, which is the same seam the agent itself uses.
That matters here more than anywhere: this file's whole job is to say whether the
index is healthy FOR THE AGENT, so it has to ask the agent's questions of the
agent's objects.

It also no longer builds its own copy of the BM25 sparse encoder. It had one --
a third independent ``SparseTextEmbedding("Qdrant/bm25")``, alongside File 11's
and the agent's -- and a validator carrying its own encoder cannot detect the
index/query vocabulary drift it exists to catch.
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py". `pip install -e .` makes it
# a no-op; without it the code directory is added to sys.path and the fact is
# printed rather than left silent.
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

from oncotriage.config import Project_Name
from oncotriage.retrieval.index_validator import (
    CheckResult,
    _fail,
    print_summary,
    stage1_index_health,
    stage2_retrieval_tests,
)


#------------------------------------------------------------------------------


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
