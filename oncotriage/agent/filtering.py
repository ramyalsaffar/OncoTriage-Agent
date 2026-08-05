"""Stage 4: the rule-based filter.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 2115-2316, verbatim except
for the MeSH filter accessor.

MeSH site relevance, cancer stage ordinal, histology, age and sex, then a
dynamic quality threshold and the cost cap. The stage and histology comparisons
are integer and set operations because ``oncotriage.extraction`` did the parsing
at INDEX time -- unknown becomes None, and None means the trial passes.

``mesh_filter_applied`` is decided ONCE here, not per trial, and it is recorded:
Stage 5's system prompt asserts to the model that disease relevance "has already
been confirmed", and that sentence is only true when the filter actually ran. In
the other three cases -- ablated, no filter loaded, patient never resolved to
C04 trees -- the model used to be told a check had passed that never ran, with
nothing in the stored row saying so.

``_MESH_FILTER`` now comes from ``oncotriage.agent.deps``; File 35 stubs it.
``apply_quality_gate`` is imported from ``retrieval`` rather than duplicated,
which is the one edge this module has into another stage.
"""

import re
import time
from typing import Dict, List

from oncotriage.agent import deps
from oncotriage.agent.retrieval import apply_quality_gate
from oncotriage.agent.state import (
    MESH_FILTER_APPLIED,
    MESH_FILTER_SKIP_ABLATED,
    MESH_FILTER_SKIP_NO_FILTER,
    MESH_FILTER_SKIP_NO_TREES,
    TrialMatchState,
)
from oncotriage.config import MAX_TRIALS_FOR_EVALUATION
from oncotriage.extraction.histology import (
    extract_patient_histology,
    is_histology_mismatch,
)
from oncotriage.extraction.stage import extract_patient_stage, is_stage_mismatch


#------------------------------------------------------------------------------


def node_rule_based_filter(state: TrialMatchState) -> dict:
    """
    Stage 4: Rule-based filtering to remove obvious mismatches.

    Fast heuristic checks before expensive GPT-4o evaluation:
        - Cancer site: patient cancer type must match trial cancer type (MeSH)  # NEW
        - Age: patient age must fall within trial's min/max age
        - Sex: patient sex must match trial's sex requirement
        - Quality threshold: drop trials whose UNBOOSTED rerank score falls
          below the QUALITY_THRESHOLD_PERCENTILE of the surviving pool
          (hard floor RERANK_SCORE_THRESHOLD). Computed on rerank_score_raw
          so the gate measures trial quality, not MeSH boost membership.
        - Cost cap: limit to MAX_TRIALS_FOR_EVALUATION candidates
    """
    start = time.time()

    patient_data = state["patient_data"]
    trials = state["reranked_trials"]

    demographics = patient_data["demographics"]
    conditions = patient_data["conditions"]

    patient_age = demographics.get("age")
    patient_sex = demographics.get("sex", "unknown").lower()

    # --- Ablation flags (read once, not per-trial) ---
    _ablation = state.get("ablation_flags") or {}
    _skip_mesh      = _ablation.get("skip_mesh_filter", False)
    _skip_stage     = _ablation.get("skip_stage_filter", False)
    _skip_histology = _ablation.get("skip_histology_filter", False)

    # Resolved through the dependency seam, ONCE per call. File 13 read these
    # as module globals bound at exec time, which is what Files 35, 36, 45 and
    # 46 rebound to redirect the pipeline; a module function cannot see a
    # caller's globals, so the seam is what keeps those redirects working.
    # Once per call rather than per use so one invocation cannot see two
    # different objects if an override is installed mid-flight.
    mesh_filter = deps.get_mesh_filter()

    # --- Get patient's MeSH cancer site tree numbers ---
    mesh_dropped = 0
    histology_dropped = 0
    patient_trees = set()
    patient_histology = set()
    if mesh_filter is not None:

        patient_trees   = state.get("patient_trees") or set()
        patient_histology = extract_patient_histology(conditions)

        # Under the ablation Stage 3 never resolves the trees, so an empty set
        # here means "ablated", not "unmappable" — the ablation line below
        # says which, so do not also claim the trees were unresolvable.
        if not _skip_mesh:
            if patient_trees:
                print(f"  MeSH patient trees: {patient_trees}")
            else:
                # Say which outcome this is. "pan_cancer_only" is a resolution
                # that was deliberately rejected, not a lookup that missed.
                print(f"  MeSH: no patient cancer trees resolved "
                      f"[{state.get('mesh_resolution') or 'unrecorded'}] — "
                      f"cancer site filter skipped")

    # --- Did the cancer site filter actually run? ---
    #
    # The per-trial condition below is loop-invariant, so it is decided once
    # here and recorded. Stage 5's system prompt asserts to the model that
    # disease relevance "has already been confirmed"; that sentence is only
    # true when this is MESH_FILTER_APPLIED. In the other three cases the model
    # was told a check passed that never ran, and no stored record said so.
    if _skip_mesh:
        mesh_filter_skip_reason = MESH_FILTER_SKIP_ABLATED
    elif mesh_filter is None:
        mesh_filter_skip_reason = MESH_FILTER_SKIP_NO_FILTER
    elif not patient_trees:
        # Covers both "unmapped" and "pan_cancer_only": state["mesh_resolution"]
        # carries which one, this carries the consequence.
        mesh_filter_skip_reason = MESH_FILTER_SKIP_NO_TREES
    else:
        mesh_filter_skip_reason = MESH_FILTER_APPLIED

    mesh_filter_applied = mesh_filter_skip_reason == MESH_FILTER_APPLIED

    # --- Extract patient cancer stage ---
    patient_stage = extract_patient_stage(
        conditions,
        cancer_stage_observations=patient_data.get('cancer_stage_observations') or []
    )
    
    stage_dropped = 0
    
    if patient_stage is not None:
        print(f"  Patient cancer stage: {patient_stage}")
    else:
        print("  Patient cancer stage: unknown — stage filter skipped")
    
    if _skip_mesh:
        print("  [Ablation] MeSH cancer site filter SKIPPED "
              "(Stage 3 relevance boost was skipped too)")
    if _skip_stage:
        print("  [Ablation] Cancer stage filter SKIPPED")
    if _skip_histology:
        print("  [Ablation] Histology mismatch filter SKIPPED")

    filtered = []

    # The age and sex cuts below used to be bare `continue`s. Every other drop
    # in this loop already had a counter, so the two that did not were the only
    # ones a stored funnel could not account for.
    age_dropped = 0
    sex_dropped = 0

    for trial_obj in trials:
        trial = trial_obj["trial"]
        eligibility = trial["eligibility"]

        # --- Cancer site filter ---
        if mesh_filter_applied:
            if not mesh_filter.is_cancer_relevant(patient_trees, trial):
                mesh_dropped += 1
                continue

        # --- Cancer stage filter ---
        if not _skip_stage:
            if patient_stage is not None:
                if is_stage_mismatch(patient_stage, trial):
                    stage_dropped += 1
                    continue

        # --- Histology filter ---
        if not _skip_histology:
            if patient_histology and is_histology_mismatch(patient_histology, trial):
                histology_dropped += 1
                continue
        
        # --- Age filter ---
        min_age_str = eligibility.get("min_age", "0 Years")
        max_age_str = eligibility.get("max_age", "999 Years")

        try:
            min_age = int(re.findall(r'\d+', min_age_str)[0]) if min_age_str else 0
            max_age = int(re.findall(r'\d+', max_age_str)[0]) if max_age_str else 999

            if patient_age is not None and not (min_age <= patient_age <= max_age):
                age_dropped += 1
                continue
        except (IndexError, ValueError):
            pass  # Keep trial if age parsing fails

        # --- Sex filter ---
        trial_sex = eligibility.get("sex", "ALL").upper()
        if trial_sex not in ["ALL", patient_sex.upper()]:
            sex_dropped += 1
            continue

        filtered.append(trial_obj)

    # Sort by rerank_score (highest first) — this IS the boosted score, since
    # ranking order is what the MeSH boost exists to influence.
    filtered.sort(
         key=lambda x: (x.get("rerank_score", 0), x["trial"]["nct_id"]),
         reverse=True
     )

    # Dynamic quality threshold: percentile of the UNBOOSTED score, hard floor.
    quality_filtered, dynamic_threshold = apply_quality_gate(filtered)
    quality_dropped = len(filtered) - len(quality_filtered)

    candidates_after_quality = len(quality_filtered)

    # Cost cap: limit candidates sent to GPT-4o
    if len(quality_filtered) > MAX_TRIALS_FOR_EVALUATION:
        quality_filtered = quality_filtered[:MAX_TRIALS_FOR_EVALUATION]

    elapsed = time.time() - start
    
    if not mesh_filter_applied:
        print(f"  Cancer site filter DID NOT RUN [{mesh_filter_skip_reason}] — "
              f"Stage 5 will not assert that disease relevance was confirmed")

    print(
        f"[Stage 4] Rule-based filter: {elapsed:.2f}s | "
        f"{len(trials)} -> {len(quality_filtered)} trials"
        f"{f' (MeSH dropped {mesh_dropped})' if mesh_dropped else ''}"
        f"{f' (stage dropped {stage_dropped})' if stage_dropped else ''}"
        f"{f' (histology dropped {histology_dropped})' if histology_dropped else ''}"
        f"{f' (age dropped {age_dropped})' if age_dropped else ''}"
        f"{f' (sex dropped {sex_dropped})' if sex_dropped else ''}"
        f"{f' (quality dropped {quality_dropped} @ raw >= {dynamic_threshold:.5f})' if quality_dropped else ''}"
    )

    return {
        "filtered_trials": quality_filtered,
        "candidates_after_rule_filter": len(filtered),
        "candidates_after_quality_filter": candidates_after_quality,
        "mesh_dropped": mesh_dropped,
        "histology_dropped": histology_dropped,
        "stage_dropped": stage_dropped,
        # The two per-trial drops that had no counter, plus the pool-level cut
        # and the score it was made at. Together with the three above they
        # account for every trial that entered this stage and did not leave it.
        "age_dropped": age_dropped,
        "sex_dropped": sex_dropped,
        "quality_dropped": quality_dropped,
        "quality_threshold": float(dynamic_threshold),
        # Read by Stage 5 to decide what its system prompt may assert, and
        # logged so a stored inference says whether the check ran.
        "mesh_filter_applied": mesh_filter_applied,
        "mesh_filter_skip_reason": mesh_filter_skip_reason,
        "stage_timings": {**state.get("stage_timings", {}), "rule_filter": round(elapsed, 3)}
    }


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
