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

NO NETWORK, NO LLM AND NO MODEL LOAD. The MeSH filter, the cancer registry and
the MedCPT cross-encoder scorer are all replaced with stubs through
oncotriage/agent/deps.py, and no flag exercised here reaches Qdrant or an
OpenAI endpoint. Test 4b measures the model half rather than asserting it:
neither MEDCPT key resolves, and neither torch nor transformers enters
sys.modules.

THE THIRD SEAM IS RECENT AND THE TWO PARAGRAPHS IT REPLACES SAID THE OPPOSITE,
which is worth keeping as the record of what changed. Until 2026-09-08 this
file DID load and run the real cross-encoder: measured by inspecting
deps.cached_keys() after a full run, `medcpt_tokenizer` and `medcpt_model` were
both built, because Test 4's last drive runs node_cross_encoder_rerank with the
cross-encoder active and that reaches models.score_pairs ->
medcpt_score_pairs -> deps.get_medcpt_tokenizer(). The old note called that
"local work against the HuggingFace cache -- no network once the cache is warm",
and BOTH HALVES WERE WRONG ON A CI RUNNER. Measured 2026-09-08, warm cache,
every outbound call trapped: FOUR attempts to huggingface.co:443, swallowed by
huggingface_hub, which fell back to the cache -- so the file passed while
emitting them. A runner's cache is COLD, so those four are the ~837 MB
checkpoint fetch, and this file was the single largest download in bucket A.

Nothing measured was lost, and that is checkable rather than asserted: no
assertion in this file reads a MedCPT score, a rerank order or a rerank_score.
The subject is which state keys a node's return declares, and a cross-encoder
returning real floats and one returning fabricated floats declare the same keys.

The FastEmbed BM25 model is NOT built either (oncotriage.embedding._MODEL stays
None, checked in 4b). The `fastembed` LIBRARY does arrive in sys.modules
regardless, because qdrant_client.fastembed_common imports it; that is a
library import, not a model construction, and it was equally true under the old
exec chain.

Run from terminal (or F5 in Spyder):
    python tests/test_agent_ablation_flag_passthrough.py
    (was: python "35- Ablation State Passthrough Test.py")

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# PASS 20d-1: THIS FILE IMPORTS THE PACKAGE. It used to exec "01- Imports.py"
# and "02- Utility Functions.py" into its own globals and then exec_chain()
# "13- LangGraph Agent.py", which is how `deps` and the two node functions used
# to arrive. Item 20c split File 13 into oncotriage/agent/, so each name comes
# from the module that defines it.
#
# THE CANDIDATE DIRECTORY IS THE PARENT OF THIS FILE'S, not this file's own.
# The same block Files 47, 48 and 49 carry looks one level up because this file
# now sits in tests/ and the package sits BESIDE tests/, not inside it.
# `pip install -e .` makes the whole block a no-op.
import os
import sys

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

from oncotriage import embedding as _embedding
from oncotriage.agent import deps, filtering, models, retrieval
from oncotriage.agent.filtering import node_rule_based_filter
from oncotriage.agent.retrieval import node_cross_encoder_rerank


#------------------------------------------------------------------------------


import ast
import textwrap

# numpy is already in sys.modules by this line -- the oncotriage import chain
# above pulls it in -- so this costs no new dependency. It is needed because
# StubCrossEncoderScorer must return what models.score_pairs' contract says:
# a 1-D float ARRAY, which node_cross_encoder_rerank calls .min()/.max()/
# .mean() and np.argsort on.
import numpy as np


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def last(seq, what: str):
    """``seq[-1]``, or a NAMED ABSENCE when it is empty.

    A bare ``seq[-1]`` raises IndexError while ``check()``'s argument is being
    evaluated, and it does so in EXACTLY the state the check exists to catch --
    so the run reports one traceback where it owes a summary and every result
    below it. Measured on 2026-09-08: reverting the MEDCPT_SCORER override made
    _SCORER.queries empty and took the file down at Test 4b. This project has
    shipped that shape seventeen times; it is a recorded failure here instead.
    """
    return seq[-1] if seq else f"<{what}: nothing recorded>"


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


class StubCrossEncoderScorer:
    """Stands in for the MedCPT cross-encoder, through deps.MEDCPT_SCORER.

    WHY THIS SEAM EXISTS AT ALL. Test 4's last drive runs
    node_cross_encoder_rerank with the cross-encoder ACTIVE (its only flag is
    skip_mesh_filter), so the node reaches models.score_pairs ->
    medcpt_score_pairs -> deps.get_medcpt_tokenizer(), and the real
    ``ncbi/MedCPT-Cross-Encoder`` checkpoint is resolved. Measured on a cold
    HuggingFace cache that is ~837 MB fetched over the network; measured on a
    WARM cache, with every outbound call trapped, it is still FOUR attempts to
    huggingface.co:443 -- huggingface_hub swallows the failure and falls back to
    the cache, so the file passed while emitting them. On a CI runner the cache
    is cold and the network is the only source, which makes this file the single
    largest download in bucket A.

    NOTHING MEASURED IS LOST, AND THAT IS THE ARGUMENT RATHER THAN A HOPE. The
    check behind that drive reads _MESH_FILTER.resolve_calls and nothing else;
    no assertion anywhere in this file reads a MedCPT score, a rerank order or a
    rerank_score. The subject of the file is ABLATION STATE PASSTHROUGH -- which
    keys a node's return declares -- and a cross-encoder that returns real
    floats and one that returns fabricated floats declare the same keys.

    THE CONTRACT IS models.score_pairs': the same two arguments, and a 1-D float
    array with one score per trial text IN INPUT ORDER. It is a numpy array
    rather than a list because node_cross_encoder_rerank calls .min(), .max(),
    .mean() and np.argsort on it.

    The scores DESCEND with input position and are never tied. Ties would leave
    the ranking entirely to np.argsort's stable tiebreak, which is a weaker
    exercise of the node than a definite order; descending keeps the reranked
    pool in the hybrid pool's order, which is the least surprising thing for a
    reader comparing this drive against the skip_cross_encoder ones.
    """

    def __init__(self):
        self.calls = 0
        self.queries = []

    def __call__(self, query, trial_texts):
        self.calls += 1
        self.queries.append(query)
        return np.array([1.0 - 0.1 * i for i in range(len(trial_texts))],
                        dtype=float)


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


# Install the stubs THROUGH THE DEPENDENCY SEAM for the whole run.
#
# WHAT CHANGED IN PASS 20c-2c, AND WHY IT HAD TO. These two lines used to be
#
#     _MESH_FILTER = StubMeshFilter()
#     _CANCER_REGISTRY = StubCancerRegistry()
#
# which worked because every project file was exec'd into THIS namespace, so
# File 13's node functions resolved both names out of this dict at call time.
# File 13 is a shim over oncotriage/agent/ now: its functions resolve their
# globals in their own modules, and a rebinding here reaches none of them. The
# nodes would have run against the REAL MeSH filter and the REAL cancer
# registry -- which load 703 descriptors and the whole ICD-10-CM release -- and
# every assertion below about resolve_calls would have failed on a stub nobody
# was calling.
#
# deps.set_override is the seam. The agent asks deps for both, so the stub is
# what it gets.
_MESH_FILTER = StubMeshFilter()
_CANCER_REGISTRY = StubCancerRegistry()
_SCORER = StubCrossEncoderScorer()

deps.set_override(deps.MESH_FILTER, _MESH_FILTER)
deps.set_override(deps.CANCER_REGISTRY, _CANCER_REGISTRY)
deps.set_override(deps.MEDCPT_SCORER, _SCORER)

# THE SCORER OVERRIDE IS FILE-SCOPE RATHER THAN WRAPPED AROUND THE ONE DRIVE
# THAT NEEDS IT, and that is a decision. Exactly one drive in this file runs
# with the cross-encoder active today (Test 4's last one), so a narrowly scoped
# override would be sufficient TODAY -- and it would make "this file loads no
# model" a property of one call site rather than of the file. A flag dropped
# from either of the other two drives, or a drive added by a later pass,
# silently reinstates the download and nothing here would fail. File scope
# makes the property structural, and it is what lets this file's bucket
# rationale in .github/scripts/ci_test_buckets.py state it unconditionally.
#
# It costs nothing: no assertion in this file reads a score, a rerank order or
# a rerank_score, and section 4b below proves the model is never built rather
# than asserting it.


# --- THE OVERRIDE IS SHOWN TO BE THE OBJECT THE AGENT ACTUALLY REACHES ------
# CLAUDE.md: an assertion that has only ever passed is not evidence. Asserting
# that set_override returned without raising proves nothing at all -- the
# failure this replaces was a rebinding that also did not raise. So what is
# asserted is IDENTITY: the object deps hands the agent must BE these stubs.
#
# The negative control is directly below it and is what makes the identity
# check discriminating: with the override cleared, deps hands back something
# else (the real filter, or None if the MeSH files are absent), and the same
# comparison is False.
print("\n" + "=" * 70)
print("Test 0: the stubs are what the agent reaches, and that is checkable")
print("=" * 70)
check("deps hands the agent THIS stub MeSH filter",
      deps.get_mesh_filter() is _MESH_FILTER, True)
check("deps hands the agent THIS stub cancer registry",
      deps.get_cancer_registry() is _CANCER_REGISTRY, True)

_saved_mesh = deps.clear_override(deps.MESH_FILTER)
check("...and with the override REMOVED it is something else, so the check "
      "above can fail (negative control)",
      deps.get_mesh_filter() is _MESH_FILTER, False)
deps.set_override(deps.MESH_FILTER, _saved_mesh)
check("...and reinstalling it restores the stub",
      deps.get_mesh_filter() is _MESH_FILTER, True)

# THE SCORER SEAM IS PROVED THE SAME WAY, AND IT NEEDS A DIFFERENT INSTRUMENT.
# MEDCPT_SCORER has no accessor in deps: its default lives in
# oncotriage.agent.models, because deps must not import models (models imports
# deps, and the reverse edge is a cycle). So there is no get_medcpt_scorer() to
# compare against, and what is asserted instead is the DISPATCH -- that
# models.score_pairs, which is what every caller inside the agent uses, returns
# THIS stub's answer. That is strictly stronger than an identity check on the
# override slot, because it is the path the node actually takes.
#
# The negative control is the override slot going back to UNSET, which is what
# models.score_pairs tests before falling through to the real medcpt_score_pairs.
# It is deliberately NOT "clear the override and call score_pairs again": that
# call would load the 837 MB checkpoint, which is the thing this seam exists to
# avoid, so the control would defeat its own subject.
_probe_before = _SCORER.calls
try:
    # GUARDED, AND NOT FOR TIDINESS. With the override missing this call falls
    # through to the real medcpt_score_pairs, which on a CI runner's COLD cache
    # cannot reach huggingface.co and RAISES -- inside a check() argument list,
    # which would take the file down with a traceback in exactly the state
    # these three checks exist to catch. It is a recorded failure instead.
    _probe_scores = models.score_pairs("probe", ["a", "b", "c"])
except Exception as _exc:                                  # noqa: BLE001
    _probe_scores = f"<score_pairs raised {type(_exc).__name__}: {_exc}>"

check("models.score_pairs dispatches to THIS stub scorer",
      _SCORER.calls, _probe_before + 1)
check("...and returns what it produced, in input order",
      list(_probe_scores) if hasattr(_probe_scores, "__iter__")
      and not isinstance(_probe_scores, str) else _probe_scores,
      [1.0, 0.9, 0.8])
# THE SHAPE IS PART OF THE CONTRACT, AND CHECKING IT HERE IS WHERE IT IS CHEAP.
# models.score_pairs' docstring says "a 1-D float array", and
# node_cross_encoder_rerank calls .min()/.max()/.mean() and np.argsort on the
# result -- so a stub returning a plain list raises INSIDE the node, thirty
# frames down, at a bare drive that is not inside a check(). Asserting the shape
# here turns that into a named failure before any node is driven. Measured: with
# the stub returning a list, this is the check that names it.
check("...and it is the ARRAY shape node_cross_encoder_rerank consumes, not a "
      "list", isinstance(_probe_scores, np.ndarray), True)

_saved_scorer = deps.clear_override(deps.MEDCPT_SCORER)
check("...and with the override REMOVED the slot models.score_pairs reads is "
      "UNSET, so the dispatch above can fall through (negative control)",
      deps.get_override(deps.MEDCPT_SCORER) is deps.UNSET, True)
deps.set_override(deps.MEDCPT_SCORER, _saved_scorer)
check("...and reinstalling it restores the stub",
      deps.get_override(deps.MEDCPT_SCORER) is _SCORER, True)


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

# RETARGETED IN PASS 20c-2c. This used to parse "13- LangGraph Agent.py", which
# is now a re-export shim carrying no node definitions at all. The walk below
# would have found none of the four functions, built an EMPTY _returns_by_node,
# and reported PASS on every check -- a structural guard that had gone
# permanently green while inspecting a file of import statements. The
# non-degeneracy block after the walk is what makes that state impossible now.
#
# The four nodes live in two modules, so both are parsed and their trees merged.
#
# PASS 20d-1: EACH PATH COMES FROM THE MODULE'S OWN __file__, not from a
# directory guess. It used to be os.path.join(_code_dir, "oncotriage", "agent",
# ...), which was correct only while this file sat in the code directory; moving
# it into tests/ would have made both paths one level off and every open() below
# raise. Asking the imported module where it lives cannot go stale that way, and
# it is also the same object the checks are about -- a path built by hand could
# name a copy of the agent that this process never imported.
_AGENT_NODE_SOURCES = (
    os.path.abspath(retrieval.__file__),
    os.path.abspath(filtering.__file__),
)

_tree = ast.Module(body=[], type_ignores=[])
for _agent_src in _AGENT_NODE_SOURCES:
    with open(_agent_src, encoding="utf-8") as _fh:
        _tree.body.extend(ast.parse(_fh.read()).body)

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

# NON-DEGENERATE FIRST, and it is the check that the retarget made necessary.
# Every assertion below is of the form "no return omits a key another declares",
# which is vacuously true for a node that was never found and trivially true for
# a node with one return. A stale filename produces exactly that state, silently.
check("the parsed agent sources define all four node functions",
      sorted(_returns_by_node), sorted(_NODE_NAMES))
check("...and every one of them yields at least one dict return",
      sorted(n for n, v in _returns_by_node.items() if not v), [])
# The node this whole file exists for. node_cross_encoder_rerank is the only one
# with an ablation early return, so it is the only one where "no return omits a
# key another return declares" can be violated at all -- MEASURED at 3 dict
# returns, and the check is >= 2 because 1 would make the comparison vacuous.
# The other three have exactly one return each; that is a fact about them, not a
# threshold, so nothing here pretends otherwise.
check("...and node_cross_encoder_rerank has more than one, so the key-set "
      "comparison is a claim about something",
      len(_returns_by_node.get("node_cross_encoder_rerank", [])) >= 2, True)

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
# THIS IS THE ONE DRIVE IN THE FILE THAT RUNS THE RERANKING BODY, and it is
# what makes the MEDCPT_SCORER seam above load-bearing rather than decorative.
_MESH_FILTER.resolve_calls = 0
_scorer_calls_before = _SCORER.calls
_ = node_cross_encoder_rerank(make_stage3_state({"skip_mesh_filter": True}))
check("skip_mesh_filter skips resolution on the reranking path too",
      _MESH_FILTER.resolve_calls, 0)


# ===========================================================================
# TEST 4b: THE RERANKING BODY RAN, AND IT BUILT NO MODEL
# ===========================================================================
# Two halves, and neither is worth anything without the other. "No model was
# built" is also true of a drive that never reached the reranking body at all
# -- the two skip_cross_encoder drives above satisfy it for free -- so the
# non-degeneracy half comes first: the node really called the scorer.

print("\n" + "=" * 70)
print("Test 4b: the reranking body ran, and it built no cross-encoder")
print("=" * 70)

check("the drive above really entered the reranking body (non-degeneracy: "
      "without this, everything below is satisfied by a node that returned "
      "early)",
      _SCORER.calls > _scorer_calls_before, True)
check("...once per rerank query, which is one here",
      _SCORER.calls - _scorer_calls_before, 1)
check("...and it was handed the expanded query, so the stub stood in for the "
      "real call rather than for nothing",
      last(_SCORER.queries, "scorer query"), "lung neoplasms")

# THE PROPERTY THE BUCKET RATIONALE CLAIMS, MEASURED RATHER THAN ASSERTED.
# Before this seam these four read: medcpt_tokenizer and medcpt_model both
# cached, torch and transformers both in sys.modules -- measured 2026-09-08 by
# running the pre-seam file under a probe. The tokenizer and the weights are
# ~837 MB of checkpoint, fetched from huggingface.co on a cold cache, which is
# every CI runner.
check("no MedCPT tokenizer was built",
      deps.is_resolved(deps.MEDCPT_TOKENIZER), False)
check("no MedCPT model was built",
      deps.is_resolved(deps.MEDCPT_MODEL), False)
check("torch never entered sys.modules", "torch" in sys.modules, False)
check("transformers never entered sys.modules",
      "transformers" in sys.modules, False)

# fastembed IS deliberately absent from that list, and its own absence would be
# the surprising reading. qdrant_client.fastembed_common imports the LIBRARY at
# module scope, so it arrives with the agent's import chain whatever this file
# does; what would be a model load is oncotriage.embedding building the sparse
# model, and that is what is checked instead.
check("fastembed the LIBRARY is present, which is not a model load",
      "fastembed" in sys.modules, True)
check("...and no BM25 sparse model was constructed",
      _embedding._MODEL, None)


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
