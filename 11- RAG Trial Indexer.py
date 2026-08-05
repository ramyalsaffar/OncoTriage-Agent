# Scrape Clinical Trials Data from the clinicaltrials.gov API
#############################################################


"""
Trial RAG Indexer (TRIAL-LEVEL EMBEDDINGS + BM25)
Scrapes trials from ClinicalTrials.gov, creates trial-level embeddings,
and prepares data for hybrid BM25 + Vector retrieval.

THIN ENTRY POINT (item 20c, pass 3a)
------------------------------------
Every definition moved to ``oncotriage/retrieval/indexer.py``. What is left is
the argparse ``__main__`` block and the import it needs.

No exec() bootstrap and no re-export shim: nothing in the repository chains this
file. Every top-level name it defined was grepped against every .py, .md, .toml
and .yml in the tree; ``parse_trial_metadata`` is named in prose by File 30 and
by two package docstrings, and ``main`` collides with same-named ``main``
functions that eight other files define for themselves. No file reads a name out
of this one's namespace.

TWO CONSEQUENCES WORTH NAMING.

Importing this file no longer LOADS A MODEL. File 11 line 53 built
``SparseTextEmbedding("Qdrant/bm25")`` at module level and printed as it went, so
even ``--help`` paid for it. The model now comes from
``oncotriage.embedding.get_bm25_sparse_model()``, on first use, and it is the
same object the agent's query encoder uses -- one construction site for a model
whose two halves have to agree or retrieval silently degrades.

The ARGUMENT SURFACE is unchanged, and that is asserted rather than assumed: the
argparse section of ``--help`` was captured before and after this change and
diffed byte for byte.
"""

import argparse
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

from oncotriage.retrieval.indexer import main


#------------------------------------------------------------------------------


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '--mode',
        choices=['staging', 'direct'],
        default='staging',
        help='staging: zero downtime (default) | direct: causes downtime'
    )
    args = parser.parse_args()

    main(use_staging=(args.mode == 'staging'))


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb 10 2026

@author: ramyalsaffar
"""
