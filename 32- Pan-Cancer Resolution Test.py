# Pan-Cancer Patient Resolution Test
###################################

"""
Pan-Cancer Patient Resolution Test

Fixture tests for the patient-side pan-cancer guard in
09- MeSH Cancer Site Relevance Filter.py and 13- LangGraph Agent.py.

The defect under test: patient_mesh_trees() could resolve a patient to C04 and
nothing else. SNOMED 363346000 (Malignant neoplastic disease), the mCODE
primary-cancer root, crosswalks to exactly ["C04"], and 35 SNOMED / 6 ICD-10 /
302 UMLS-synonym keys behave the same way. C04 is a prefix of every descriptor
in the tree, so:

  Stage 1  built child_prefixes = {"C04."}, which matches every descriptor:
           the expanded query named every cancer type in MeSH and fed two of
           the four fusion channels.
  Stage 3  found "shared ancestry" with every mapped trial, so specific trials
           took the full direct boost while genuine basket trials took only the
           smaller pan boost — the ranking signal inverted.
  Stage 4  was inert either way (C04 matches everything, so nothing dropped).

The trial side already had _is_pan_cancer. The patient side had no guard.

Covers:
    1. specific_cancer_trees — the depth test itself
    2. resolve_patient_trees — pan-only layers are walked past, not accepted;
       resolution continues through the remaining layers; a patient no layer
       resolves is reported unresolved, not pan-cancer
    3. The siteless-display gate — heuristic strategies must not invent a site
       for "Malignant neoplastic disease" / "Cancer"
    4. resolve_patient_mesh — refuted conditions, no fallback to the
       unfiltered list
    5. Stage 1 guard — no query expansion from a pan-cancer-only patient
    6. Stage 3 guard — no boost from a pan-cancer-only patient
    7. mesh_resolution reaches the node output and the inferences schema

No network and no LLM: everything runs off the pre-built MeSH JSON lookups.

Running this loads file 13, which loads the MedCPT cross-encoder and the
FastEmbed BM25 model at import (both cached locally after the first run).
No inference is performed on either.

Run from terminal (or F5 in Spyder):
    python "32- Pan-Cancer Resolution Test.py"

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# 13 chains 03, 08, 09, 10 itself — do not list them again here.
exec_chain(
    ["13- LangGraph Agent.py"],
    caller_file=_code_dir + "32- Pan-Cancer Resolution Test.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 13 (→ 03, 08, 09, 10)",
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


# ===========================================================================
# FIXTURES
# ===========================================================================

# The filter is required: every assertion below is about how a real MeSH
# resolution behaves. Without the JSON lookups there is nothing to test.
if _MESH_FILTER is None:
    print("\nMeSH filter not loaded (missing JSON lookups) — cannot run this test.")
    sys.exit(1)


def make_condition(display: str, snomed: str = None, icd10: str = None,
                   verification: str = "confirmed") -> dict:
    """Parsed-FHIR-shaped condition dict, multi-coding aware."""
    codings = []
    if snomed:
        codings.append({"system_key": "snomed", "code": snomed, "display": display})
    if icd10:
        codings.append({"system_key": "icd10cm", "code": icd10, "display": display})
    return {
        "code":                snomed or icd10 or "",
        "display":             display,
        "codings":             codings,
        "verification_status": verification,
        "clinical_status":     "active",
        "onset_date":          "2024-01-01",
    }


# SNOMED 363346000 is the mCODE primary-cancer root. It is in _SNOMED_PRIMARY,
# so is_primary_cancer() accepts it, and it crosswalks to exactly ["C04"].
MCODE_ROOT = "363346000"
NSCLC_SNOMED = "254637007"      # Non-small cell lung cancer -> a depth-8 tree

PAN_ONLY_CONDITION = make_condition("Malignant neoplastic disease (disorder)",
                                    snomed=MCODE_ROOT)
PAN_CODE_SPECIFIC_DISPLAY = make_condition("Non-small cell carcinoma of lung (disorder)",
                                           snomed=MCODE_ROOT)
LUNG_CONDITION = make_condition("Non-small cell lung cancer (disorder)",
                                snomed=NSCLC_SNOMED)


def make_patient(conditions: list) -> dict:
    """Minimal patient_data dict — only what Stage 1 and Stage 3 read."""
    return {
        "patient_id":   "test-pan-cancer",
        "demographics": {"age": 64, "sex": "male"},
        "conditions":   conditions,
        "observations": [],
    }


print("\n" + "=" * 70)
print("Test 1: specific_cancer_trees — the depth test")
print("=" * 70)

check("C04 dropped (depth 1)",
      specific_cancer_trees({"C04"}), set())
check("C04.588 dropped (depth 2)",
      specific_cancer_trees({"C04.588"}), set())
check("depth 3 kept",
      specific_cancer_trees({"C04.588.274"}), {"C04.588.274"})
check("pan node dropped from a mixed set",
      specific_cancer_trees({"C04", "C04.588", "C04.588.274"}), {"C04.588.274"})
check("empty in, empty out",
      specific_cancer_trees(set()), set())


print("\n" + "=" * 70)
print("Test 2: resolve_patient_trees — pan-cancer-only is unresolved")
print("=" * 70)

# The crosswalk fact this whole test rests on. If UMLS ever remaps the mCODE
# root to a specific site, this assertion is the one that should fail first.
check("SNOMED 363346000 crosswalks to C04 alone",
      _MESH_FILTER.snomed_to_trees.get(MCODE_ROOT), ["C04"])

_pan = _MESH_FILTER.resolve_patient_trees([PAN_ONLY_CONDITION], _CANCER_REGISTRY)
check("pan-only patient resolves to no trees", _pan["trees"], set())
check("pan-only patient reported as pan_cancer_only",
      _pan["resolution"], "pan_cancer_only")
check("the pan-only layers are named", "snomed" in _pan["pan_only_layers"], True)
check("condition counted as pan-only", _pan["conditions_pan_only"], 1)
check("no layer claimed to have resolved it", _pan["layers"], [])

# The escalation: the SNOMED layer is pan-only, so resolution continues to the
# remaining layers instead of stopping at C04.
_esc = _MESH_FILTER.resolve_patient_trees([PAN_CODE_SPECIFIC_DISPLAY], _CANCER_REGISTRY)
check("pan-only code + specific display escalates past the SNOMED layer",
      _esc["resolution"], "fuzzy_synonym")
check("escalation records the layer it walked past",
      _esc["pan_only_layers"], ["snomed"])
check("escalation produced specific trees only",
      _esc["trees"], specific_cancer_trees(_esc["trees"]))
check("escalation resolved the actual site (lung)",
      all(t.startswith("C04.588.894.797.520") for t in _esc["trees"]), True)

_lung = _MESH_FILTER.resolve_patient_trees([LUNG_CONDITION], _CANCER_REGISTRY)
check("a normal patient still resolves on the SNOMED layer",
      _lung["resolution"], "snomed")
check("patient_mesh_trees agrees with resolve_patient_trees",
      _MESH_FILTER.patient_mesh_trees([LUNG_CONDITION], _CANCER_REGISTRY),
      _lung["trees"])

_none = _MESH_FILTER.resolve_patient_trees(
    [make_condition("Hypertension", snomed="38341003")], _CANCER_REGISTRY)
check("no cancer condition is its own outcome, not pan_cancer_only",
      _none["resolution"], "no_cancer_condition")

# Unresolved must stay conservative: an unresolved patient keeps every trial,
# exactly as an unmappable one always did.
_prostate_trial = {"conditions": ["Prostatic Neoplasms"], "keywords": [], "title": "P"}
check("unresolved patient keeps an unrelated trial (conservative)",
      _MESH_FILTER.is_cancer_relevant(_pan["trees"], _prostate_trial), True)
check("resolved lung patient still drops the prostate trial",
      _MESH_FILTER.is_cancer_relevant(_lung["trees"], _prostate_trial), False)


print("\n" + "=" * 70)
print("Test 3: siteless displays do not get a site invented for them")
print("=" * 70)

# Stem overlap on "malignant"/"neoplast"/"disease" used to return 27 unrelated
# descriptors (Bowen's Disease, Hodgkin Disease, Carcinoid Heart Disease...),
# and substring on "cancer" returns Hereditary Breast and Ovarian Cancer
# Syndrome. Walking past a pan-only layer must not hand the patient to those.
for _siteless in ("Malignant neoplastic disease", "Malignant neoplasm, unspecified",
                  "Cancer", "Malignant tumor"):
    _strategies = [s for s, _ in _MESH_FILTER._fuzzy_layers(_siteless)]
    check(f"'{_siteless}' -> no heuristic strategy fires",
          [s for s in _strategies if s in ("fuzzy_substring", "fuzzy_stem")], [])
    check(f"'{_siteless}' -> no specific trees",
          _MESH_FILTER._fuzzy_match_display(_siteless), set())

# ...while a display that does name a site still resolves through them.
check("'Malignant neoplasm of colon' still resolves",
      bool(_MESH_FILTER._fuzzy_match_display("Malignant neoplasm of colon")), True)


print("\n" + "=" * 70)
print("Test 4: resolve_patient_mesh — refuted conditions, no fallback")
print("=" * 70)

_refuted = make_condition("Non-small cell lung cancer (disorder)",
                          snomed=NSCLC_SNOMED, verification="refuted")
_r = resolve_patient_mesh([_refuted], _CANCER_REGISTRY, _MESH_FILTER)
check("an all-refuted condition list resolves to nothing", _r["trees"], set())
check("...and says why", _r["resolution"], MESH_RESOLUTION_NO_CONDITIONS)

_r2 = resolve_patient_mesh([LUNG_CONDITION], _CANCER_REGISTRY, None)
check("no filter loaded is its own outcome", _r2["resolution"], MESH_RESOLUTION_NO_FILTER)

_r3 = resolve_patient_mesh([_refuted, LUNG_CONDITION], _CANCER_REGISTRY, _MESH_FILTER)
check("a refuted condition alongside a valid one is ignored, not fatal",
      _r3["trees"], _lung["trees"])


print("\n" + "=" * 70)
print("Test 5: Stage 1 — no query expansion from a pan-cancer-only patient")
print("=" * 70)

_exp_pan = expand_query_from_mesh([PAN_ONLY_CONDITION], _CANCER_REGISTRY, _MESH_FILTER)
check("no MeSH terms", _exp_pan["mesh_terms"], [])
check("no patient trees", _exp_pan["patient_trees"], [])
check("no primary descriptor", _exp_pan["primary_mesh"], None)
check("resolution recorded as pan_cancer_only", _exp_pan["resolution"], "pan_cancer_only")

_exp_lung = expand_query_from_mesh([LUNG_CONDITION], _CANCER_REGISTRY, _MESH_FILTER)
check("a resolved patient still expands", bool(_exp_lung["mesh_terms"]), True)
check("...and no pan-cancer node is among its trees",
      [t for t in _exp_lung["patient_trees"] if len(t.split(".")) <= 2], [])
check("...and the layer is named", _exp_lung["resolution"], "snomed")

# The node contract: mesh_resolution leaves Stage 1 for the state.
_node_out = node_query_expansion({"patient_data": make_patient([PAN_ONLY_CONDITION])})
check("node_query_expansion reports mesh_resolution",
      _node_out["mesh_resolution"], "pan_cancer_only")
check("...and falls back to the base query, not a tree-wide one",
      "Neoplasms" in _node_out["expanded_query"], False)


print("\n" + "=" * 70)
print("Test 6: Stage 3 — no boost from a pan-cancer-only patient")
print("=" * 70)


def make_candidate(nct_id: str, score: float, trees: set) -> dict:
    return {
        "trial": {"nct_id": nct_id, "conditions": [], "keywords": [], "title": "T",
                  "_trees": trees},
        "rerank_score":     score,
        "rerank_score_raw": score,
        "mesh_boost":       0.0,
        "mesh_boost_tier":  "none",
    }


class _TreeStub:
    """Trial-side trees by fixture, patient side untouched."""

    PAN_CANCER_MAX_DEPTH = _MESH_FILTER.PAN_CANCER_MAX_DEPTH

    def trial_mesh_trees(self, trial):
        return set(trial.get("_trees") or set())

    def _is_pan_cancer(self, trial_trees):
        return _MESH_FILTER._is_pan_cancer(trial_trees)


_pool = [
    make_candidate("NCT00000001", 0.030, {"C04.588.894.797.520.109"}),   # lung-specific
    make_candidate("NCT00000002", 0.020, {"C04.588"}),                    # basket
    make_candidate("NCT00000003", 0.010, {"C04.588.322.400"}),            # breast
]

_pan_stats = apply_mesh_relevance_boost(_pool, {"C04"}, _TreeStub())
check("pan-cancer-only patient trees report their own path",
      _pan_stats["path"], "pan_cancer_only_patient_trees")
check("...and nothing is boosted", _pan_stats["direct_boosted"], 0)
check("...and every trial is counted unboosted",
      _pan_stats["unboosted"], len(_pool))
check("...and no score moved",
      [t["rerank_score"] for t in _pool], [0.030, 0.020, 0.010])
check("...and no trial carries a boost tier",
      {t["mesh_boost_tier"] for t in _pool}, {"none"})

# The empty-set path stays distinguishable from the pan-only path.
_empty_stats = apply_mesh_relevance_boost(
    [make_candidate("NCT00000004", 0.030, {"C04.588.894.797.520.109"})],
    set(), _TreeStub())
check("unmappable patient still reports no_patient_trees",
      _empty_stats["path"], "no_patient_trees")

# A resolved patient still gets the tiers it always did.
_pool2 = [
    make_candidate("NCT00000001", 0.030, {"C04.588.894.797.520.109"}),
    make_candidate("NCT00000002", 0.020, {"C04.588"}),
    make_candidate("NCT00000003", 0.010, {"C04.588.322.400"}),
]
_ok_stats = apply_mesh_relevance_boost(
    _pool2, {"C04.588.894.797.520"}, _TreeStub())
check("resolved patient: one direct match", _ok_stats["direct_boosted"], 1)
check("resolved patient: one pan-cancer match", _ok_stats["pan_boosted"], 1)
check("resolved patient: one unrelated trial unboosted", _ok_stats["unboosted"], 1)


print("\n" + "=" * 70)
print("Test 7: mesh_resolution is recorded")
print("=" * 70)

# The column has to exist for the value to be queryable, so this section builds
# a database and looks at its schema.
#
# WHAT WAS WRONG HERE, corrected in pass 20c-2b. The comment used to say
# "loading file 14 runs the additive migration; it is idempotent", and the
# PRAGMA below ran against the PRODUCTION inferences.db. Both halves stopped
# being true at item 20b, which turned schema creation into a function: loading
# File 14 has run no migration since. The check kept passing only because the
# production database already carried the column from an earlier run — so it was
# reporting on a file's history rather than on File 14's schema, and it would
# have gone on passing after the column was deleted from the schema entirely.
#
# It now does what Files 36, 37, 38 and 40 do: a temporary database, built by an
# explicit initialize_database() call, thrown away afterwards. Nothing in this
# file opens the production database any more, and this was the only place that
# did.
#
# The chain still loads File 14 — initialize_database has to come from
# somewhere — but it is now loaded for the function rather than for a side
# effect it no longer has.
exec_chain(
    ["14- Database Logger.py"],
    caller_file=_code_dir + "32- Pan-Cancer Resolution Test.py",
    caller_globals=globals(),
    chain_label="14",
)

import shutil as _shutil
import tempfile as _tempfile

_SCHEMA_TMP_DIR = _tempfile.mkdtemp(prefix="oncotriage_pan_cancer_schema_")
_SCHEMA_DB = os.path.join(_SCHEMA_TMP_DIR, "inferences_test.db")

# NON-DEGENERATE FIRST. The membership check below is satisfied by any database
# that happens to carry the column, including one built by some earlier run —
# which is exactly the defect this block replaces. The file must not exist
# before initialize_database() creates it, so the schema under test is the one
# File 14 just produced and nothing else.
check("the schema database does not exist before it is built",
      os.path.exists(_SCHEMA_DB), False)
check("...and it is not the production database",
      os.path.abspath(_SCHEMA_DB) == os.path.abspath(inferences_path), False)

initialize_database(_SCHEMA_DB)

_conn = sqlite3.connect(_SCHEMA_DB)
try:
    _cols = {row[1] for row in _conn.execute("PRAGMA table_info(inferences)")}
finally:
    _conn.close()

# ...and the column set actually came back. An empty set makes every "column X
# exists" check fail rather than pass, but it fails for the wrong reason, and
# three empty-set failures is precisely what item 20b's removal of the load-time
# side effect produced in File 40 before its explicit call was added.
check("the freshly built schema reports a substantial column set",
      len(_cols) >= 40, True)

check("inferences.mesh_resolution exists", "mesh_resolution" in _cols, True)

_shutil.rmtree(_SCHEMA_TMP_DIR, ignore_errors=True)

# The finalize nodes carry it from state into the logged result.
_state = {
    "patient_data":    make_patient([PAN_ONLY_CONDITION]),
    "mesh_resolution": "pan_cancer_only",
    "evaluations":     [],
}
check("node_finalize passes it through",
      node_finalize(_state)["result"]["mesh_resolution"], "pan_cancer_only")
check("node_no_candidates passes it through",
      node_no_candidates(_state)["result"]["mesh_resolution"], "pan_cancer_only")
check("node_error_handler passes it through",
      node_error_handler({**_state, "error": "boom"})["result"]["mesh_resolution"],
      "pan_cancer_only")


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
        print(f"  - {_f}")

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 2 2026

@author: ramyalsaffar
"""
