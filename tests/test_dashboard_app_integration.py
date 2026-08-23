# Dashboard App Integration and Null-Resilience Test
###################################################

"""
Three things nothing in this project could see before, in one file because they
are one question: DOES THE DASHBOARD RENDER.

  1. THE TAB WIRING.  ``oncotriage/dashboard/app.py`` builds its tab strip from
     one literal list and then calls ten render functions positionally. A tab
     dropped from either half, or a label edited, changed what every reader of
     this dashboard sees and failed NOTHING -- no test in the suite had ever
     called ``main()``. ``tests/test_dashboard_run_health.py`` and
     ``tests/test_dashboard_reproducibility_tab.py`` each drive ONE tab
     function, deliberately, and neither can see the wiring above them.

  2. A SPARSE ROW USED TO TAKE THE PAGE DOWN.  Every numeric cell in the Patient
     Explorer went through a bare ``int()`` / ``round()`` / f-string format, and
     most of the columns it reads are legitimately NULL. ``int(None)`` raises
     inside ``main()``, which has no handler, so ONE row cost ALL TEN TABS.
     Measured against the pre-fix module, not reasoned about -- see section 6's
     controls, which reproduce each raise in a copy.

  3. A DEGENERATE DISTRIBUTION USED TO TAKE THE PAGE DOWN.  ``_kde_curve`` in
     the Performance tab guarded ``len(scores) < 3`` and not zero variance, and
     ``scipy.stats.gaussian_kde`` raises ``numpy.linalg.LinAlgError`` on input
     whose values are all identical -- which a BM25-only fallback run, or any
     two-trial arm scoring the same, produces.

WHY ONE FILE. All three are answered by rendering, and rendering is the
expensive part: one seeded database and one harness serve every check. Splitting
them would mean three copies of the offline guard, the isolation recorder and
the scratch schema, and three chances for those copies to disagree about what
"isolated" means.

WHY NO GOLDEN SNAPSHOT. ``tests/test_dashboard_run_health.py``'s ruling,
adopted: a reference file refreshed to accommodate a change makes whatever the
code does correct by definition, and there is no BEFORE here to establish one
against. Every expectation below is computed from THE SEED -- the rows this file
inserted -- or is a named literal in the module under test, never a value read
back out of the render being checked. The tab strip is the one exception and it
is the right one: its expected value is the LITERAL LIST in ``app.py``, read by
AST, so the check is "the strip renders what the source declares" rather than
"the strip renders what it rendered last time".

RUNS, COSTS, KEYS
-----------------
No network (measured, section 7, with a control that fires), no keys, no spend,
no live Qdrant, no model load, no corpus, no git history. NOT in the collision
matrix, derived: it writes only inside a ``tempfile.mkdtemp`` it removes, and
the six repository files it READS -- app.py, nullsafe.py and the four tab
modules -- are written by neither of the suite's two writers, and are
sha256-compared at the end. It EXECS NOTHING: every plant is a COPY written to a
temp directory and imported from there, so no ``_EXEC_ALLOWLIST`` entry is
needed.
"""

import ast
import contextlib
import hashlib
import io as _io
import os
import pickle
import shutil
import socket
import sqlite3
import sys
import tempfile
import time
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from oncotriage import paths as _paths
from oncotriage.dashboard import app as _app
from oncotriage.dashboard import nullsafe as _ns
from oncotriage.dashboard.tabs import demographics as _demo
from oncotriage.dashboard.tabs import match_quality as _mq
from oncotriage.dashboard.tabs import patient_explorer as _pe
from oncotriage.dashboard.tabs import performance as _perf
from oncotriage.dashboard.tabs import trial_explorer as _te
from oncotriage.dashboard.tiers import (TRIAL_STATUS_NO_SCORE, enrich_match_tiers)
from oncotriage.storage.database_logger import initialize_database


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []
_SKIPS = []


def check(label, actual, expected):
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


def fail(label, detail):
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}\n          {detail}")


def skip(label, reason):
    """Coverage that could NOT be exercised here. NEVER counted as a pass.

    The mechanism and the argument are ``tests/test_package_invariants.py``'s
    and ``tests/test_dashboard_run_health.py``'s, adopted rather than invented:
    its own counter, its own list, and a summary line printed EVEN AT ZERO,
    because a skip count that appears only when non-zero is indistinguishable
    from a file with no skip mechanism at all.
    """
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}\n          {reason}")


def at_(sequence, index, default="(absent)"):
    """``sequence[index]`` or a named absence.

    A BARE INDEX ABORTS THE FILE, AND THIS PROJECT HAS SHIPPED THAT SHAPE TEN
    TIMES. Every plant below is designed to remove an element or make a render
    raise, so every read of a rendered list is exactly the expression that
    raises ``IndexError`` when the defect under test fires -- turning a run that
    owes a hundred recorded failures into one traceback and no summary.
    """
    try:
        return sequence[index]
    except (IndexError, KeyError):
        return default


def called(fn, *args, **kwargs):
    """``fn(*args)``, or a marker string when it raises.

    The same rule one level down: a control that makes production code raise
    must produce a VALUE that ``check`` fails on, never an exception evaluated
    inside ``check``'s own argument list.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"raised: {type(exc).__name__}: {exc}"


def digest_file(path):
    """sha256 of a file, or a NAMED non-reading -- never a raise."""
    if not os.path.exists(path):
        return "absent"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"


def is_real_digest(reading):
    """True only for a real sha256: 64 lower-case hex characters.

    Asked instead of ``!= "absent"`` because there are TWO non-readings and a
    predicate written against one of them reports the other as a real digest --
    the vacuous pass a probe exists to prevent, one sentinel over.
    """
    return (isinstance(reading, str) and len(reading) == 64
            and all(c in "0123456789abcdef" for c in reading))


_T_START = time.time()

_WATCHED = {
    "app.py": os.path.abspath(_app.__file__),
    "nullsafe.py": os.path.abspath(_ns.__file__),
    "patient_explorer.py": os.path.abspath(_pe.__file__),
    "performance.py": os.path.abspath(_perf.__file__),
    "trial_explorer.py": os.path.abspath(_te.__file__),
    "match_quality.py": os.path.abspath(_mq.__file__),
    "demographics.py": os.path.abspath(_demo.__file__),
}
_DIGESTS_BEFORE = {k: digest_file(v) for k, v in _WATCHED.items()}

_TMP = tempfile.mkdtemp(prefix="oncotriage-dash-integration-")
_PLANT_DIR = os.path.join(_TMP, "plants")
os.makedirs(_PLANT_DIR, exist_ok=True)
_PLANT_SEQ = [0]

print("=" * 74)
print("DASHBOARD APP INTEGRATION AND NULL-RESILIENCE TEST")
print("=" * 74)
print(f"Scratch root: {_TMP}")
print()


# ===========================================================================
# SECTION 1: THE SEEDED DATABASES, AND NONE OF THEM IS THE PRODUCTION ONE
# ===========================================================================

print("=" * 74)
print("Section 1: the seed, and the isolation that makes it mean anything")
print("=" * 74)

_PRODUCTION_DB = os.path.abspath(_paths.inferences_path)
_PRODUCTION_DIGEST_BEFORE = digest_file(_PRODUCTION_DB)
_PRODUCTION_EXISTED_BEFORE = os.path.exists(_PRODUCTION_DB)


def _quiet_initialize(path):
    with contextlib.redirect_stderr(_io.StringIO()):
        with contextlib.redirect_stdout(_io.StringIO()):
            initialize_database(path)


# --- THE SEED, WHICH IS ALSO THE REFERENCE ---------------------------------
#
# THE SPARSE ROW IS THE POINT OF THIS FILE and it is written the way a real one
# arrives: `INSERT` naming only the columns a caller supplied, so every other
# column is SQL NULL. It is not a hand-built frame of NaN -- an inference row
# that ended at the error handler, and every row written before an additive
# column existed, is exactly this shape, and building it any other way would
# test a fabrication.

_FULL_PATIENT = "P-FULL"
_SPARSE_PATIENT = "P-SPARSE"

# The numeric columns the Patient Explorer converts, all of them NULL on the
# sparse row. Written out so section 3's expectations are derived from THIS
# list rather than from what the render happened to produce.
_SPARSE_NULL_COLUMNS = (
    "age", "condition_count", "medication_count", "candidates_retrieved",
    "candidates_reranked", "candidates_after_rule_filter",
    "candidates_after_quality_filter", "candidates_filtered",
    "candidates_evaluated", "eligible_matches", "total_time",
    "estimated_cost_usd", "llm_classifier_input_tokens",
    "llm_classifier_output_tokens", "llm_classifier_reasoning_tokens",
    "mesh_dropped", "stage_dropped", "histology_dropped",
)

# The funnel stages the tab draws, in its own order, and the ones a row with
# every count NULL cannot record. Derived from _FUNNEL in the module under test
# would be circular; this is the seed's own statement of what it withheld.
# THE SEVEN STAGES A ROW WITH EVERY DATABASE COUNT NULL CANNOT RECORD. The last
# three funnel stages -- Full Match / Partial / Unconfirmed -- are NOT here,
# because `enrich_match_tiers` derives them and fills them with a measured zero
# (see check 3a), so they are recorded even on this row. Listing them would
# assert that a measured value is missing.
_FUNNEL_UNRECORDED_ON_SPARSE = ("Retrieved", "Reranked", "Rule Filter",
                                "Quality Filter", "Cost Cap", "Evaluated",
                                "Eligible (Any)")
_FUNNEL_RECORDED_ON_SPARSE = ("Full Match", "Partial", "Unconfirmed")

# The constant cross-encoder score. ONE VALUE FOR EVERY TRIAL is what makes the
# KDE's covariance matrix singular, and it is a reachable state rather than a
# contrived one: Stage 2 falling back to BM25-only leaves every rerank_score
# unset, and a re-index that scores an arm identically does it too.
#
# THE VALUE IS 0.5 AND THAT IS NOT ARBITRARY -- MEASURED, NOT ASSUMED. Whether
# `gaussian_kde` RAISES on a constant distribution depends on the VALUE, because
# the covariance it inverts is computed by subtracting a mean: for a value that
# is exactly representable in binary (0.5, 0.0, 1.0) the residuals are exactly
# zero and the matrix is exactly singular, so it raises; for one that is not
# (0.42) they are denormal-but-non-zero and the inversion SUCCEEDS, producing an
# enormously peaked curve over an interval of width zero -- a rendered
# nonsense-answer instead of a crash. Measured on scipy 1.15.3:
#
#     [0.5]*4  -> LinAlgError      [0.42]*6 -> no raise, a curve
#     [0.0]*5  -> LinAlgError      [0.42]*3 -> no raise, a curve
#     [1.0]*6  -> LinAlgError
#
# So the guard is right about BOTH outcomes and section 6's control has to use
# the raising value or it would report the defect as uncaught. Check 4f records
# the other half, because a test that only ever saw the raise would leave a
# reader believing the silent-nonsense case does not exist.
_CONSTANT_RERANK = 0.5
_NON_REPRESENTABLE_CONSTANT = 0.42

_TRIAL_SEED = [
    # (nct_id, eligible, match_score, rerank_score)
    ("NCT-A", "eligible",     1.0, _CONSTANT_RERANK),
    ("NCT-B", "not_eligible", 0.0, _CONSTANT_RERANK),
    ("NCT-C", "eligible",     0.5, _CONSTANT_RERANK),
    ("NCT-D", "eligible",     0.0, _CONSTANT_RERANK),
    ("NCT-E", "not_eligible", 0.0, _CONSTANT_RERANK),
    ("NCT-F", "eligible",     1.0, _CONSTANT_RERANK),
]

# The trial with NO score. `trial_matches.match_score` is a nullable REAL and a
# Stage 5 failure return writes rows without one; `classify_trial_score` RAISES
# on None and answers 'Unconfirmed Match' -- a real verdict -- on NaN.
#
# IT HANGS OFF THE FULL PATIENT, NOT THE SPARSE ONE, AND THE FIRST DRAFT HAD IT
# THE OTHER WAY ROUND. `enrich_match_tiers` computes the three match counts by
# COUNTING trial rows, so a patient with ANY trial row gets a measured 0 in each
# bucket, not a NaN -- which made the sparse row's counts a measurement and the
# assertion that they render as absent simply wrong. A patient with NO trial
# rows at all is the shape that leaves them NaN, and that is the shape the
# sparse row has to have for those three columns to be under test.
_UNSCORED_TRIAL = ("NCT-NOSCORE", "eligible", None, None)

_CRITERIA_JSON = ('{"inclusion": [{"criterion": "ECOG <= 1", '
                  '"patient_value": "0", "status": "met"}], "exclusion": []}')


def _build(path):
    """Seed one scratch database. Returns {patient_id: inference id}."""
    _quiet_initialize(path)
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO inferences (patient_id, timestamp, age, sex, race, "
        "ethnicity, primary_condition, condition_count, medication_count, "
        "candidates_retrieved, candidates_reranked, "
        "candidates_after_rule_filter, candidates_after_quality_filter, "
        "candidates_filtered, candidates_evaluated, eligible_matches, "
        "total_time, estimated_cost_usd, llm_classifier_input_tokens, "
        "llm_classifier_output_tokens, llm_classifier_reasoning_tokens, "
        "matching_model, error, mesh_dropped, stage_dropped, "
        "histology_dropped) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
        "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (_FULL_PATIENT, "2026-08-20 10:00:00", 60, "female", "White",
         "Not Hispanic or Latino", "Malignant neoplasm of breast", 5, 3,
         100, 40, 30, 20, 15, 15, 3, 120.5, 0.18, 9000, 1200, 300,
         "gpt-5.6-terra", "", 3, 1, 0))
    full_id = cur.lastrowid

    # EVERY OTHER COLUMN OMITTED, which is what makes them NULL.
    cur.execute(
        "INSERT INTO inferences (patient_id, timestamp, sex, matching_model) "
        "VALUES (?, ?, ?, ?)",
        (_SPARSE_PATIENT, "2026-08-20 11:00:00", "male", "gpt-5.6-terra"))
    sparse_id = cur.lastrowid

    for nct, eligible, score, rerank in _TRIAL_SEED:
        cur.execute(
            "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
            "trial_phase, eligible, match_score, assessment, rerank_score, "
            "criterion_details) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (full_id, nct, f"Trial {nct}", "Phase 2", eligible, score,
             "assessment text", rerank, _CRITERIA_JSON))
    nct, eligible, score, rerank = _UNSCORED_TRIAL
    cur.execute(
        "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
        "trial_phase, eligible, match_score, assessment, rerank_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (full_id, nct, f"Trial {nct}", "Phase 1", eligible, score,
         "assessment text", rerank))
    # THE SPARSE PATIENT DELIBERATELY GETS NO TRIAL ROW -- see _UNSCORED_TRIAL.
    conn.commit()
    conn.close()
    return {_FULL_PATIENT: full_id, _SPARSE_PATIENT: sparse_id}


_DB = os.path.join(_TMP, "seeded.db")
_IDS = _build(_DB)

check("1a  the package default resolves to the production database and the "
      "scratch path is NOT it (without this every check below is vacuous)",
      os.path.abspath(_DB) == _PRODUCTION_DB, False)
check("1a  ...and the production path is a real resolved path",
      _PRODUCTION_DB.endswith("inferences.db") and len(_PRODUCTION_DB) > 20,
      True)
check("1b  the seed wrote two inference rows", len(_IDS), 2)
check_true("1b  ...with distinct ids (non-degeneracy)",
           len(set(_IDS.values())) == 2)

_probe = sqlite3.connect(_DB)
_sparse_row = dict(zip(
    [d[0] for d in _probe.execute(
        "SELECT * FROM inferences WHERE patient_id = ?",
        (_SPARSE_PATIENT,)).description],
    _probe.execute("SELECT * FROM inferences WHERE patient_id = ?",
                   (_SPARSE_PATIENT,)).fetchone()))
check("1c  every column the sparse row is supposed to withhold really is NULL "
      "in the database -- the whole file is vacuous if the seed quietly "
      "supplied a default",
      sorted(c for c in _SPARSE_NULL_COLUMNS if _sparse_row.get(c) is not None),
      [])
_full_row = dict(zip(
    [d[0] for d in _probe.execute(
        "SELECT * FROM inferences WHERE patient_id = ?",
        (_FULL_PATIENT,)).description],
    _probe.execute("SELECT * FROM inferences WHERE patient_id = ?",
                   (_FULL_PATIENT,)).fetchone()))
check("1c  ...and the FULL row carries all of them (the control: a seed that "
      "withheld everything would satisfy the line above too)",
      sorted(c for c in _SPARSE_NULL_COLUMNS if _full_row.get(c) is None), [])
check("1d  exactly one seeded trial row carries no match_score",
      _probe.execute("SELECT COUNT(*) FROM trial_matches "
                     "WHERE match_score IS NULL").fetchone()[0], 1)
check("1d  ...and the sparse patient has NO trial rows at all, which is what "
      "leaves its three match counts NaN rather than a measured 0",
      _probe.execute("SELECT COUNT(*) FROM trial_matches WHERE inference_id = ?",
                     (_IDS[_SPARSE_PATIENT],)).fetchone()[0], 0)
check("1d  ...and the scored ones all share one rerank_score, which is what "
      "makes the KDE covariance singular",
      sorted({r[0] for r in _probe.execute(
          "SELECT rerank_score FROM trial_matches "
          "WHERE rerank_score IS NOT NULL")}), [_CONSTANT_RERANK])
_probe.close()

_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")


# ===========================================================================
# THE RENDER HARNESS
# ===========================================================================

_DRIVER_APP = """
import importlib, sys
sys.path.insert(0, {extra_path!r})
_mod = importlib.import_module({module!r})
_mod.main()
"""

_DRIVER_TAB = """
import pickle, importlib, sys
sys.path.insert(0, {extra_path!r})
_mod = importlib.import_module({module!r})
with open({frame!r}, "rb") as _fh:
    _df = pickle.load(_fh)
getattr(_mod, {fn!r})(_df)
"""

_CONNECTED_PATHS = []
_REAL_CONNECT = sqlite3.connect


def _recording_connect(database, *args, **kwargs):
    _CONNECTED_PATHS.append(str(database))
    return _REAL_CONNECT(database, *args, **kwargs)


# --- the offline guard -------------------------------------------------------
#
# Named functions and not lambdas, and the guard's own frames skipped BY NAME:
# a guard that walks back a fixed number of frames reports its own stand-in as
# the caller, and its control then passes for the wrong reason. That is a
# recorded lesson from tests/test_dashboard_reproducibility_tab.py, adopted
# rather than rediscovered.

_NETWORK_ATTEMPTS = []
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo

_GUARD_FRAMES = {"_blocked", "_network_caller", "_guard_connect",
                 "_guard_connect_ex", "_guard_create_connection",
                 "_guard_getaddrinfo"}


def _network_caller():
    for frame in reversed(traceback.extract_stack()):
        if os.path.basename(frame.filename) == "socket.py":
            continue
        if frame.name in _GUARD_FRAMES:
            continue
        return f"{os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
    return "unknown"


def _blocked(call_name, target):
    where = _network_caller()
    _NETWORK_ATTEMPTS.append({"call": call_name, "target": repr(target),
                              "caller": where})
    raise OSError(f"[offline guard] {call_name} to {target!r} blocked; "
                  f"attempted from {where}")


def _guard_connect(self, address, *a, **k):
    return _blocked("socket.connect", address)


def _guard_connect_ex(self, address, *a, **k):
    return _blocked("socket.connect_ex", address)


def _guard_create_connection(address, *a, **k):
    return _blocked("socket.create_connection", address)


def _guard_getaddrinfo(host, port, *a, **k):
    return _blocked("socket.getaddrinfo", (host, port))


def _arm_offline_guard():
    socket.socket.connect = _guard_connect
    socket.socket.connect_ex = _guard_connect_ex
    socket.create_connection = _guard_create_connection
    socket.getaddrinfo = _guard_getaddrinfo


def _disarm_offline_guard():
    socket.socket.connect = _REAL_SOCKET_CONNECT
    socket.socket.connect_ex = _REAL_SOCKET_CONNECT_EX
    socket.create_connection = _REAL_CREATE_CONNECTION
    socket.getaddrinfo = _REAL_GETADDRINFO


def _frames(db_path):
    """The (inferences, trial_matches) frames ``main()`` would build."""
    conn = _REAL_CONNECT(db_path)
    try:
        inferences = pd.read_sql_query("SELECT * FROM inferences", conn)
        matches = pd.read_sql_query("SELECT * FROM trial_matches", conn)
    finally:
        conn.close()
    if not inferences.empty:
        inferences["timestamp"] = pd.to_datetime(inferences["timestamp"])
    return inferences, matches


def _enriched(db_path, only_patient=None):
    """The frame a TAB receives: enriched exactly as ``main()`` enriches it.

    ``enrich_match_tiers`` IS APPLIED HERE RATHER THAN SKIPPED, because it is
    what creates three of the columns the Patient Explorer converts --
    full/partial/unconfirmed_match_count are NOT database columns, and for a
    patient with no trial rows they come back NaN. Handing a tab a frame without
    them would test a shape production never produces.
    """
    inferences, matches = _frames(db_path)
    inferences = enrich_match_tiers(inferences, matches)
    if only_patient is not None:
        inferences = inferences[
            inferences["patient_id"] == only_patient].reset_index(drop=True)
    return inferences


def _capture(at):
    return {
        "exception": [str(e.value).splitlines()[0] for e in at.exception],
        "metrics": [(m.label, m.value) for m in at.metric],
        "success": [s.value for s in at.success],
        "warning": [w.value for w in at.warning],
        "info": [i.value for i in at.info],
        "error": [e.value for e in at.error],
        "caption": [c.value for c in at.caption],
        "markdown": [m.value for m in at.markdown],
        "subheader": [s.value for s in at.subheader],
        "header": [h.value for h in at.header],
        "dataframe_objects": [d.value for d in at.dataframe],
        "tabs": [t.label for t in at.get("tab")],
        "plotly": len(at.get("plotly_chart")),
    }


def _run(script, db_path):
    """Run one driver script against one scratch database, isolated + offline.

    Returns (capture, connected sqlite paths, attempted network calls).
    """
    _paths._RESOLVED["inferences_path"] = db_path
    st.cache_data.clear()
    del _CONNECTED_PATHS[:]
    del _NETWORK_ATTEMPTS[:]
    sqlite3.connect = _recording_connect
    import oncotriage.dashboard.data as _data_mod
    _data_mod.sqlite3.connect = _recording_connect
    _arm_offline_guard()
    try:
        at = AppTest.from_string(script, default_timeout=300)
        at.run()
    finally:
        _disarm_offline_guard()
        sqlite3.connect = _REAL_CONNECT
        _data_mod.sqlite3.connect = _REAL_CONNECT
    return _capture(at), list(_CONNECTED_PATHS), list(_NETWORK_ATTEMPTS)


def _render_app(db_path, module="oncotriage.dashboard.app"):
    return _run(_DRIVER_APP.format(extra_path=_PLANT_DIR, module=module),
                db_path)


def _render_tab(module, fn, frame, db_path=None):
    path = os.path.join(_TMP, "frame.pkl")
    with open(path, "wb") as handle:
        pickle.dump(frame, handle)
    return _run(_DRIVER_TAB.format(extra_path=_PLANT_DIR, module=module,
                                   frame=path, fn=fn), db_path or _DB)


def _plant(source_path, old, new, prefix, count=1):
    """A COPY of `source_path` with `old` replaced, in the temp plant directory.

    Returns (module name, occurrences of `old` in the shipped source). Nothing
    under version control is touched. The occurrence count is returned so that a
    plant which matched NOTHING is a named failure rather than a defect reported
    as uncaught -- pass 20f-1's lesson: a revert reporting MISSED can mean the
    check is weak OR that the revert never took effect, and those are not the
    same finding.
    """
    text = Path(source_path).read_text(encoding="utf-8")
    made = text.count(old)
    _PLANT_SEQ[0] += 1
    name = f"{prefix}_plant_{_PLANT_SEQ[0]}"
    Path(os.path.join(_PLANT_DIR, name + ".py")).write_text(
        text.replace(old, new, count), encoding="utf-8")
    return name, made


def _joined(capture, *keys):
    out = []
    for key in keys:
        out.extend(str(v) for v in capture.get(key, []))
    return "\n".join(out)


def _metric(capture, label):
    for name, value in capture["metrics"]:
        if name == label:
            return value
    return "(no such metric)"


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: main() RENDERS, AND ITS TAB STRIP IS WHAT app.py DECLARES
# ===========================================================================

print()
print("=" * 74)
print("Section 2: the ten-tab wiring")
print("=" * 74)


def declared_tab_labels(source_path):
    """The tab labels ``main()`` passes to ``st.tabs``, read off the source.

    BY AST AND NOT BY RENDERING, which is the whole point: comparing a render
    against itself proves nothing. This reads the LITERAL LIST in the source, so
    the check below is "the strip the reader sees is the strip the source
    declares" -- and a label edited in one place still fails it, because the
    render and the source are then two different answers.

    At any nesting depth, deliberately: ``st.tabs`` is inside ``main()``, and a
    future refactor that moves it into a helper must not escape this pin by
    moving.
    """
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not (isinstance(target, ast.Attribute) and target.attr == "tabs"):
            continue
        for arg in node.args:
            if isinstance(arg, (ast.List, ast.Tuple)):
                found.append([e.value for e in arg.elts
                              if isinstance(e, ast.Constant)])
    return found


def declared_render_calls(source_path):
    """Every ``render_*_tab`` call in the source, in order.

    THE SECOND HALF OF THE WIRING. ``st.tabs`` builds the strip and a separate
    sequence of ``with tabN:`` blocks calls the renderers; a tab added to one
    list and not the other renders an EMPTY tab, which no assertion about
    labels can see.
    """
    tree = ast.parse(Path(source_path).read_text(encoding="utf-8"))
    calls = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id.startswith("render_")
                and node.func.id.endswith("_tab")):
            calls.append((node.lineno, node.func.id))
    return [name for _line, name in sorted(calls)]


_APP_PY = _WATCHED["app.py"]
_DECLARED = declared_tab_labels(_APP_PY)
check("2a  app.py calls st.tabs exactly once with one literal list "
      "(non-degeneracy: a walk that found none would satisfy 2b for free)",
      len(_DECLARED), 1)
_LABELS = at_(_DECLARED, 0, [])
check_true("2a  ...and that list is not empty", len(_LABELS) >= 1)

_RENDER_CALLS = declared_render_calls(_APP_PY)
check("2b  every declared tab has a render call and vice versa",
      (len(_LABELS), len(_RENDER_CALLS)), (10, 10))
check("2b  ...and the render calls are ten DISTINCT functions -- a copy-paste "
      "that called one renderer twice would leave a tab blank",
      len(set(_RENDER_CALLS)), 10)

_app_cap, _app_paths, _app_net = _render_app(_DB)

check("2c  main() renders the whole dashboard without raising, against a "
      "database holding one full row and one row with every numeric column "
      "NULL", _app_cap["exception"], [])
check("2d  the rendered tab strip is EXACTLY what app.py declares, in order",
      _app_cap["tabs"], _LABELS)
check_true("2d  ...and there are ten of them", len(_app_cap["tabs"]) == 10)
check("2e  every tab rendered its own header -- a tab present in the strip and "
      "silent underneath is what a dropped `with tabN:` block produces",
      len([h for h in _app_cap["header"] if h]) >= 10, True)


# ===========================================================================
# SECTION 3: THE SPARSE ROW, RENDERED HONESTLY RATHER THAN NOT AT ALL
# ===========================================================================

print()
print("=" * 74)
print("Section 3: one row with every numeric column NULL")
print("=" * 74)

_SPARSE_FRAME = _enriched(_DB, only_patient=_SPARSE_PATIENT)
check("3a  the sparse frame really is one row (non-degeneracy)",
      len(_SPARSE_FRAME), 1)
# THE THREE MATCH COUNTS ARE NEVER ABSENT, AND MEASURING THAT IS WHY THIS CHECK
# IS HERE RATHER THAN AN ASSERTION THAT THEY ARE. `enrich_match_tiers` ends each
# of them with `.fillna(0).astype(int)`, so a patient with no trial rows gets a
# MEASURED zero -- which is a defensible reading (no eligible trial row was
# found for this inference) and is the reading the Patient Explorer therefore
# has to render. The first draft of this file asserted the opposite and was
# wrong twice: once about a patient WITH an unscored trial, once about a patient
# with no trials at all. The columns under test for absence are the ones the
# DATABASE leaves NULL, listed in _SPARSE_NULL_COLUMNS.
check("3a  ...and its three match counts are a MEASURED zero rather than "
      "absent, because enrich_match_tiers fills them -- so they are not part "
      "of what section 3c is about",
      sorted(c for c in ("full_match_count", "partial_match_count",
                         "unconfirmed_match_count")
             if pd.isna(_SPARSE_FRAME.iloc[0].get(c))),
      [])

_pe_cap, _pe_paths, _pe_net = _render_tab(
    "oncotriage.dashboard.tabs.patient_explorer",
    "render_patient_explorer_tab", _SPARSE_FRAME)

check("3b  the Patient Explorer renders the sparse row without raising",
      _pe_cap["exception"], [])

_ABSENT = _ns.ABSENT_TEXT
for _label in ("Conditions", "Medications", "Retrieved", "Evaluated"):
    check(f"3c  '{_label}' renders the absent marker rather than 0 -- a count "
          f"nobody recorded must not print as the measured answer",
          _metric(_pe_cap, _label), _ABSENT)

check("3c  the three DERIVED match counts render their measured zero, and are "
      "the control for the four above: a tab that rendered the absent marker "
      "for every numeric cell would satisfy those four for free",
      [_metric(_pe_cap, n) for n in
       ("✅ Full Matches", "🟡 Partial Matches", "🔶 Unconfirmed")],
      ["0", "0", "0"])
check("3c  ...Total Time renders the marker and not 'nans'",
      _metric(_pe_cap, "Total Time"), _ABSENT)
check("3c  ...and Cost renders the marker and not '$nan'",
      _metric(_pe_cap, "Cost"), _ABSENT)
check("3c  ...and the token tiles too",
      (_metric(_pe_cap, "gpt-5.6-terra Input Tokens"),
       _metric(_pe_cap, "gpt-5.6-terra Output Tokens")), (_ABSENT, _ABSENT))
check("3c  ...while the reasoning tile keeps the 'n/a' its own help text "
      "explains, rather than being swept into the em dash",
      _metric(_pe_cap, "…of which reasoning"), "n/a")

check_true("3d  the funnel NAMES the stages it drew at zero, so a bar at zero "
           "is never silently a bar for a missing number",
           "drawn at zero" in _joined(_pe_cap, "caption"))
_zero_caption = [c for c in _pe_cap["caption"] if "drawn at zero" in c]
check("3d  ...and it names every stage the DATABASE left NULL",
      sorted(s for s in _FUNNEL_UNRECORDED_ON_SPARSE
             if s not in at_(_zero_caption, 0, "")), [])
check("3d  ...and does NOT name the three the enrichment measured as zero -- a "
      "caption that named every stage would be satisfied by a helper that "
      "always reported 'unrecorded'",
      sorted(s for s in _FUNNEL_RECORDED_ON_SPARSE
             if s in at_(_zero_caption, 0, "")), [])

_full_frame = _enriched(_DB, only_patient=_FULL_PATIENT)
_pe_full, _, _ = _render_tab("oncotriage.dashboard.tabs.patient_explorer",
                             "render_patient_explorer_tab", _full_frame)

check("3e  the unscored trial is reported as its own status, not as an "
      "unconfirmed match",
      _pe_full["exception"] == [] and TRIAL_STATUS_NO_SCORE in str(
          at_(_pe_full["dataframe_objects"], 0, pd.DataFrame())
          .get("Status", pd.Series(dtype=object)).tolist()), True)
check_true("3e  ...and the caption says how many there were",
           "no recorded score" in _joined(_pe_full, "caption"))

check("3f  CONTROL the fully-populated row still renders its real numbers -- "
      "a tab that printed the absent marker for everything would satisfy "
      "every check above",
      (_pe_full["exception"], _metric(_pe_full, "Conditions"),
       _metric(_pe_full, "Retrieved")),
      ([], "5", "100"))
check("3f  ...and it prints no drawn-at-zero caption, because it recorded "
      "every stage",
      [c for c in _pe_full["caption"] if "drawn at zero" in c], [])


# ===========================================================================
# SECTION 4: THE KDE, AND THE DEGENERATE DISTRIBUTION THAT USED TO RAISE
# ===========================================================================

print()
print("=" * 74)
print("Section 4: zero-variance scores")
print("=" * 74)

# The function directly. It is module-level for exactly this reason: nested
# inside the render it could not be driven, so the input that crashed it could
# not be shown to be survived.
check("4a  a constant distribution yields no curve rather than raising",
      called(_perf._kde_curve, np.array([_CONSTANT_RERANK] * 6)), None)
check("4a  ...at the smallest size that reaches the estimator at all",
      called(_perf._kde_curve, np.array([0.5, 0.5, 0.5])), None)
check("4b  fewer than three points still yields no curve (the guard that was "
      "already there)", called(_perf._kde_curve, np.array([0.1, 0.2])), None)
check("4c  a non-finite value yields no curve -- np.ptp over an array holding "
      "inf is nan, which compares False against 0 and would slip straight "
      "into the estimator",
      (called(_perf._kde_curve, np.array([0.1, 0.2, float("inf")])),
       called(_perf._kde_curve, np.array([0.1, 0.2, float("nan")]))),
      (None, None))
check("4f  the raise is VALUE-DEPENDENT and the guard covers both outcomes: a "
      "constant that is not exactly representable in binary leaves a residual "
      "covariance, so the estimator does NOT raise and returns a curve over an "
      "interval of width zero. Both are wrong; only one is loud.",
      called(_perf._kde_curve,
             np.array([_NON_REPRESENTABLE_CONSTANT] * 6)), None)

_curve = called(_perf._kde_curve, np.array([0.1, 0.2, 0.3, 0.9]))
check("4d  CONTROL a real distribution still yields a curve -- a function that "
      "returned None for everything would satisfy every check above",
      isinstance(_curve, tuple) and len(_curve) == 2
      and len(_curve[0]) == 300 and len(_curve[1]) == 300, True)

_perf_cap, _, _ = _render_tab("oncotriage.dashboard.tabs.performance",
                              "render_performance_tab", _enriched(_DB))
check("4e  the Performance tab renders a database whose every cross-encoder "
      "score is identical, without raising", _perf_cap["exception"], [])
check_true("4e  ...and says that the unscored trial was excluded rather than "
           "counting it as not-eligible",
           "no `match_score`" in _joined(_perf_cap, "caption"))

# THE TRIAL EXPLORER NEEDS ITS OWN DATABASE, AND MEASURING WHY IS THE POINT.
# It renders the SELECTED trial only, and its selector is ordered by patient
# count -- so on the seed above the unscored trial is never the default
# selection, and a render against it exercises none of the guard. Reverting the
# fix and driving main() proves it: the page renders clean. A database whose
# ONLY trial is the unscored one is what puts the guard under test, and it is a
# real shape rather than a contrived one (a cohort whose every Stage 5 call hit
# a failure return).
_UNSCORED_ONLY_DB = os.path.join(_TMP, "unscored_only.db")
_quiet_initialize(_UNSCORED_ONLY_DB)
_uconn = sqlite3.connect(_UNSCORED_ONLY_DB)
_uconn.execute(
    "INSERT INTO inferences (patient_id, timestamp, age, sex, race, ethnicity, "
    "primary_condition, matching_model) VALUES "
    "('P-U', '2026-08-20 10:00:00', 55, 'female', 'White', 'NH', 'Breast', "
    "'gpt-5.6-terra')")
_uid = _uconn.execute("SELECT last_insert_rowid()").fetchone()[0]
_uconn.execute(
    "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
    "trial_phase, eligible, match_score, assessment, rerank_score) VALUES "
    "(?, 'NCT-ONLY', 'The only trial', 'Phase 1', 'eligible', NULL, 'a', NULL)",
    (_uid,))
_uconn.commit()
_uconn.close()

_te_cap, _, _ = _render_tab("oncotriage.dashboard.tabs.trial_explorer",
                            "render_trial_explorer_tab",
                            _enriched(_UNSCORED_ONLY_DB),
                            db_path=_UNSCORED_ONLY_DB)
check("4f  the Trial Explorer renders when the SELECTED trial carries no "
      "match_score", _te_cap["exception"], [])
check_true("4f  ...and reports it as its own status rather than a verdict",
           TRIAL_STATUS_NO_SCORE in _joined(_te_cap, "markdown", "caption")
           or any(TRIAL_STATUS_NO_SCORE in str(f)
                  for f in _te_cap["dataframe_objects"]))

# THE DEMOGRAPHICS GUARD IS DRIVEN DIRECTLY, AND THE REASON IS THE SAME KIND OF
# MEASUREMENT. `main()`'s sidebar drops rows whose `age` is NULL before any tab
# sees them, so reverting the demographics fix and rendering main() ALSO comes
# back clean -- the crash is unreachable through that path today. The tab
# function is public and takes whatever frame it is handed, so the guard has a
# subject; it is exercised here rather than left as an assertion nobody drives.
_demo_cap, _, _ = _render_tab("oncotriage.dashboard.tabs.demographics",
                              "render_patient_demographics_tab", _SPARSE_FRAME)
check("4g  the Demographics tab renders a frame whose every age is NULL "
      "without raising -- `(NaN // 10) * 10` then `.astype(int)` refuses",
      _demo_cap["exception"], [])
check_true("4g  ...and states how many rows it excluded rather than dropping "
           "them silently",
           "no `age`" in _joined(_demo_cap, "caption"))


# ===========================================================================
# SECTION 5: THE NULL-SAFE READERS, AS PURE FUNCTIONS
# ===========================================================================

print()
print("=" * 74)
print("Section 5: oncotriage/dashboard/nullsafe.py")
print("=" * 74)

check("5a  None, nan and NaT are all absent",
      [_ns.is_absent(v) for v in (None, float("nan"), pd.NaT, np.nan)],
      [True, True, True, True])
check("5a  ...and 0, '', False and a real value are NOT -- an 'absent' that "
      "swallowed a measured zero would make every reading above meaningless",
      [_ns.is_absent(v) for v in (0, "", False, 0.0, "x", 3)],
      [False] * 6)
check("5b  a list and an ndarray are answered rather than raising: pd.isna "
      "over a container returns an ARRAY, and bool() of one raises",
      [called(_ns.is_absent, v) for v in ([1, 2], np.array([1, 2]), {"a": 1})],
      [False, False, False])

check("5c  as_int defaults an absent cell and converts a present one",
      [_ns.as_int(None), _ns.as_int(float("nan")), _ns.as_int(3.7),
       _ns.as_int("x", -1), _ns.as_int(None, -1)], [0, 0, 3, -1, -1])
check("5d  a bool is NOT laundered into a count -- True is not 1 candidate",
      [_ns.as_int(True), _ns.as_int(False, 9)], [0, 9])
check("5d  ...INCLUDING a numpy boolean, which is what a pandas column of "
      "dtype bool yields on every element read and which is NOT a subclass of "
      "`bool` -- so an isinstance test alone refuses the hand-written value "
      "and launders the one that actually occurs",
      [_ns.is_boolean(np.True_), _ns.as_int(np.True_),
       _ns.optional_int_text(np.False_)],
      [True, 0, _ns.ABSENT_TEXT])
check("5d  ...and a numpy INTEGER is still a number (the control: a bool test "
      "that refused every numpy scalar would satisfy the line above and break "
      "every real count)",
      [_ns.is_boolean(np.int64(1)), _ns.as_int(np.int64(7)),
       _ns.as_int(np.float64(3.9))], [False, 7, 3])
check("5e  optional_int_text NEVER defaults to a number, which is the whole "
      "reason it is a separate function from as_int",
      [_ns.optional_int_text(None), _ns.optional_int_text(0),
       _ns.optional_int_text(12)], [_ns.ABSENT_TEXT, "0", "12"])
check_true("5e  ...and its default is not a digit (the control for a future "
           "edit that 'simplifies' it to 0)",
           not _ns.optional_int_text(None).strip().lstrip("-").isdigit())
check("5f  format_number renders the marker for None AND for nan -- the second "
      "does not raise, it renders the string 'nan' into a metric tile, which "
      "looks like a measurement",
      [_ns.format_number(None, ".2f"), _ns.format_number(float("nan"), ".2f"),
       _ns.format_number(1.239, ".2f"), _ns.format_number(9000, ",.0f")],
      [_ns.ABSENT_TEXT, _ns.ABSENT_TEXT, "1.24", "9,000"])
check("5g  format_timestamp survives NaT, which RAISES from strftime rather "
      "than returning a marker",
      [_ns.format_timestamp(pd.NaT),
       _ns.format_timestamp(pd.Timestamp("2026-01-02 03:04")),
       _ns.format_timestamp("already a string")],
      [_ns.ABSENT_TEXT, "2026-01-02 03:04", "already a string"])
check("5h  CONTROL the raw operations these replace really do raise on the "
      "same inputs -- without this the helpers above are guarding nothing",
      [str(called(int, None)).split(":")[0],
       str(called(int, float("nan"))).split(":")[0],
       str(called(round, None, 2)).split(":")[0],
       str(called(lambda v: f"{v:.2f}", None)).split(":")[0],
       str(called(pd.NaT.strftime, "%Y")).split(":")[0]],
      ["raised", "raised", "raised", "raised", "raised"])


# ===========================================================================
# SECTION 6: PLANTED DEFECTS -- EACH INTO A COPY
# ===========================================================================

print()
print("=" * 74)
print("Section 6: planted defects, each caught by a named check")
print("=" * 74)


def _plant_render_tab(label, source, old, new, fn, frame, prefix,
                      expect_occurrences=1):
    module, made = _plant(source, old, new, prefix)
    check(f"6  [{label}] the plant matched the shipped source exactly "
          f"{expect_occurrences} time(s)", made, expect_occurrences)
    capture, _, _ = _render_tab(module, fn, frame)
    return capture


# P1 -- the "never measured" reading collapses into a measured zero.
_p1 = _plant_render_tab(
    "P1 an unrecorded count renders as 0",
    _WATCHED["patient_explorer.py"],
    '''        st.metric("Conditions", optional_int_text(patient_df.get('condition_count'))''',
    '''        st.metric("Conditions", as_int(patient_df.get('condition_count'))''',
    "render_patient_explorer_tab", _SPARSE_FRAME, "pe")
check("6a  P1 is caught: a count nobody recorded now claims to be zero",
      _metric(_p1, "Conditions"), "0")
check("6a  ...and the shipped module does not (the control)",
      _metric(_pe_cap, "Conditions"), _ABSENT)

# P2 -- the funnel stops naming the stages it drew at zero.
_p2 = _plant_render_tab(
    "P2 the drawn-at-zero caption disappears",
    _WATCHED["patient_explorer.py"],
    "    if unrecorded_stages:\n        st.caption(",
    "    if False:\n        st.caption(",
    "render_patient_explorer_tab", _SPARSE_FRAME, "pe")
check("6b  P2 is caught: ten zero bars are drawn with nothing saying they are "
      "not measurements",
      "drawn at zero" in _joined(_p2, "caption"), False)
check_true("6b  ...and the shipped module does print it (the control)",
           "drawn at zero" in _joined(_pe_cap, "caption"))

# P3 -- the unscored trial goes back to classify_trial_score.
_p3 = _plant_render_tab(
    "P3 an unscored trial is classified as if it had a score",
    _WATCHED["patient_explorer.py"],
    """                if is_absent(row.get('match_score')):
                    return TRIAL_STATUS_NO_SCORE
""",
    "",
    # THE FULL PATIENT, because the unscored trial hangs off it -- the sparse
    # patient has no trial rows at all, so the table this plant is about is
    # never rendered for it and the plant would report as uncaught.
    "render_patient_explorer_tab", _full_frame, "pe")
check("6c  P3 is caught: the tab either raises or reports a verdict for a "
      "trial whose score was never recorded",
      (_p3["exception"] != []) or (TRIAL_STATUS_NO_SCORE not in str(
          at_(_p3["dataframe_objects"], 0, pd.DataFrame()))), True)
check_true("6c  ...and the shipped module reports neither (the control)",
           _pe_full["exception"] == []
           and TRIAL_STATUS_NO_SCORE in str(
               at_(_pe_full["dataframe_objects"], 0, pd.DataFrame())))

# P4 -- the nullable dtype goes back to a plain int cast.
_p4 = _plant_render_tab(
    "P4 the score column is cast to a non-nullable int",
    _WATCHED["patient_explorer.py"],
    ").round(0).astype('Int64')",
    ").round(0).astype(int)",
    "render_patient_explorer_tab", _full_frame, "pe")
check("6d  P4 is caught: pandas refuses a non-finite value and the tab raises",
      _p4["exception"] != [], True)
check("6d  ...and the shipped module renders (the control)",
      _pe_full["exception"], [])

# P5 -- the KDE's zero-variance guard is removed.
_p5_mod, _p5_made = _plant(
    _WATCHED["performance.py"],
    "    if np.ptp(scores) == 0:\n        return None\n", "", "perf")
check("6e  [P5] the plant matched the shipped source exactly once", _p5_made, 1)
sys.path.insert(0, _PLANT_DIR)
try:
    import importlib
    _p5 = importlib.import_module(_p5_mod)
    # THE EXACTLY-REPRESENTABLE CONSTANT, deliberately -- see check 4f. With
    # 0.42 the unguarded estimator does not raise, so this control would report
    # the defect as uncaught while the defect is real.
    _p5_result = called(_p5._kde_curve, np.array([_CONSTANT_RERANK] * 6))
finally:
    sys.path.remove(_PLANT_DIR)
check("6e  P5 is caught: without the guard the estimator raises LinAlgError on "
      "a constant distribution",
      isinstance(_p5_result, str) and "LinAlgError" in _p5_result, True)
check("6e  ...and the shipped one returns None (the control)",
      called(_perf._kde_curve, np.array([_CONSTANT_RERANK] * 6)), None)
_p5_perf = _render_tab(_p5_mod, "render_performance_tab", _enriched(_DB))[0]
check("6e  ...and the plant takes the whole tab down when rendered, which is "
      "what it did inside main() for every reader",
      _p5_perf["exception"] != [], True)

# P9 -- the Trial Explorer's absence guard is removed.
_p9 = _plant_render_tab(
    "P9 the trial explorer classifies an unscored trial",
    _WATCHED["trial_explorer.py"],
    """        if is_absent(row.get('match_score')):
            return TRIAL_STATUS_NO_SCORE
""",
    "",
    "render_trial_explorer_tab", _enriched(_UNSCORED_ONLY_DB), "te")
check("6i  P9 is caught: the tab raises, or reports a verdict for a trial "
      "whose score was never recorded",
      (_p9["exception"] != [])
      or (TRIAL_STATUS_NO_SCORE not in _joined(_p9, "markdown", "caption")
          and not any(TRIAL_STATUS_NO_SCORE in str(f)
                      for f in _p9["dataframe_objects"])), True)
check("6i  ...and the shipped module reports neither (the control)",
      _te_cap["exception"], [])

# P10 -- the Demographics age exclusion is removed.
_p10 = _plant_render_tab(
    "P10 the demographics age panel buckets a NULL age",
    _WATCHED["demographics.py"],
    "            df_age = df_age[df_age['age'].notna()].copy()",
    "            pass",
    "render_patient_demographics_tab", _SPARSE_FRAME, "demo")
check("6j  P10 is caught: pandas refuses a non-finite decade and the tab raises",
      _p10["exception"] != [], True)
check("6j  ...and the shipped module renders (the control)",
      _demo_cap["exception"], [])

# P6 -- a tab is dropped from the strip.
_p6_mod, _p6_made = _plant(
    _APP_PY, '        "🩺  Run Health"\n    ])', "    ])", "app")
check("6f  [P6] the plant matched the shipped source exactly once", _p6_made, 1)
_p6 = _render_app(_DB, module=_p6_mod)[0]
check("6f  P6 is caught: the rendered strip no longer matches what the source "
      "declares", _p6["tabs"] == _LABELS, False)
check("6f  ...and the shipped module matches (the control)",
      _app_cap["tabs"], _LABELS)

# P7 -- a tab is renamed.
_p7_mod, _p7_made = _plant(_APP_PY, '"🩺  Run Health"', '"🩺  Runs"', "app")
check("6g  [P7] the plant matched the shipped source exactly once", _p7_made, 1)
check("6g  P7 is caught: an edited label changes the strip a reader sees",
      at_(declared_tab_labels(
          os.path.join(_PLANT_DIR, _p7_mod + ".py")), 0, []) == _LABELS, False)

# P8 -- the strip and the render calls fall out of step.
_p8_mod, _p8_made = _plant(
    _APP_PY, "    with tab10:\n        render_run_health_tab(filtered_df)\n",
    "", "app")
check("6h  [P8] the plant matched the shipped source exactly once", _p8_made, 1)
check("6h  P8 is caught: a tab in the strip with no render call underneath it "
      "renders empty, which no assertion about LABELS can see",
      len(declared_render_calls(os.path.join(_PLANT_DIR, _p8_mod + ".py")))
      == len(_LABELS), False)


# ===========================================================================
# SECTION 7: ISOLATION AND THE OFFLINE GUARD, BOTH WITH CONTROLS
# ===========================================================================

print()
print("=" * 74)
print("Section 7: it reads the scratch database and nothing else, offline")
print("=" * 74)

check("7a  the app render opened at least one database (non-degeneracy: a "
      "render that opened none would satisfy 7b for free)",
      len(_app_paths) >= 1, True)
check("7b  ...and every database it opened was the scratch one",
      sorted({p for p in _app_paths
              if os.path.abspath(p.replace("file:", "").split("?")[0])
              != os.path.abspath(_DB)}), [])

# THE DECOY CONTROL. Without it, 7b is satisfied by an assertion that always
# looked at an empty list.
_DECOY = os.path.join(_TMP, "decoy.db")
_build(_DECOY)
_decoy_cap, _decoy_paths, _ = _render_app(_DECOY)
check("7c  CONTROL the same assertion FAILS against a different database, "
      "which is what says it is discriminating rather than vacuous",
      sorted({p for p in _decoy_paths
              if os.path.abspath(p.replace("file:", "").split("?")[0])
              != os.path.abspath(_DB)}) == [], False)

check("7d  no render attempted a network call",
      _app_net + _pe_net + _perf_cap.get("network", []), [])

_arm_offline_guard()


def _offline_control_call():
    """Make a real outbound call, in a frame with a name to be reported.

    IT DOES NOT GO THROUGH ``called()``, AND THAT COST ONE FAILING RUN TO
    LEARN. The guard reports the nearest frame that is not its own, so a helper
    between this function and the socket call becomes the reported caller and
    the assertion below then passes only because that helper is in this file
    too -- satisfied for the wrong reason, which is the exact shape a control is
    written to avoid.
    """
    try:
        socket.create_connection(("example.invalid", 80))
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"raised: {type(exc).__name__}: {exc}"
    return "no raise"


_control_result = _offline_control_call()
_disarm_offline_guard()
check("7e  CONTROL the offline guard really blocks and really records -- "
      "without this, '0 attempts' is also what an unarmed guard reports",
      isinstance(_control_result, str) and "offline guard" in _control_result,
      True)
check("7e  ...and it names the caller rather than its own stand-in",
      at_(_NETWORK_ATTEMPTS, -1, {}).get("caller", "").endswith(
          "in _offline_control_call"), True)


# ===========================================================================
# SECTION 8: HYGIENE
# ===========================================================================

print()
print("=" * 74)
print("Section 8: nothing in the repository was written")
print("=" * 74)

for _name, _path in _WATCHED.items():
    check(f"8a  {_name} is byte-identical after the run",
          digest_file(_path), _DIGESTS_BEFORE[_name])
check("8a  ...and those readings are real digests, not two 'absent' sentinels "
      "compared with each other (non-degeneracy)",
      sorted(k for k, v in _DIGESTS_BEFORE.items() if not is_real_digest(v)),
      [])

check("8b  the production database is byte-identical",
      digest_file(_PRODUCTION_DB), _PRODUCTION_DIGEST_BEFORE)
_PROBE_LABEL = ("8b  ...and that reading is a real sha256, so the comparison "
                "above is not two sentinels (non-degeneracy)")
if _PRODUCTION_EXISTED_BEFORE:
    check(_PROBE_LABEL, is_real_digest(_PRODUCTION_DIGEST_BEFORE), True)
else:
    skip(_PROBE_LABEL,
         f"no production database at {_PRODUCTION_DB}, so the byte-identity "
         f"check above had nothing to be exercised against. That check stayed "
         f"LIVE and would still have caught this run creating one. Expected on "
         f"a CI runner: provision_ci_paths.py creates the parent directory and "
         f"deliberately not the file.")

check("8c  no plant leaked into the package directory",
      sorted(p.name for p in Path(os.path.dirname(_WATCHED["app.py"])).glob(
          "*_plant_*.py")), [])

_paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)
check("8d  the paths resolver cache is restored",
      _paths._RESOLVED.get("inferences_path"), _SAVED_RESOLVED)
check("8e  sqlite3.connect is the real one again",
      sqlite3.connect is _REAL_CONNECT, True)
check("8e  ...and every socket primitive is restored",
      [socket.socket.connect is _REAL_SOCKET_CONNECT,
       socket.socket.connect_ex is _REAL_SOCKET_CONNECT_EX,
       socket.create_connection is _REAL_CREATE_CONNECTION,
       socket.getaddrinfo is _REAL_GETADDRINFO], [True] * 4)

st.cache_data.clear()
shutil.rmtree(_TMP, ignore_errors=True)
check("8f  the temporary directory is removed", os.path.exists(_TMP), False)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
print(f"Skipped: {_RESULTS['skipped']}   (a skip is NOT a pass and is not "
      f"counted as one)")
print(f"Runtime: {time.time() - _T_START:.1f}s")

if _SKIPS:
    print("\nSkipped:")
    for _s in _SKIPS:
        print(f"  - {_s}")

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
Created on Sun Aug 23 2026

@author: ramyalsaffar
"""
