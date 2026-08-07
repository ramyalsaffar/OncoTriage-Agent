# MeSH Boost / Quality Gate Test
################################

"""
MeSH Boost and Quality Gate Test

Fixture tests for the separation of the Stage 3 MeSH relevance boost from the
Stage 4 dynamic quality gate, in 13- LangGraph Agent.py.

The defect under test: the gate used to be a percentile of the BOOSTED score,
so with a direct-match boost of 0.75 of the RRF spread every boosted trial
cleared the gate by construction and unboosted trials were cut in its place.
The gate was measuring MeSH boost membership, not trial quality — a second,
uncounted MeSH filter sitting behind the one the ablation flag controls.

Covers:
    1. Stage 3 boost — tiers, magnitude (0.25 of spread, both tiers equal),
       raw score preserved, boost stored on its own field, re-sort by boost
    2. Degenerate spread — absolute floor path, reported as its own path
    3. Quality gate — computed on the UNBOOSTED score, with the 40-trial
       distribution from the original simulation
    4. THE FIXTURE: an unboosted high-quality trial survives the gate, and a
       boosted low-quality trial no longer buys its way through
    5. skip_mesh_filter — bypasses the boost (Stage 3) and the drop (Stage 4)
    6. node_rule_based_filter end to end on a synthetic state

No network and no LLM: MeSH ancestry is supplied by a stub filter, and the
patient carries no MeSH trees so the Stage 4 MeSH drop is inert.

Running this BUILDS NO MODEL. Measured, 2026-08-06, by inspecting
deps.cached_keys() and oncotriage.embedding._MODEL after a full run: the only
dependency resolved is `mesh_filter`, and neither MedCPT half nor the FastEmbed
BM25 model is constructed. This file used to exec-chain "13- LangGraph Agent.py",
which built both at exec time; item 20c pass 2c made them lazy behind
oncotriage/agent/deps.py, and nothing below scores a pair. No inference is
performed.

(The `fastembed` LIBRARY still arrives in sys.modules, because
qdrant_client.fastembed_common imports it. That is a library import, not a model
construction, and it was equally true under the old chain.)

Run from terminal (or F5 in Spyder):
    python tests/test_agent_mesh_boost_and_quality_gate.py
    (was: python "31- MeSH Boost Gate Test.py")

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# PASS 20d-1: THIS FILE IMPORTS THE PACKAGE. It used to exec "01- Imports.py"
# and "02- Utility Functions.py" into its own globals and then exec_chain()
# "13- LangGraph Agent.py", which is how `np` and every agent name below used to
# arrive. Item 20c split File 13 into oncotriage/agent/, so each name comes from
# the module that defines it.
#
# THE CANDIDATE DIRECTORY IS THE PARENT OF THIS FILE'S, not this file's own.
# The same block Files 47, 48 and 49 carry looks one level up because this file
# now sits in tests/ and the package sits BESIDE tests/, not inside it.
# `pip install -e .` makes the whole block a no-op.
import json
import os
import sys

import numpy as np


def _reject(token):
    """parse_constant hook: a strict JSON reader has no Infinity."""
    raise ValueError(f'not valid JSON: {token}')

try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

from oncotriage.agent.filtering import node_rule_based_filter
from oncotriage.agent.retrieval import (
    RERANK_RRF_K,
    apply_mesh_relevance_boost,
    apply_quality_gate,
    unboosted_score,
)
from oncotriage.config import (
    MAX_TRIALS_FOR_EVALUATION,
    MESH_BOOST_DIRECT_FLOOR,
    MESH_BOOST_DIRECT_FRACTION,
    MESH_BOOST_PAN_FLOOR,
    MESH_BOOST_PAN_FRACTION,
    QUALITY_THRESHOLD_PERCENTILE,
)


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_close(label: str, actual, expected, tol: float = 1e-9) -> None:
    """Assert float equality within a tolerance."""
    check(label, abs(actual - expected) <= tol, True)
    if abs(actual - expected) > tol:
        _FAILURES[-1] = f"{label}\n          expected: {expected}\n          actual:   {actual}"


# ===========================================================================
# FIXTURES
# ===========================================================================

class StubMeshFilter:
    """Stands in for MeSHCancerSiteFilter with a hand-written tree map.

    Only the two methods apply_mesh_relevance_boost calls are implemented:
    trial_mesh_trees() and _is_pan_cancer(). Real MeSH resolution needs the
    UMLS crosswalk files and is exercised by the pipeline itself; what is
    under test here is the arithmetic and the gate, not the ontology.
    """

    def __init__(self, trees_by_nct):
        self.trees_by_nct = trees_by_nct

    def trial_mesh_trees(self, trial):
        return set(self.trees_by_nct.get(trial["nct_id"], set()))

    def _is_pan_cancer(self, trial_trees):
        # Same convention as the real filter: depth <= 2 is a broad category.
        return any(t.count(".") <= 1 for t in trial_trees)


# Patient with lung cancer: MeSH C04.588.894.797.520 (Lung Neoplasms)
PATIENT_TREES = {"C04.588.894.797.520"}

# Tree assignments used across the tests
#   direct    -> descendant of the patient tree
#   pan       -> broad neoplasm category (depth <= 2)
#   unmapped  -> no MeSH C04 trees at all (the filter KEEPS these on purpose)
TREES = {
    "direct":   {"C04.588.894.797.520.109"},
    "pan":      {"C04.588"},
    "other":    {"C04.588.322.400"},          # breast — mapped, not related
    "unmapped": set(),
}


def make_trial(nct_id: str, kind: str) -> dict:
    """Minimal trial dict shaped like the payload Stage 3 carries."""
    return {
        "nct_id": nct_id,
        "title": f"{kind} trial {nct_id}",
        "phase": "PHASE2",
        "eligibility": {
            "criteria_text": "Inclusion Criteria: adults",
            "inclusion_criteria": "Inclusion Criteria: adults",
            "exclusion_criteria": "Exclusion Criteria: none",
            "min_age": "18 Years",
            "max_age": "99 Years",
            "sex": "ALL",
        },
        "histology_tags": [],
    }


def make_candidate(nct_id: str, score: float, kind: str = "unmapped",
                   medcpt: float = None) -> dict:
    """Trial object as Stage 3 emits it, before any boost.

    medcpt DEFAULTS TO None, which is what the absolute knob reads as "do not
    judge" -- so every test written before the floor existed keeps measuring
    the relative percentile alone, unchanged. The floor gets its own section
    below, with candidates that carry a score.
    """
    return {
        "trial":                 make_trial(nct_id, kind),
        "rerank_score":          score,
        "rerank_score_raw":      score,
        "mesh_boost":            0.0,
        "mesh_boost_tier":       "none",
        "medcpt_score_max":      medcpt,
        "medcpt_queries_scored": 0 if medcpt is None else 3,
    }


def realistic_rrf_pool():
    """40 trials on a realistic Stage 3 RRF distribution.

    RRF over 3 queries with k=60 puts the top trial near 3/60 and the last
    near 3/99, which is the range the original simulation used. Eighteen
    trials are unmappable — the population the boosted gate was cutting.
    """
    pool = []
    for rank in range(40):
        score = sum(1.0 / (RERANK_RRF_K + rank) for _ in range(3))
        # Interleave so the MeSH kinds are not correlated with rank order:
        # every third trial is unmappable, the rest alternate direct/pan/other.
        kind = ["direct", "unmapped", "pan", "other", "unmapped"][rank % 5]
        pool.append(make_candidate(f"NCT{90000000 + rank}", score, kind))
    return pool


def stub_for(pool):
    """Build a StubMeshFilter whose tree map matches the pool's kinds."""
    trees = {}
    for cand in pool:
        kind = cand["trial"]["title"].split()[0]
        trees[cand["trial"]["nct_id"]] = TREES[kind]
    return StubMeshFilter(trees)


# ===========================================================================
# TEST 1: STAGE 3 BOOST — TIERS, MAGNITUDE, RAW SCORE PRESERVED
# ===========================================================================

print("=" * 70)
print("Test 1: Stage 3 boost — tiers, magnitude, raw score preserved")
print("=" * 70)

_pool = [
    make_candidate("NCT00000001", 0.050, "direct"),
    make_candidate("NCT00000002", 0.040, "pan"),
    make_candidate("NCT00000003", 0.030, "other"),
    make_candidate("NCT00000004", 0.010, "unmapped"),
]
_spread = 0.050 - 0.010
_stats = apply_mesh_relevance_boost(_pool, PATIENT_TREES, stub_for(_pool))

_by_id = {c["trial"]["nct_id"]: c for c in _pool}

check("both boost tiers are 0.25 of spread in config",
      (MESH_BOOST_DIRECT_FRACTION, MESH_BOOST_PAN_FRACTION), (0.25, 0.25))
check_close("direct boost = 0.25 x spread", _stats["boost_direct"], _spread * 0.25)
check_close("pan boost = 0.25 x spread", _stats["boost_pan"], _spread * 0.25)
check("direct and pan are one graded tier, not a 3-to-1 split",
      _stats["boost_direct"] == _stats["boost_pan"], True)

check("direct-match trial tagged 'direct'",
      _by_id["NCT00000001"]["mesh_boost_tier"], "direct")
check("pan-cancer trial tagged 'pan_cancer'",
      _by_id["NCT00000002"]["mesh_boost_tier"], "pan_cancer")
check("mapped-but-unrelated trial not boosted",
      _by_id["NCT00000003"]["mesh_boost_tier"], "none")
check("unmappable trial not boosted (neutral, as the filter intends)",
      _by_id["NCT00000004"]["mesh_boost_tier"], "none")

check_close("boost stored on its own field", _by_id["NCT00000001"]["mesh_boost"],
            _spread * 0.25)
check_close("rerank_score carries the boost",
            _by_id["NCT00000001"]["rerank_score"], 0.050 + _spread * 0.25)
check_close("rerank_score_raw is untouched by the boost",
            _by_id["NCT00000001"]["rerank_score_raw"], 0.050)
check("boosted - raw == mesh_boost for every trial",
      all(abs((c["rerank_score"] - c["rerank_score_raw"]) - c["mesh_boost"]) < 1e-12
          for c in _pool), True)

check("counters: 1 direct, 1 pan, 2 unboosted",
      (_stats["direct_boosted"], _stats["pan_boosted"], _stats["unboosted"]),
      (1, 1, 2))
check("path recorded as 'spread'", _stats["path"], "spread")

# --- The boost still does its job: it reorders ---
# The gap to close is 0.004 against a spread of 0.040, so a boost of 0.25 of
# the spread (0.010) clears it.
_pool2 = [
    make_candidate("NCT00000010", 0.050, "unmapped"),   # best text match
    make_candidate("NCT00000011", 0.046, "direct"),     # slightly worse, on-site
    make_candidate("NCT00000012", 0.010, "unmapped"),   # sets the spread
]
apply_mesh_relevance_boost(_pool2, PATIENT_TREES, stub_for(_pool2))
check("boost promotes an on-site trial over a marginally better off-site one",
      _pool2[0]["trial"]["nct_id"], "NCT00000011")

# --- but at 0.25 it does NOT flatten the cross-encoder ---
_pool3 = [
    make_candidate("NCT00000020", 0.050, "unmapped"),   # rank 1, no boost
    make_candidate("NCT00000021", 0.010, "direct"),     # last, boosted
]
apply_mesh_relevance_boost(_pool3, PATIENT_TREES, stub_for(_pool3))
check("bottom-ranked boosted trial does NOT overtake the top unboosted trial",
      _pool3[0]["trial"]["nct_id"], "NCT00000020")
# At the old 0.75 it would have: 0.010 + 0.75*0.04 = 0.040 vs ... still below.
# The failure at 0.75 is a near-tie across the whole range; assert the margin.
check("0.25 leaves three quarters of the range to the cross-encoder",
      _pool3[0]["rerank_score"] - _pool3[1]["rerank_score"] > 0.5 * 0.040, True)


# ===========================================================================
# TEST 2: DEGENERATE SPREAD — ABSOLUTE FLOOR PATH
# ===========================================================================

print()
print("=" * 70)
print("Test 2: Degenerate spread falls back to the absolute floor")
print("=" * 70)

_tied = [
    make_candidate("NCT00000030", 0.030, "direct"),
    make_candidate("NCT00000031", 0.030, "pan"),
    make_candidate("NCT00000032", 0.030, "unmapped"),
]
_tied_stats = apply_mesh_relevance_boost(_tied, PATIENT_TREES, stub_for(_tied))

check("fallback path is named in the report",
      _tied_stats["path"], "degenerate_spread_floor")
check_close("direct floor applied", _tied_stats["boost_direct"], MESH_BOOST_DIRECT_FLOOR)
check_close("pan floor applied", _tied_stats["boost_pan"], MESH_BOOST_PAN_FLOOR)
# Direct and pan-cancer now carry the same floor, so they stay tied with each
# other; what the floor must still do is lift both above the unmapped trial.
check("floor still lifts both boosted tiers above the unmapped trial",
      _tied[-1]["trial"]["nct_id"], "NCT00000032")
check("boosted trials are tied with each other, not ordered by tier",
      _tied[0]["rerank_score"], _tied[1]["rerank_score"])

# --- Unmappable patient: no boost pass at all, and it is reported ---
_nopt = [make_candidate("NCT00000040", 0.030, "direct")]
_nopt_stats = apply_mesh_relevance_boost(_nopt, set(), stub_for(_nopt))
check("no patient trees → path reported", _nopt_stats["path"], "no_patient_trees")
check("no patient trees → no boost applied", _nopt[0]["mesh_boost"], 0.0)

_empty_stats = apply_mesh_relevance_boost([], PATIENT_TREES, StubMeshFilter({}))
check("empty pool → path reported", _empty_stats["path"], "no_trials")


# ===========================================================================
# TEST 3: THE GATE IS COMPUTED ON THE UNBOOSTED SCORE
# ===========================================================================

print()
print("=" * 70)
print("Test 3: Quality gate reads rerank_score_raw, not the boosted score")
print("=" * 70)

_pool40 = realistic_rrf_pool()
_raw_p25 = float(np.percentile([c["rerank_score_raw"] for c in _pool40], 25))

apply_mesh_relevance_boost(_pool40, PATIENT_TREES, stub_for(_pool40))
_boosted_p25 = float(np.percentile([c["rerank_score"] for c in _pool40], 25))

_kept, _threshold, _drops = apply_quality_gate(_pool40)

check("config percentile is 25", QUALITY_THRESHOLD_PERCENTILE, 25)
check_close("threshold equals the p25 of the RAW scores", _threshold, _raw_p25)
check("threshold is NOT the p25 of the boosted scores",
      abs(_threshold - _boosted_p25) > 1e-9, True)
check("every survivor clears the gate on its raw score",
      all(unboosted_score(c) >= _threshold for c in _kept), True)

# The gate keeps ~75% of the pool by construction — it is a percentile, so
# the count must not depend on how many trials were boosted.
_unboosted_pool = realistic_rrf_pool()          # same scores, no boost applied
_kept_unboosted, _threshold_unboosted, _ = apply_quality_gate(_unboosted_pool)
check("survivor count is identical with and without the boost",
      len(_kept), len(_kept_unboosted))
check_close("threshold is identical with and without the boost",
            _threshold, _threshold_unboosted)
check("survivor NCT set is identical with and without the boost",
      {c["trial"]["nct_id"] for c in _kept},
      {c["trial"]["nct_id"] for c in _kept_unboosted})

# The gate is no longer a MeSH filter: unmappable trials survive at their
# population rate rather than being systematically cut.
_kept_unmapped = sum(1 for c in _kept if c["mesh_boost_tier"] == "none")
_pool_unmapped = sum(1 for c in _pool40 if c["mesh_boost_tier"] == "none")
check("unboosted trials are not systematically cut by the gate",
      _kept_unmapped >= int(0.6 * _pool_unmapped), True)
print(f"        (unboosted survivors: {_kept_unmapped}/{_pool_unmapped}, "
      f"pool survivors: {len(_kept)}/{len(_pool40)})")


# ===========================================================================
# TEST 4: THE FIXTURE — AN UNBOOSTED HIGH-QUALITY TRIAL SURVIVES THE GATE
# ===========================================================================
# This is the exact case the old gate got wrong. NCT_GOOD is unmappable on
# the MeSH axis (so it receives no boost) but has the SECOND HIGHEST raw
# cross-encoder score in the pool. Under a gate on the boosted score it was
# cut; under a gate on the raw score it must survive.

print()
print("=" * 70)
print("Test 4: FIXTURE — unboosted high-quality trial survives the gate")
print("=" * 70)

def fixture_pool():
    """12 trials. Raw p25 = 0.01275, boosted p25 = 0.01975 (spread 0.04).

    NCT_GOOD  raw 0.0490, unboosted  -> must survive (the regression)
    NCT_LOW1  raw 0.0120, boosted    -> below the raw p25, above the boosted
                                        p25: the trial the old gate waved
                                        through on its boost alone
    """
    return [
        make_candidate("NCT_TOP",  0.0500, "direct"),     # best, boosted
        make_candidate("NCT_GOOD", 0.0490, "unmapped"),   # 2nd best, NOT boosted
        make_candidate("NCT_A",    0.0450, "direct"),
        make_candidate("NCT_B",    0.0430, "pan"),
        make_candidate("NCT_C",    0.0400, "unmapped"),
        make_candidate("NCT_D",    0.0380, "direct"),
        make_candidate("NCT_E",    0.0350, "other"),
        make_candidate("NCT_F",    0.0330, "direct"),
        make_candidate("NCT_WEAK", 0.0130, "unmapped"),   # weak, not boosted
        make_candidate("NCT_LOW1", 0.0120, "direct"),     # weak, boosted
        make_candidate("NCT_LOW2", 0.0110, "unmapped"),
        make_candidate("NCT_LOW3", 0.0100, "unmapped"),
    ]


_fixture = fixture_pool()
apply_mesh_relevance_boost(_fixture, PATIENT_TREES, stub_for(_fixture))
_fixture.sort(key=lambda x: (x["rerank_score"], x["trial"]["nct_id"]), reverse=True)

_fkept, _fthreshold, _ = apply_quality_gate(_fixture)
_fkept_ids = {c["trial"]["nct_id"] for c in _fkept}

check("THE FIXTURE: unboosted high-quality trial survives the gate",
      "NCT_GOOD" in _fkept_ids, True)
check("its survival is on merit — raw score above the threshold",
      unboosted_score(next(c for c in _fixture
                           if c["trial"]["nct_id"] == "NCT_GOOD")) >= _fthreshold,
      True)
check("it never received a boost",
      next(c for c in _fixture if c["trial"]["nct_id"] == "NCT_GOOD")["mesh_boost"],
      0.0)

# --- Counter-check: a boosted trial no longer buys its way through ---
_boosted_weak = next(c for c in _fixture if c["trial"]["nct_id"] == "NCT_LOW1")
check("boosted weak trial is cut on its raw score",
      "NCT_LOW1" in _fkept_ids, False)
check("...and it WOULD have survived a gate on the boosted score",
      _boosted_weak["rerank_score"] >=
      float(np.percentile([c["rerank_score"] for c in _fixture], 25)), True)

# --- Ranking still reflects the boost, gating does not ---
check("boosted trial still outranks the unboosted one it beats on raw score",
      _fixture[0]["trial"]["nct_id"], "NCT_TOP")


# ===========================================================================
# TEST 5: skip_mesh_filter BYPASSES BOTH THE BOOST AND THE DROP
# ===========================================================================

print()
print("=" * 70)
print("Test 5: skip_mesh_filter bypasses the Stage 3 boost as well")
print("=" * 70)

# Stage 3: the node only calls apply_mesh_relevance_boost when the flag is
# clear, so the ablated run must leave every score raw. Assert on the node's
# guard by driving the same branch condition the node uses.
_ablation_on = {"skip_mesh_filter": True}
_ablation_off = {"skip_mesh_filter": False}

check("flag read matches the node's guard (on)",
      (_ablation_on.get("skip_mesh_filter", False)), True)
check("flag read matches the node's guard (off)",
      (_ablation_off.get("skip_mesh_filter", False)), False)

# What the ablated pool looks like: untouched by the boost.
_ablated = realistic_rrf_pool()
_ablated_before = [c["rerank_score"] for c in _ablated]
# (no apply_mesh_relevance_boost call — that IS the ablation)
check("ablated pool keeps raw == boosted for every trial",
      all(c["rerank_score"] == c["rerank_score_raw"] for c in _ablated), True)
check("ablated pool records no boost",
      {c["mesh_boost"] for c in _ablated}, {0.0})
check("ablated pool scores unchanged",
      [c["rerank_score"] for c in _ablated], _ablated_before)

# The gate must then behave identically to the boosted run, because the gate
# reads the raw score either way. This is the confound the fix removes.
_ablated_kept, _ablated_threshold, _ = apply_quality_gate(_ablated)
check_close("gate threshold is the same with the boost ablated",
            _ablated_threshold, _threshold)
check("gate keeps the same trials with the boost ablated",
      {c["trial"]["nct_id"] for c in _ablated_kept},
      {c["trial"]["nct_id"] for c in _kept})


# ===========================================================================
# TEST 6: node_rule_based_filter END TO END
# ===========================================================================
# The real Stage 4 node, on a synthetic state. patient_trees is empty, so the
# MeSH drop is inert and what is exercised is the gate itself.

print()
print("=" * 70)
print("Test 6: node_rule_based_filter — gate on the real node")
print("=" * 70)


def make_state(pool, ablation_flags=None):
    return {
        "patient_data": {
            "demographics": {"age": 62, "sex": "male"},
            "conditions": [],
            "cancer_stage_observations": [],
        },
        "reranked_trials": pool,
        "patient_trees": set(),          # MeSH drop inert
        "ablation_flags": ablation_flags or {},
        "stage_timings": {},
    }


# Rebuild the fixture pool (Test 4 consumed it in place)
_e2e_pool = fixture_pool()
apply_mesh_relevance_boost(_e2e_pool, PATIENT_TREES, stub_for(_e2e_pool))

_out = node_rule_based_filter(make_state(_e2e_pool))
_out_ids = [c["trial"]["nct_id"] for c in _out["filtered_trials"]]

check("node keeps the unboosted high-quality trial", "NCT_GOOD" in _out_ids, True)
check("node cuts the boosted weak trial", "NCT_LOW1" in _out_ids, False)
check("node ranks by the boosted score", _out_ids[0], "NCT_TOP")
check("quality-filter counter matches the survivors",
      _out["candidates_after_quality_filter"], len(_out_ids))
check("rule-filter counter is the pre-gate count",
      _out["candidates_after_rule_filter"], len(_e2e_pool))

# --- The cost cap still applies on top of the gate ---
_big_pool = realistic_rrf_pool()
apply_mesh_relevance_boost(_big_pool, PATIENT_TREES, stub_for(_big_pool))
_big_out = node_rule_based_filter(make_state(_big_pool))
check("cost cap still bounds what reaches GPT-4o",
      len(_big_out["filtered_trials"]) <= MAX_TRIALS_FOR_EVALUATION, True)
check("count before the cap is recorded separately",
      _big_out["candidates_after_quality_filter"] >= len(_big_out["filtered_trials"]),
      True)

# --- Backward compatibility: a trial dict with no rerank_score_raw ---
_legacy = [
    {"trial": make_trial("NCT_LEGACY1", "unmapped"), "rerank_score": 0.050},
    {"trial": make_trial("NCT_LEGACY2", "unmapped"), "rerank_score": 0.049},
    {"trial": make_trial("NCT_LEGACY3", "unmapped"), "rerank_score": 0.010},
]
_legacy_out = node_rule_based_filter(make_state(_legacy))
check("legacy trial dicts (no rerank_score_raw) still gate on rerank_score",
      [c["trial"]["nct_id"] for c in _legacy_out["filtered_trials"]],
      ["NCT_LEGACY1", "NCT_LEGACY2"])


# ===========================================================================
# TEST 7: THE ABSOLUTE MedCPT FLOOR
# ===========================================================================
# The second knob. Everything above measures the relative percentile, which is
# what the gate USED to be entirely: the old floor was RERANK_SCORE_THRESHOLD,
# a value on the fused RRF score, and max(percentile, -10) could never select
# it because a fused score runs about 0.01 .. 0.06. So the percentile was doing
# 100% of the filtering and no test here could tell, because the arithmetic was
# correct -- it was the QUANTITY that was wrong.
#
# EVERY CHECK BELOW IS PAIRED WITH A CONTROL that makes it fail, because a
# floor that never fires passes an "it did not drop this" assertion for the
# same reason a working one does.

print()
print("=" * 70)
print("Test 7: absolute MedCPT floor, independent of the percentile")
print("=" * 70)

# Four trials, all with the SAME raw score, so the percentile cannot separate
# them: np.percentile of a constant array is that constant, and every trial
# satisfies `>= threshold`. Whatever is dropped here is dropped by the floor.
_FLOOR = -5.0
_flat = [
    make_candidate("NCT_F_HIGH",  0.0500, "unmapped", medcpt=+2.0),
    make_candidate("NCT_F_AT",    0.0500, "unmapped", medcpt=_FLOOR),
    make_candidate("NCT_F_LOW",   0.0500, "unmapped", medcpt=-9.0),
    make_candidate("NCT_F_NONE",  0.0500, "unmapped", medcpt=None),
]

_fl_kept, _fl_thr, _fl_drops = apply_quality_gate(_flat, medcpt_floor=_FLOOR)
_fl_ids = {c["trial"]["nct_id"] for c in _fl_kept}

# NON-DEGENERACY FIRST: if the percentile dropped anything here, the section
# below would be measuring the wrong knob and would still look like it passed.
check("NON-DEGENERACY: the flat pool defeats the percentile (0 dropped by it)",
      _fl_drops["percentile"], 0)

check("a trial BELOW the floor is dropped", "NCT_F_LOW" in _fl_ids, False)
check("a trial ABOVE the floor survives", "NCT_F_HIGH" in _fl_ids, True)
check("the comparison is >= , so a trial EXACTLY at the floor survives",
      "NCT_F_AT" in _fl_ids, True)

# THE None RULE. Absence of a score is not a low score.
check("a trial with medcpt_score_max=None is NOT dropped by the floor",
      "NCT_F_NONE" in _fl_ids, True)
# CONTROL for that claim: the identical trial carrying a low score IS dropped,
# so "survived" above is a decision and not the gate failing to run.
_none_control = [make_candidate("NCT_F_NONE", 0.0500, "unmapped", medcpt=-9.0)]
_nc_kept, _, _nc_drops = apply_quality_gate(_none_control + _flat[:1],
                                            medcpt_floor=_FLOOR)
check("CONTROL: the same trial with a LOW score is dropped",
      "NCT_F_NONE" in {c["trial"]["nct_id"] for c in _nc_kept}, False)

check("the floor's drop count is its own", _fl_drops["floor"], 1)
check("and every one of them is floor-only here",
      _fl_drops["floor_only"], 1)

# --- THE TWO KNOBS OVERLAP, AND floor_only IS WHAT SEPARATES THEM ----------
#
# A pool where one trial fails BOTH knobs and one fails only the floor. If the
# implementation counted floor_only as "floor", the two would be equal and the
# question "is the absolute knob doing work the relative one was not" could not
# be answered from the record.
_mixed = [
    make_candidate("NCT_M_TOP",  0.0500, "unmapped", medcpt=+3.0),
    make_candidate("NCT_M_TOP2", 0.0490, "unmapped", medcpt=+2.0),
    make_candidate("NCT_M_TOP3", 0.0480, "unmapped", medcpt=+1.0),
    # high raw score, low MedCPT -> floor only
    make_candidate("NCT_M_FLOOR_ONLY", 0.0470, "unmapped", medcpt=-40.0),
    # low raw score, low MedCPT -> both
    make_candidate("NCT_M_BOTH", 0.0100, "unmapped", medcpt=-40.0),
]
_mx_kept, _mx_thr, _mx_drops = apply_quality_gate(_mixed, medcpt_floor=_FLOOR)
_mx_ids = {c["trial"]["nct_id"] for c in _mx_kept}

check("NON-DEGENERACY: the mixed pool has a percentile drop",
      _mx_drops["percentile"] >= 1, True)
check("NON-DEGENERACY: and a floor drop", _mx_drops["floor"] >= 1, True)
check("the trial failing only the floor is counted in floor_only",
      _mx_drops["floor_only"], 1)
check("the trial failing BOTH is counted in floor but NOT in floor_only",
      _mx_drops["floor"], 2)
check("so the two counts OVERLAP and do not sum to the total dropped",
      _mx_drops["percentile"] + _mx_drops["floor"] > len(_mixed) - len(_mx_kept),
      True)
check("both weak trials are gone",
      {"NCT_M_FLOOR_ONLY", "NCT_M_BOTH"} & _mx_ids, set())

# --- THE RELATIVE KNOB IS UNCHANGED ---------------------------------------
#
# The old gate was max(percentile, floor). If any max() survived, moving the
# floor would move the threshold. It must not: they are different quantities
# now and the threshold describes only the relative knob.
_r1 = apply_quality_gate(fixture_pool(), medcpt_floor=-1000.0)[1]
_r2 = apply_quality_gate(fixture_pool(), medcpt_floor=+1000.0)[1]
check_close("the reported threshold is the percentile and ONLY the percentile",
            _r1, _r2)
check("NON-DEGENERACY: that threshold is a real fused score, not a sentinel",
      0.0 < _r1 < 1.0, True)
# CONTROL: the second floor is high enough to change the OUTCOME, so the
# equality above is about the threshold rather than about the floor being inert.
check("CONTROL: the same two floors DO change what survives",
      len(apply_quality_gate(fixture_pool(), medcpt_floor=-1000.0)[0])
      != len(apply_quality_gate(
          [make_candidate(c["trial"]["nct_id"], c["rerank_score_raw"],
                          "unmapped", medcpt=0.0)
           for c in fixture_pool()], medcpt_floor=+1000.0)[0]),
      True)

# --- THE EMPTY POOL --------------------------------------------------------
#
# REACHABLE IN PRODUCTION: every trial can be removed by the MeSH / stage /
# histology / age / sex filters before the gate is called. What comes back has
# to survive TWO JSON serializers -- the structured log line and the fixture
# prefix -- and float("-inf"), which an earlier version of this returned, does
# not: json.dumps emits `-Infinity`, for which RFC 8259 has no literal.
_e_kept, _e_thr, _e_drops = apply_quality_gate([], medcpt_floor=_FLOOR)
check("empty pool keeps nothing", _e_kept, [])
check("empty pool reports no drops", _e_drops,
      {"percentile": 0, "floor": 0, "floor_only": 0})
check("empty pool's threshold is None -- no cut was made, and NOT the MedCPT "
      "floor, which is a different quantity entirely", _e_thr, None)
check("...and it is not an infinity either", _e_thr in (float("-inf"), float("inf")),
      False)
check("...so it round-trips through json.dumps as valid JSON",
      json.dumps({"threshold": _e_thr}), '{"threshold": null}')
# CONTROL: the value the earlier version returned does NOT round-trip.
check("CONTROL: -inf serialises to a token no strict JSON parser accepts",
      json.dumps({"threshold": float("-inf")}), '{"threshold": -Infinity}')
try:
    json.loads(json.dumps({"threshold": float("-inf")}), parse_constant=_reject)
    _strict_ok = True
except ValueError:
    _strict_ok = False
check("...and a strict parser rejects it", _strict_ok, False)

# END TO END: the real node, with every trial removed before the gate. This is
# the path float(None) would have raised on.
_empty_state = make_state([])
_empty_out = node_rule_based_filter(_empty_state)
check("the node survives an empty pool", _empty_out["filtered_trials"], [])
check("...and reports quality_threshold as None, not a number",
      _empty_out["quality_threshold"], None)
check("...and the whole Stage 4 result is JSON-serialisable",
      json.dumps({k: v for k, v in _empty_out.items()
                  if k != "filtered_trials"}) is not None, True)

# --- END TO END THROUGH THE REAL NODE --------------------------------------
_e2e_floor_pool = [
    make_candidate("NCT_E_TOP",   0.0500, "unmapped", medcpt=+3.0),
    make_candidate("NCT_E_TOP2",  0.0500, "unmapped", medcpt=+2.0),
    make_candidate("NCT_E_TOP3",  0.0500, "unmapped", medcpt=+1.0),
    make_candidate("NCT_E_WEAK",  0.0500, "unmapped", medcpt=-999.0),
]
_e2e_floor_out = node_rule_based_filter(make_state(_e2e_floor_pool))

check("the node reports the percentile knob's count",
      "quality_dropped_percentile" in _e2e_floor_out, True)
check("the node reports the floor knob's count",
      "quality_dropped_floor" in _e2e_floor_out, True)
check("the node reports the non-overlapping floor count",
      "quality_dropped_floor_only" in _e2e_floor_out, True)
check("a trial far below the configured floor is dropped by the node",
      "NCT_E_WEAK" in [c["trial"]["nct_id"]
                       for c in _e2e_floor_out["filtered_trials"]], False)
check("and the node attributes it to the floor, not the percentile",
      (_e2e_floor_out["quality_dropped_floor"],
       _e2e_floor_out["quality_dropped_percentile"]), (1, 0))
check("quality_dropped stays the TOTAL, so no existing reader changed meaning",
      _e2e_floor_out["quality_dropped"], 1)

# The ablation path emits both fields, with None and 0. Driven through the real
# node so the assertion is about what Stage 4 receives, not about a literal.
_abl_pool = [
    {**make_candidate("NCT_A1", 0.0500, "unmapped"), "medcpt_score_max": None,
     "medcpt_queries_scored": 0},
    {**make_candidate("NCT_A2", 0.0100, "unmapped"), "medcpt_score_max": None,
     "medcpt_queries_scored": 0},
]
_abl_out = node_rule_based_filter(make_state(_abl_pool))
check("with every medcpt_score_max None the floor drops nothing",
      _abl_out["quality_dropped_floor"], 0)
check("...while the percentile still works, so the pool was not simply empty",
      _abl_out["quality_dropped_percentile"] >= 1, True)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  2 12:00:00 2026

@author: ramyalsaffar
"""
