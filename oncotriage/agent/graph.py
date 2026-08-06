"""The StateGraph wiring and the public entry point.

Item 20c, pass 2c. Two slices of "13- LangGraph Agent.py":

    5062-5221  route_after_retrieval, route_after_filter, route_after_gpt4o,
               build_matching_graph
    5284-5358  match_patient_to_trials, build_initial_state

The three routers are the conditional edges: skip the cross-encoder when
retrieval returned nothing, skip Stage 5 when the filter emptied the pool, and
loop Stage 5 back on a JSON parse failure up to MAX_GPT4O_RETRIES. Any exception
anywhere lands in node_error_handler, which still emits a well-formed result.

``match_patient_to_trials`` is the public entry point. It stamps
``qdrant_collection`` and ``patient_data_hash`` onto the result, which is what
lets two rows in inferences.db be compared at all: the first says which index
answered, the second says whether the input was the same.

This module imports every stage and nothing imports it back, so it is the one
place the whole pipeline is visible at once.
"""

from typing import Dict

from langgraph.graph import END, START, StateGraph

from oncotriage.agent.evaluation import node_gpt4o_evaluation
from oncotriage.agent.filtering import node_rule_based_filter
from oncotriage.agent.patient import compute_patient_hash
from oncotriage.agent.retrieval import (
    node_cross_encoder_rerank,
    node_hybrid_retrieval,
    node_query_expansion,
)
from oncotriage.agent.state import TrialMatchState
from oncotriage.agent.terminal import (
    node_error_handler,
    node_finalize,
    node_no_candidates,
)
from oncotriage.config import MAX_GPT4O_RETRIES, Project_Name
from oncotriage.utils import resolve_qdrant_collection


#------------------------------------------------------------------------------


# ===========================================================================
# ROUTING FUNCTIONS (conditional edge logic)
# ===========================================================================

def route_after_retrieval(state: TrialMatchState) -> str:
    """
    Conditional edge after hybrid retrieval.

    If retrieval returned 0 results, skip cross-encoder and go
    directly to no_candidates. No point scoring empty results.
    """
    results = state.get("hybrid_results", [])

    if not results:
        return "no_candidates"
    return "cross_encoder_rerank"


def route_after_filter(state: TrialMatchState) -> str:
    """
    Conditional edge after rule-based filtering.

    If filtered_trials is empty, skip GPT-4o evaluation (saves cost)
    and go directly to the no_candidates terminal node.
    """
    filtered = state.get("filtered_trials", [])

    if not filtered:
        return "no_candidates"
    return "gpt4o_evaluation"


def route_after_gpt4o(state: TrialMatchState) -> str:
    """
    Conditional edge after GPT-4o evaluation.

    Three possible outcomes:
        1. Success (evaluations exist, no error) -> finalize
        2. Failure + retries remaining -> retry (loop back to gpt4o_evaluation)
        3. Failure + retries exhausted -> error_handler
    """
    error = state.get("error", "")
    retries = state.get("gpt4o_retries", 0)
    evaluations = state.get("evaluations", [])

    # Success: got valid evaluations
    if evaluations and not error:
        return "finalize"

    # Failure but retries remaining: loop back
    if retries < MAX_GPT4O_RETRIES:
        return "gpt4o_retry"

    # Retries exhausted: go to error handler
    return "error_handler"


# ===========================================================================
# GRAPH CONSTRUCTION
# ===========================================================================

def build_matching_graph() -> object:
    """
    Build and compile the LangGraph StateGraph for the pipeline.

    Graph topology:

        START
          |
        query_expansion (MeSH deterministic)
          |
        hybrid_retrieval
         / \\
       (has  (empty)
       trials)  |
         |   no_candidates ---> END
       cross_encoder_rerank
         |
       rule_based_filter
        / \\
      (has  (empty)
      trials)  |
        |   no_candidates ---> END
      gpt4o_evaluation
       /  |  \\
     (ok) |  (fail + retries left)
      |   |       |
      | (fail + exhausted)
      |   |       |
      |  error   gpt4o_evaluation  <-- RETRY LOOP (cyclic edge)
      |  handler
      |   |
    finalize
      |   |
     END  END
    """

    workflow = StateGraph(TrialMatchState)

    # --- Add Nodes ---
    workflow.add_node("query_expansion",      node_query_expansion)
    workflow.add_node("hybrid_retrieval",      node_hybrid_retrieval)
    workflow.add_node("cross_encoder_rerank",  node_cross_encoder_rerank)
    workflow.add_node("rule_based_filter",     node_rule_based_filter)
    workflow.add_node("gpt4o_evaluation",      node_gpt4o_evaluation)
    workflow.add_node("finalize",              node_finalize)
    workflow.add_node("no_candidates",         node_no_candidates)
    workflow.add_node("error_handler",         node_error_handler)

    # --- Linear Edges ---
    workflow.add_edge(START,                   "query_expansion")
    workflow.add_edge("query_expansion",       "hybrid_retrieval")

    # --- Conditional Edge 1: After Retrieval ---
    # Skip cross-encoder if retrieval returned nothing
    workflow.add_conditional_edges(
        "hybrid_retrieval",
        route_after_retrieval,
        {
            "cross_encoder_rerank": "cross_encoder_rerank",
            "no_candidates":       "no_candidates"
        }
    )

    # --- Linear: rerank -> filter ---
    workflow.add_edge("cross_encoder_rerank",  "rule_based_filter")

    # --- Conditional Edge 2: After Filtering ---
    # Skip GPT-4o if no candidates survived
    workflow.add_conditional_edges(
        "rule_based_filter",
        route_after_filter,
        {
            "gpt4o_evaluation": "gpt4o_evaluation",
            "no_candidates":    "no_candidates"
        }
    )

    # --- Conditional Edge 3: After GPT-4o (retry loop) ---
    # Success -> finalize | Parse failure + retries left -> retry | Exhausted -> error
    workflow.add_conditional_edges(
        "gpt4o_evaluation",
        route_after_gpt4o,
        {
            "finalize":       "finalize",
            "gpt4o_retry":    "gpt4o_evaluation",   # <-- CYCLIC EDGE (retry loop)
            "error_handler":  "error_handler"
        }
    )

    # --- Terminal Edges ---
    workflow.add_edge("finalize",       END)
    workflow.add_edge("no_candidates",  END)
    workflow.add_edge("error_handler",  END)

    # --- Compile ---
    graph = workflow.compile()

    print("LangGraph pipeline compiled successfully.")
    return graph


#------------------------------------------------------------------------------


# ===========================================================================
# PUBLIC API: Match a Single Patient
# ===========================================================================

def match_patient_to_trials(
    patient_data: Dict,
    graph: object
) -> Dict:
    """
    Run the full matching pipeline for one patient.

    Args:
        patient_data: Parsed FHIR patient dictionary
        graph:        Compiled LangGraph StateGraph

    Returns:
        Result dictionary with ranked trials, explanations, and metadata
    """
    print(f"\n{'='*80}")
    print(f"{Project_Name}: Matching Patient {patient_data['patient_id']}")
    print(f"{'='*80}\n")

    initial_state = build_initial_state(patient_data)

    # Invoke the LangGraph pipeline
    final_state = graph.invoke(initial_state)

    result = final_state["result"]

    result["qdrant_collection"] = resolve_qdrant_collection()

    result["patient_data_hash"] = compute_patient_hash(patient_data)

    return result


def build_initial_state(patient_data: Dict, ablation_flags: Dict = None) -> Dict:
    """The state every run starts from, in one place.

    Extracted from match_patient_to_trials() because it is no longer that
    function's private business: fixture_capture.py and 46- Fixture
    Replay.py invoke the graph directly (they need the whole final state, not
    just state["result"]), and a second hand-written copy of this dict would
    drift from the real one exactly when it mattered — a key seeded here but
    not there is a run that starts from different ground.

    ablation_flags defaults to {} = full pipeline, which is what every
    production caller passes.
    """
    return {
        "patient_data":       patient_data,
        "expanded_query":     "",
        "hybrid_results":     [],
        "bm25_retrieved":     0,
        "vector_retrieved":   0,
        "reranked_trials":    [],
        "filtered_trials":    [],
        "candidates_after_rule_filter": 0,
        "candidates_after_quality_filter": 0,
        "evaluations":        [],
        "gpt4o_retries":      0,
        "gpt4o_raw_response": "",
        "cross_vocab_remaps": 0,
        "result":             {},
        "error":              "",
        "stage_timings":      {},
        "ablation_flags":     dict(ablation_flags or {}),
        "patient_trees":      set(),
        "patient_histology":  set(),
        "mesh_resolution":    "",
        # Degradation keys are deliberately NOT pre-seeded with clean values.
        # The stage that owns each one writes it; until then it is absent, and
        # _pipeline_provenance() turns absence into NULL rather than into a
        # claim that the stage ran and found nothing wrong.
    }


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
