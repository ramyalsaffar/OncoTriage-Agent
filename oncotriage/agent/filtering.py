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

ITEM 11a CHANGED TWO THINGS HERE, one of them a behaviour change:

  * ``extract_patient_histology`` is called UNCONDITIONALLY. It used to sit
    inside ``if mesh_filter is not None:``, so a missing MeSH lookup file
    disabled the histology filter as well as the cancer site filter — two
    unrelated checks wired to one file's presence. On the degraded path
    (no MeSH filter) histology mismatches are now dropped instead of reaching
    Stage 5. On the normal path nothing changes.
  * an unparseable trial ``min_age`` / ``max_age`` is COUNTED, in the
    module-level ``AGE_PARSE_FAILURES``, and reported in the Stage 4 line. The
    recovery is unchanged — the trial is kept and the age check is skipped for
    it — because the failing value comes from ClinicalTrials.gov and there is
    no operator action that would fix it. See the counter's own note.
"""

import re
import time
from collections import Counter

from oncotriage.agent import deps
from oncotriage.agent.retrieval import apply_quality_gate
from oncotriage.agent.state import (
    MESH_FILTER_APPLIED,
    MESH_FILTER_SKIP_ABLATED,
    MESH_FILTER_SKIP_NO_FILTER,
    MESH_FILTER_SKIP_NO_TREES,
    TrialMatchState,
)
from oncotriage.config import MAX_TRIALS_FOR_EVALUATION, MEDCPT_SCORE_FLOOR
from oncotriage.extraction.histology import (
    extract_patient_histology,
    is_histology_mismatch,
)
from oncotriage.extraction.stage import extract_patient_stage, is_stage_mismatch
from oncotriage.observability import get_logger


log = get_logger(__name__)


#------------------------------------------------------------------------------


# ===========================================================================
# AGE-PARSE DEGRADATION RECORD (item 11a)
# ===========================================================================
#
# `Exception and Fallback Audit.md` ranked the handler below Open, HIGHEST
# PRIORITY, and recorded that item 11b did not change it: a trial whose
# min_age / max_age will not parse is KEPT, so the age filter silently does not
# run for that trial and it can reach GPT-4o for a patient outside its range.
# The direction is safe at pre-screening — false-eligible, never
# false-ineligible — but the RATE was unknown, and unknown is the defect.
#
# THIS COUNTS RATHER THAN RAISES, and that is a deliberate departure from the
# two layers above. A missing MeSH file or a missing pip package is a
# CONFIGURATION defect: one operator, one command, and every run afterwards is
# correct, so raising costs one run and fixes the class. An unparseable age
# bound is third-party DATA — whatever ClinicalTrials.gov happened to register
# for one trial — and raising on it would abort a whole patient's pipeline
# because one of 75 retrieved trials has a strange string in one field. There
# is no command the operator can run to fix ClinicalTrials.gov. Converting a
# per-trial degradation into a per-patient outage is not a safety improvement,
# so the fix is the counter the audit asked for, on the same footing as
# mesh_dropped, plus the Stage 4 line saying it happened.
#
# MODULE-LEVEL, following PARTIAL_DATE_DEGRADATIONS in oncotriage/utils.py, and
# NOT a new key in the returned dict: the twelve characterization fixtures diff
# the pipeline's output field by field, and a new field means recapturing all
# twelve — twelve live GPT-4o runs — to record something no stage reads.
#
# Keyed by which bound failed and on what text, capped in length, so a run can
# answer "how often, and on what" rather than only "how often". The NCT id is
# deliberately NOT in the key: 75 trials per patient across 22k patients would
# make this Counter unbounded, and the failing SHAPE is what a fix needs.
AGE_PARSE_FAILURES = Counter()

# Longest raw age string kept in a counter key. Long enough to see the shape of
# a real value ("6 Months", "N/A", "18 Years and older"), short enough that a
# pathological field cannot grow the key without bound.
_AGE_KEY_MAX_LEN = 40


def _record_age_parse_failure(bound: str, raw, exc: Exception) -> None:
    """Record one unparseable trial age bound. Never raises.

    `bound` is "min_age" or "max_age". The exception TYPE is in the key because
    IndexError (the regex found no digits) and ValueError (digits that int()
    refused) are different data problems with different fixes.
    """
    text = str(raw)
    if len(text) > _AGE_KEY_MAX_LEN:
        text = text[:_AGE_KEY_MAX_LEN] + "..."
    AGE_PARSE_FAILURES[f"{bound}:{type(exc).__name__}:{text}"] += 1


def _parse_age_bound(raw, default: int, bound: str):
    """Parse one trial age bound. Returns the int, or None if it will not parse.

    THE RECOVERY IS UNCHANGED AND THAT IS THE POINT. None propagates to the
    caller, which then skips the age check for that trial and keeps it —
    byte-for-byte the outcome of the old `except (IndexError, ValueError): pass`,
    including the case where max_age is unparseable and min_age is fine: the
    old `try` wrapped both parses AND the comparison, so one bad bound meant the
    whole check was skipped rather than the good bound being applied alone.
    Applying the good bound would be defensible and it would DROP trials the old
    code kept, which is a live behaviour change dressed up as instrumentation.
    Item 11a adds the record; changing which trials survive is a different
    decision and belongs to whoever reads the counts this now produces.

    What IS new is per-bound attribution: the old handler could not say which of
    the two strings was the bad one, and the counter is only actionable if it can.
    """
    if not raw:
        return default
    try:
        return int(re.findall(r'\d+', raw)[0])
    except (IndexError, ValueError) as exc:
        _record_age_parse_failure(bound, raw, exc)
        return None


def node_rule_based_filter(state: TrialMatchState) -> dict:
    """
    Stage 4: Rule-based filtering to remove obvious mismatches.

    Fast heuristic checks before expensive GPT-4o evaluation:
        - Cancer site: patient cancer type must match trial cancer type (MeSH)  # NEW
        - Age: patient age must fall within trial's min/max age
        - Sex: patient sex must match trial's sex requirement
        - Quality gate, two independent knobs, both must pass: the UNBOOSTED
          rerank score must reach QUALITY_THRESHOLD_PERCENTILE of the surviving
          pool (computed on rerank_score_raw, so the gate measures trial
          quality and not MeSH boost membership), AND medcpt_score_max must
          reach MEDCPT_SCORE_FLOOR. A trial with no MedCPT score is not
          dropped by the second. Each knob reports its own drop count.
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

    # --- Patient histology, computed UNCONDITIONALLY (item 11a) ---
    #
    # It used to be computed INSIDE the `if mesh_filter is not None:` block
    # below, so a missing MeSH lookup file disabled the HISTOLOGY filter too —
    # a filter that reads no MeSH data, resolves no tree numbers and has nothing
    # to do with cancer site relevance. Two unrelated capabilities were wired to
    # one file's presence, and nothing said so: `histology_dropped` came back 0,
    # which is also what "checked, nothing to drop" looks like.
    #
    # BEHAVIOUR CHANGE ON THE DEGRADED PATH, and it is the intended one: with no
    # MeSH filter loaded, trials whose histology contradicts the patient's are
    # now dropped instead of being passed to GPT-4o. On the normal path
    # (mesh_filter present) nothing changes at all — this is the same call with
    # the same argument, one indent level out — which is why the twelve
    # characterization fixtures, all captured with a filter loaded, replay
    # unchanged.
    patient_histology = extract_patient_histology(conditions)

    # --- Get patient's MeSH cancer site tree numbers ---
    mesh_dropped = 0
    histology_dropped = 0
    patient_trees = set()
    if mesh_filter is not None:

        patient_trees   = state.get("patient_trees") or set()

        # Under the ablation Stage 3 never resolves the trees, so an empty set
        # here means "ablated", not "unmappable" — the ablation line below
        # says which, so do not also claim the trees were unresolvable.
        if not _skip_mesh:
            if patient_trees:
                # THE COUNT, NOT THE TREES. A MeSH C04 tree number names the
                # patient's cancer site -- "C04.588.180" is breast. Printed to
                # a terminal that was transient; in a structured record keyed by
                # a correlation ID it is a durable statement of this patient's
                # diagnosis, which is exactly what LOGGABLE_FIELDS exists to
                # keep out. The operationally useful fact is how many resolved.
                log.info("MeSH patient trees resolved", stage=4,
                         filter="mesh_site", trees_count=len(patient_trees))
            else:
                # Say which outcome this is. "pan_cancer_only" is a resolution
                # that was deliberately rejected, not a lookup that missed.
                log.info("no patient cancer trees resolved; cancer site filter "
                         "skipped", stage=4, filter="mesh_site", trees_count=0,
                         mesh_resolution=state.get("mesh_resolution")
                                         or "unrecorded")

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
        # KNOWN vs UNKNOWN, not the ordinal. A cancer stage is a clinical fact
        # about this patient; whether the filter had one to work with is an
        # operational fact about the run, and it is the one that explains the
        # funnel. The ordinal is still in `inferences`, which is a clinical
        # store with access control; the log is not.
        log.info("patient cancer stage extracted", stage=4,
                 filter="cancer_stage", status="known")
    else:
        log.info("patient cancer stage unknown; stage filter skipped", stage=4,
                 filter="cancer_stage", status="unknown")
    
    if _skip_mesh:
        log.info("MeSH cancer site filter skipped by ablation flag "
                 "(the Stage 3 relevance boost was skipped too)",
                 stage=4, filter="mesh_site", ablation_flag="skip_mesh_filter")
    if _skip_stage:
        log.info("cancer stage filter skipped by ablation flag", stage=4,
                 filter="cancer_stage", ablation_flag="skip_stage_filter")
    if _skip_histology:
        log.info("histology mismatch filter skipped by ablation flag", stage=4,
                 filter="histology", ablation_flag="skip_histology_filter")

    filtered = []

    # The age and sex cuts below used to be bare `continue`s. Every other drop
    # in this loop already had a counter, so the two that did not were the only
    # ones a stored funnel could not account for.
    age_dropped = 0
    sex_dropped = 0

    # Trials whose age bounds would not parse, so the age check did not run for
    # them. A LOCAL, reported in the Stage 4 line below; the durable record is
    # the module-level AGE_PARSE_FAILURES counter, which also carries the text
    # that failed. It is not returned, for the fixture reason argued there.
    age_unparsed = 0

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

        min_age = _parse_age_bound(min_age_str, 0, "min_age")
        max_age = _parse_age_bound(max_age_str, 999, "max_age")

        if min_age is None or max_age is None:
            # Unparseable bound: keep the trial and skip the age check, which is
            # what the bare `except ... : pass` did. It is COUNTED now, in
            # AGE_PARSE_FAILURES, so a run can say how often the age filter did
            # not run and on what text. Counted per trial rather than tracked in
            # a local, because the recovery must not become a new field in the
            # returned dict — see the note above the counter.
            age_unparsed += 1
        elif patient_age is not None and not (min_age <= patient_age <= max_age):
            age_dropped += 1
            continue

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

    # Two independent quality knobs: a percentile of the UNBOOSTED fused score
    # within this pool, and an absolute floor on the trial's best MedCPT
    # cross-encoder score. A trial must pass both. quality_dropped stays the
    # total so no existing reader changes meaning; the per-knob counts are
    # reported beside it because the two overlap and their sum is not the total.
    quality_filtered, dynamic_threshold, quality_drops = apply_quality_gate(filtered)
    quality_dropped = len(filtered) - len(quality_filtered)

    candidates_after_quality = len(quality_filtered)

    # Cost cap: limit candidates sent to GPT-4o
    if len(quality_filtered) > MAX_TRIALS_FOR_EVALUATION:
        quality_filtered = quality_filtered[:MAX_TRIALS_FOR_EVALUATION]

    elapsed = time.time() - start
    
    if not mesh_filter_applied:
        log.warning("cancer site filter did not run; Stage 5 will not assert "
                    "that disease relevance was confirmed", stage=4,
                    filter="mesh_site", skip_reason=mesh_filter_skip_reason)

    log.info("rule-based filter complete", stage=4, duration_s=round(elapsed, 3),
             trials_in=len(trials), trials_out=len(quality_filtered),
             dropped=len(trials) - len(quality_filtered),
             # Every drop reason as its own field rather than folded into a
             # sentence: the whole point of the funnel is that a query can ask
             # "which stage lost the trials" without parsing prose.
             mesh_dropped=mesh_dropped, stage_dropped=stage_dropped,
             histology_dropped=histology_dropped, age_dropped=age_dropped,
             # The age filter DID NOT RUN for these -- distinct from a drop.
             age_unparsed=age_unparsed, sex_dropped=sex_dropped,
             quality_dropped=quality_dropped,
             # The two knobs, apart. They OVERLAP -- a trial can fail both --
             # so these do not sum to quality_dropped, and quality_dropped_floor
             # alone does not say whether the absolute knob did any work the
             # percentile had not already done. quality_dropped_floor_only does.
             quality_dropped_percentile=quality_drops["percentile"],
             quality_dropped_floor=quality_drops["floor"],
             quality_dropped_floor_only=quality_drops["floor_only"],
             medcpt_floor=MEDCPT_SCORE_FLOOR,
             # None when the pool reaching the gate was empty -- no cut was
             # made, so there is no score to report. round(None, 5) raises, so
             # the guard is not decoration.
             threshold=(round(dynamic_threshold, 5)
                        if dynamic_threshold is not None else None))

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
        # THE TWO KNOBS, SEPARATELY. The gate stopped being one number, so one
        # counter can no longer describe it: a run that lost trials to a
        # mis-set absolute floor and a run that lost them to an unusually tight
        # pool are the same quality_dropped and different findings.
        #
        # These ARE new keys in this dict, which the item 11a note above the
        # AGE_PARSE_FAILURES counter forbids for a DEGRADATION counter. The
        # reason given there was that the twelve characterization fixtures diff
        # this dict field by field. Measured rather than inherited:
        # oncotriage/fixtures/capture.py builds its stage4 block by naming keys
        # one at a time, so a key added here is not in the fixture prefix and
        # costs no recapture. What these are is a FILTER's own accounting, not
        # a recovery record, and it belongs where every other drop count is.
        "quality_dropped_percentile": quality_drops["percentile"],
        "quality_dropped_floor": quality_drops["floor"],
        "quality_dropped_floor_only": quality_drops["floor_only"],
        # NULL rather than a forged number when the gate saw an empty pool.
        # float(None) RAISES, so the unguarded float() this replaced would have
        # taken Stage 4 down on any patient whose whole pool was removed by the
        # MeSH / stage / histology / age / sex filters above.
        "quality_threshold": (float(dynamic_threshold)
                              if dynamic_threshold is not None else None),
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
