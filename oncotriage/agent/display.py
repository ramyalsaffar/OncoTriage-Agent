"""Human-readable rendering of a match result.

Item 20c, pass 2c: "13- LangGraph Agent.py" lines 5361-5484, verbatim.

Console output only. Nothing in the pipeline reads what this produces, and
nothing here reads a client, a model or a registry -- which is why it is the one
agent module that imports only two config constants.
"""

from typing import Dict

from oncotriage.config import MAX_GPT4O_RETRIES, Project_Name
from oncotriage.observability import console


#------------------------------------------------------------------------------


# ===========================================================================
# DISPLAY RESULTS
# ===========================================================================

def display_match_results(result: Dict):
    """
    Pretty-print match results for a single patient.

    Displays the trial-level classification tiers:
      ELIGIBLE:      "eligible"      — no known disqualifiers, pre-screening candidate
      NOT ELIGIBLE:  "not_eligible"  — explicit disqualifying evidence found
      NOT EVALUABLE: "not_evaluable" — the trial could not be assessed; counted, not reported as a rejection

    For each eligible match, lists criteria that could not be evaluated
    from the patient record so the coordinator knows what to verify.
    """

    console.out(f"\n{'='*80}")
    console.out(f"{Project_Name}: MATCH RESULTS FOR PATIENT {result['patient_id']}")
    console.out(f"{'='*80}\n")

    # Check for pipeline error
    if result.get("error"):
        console.out(f"PIPELINE ERROR: {result['error']}")
        retries = result.get("gpt4o_retries", 0)
        if retries:
            console.out(f"GPT-4o retries exhausted: {retries}/{MAX_GPT4O_RETRIES}")
        console.out()

    # Pipeline summary
    matches = result.get("matches", [])
    near_misses = result.get("near_misses", [])
    not_evaluable = result.get("not_evaluable", [])

    console.out(f"Pipeline Summary:")
    console.out(f"  BM25 Retrieved:        {result.get('bm25_retrieved', 0)}")
    console.out(f"  Vector Retrieved:      {result.get('vector_retrieved', 0)}")
    console.out(f"  Candidates Retrieved:  {result.get('candidates_retrieved', 0)}")

    console.out(f"  Candidates Re-Ranked:  {result.get('candidates_reranked', 0)}")
    console.out(f"  After Rule Filters:    {result.get('candidates_after_rule_filter', 0)}")
    console.out(f"  After Quality Filter:  {result.get('candidates_after_quality_filter', 0)}")
    console.out(f"  Candidates Filtered:   {result.get('candidates_filtered', 0)}")
    console.out(f"  Candidates Evaluated:  {result.get('candidates_evaluated', 0)}")
    console.out(f"  Matches:               {len(matches)}")
    console.out(f"  Not Eligible:          {len(near_misses)}")
    console.out(f"  Not Evaluable:         {len(not_evaluable)}")
    console.out(f"  Label Remaps:          {result.get('cross_vocab_remaps', 0)}")
    if result.get("gpt4o_retries", 0):
        console.out(f"  GPT-4o Retries:        {result['gpt4o_retries']}/{MAX_GPT4O_RETRIES}")
    if result.get("ablation_flags"):
        console.out(f"  Ablation Flags:        {result['ablation_flags']}")

    timings = result.get("stage_timings", {})
    if timings:
        console.out(f"\nStage Latencies:")
        for stage, seconds in timings.items():
            console.out(f"  {stage}: {seconds:.3f}s")
        total = sum(timings.values())
        console.out(f"  TOTAL: {total:.3f}s")

    console.out()

    # ── ELIGIBLE ─────────────────────────────────────────────────────────
    if matches:
        console.out(f"ELIGIBLE — Pre-Screening Candidates ({len(matches)}):\n")
        for idx, match in enumerate(matches[:10], 1):
            _print_match_detail(idx, match)

    # ── NOT ELIGIBLE ─────────────────────────────────────────────────────
    if not matches:
        console.out("No matching trials found for this patient.\n")

        if near_misses:
            console.out(f"NOT ELIGIBLE — Top 3 Near-Misses:\n")
            for idx, match in enumerate(near_misses[:3], 1):
                console.out(f"  {idx}. {match.get('nct_id', 'N/A')} | {match.get('title', 'No title')}")
                console.out(f"     {match.get('explanation', 'N/A')}")
                console.out()
    elif near_misses:
        # Matches exist, but also show count of rejected trials
        console.out(f"({len(near_misses)} additional trials evaluated but not eligible.)\n")

    # ── NOT EVALUABLE ────────────────────────────────────────────────────
    # Reported separately from rejections: these trials were never assessed.
    if not_evaluable:
        console.out(f"NOT EVALUABLE — could not be assessed ({len(not_evaluable)}):\n")
        for trial in not_evaluable:
            console.out(f"  - {trial.get('nct_id', 'N/A')} | {trial.get('explanation', 'No criteria returned.')}")
        console.out()


def _print_match_detail(idx: int, match: Dict):
    """
    Print a single match with criterion-level transparency.

    Shows the trial identification, score, explanation, and — critically —
    which criteria could not be evaluated from the patient record. This tells
    the research coordinator exactly what tests/data to obtain before referral.
    """
    console.out(f"  {idx}. {match.get('nct_id', 'N/A')} | {match.get('title', 'No title')}")
    console.out(f"     Score: {match.get('match_score', 0):.2f} | Status: {match.get('eligible', 'unknown')}")
    console.out(f"     {match.get('explanation', 'N/A')}")

    # Show criteria that need verification (not_evaluable from inclusions)
    needs_verification = [
        c.get("criterion", "Unknown criterion")
        for c in match.get("inclusion_criteria", [])
        if c.get("status") == "not_evaluable"
    ]
    # Also check exclusions that are not_evaluable (coordinator should verify
    # the patient does NOT have the excluded condition)
    needs_verification += [
        c.get("criterion", "Unknown criterion") + " (exclusion)"
        for c in match.get("exclusion_criteria", [])
        if c.get("status") == "not_evaluable"
    ]

    if needs_verification:
        console.out(f"     Needs verification ({len(needs_verification)}):")
        for criterion in needs_verification:
            console.out(f"       - {criterion}")

    console.out()                


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
