"""Building and validating the Qdrant trial index.

Item 20c, pass 3a.

    indexer           "11- RAG Trial Indexer.py" whole — scrape
                      ClinicalTrials.gov, build the dense + three-field BM25
                      sparse vectors, index into a staging collection, swap the
                      ``trial_criteria`` alias atomically, clean up old
                      collections.

    index_validator   "12- RAG Trial Indexer Validator.py" whole — nine checks
                      over the live collection: it exists, the point count is
                      sane, the vector dimensionality matches, payloads are
                      complete, all three sparse fields respond, both retrieval
                      channels return results for five smoke queries, and the
                      cross-encoder scores a pair.

THE TWO REACH THEIR CLIENTS DIFFERENTLY, and it is deliberate.

``indexer`` uses ``oncotriage.config.get_qdrant_client()`` /
``get_openai_client()``. ``index_validator`` uses
``oncotriage.agent.deps.get_qdrant_client()`` and friends.

The split follows what each one is FOR. The indexer WRITES the index; it must
build the real thing against the real endpoint, and a stub installed by a test
harness redirecting an index build would be a defect, not a feature. The
validator asks "is the index the AGENT will query healthy", so it has to reach
what the agent reaches -- through the same seam, including the MedCPT tokenizer
and cross-encoder accessors, which exist only in ``deps``.

That also keeps the import direction right: ``retrieval.indexer`` does not
import ``agent``.

BOTH SIDES OF THE BM25 MODEL COME FROM ONE PLACE. ``indexer`` gets the sparse
encoder from ``oncotriage.embedding``, and so does ``agent.deps``. Before this
pass they were built independently from the same model name, which is a silent
retrieval-quality hazard the moment the two names diverge -- see
``oncotriage/embedding.py``.

This ``__init__`` imports no submodule. ``import oncotriage.retrieval`` stays
free; the caller names the module it wants.
"""

__all__ = ["indexer", "index_validator"]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
