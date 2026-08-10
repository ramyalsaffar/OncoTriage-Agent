# Degraded-Run Marker and Run-End Degradation Reporting Test
############################################################

"""What this pass added, and the demonstration that each piece can fail.

FIVE SUBJECTS, all of them about VISIBILITY and none of them about behaviour:

  1. ``inferences.degraded_run`` -- the one-glance per-patient marker, derived in
     ``oncotriage/agent/terminal.py:_derive_degraded_run`` from observations the
     state already carried. Clean state yields 0, each contributing observation
     ALONE yields 1, and a result dict that never met a terminal node stores
     NULL.
  2. ``oncotriage/utils.py:QDRANT_RETRIES`` -- ``qdrant_retry`` retried silently
     for the whole life of the project. The counter is driven through TENACITY'S
     OWN MACHINERY against a stub that raises real httpx errors, never through a
     hand-rolled imitation of it: the thing under test is the decorator's
     before_sleep contract, and a fake retry loop would test this file.
  3. ``oncotriage/batch/runner.py:load_results`` -- a corrupt results file used
     to return ``[]`` silently and then be DESTROYED by the first
     ``append_result`` of a resumed run. It now logs, counts, and is renamed to
     a ``.corrupt`` sidecar before any write can replace it.
  4. ``oncotriage/degradation.py`` -- the run-end report. A non-zero counter
     appears in the summary event and the console block; an all-zero registry
     STATES that it is zero, so silence is a statement rather than an absence.
  5. The four Stage 4 filter-applied markers (stage, histology, age, sex), which
     make ``stage_dropped = 0`` distinguishable from "the stage filter never
     ran" -- mesh_resolution's precedent, applied to the four filters that were
     missing it.

WHAT IT COSTS: nothing. No network, no keys, no spend, no live server, no live
Qdrant, no git history, no corpus. Every trial and every patient is a literal
dict, the Qdrant client and the MeSH filter are replaced through
oncotriage/agent/deps.py, and the only database is a temp file every
log_inference call is pointed at explicitly.

IT EXECS NOTHING, so it needs no _EXEC_ALLOWLIST entry. Every control either
feeds a DIFFERENT INPUT to a pure function of its argument -- the natural
control for the derivation, and the shape tests/test_agent_patient_hash_coverage.py
argues for -- or creates the failing condition for real (a genuinely corrupt
file, a genuinely unwritable directory, a stub that genuinely raises), or
rebinds a module attribute inside a try/finally, which is what
tests/test_storage_write_durability.py does for the same reason.

Run from terminal:
    python tests/test_agent_degraded_run_and_reporting.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import shutil
from contextlib import redirect_stderr

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

import httpx

from oncotriage import degradation
from oncotriage import paths
from oncotriage import utils
from oncotriage.agent import deps
from oncotriage.agent import filtering as _filtering
from oncotriage.agent import terminal as _terminal
from oncotriage.agent.state import (
    AGE_FILTER_SKIP_NO_PATIENT_AGE,
    FILTER_APPLIED,
    FILTER_SKIP_ABLATED,
    HISTOLOGY_FILTER_SKIP_NO_PATIENT_HISTOLOGY,
    MESH_FILTER_SKIP_NO_FILTER,
    MESH_FILTER_SKIP_NO_TREES,
    SEX_FILTER_SKIP_NOT_COMPARABLE,
    STAGE_FILTER_SKIP_NO_PATIENT_STAGE,
)
from oncotriage.agent.terminal import (
    node_error_handler,
    node_finalize,
    node_no_candidates,
)
from oncotriage.batch import runner as _runner
from oncotriage.config import MAX_LLM_CLASSIFIER_RETRIES
from oncotriage.storage.database_logger import (
    initialize_database,
    log_inference,
    resolve_inference_db_path,
)


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def check_true(label, condition):
    check(label, bool(condition), True)


def quiet(fn, *args, **kwargs):
    """Run fn with the console and log streams captured. Returns (value, text).

    Both channels write to STDERR (oncotriage/observability.py), so one redirect
    catches the console block and the JSON records together -- which is what
    section 5 needs, because it asserts on both.
    """
    buf = io.StringIO()
    with redirect_stderr(buf):
        value = fn(*args, **kwargs)
    return value, buf.getvalue()


#------------------------------------------------------------------------------


# ===========================================================================
# A DATABASE THAT IS NOT THE PRODUCTION ONE, ASSERTED RATHER THAN ASSUMED
# ===========================================================================

_SCRATCH = tempfile.mkdtemp(prefix="oncotriage-degraded-run-")
_DB = os.path.join(_SCRATCH, "inferences_test.db")

print("=" * 74)
print("0. isolation: the scratch database is not the production one")
print("=" * 74)

_PRODUCTION_DB = resolve_inference_db_path(None)
check_true("the default still resolves to the production database",
           _PRODUCTION_DB and _PRODUCTION_DB != _DB)
check("...and an explicit path outranks it",
      resolve_inference_db_path(_DB), _DB)
initialize_database(_DB)
check_true("the scratch database was created", os.path.exists(_DB))


#------------------------------------------------------------------------------


# ===========================================================================
# 1. THE degraded_run DERIVATION, ONE TERM AT A TIME
# ===========================================================================
#
# THE CASES ARE EACH OTHER'S CONTROLS, which is why the clean case is asserted
# first and separately: without it, "every case yields 1" would be satisfied by
# a derivation that returns 1 unconditionally, and every check below it would
# pass for the wrong reason. _derive_degraded_run is a pure function of state,
# so a different input IS the natural control -- there is nothing to patch.

print("\n" + "=" * 74)
print("1. degraded_run: clean yields 0, each observation alone yields 1")
print("=" * 74)

PATIENT_DATA = {
    "patient_id": "degraded-run-test-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino",
                     "birth_date_precision": "day"},
    "conditions": [{"display": "Malignant neoplasm of breast (disorder)",
                    "code": "254837009", "system": "SNOMED"}],
    "medications": [],
    "allergies": [],
    "observations": [],
}


def clean_state(**overrides):
    """A terminal state on which no degradation signal fires.

    Every term of the predicate is present and clean rather than absent, so a
    0 from this state is a measurement and not a default.
    """
    state = {
        "patient_data": PATIENT_DATA,
        "expanded_query": "breast neoplasms",
        "hybrid_results": [],
        "reranked_trials": [],
        "filtered_trials": [],
        "evaluations": [],
        "stage_timings": {},
        "ablation_flags": {},
        "error": "",
        "retrieval_degraded": 0,
        "retrieval_channels_expected": 4,
        "retrieval_channels_ok": 4,
        "mesh_filter_applied": True,
        "mesh_filter_skip_reason": FILTER_APPLIED,
        "llm_classifier_retries": 0,
        "not_evaluable_truncated": 0,
        "hallucinated_trials": 0,
    }
    state.update(overrides)
    return state


# The five ways a run is degraded, each one on its own. The label is what the
# report says; the override is the ONLY thing that differs from clean_state().
_DEGRADING = [
    ("a Stage 5 failure reached the error handler",
     {"error": "planted: the model call failed"}),
    ("an expected retrieval channel did not return",
     {"retrieval_degraded": 1}),
    ("the cancer site filter had no MeSH data to run with",
     {"mesh_filter_applied": False,
      "mesh_filter_skip_reason": MESH_FILTER_SKIP_NO_FILTER}),
    ("Stage 5 exhausted its parse-retry budget",
     {"llm_classifier_retries": MAX_LLM_CLASSIFIER_RETRIES}),
    ("a trial left Stage 5 with no verdict because of truncation",
     {"not_evaluable_truncated": 1}),
]

# NOT degrading, and each is a decision recorded at the derivation rather than
# an oversight. Asserted so a later edit that folds one of them in has to change
# this file and say why.
_NOT_DEGRADING = [
    ("an ABLATED cancer site filter is a configured experiment, not a fault",
     {"mesh_filter_applied": False,
      "mesh_filter_skip_reason": FILTER_SKIP_ABLATED}),
    ("a patient who resolved to no C04 trees is a property of the record",
     {"mesh_filter_applied": False,
      "mesh_filter_skip_reason": MESH_FILTER_SKIP_NO_TREES}),
    ("retries SPENT but not exhausted is a run that recovered",
     {"llm_classifier_retries": MAX_LLM_CLASSIFIER_RETRIES - 1}),
]

check_true("the retry ceiling is above 1, so 'spent' and 'exhausted' are "
           "distinguishable (non-degeneracy)", MAX_LLM_CLASSIFIER_RETRIES > 1)

check("a clean state yields 0", _terminal._derive_degraded_run(clean_state()), 0)

for _label, _override in _DEGRADING:
    check(f"1 when {_label}",
          _terminal._derive_degraded_run(clean_state(**_override)), 1)

for _label, _override in _NOT_DEGRADING:
    check(f"still 0: {_label}",
          _terminal._derive_degraded_run(clean_state(**_override)), 0)


#------------------------------------------------------------------------------


# ===========================================================================
# 1b. THE SAME MATRIX THROUGH THE THREE TERMINAL NODES
# ===========================================================================
#
# The derivation being right is not the same as it REACHING the result. It is
# spread into all three terminal results through _pipeline_provenance, so a key
# on one path only is structurally impossible -- but a call site that dropped it
# would leave every stored row NULL while this file's section 1 stayed green.

print("\n" + "=" * 74)
print("1b. every terminal node carries the marker it derived")
print("=" * 74)

for _node_name, _node in (("node_finalize", node_finalize),
                          ("node_no_candidates", node_no_candidates),
                          ("node_error_handler", node_error_handler)):
    _clean_result = _node(clean_state())["result"]
    check(f"{_node_name} declares degraded_run",
          "degraded_run" in _clean_result, True)
    check(f"{_node_name} on a clean state reports 0",
          _clean_result["degraded_run"], 0)
    _dirty_result = _node(clean_state(retrieval_degraded=1))["result"]
    check(f"{_node_name} on a degraded state reports 1",
          _dirty_result["degraded_run"], 1)


#------------------------------------------------------------------------------


# ===========================================================================
# 1c. THE CONTROL: a derivation that stops reading a term is CAUGHT
# ===========================================================================
#
# Sections 1 and 1b compare the shipped function against inputs. This asks the
# other question -- would they NOTICE a regression -- by installing a crippled
# derivation and requiring the matrix to change. The rebind is inside a
# try/finally and the module attribute is compared before and after, which is
# tests/test_storage_write_durability.py's control shape and needs no exec.
#
# The crippled version drops exactly one term (`error`), which is the smallest
# regression a careless edit could produce. A control that broke everything
# would prove only that the matrix reads SOMETHING.

print("\n" + "=" * 74)
print("1c. control: a derivation missing one term is caught by section 1")
print("=" * 74)


def _matrix(derive):
    """Every case's answer under `derive`, as a tuple. Order is _DEGRADING's."""
    return tuple(derive(clean_state(**o)) for _, o in _DEGRADING)


_SHIPPED = _terminal._derive_degraded_run
_shipped_answers = _matrix(_SHIPPED)
check("the shipped derivation answers 1 to every degrading case",
      _shipped_answers, (1,) * len(_DEGRADING))


def _crippled(state):
    """_derive_degraded_run with the `error` term deleted, and nothing else."""
    if state.get("retrieval_degraded"):
        return 1
    if state.get("mesh_filter_skip_reason") == MESH_FILTER_SKIP_NO_FILTER:
        return 1
    if (state.get("llm_classifier_retries") or 0) >= MAX_LLM_CLASSIFIER_RETRIES:
        return 1
    if (state.get("not_evaluable_truncated") or 0) > 0:
        return 1
    return 0


try:
    _terminal._derive_degraded_run = _crippled
    _crippled_answers = _matrix(_terminal._derive_degraded_run)
    # The whole point: the SAME assertion section 1 makes now fails.
    check("...and the crippled one does not, so section 1 can fail",
          _crippled_answers == (1,) * len(_DEGRADING), False)
    check("exactly the dropped term's case flipped, and no other",
          [i for i, (a, b) in enumerate(zip(_shipped_answers, _crippled_answers))
           if a != b], [0])
    # AND the terminal node picks the crippled one up, which is what proves
    # section 1b would fail too rather than reading a cached value.
    check("the terminal node reports the crippled answer while it is installed",
          node_error_handler(clean_state(error="planted"))["result"]["degraded_run"],
          0)
finally:
    _terminal._derive_degraded_run = _SHIPPED

check("the shipped derivation was restored",
      _terminal._derive_degraded_run is _SHIPPED, True)
check("...and the matrix is green again",
      _matrix(_terminal._derive_degraded_run), (1,) * len(_DEGRADING))


#------------------------------------------------------------------------------


# ===========================================================================
# 2. THE COLUMN: 0, 1 AND NULL THROUGH A REAL WRITE
# ===========================================================================
#
# A throwaway database, every call pointed at it explicitly, and the three
# values read back out of SQLite rather than off the result dict -- because the
# thing that can go wrong between the two is the INSERT's positional tuple, and
# only a round trip can see it.

print("\n" + "=" * 74)
print("2. degraded_run reaches the column as 0, 1 and NULL")
print("=" * 74)


def _write(result, patient_id):
    result = dict(result)
    result["patient_id"] = patient_id
    outcome = log_inference(result, PATIENT_DATA, db_path=_DB)
    check(f"the row for {patient_id} was written to the scratch database",
          (str(outcome), getattr(outcome, "ok", None)), (_DB, True))
    return outcome


def _column(patient_id, column="degraded_run"):
    conn = sqlite3.connect(_DB)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            f"SELECT {column} FROM inferences WHERE patient_id = ? "
            f"ORDER BY id DESC LIMIT 1", (patient_id,)).fetchone()
        return row[column] if row else "<no row>"
    finally:
        conn.close()


_write(node_finalize(clean_state())["result"], "degraded-run-clean")
check("a clean run stores 0", _column("degraded-run-clean"), 0)

_write(node_error_handler(clean_state(error="planted"))["result"],
       "degraded-run-degraded")
check("a degraded run stores 1", _column("degraded-run-degraded"), 1)

# NEVER REACHED: a result dict that did not come from a terminal node. This is
# the state the brief calls "the run never reached the derivation", and it is
# llm_classifier_prompt_sha256's convention -- absence of the fact, never a
# fallback to a clean value.
_write({"patient_id": "x", "timestamp": "2026-08-10T00:00:00",
        "matches": [], "near_misses": [], "not_evaluable": [],
        "stage_timings": {}, "error": ""},
       "degraded-run-never-derived")
check("a result dict that never met a terminal node stores NULL",
      _column("degraded-run-never-derived"), None)

check("the three arms are three distinct values (non-degeneracy)",
      sorted({_column("degraded-run-clean"),
              _column("degraded-run-degraded"),
              _column("degraded-run-never-derived")}, key=str),
      [0, 1, None])


#------------------------------------------------------------------------------


# ===========================================================================
# 3. THE FOUR STAGE 4 FILTER-APPLIED MARKERS
# ===========================================================================
#
# mesh_dropped had mesh_filter_applied beside it; stage_dropped,
# histology_dropped, age_dropped and sex_dropped had nothing, so a 0 in any of
# the four meant both "checked, nothing to drop" and "never checked".
#
# THE ASSERTION THAT MATTERS IS THE PAIR, not the marker on its own: each case
# below asserts the drop count is 0 AND the marker says why, because a marker
# that reported "skipped" while the filter was in fact dropping trials would be
# worse than no marker.

print("\n" + "=" * 74)
print("3. stage / histology / age / sex report whether they ran")
print("=" * 74)


def _trial(nct_id, **eligibility):
    base = {"min_age": "18 Years", "max_age": "99 Years", "sex": "ALL",
            "criteria": "", "inclusion_criteria": "", "exclusion_criteria": ""}
    base.update(eligibility)
    return {"trial": {"nct_id": nct_id, "title": "A trial",
                      "conditions": ["Breast Neoplasms"],
                      "eligibility": base,
                      "stage_requirements": {}, "histology_tags": []},
            "rerank_score": 1.0, "rerank_score_raw": 1.0,
            "medcpt_score_max": 10.0, "medcpt_queries_scored": 1}


def _stage4(patient_overrides=None, ablation=None):
    """Run the shipped Stage 4 node with the MeSH filter absent.

    The filter is set to None through the dependency seam -- deliberately, so
    this section measures the FOUR markers it is about without a MeSH lookup
    file being a precondition. mesh_filter_applied is then no_mesh_filter on
    every case here, which is asserted rather than ignored.
    """
    patient = dict(PATIENT_DATA)
    patient["demographics"] = dict(PATIENT_DATA["demographics"])
    for key, value in (patient_overrides or {}).items():
        if key in ("age", "sex"):
            patient["demographics"][key] = value
        else:
            patient[key] = value
    state = {
        "patient_data": patient,
        "reranked_trials": [_trial("NCT00000001")],
        "stage_timings": {},
        "ablation_flags": ablation or {},
        "patient_trees": set(),
        "mesh_resolution": "unmapped",
    }
    return _filtering.node_rule_based_filter(state)


deps.set_override(deps.MESH_FILTER, None)
try:
    _base = _stage4()
    check("the MeSH filter is genuinely absent for this section "
          "(non-degeneracy)",
          _base["mesh_filter_skip_reason"], MESH_FILTER_SKIP_NO_FILTER)

    # --- cancer stage --------------------------------------------------------
    # The literal patient above carries a breast-cancer condition with no stage
    # in its display and no stage observations, so the extractor returns None.
    check("stage: a patient with no resolvable stage reports not-applied",
          (_base["stage_filter_applied"], _base["stage_filter_skip_reason"],
           _base["stage_dropped"]),
          (False, STAGE_FILTER_SKIP_NO_PATIENT_STAGE, 0))
    _staged = _stage4({"conditions": [
        {"display": "Malignant neoplasm of breast, TNM stage 2",
         "code": "254837009", "system": "SNOMED"}]})
    check("stage: a patient WITH a stage reports applied (the control)",
          (_staged["stage_filter_applied"], _staged["stage_filter_skip_reason"]),
          (True, FILTER_APPLIED))
    _stage_ablated = _stage4({"conditions": [
        {"display": "Malignant neoplasm of breast, TNM stage 2",
         "code": "254837009", "system": "SNOMED"}]},
        ablation={"skip_stage_filter": True})
    check("stage: the ablation flag outranks the patient's stage",
          (_stage_ablated["stage_filter_applied"],
           _stage_ablated["stage_filter_skip_reason"]),
          (False, FILTER_SKIP_ABLATED))

    # --- histology -----------------------------------------------------------
    check("histology: a patient with no histology tag reports not-applied",
          (_base["histology_filter_applied"],
           _base["histology_filter_skip_reason"], _base["histology_dropped"]),
          (False, HISTOLOGY_FILTER_SKIP_NO_PATIENT_HISTOLOGY, 0))
    _histo = _stage4({"conditions": [
        {"display": "Non-small cell carcinoma of lung (disorder)",
         "code": "254637007", "system": "SNOMED"}]})
    check("histology: a tagged patient reports applied (the control)",
          (_histo["histology_filter_applied"],
           _histo["histology_filter_skip_reason"]),
          (True, FILTER_APPLIED))
    _histo_ablated = _stage4({"conditions": [
        {"display": "Non-small cell carcinoma of lung (disorder)",
         "code": "254637007", "system": "SNOMED"}]},
        ablation={"skip_histology_filter": True})
    check("histology: the ablation flag outranks the patient's tags",
          (_histo_ablated["histology_filter_applied"],
           _histo_ablated["histology_filter_skip_reason"]),
          (False, FILTER_SKIP_ABLATED))

    # --- age -----------------------------------------------------------------
    check("age: a patient with an age reports applied",
          (_base["age_filter_applied"], _base["age_filter_skip_reason"]),
          (True, FILTER_APPLIED))
    _no_age = _stage4({"age": None})
    check("age: a patient with no computable age reports not-applied",
          (_no_age["age_filter_applied"], _no_age["age_filter_skip_reason"],
           _no_age["age_dropped"]),
          (False, AGE_FILTER_SKIP_NO_PATIENT_AGE, 0))

    # --- sex -----------------------------------------------------------------
    check("sex: a comparable sex reports applied",
          (_base["sex_filter_applied"], _base["sex_filter_skip_reason"]),
          (True, FILTER_APPLIED))
    _no_sex = _stage4({"sex": None})
    check("sex: an uncomparable sex reports not-applied",
          (_no_sex["sex_filter_applied"], _no_sex["sex_filter_skip_reason"],
           _no_sex["sex_dropped"]),
          (False, SEX_FILTER_SKIP_NOT_COMPARABLE, 0))

    # THE MARKER IS THE PREDICATE, NOT A DECLARATION BESIDE IT. A sex-specific
    # trial reaching a patient whose sex does not parse must be KEPT -- the
    # governing rule -- and the marker must say the filter did not run. If the
    # two ever came apart, this is the check that sees it.
    _sex_specific = _filtering.node_rule_based_filter({
        "patient_data": {**PATIENT_DATA,
                         "demographics": {**PATIENT_DATA["demographics"],
                                          "sex": None}},
        "reranked_trials": [_trial("NCT00000002", sex="MALE")],
        "stage_timings": {}, "ablation_flags": {},
        "patient_trees": set(), "mesh_resolution": "unmapped",
    })
    check("an uncomparable sex KEEPS a sex-specific trial and says the filter "
          "did not run",
          (len(_sex_specific["filtered_trials"]),
           _sex_specific["sex_dropped"],
           _sex_specific["sex_filter_applied"]),
          (1, 0, False))
finally:
    deps.clear_override(deps.MESH_FILTER)

check("the MeSH override was cleared, so this file leaves no stand-in behind",
      deps.resolution_state(deps.MESH_FILTER) == deps.RESOLVED_OVERRIDE, False)


#------------------------------------------------------------------------------


# ===========================================================================
# 4. qdrant_retry COUNTS WHAT IT RETRIES
# ===========================================================================
#
# THROUGH TENACITY'S OWN MACHINERY. The subject is the decorator's before_sleep
# contract, so the decorator is applied to a stub and the stub raises the real
# httpx exceptions the retry predicate names. A hand-rolled loop here would
# assert that this file can count, which is not in doubt.
#
# THE ZERO ARM IS THE CONTROL and runs first: without it, "the counter moved"
# would be satisfied by a hook that fires on every call.

print("\n" + "=" * 74)
print("4. QDRANT_RETRIES records a retried Qdrant call, and nothing else")
print("=" * 74)

utils.QDRANT_RETRIES.clear()


@utils.qdrant_retry
def _never_fails():
    return "ok"


check("a call that succeeds first time returns normally", _never_fails(), "ok")
check("...and records NOTHING (the control)", dict(utils.QDRANT_RETRIES), {})

_attempts = {"n": 0}


@utils.qdrant_retry
def _fails_twice_then_works():
    _attempts["n"] += 1
    if _attempts["n"] < 3:
        raise httpx.ConnectError("planted connection failure")
    return "ok"


_value, _out = quiet(_fails_twice_then_works)
check("a call that fails twice still returns its value", _value, "ok")
check("...having been attempted three times", _attempts["n"], 3)
check("...and TWO retries were recorded, keyed by the function",
      dict(utils.QDRANT_RETRIES), {"_fails_twice_then_works": 2})
check_true("...and each retry emitted a structured record naming the error",
           _out.count('"event": "qdrant_retry"') == 2
           and '"error_type": "ConnectError"' in _out)

# A NON-RETRYABLE EXCEPTION MUST NOT BE COUNTED, because the counter's meaning
# is "the retry policy did work", not "something failed". This also confirms
# the pass did not widen the exception classes.
utils.QDRANT_RETRIES.clear()


@utils.qdrant_retry
def _raises_unretryable():
    raise ValueError("not a Qdrant transport failure")


_raised = None
try:
    _raises_unretryable()
except ValueError as _exc:
    _raised = type(_exc).__name__
check("a non-retryable exception propagates unchanged", _raised, "ValueError")
check("...and is NOT counted as a retry", dict(utils.QDRANT_RETRIES), {})

# THE POLICY IS UNCHANGED, asserted rather than assumed: three attempts, and
# the exception reaches the caller when they are exhausted.
utils.QDRANT_RETRIES.clear()
_hopeless = {"n": 0}


@utils.qdrant_retry
def _always_fails():
    _hopeless["n"] += 1
    raise httpx.ConnectError("planted, permanent")


_final = None
try:
    quiet(_always_fails)
except Exception as _exc:            # tenacity re-raises as RetryError
    _final = type(_exc).__name__
check("an exhausted retry still reaches the caller", _final is not None, True)
check("...after exactly 3 attempts (the policy was not changed)",
      _hopeless["n"], 3)
check("...and 2 sleeps were counted, which is 3 attempts",
      sum(utils.QDRANT_RETRIES.values()), 2)
utils.QDRANT_RETRIES.clear()


#------------------------------------------------------------------------------


# ===========================================================================
# 5. THE RUN-END REPORT: NON-ZERO IS NAMED, ALL-ZERO IS STATED
# ===========================================================================

print("\n" + "=" * 74)
print("5. the degradation report names what moved and states when nothing did")
print("=" * 74)

check_true("the registry is non-empty (non-degeneracy)",
           len(degradation.registered_names()) >= 15)
check_true("...and it contains the counters this pass added",
           {"QDRANT_RETRIES", "RESULTS_FILE_FAILURES"}
           <= set(degradation.registered_names()))

_saved_registered = {name: dict(degradation._REGISTRY[name])
                     for name in degradation.registered_names()}
try:
    degradation.clear_all()

    # --- ALL ZERO ---------------------------------------------------------
    _snap = degradation.snapshot()
    check("a clean registry snapshots to nothing", _snap, {})
    _clean_text = "\n".join(degradation.report_lines(_snap))
    check_true("...and the block SAYS SO rather than printing nothing",
               "CLEAN" in _clean_text
               and str(len(degradation.registered_names())) in _clean_text)
    check_true("...and calls it a measurement, not an absence",
               "measurement" in _clean_text)
    _totals, _clean_out = quiet(degradation.log_summary, _snap)
    check("the clean event reports zero totals", _totals, {})
    check_true("...and it IS emitted, with status=clean",
               '"event": "degradation_summary"' in _clean_out
               and '"status": "clean"' in _clean_out)

    # --- NON-ZERO ---------------------------------------------------------
    utils.QDRANT_RETRIES["_planted_scroll"] += 3
    _filtering.AGE_PARSE_FAILURES["min_age:IndexError:N/A"] += 1
    _snap = degradation.snapshot()
    check("only the counters that moved are in the snapshot",
          sorted(_snap), ["AGE_PARSE_FAILURES", "QDRANT_RETRIES"])
    check("...with their keys and counts",
          _snap["QDRANT_RETRIES"], {"_planted_scroll": 3})
    _text = "\n".join(degradation.report_lines(_snap))
    check_true("the block names the counter, its total and its key",
               "QDRANT_RETRIES" in _text and "_planted_scroll" in _text
               and "2 of" in _text)
    check_true("...and carries the line that says what a non-zero MEANS",
               "retried" in _text)
    check_true("...and no longer claims the run was clean",
               "CLEAN" not in _text)

    _totals, _out = quiet(degradation.log_summary, _snap)
    check("the event's totals are per counter, summed over its keys",
          _totals, {"QDRANT_RETRIES": 3, "AGE_PARSE_FAILURES": 1})
    check_true("the event is emitted with status=degraded and the grand total",
               '"status": "degraded"' in _out and '"total": 4' in _out)
    check_true("...and the totals ride in the allowlisted field",
               '"degradation_totals"' in _out and '"QDRANT_RETRIES": 3' in _out)
    # THE KEYS MUST NOT REACH THE DURABLE RECORD. SEX_UNKNOWN_KEPT is keyed by
    # the patient's recorded sex and M_CATEGORY_UNREADABLE by observation text;
    # the event carries counter NAMES only, and this is what says so.
    check_true("the event carries NO counter KEY, only counter names",
               "_planted_scroll" not in _out
               and "min_age:IndexError" not in _out)
    check_true("...and nothing was dropped by the field allowlist, so the "
               "field is genuinely allowed (non-degeneracy)",
               '"dropped_fields"' not in _out)

    # --- THE CONTROL: a counter with no reader is caught -------------------
    # Not "does the report print", but "would the report NOTICE a counter that
    # was left out of the registry". A counter removed from the registry moves
    # and the report stays silent, which is precisely the pre-pass state.
    _removed = degradation._REGISTRY.pop("QDRANT_RETRIES")
    try:
        _silent = "\n".join(degradation.report_lines(degradation.snapshot()))
        check("a counter removed from the registry moves and is NOT reported "
              "(the pre-pass state, reproduced)",
              "QDRANT_RETRIES" in _silent, False)
        check_true("...while the counter itself is genuinely non-zero, so the "
                   "silence is the registry's and not the counter's",
                   sum(_removed.values()) == 3)
    finally:
        degradation._REGISTRY["QDRANT_RETRIES"] = _removed

    check_true("the registry was restored",
               "QDRANT_RETRIES" in degradation.registered_names())

    # A DUPLICATE REGISTRATION RAISES rather than shadowing.
    _dup = None
    try:
        degradation.register("QDRANT_RETRIES", utils.QDRANT_RETRIES, "x")
    except ValueError as _exc:
        _dup = str(_exc)
    check_true("registering a name twice raises and names it",
               _dup is not None and "QDRANT_RETRIES" in _dup)
finally:
    degradation.clear_all()
    for _name, _values in _saved_registered.items():
        degradation._REGISTRY[_name].update(_values)

check("every registered counter was restored to what it was",
      {n: dict(degradation._REGISTRY[n]) for n in _saved_registered},
      _saved_registered)


#------------------------------------------------------------------------------


# ===========================================================================
# 6. load_results: A CORRUPT FILE IS COUNTED, PRESERVED AND SURVIVED
# ===========================================================================
#
# THE FULL CONSEQUENCE IS WHAT IS TESTED, not just the return value: the old
# code returned [] silently, and the NEXT append_result then replaced the
# unreadable file with a one-entry list. So the assertion that matters is the
# LAST one in each block -- that the preserved sidecar is still intact after a
# write has landed.
#
# The checkpoint path is redirected through paths._RESOLVED, the seam
# tests/test_ablation_db_isolation.py already uses, and restored afterwards.

print("\n" + "=" * 74)
print("6. load_results: could-not-load is distinguished, counted and preserved")
print("=" * 74)

_RESULTS_SCRATCH = tempfile.mkdtemp(prefix="oncotriage-results-")
_paths_had = "checkpoint_path" in paths._RESOLVED
_paths_was = paths._RESOLVED.get("checkpoint_path")
try:
    paths._RESOLVED["checkpoint_path"] = _RESULTS_SCRATCH + os.sep
    _rp = _runner._results_path()
    check_true("the results path is inside the scratch directory (isolation)",
               str(_rp).startswith(_RESULTS_SCRATCH))

    # --- (a) NO FILE: unchanged, and ok ------------------------------------
    _loaded = _runner.load_results()
    check("no results file yields an empty list", list(_loaded), [])
    check("...and ok=True, because 'nothing to resume' is not a failure",
          _loaded.ok, True)

    # --- (b) A GENUINELY EMPTY RESULT SET: still reports as before ---------
    with open(_rp, "w") as _fh:
        json.dump([], _fh)
    _loaded = _runner.load_results()
    check("an empty results FILE also yields an empty list", list(_loaded), [])
    check("...and is ok, which is what separates it from a corrupt file",
          _loaded.ok, True)
    check("...and records no failure", dict(_runner.RESULTS_FILE_FAILURES), {})

    # --- (c) A POPULATED FILE: unchanged -----------------------------------
    _prior = [{"patient_id": f"p{i}", "status": "success", "total_time": 1.0,
               "eligible_matches": 1, "is_resample": False} for i in range(5)]
    with open(_rp, "w") as _fh:
        json.dump(_prior, _fh)
    _loaded = _runner.load_results()
    check("a populated file round-trips", list(_loaded), _prior)
    check("...and is ok", _loaded.ok, True)

    # --- (d) A CORRUPT FILE ------------------------------------------------
    _runner.RESULTS_FILE_FAILURES.clear()
    with open(_rp, "w") as _fh:
        _fh.write(json.dumps(_prior)[:len(json.dumps(_prior)) // 2])
    _corrupt_bytes = open(_rp, "rb").read()
    _loaded, _out = quiet(_runner.load_results)

    check("a corrupt file yields an empty list", list(_loaded), [])
    check("...and ok=False, which is the whole return-contract change",
          _loaded.ok, False)
    check_true("...naming the exception, like load_checkpoint's warning does",
               "JSONDecodeError" in (_loaded.error or ""))
    check("...and it is COUNTED",
          dict(_runner.RESULTS_FILE_FAILURES),
          {"load:JSONDecodeError": 1})
    check_true("...and logged as a structured event",
               '"event": "results_load_failed"' in _out)
    check_true("...and the console warning names the preserved file",
               "WARNING" in _out and ".corrupt" in _out)

    check_true("the unreadable file was renamed to a .corrupt sidecar",
               _loaded.preserved_path
               and os.path.exists(_loaded.preserved_path))
    check("...and the sidecar holds the ORIGINAL bytes",
          open(_loaded.preserved_path, "rb").read(), _corrupt_bytes)
    check_true("...and the results path itself is now gone",
               not os.path.exists(_rp))

    # THE ASSERTION THE WHOLE ITEM EXISTS FOR: the next write cannot destroy it.
    _runner.append_result(_loaded, {"patient_id": "new-1", "status": "success",
                                    "total_time": 1.0, "eligible_matches": 0,
                                    "is_resample": False})
    check_true("after the next append_result the results file exists again",
               os.path.exists(_rp))
    check("...holding only this session's entry",
          [e["patient_id"] for e in json.load(open(_rp))], ["new-1"])
    check("...and the preserved sidecar is STILL the original bytes",
          open(_loaded.preserved_path, "rb").read(), _corrupt_bytes)

    # --- (e) A SECOND CORRUPTION DOES NOT OVERWRITE THE FIRST SIDECAR ------
    _first_sidecar = _loaded.preserved_path
    with open(_rp, "w") as _fh:
        _fh.write("{ also not json")
    _loaded2, _ = quiet(_runner.load_results)
    check_true("a second corruption is preserved under a different name",
               _loaded2.preserved_path
               and _loaded2.preserved_path != _first_sidecar)
    check("...and the first sidecar is untouched",
          open(_first_sidecar, "rb").read(), _corrupt_bytes)

    # --- (f) A PARSEABLE NON-ARRAY IS ALSO UNREADABLE ----------------------
    _runner.RESULTS_FILE_FAILURES.clear()
    with open(_rp, "w") as _fh:
        json.dump({"patient_id": "not a list"}, _fh)
    _loaded3, _ = quiet(_runner.load_results)
    check("a JSON object where an array was expected is not loadable",
          (list(_loaded3), _loaded3.ok), ([], False))
    check("...and is counted under its own phase, not as a decode error",
          dict(_runner.RESULTS_FILE_FAILURES), {"shape:dict": 1})

    # --- (g) THE SUMMARY SAYS SO ------------------------------------------
    _, _summary = quiet(_runner.print_summary, _loaded3, 12.0, db_path=_DB,
                        degradation_snapshot={})
    check_true("the summary states that prior results were not loaded",
               "PRIOR RESULTS WERE NOT LOADED" in _summary)
    check_true("...and warns that the statistics below cover this session only",
               "THIS SESSION'S PATIENTS" in _summary)
    check_true("...and says the checkpoint was unaffected",
               "checkpoint was NOT affected" in _summary
               or "CHECKPOINT IS UNAFFECTED" in _summary)
    check_true("...and the all-zero degradation block is in the same summary",
               "DEGRADATION COUNTERS" in _summary and "CLEAN" in _summary)

    # THE CONTROL: a clean load must NOT print the caveat, or the caveat means
    # nothing.
    _, _clean_summary = quiet(_runner.print_summary,
                              _runner.ResultsLoad(_prior), 12.0, db_path=_DB)
    check("a summary over loadable results does NOT carry the caveat",
          "PRIOR RESULTS WERE NOT LOADED" in _clean_summary, False)
    check("...and with no snapshot passed, no degradation block is printed",
          "DEGRADATION COUNTERS" in _clean_summary, False)

    # --- (g2) A PRESERVE THAT FAILS IS SAID SO, NOT SWALLOWED --------------
    #
    # THE CONDITION IS CREATED FOR REAL: the directory holding the results file
    # is made unwritable, so os.rename genuinely raises EACCES. No source is
    # patched and no exception is faked -- this is the shape
    # tests/test_storage_write_durability.py uses for the same reason.
    #
    # Skipped rather than asserted when the process can rename anyway (running
    # as root defeats the mode bits), and the skip is a PRINTED line plus a
    # recorded check that the precondition held, never a silent pass.
    _runner.RESULTS_FILE_FAILURES.clear()
    with open(_rp, "w") as _fh:
        _fh.write("not json either")
    _mode_was = os.stat(_RESULTS_SCRATCH).st_mode
    os.chmod(_RESULTS_SCRATCH, 0o500)
    try:
        _rename_blocked = True
        try:
            os.rename(_rp, _rp.with_name(_rp.name + ".probe"))
            os.rename(_rp.with_name(_rp.name + ".probe"), _rp)
            _rename_blocked = False
        except OSError:
            pass
        check("the read-only directory genuinely blocks a rename "
              "(non-degeneracy: without this the branch below is untested)",
              _rename_blocked, True)
        if _rename_blocked:
            _loaded4, _out4 = quiet(_runner.load_results)
            check("a file that cannot be preserved still reports ok=False",
                  _loaded4.ok, False)
            check("...and reports NO preserved path rather than a false one",
                  _loaded4.preserved_path, None)
            check_true("...and the failure to preserve is counted separately "
                       "from the failure to load",
                       any(k.startswith("preserve:") for k
                           in _runner.RESULTS_FILE_FAILURES)
                       and any(k.startswith("load:") for k
                               in _runner.RESULTS_FILE_FAILURES))
            check_true("...and the console says the next write WILL destroy it",
                       "WILL overwrite" in _out4)
    finally:
        os.chmod(_RESULTS_SCRATCH, _mode_was)

    # --- (h) THE CHECKPOINT IS UNTOUCHED, asserted rather than claimed -----
    _runner.save_checkpoint({"patient-a", "patient-b"})
    with open(_rp, "w") as _fh:
        _fh.write("still not json")
    quiet(_runner.load_results)
    check("a corrupt results file leaves the checkpoint intact, so nothing "
          "is re-run because of it",
          _runner.load_checkpoint(), {"patient-a", "patient-b"})
finally:
    if _paths_had:
        paths._RESOLVED["checkpoint_path"] = _paths_was
    else:
        paths._RESOLVED.pop("checkpoint_path", None)
    shutil.rmtree(_RESULTS_SCRATCH, ignore_errors=True)
    _runner.RESULTS_FILE_FAILURES.clear()

check("the checkpoint path resolver was restored",
      "checkpoint_path" in paths._RESOLVED, _paths_had)


#------------------------------------------------------------------------------


# ===========================================================================
# SUMMARY
# ===========================================================================

shutil.rmtree(_SCRATCH, ignore_errors=True)

print("\n" + "=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFAILURES:")
    for _failure in _FAILURES:
        print(f"  - {_failure}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 10 2026

@author: ramyalsaffar
"""
