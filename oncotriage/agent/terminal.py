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

from oncotriage.agent.state import (
    MESH_FILTER_SKIP_NO_FILTER,
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_EVALUABLE,
    TrialMatchState,
    normalize_trial_verdict,
)
from oncotriage.agent.prompts import PROMPT_VERSION
from oncotriage.config import MAX_LLM_CLASSIFIER_RETRIES
from oncotriage.observability import get_logger
from oncotriage.registries.primary_cancer import _resolve_primary_cancer
from oncotriage.utils import deduplicate_by_display, get_age_reference_date


log = get_logger(__name__)


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

# ---------------------------------------------------------------------------
# THE ONE-GLANCE DEGRADATION MARKER
# ---------------------------------------------------------------------------
#
# WHAT degraded_run IS, EXACTLY. It is 1 when at least one of the four
# observations below is POSITIVELY set on this run's state, and 0 when none of
# them is. It is a summary of columns that already exist; it adds no
# measurement of its own, and the per-stage columns remain the detail.
#
# WHAT 0 DOES AND DOES NOT ASSERT — read this before writing a query.
#
#   0 means "no degradation signal fired".
#   0 does NOT mean "every check ran and passed".
#
# Those differ on a run that ended early: node_no_candidates on a patient whose
# pool emptied at Stage 2 never reaches Stage 4, so mesh_filter_applied is NULL
# rather than 0 and the mesh term below cannot fire. The honest reading of that
# row is "nothing went wrong that this run got far enough to observe", and the
# way to ask the stronger question is the one the existing columns already
# answer: `retrieval_degraded IS NOT NULL AND mesh_filter_applied IS NOT NULL
# AND ... AND degraded_run = 0`. Collapsing that into this column would mean
# choosing between calling every no-candidate run degraded (false) and calling
# an unobserved check clean (also false), so the column reports the one thing
# it can state without inventing either.
#
# NULL is the third value and it is NOT produced here. Every terminal node
# spreads this dict, so a result that came from the graph always carries an int.
# NULL appears in the database when the result dict reaching log_inference has
# no such key at all: a caller that built a result by hand, or a row written
# before this column existed. That is llm_classifier_prompt_sha256's convention,
# not llm_classifier_prompt_version's -- absence of the fact, never a fallback.
#
# THE FOUR TERMS, and what was considered and left out.
#
#   1. state["error"] is non-empty. The run ended at node_error_handler, which
#      is only reachable from Stage 5's failure and refusal paths, and `error`
#      is seeded "" by build_initial_state and CLEARED by Stage 5's success
#      return -- so this is exactly "this run failed", not a stale flag. Listed
#      first because a crashed run reported as clean is the worst thing this
#      column could do, and the brief's four signals do not by themselves cover
#      a Stage 1 or Stage 2 exception.
#   2. retrieval_degraded is truthy. Stage 2 sets it to 1 when an EXPECTED
#      channel did not return; ablated channels are excluded from "expected" by
#      Stage 2 itself, so an ablation run is not reported as degraded here for
#      free.
#   3. The cancer site filter was skipped BECAUSE ITS DATA WAS ABSENT
#      (MESH_FILTER_SKIP_NO_FILTER). Deliberately not the other two skips:
#      MESH_FILTER_SKIP_ABLATED is a configured experiment, on exactly the
#      footing Stage 2 excludes ablated channels; MESH_FILTER_SKIP_NO_TREES is
#      a property of the patient's record rather than of the run's health, it
#      is already carried in full by mesh_resolution, and counting it would
#      mark every unmappable patient as a degraded run. That second exclusion
#      is the judgement call in this predicate -- it is the one to revisit
#      first if the marker ever reads too clean.
#   4. Stage 5 exhausted a budget: llm_classifier_retries reached
#      MAX_LLM_CLASSIFIER_RETRIES, or a trial left Stage 5 with no verdict
#      because of truncation (not_evaluable_truncated > 0, which covers both
#      the single-trial floor and the exhausted split budget).
#
# CONSIDERED AND NOT INCLUDED, so the next reader does not have to re-derive
# the absence: retrieval_trials_lost > 0 (a real degradation, but it is the
# brief's next item rather than this one's, and adding it silently would change
# what a column means between two runs of the same build);
# query_expansion_path == EXPANSION_PATH_FALLBACK (the fallback is a designed
# path, not a fault); the three new *_filter_applied = 0 markers (all three
# skips are ordinary properties of a patient record). Each of those is one term
# away if it is wanted, and each is a decision rather than an oversight.

def _derive_degraded_run(state) -> int:
    """1 if any degradation signal fired on this run, else 0. Never None.

    Reads state only. Adds no measurement: every term is a value some stage
    already wrote and already logs to its own column.
    """
    if state.get("error"):
        return 1
    if state.get("retrieval_degraded"):
        return 1
    if state.get("mesh_filter_skip_reason") == MESH_FILTER_SKIP_NO_FILTER:
        return 1
    if (state.get("llm_classifier_retries") or 0) >= MAX_LLM_CLASSIFIER_RETRIES:
        return 1
    if (state.get("not_evaluable_truncated") or 0) > 0:
        return 1
    return 0


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
        "llm_classifier_retries": state.get("llm_classifier_retries", 0),

        # --- Stage 5 truncation record ------------------------------------
        # Defaulted to 0 rather than None, unlike the degradation keys below,
        # and the difference is deliberate: those describe a stage that may
        # never have reported, while these describe work that either happened
        # or did not. A run that ended before Stage 5 genuinely performed zero
        # splits. The estimate is the exception -- it is None when Stage 5
        # never ran, because "we estimated nothing" and "we estimated 0 tokens"
        # are different claims.
        "llm_classifier_truncation_splits": state.get("llm_classifier_truncation_splits", 0),
        "llm_classifier_output_tokens_estimated": state.get("llm_classifier_output_tokens_estimated"),
        "not_evaluable_truncated": state.get("not_evaluable_truncated", 0),
        "llm_classifier_calls": state.get("llm_classifier_calls", 0),

        # --- The out-of-set detector's count ------------------------------
        #
        # NO DEFAULT, and it is the prompt-hash rule rather than the truncation
        # rule immediately above. Those counters describe work that either
        # happened or did not, so 0 is true of a run that ended early. This one
        # describes a CHECK: 0 asserts that every entry the model returned was
        # compared against the candidate set and every one of them belonged to
        # it, which is a claim no run that ended before Stage 5 completed is
        # entitled to make. Stage 5 writes the key on its success return only,
        # so None here means the detector did not run and File 14 stores NULL.
        "hallucinated_trials": state.get("hallucinated_trials"),

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
        # llm_classifier_reasoning_tokens is the reasoning SUBSET of
        # llm_classifier_output_tokens, not an addition to it, so it is a breakdown
        # column and never a costing term. None -- not 0 -- when no response
        # carried the breakdown: a stub, a replayed pre-migration fixture, or a
        # run that never reached Stage 5. A non-reasoning model reporting a
        # genuine 0 is a different fact and stays 0.
        "matching_model": state.get("matching_model"),
        "llm_classifier_reasoning_tokens": state.get("llm_classifier_reasoning_tokens"),

        # --- What the Stage 5 INPUT packer did, and what caching returned ---
        #
        # ALL THREE TAKE THE NO-DEFAULT ROUTE, with hallucinated_trials above
        # rather than with the truncation counters. Each describes a MEASUREMENT
        # this run either made or did not: how many requests the packer produced
        # and how they were sized, and how much of the shared prefix the
        # provider served from cache. A run that ended at node_no_candidates
        # packed nothing and asked nothing, and 0 there would assert one request
        # of zero cached tokens rather than "no request was made".
        #
        # llm_classifier_cached_input_tokens is a SUBSET of
        # llm_classifier_input_tokens and never a costing term -- get_model_cost
        # prices the whole input at the uncached rate, deliberately, so the
        # stored cost stays comparable with every historical row.
        #
        # NEITHER PACKING KEY HAS A DATABASE COLUMN. They reach the API response
        # and any in-process consumer; File 14 writes the columns it declares and
        # ignores the rest. That is stated rather than left to be discovered:
        # the validation run reads these off the result, and persisting them is
        # a schema decision this pass does not make.
        "llm_classifier_packed_chunks": state.get("llm_classifier_packed_chunks"),
        "llm_classifier_packing": state.get("llm_classifier_packing"),
        "llm_classifier_cached_input_tokens": state.get(
            "llm_classifier_cached_input_tokens"),
        # The per-call ledger behind the two token totals above. Same no-default
        # route, and for once the absence is rarer than the presence: Stage 5
        # writes this on EVERY one of its returns, so None here means the node
        # was never entered at all rather than that it failed part way.
        #
        # ALL FOUR OF THESE KEYS WERE DROPPED BY THE GRAPH UNTIL THEY WERE
        # DECLARED IN TrialMatchState. Reading a key here is not enough to make
        # it exist: LangGraph writes only the channels the schema declares and
        # discards the rest in silence. See the block that declares them.
        "llm_classifier_call_details": state.get("llm_classifier_call_details"),

        # --- Which Stage 5 system prompt produced this row ------------------
        #
        # THE TWO FIELDS DEFAULT DIFFERENTLY, AND THE ASYMMETRY IS THE POINT.
        #
        # The VERSION is what a human intended the template to be, and it is a
        # property of the CODE this process is running, not of any stage. So it
        # is answered even when Stage 5 never ran: node_no_candidates emptied
        # the pool before the prompt was rendered, and the honest statement
        # about that row is still "this build carried template 1.0.0". Stage 5
        # writes it into state on every one of its returns, so a run that DID
        # render reports the version that rendered; the fallback is for the
        # paths that never reached the node.
        #
        # The HASH has no fallback and is None when no prompt was rendered.
        # NULL is the honest value for the hash of a prompt that never existed;
        # rendering one here to hash it would record an event that did not
        # happen, which is the defect class this project exists to remove. A
        # reader therefore separates "Stage 5 ran" from "Stage 5 never ran" by
        # llm_classifier_prompt_sha256 IS NULL, never by the version.
        "llm_classifier_prompt_version": (state.get("llm_classifier_prompt_version")
                                          or PROMPT_VERSION),
        "llm_classifier_prompt_sha256": state.get("llm_classifier_prompt_sha256"),
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

        # The same pair for the other four Stage 4 filters. No default, for the
        # reason the four keys above have none: a run that ended before Stage 4
        # has no filter outcome to report, and 0 would assert that the filter
        # ran and dropped nothing.
        "stage_filter_applied": state.get("stage_filter_applied"),
        "stage_filter_skip_reason": state.get("stage_filter_skip_reason"),
        "histology_filter_applied": state.get("histology_filter_applied"),
        "histology_filter_skip_reason": state.get("histology_filter_skip_reason"),
        "age_filter_applied": state.get("age_filter_applied"),
        "age_filter_skip_reason": state.get("age_filter_skip_reason"),
        "sex_filter_applied": state.get("sex_filter_applied"),
        "sex_filter_skip_reason": state.get("sex_filter_skip_reason"),

        # --- The one-glance marker ------------------------------------------
        "degraded_run": _derive_degraded_run(state),

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
    #
    # The vocabulary and the synonym map moved to oncotriage/agent/state.py,
    # which Stage 5 also calls. THE MAP THAT USED TO BE HERE COULD NOT BE
    # REACHED: Stage 5 ran first and forced every value outside its own
    # three-member tuple to "not_eligible", so boolean True and "Eligible"
    # arrived already destroyed and this map's whole recovery vocabulary was
    # dead code that looked live. One normalizer now answers for both stages.
    #
    # AN UNRESOLVABLE LABEL BECOMES not_evaluable RATHER THAN FALLING THROUGH
    # TO near_misses, which is what the deleted `# else: leave as-is` comment
    # arranged. That fall-through is the same fabricated rejection Stage 5 has
    # just stopped making, one stage later and reachable by any caller that
    # builds `evaluations` without Stage 5. On every path the pipeline actually
    # takes it is a no-op, because Stage 5 now emits only the canonical three.
    _unresolved_verdicts = []

    for e in evaluations:
        raw = e.get("eligible")
        verdict, _source = normalize_trial_verdict(raw)
        if verdict is None:
            _unresolved_verdicts.append(type(raw).__name__)
            verdict = TRIAL_VERDICT_NOT_EVALUABLE
        e["eligible"] = verdict

    if _unresolved_verdicts:
        log.warning("trial-level verdict labels reached Stage 6 unresolvable; "
                    "recording them as not evaluable rather than as rejections",
                    stage=6, node=TERMINAL_NODE_FINALIZE,
                    patient_id=patient_data["patient_id"],
                    count=len(_unresolved_verdicts),
                    error_type=",".join(sorted(set(_unresolved_verdicts))))

    # ── Split into matches vs. near-misses vs. non-evaluations ───────────
    _ACTIONABLE = frozenset({TRIAL_VERDICT_ELIGIBLE})
    _UNEVALUABLE = frozenset({TRIAL_VERDICT_NOT_EVALUABLE})

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

    # ── trial_number is the RETRIEVAL rank, not the answer order ───────────
    #
    # It used to be `enumerate(evaluations)`. Stage 5 sorts that list by
    # match_score descending immediately before returning it, so the stored
    # number was a rank within the model's own verdicts -- and it moved
    # whenever the verdicts moved, while the pipeline's ranking underneath it
    # had not. Two runs over an identical candidate set could disagree about
    # which trial is "1" because one criterion was scored differently.
    #
    # The rank is the position in filtered_trials, which is the list Stage 5
    # was sent, in the order Stages 3 and 4 left it. That is the pipeline's own
    # ranking, so trial_number 1 is the top-ranked candidate whatever order the
    # model answered in, and a reconciliation entry for a trial the model never
    # mentioned carries its real rank rather than falling to the bottom.
    #
    # FIRST position wins on a repeated id, so a duplicate cannot promote a
    # trial past its own best rank. None when the id is not in filtered_trials
    # at all: that is unreachable from Stage 5 (the out-of-set detector drops
    # such an entry before it can get here) and reachable by a caller that
    # builds `evaluations` by hand, for which "this trial has no position in a
    # ranking that was never produced" is the honest answer -- the same one the
    # rerank_score lookup beside it already gives.
    _rank_by_nct = {}
    for _pos, _t in enumerate(state.get("filtered_trials", []), start=1):
        if "trial" in _t and "nct_id" in _t["trial"]:
            _rank_by_nct.setdefault(_t["trial"]["nct_id"], _pos)

    # Merge scores and trial_number into each evaluation
    for e in evaluations:
        nct_id = e.get("nct_id", "")
        _scores = _rerank_lookup.get(nct_id, (None, None, None, None))
        e["rerank_score"]     = _scores[0]
        e["rerank_score_raw"] = _scores[1]
        e["mesh_boost"]       = _scores[2]
        e["mesh_boost_tier"]  = _scores[3]
        e["trial_number"] = _rank_by_nct.get(nct_id)

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
        "llm_classifier_prompt": state.get("llm_classifier_prompt", ""),
        "llm_classifier_input_tokens": state.get("llm_classifier_input_tokens", 0),
        "llm_classifier_output_tokens": state.get("llm_classifier_output_tokens", 0),
        "timestamp": datetime.now().isoformat(),
        "error": "",
        "patient_data_hash": "",
        # Which node produced this result, stated rather than inferred.
        "terminal_node": TERMINAL_NODE_FINALIZE,
        **_pipeline_provenance(state),
    }

    log.info("finalized", stage=6, node=TERMINAL_NODE_FINALIZE,
             patient_id=patient_data["patient_id"],
             eligible=len(matches), not_eligible=len(near_misses),
             not_evaluable=len(not_evaluable))


    return {"result": result}


def node_no_candidates(state: TrialMatchState) -> dict:
    """
    Terminal node: no candidates survived retrieval or filtering.

    Returns a clean result indicating no trials were found,
    rather than wasting an LLM classifier call on an empty candidate set.
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
        "llm_classifier_prompt": "",
        "llm_classifier_input_tokens": 0,
        "llm_classifier_output_tokens": 0,
        "message": "No trials passed retrieval or filtering for this patient.",
        "error": "",
        "patient_data_hash": "",
        "terminal_node": TERMINAL_NODE_NO_CANDIDATES,
        "stage_timings": state.get("stage_timings", {}),
        "timestamp": datetime.now().isoformat(),
        **_pipeline_provenance(state),
    }

    log.info("no candidates survived the pipeline",
             node=TERMINAL_NODE_NO_CANDIDATES,
             patient_id=patient_data["patient_id"], eligible=0)

    return {"result": result}


def node_error_handler(state: TrialMatchState) -> dict:
    """
    Error terminal node: the LLM classifier failed after all retries.

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
        "llm_classifier_prompt": state.get("llm_classifier_prompt", ""),
        "llm_classifier_input_tokens": state.get("llm_classifier_input_tokens", 0),
        "llm_classifier_output_tokens": state.get("llm_classifier_output_tokens", 0),
        "error": error_msg,
        "patient_data_hash": "",
        "terminal_node": TERMINAL_NODE_ERROR,
        # Retired key. It said the same thing as llm_classifier_retries but existed only
        # on this path, which is how the count came to be logged as 0 for every
        # run that did not end here. Kept as an alias for one release so an
        # external consumer of the API response is not broken by the rename;
        # nothing inside this repo reads it.
        "llm_classifier_retries_exhausted": state.get("llm_classifier_retries", 0),
        "stage_timings": state.get("stage_timings", {}),
        "timestamp": datetime.now().isoformat(),
        **_pipeline_provenance(state),
    }

    log.error("pipeline failed", node=TERMINAL_NODE_ERROR,
              patient_id=patient_data["patient_id"], status="error",
              error_message=error_msg)

    return {"result": result}


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 14:01:38 2026

@author: ramyalsaffar
"""
