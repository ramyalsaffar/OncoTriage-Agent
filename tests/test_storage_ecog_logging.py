# ECOG Logging Test
###################

"""
ECOG Logging Test

Covers carrying the parsed ECOG performance status through to the stored row.

The gap: File 07 surfaces ecog_performance_status and File 13 prints the score
into the Stage 5 prompt, so ECOG moves the verdict directly -- ECOG 0-1 or 0-2
gates nearly every interventional oncology trial. But log_inference() recorded
none of it. A corpus whose observations all postdate DATA_SNAPSHOT_DATE would
resolve to all_after_reference_date for every patient, match systematically
worse, and leave nothing in inferences.db to explain it.

Three nullable columns now carry it: ecog_value, ecog_selection,
ecog_observations_found.

THE NULL CONVENTION, which is what most of this file is about:

    ecog_value IS NULL                  ambiguous on its own. It is the value
                                        for a pre-migration row AND for a
                                        patient with no observation AND for a
                                        patient whose observations were all
                                        unusable.
    ecog_selection IS NULL              the row predates the migration, or the
                                        result did not come from a terminal
                                        node. Nothing is known.
    ecog_selection = 'none_recorded'    the patient genuinely had no ECOG.
    ecog_selection = 'all_after_reference_date' | 'undated_ambiguous'
                                        observations existed but none was
                                        usable; ecog_observations_found says
                                        how many.

    Absence is `ecog_selection = 'none_recorded'`, NEVER `ecog_value IS NULL`.
    And ecog_value = 0 is a real, fully-active patient -- the most eligible
    score there is -- so it must never be read as missing either.

Covers:
    1. _pipeline_provenance (File 13) reads ECOG off state["patient_data"] --
       the same route birth_date_precision takes -- so nothing is duplicated
       onto state, and the value is present on every path including the error
       path.
    2. All three terminal results stay key-identical after the addition.
    3. A fresh database gets the three columns from CREATE TABLE; a
       pre-existing database gets them from the ALTER TABLE migration, and the
       rows written before it keep NULL in all three.
    4. log_inference writes all three, including the two values a truthiness
       test would destroy: ECOG 0 and observations_found 0.
    5. Absence, present-but-unusable, and never-reported are three
       distinguishable states in the stored row.
    6. ablation_results gains none of these columns -- ECOG is a patient-level
       property, constant across configurations.
    7. END TO END on a real scratch bundle from '04- FHIR Generate Data.py':
       parsed, run through the provenance builder, logged, and read back.

No network and no LLM. The pipeline is never run: terminal nodes are called
directly on a stubbed state, and every write passes an explicit db_path at a
temporary file, so the real inferences.db is never opened.

Run from terminal (or F5 in Spyder):
    python tests/test_storage_ecog_logging.py
    (was: python "40- ECOG Logging Test.py")

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# PASS 20d-1: THIS FILE IMPORTS THE PACKAGE. It used to exec "01- Imports.py"
# and "02- Utility Functions.py" into its own globals and then exec_chain()
# Files 13 and 07, which is how every name below used to arrive.
#
# THE STORAGE LAYER IS IMPORTED HERE, NOT EXEC'D LATER, for the reason given at
# the throwaway-database block below. The explicit initialize_database() call
# item 20b made necessary stays exactly where it was.
#
# THE CANDIDATE DIRECTORY IS THE PARENT OF THIS FILE'S, not this file's own.
# The same block Files 47, 48 and 49 carry looks one level up because this file
# now sits in tests/ and the package sits BESIDE tests/, not inside it.
# `pip install -e .` makes the whole block a no-op.
import os
import sqlite3
import sys
from pathlib import Path

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

from oncotriage.ablation import study as _ablation_study
from oncotriage.agent import terminal as _agent_terminal
from oncotriage.agent.terminal import (
    _pipeline_provenance,
    node_error_handler,
    node_finalize,
    node_no_candidates,
)
from oncotriage.config import DATA_SNAPSHOT_DATE
from oncotriage.fhir.parser import parse_fhir_bundle
from oncotriage.paths import data_patient_path, inferences_path
from oncotriage.storage.database_logger import (
    INFERENCE_COLUMN_ADDITIONS,
    initialize_database,
    log_inference,
    resolve_inference_db_path,
)


#------------------------------------------------------------------------------


import ast
import shutil
import tempfile
import textwrap


_SCRATCH_FHIR_DIR = data_patient_path + "scratch_ecog/fhir/"


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
# THROWAWAY DATABASE
# ===========================================================================
# TWO INDEPENDENT MECHANISMS KEEP THIS TEST OFF THE PRODUCTION DATABASE, and the
# second arrived in pass 20c-2b because the first stopped being enough on its
# own.
#
#   1. RETIRED IN PASS 20d-1 rather than left looking live. It was: rebind
#      inferences_path, then exec "14- Database Logger.py" into this namespace
#      so the shim's log_inference wrapper picked the rebound value up through
#      globals().get("inferences_path"). That worked only while this file was
#      part of the exec chain; it now imports the package module directly, so
#      the rebinding below reaches the writer NOT AT ALL.
#   2. logged_row() calls log_inference with db_path EXPLICITLY and asserts on
#      the path the writer reports back, which depends on no seam at all. This
#      is the whole protection now, and the discrimination block below is what
#      keeps it from being circular.
#
# Item 20b: File 14 no longer creates its tables at load time, so the rebinding
# was never sufficient on its own -- initialize_database() has to be called.
# Section 3 below reads PRAGMA table_info(inferences) straight after this block
# and used to be served by File 14's import side effect; without the explicit
# call it saw a database with no tables and reported three empty-set failures.
# That is the reliance item 20b exists to remove, so the call is made here
# rather than the side effect restored. initialize_database has always taken the
# path as an argument, which is why it needed no change in pass 2b.

_PRODUCTION_INFERENCES_PATH = inferences_path

_TMP_DIR = tempfile.mkdtemp(prefix="oncotriage_ecog_logging_")
inferences_path = os.path.join(_TMP_DIR, "inferences_test.db")

initialize_database(inferences_path)


# --- THE DATABASE-ISOLATION ASSERTION IS SHOWN TO DISCRIMINATE --------------
# CLAUDE.md: an assertion that has only ever passed is not evidence that it can
# catch anything. resolve_inference_db_path(None) is what a caller that forgot
# db_path gets. It RESOLVES without connecting, so this control names the hazard
# without going near the production file.
_PACKAGE_DEFAULT_DB = resolve_inference_db_path(None)

print("\n" + "=" * 70)
print("0. the database-isolation assertion can fail")
print("=" * 70)
check("the scratch path is non-empty (non-degeneracy)",
      bool(inferences_path) and inferences_path.endswith(".db"), True)
check("the production path is non-empty (non-degeneracy)",
      bool(_PRODUCTION_INFERENCES_PATH), True)
check("omitting db_path resolves to the PRODUCTION database",
      os.path.abspath(_PACKAGE_DEFAULT_DB),
      os.path.abspath(_PRODUCTION_INFERENCES_PATH))
check("...which is NOT this test's scratch database, so passing db_path is "
      "doing real work and the checks below can fail",
      os.path.abspath(_PACKAGE_DEFAULT_DB) == os.path.abspath(inferences_path),
      False)
check("...and passing db_path resolves to exactly what was passed",
      resolve_inference_db_path(inferences_path), inferences_path)


# ===========================================================================
# FIXTURES
# ===========================================================================

def make_ecog(value=None, selection="none_recorded", found=0,
              on_or_before=0, after=0, undated=0, date=None):
    """An ecog_performance_status dict of the shape File 07 produces."""
    return {
        "value": value,
        "date": date,
        "value_shape": "valueInteger" if value is not None else None,
        "unit": None,
        "observations_found": found,
        "observations_on_or_before_reference": on_or_before,
        "observations_after_reference": after,
        "observations_undated": undated,
        "selection": selection,
        "reference_date": DATA_SNAPSHOT_DATE,
    }


ECOG_SCORED_ONE = make_ecog(value=1, selection="most_recent_on_or_before_reference_date",
                            found=1, on_or_before=1, date="2024-06-15")
ECOG_SCORED_ZERO = make_ecog(value=0, selection="most_recent_on_or_before_reference_date",
                             found=1, on_or_before=1, date="2023-01-09")
ECOG_ABSENT = make_ecog()                                   # none_recorded, found 0
ECOG_UNUSABLE = make_ecog(selection="all_after_reference_date", found=2, after=2)


def make_patient(ecog=None, patient_id="ecog-logging-patient"):
    """Parsed-patient dict of the shape File 07 returns."""
    patient = {
        "patient_id": patient_id,
        "demographics": {"age": 62, "sex": "male", "race": "White",
                         "ethnicity": "Not Hispanic or Latino",
                         "birth_date": "1963-04-12",
                         "birth_date_precision": "day"},
        "conditions": [{"code": "254637007",
                        "display": "Non-small cell lung cancer",
                        "verification_status": "confirmed"}],
        "medications": [],
        "allergies": [],
        "observations": [],
        "procedures": [],
        "cancer_stage_observations": [],
        "cancer_genomic_variants": [],
    }
    if ecog is not None:
        patient["ecog_performance_status"] = ecog
    return patient


def make_terminal_state(patient, **overrides):
    """State as LangGraph hands it to a terminal node."""
    state = {
        "patient_data":                    patient,
        "expanded_query":                  "lung neoplasms",
        "hybrid_results":                  [],
        "bm25_retrieved":                  0,
        "vector_retrieved":                0,
        "reranked_trials":                 [],
        "filtered_trials":                 [],
        "candidates_after_rule_filter":    0,
        "candidates_after_quality_filter": 0,
        "mesh_dropped":                    0,
        "mesh_resolution":                 "snomed_cui_mesh",
        "stage_dropped":                   0,
        "histology_dropped":               0,
        "evaluations":                     [],
        "gpt4o_retries":                   0,
        "cross_vocab_remaps":              0,
        "gpt4o_prompt":                    "",
        "gpt4o_input_tokens":              0,
        "gpt4o_output_tokens":             0,
        "expansion_prompt":                "",
        "expansion_input_tokens":          0,
        "expansion_output_tokens":         0,
        "stage_timings":                   {"query_expansion": 0.01},
        "error":                           "",
        "ablation_flags":                  {},
    }
    state.update(overrides)
    return state


TERMINAL_NODES = {
    "node_finalize":      node_finalize,
    "node_no_candidates": node_no_candidates,
    "node_error_handler": node_error_handler,
}

ECOG_KEYS = ("ecog_value", "ecog_selection", "ecog_observations_found")


def logged_row(result, patient, patient_id):
    """log_inference() the pair, then read the row back.

    db_path is passed explicitly, and the path log_inference reports back is
    checked against the scratch database on EVERY call rather than once at
    startup: this helper is the only writer in the file, so one assertion here
    covers every row it produces.
    """
    result = dict(result)
    result["patient_id"] = patient_id
    result.setdefault("timestamp", "2026-03-11T00:00:00")
    check(f"{patient_id}: logged into the scratch database, not production",
          log_inference(result, patient, db_path=inferences_path),
          inferences_path)

    conn = sqlite3.connect(inferences_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM inferences WHERE patient_id = ? ORDER BY id DESC LIMIT 1",
        (patient_id,)
    ).fetchone()
    conn.close()
    return row


print("\n" + "=" * 70)
print("ECOG LOGGING TEST")
print("=" * 70)


# ===========================================================================
# 1. REACHABILITY -- _pipeline_provenance reads ECOG off state["patient_data"]
# ===========================================================================

print("\n" + "=" * 70)
print("1. _pipeline_provenance reaches ECOG through state['patient_data']")
print("=" * 70)

_prov_scored = _pipeline_provenance(make_terminal_state(make_patient(ECOG_SCORED_ONE)))
check("value carried", _prov_scored["ecog_value"], 1)
check("selection carried", _prov_scored["ecog_selection"],
      "most_recent_on_or_before_reference_date")
check("count carried", _prov_scored["ecog_observations_found"], 1)

# ECOG 0 is the value a truthiness test destroys, and it is the most eligible
# score a patient can have.
_prov_zero = _pipeline_provenance(make_terminal_state(make_patient(ECOG_SCORED_ZERO)))
check("ECOG 0 survives provenance as 0", _prov_zero["ecog_value"], 0)
check("and is not None", _prov_zero["ecog_value"] is None, False)

_prov_absent = _pipeline_provenance(make_terminal_state(make_patient(ECOG_ABSENT)))
check("absent patient: value None", _prov_absent["ecog_value"], None)
check("absent patient: selection says so", _prov_absent["ecog_selection"], "none_recorded")
check("absent patient: count 0 not None", _prov_absent["ecog_observations_found"], 0)

_prov_unusable = _pipeline_provenance(make_terminal_state(make_patient(ECOG_UNUSABLE)))
check("unusable: value None", _prov_unusable["ecog_value"], None)
check("unusable: selection names the reason",
      _prov_unusable["ecog_selection"], "all_after_reference_date")
check("unusable: count is non-zero", _prov_unusable["ecog_observations_found"], 2)

# A hand-built patient dict with no ECOG key at all -- never reported.
_prov_missing = _pipeline_provenance(make_terminal_state(make_patient(None)))
for _k in ECOG_KEYS:
    check(f"patient dict without the field: {_k} is None", _prov_missing[_k], None)
check("never-reported is distinguishable from none_recorded",
      _prov_missing["ecog_selection"] == _prov_absent["ecog_selection"], False)

# Reading state["patient_data"] rather than a copied-onto-state value is what
# makes the error path work: no node has to remember to propagate it.
# RETARGETED IN PASS 20c-2c. _pipeline_provenance moved to
# oncotriage/agent/terminal.py; "13- LangGraph Agent.py" is a re-export shim.
#
# THIS ONE WOULD NOT HAVE GONE SILENTLY GREEN, and it is worth saying which way
# it fails: `next(... if n.name == "_pipeline_provenance")` raises StopIteration
# against a file that does not define it, which ABORTS the run at this line.
# Loud, but it takes the remaining sections down with it. The other check in
# this pair -- `'state.get("ecog_value")' in _prov_body` expecting False -- is
# the one that would have passed on an empty body, which is why the
# non-degeneracy check below is here anyway.
_prov_src = inspect_source = None
# PASS 20d-1: the path comes from the imported module's own __file__ rather than
# from os.path.join(_code_dir, ...), which was correct only while this file sat
# beside the package.
_PROVENANCE_SOURCE = os.path.abspath(_agent_terminal.__file__)
_prov_text = Path(_PROVENANCE_SOURCE).read_text(encoding="utf-8")
_prov_node = next((n for n in ast.parse(_prov_text).body
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "_pipeline_provenance"), None)
check("the parsed agent source defines _pipeline_provenance", _prov_node is not None, True)
_prov_body = ast.get_source_segment(_prov_text, _prov_node) if _prov_node else ""
# NON-DEGENERATE: the "does not contain" check below is satisfied by an empty
# string, which is exactly what a stale filename would leave here.
check("...and its source body is substantial (non-degeneracy)",
      len(_prov_body) > 2000, True)
check("provenance reads ecog off patient_data, not off state directly",
      'state.get("ecog_value")' in _prov_body, False)
check("and it does read patient_data",
      'ecog_performance_status' in _prov_body, True)


# ===========================================================================
# 2. ALL THREE TERMINAL RESULTS CARRY THE KEYS, IDENTICALLY
# ===========================================================================

print("\n" + "=" * 70)
print("2. All three terminal nodes emit the ECOG keys")
print("=" * 70)

_state = make_terminal_state(make_patient(ECOG_SCORED_ONE))
_terminal_results = {name: fn(_state)["result"] for name, fn in TERMINAL_NODES.items()}

for _name, _res in _terminal_results.items():
    for _k in ECOG_KEYS:
        check(f"{_name}: declares {_k}", _k in _res, True)
    check(f"{_name}: value is the parsed score", _res["ecog_value"], 1)
    check(f"{_name}: selection is the parsed path", _res["ecog_selection"],
          "most_recent_on_or_before_reference_date")

# The contract File 36 guards, restated for the keys added here: no terminal
# node may know about an ECOG key the others do not.
_key_sets = {n: set(r) for n, r in _terminal_results.items()}
_core = set.intersection(*_key_sets.values())
for _k in ECOG_KEYS:
    check(f"{_k} is in the shared contract of all three nodes", _k in _core, True)


# ===========================================================================
# 3. SCHEMA -- CREATE TABLE on a fresh DB, ALTER TABLE on an existing one
# ===========================================================================

print("\n" + "=" * 70)
print("3. Schema: fresh create and migration of a pre-existing database")
print("=" * 70)

_conn = sqlite3.connect(inferences_path)
_fresh_cols = {r[1] for r in _conn.execute("PRAGMA table_info(inferences)")}
_conn.close()
for _k in ECOG_KEYS:
    check(f"fresh database has inferences.{_k}", _k in _fresh_cols, True)

# Migration path. Build a database holding an inferences table WITHOUT the three
# columns, write a row into it, then run File 14's migration loop over it and
# confirm the columns arrive and the pre-existing row keeps NULL.
_legacy_path = os.path.join(_TMP_DIR, "legacy.db")
_legacy = sqlite3.connect(_legacy_path)
_legacy.execute("""
    CREATE TABLE inferences (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        age INTEGER
    )
""")
_legacy.execute("INSERT INTO inferences (patient_id, timestamp, age) "
                "VALUES ('pre-migration', '2026-01-01', 60)")
_legacy.commit()

_legacy_cols_before = {r[1] for r in _legacy.execute("PRAGMA table_info(inferences)")}
check("legacy database starts without the ECOG columns",
      sorted(set(ECOG_KEYS) & _legacy_cols_before), [])

_legacy_cursor = _legacy.cursor()
_existing = {r[1] for r in _legacy_cursor.execute("PRAGMA table_info(inferences)")}
for _column, _sql_type in INFERENCE_COLUMN_ADDITIONS.items():
    if _column not in _existing:
        _legacy_cursor.execute(
            f"ALTER TABLE inferences ADD COLUMN {_column} {_sql_type}")
_legacy.commit()

_legacy_cols_after = {r[1] for r in _legacy.execute("PRAGMA table_info(inferences)")}
for _k in ECOG_KEYS:
    check(f"migration adds inferences.{_k}", _k in _legacy_cols_after, True)
check("the ECOG columns are declared in INFERENCE_COLUMN_ADDITIONS",
      sorted(k for k in ECOG_KEYS if k in INFERENCE_COLUMN_ADDITIONS),
      sorted(ECOG_KEYS))

_legacy.row_factory = sqlite3.Row
_pre = _legacy.execute(
    "SELECT * FROM inferences WHERE patient_id = 'pre-migration'").fetchone()
for _k in ECOG_KEYS:
    check(f"pre-migration row keeps NULL in {_k}", _pre[_k], None)
_legacy.close()

check("declared SQL types",
      [INFERENCE_COLUMN_ADDITIONS[k] for k in ECOG_KEYS],
      ["INTEGER", "TEXT", "INTEGER"])


# ===========================================================================
# 4. log_inference WRITES THE THREE COLUMNS
# ===========================================================================

print("\n" + "=" * 70)
print("4. log_inference writes value, selection and count")
print("=" * 70)

_row_scored = logged_row(_terminal_results["node_finalize"],
                         make_patient(ECOG_SCORED_ONE), "row-scored")
check("logged ecog_value", _row_scored["ecog_value"], 1)
check("logged ecog_selection", _row_scored["ecog_selection"],
      "most_recent_on_or_before_reference_date")
check("logged ecog_observations_found", _row_scored["ecog_observations_found"], 1)

# ECOG 0 and count 0 are both falsy and both real. An `or` chain anywhere on
# this path would turn them into NULL.
_zero_result = node_finalize(make_terminal_state(make_patient(ECOG_SCORED_ZERO)))["result"]
_row_zero = logged_row(_zero_result, make_patient(ECOG_SCORED_ZERO), "row-zero")
check("ECOG 0 is stored as 0, not NULL", _row_zero["ecog_value"], 0)
check("and is not NULL", _row_zero["ecog_value"] is None, False)

_absent_result = node_finalize(make_terminal_state(make_patient(ECOG_ABSENT)))["result"]
_row_absent = logged_row(_absent_result, make_patient(ECOG_ABSENT), "row-absent")
check("absent patient: value NULL", _row_absent["ecog_value"], None)
check("absent patient: selection is none_recorded",
      _row_absent["ecog_selection"], "none_recorded")
check("absent patient: count stored as 0, not NULL",
      _row_absent["ecog_observations_found"], 0)

_unusable_result = node_finalize(make_terminal_state(make_patient(ECOG_UNUSABLE)))["result"]
_row_unusable = logged_row(_unusable_result, make_patient(ECOG_UNUSABLE), "row-unusable")
check("unusable: value NULL", _row_unusable["ecog_value"], None)
check("unusable: selection names the reason",
      _row_unusable["ecog_selection"], "all_after_reference_date")
check("unusable: count survives", _row_unusable["ecog_observations_found"], 2)

# A result that never came from a terminal node, logged against a patient dict
# with no ECOG field: nothing is known, so all three stay NULL.
_row_unreported = logged_row({"stage_timings": {}}, make_patient(None), "row-unreported")
for _k in ECOG_KEYS:
    check(f"never-reported row stores NULL in {_k}", _row_unreported[_k], None)

# Fallback: a result dict without the keys, but a patient dict that has them.
_row_fallback = logged_row({"stage_timings": {}},
                           make_patient(ECOG_SCORED_ONE), "row-fallback")
check("falls back to the patient dict for value", _row_fallback["ecog_value"], 1)
check("falls back for selection", _row_fallback["ecog_selection"],
      "most_recent_on_or_before_reference_date")


# ===========================================================================
# 5. THE NULL CONVENTION -- three distinguishable states
# ===========================================================================

print("\n" + "=" * 70)
print("5. Absence, unusable and never-reported are distinguishable")
print("=" * 70)

check("all three have ecog_value NULL",
      [_row_absent["ecog_value"], _row_unusable["ecog_value"],
       _row_unreported["ecog_value"]], [None, None, None])
check("but ecog_selection separates them",
      len({_row_absent["ecog_selection"], _row_unusable["ecog_selection"],
           _row_unreported["ecog_selection"]}), 3)

# The query the convention prescribes, run against exactly the four rows written
# above -- scoped by patient_id so later sections cannot shift the counts.
#
#   row-scored     ECOG 1, most_recent_on_or_before_reference_date
#   row-zero       ECOG 0, most_recent_on_or_before_reference_date
#   row-absent     no observation, none_recorded
#   row-unusable   2 observations, all_after_reference_date
#   row-unreported nothing known, selection NULL
_CONVENTION_ROWS = ("row-scored", "row-zero", "row-absent",
                    "row-unusable", "row-unreported")
_placeholders = ",".join("?" * len(_CONVENTION_ROWS))

_conn = sqlite3.connect(inferences_path)
_count = lambda where: _conn.execute(
    f"SELECT COUNT(*) FROM inferences WHERE patient_id IN ({_placeholders}) AND {where}",
    _CONVENTION_ROWS
).fetchone()[0]

_genuinely_absent = _count("ecog_selection = 'none_recorded'")
_value_is_null    = _count("ecog_value IS NULL")
_scored           = _count("ecog_value IS NOT NULL")
_never_reported   = _count("ecog_selection IS NULL")
_conn.close()

check("counting absence by ecog_value IS NULL over-counts",
      _value_is_null, 3)                    # absent + unusable + unreported
check("counting it by selection = 'none_recorded' is exact", _genuinely_absent, 1)
check("never-reported is its own state", _never_reported, 1)
check("ECOG 0 is counted as scored, not as missing",
      _scored, 2)                           # row-scored (1) + row-zero (0)


# ===========================================================================
# 6. ablation_results IS UNCHANGED
# ===========================================================================

print("\n" + "=" * 70)
print("6. ablation_results gains none of these columns")
print("=" * 70)

# ECOG is a patient-level property, constant across configurations, so it has no
# place on a per-configuration table. The schema is read as SOURCE, never
# executed: executing it would open the real ablation_results.db.
#
# IT POINTS AT THE PACKAGE MODULE, NOT AT FILE 26 (item 20c, pass 3d). File 26
# is a thin entry point now -- a `__main__` block and one import -- so the
# CREATE TABLE lives in oncotriage/ablation/study.py. Reading the entry point
# would find no schema at all and `split(...)[1]` would raise IndexError, which
# is exactly what it did the first time this suite ran after the move: a
# structural check aimed at a file that no longer holds the thing under test
# CANNOT PASS VACUOUSLY, but it also cannot report the defect it exists for.
# Same retargeting, for the same reason, as Files 38, 39, 42 and 43, which all
# read package modules rather than the shims over them.
#
# The path is asserted to exist before it is read, so a future move produces a
# named failure here rather than a FileNotFoundError thirty lines of traceback
# deep.
# PASS 20d-1: the path comes from the imported module's own __file__. The
# `is_file()` guard below stays -- it costs nothing and it is what turns a
# future move into a named failure -- but a module's own __file__ is not a guess
# that can be one directory off, which is what the previous _code_dir form
# became the moment this file was moved into tests/.
_ablation_source = Path(os.path.abspath(_ablation_study.__file__))
check("the ablation schema module is where this check expects it",
      _ablation_source.is_file(), True)
_ablation_text = _ablation_source.read_text(encoding="utf-8")
check("...and it actually carries the ablation_results CREATE TABLE "
      "(non-degeneracy: a file without it would make every check below vacuous)",
      "CREATE TABLE IF NOT EXISTS ablation_results (" in _ablation_text, True)
_ablation_create = _ablation_text.split("CREATE TABLE IF NOT EXISTS ablation_results (", 1)[1]
_ablation_create = _ablation_create.split(")", 1)[0]

for _k in ECOG_KEYS:
    check(f"ablation_results CREATE TABLE has no {_k}", _k in _ablation_create, False)
check("the ablation study module mentions no ECOG column anywhere",
      any(k in _ablation_text for k in ECOG_KEYS), False)

# AST, not a substring search: the module's docstring says "Does NOT call
# log_inference()", which a grep reads as a call site. Only real Call nodes count.
_ablation_calls = {
    n.func.id
    for n in ast.walk(ast.parse(_ablation_text))
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
}
check("and the ablation study never calls log_inference",
      "log_inference" in _ablation_calls, False)


# ===========================================================================
# 7. END TO END -- a real scratch bundle
# ===========================================================================

print("\n" + "=" * 70)
print("7. END TO END -- real scratch bundle, parsed, logged, read back")
print("=" * 70)

_scratch = Path(_SCRATCH_FHIR_DIR)
if not _scratch.is_dir():
    _RESULTS["failed"] += 1
    _FAILURES.append(
        f"scratch corpus not found at {_SCRATCH_FHIR_DIR}\n"
        f"          Generate it first:\n"
        f'          python "04- FHIR Generate Data.py" --population 3000 '
        f'--seed 20260803 --output-dir <data_patient_path>/scratch_ecog'
    )
    print(f"  FAIL  scratch corpus not found at {_SCRATCH_FHIR_DIR}")
else:
    _real_scored = _real_unscored = None
    for _path in sorted(_scratch.glob("*.json")):
        if _real_scored is not None and _real_unscored is not None:
            break
        _txt = _path.read_text(encoding="utf-8")
        if '"resourceType": "Patient"' not in _txt:
            continue
        _parsed = parse_fhir_bundle(str(_path))
        if _parsed.get("patient_id") is None:
            continue
        if _parsed["ecog_performance_status"]["value"] is not None:
            _real_scored = _real_scored or _parsed
        elif _parsed["ecog_performance_status"]["observations_found"] == 0:
            _real_unscored = _real_unscored or _parsed

    check("found a scored scratch patient", _real_scored is not None, True)
    check("found an unscored scratch patient", _real_unscored is not None, True)

    if _real_scored is not None:
        _real_status = _real_scored["ecog_performance_status"]
        _real_result = node_finalize(make_terminal_state(_real_scored))["result"]
        check("real patient: provenance carries the parsed value",
              _real_result["ecog_value"], _real_status["value"])

        _row = logged_row(_real_result, _real_scored, "real-scored")
        check("real patient: value round-trips through the database",
              _row["ecog_value"], _real_status["value"])
        check("real patient: selection round-trips",
              _row["ecog_selection"], _real_status["selection"])
        check("real patient: count round-trips",
              _row["ecog_observations_found"], _real_status["observations_found"])
        check("real patient: the stored value is an int in 0-4",
              isinstance(_row["ecog_value"], int) and 0 <= _row["ecog_value"] <= 4,
              True)

    if _real_unscored is not None:
        _un_result = node_finalize(make_terminal_state(_real_unscored))["result"]
        _un_row = logged_row(_un_result, _real_unscored, "real-unscored")
        check("real unscored patient: selection is none_recorded",
              _un_row["ecog_selection"], "none_recorded")
        check("real unscored patient: value NULL", _un_row["ecog_value"], None)
        check("real unscored patient: count 0",
              _un_row["ecog_observations_found"], 0)

    # The error path must carry ECOG too -- it is the path where a run failed
    # and the operator most needs to know what the model was told.
    if _real_scored is not None:
        _err = node_error_handler(make_terminal_state(
            _real_scored, error="stubbed failure"))["result"]
        _err_row = logged_row(_err, _real_scored, "real-error-path")
        check("error path: value still logged",
              _err_row["ecog_value"], _real_scored["ecog_performance_status"]["value"])
        check("error path: selection still logged",
              _err_row["ecog_selection"],
              _real_scored["ecog_performance_status"]["selection"])


# ===========================================================================
# CLEANUP + SUMMARY
# ===========================================================================

shutil.rmtree(_TMP_DIR, ignore_errors=True)

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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug  3 2026

@author: ramyalsaffar
"""
