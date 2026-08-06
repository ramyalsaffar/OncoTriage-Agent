"""The 6-stage LangGraph matching pipeline.

Item 20c, pass 2c. This is "13- LangGraph Agent.py", 5,565 lines, split into
twelve modules. The numbered file survives as an explicit re-export shim.

    deps            THE SEAM. Every client, model and registry the agent uses,
                    each lazily resolved, cached, and overridable by name.
    state           TrialMatchState and the degradation vocabularies.
    text            tokenize_for_bm25, shared with index time.
    models          the MedCPT cross-encoder call and the OpenAI embedding call.
    patient         patient dict -> Stage 5 prompt text, and the input hash.
    mesh_expansion  Stage 1's deterministic MeSH walk.
    retrieval       Stages 1, 2 and 3, plus the BM25 index builder.
    filtering       Stage 4, the rule-based filter.
    evaluation      Stage 5, the criterion-level judgement.
    terminal        the three terminal nodes and _pipeline_provenance.
    graph           the StateGraph wiring and match_patient_to_trials.
    display         console rendering.

THE IMPORT DIRECTION, and there is no arrow back anywhere::

    deps  <- models  <- retrieval  <- filtering
    state <- patient <- retrieval  <- graph
             mesh_expansion <- retrieval
             evaluation <- graph
             terminal   <- graph
    display imports only config

WHY deps EXISTS, in one paragraph. Files 45 and 46 redirected the pipeline by
rebinding four names -- openai_client, qdrant_client, _bm25_query_model,
medcpt_score_pairs -- in the shared exec namespace. That worked only because
every project file was exec'd into one dict. A module function resolves its
globals in its own module, so those rebindings would have reached nothing, and
fixture_replay.py would have gone on reporting that every fixture replayed
clean while sending each Stage 5 prompt to the real OpenAI endpoint. Nothing
would have raised. See deps.py.

IMPORTING ANY MODULE HERE LOADS NO MODEL and opens no client. File 13 loaded
MedCPT and FastEmbed at exec() time, so all twelve files that chain it paid for
both just by being read. Both are lazy now.

This ``__init__`` imports NO submodule. ``import oncotriage.agent`` must stay
free; the caller names the module it wants.
"""

__all__ = [
    "deps", "state", "text", "models", "patient", "mesh_expansion",
    "retrieval", "filtering", "evaluation", "terminal", "graph", "display",
]


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
