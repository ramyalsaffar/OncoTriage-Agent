"""The three ways a run can end, and the provenance all three must carry.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 3812-4229, verbatim except
for the _resolve_primary_cancer import.

``node_finalize``, ``node_no_candidates`` and ``node_error_handler`` are the only
three nodes that produce a result dict, and ``_pipeline_provenance`` is what
makes them agree. A degradation key written by one terminal node and not the
others produces a database column that is populated on some runs and NULL on
others for reasons that have nothing to do with the run -- which is why File 36
walks all three and fails on any key present in one and missing from another.

TERMINAL_NODE_* is the node's own name, stamped into the result. A reader used
to have to INFER which node ran from which keys happened to be present, and
adding a "message" key to node_finalize would have silently relabelled every
successful run as a no-candidate run.

``_resolve_primary_cancer`` used to be reached out of File 14's namespace, which
made the AGENT depend on the STORAGE layer for a registry lookup and worked only
because every production entry point happens to chain 14 after 13. Pass 2c moved
it to ``oncotriage.registries.primary_cancer``; both the agent and the storage
logger import it from there, and neither imports the other.
"""

from datetime import datetime
from typing import Dict

from oncotriage.agent.state import TrialMatchState
from oncotriage.registries.primary_cancer import _resolve_primary_cancer
from oncotriage.utils import deduplicate_by_display, get_age_reference_date


#------------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Terminal node identity
# ---------------------------------------------------------------------------
#
# Each terminal node stamps its own name into the result. A reader used to have
# to INFER which one ran from the keys that happened to be present -- a
# non-empty "error" meant the error handler, a "message" key meant no
# candidates -- which is a rule about incidental structure, not about identity.
# Adding a "message" key to node_finalize would have silently made every
# successful run report itself as a no-candidate run.
#
# Values are the function names so the string points at the code that produced
# it. 45-/46- Fixture Capture/Replay read this field; nothing infers it.
TERMINAL_NODE_FINALIZE = "node_finalize"
TERMINAL_NODE_NO_CANDIDATES = "node_no_candidates"
TERMINAL_NODE_ERROR = "node_error_handler"


# ---------------------------------------------------------------------------
# Provenance block shared by the three terminal nodes
# ---------------------------------------------------------------------------
#
# node_finalize, node_no_candidates and node_error_handler each end a run, and
# File 14 logs whichever one produced the result. A key written on only one of
# the three is a column that is populated for a minority of rows and constant
# for the rest — which reads downstream as a signal that never varies.
#
# That was the defect: gpt4o_retries existed only on the error path (so every
# successful inference logged 0 retries no matter how many were spent, and
# File 20's retry drift monitored a constant), ablation_flags was written by no
# terminal node at all (so the production column was '{}' on every row), and
# the per-channel retrieval counts were written nowhere, so File 14 inserted
# BM25_RETRIEVAL_SIZE / VECTOR_RETRIEVAL_SIZE in their place.
#
# Every value is read from state, so a stage that never ran contributes its
# initialized value rather than a fabricated one.

def _pipeline_provenance(state) -> Dict:
    """Run-level provenance keys that all three terminal results must carry."""

    # ECOG is read off state["patient_data"], the same route birth_date_precision
    # takes below, rather than being copied onto state by a node. It is a
    # property of the parsed patient, not something any stage computes, so a
    # second copy on state could only ever disagree with the first. All three
    # terminal nodes already bind state["patient_data"], so the value is
    # reachable on every path including the error path.
    #
    # {} when the key is absent, which is what a hand-built patient dict or a
    # bundle parsed before File 07 grew the field produces. That is deliberately
    # NOT the same as a parsed patient with no observation: the former leaves
    # ecog_selection None, the latter sets it to "none_recorded". File 14's
    # schema comment records the convention.
    _ecog = ((state.get("patient_data") or {}).get("ecog_performance_status") or {})

    return {
        # Retries actually spent in Stage 5. Stage 5 writes the count back into
        # state on its success return and on every failure return, so this is
        # the observed number of API / JSON-parse retries, not a ceiling.
        "gpt4o_retries": state.get("gpt4o_retries", 0),

        # --- Stage 5 truncation record ------------------------------------
        # Defaulted to 0 rather than None, unlike the degradation keys below,
        # and the difference is deliberate: those describe a stage that may
        # never have reported, while these describe work that either happened
        # or did not. A run that ended before Stage 5 genuinely performed zero
        # splits. The estimate is the exception -- it is None when Stage 5
        # never ran, because "we estimated nothing" and "we estimated 0 tokens"
        # are different claims.
        "gpt4o_truncation_splits": state.get("gpt4o_truncation_splits", 0),
        "gpt4o_output_tokens_estimated": state.get("gpt4o_output_tokens_estimated"),
        "not_evaluable_truncated": state.get("not_evaluable_truncated", 0),
        "gpt4o_calls": state.get("gpt4o_calls", 0),

        # --- Which model answered, and what it spent thinking ---------------
        #
        # BOTH BELONG HERE RATHER THAN ON node_finalize. File 14 reads them on
        # every row it writes; a key declared by one terminal node only makes
        # the column populated for a minority of rows and constant for the
        # rest, which is the exact defect this block exists to prevent, and
        # File 36's Test 1 fails for it.
        #
        # matching_model is the string the API ANSWERED with, read off
        # response.model by Stage 5. None means no Stage 5 response was ever
        # obtained -- the run ended at node_no_candidates, or died before the
        # first call returned. That is NOT the same as "it ran on the
        # configured model", which is what logging MATCHING_MODEL here would
        # assert on a run that never made a request. File 14 prices against
        # this value.
        #
        # gpt4o_reasoning_tokens is the reasoning SUBSET of
        # gpt4o_output_tokens, not an addition to it, so it is a breakdown
        # column and never a costing term. None -- not 0 -- when no response
        # carried the breakdown: a stub, a replayed pre-migration fixture, or a
        # run that never reached Stage 5. A non-reasoning model reporting a
        # genuine 0 is a different fact and stays 0.
        "matching_model": state.get("matching_model"),
        "gpt4o_reasoning_tokens": state.get("gpt4o_reasoning_tokens"),
        # Which stages were disabled for this run; {} = full pipeline. Copied
        # rather than aliased so the logged record cannot be mutated later.
        "ablation_flags": dict(state.get("ablation_flags") or {}),
        # Observed Stage 2 channel counts (see TrialMatchState). Absent from
        # state only when the run ended before Stage 2 returned, and 0 is then
        # the true count of what that channel retrieved.
        "bm25_retrieved": state.get("bm25_retrieved", 0),
        "vector_retrieved": state.get("vector_retrieved", 0),

        # The date this run's patient ages and the Stage 5 prompt's temporal
        # reasoning were anchored to. Not read from state: it is a property of
        # the run's configuration, identical on every path including the error
        # path, and it is recorded per run precisely so a stored row can be
        # reproduced without knowing when it was produced. Taken from
        # DATA_SNAPSHOT_DATE (File 03) rather than from the patient dict so it
        # is present even when demographics never parsed.
        "age_reference_date": get_age_reference_date().isoformat(),

        # How much of the patient's birthDate the record carried ("day" =
        # exact age, "month"/"year" = imputed from an anchor, "missing" /
        # "unparseable" / "after_reference" = no age at all). Written by File
        # 07 into demographics; None when the caller built the patient dict by
        # hand, which is not the same as "the date was exact".
        "birth_date_precision": ((state.get("patient_data") or {})
                                 .get("demographics") or {})
                                .get("birth_date_precision"),

        # --- Degradation record (see the vocabularies at the top of this file) ---
        #
        # These four default to None, not to a clean value, and File 14 writes
        # NULL for None. The distinction matters more here than anywhere else
        # in this dict: a run that ended before Stage 2 has no channel outcomes
        # to report, and writing "0 failures" for it would assert the opposite
        # of what happened. A caller reading these must treat NULL as "the
        # stage did not report", never as "nothing went wrong".
        "retrieval_channels": dict(state["retrieval_channels"])
                              if state.get("retrieval_channels") else None,
        "retrieval_channels_expected": state.get("retrieval_channels_expected"),
        "retrieval_channels_ok": state.get("retrieval_channels_ok"),
        "retrieval_degraded": state.get("retrieval_degraded"),
        "retrieval_trials_lost": state.get("retrieval_trials_lost"),

        # Which query Stage 1 handed to retrieval, and whether Stage 4's cancer
        # site filter ran. Both None when the stage that writes them did not
        # complete. mesh_filter_applied is stored as 0/1 by File 14.
        "query_expansion_path": state.get("query_expansion_path"),
        "mesh_filter_applied": state.get("mesh_filter_applied"),
        "mesh_filter_skip_reason": state.get("mesh_filter_skip_reason"),

        # --- ECOG performance status (see File 07) -------------------------
        #
        # The score printed into the Stage 5 prompt, the path that produced it,
        # and how many observations the bundle carried. All three belong in the
        # record of the inference they shaped: ECOG 0-1 or 0-2 gates nearly every
        # interventional oncology trial, so a corpus that resolved entirely to
        # "all_after_reference_date" would match systematically worse with
        # nothing in the row to say why.
        #
        # ecog_value is None both for a patient with no observation and for one
        # whose only observation postdates the snapshot. ecog_selection is what
        # separates them, and ecog_observations_found is what makes the second
        # case countable. Never read absence off ecog_value alone.
        "ecog_value": _ecog.get("value"),
        "ecog_selection": _ecog.get("selection"),
        "ecog_observations_found": _ecog.get("observations_found"),
    }


def node_finalize(state: TrialMatchState) -> dict:
    """
    Stage 6: Assemble final output with pipeline metadata.

    Splits evaluations into three groups based on the trial-level classification:

      matches:        "eligible"      — no known disqualifiers, pre-screening candidate
      near_misses:    "not_eligible"  — explicit disqualifying evidence found
      not_evaluable:  "not_evaluable" — the trial could not be assessed at all

    A "not_evaluable" trial is deliberately kept out of near_misses: it is a
    non-evaluation to be counted, not a rejection to be reported.

    Matches are sorted by match_score descending.
    """

    patient_data = state["patient_data"]
    evaluations = state.get("evaluations", [])

    # ── Normalize eligible field ─────────────────────────────────────────
    # GPT-4o returns "eligible" / "not_eligible" / "not_evaluable". Handle edge cases.
    _ELIGIBLE_NORM = {
        True:  "eligible",
        False: "not_eligible",
        "true":  "eligible",
        "false": "not_eligible",
        "yes":   "eligible",
        "no":    "not_eligible",
    }

    for e in evaluations:
        raw = e.get("eligible")
        if isinstance(raw, bool):
            e["eligible"] = _ELIGIBLE_NORM[raw]
        elif isinstance(raw, str):
            normalized = raw.strip().lower()
            e["eligible"] = _ELIGIBLE_NORM.get(normalized, normalized)
        # else: leave as-is (will fall through to near_misses)

    # ── Split into matches vs. near-misses vs. non-evaluations ───────────
    _ACTIONABLE = frozenset({"eligible"})
    _UNEVALUABLE = frozenset({"not_evaluable"})

    # Build score lookup from filtered_trials by nct_id.
    # The boosted score, the unboosted score and the boost itself are all
    # carried through so the boost's effect on ranking stays measurable
    # downstream (trial_matches.mesh_boost) instead of being folded away.
    _rerank_lookup = {
        t["trial"]["nct_id"]: (
            t.get("rerank_score", None),
            t.get("rerank_score_raw", None),
            t.get("mesh_boost", 0.0),
            t.get("mesh_boost_tier", "none"),
        )
        for t in state.get("filtered_trials", [])
        if "trial" in t and "nct_id" in t["trial"]
    }

    # Merge scores and trial_number into each evaluation
    for rank_pos, e in enumerate(evaluations, start=1):
        nct_id = e.get("nct_id", "")
        _scores = _rerank_lookup.get(nct_id, (None, None, None, None))
        e["rerank_score"]     = _scores[0]
        e["rerank_score_raw"] = _scores[1]
        e["mesh_boost"]       = _scores[2]
        e["mesh_boost_tier"]  = _scores[3]
        e["trial_number"] = rank_pos

    matches = [e for e in evaluations if e.get("eligible") in _ACTIONABLE]
    not_evaluable = [e for e in evaluations if e.get("eligible") in _UNEVALUABLE]
    near_misses = [
        e for e in evaluations
        if e.get("eligible") not in _ACTIONABLE and e.get("eligible") not in _UNEVALUABLE
    ]

    # Sort matches by match_score descending
    matches.sort(key=lambda e: -e.get("match_score", 0))

    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    
    result = {
        "patient_id": patient_data["patient_id"],
        "primary_condition": _resolve_primary_cancer(conditions),
        "condition_count": len(deduplicate_by_display(conditions)),
        "medication_count": len(deduplicate_by_display(medications)),
        "allergy_count": len(patient_data.get("allergies", [])),
        "expanded_query": state.get("expanded_query", ""),
        "candidates_retrieved": len(state.get("hybrid_results", [])),
        "candidates_reranked": len(state.get("reranked_trials", [])),
        "candidates_after_rule_filter": state.get("candidates_after_rule_filter", 0),
        "candidates_after_quality_filter": state.get("candidates_after_quality_filter", 0),
        "candidates_filtered": len(state.get("filtered_trials", [])),
        "mesh_dropped": state.get("mesh_dropped", 0),
        "mesh_resolution": state.get("mesh_resolution", ""),
        "stage_dropped": state.get("stage_dropped", 0),
        "histology_dropped": state.get("histology_dropped", 0),
        "candidates_evaluated": len(evaluations),
        # Criteria dropped from match_score because they cannot apply to this
        # patient (Section 3 "Not applicable"). Reported so a score computed
        # over a shrunken denominator is never mistaken for one computed over
        # the full criteria set.
        "criteria_not_applicable": sum(
            e.get("criteria_not_applicable", 0) for e in evaluations
        ),
        "matches": matches,
        "near_misses": near_misses,
        "not_evaluable": not_evaluable,
        "not_evaluable_trials": len(not_evaluable),
        "cross_vocab_remaps": state.get("cross_vocab_remaps", 0),
        "stage_timings": state.get("stage_timings", {}),
        "expansion_prompt": state.get("expansion_prompt", ""),
        "expansion_input_tokens": state.get("expansion_input_tokens", 0),
        "expansion_output_tokens": state.get("expansion_output_tokens", 0),
        "gpt4o_prompt": state.get("gpt4o_prompt", ""),
        "gpt4o_input_tokens": state.get("gpt4o_input_tokens", 0),
        "gpt4o_output_tokens": state.get("gpt4o_output_tokens", 0),
        "timestamp": datetime.now().isoformat(),
        "error": "",
        "patient_data_hash": "",
        # Which node produced this result, stated rather than inferred.
        "terminal_node": TERMINAL_NODE_FINALIZE,
        **_pipeline_provenance(state),
    }

    eligible_count = len(matches)
    print(
        f"[Stage 6] Finalized: {eligible_count} eligible, "
        f"{len(near_misses)} not_eligible, "
        f"{len(not_evaluable)} not_evaluable "
        f"for patient {patient_data['patient_id']}"
    )
    
    return {"result": result}


def node_no_candidates(state: TrialMatchState) -> dict:
    """
    Terminal node: no candidates survived retrieval or filtering.

    Returns a clean result indicating no trials were found,
    rather than wasting a GPT-4o call on an empty candidate set.
    """
    patient_data = state["patient_data"]

    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    
    result = {
        "patient_id": patient_data["patient_id"],
        "primary_condition": _resolve_primary_cancer(conditions),
        "condition_count": len(deduplicate_by_display(conditions)),
        "medication_count": len(deduplicate_by_display(medications)),
        "allergy_count": len(patient_data.get("allergies", [])),
        "expanded_query": state.get("expanded_query", ""),
        "candidates_retrieved": len(state.get("hybrid_results", [])),
        "candidates_reranked": len(state.get("reranked_trials", [])),
        "candidates_after_rule_filter": state.get("candidates_after_rule_filter", 0),
        "candidates_after_quality_filter": state.get("candidates_after_quality_filter", 0),
        "candidates_filtered": len(state.get("filtered_trials", [])),
        "mesh_dropped": state.get("mesh_dropped", 0),
        "mesh_resolution": state.get("mesh_resolution", ""),
        "stage_dropped": state.get("stage_dropped", 0),
        "histology_dropped": state.get("histology_dropped", 0),
        "candidates_evaluated": 0,
        # No evaluation ran, so no criterion was excluded from a score. Written
        # anyway: the three terminal results declare the same keys, so a
        # consumer never has to know which one produced the row it is reading.
        "criteria_not_applicable": 0,
        "matches": [],
        "near_misses": [],
        "not_evaluable": [],
        "not_evaluable_trials": 0,
        "cross_vocab_remaps": 0,
        "expansion_prompt": state.get("expansion_prompt", ""),
        "expansion_input_tokens": state.get("expansion_input_tokens", 0),
        "expansion_output_tokens": state.get("expansion_output_tokens", 0),
        "gpt4o_prompt": "",
        "gpt4o_input_tokens": 0,
        "gpt4o_output_tokens": 0,
        "message": "No trials passed retrieval or filtering for this patient.",
        "error": "",
        "patient_data_hash": "",
        "terminal_node": TERMINAL_NODE_NO_CANDIDATES,
        "stage_timings": state.get("stage_timings", {}),
        "timestamp": datetime.now().isoformat(),
        **_pipeline_provenance(state),
    }

    print(f"[No Candidates] No matching trials for patient {patient_data['patient_id']}")

    return {"result": result}


def node_error_handler(state: TrialMatchState) -> dict:
    """
    Error terminal node: GPT-4o failed after all retries.

    Packages whatever information is available into a clean error
    response so the caller gets structured output (not a crash).
    """
    patient_data = state["patient_data"]
    error_msg = state.get("error", "Unknown error")

    conditions = patient_data.get("conditions", [])
    medications = patient_data.get("medications", [])
    
    result = {
        "patient_id": patient_data["patient_id"],
        "primary_condition": _resolve_primary_cancer(conditions),
        "condition_count": len(deduplicate_by_display(conditions)),
        "medication_count": len(deduplicate_by_display(medications)),
        "allergy_count": len(patient_data.get("allergies", [])),
        "expanded_query": state.get("expanded_query", ""),
        "candidates_retrieved": len(state.get("hybrid_results", [])),
        "candidates_reranked": len(state.get("reranked_trials", [])),
        "candidates_after_rule_filter": state.get("candidates_after_rule_filter", 0),
        "candidates_after_quality_filter": state.get("candidates_after_quality_filter", 0),
        "candidates_filtered": len(state.get("filtered_trials", [])),
        "mesh_dropped": state.get("mesh_dropped", 0),
        "mesh_resolution": state.get("mesh_resolution", ""),
        "stage_dropped": state.get("stage_dropped", 0),
        "histology_dropped": state.get("histology_dropped", 0),
        "candidates_evaluated": 0,
        "criteria_not_applicable": 0,
        "matches": [],
        "near_misses": [],
        "not_evaluable": [],
        "not_evaluable_trials": 0,
        "cross_vocab_remaps": state.get("cross_vocab_remaps", 0),
        "expansion_prompt": state.get("expansion_prompt", ""),
        "expansion_input_tokens": state.get("expansion_input_tokens", 0),
        "expansion_output_tokens": state.get("expansion_output_tokens", 0),
        "gpt4o_prompt": state.get("gpt4o_prompt", ""),
        "gpt4o_input_tokens": state.get("gpt4o_input_tokens", 0),
        "gpt4o_output_tokens": state.get("gpt4o_output_tokens", 0),
        "error": error_msg,
        "patient_data_hash": "",
        "terminal_node": TERMINAL_NODE_ERROR,
        # Retired key. It said the same thing as gpt4o_retries but existed only
        # on this path, which is how the count came to be logged as 0 for every
        # run that did not end here. Kept as an alias for one release so an
        # external consumer of the API response is not broken by the rename;
        # nothing inside this repo reads it.
        "gpt4o_retries_exhausted": state.get("gpt4o_retries", 0),
        "stage_timings": state.get("stage_timings", {}),
        "timestamp": datetime.now().isoformat(),
        **_pipeline_provenance(state),
    }

    print(f"[ERROR] Pipeline failed for patient {patient_data['patient_id']}: {error_msg}")

    return {"result": result}


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
