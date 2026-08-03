# Ablation State Passthrough Test
#################################

"""
Ablation State Passthrough Test

Audits every ablation flag in 13- LangGraph Agent.py for the defect class
where a flag's early-return path omits a state key a later node reads, so the
ablation row silently measures more than one stage removal.

The defect under test: node_cross_encoder_rerank resolved the patient's MeSH
trees inside its reranking body, below the skip_cross_encoder guard. The guard
returned early, state["patient_trees"] was never written, and Stage 4's cancer
site filter read an empty set and dropped nothing. The no_cross_encoder row
therefore measured cross-encoder removal AND MeSH-filter removal together.

Covers:
    1. STRUCTURAL — every `return {...}` in a node function declares the same
       key set, so no ablation path can silently drop state. This is the
       regression guard: it fails for any future flag, not just this one.
    2. skip_cross_encoder — the early return carries the resolved patient_trees
    3. skip_cross_encoder end to end — Stage 4's MeSH filter still drops the
       unrelated trial, which is the confound the fix removes
    4. skip_mesh_filter — still deliberately skips resolution (trees empty),
       and Stage 4 drops nothing on MeSH. Items 6 and 7a must not be undone.
    5. skip_stage_filter / skip_histology_filter — no early return anywhere;
       both are recomputed locally inside Stage 4 and read no Stage 3 state
    6. retrieval_mode — node_hybrid_retrieval has one return, so no path forks

No network and no LLM: _MESH_FILTER and _CANCER_REGISTRY are replaced with
stubs, and every flag exercised here takes a branch that never reaches MedCPT
or Qdrant. The cross-encoder is loaded by file 13 at import but never run.

Run from terminal (or F5 in Spyder):
    python "35- Ablation State Passthrough Test.py"

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# 13 chains 03, 08, 09, 10 itself — do not list them again here.
exec_chain(
    ["13- LangGraph Agent.py"],
    caller_file=_code_dir + "35- Ablation State Passthrough Test.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 13 (→ 03, 08, 09, 10)",
)


#------------------------------------------------------------------------------


import ast
import textwrap


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
        _FAILURES.append(
            f"{label}\n          expected: {expected}\n          actual:   {actual}"
        )
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


# ===========================================================================
# FIXTURES
# ===========================================================================

# Patient with lung cancer: MeSH C04.588.894.797.520 (Lung Neoplasms)
PATIENT_TREES = {"C04.588.894.797.520"}

TRIAL_TREES = {
    "NCT_LUNG":   {"C04.588.894.797.520.109"},   # descendant — relevant
    "NCT_BREAST": {"C04.588.180"},               # mapped, unrelated — dropped
}


class StubMeshFilter:
    """Stands in for MeSHCancerSiteFilter.

    Implements the three methods the pipeline calls on the paths under test:
    resolve_patient_trees() (Stage 3), trial_mesh_trees() and _is_pan_cancer()
    (the boost), and is_cancer_relevant() (Stage 4). Real MeSH resolution needs
    the UMLS crosswalk; what is under test is state passthrough, not ontology.
    """

    def __init__(self):
        self.resolve_calls = 0

    def resolve_patient_trees(self, conditions, cancer_registry):
        self.resolve_calls += 1
        return {
            "trees":               set(PATIENT_TREES),
            "resolution":          "snomed_cui_mesh",
            "conditions_total":    len(conditions),
            "conditions_resolved": len(conditions),
            "conditions_pan_only": 0,
            "conditions_unmapped": 0,
            "pan_only_layers":     [],
        }

    def trial_mesh_trees(self, trial):
        return set(TRIAL_TREES.get(trial["nct_id"], set()))

    def _is_pan_cancer(self, trial_trees):
        return any(t.count(".") <= 1 for t in trial_trees)

    def is_cancer_relevant(self, patient_trees, trial):
        """Conservative by design: unmappable trial ⇒ KEEP."""
        trial_trees = self.trial_mesh_trees(trial)
        if not trial_trees:
            return True
        for tt in trial_trees:
            for pt in patient_trees:
                if tt.startswith(pt) or pt.startswith(tt):
                    return True
        return False


class StubCancerRegistry:
    """Only the attribute resolve_patient_mesh() reads."""
    exclude_verification = {"refuted", "entered-in-error"}


def make_trial(nct_id: str) -> dict:
    """Minimal trial dict shaped like the payload Stage 3 carries."""
    return {
        "nct_id": nct_id,
        "title": f"trial {nct_id}",
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


def make_hybrid_candidate(nct_id: str, fusion_score: float) -> dict:
    """Trial object as Stage 2 emits it, which is what Stage 3 consumes."""
    return {"trial": make_trial(nct_id), "fusion_score": fusion_score}


PATIENT_DATA = {
    "patient_id": "test-patient",
    "demographics": {"age": 62, "sex": "male"},
    "conditions": [
        {
            "code": "254637007",
            "display": "Non-small cell lung cancer",
            "verification_status": "confirmed",
        }
    ],
    "medications": [],
    "observations": [],
    "cancer_stage_observations": [],
}


def make_stage3_state(ablation_flags=None) -> dict:
    """Input state for node_cross_encoder_rerank."""
    return {
        "patient_data":    PATIENT_DATA,
        "expanded_query":  "lung neoplasms",
        "rerank_queries":  ["lung neoplasms"],
        "hybrid_results":  [
            make_hybrid_candidate("NCT_LUNG",   0.050),
            make_hybrid_candidate("NCT_BREAST", 0.040),
        ],
        "stage_timings":   {},
        "ablation_flags":  ablation_flags or {},
    }


def make_stage4_state(stage3_out: dict, ablation_flags=None) -> dict:
    """Stage 4 input built the way LangGraph builds it: prior state merged
    with whatever Stage 3 returned. Keys Stage 3 omits simply stay absent."""
    state = {
        "patient_data":   PATIENT_DATA,
        "stage_timings":  {},
        "ablation_flags": ablation_flags or {},
        "mesh_resolution": "snomed_cui_mesh",
    }
    state.update(stage3_out)
    return state


# Install the stubs into file 13's globals for the whole run.
_MESH_FILTER = StubMeshFilter()
_CANCER_REGISTRY = StubCancerRegistry()


print("\n" + "=" * 70)
print("ABLATION STATE PASSTHROUGH TEST")
print("=" * 70)


# ===========================================================================
# TEST 1: STRUCTURAL — every return in a node declares the same key set
# ===========================================================================
# This is the regression guard the audit asks for. It does not know about
# patient_trees specifically: it asserts that no node can return one key set
# on its normal path and a smaller one on an ablation/guard path. Any future
# skip_ flag that early-returns without carrying state fails here.

print("\n" + "=" * 70)
print("Test 1: no node returns a smaller key set on any path")
print("=" * 70)

with open(_code_dir + "13- LangGraph Agent.py") as _fh:
    _tree = ast.parse(_fh.read())

_NODE_NAMES = [
    "node_query_expansion",
    "node_hybrid_retrieval",
    "node_cross_encoder_rerank",
    "node_rule_based_filter",
]

_returns_by_node = {}
for _fn in ast.walk(_tree):
    if isinstance(_fn, ast.FunctionDef) and _fn.name in _NODE_NAMES:
        key_sets = []
        for _node in ast.walk(_fn):
            if isinstance(_node, ast.Return) and isinstance(_node.value, ast.Dict):
                keys = frozenset(
                    k.value for k in _node.value.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)
                )
                key_sets.append((_node.lineno, keys))
        _returns_by_node[_fn.name] = key_sets

for _name in _NODE_NAMES:
    _key_sets = _returns_by_node.get(_name, [])
    check(f"{_name}: found its return statements", len(_key_sets) >= 1, True)
    if not _key_sets:
        continue
    _union = frozenset().union(*[k for _, k in _key_sets])
    _short = [(ln, sorted(_union - k)) for ln, k in _key_sets if k != _union]
    if _short:
        detail = "; ".join(f"line {ln} omits {missing}" for ln, missing in _short)
    else:
        detail = "none"
    check(f"{_name}: no return omits a key another return declares", detail, "none")


# ===========================================================================
# TEST 2: skip_cross_encoder carries the resolved patient_trees
# ===========================================================================

print("\n" + "=" * 70)
print("Test 2: skip_cross_encoder early return carries patient_trees")
print("=" * 70)

_MESH_FILTER.resolve_calls = 0
out_skip_ce = node_cross_encoder_rerank(
    make_stage3_state({"skip_cross_encoder": True})
)

check("skip_cross_encoder resolves the patient's MeSH identity",
      _MESH_FILTER.resolve_calls, 1)
check("early return declares patient_trees",
      "patient_trees" in out_skip_ce, True)
check("early return carries the resolved trees, not an empty set",
      out_skip_ce.get("patient_trees"), PATIENT_TREES)
check("early return still bypasses reranking",
      out_skip_ce["stage_timings"]["cross_encoder"], 0.0)
check("passthrough pool is the hybrid pool, fusion-sorted",
      [t["trial"]["nct_id"] for t in out_skip_ce["reranked_trials"]],
      ["NCT_LUNG", "NCT_BREAST"])
check("passthrough applies no MeSH boost",
      {t["mesh_boost"] for t in out_skip_ce["reranked_trials"]}, {0.0})


# ===========================================================================
# TEST 3: THE CONFOUND — Stage 4's MeSH filter survives skip_cross_encoder
# ===========================================================================
# Before the fix, patient_trees was empty here and the `if patient_trees`
# guard in Stage 4 skipped the cancer site filter entirely, so the unrelated
# breast trial survived and mesh_dropped was 0.

print("\n" + "=" * 70)
print("Test 3: skip_cross_encoder end to end — MeSH filter still fires")
print("=" * 70)

flags_ce = {"skip_cross_encoder": True}
s4_out = node_rule_based_filter(make_stage4_state(out_skip_ce, flags_ce))

check("Stage 4 drops the unrelated trial on MeSH", s4_out["mesh_dropped"], 1)
# candidates_after_rule_filter is the pre-gate count. Asserting on
# filtered_trials instead would measure the dynamic quality gate, which cuts
# the 25th percentile of any pool and has nothing to do with MeSH.
check("Stage 4 rule pass keeps only the relevant trial",
      s4_out["candidates_after_rule_filter"], 1)

# Same pool, but with patient_trees stripped — the pre-fix behavior. Kept as
# the counter-example, so a regression cannot pass Test 3 by accident.
_pre_fix = dict(out_skip_ce)
_pre_fix.pop("patient_trees")
s4_pre_fix = node_rule_based_filter(make_stage4_state(_pre_fix, flags_ce))
check("counter-example: without patient_trees the MeSH filter is blind",
      s4_pre_fix["mesh_dropped"], 0)
check("counter-example: the unrelated trial survives the rule pass",
      s4_pre_fix["candidates_after_rule_filter"], 2)


# ===========================================================================
# TEST 4: skip_mesh_filter still skips resolution (items 6 / 7a preserved)
# ===========================================================================

print("\n" + "=" * 70)
print("Test 4: skip_mesh_filter still bypasses resolution")
print("=" * 70)

_MESH_FILTER.resolve_calls = 0
out_skip_mesh = node_cross_encoder_rerank(
    make_stage3_state({"skip_mesh_filter": True, "skip_cross_encoder": True})
)
check("skip_mesh_filter does NOT resolve MeSH", _MESH_FILTER.resolve_calls, 0)
check("skip_mesh_filter declares patient_trees anyway",
      "patient_trees" in out_skip_mesh, True)
check("skip_mesh_filter leaves the trees empty",
      out_skip_mesh["patient_trees"], set())

flags_both = {"skip_mesh_filter": True, "skip_cross_encoder": True}
s4_mesh = node_rule_based_filter(make_stage4_state(out_skip_mesh, flags_both))
check("Stage 4 drops nothing on MeSH under skip_mesh_filter",
      s4_mesh["mesh_dropped"], 0)
check("Stage 4 rule pass keeps both trials under skip_mesh_filter",
      s4_mesh["candidates_after_rule_filter"], 2)

# skip_mesh_filter alone (cross-encoder active) must also skip resolution.
_MESH_FILTER.resolve_calls = 0
_ = node_cross_encoder_rerank(make_stage3_state({"skip_mesh_filter": True}))
check("skip_mesh_filter skips resolution on the reranking path too",
      _MESH_FILTER.resolve_calls, 0)


# ===========================================================================
# TEST 5: skip_stage_filter and skip_histology_filter read no Stage 3 state
# ===========================================================================
# Both are computed locally inside Stage 4 (extract_patient_stage and
# extract_patient_histology, from patient_data). Neither node writes a state
# key for them, so neither flag can drop state a later node reads. Assert
# that directly: the two filters behave identically whether or not Stage 3
# wrote patient_trees.

print("\n" + "=" * 70)
print("Test 5: stage / histology filters are independent of Stage 3 state")
print("=" * 70)

for _flag in ("skip_stage_filter", "skip_histology_filter"):
    _flags = {_flag: True}
    _with_trees = node_rule_based_filter(
        make_stage4_state({"reranked_trials": out_skip_ce["reranked_trials"],
                           "patient_trees": set()}, _flags)
    )
    check(f"{_flag}: Stage 4 still returns its counters",
          {"stage_dropped", "histology_dropped", "mesh_dropped"}
          <= set(_with_trees), True)
    check(f"{_flag}: no Stage 3 key is consumed for it",
          _with_trees["stage_dropped"] == 0 and _with_trees["histology_dropped"] == 0,
          True)

# Neither flag appears anywhere outside node_rule_based_filter.
_src_by_fn = {}
for _fn in ast.walk(_tree):
    if isinstance(_fn, ast.FunctionDef):
        _src_by_fn.setdefault(_fn.name, []).append(ast.dump(_fn))

for _flag in ("skip_stage_filter", "skip_histology_filter"):
    _readers = sorted(
        name for name, dumps in _src_by_fn.items()
        if any(repr(_flag)[1:-1] in d for d in dumps)
    )
    check(f"{_flag} is read only by node_rule_based_filter",
          _readers, ["node_rule_based_filter"])


# ===========================================================================
# TEST 6: retrieval_mode has a single return path
# ===========================================================================

print("\n" + "=" * 70)
print("Test 6: retrieval_mode cannot fork state")
print("=" * 70)

_retrieval_returns = _returns_by_node["node_hybrid_retrieval"]
check("node_hybrid_retrieval has exactly one dict return",
      len(_retrieval_returns), 1)

_mode_readers = sorted(
    name for name, dumps in _src_by_fn.items()
    if any("retrieval_mode" in d for d in dumps)
)
check("retrieval_mode is read only by node_hybrid_retrieval",
      _mode_readers, ["node_hybrid_retrieval"])


# ===========================================================================
# SUMMARY
# ===========================================================================

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(textwrap.indent(f"  - {_f}", ""))

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  2 2026

@author: ramyalsaffar
"""
