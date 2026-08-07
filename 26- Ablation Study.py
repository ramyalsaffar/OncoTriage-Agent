# Ablation Study
################

"""
Ablation Study — entry point.

Measures the contribution of each pipeline stage by running the full matching
pipeline with one stage disabled at a time on a stratified patient sample.

The study itself is ``oncotriage/ablation/study.py``. Item 20c pass 3d moved it
there; this file is a ``__main__`` block and the one import it needs.

Ablation configurations (7)
---------------------------
  1. full_pipeline       — all stages active (baseline)
  2. no_mesh_filter      — skip MeSH cancer site relevance filter
  3. no_stage_filter     — skip cancer stage mismatch filter
  4. no_histology_filter — skip histology mismatch filter
  5. no_cross_encoder    — skip MedCPT cross-encoder reranking
  6. bm25_only           — disable vector search (BM25 retrieval only)
  7. vector_only         — disable BM25 (vector retrieval only)

Each config runs on the SAME stratified patient sample; only one variable
changes per config. Flags ride in the LangGraph state as ``ablation_flags`` and
are read at three points (nodes 2, 3 and 4). Default is ``{}`` — all stages
active — so the production pipeline, the FastAPI server and the batch runner are
unaffected.

Output
------
  ablation_results.db     SQLite, in result_ablation_path, separate from the
                          production inferences.db so an ablation run never
                          reaches drift detection or the Reproducibility tab.
  ablation_summary.json   machine-readable summary for the paper figures.
  a console table with per-config averages and deltas against the baseline.

``--db PATH`` (pass 20f-1) writes the study to a different SQLite file, with
``ablation_summary.json`` beside it. Until that pass this was the last database
writer in the project whose path could not be overridden, which is why it was
also the only one with no isolation test;
``tests/test_ablation_db_isolation.py`` is that test. PASS 20f-3 CLOSED THE TWO
THINGS THAT PASS RECORDED AS FOLLOW-UPS: the CHECKPOINT follows ``--db`` now
(beside the database, named after it -- before, an isolated run read the
production resume file, skipped every pair a production run had done, wrote
nothing for them and still printed ``Status: COMPLETE``), and a ``--db`` whose
PARENT DIRECTORY is missing is refused by name instead of reaching sqlite3 and
coming back as "unable to open database file", which names neither the path nor
the flag. Both are argued in ``oncotriage/ablation/study.py``.

THIS COSTS MONEY. 7 configs × 75 patients = 525 live pipeline runs, each with a
Stage 5 call. Roughly $2.50–$4.00 and 3–5 hours at the default sample size. The
run is checkpointed per (config, patient), so an interrupted study resumes with
the same command and pays nothing for what it already did.

NO RE-EXPORT SHIM. Nothing in the repository chained this file or read a name out
of it: all 28 of its top-level names were grepped against every .py, .md, .toml
and .yml in the tree, and the only hits are File 27's own ``ABLATION_DB`` over
the same directory, a prose mention of ``ABLATION_CONFIGS`` in File 27's comment,
two prose mentions of ``log_ablation_result``, and the exec-bootstrap locals
(``_code_dir``, ``_bootstrap``, ``_fh``, ``_os_boot``) that every numbered file
shares.

Usage
-----
    python "26- Ablation Study.py"                   # full run (75 patients)
    python "26- Ablation Study.py" --sample-size 20  # quick test
    python "26- Ablation Study.py" --summary-only    # reprint from the database
    python "26- Ablation Study.py" --configs full_pipeline no_mesh_filter
    python "26- Ablation Study.py" --db /tmp/scratch/ablation_results.db
"""

import os
import sys


# Make the oncotriage package importable
#---------------------------------------
# See the same block in "04- FHIR Generate Data.py" and "29- Download Qdrant
# Data.py". `pip install -e .` from 03- Code/ makes it a no-op.
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

from oncotriage.ablation.study import main


#------------------------------------------------------------------------------


if __name__ == "__main__":
    main()


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Mar 02 2026

@author: ramyalsaffar
"""
