"""The ONE place the FastEmbed BM25 sparse model is constructed.

Item 20c, pass 3a. This module exists because of a correctness hazard, not for
tidiness.

THE HAZARD
----------
Before this pass the project constructed ``SparseTextEmbedding("Qdrant/bm25")``
in THREE independent places:

    "11- RAG Trial Indexer.py" line 53      at INDEX time, module level, eager
    oncotriage/agent/deps.py                at QUERY time, lazily
    "12- RAG Trial Indexer Validator.py"    inside stage2_retrieval_tests()

The first two are the same model doing the two halves of one job: File 11 embeds
every trial's three BM25 fields into Qdrant's sparse vectors, and the agent
embeds the patient query that is scored against them. File 13's own comment said
so — "Same model used at index time (File 11) to generate document sparse
vectors."

Two loaders of one model is a silent correctness hazard. BM25 sparse vectors are
TOKEN-ID vectors: the indices are positions in the model's vocabulary. Change the
model name on one side and the two sides no longer share a vocabulary, so the
query's indices address different terms than the documents' indices do. Qdrant
still returns results — it computes a dot product over whatever indices it is
given — and every one of them is scored against the wrong terms. Nothing raises,
no counter moves, and the only visible symptom is that retrieval quality drops.
That is precisely the class of failure this project exists to remove.

So there is one construction site, and ``47- Package Split Test.py`` section 2f
asserts by ast that the count is exactly one and that it is here. A second call
to ``SparseTextEmbedding`` anywhere in the package fails that check.

WHAT THIS MODULE DOES NOT DO
----------------------------
It does not honour ``ONCOTRIAGE_DEFER_LOCAL_MODELS``. That switch selects
between two ways of running the AGENT: a replay harness serves every model
output from a recording and therefore needs no model, and the switch turns a
forgotten stand-in into a named RuntimeError instead of an AttributeError thirty
frames down. There is no replay path at index time — an index built from a
placeholder would be an index of nothing, written to Qdrant, swapped onto the
live alias, and indistinguishable from a real one. ``oncotriage.agent.deps``
keeps the switch and consults it BEFORE reaching this module, which is the only
place it means anything.

It is also NOT overridable. ``deps`` owns the override seam and its
``BM25_QUERY_MODEL`` key redirects the QUERY side, which is the side a fixture
harness records. The index side has no fixtures and no harness; a caller that
wants a different index-time encoder changes ``BM25_SPARSE_MODEL_NAME`` here,
which changes both sides together, which is the entire point.

WHAT IMPORTING THIS MODULE DOES
-------------------------------
Nothing. ``from fastembed import SparseTextEmbedding`` is inside the accessor,
the same deliberate exemption from the no-deferred-import rule that
``deps._build_medcpt_model`` and ``_build_icd10_cancer_sets`` carry: the rule
covers oncotriage-to-oncotriage edges, and hoisting this one would make
importing the indexer pull in onnxruntime and a tokenizer.
"""

import threading


#------------------------------------------------------------------------------


# THE MODEL NAME, and it is a fact about Qdrant's model registry rather than a
# tunable, which is why it is a named constant here and not in
# oncotriage/config.py. "Qdrant/bm25" is the identifier FastEmbed resolves to the
# BM25 vocabulary Qdrant's own sparse-vector documentation pairs with the IDF
# modifier that "11- RAG Trial Indexer.py" sets on all three sparse fields.
#
# CHANGING IT REBUILDS THE INDEX. The vectors already in Qdrant were produced by
# whatever this said when they were written; a query encoded by a different
# vocabulary addresses different terms and matches nothing meaningful. Change it
# and re-run '11- RAG Trial Indexer.py' in the same commit.
BM25_SPARSE_MODEL_NAME = "Qdrant/bm25"

# One lock over the one slot. Not because a second construction would be
# incorrect -- FastEmbed models are independent -- but because it would be a
# second copy of the vocabulary in memory, built while a caller waits, and
# "25- Batch Runner.py" drives twelve threads through the agent's side of this.
_LOCK = threading.RLock()
_MODEL = None


def get_bm25_sparse_model():
    """The FastEmbed BM25 sparse encoder. Built on first call, cached forever.

    Used at INDEX time by ``oncotriage.retrieval.indexer`` to produce the three
    per-field document sparse vectors, and at QUERY time by
    ``oncotriage.agent.deps`` (through ``get_bm25_query_model``) to produce the
    sparse query vector scored against them. One object, one vocabulary, one
    place it comes from.

    The cache is deliberately not resettable: swapping the encoder halfway
    through an index build would write two vocabularies into one collection.
    """
    global _MODEL
    with _LOCK:
        if _MODEL is None:
            from fastembed import SparseTextEmbedding

            print("Loading BM25 sparse embedding model (FastEmbed)...")
            _MODEL = SparseTextEmbedding(model_name=BM25_SPARSE_MODEL_NAME)
            print("BM25 sparse model loaded.\n")
        return _MODEL


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
