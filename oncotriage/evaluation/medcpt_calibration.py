# MedCPT Score Calibration
##########################

"""
Measure the distribution of ``medcpt_score_max`` so MEDCPT_SCORE_FLOOR can be
SET FROM DATA rather than chosen.

WHY THIS FILE EXISTS. See the comment above ``FLOOR_PERCENTILE`` below. The
short version: the Stage 4 absolute knob used to be a floor on the FUSED RRF
score, set from a comment about MedCPT's range, and it could never fire. The
replacement reads the calibrated per-query MedCPT score -- and a number chosen
by judgement would be the same defect one layer along, so it is measured here.

WHAT IT RUNS, AND WHAT IT COSTS. Stages 1, 2 and 3 only:

    node_query_expansion   deterministic, no LLM, free
    node_hybrid_retrieval  Qdrant + ONE text-embedding-3-small call per
                           PATIENT, over ``state["expanded_query"]``
    node_cross_encoder_rerank   MedCPT, local, free

ONE CALL PER PATIENT, NOT ONE PER RERANK QUERY, and this file claimed the
latter until it was measured. Stage 2's three BM25 channels are FastEmbed
sparse vectors computed locally, and Stage 3's rerank queries are scored by
MedCPT, which is also local -- so the only priced endpoint either stage
reaches is the dense channel's single embedding of the fused query
(``oncotriage/agent/retrieval.py``: ``query = state["expanded_query"]``, then
``models.get_embedding(query)`` once).

IT STOPS BEFORE STAGE 4 AND STAGE 5. No eligibility call is made, so the run
costs ``SAMPLE_TOTAL`` embedding calls and nothing else. MEASURED on the
shipped corpus, 2026-09-03: $0.000074 for the whole run, read back out of
``oncotriage.spend.SPEND_LEDGER`` rather than estimated. It is not free, and
saying "free" about a script that calls an API is the kind of claim this
project does not make.

HOW THE PATIENTS ARE DRAWN. ``SAMPLE_TOTAL`` patients in total, allocated
PROPORTIONALLY across every cancer group the corpus holds -- minimum one per
non-empty group -- by ``oncotriage/evaluation/cohort.allocate_proportional``,
the same allocator the campaign cohort and the evaluation extract use, so the
three samplers in this package cannot come to disagree about what
"proportional" means. Each patient's group comes from the ONE grouper,
``oncotriage/registries/primary_cancer.py:cancer_group_key``, reached here as
``sampling.classify_cancer`` -- an alias of it rather than a forwarder, so
this pool cannot be drawn through a vocabulary of its own. Each group's pool
is sorted by bundle filename so the draw is order-independent, and its members
are taken with ``random.Random(seed).sample``. Seed 42 by default, which is
``sampling.SEED``.

THIS PARAGRAPH DESCRIBED THE RETIRED RULE -- "ten each from breast, colon and
lung" -- for the whole of the pass that replaced it, and that is recorded
rather than quietly corrected. The code moved at the cohort-stratification
pass and the prose did not, so a reader of this file was told the pool was
three fixed groups of ten while the function beneath drew a proportional
sample over fifteen. A docstring that contradicts its own function is the same
defect class as the stale comment ``RERANK_SCORE_THRESHOLD`` died of, one
directory over.

IT DRAWS FROM THE FHIR CORPUS, NOT FROM ``inferences.db``, and that is
deliberate: the floor must be measured against the patients the pipeline can be
asked about, not against the subset that happens to have been run already --
which is a record of past batch runs and would make the floor a function of
what was measured last time.

THE FLOOR IS STALE THE MOMENT ANY OF FOUR THINGS MOVES: the indexed corpus
(different documents to score), the rerank queries (different queries to score
them with), the cross-encoder checkpoint (a different scale entirely), or THE
GROUPING THIS POOL IS DRAWN THROUGH (a different population to score them
over). Re-run this after any of them. ``config.MEDCPT_SCORE_FLOOR`` carries
the same four conditions and is where each is argued; the count is stated in
both places because a reader who meets one of them will be at whichever of the
two files they opened first.

Run from terminal (or F5 in Spyder):
    python measure_medcpt_scores.py
    python measure_medcpt_scores.py --sample-total 60 --seed 42
    python measure_medcpt_scores.py --json /tmp/medcpt.json

Exit codes:
    0 -- the measurement completed
    1 -- no patient could be measured (nothing to report a percentile over)
"""


# Imports
#--------
import glob
import hashlib
import json
import os
import random

import numpy as np

from oncotriage import paths
from oncotriage.agent.graph import build_initial_state
from oncotriage.agent.retrieval import (
    apply_quality_gate,
    node_cross_encoder_rerank,
    node_hybrid_retrieval,
    node_query_expansion,
)
from oncotriage.evaluation.cohort import allocate_proportional
from oncotriage.evaluation.sampling import SEED, classify_cancer
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.observability import console, correlation_scope
from oncotriage.registries.primary_cancer import _resolve_primary_cancer
from oncotriage.utils import CaffeinateSession


#------------------------------------------------------------------------------


# ===========================================================================
# CONFIGURATION
# ===========================================================================

# THE DEFECT THIS FILE REPLACES. oncotriage/config.py used to hold
# RERANK_SCORE_THRESHOLD = -10 under a comment saying MedCPT scores run about
# -25 .. +10. The comment was true of the code it was written for and false of
# the code it sat above: Stage 3 had since moved to multi-query RRF fusion, so
# the gate was comparing that -10 against a FUSED value in the 0.01 .. 0.06
# range, and max(percentile, -10) could never select the floor. A NUMBER SET
# FROM A STALE COMMENT is the failure; a number set from judgement is the same
# failure with better prose. Hence the measurement.
#
# (This paragraph is a `#` comment rather than part of the module docstring on
# purpose: tests/test_package_invariants.py check 2h counts a name inside any
# string literal as a read, so a deleted constant named in a docstring is
# invisible to the scan that would report it if somebody reinstated it.)

# The percentile the floor is set at. 5 keeps the gate a floor -- something
# that fires on the tail -- rather than a second percentile competing with
# QUALITY_THRESHOLD_PERCENTILE for the same job.
FLOOR_PERCENTILE = 5

# Reported so a reader can see the whole shape and disagree with the choice
# above from the same numbers.
REPORTED_PERCENTILES = (0, 1, 5, 10, 25, 50, 75, 90, 95, 99, 100)

# THE DRAW IS A TOTAL, NOT A PER-GROUP COUNT, AS OF THE COHORT-STRATIFICATION
# PASS. `PATIENTS_PER_CANCER = 10` and `CANCER_TYPES` used to live here and in
# oncotriage/evaluation/sampling.py; that three-group vocabulary was fitted to a
# retired corpus and on the current one it excluded 289 of 1,000 patients -- so
# the pool this floor is calibrated over covered 71% of the corpus and RAISED
# whenever a group held fewer than ten. Both are properties of the vocabulary,
# which now has one owner in oncotriage/registries/primary_cancer.py.
#
# `config.MEDCPT_SCORE_FLOOR`'s FOURTH STALENESS CONDITION IS THIS CHANGE, and
# it is recorded there. The shipped floor was measured over the OLD pool; this
# pass deliberately did not re-measure it, because a recalibration is a real
# run against a live index.
SAMPLE_TOTAL = 30


#------------------------------------------------------------------------------


# ===========================================================================
# SAMPLE SELECTION
# ===========================================================================

def _primary_condition(patient_data) -> str:
    """The condition ``sampling.classify_cancer`` is applied to.

    ``inferences.primary_condition`` is written by
    ``registries.primary_cancer._resolve_primary_cancer``, so the same function
    is used here rather than "the first condition in the bundle" -- which is
    the assumption CLAUDE.md names as never safe. It returns the winning
    condition's DISPLAY TEXT (a str) or None, not a condition dict.
    """
    return _resolve_primary_cancer(patient_data.get("conditions", [])) or ""


def select_patients(sample_total: int = SAMPLE_TOTAL,
                    seed: int = SEED) -> list:
    """Draw ``sample_total`` patients from the FHIR corpus, stratified by group.

    Returns a list of ``(cancer_group, bundle_path, patient_data)``, ordered by
    group and then by the draw.

    PROPORTIONAL, MINIMUM ONE PER NON-EMPTY GROUP, through
    ``cohort.allocate_proportional`` -- the same allocator the campaign cohort
    and the evaluation extract use, so the three samplers in this package
    cannot come to disagree about what "proportional" means.

    THE RAISE IS GONE AND ITS ARGUMENT IS ANSWERED RATHER THAN DROPPED. It read
    "a silently short group would put a floor measured on twenty-two patients
    into a config comment claiming thirty" -- which is right, and the allocator
    makes it unreachable rather than merely tolerated: it never asks a group for
    more than it holds, so the total is ``min(sample_total, corpus)`` by
    construction. ``measure()`` records the realised total and the per-group
    breakdown, so the comment states what was drawn.
    """
    bundles = sorted(glob.glob(os.path.join(paths.data_fhir_path, "*.json")))
    console.out(f"Corpus: {len(bundles)} bundles at {paths.data_fhir_path}")

    # EVERY PATIENT LANDS IN A GROUP. The dict is built from what the corpus
    # HOLDS rather than seeded with a short list of names, and the
    # `if kind in groups` filter that used to discard everything else is gone
    # -- that filter is what excluded 289 of this corpus's 1,000 patients.
    groups = {}
    parsed = {}
    for path in bundles:
        try:
            patient_data = parse_fhir_bundle(path)
        except Exception as exc:
            # Counted rather than swallowed. A bundle this script cannot parse
            # is one the pipeline cannot either, and the count is the only
            # thing that says the corpus below is not the corpus above.
            parsed.setdefault("_errors", []).append((os.path.basename(path), repr(exc)))
            continue
        kind = classify_cancer(_primary_condition(patient_data))
        groups.setdefault(kind, []).append(path)
        parsed[path] = patient_data

    errors = parsed.pop("_errors", [])
    if errors:
        console.out(f"WARNING: {len(errors)} bundle(s) failed to parse and are "
                    f"excluded from the pool. First: {errors[0]}")

    allocation = allocate_proportional(
        {k: len(v) for k, v in groups.items()}, sample_total)

    selected = []
    for kind in sorted(groups):
        share = allocation[kind]
        if not share:
            continue
        pool = sorted(groups[kind])          # filename order: draw-independent
        # A FRESH Random(seed) PER GROUP, which is what this function has always
        # done. It is not the shared-stream convention the other samplers use,
        # and it is kept rather than "corrected" because changing it changes
        # which patients this floor would be re-measured over -- a change to a
        # calibration input, disguised as a tidy-up.
        for path in random.Random(seed).sample(pool, share):
            selected.append((kind, path, parsed[path]))

    # TWO DIFFERENT NUMBERS, AND UNDER THE PROPORTIONAL DRAW THEY ARE EASY TO
    # CONFUSE. The first line is the CORPUS -- how many patients each group
    # holds -- and the second is what was DRAWN from it. Only the first was
    # printed, under the label "Pool sizes", which reads as the drawn pool: a
    # reader seeing `breast=290` would reasonably take it for 290 breast
    # patients measured. That was harmless while the draw was a fixed ten per
    # group and the two could not be confused, and became misleading the
    # moment the allocation started varying with the corpus -- which is the
    # change `config.MEDCPT_SCORE_FLOOR`'s fourth staleness condition records.
    console.out("Corpus group populations: "
                + ", ".join(f"{k}={len(groups[k])}" for k in sorted(groups)))
    console.out(f"Drawn ({len(selected)} of {sample_total} requested, "
                f"proportional, minimum one per non-empty group): "
                + ", ".join(f"{k}={allocation[k]}" for k in sorted(groups)
                            if allocation[k]))
    return selected


#------------------------------------------------------------------------------


# ===========================================================================
# STAGES 1-3
# ===========================================================================

def rerank_one(patient_data: dict) -> list:
    """Run Stages 1, 2 and 3 and return the reranked trial objects.

    The nodes are called directly rather than through the compiled graph
    because the graph's conditional edges route to Stage 4 and the terminal
    nodes, and Stage 5 is a billed call. Calling three nodes in order is the
    same code the graph would run for those three.
    """
    state = build_initial_state(patient_data)
    state.update(node_query_expansion(state))
    state.update(node_hybrid_retrieval(state))
    state.update(node_cross_encoder_rerank(state))
    return state["reranked_trials"]


def pool_digest(reranked: list) -> str:
    """A stable identity for a reranked pool: its SORTED (nct_id, score) pairs.

    WHY THIS EXISTS, and it is the most important thing this file measures.
    Synthea patients within one cancer type carry near-identical condition
    lists, so Stage 1 builds the same expanded query, Stage 2 retrieves the
    same trials and Stage 3 hands back the same pool. Measured on the shipped
    corpus under the PROPORTIONAL draw, 2026-09-03: 30 patients produce 21
    DISTINCT pools -- 840 distinct trials counted 1,200 times. Without this,
    "1,200 trials from 30 patients" is a sample size the measurement does not
    have. (It was 19 pools and 760 trials under the retired three-group draw,
    measured 2026-08-07; the figure is a property of the pool, so it moves
    with the draw and with the corpus.)

    THIS DIGEST IS THE ONE PART OF THE MEASUREMENT THAT IS NOT REPRODUCIBLE
    RUN TO RUN, and the recommendation is taken from the column it produces,
    so the sensitivity is worth knowing before trusting a fourth decimal.
    Three runs against one corpus, one seed and one collection gave 21, 21 and
    20 distinct pools: on the third, one patient's Stage 2 returned a
    candidate set equal to another patient's, and the distinct-pool p5 moved
    by 0.034. THE CAUSE IS THE ANN SEARCH AND NOT THIS FUNCTION, measured --
    across those thirty pools the count of distinct trial id SETS equals the
    count of distinct digests, and no two pools share an id set while
    differing in digest, so MedCPT is bit-reproducible given the same trials
    and Qdrant's HNSW is what varied. The per-patient distribution is
    unaffected: identical to four decimals at all eleven reported percentiles
    in all three runs. ``config.MEDCPT_SCORE_FLOOR`` records the spread and
    which run the shipped value came from.

    A CHEAPER SIGNATURE WAS TRIED FIRST AND WAS WRONG. Grouping patients by
    (min, max) of their pool's scores reported all ten breast patients as one
    pool, because they share their top and bottom trial while differing in the
    middle. Extremes are not an identity.

    SORTED, NOT IN RANK ORDER, and the first version of this function got that
    wrong. It hashed the list as Stage 3 emitted it -- ``json.dumps(...,
    sort_keys=True)`` sorts DICT KEYS, not list elements -- so two pools
    holding exactly the same forty trials at exactly the same scores in a
    different rank order hashed differently and were counted as two distinct
    measurements. For a DISTRIBUTION, order carries no information: the same
    multiset of scores contributes identically however it is arranged. An
    order-sensitive digest therefore OVERSTATES distinctness, which is the
    direction that flatters the sample -- so it was fixed rather than noted.
    """
    # Keyed on the NCT ID ALONE, not on the whole tuple. medcpt_score_max is
    # legitimately None (the skip_cross_encoder path writes None for every
    # trial), and a plain tuple sort compares the second element whenever two
    # first elements tie -- None < float raises TypeError. IDs are unique
    # within a pool, so the key is total and the score never enters the
    # comparison while still entering the digest.
    payload = json.dumps(
        sorted(((t["trial"]["nct_id"], t.get("medcpt_score_max"))
                for t in reranked), key=lambda pair: pair[0]),
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def rerank_sample(sample_total: int = SAMPLE_TOTAL,
                  seed: int = SEED) -> list:
    """Run Stages 1-3 ONCE for the whole sample.

    Returns ``[(cancer_type, bundle_name, reranked_pool, digest), ...]``. Both
    analyses below read this one list, so they cannot describe different runs
    -- and the embedding calls are paid for once rather than twice, which an
    earlier draft of this file did by calling select_patients() again inside
    floor_impact().
    """
    selected = select_patients(sample_total, seed)
    pools = []
    for idx, (kind, path, patient_data) in enumerate(selected, 1):
        name = os.path.basename(path)
        with correlation_scope():
            reranked = rerank_one(patient_data)
        digest = pool_digest(reranked)
        pools.append((kind, name, reranked, digest))
        console.out(f"[{idx:>3}/{len(selected)}] {kind:<6} {name[:40]:<40} "
                    f"reranked={len(reranked):>3} pool={digest[:8]}")
    return pools


def distinct_pools(pools: list) -> list:
    """One entry per DISTINCT pool, first occurrence wins."""
    seen, out = set(), []
    for entry in pools:
        if entry[3] in seen:
            continue
        seen.add(entry[3])
        out.append(entry)
    return out


def measure(pools: list, seed: int, sample_total: int) -> dict:
    """The distribution report, over pools produced by ``rerank_sample``."""
    scores = []                # every medcpt_score_max, across every patient
    per_patient = []
    unscored = 0               # trials whose medcpt_score_max came back None
    query_counts = {}

    for kind, name, reranked, digest in pools:
        patient_scores = []
        for trial in reranked:
            value = trial.get("medcpt_score_max")
            n_q = trial.get("medcpt_queries_scored")
            query_counts[n_q] = query_counts.get(n_q, 0) + 1
            if value is None:
                unscored += 1
            else:
                patient_scores.append(float(value))

        scores.extend(patient_scores)
        per_patient.append({
            "cancer_type": kind,
            "bundle": name,
            "pool_digest": digest,
            "reranked": len(reranked),
            "scored": len(patient_scores),
            "min": min(patient_scores) if patient_scores else None,
            "max": max(patient_scores) if patient_scores else None,
        })

    selected = pools
    report = {
        "patients_requested": len(selected),
        "patients_measured": sum(1 for p in per_patient if p["scored"] > 0),
        "seed": seed,
        "sample_total_requested": sample_total,
        "trials_scored": len(scores),
        "trials_unscored": unscored,
        "queries_scored_histogram": {str(k): v for k, v in sorted(
            query_counts.items(), key=lambda kv: (kv[0] is None, kv[0]))},
        # WHAT WAS ACTUALLY DRAWN, PER GROUP. Derived from per_patient rather
        # than passed in, so it describes the pools this report is computed
        # over and cannot disagree with them. It is a field of its own because
        # under the proportional draw the composition is a function of the
        # CORPUS -- it was a constant 10/10/10 before -- so it is the first
        # thing a reader needs in order to judge how far the floor below
        # generalises, and re-deriving it from per_patient is work every
        # consumer of the artefact would otherwise repeat.
        "drawn_composition": {
            k: sum(1 for p in per_patient if p["cancer_type"] == k)
            for k in sorted({p["cancer_type"] for p in per_patient})},
        "per_patient": per_patient,
    }

    if scores:
        arr = np.asarray(scores, dtype=float)
        report["percentiles"] = {
            str(p): float(np.percentile(arr, p)) for p in REPORTED_PERCENTILES
        }
        report["mean"] = float(arr.mean())
        report["std"] = float(arr.std())
        report["proposed_floor"] = float(np.percentile(arr, FLOOR_PERCENTILE))
        report["floor_percentile"] = FLOOR_PERCENTILE

    # --- THE SAME DISTRIBUTION OVER DISTINCT POOLS --------------------------
    #
    # WITHOUT THIS THE HEADLINE NUMBER IS A COHORT ARTEFACT WEARING A SAMPLE
    # SIZE. Patients within one cancer group routinely share a byte-identical
    # reranked pool: measured 2026-09-03 on the proportional draw, 30 patients
    # collapse to 21 distinct pools with the largest cluster holding five, so
    # "1,200 trials from 30 patients" is really 840 distinct trials unevenly
    # weighted. The per-patient figure is still the one the floor is set from
    # -- production gates per patient, so a pool that recurs five times really
    # is gated five times -- but a reader who is not shown the deduplicated
    # figure beside it cannot tell a broad measurement from a narrow one
    # repeated.
    unique = distinct_pools(pools)
    unique_scores = [
        float(t["medcpt_score_max"])
        for _k, _n, reranked, _d in unique
        for t in reranked
        if t.get("medcpt_score_max") is not None
    ]
    report["distinct_pools"] = len(unique)
    report["distinct_pool_digests"] = [d for _k, _n, _r, d in unique]
    if unique_scores:
        u = np.asarray(unique_scores, dtype=float)
        report["distinct_percentiles"] = {
            str(p): float(np.percentile(u, p)) for p in REPORTED_PERCENTILES
        }
        report["distinct_trials_scored"] = len(unique_scores)
        report["distinct_proposed_floor"] = float(
            np.percentile(u, FLOOR_PERCENTILE))

    # --- THE RECOMMENDATION IS A RULE, NOT A CHOICE ------------------------
    #
    # Take the LOWER of the two estimates. Stated as a rule so the same
    # measurement always produces the same number and nobody -- including
    # whoever runs this next -- gets to pick the one they like after seeing
    # both.
    #
    # WHY LOWER. A floor's two failure directions are not symmetric. Set too
    # low it drops nothing, which is exactly the state this replaced, so the
    # cost is zero and the error is obvious (floor_only == 0). Set too high it
    # silently removes trials that would have been evaluated, and that loss
    # appears in no counter, no log line and no stored row -- it looks like a
    # patient with fewer matches. When two defensible estimates of the same
    # quantity disagree, the permissive one is the only one whose error is
    # visible.
    if "proposed_floor" in report:
        candidates = [("per-patient", report["proposed_floor"])]
        if "distinct_proposed_floor" in report:
            candidates.append(("distinct-pool", report["distinct_proposed_floor"]))
        basis, value = min(candidates, key=lambda kv: kv[1])
        report["recommended_floor"] = value
        report["recommended_floor_basis"] = basis

    return report


#------------------------------------------------------------------------------


# ===========================================================================
# WHAT THE FLOOR WOULD ACTUALLY DROP
# ===========================================================================

def floor_impact(pools: list, floor: float) -> dict:
    """How many trials the floor drops THAT THE PERCENTILE DOES NOT.

    The number that says whether the absolute knob is doing any work. It is
    MEASURED by running the gate over each patient's real reranked pool, never
    derived from the threshold value -- a floor set at the 5th percentile of a
    pooled distribution does not drop 5% of any individual pool, because the
    percentile knob is computed within each pool and the two overlap.

    It may legitimately be zero. Zero is a finding: it says the relative knob
    already removes everything the absolute one would, on this corpus, for
    these patients.

    NOTE WHAT THIS IS NOT. It gates the RERANKED pool, which is what Stage 4
    receives; production applies the MeSH / stage / histology / age / sex drops
    first, so the pool the gate actually sees in a run is smaller and its
    percentile sits elsewhere. Those drops need a live MeSH filter and the
    patient's trees, and folding them in would make this a measurement of Stage
    4 rather than of the knob. Stated rather than glossed.
    """
    totals = {"pools": 0, "trials": 0,
              "percentile": 0, "floor": 0, "floor_only": 0}
    per_patient = []

    for kind, name, reranked, digest in pools:
        if not reranked:
            continue
        # Sorted the way Stage 4 sorts before it gates, so the pool the gate
        # sees here is the pool it sees in production.
        pool = sorted(reranked,
                      key=lambda x: (x.get("rerank_score", 0),
                                     x["trial"]["nct_id"]),
                      reverse=True)
        _kept, _threshold, drops = apply_quality_gate(pool, medcpt_floor=floor)
        totals["pools"] += 1
        totals["trials"] += len(pool)
        for key in ("percentile", "floor", "floor_only"):
            totals[key] += drops[key]
        per_patient.append({
            "cancer_type": kind,
            "bundle": name,
            "pool_digest": digest,
            "trials": len(pool),
            "kept": len(_kept),
            "threshold": _threshold,
            **drops,
        })

    totals["floor_used"] = floor
    totals["per_patient"] = per_patient
    return totals


#------------------------------------------------------------------------------


# ===========================================================================
# REPORT
# ===========================================================================

def print_report(report: dict) -> None:
    console.out()
    console.out("=" * 70)
    console.out("medcpt_score_max DISTRIBUTION")
    console.out("=" * 70)
    console.out(f"patients drawn      : {report['patients_requested']} "
                f"(of {report['sample_total_requested']} requested, "
                f"proportional across cancer groups, "
                f"seed {report['seed']})")
    console.out("  by cancer group   : "
                + (", ".join(f"{k}={v}" for k, v in
                             sorted(report.get("drawn_composition", {}).items()))
                   or "(none)"))
    console.out(f"patients measured   : {report['patients_measured']}")
    console.out(f"trials scored       : {report['trials_scored']}")
    console.out(f"trials with NO score: {report['trials_unscored']}")
    console.out(f"queries per trial   : {report['queries_scored_histogram']}")

    if "percentiles" not in report:
        console.out("NO SCORES COLLECTED -- nothing to set a floor from.")
        return

    console.out()
    console.out("          per-patient   per-DISTINCT-pool")
    for p in REPORTED_PERCENTILES:
        d = report.get("distinct_percentiles", {}).get(str(p))
        console.out(f"  p{p:<3} {report['percentiles'][str(p)]:+13.4f}"
                    f"{('%+.4f' % d).rjust(20) if d is not None else ' ' * 20}")
    console.out(f"  mean {report['mean']:+13.4f}   std {report['std']:9.4f}")

    # THE DEGENERACY REPORT. Loud, and above the proposed floor rather than
    # below it, because it is what decides how far to trust the number.
    console.out()
    console.out(f"DISTINCT POOLS: {report['distinct_pools']} "
                f"across {report['patients_requested']} patients "
                f"({report.get('distinct_trials_scored', 0)} distinct trials "
                f"of {report['trials_scored']} counted)")
    if report["distinct_pools"] < report["patients_requested"]:
        console.out("  WARNING: patients share reranked pools. Synthea patients "
                    "within one cancer type carry near-identical condition "
                    "lists, so Stage 1 builds the same expanded query and "
                    "Stage 2 retrieves the same trials. The per-patient "
                    "percentile below is weighted by how often each pool "
                    "RECURS, not by how many distinct trials were seen.")

    console.out()
    console.out(f"  p{report['floor_percentile']} per patient        : "
                f"{report['proposed_floor']:+.4f}")
    if "distinct_proposed_floor" in report:
        console.out(f"  p{report['floor_percentile']} over distinct pools: "
                    f"{report['distinct_proposed_floor']:+.4f}")
    console.out()
    console.out(f"RECOMMENDED MEDCPT_SCORE_FLOOR = "
                f"{report['recommended_floor']:+.4f}"
                f"   [{report['recommended_floor_basis']}]")
    console.out("  Rule: the LOWER of the two, always, and it is a rule rather "
                "than a judgement so the same measurement always yields the")
    console.out("  same number. A floor's failure directions are asymmetric -- "
                "set too low it drops nothing and costs nothing, set too high "
                "it")
    console.out("  silently removes trials that would have been evaluated and "
                "the loss appears nowhere. When two defensible estimates")
    console.out("  disagree, the permissive one is the only one whose error is "
                "visible.")


def print_impact(totals: dict) -> None:
    console.out()
    console.out("=" * 70)
    console.out(f"WHAT THE FLOOR {totals['floor_used']:+.4f} DROPS, MEASURED")
    console.out("=" * 70)
    console.out(f"pools gated                     : {totals['pools']}")
    console.out(f"trials entering the gate        : {totals['trials']}")
    console.out(f"dropped by the percentile       : {totals['percentile']}")
    console.out(f"dropped by the floor            : {totals['floor']}")
    console.out(f"dropped by the floor ONLY       : {totals['floor_only']}"
                f"   <- the number that says whether the knob does any work")

    # AND THE SAME NUMBER OVER DISTINCT POOLS. Ten floor-only drops across ten
    # patients who share one pool is ONE finding seen ten times, and reporting
    # only the first number would overstate the knob by an order of magnitude.
    seen, distinct_only, distinct_pools_n = set(), 0, 0
    for r in totals["per_patient"]:
        if r["pool_digest"] in seen:
            continue
        seen.add(r["pool_digest"])
        distinct_pools_n += 1
        distinct_only += r["floor_only"]
    console.out(f"  ...over {distinct_pools_n} DISTINCT pools: {distinct_only}"
                f"   <- the same finding with duplicates removed")
    n_affected = sum(1 for r in totals["per_patient"] if r["floor_only"])
    console.out(f"patients affected at all        : {n_affected}"
                f"/{len(totals['per_patient'])}")


#------------------------------------------------------------------------------


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="Measure the medcpt_score_max distribution and propose "
                    "MEDCPT_SCORE_FLOOR.")
    parser.add_argument("--sample-total", type=int,
                        default=SAMPLE_TOTAL,
                        help="how many patients in total, allocated "
                             "proportionally across every cancer group the "
                             "corpus holds, minimum one per non-empty group "
                             f"(default {SAMPLE_TOTAL})")
    parser.add_argument("--seed", type=int, default=SEED,
                        help=f"sampling seed (default {SEED})")
    parser.add_argument("--floor", type=float, default=None,
                        help="measure the impact of THIS floor instead of the "
                             "one this run proposes")
    parser.add_argument("--json", default=None,
                        help="also write the whole report to this path")
    args = parser.parse_args(argv)

    # Taken AFTER parse_args, so --help does not spawn a subprocess. Held only
    # around the measurement, which is the part that takes ~20 minutes and is
    # the reason a laptop must not sleep mid-run.
    with CaffeinateSession("medcpt-calibration"):
        pools = rerank_sample(args.sample_total, args.seed)

    report = measure(pools, args.seed, args.sample_total)
    print_report(report)

    if "recommended_floor" not in report:
        return 1

    # The RECOMMENDED floor, not the per-patient one: the impact figures below
    # must describe the number that will actually be written into the config.
    floor = args.floor if args.floor is not None else report["recommended_floor"]
    totals = floor_impact(pools, floor)
    print_impact(totals)
    report["impact"] = totals

    if args.json:
        with open(args.json, "w") as handle:
            json.dump(report, handle, indent=2)
        console.out(f"\nReport written to {args.json}")

    return 0


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug  7 2026

@author: ramyalsaffar
"""
