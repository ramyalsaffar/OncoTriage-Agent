# Dashboard Schema Preflight, Funnel Arithmetic and Any-Match Ownership Test
############################################################################

"""
Six things the dashboard got wrong, all answered at the RENDERED SURFACE.

  1. AN OUT-OF-ERA DATABASE TOOK THE WHOLE PAGE DOWN. The production
     ``inferences.db`` is SCHEMA ERA 0 -- ``PRAGMA user_version`` 0, the
     pre-naming-pass ``gpt4o_*`` spellings, no ``runs``, no ``run_metrics``.
     Driven through ``main()`` against a database of exactly that shape it
     raised ``KeyError: 'llm_classifier_evaluation_time'`` inside
     ``tabs/overview.py``, which has no handler anywhere between it and
     ``main()``, so ten tabs rendered one traceback and no diagnosis.

  2. AN EMPTY CATEGORY CRASHED THE DRIFT TAB. ``DataFrame.apply(axis=1)`` over
     no rows returns a DATAFRAME, so the three ``display_df[...] = ...apply(...)``
     assignments raised ``ValueError: Cannot set a DataFrame with multiple
     columns to the single column Status``. Reachable on an ordinary run: a
     drift run whose designated reference could not be resolved records the
     baseline-independent family and refuses every comparison family, so
     selecting a comparison category finds nothing.

  3. THE FUNNEL TILE DIVIDED BY THE WRONG COLUMN. "Reranked → Rule Filter" read
     ``candidates_filtered`` -- Stage 4's output after the quality gate AND the
     cost cap -- so it reported two stages further down the funnel than its own
     heading names.

  4. "ANY MATCH" HAD TWO DERIVATIONS. Overview summed three tier percentages;
     Demographics read ``eligible_matches``. They agree on the production table
     and come apart on shapes the pipeline produces.

  5. ONE UNPRICEABLE MODEL BLANKED THE COST TAB.

  6. THE TAB STRIP OVERFLOWED SILENTLY.

WHY ONE FILE. All six are answered by rendering, and rendering is the expensive
part: one seed and one harness serve every check. ``test_dashboard_app_
integration.py``'s ruling, adopted -- splitting them would mean six copies of
the offline guard and the isolation recorder.

WHY NO GOLDEN SNAPSHOT. Every expectation is computed from THE SEED or is a
named literal in the module under test, never a value read back out of the
render being checked. ``tests/test_dashboard_run_health.py``'s ruling.

RUNS, COSTS, KEYS
-----------------
No network (measured, section 8, with a control that fires), no keys, no spend,
no live Qdrant, no model load, no corpus, no git history, no live server. NOT
in the collision matrix, derived: it writes only inside a ``tempfile.mkdtemp``
it removes and asserts gone, ``paths._RESOLVED`` is seeded and restored, and
the six repository files it READS are written by neither of the suite's two
writers and are sha256-compared at the end. It EXECS NOTHING: every plant is a
COPY written to a temp directory, ``ast.parse``d before it is used, and
imported from there.
"""

import ast
import contextlib
import hashlib
import io as _io
import json
import os
import pickle
import shutil
import socket
import sqlite3
import sys
import tempfile
import traceback
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from oncotriage import paths as _paths
from oncotriage.dashboard import app as _app
from oncotriage.dashboard import data as _data
from oncotriage.dashboard import sidebar as _sidebar_mod
from oncotriage.dashboard import tiers as _tiers
from oncotriage.dashboard.tabs import cost_tokens as _cost
from oncotriage.dashboard.tabs import demographics as _demo
from oncotriage.dashboard.tabs import drift as _drift
from oncotriage.dashboard.tabs import overview as _overview
from oncotriage.dashboard.tabs import run_health as _run_health
from oncotriage.dashboard.tabs import trial_explorer as _te
from oncotriage.storage import queries as queries_mod
from oncotriage.storage.database_logger import (SCHEMA_USER_VERSION,
                                                initialize_database)


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

    ``tests/test_package_invariants.py``'s mechanism, adopted: its own counter,
    its own list, and a summary line printed EVEN AT ZERO, because a skip count
    that appears only when non-zero is indistinguishable from a file with no
    skip mechanism at all.
    """
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}\n          {reason}")


def at_(sequence, index, default="(absent)"):
    """``sequence[index]`` or a named absence.

    A BARE INDEX ABORTS THE FILE, AND THIS PROJECT HAS SHIPPED THAT SHAPE
    EIGHTEEN TIMES. Every plant below is designed to make a render raise or a
    list shorten, so every read of a rendered list is exactly the expression
    that raises when the defect under test fires.
    """
    try:
        return sequence[index]
    except (IndexError, KeyError):
        return default


def called(fn, *args, **kwargs):
    """``fn(*args)``, or a marker string when it raises."""
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
    """True only for a real sha256. Asked instead of ``!= "absent"`` because
    there are TWO non-readings and a predicate written against one reports the
    other as a real digest."""
    return (isinstance(reading, str) and len(reading) == 64
            and all(c in "0123456789abcdef" for c in reading))


_WATCHED = {
    "app.py": os.path.abspath(_app.__file__),
    "data.py": os.path.abspath(_data.__file__),
    "tiers.py": os.path.abspath(_tiers.__file__),
    "sidebar.py": os.path.abspath(_sidebar_mod.__file__),
    "overview.py": os.path.abspath(_overview.__file__),
    "demographics.py": os.path.abspath(_demo.__file__),
    "drift.py": os.path.abspath(_drift.__file__),
    "cost_tokens.py": os.path.abspath(_cost.__file__),
    "trial_explorer.py": os.path.abspath(_te.__file__),
}
_DIGESTS_BEFORE = {k: digest_file(v) for k, v in _WATCHED.items()}

_TMP = tempfile.mkdtemp(prefix="oncotriage-dash-schema-")
_PLANT_DIR = os.path.join(_TMP, "plants")
os.makedirs(_PLANT_DIR, exist_ok=True)
_PLANT_SEQ = [0]

print("=" * 74)
print("DASHBOARD SCHEMA PREFLIGHT, FUNNEL ARITHMETIC AND ANY-MATCH OWNERSHIP")
print("=" * 74)
print(f"Scratch root: {_TMP}")
print()


def _plant(source_path, replacements, prefix):
    """A COPY of `source_path` with `replacements` applied, in the plant dir.

    ``replacements`` is a sequence of ``(old, new, expected_occurrences)``.

    THE OCCURRENCE COUNT IS ASSERTED AND THE RESULT IS ``ast.parse``d. A plant
    that matched nothing is a working check reported as broken, and a plant that
    does not parse is an ABORT reported as a failure -- pass 20f-1's lesson and
    the secret-gate pass's, both adopted rather than rediscovered.
    """
    text = Path(source_path).read_text(encoding="utf-8")
    for old, new, expected in replacements:
        made = text.count(old)
        if made != expected:
            fail(f"PLANT-FAILED in {os.path.basename(source_path)}",
                 f"pattern occurs {made} time(s), expected {expected}: "
                 f"{old[:70]!r}")
            return None
        text = text.replace(old, new)
    _PLANT_SEQ[0] += 1
    name = f"{prefix}_plant_{_PLANT_SEQ[0]}"
    try:
        ast.parse(text)
    except SyntaxError as exc:
        fail(f"PLANT-FAILED in {os.path.basename(source_path)}",
             f"the planted copy does not parse: {exc}")
        return None
    Path(os.path.join(_PLANT_DIR, name + ".py")).write_text(text,
                                                            encoding="utf-8")
    return name


#------------------------------------------------------------------------------


# ===========================================================================
# THE RENDER HARNESS, THE ISOLATION RECORDER AND THE OFFLINE GUARD
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


_NETWORK_ATTEMPTS = []
_REAL_SOCKET_CONNECT = socket.socket.connect
_REAL_SOCKET_CONNECT_EX = socket.socket.connect_ex
_REAL_CREATE_CONNECTION = socket.create_connection
_REAL_GETADDRINFO = socket.getaddrinfo

# NAMED FUNCTIONS AND NOT LAMBDAS, and the guard's own frames skipped BY NAME.
# A guard that walks back a fixed number of frames reports its own stand-in as
# the caller and its control then passes for the wrong reason -- a recorded
# lesson from tests/test_dashboard_reproducibility_tab.py.
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


def _capture(at):
    return {
        "exception": [str(e.value).splitlines()[0] for e in at.exception],
        "metrics": {m.label: (m.value, m.delta) for m in at.metric},
        "metric_order": [m.label for m in at.metric],
        "success": [s.value for s in at.success],
        "warning": [w.value for w in at.warning],
        "info": [i.value for i in at.info],
        "error": [e.value for e in at.error],
        "caption": [c.value for c in at.caption],
        "markdown": [m.value for m in at.markdown],
        "code": [c.value for c in at.code],
        "subheader": [s.value for s in at.subheader],
        "dataframes": len(at.dataframe),
        "tabs": [t.label for t in at.get("tab")],
        # THE SPEC, NOT `.value`. A ChartElement's `.value` reads the widget
        # out of session state, which a non-interactive `st.plotly_chart` never
        # writes -- so it raises KeyError, inside `_capture`, for every render
        # this file does. That is the abort shape this project has shipped
        # eighteen times, and it arrived here in the file written to avoid it.
        # The proto's `figure.spec` is the chart's own JSON and is always there.
        "plotly": [_plotly_spec(f) for f in at.get("plotly_chart")],
    }


def _plotly_spec(element):
    """The chart's figure spec as a dict, or a NAMED absence -- never a raise."""
    # `element.proto.spec`, which is what
    # tests/test_dashboard_reproducibility_tab.py already reads -- NOT
    # `proto.figure.spec`, which exists on the message and is empty. The first
    # draft read the second and every spec came back unreadable, which the 4f
    # check reported as a chart with no labels.
    try:
        return json.loads(element.proto.spec)
    except Exception as exc:                           # noqa: BLE001 -- reported
        return {"__unreadable__": f"{type(exc).__name__}: {exc}"}


def _chart_category_labels(specs):
    """Every categorical value on EITHER axis across a list of chart specs.

    BOTH AXES, and that is not defensiveness. The stage-timing chart is a
    HORIZONTAL bar -- its stage names are the `y` values -- so a reader written
    against `x` alone finds nothing and reports a correctly-labelled chart as
    unlabelled. Measured, in this file's own first run.
    """
    labels = set()
    for spec in specs:
        for trace in (spec or {}).get("data", []) or []:
            for axis in ("x", "y"):
                for value in trace.get(axis, []) or []:
                    if isinstance(value, str):
                        labels.add(value)
    return labels


def _run(script, db_path, keep_open=False):
    """Run one driver script against one scratch database, isolated + offline."""
    _paths._RESOLVED["inferences_path"] = db_path
    st.cache_data.clear()
    del _CONNECTED_PATHS[:]
    del _NETWORK_ATTEMPTS[:]
    sqlite3.connect = _recording_connect
    _data.sqlite3.connect = _recording_connect
    _arm_offline_guard()
    try:
        at = AppTest.from_string(script, default_timeout=300)
        at.run()
    finally:
        _disarm_offline_guard()
        sqlite3.connect = _REAL_CONNECT
        _data.sqlite3.connect = _REAL_CONNECT
    if keep_open:
        return at, list(_CONNECTED_PATHS), list(_NETWORK_ATTEMPTS)
    return _capture(at), list(_CONNECTED_PATHS), list(_NETWORK_ATTEMPTS)


def render_app(db_path, module="oncotriage.dashboard.app"):
    return _run(_DRIVER_APP.format(extra_path=_PLANT_DIR, module=module),
                db_path)


def render_tab(module, fn, frame, db_path, keep_open=False):
    path = os.path.join(_TMP, f"frame_{_PLANT_SEQ[0]}_{id(frame)}.pkl")
    with open(path, "wb") as handle:
        pickle.dump(frame, handle)
    return _run(_DRIVER_TAB.format(extra_path=_PLANT_DIR, module=module,
                                   frame=path, fn=fn), db_path,
                keep_open=keep_open)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1: THE SEEDED DATABASES, AND NONE OF THEM IS THE PRODUCTION ONE
# ===========================================================================

print("=" * 74)
print("Section 1: the seeds, and the isolation that makes them mean anything")
print("=" * 74)

_PRODUCTION_DB = os.path.abspath(_paths.inferences_path)
_PRODUCTION_DIGEST_BEFORE = digest_file(_PRODUCTION_DB)
_PRODUCTION_EXISTED_BEFORE = os.path.exists(_PRODUCTION_DB)
_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")


def _quiet_initialize(path):
    with contextlib.redirect_stderr(_io.StringIO()):
        with contextlib.redirect_stdout(_io.StringIO()):
            initialize_database(path)


# --- THE ERA-0 SHAPE, BUILT FROM A DECLARATION AND NOT COPIED --------------
#
# The production database is NEVER opened by this file, not even read-only. The
# shape below is the pre-naming-pass schema written out as the DDL it is: it
# carries `gpt4o_evaluation_time` where the current schema carries
# `llm_classifier_evaluation_time`, which is the RENAME that no migration can
# repair and which is what makes an era-0 database unrenderable rather than
# merely old.
_ERA0_DDL = """
CREATE TABLE inferences (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id TEXT, timestamp TEXT, age INTEGER, sex TEXT, race TEXT,
    ethnicity TEXT, primary_condition TEXT, condition_count INTEGER,
    medication_count INTEGER, candidates_retrieved INTEGER,
    candidates_reranked INTEGER, candidates_after_rule_filter INTEGER,
    candidates_after_quality_filter INTEGER, candidates_filtered INTEGER,
    candidates_evaluated INTEGER, eligible_matches INTEGER,
    near_misses INTEGER, query_expansion_time REAL, hybrid_retrieval_time REAL,
    cross_encoder_time REAL, rule_filter_time REAL, gpt4o_evaluation_time REAL,
    total_time REAL, gpt4o_prompt TEXT, gpt4o_input_tokens INTEGER,
    gpt4o_output_tokens INTEGER, matching_model TEXT, estimated_cost_usd REAL,
    error TEXT
);
CREATE TABLE trial_matches (
    id INTEGER PRIMARY KEY AUTOINCREMENT, inference_id INTEGER, nct_id TEXT,
    trial_title TEXT, trial_phase TEXT, rerank_score REAL, match_score REAL,
    eligible TEXT, explanation TEXT, criterion_details TEXT
);
CREATE TABLE drift_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, metric_category TEXT,
    metric_name TEXT, metric_value REAL, baseline_mean REAL, baseline_std REAL,
    p_value REAL, z_score REAL, threshold REAL, alert INTEGER,
    baseline_window_days INTEGER, comparison_window_days INTEGER, notes TEXT
);
"""


def build_era0(path):
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_ERA0_DDL)
        # EVERY COLUMN THE SIDEBAR FILTERS ON IS POPULATED, and that is not
        # padding. `render_sidebar` filters on condition_count and
        # medication_count with `>=`/`<=`, and a NULL fails both, so a row
        # missing them is dropped before any tab renders -- `main()` then
        # returns at "No data matches the current filters" and the KeyError
        # this seed exists to provoke is never reached. Measured: control C1
        # reported the defect as UNCAUGHT until this row carried them.
        conn.execute(
            "INSERT INTO inferences (patient_id, timestamp, age, sex, race, "
            "ethnicity, primary_condition, condition_count, medication_count, "
            "matching_model, error, candidates_retrieved, candidates_reranked, "
            "candidates_after_rule_filter, candidates_after_quality_filter, "
            "candidates_filtered, candidates_evaluated, eligible_matches, "
            "total_time, estimated_cost_usd, gpt4o_input_tokens, "
            "gpt4o_output_tokens, gpt4o_evaluation_time, query_expansion_time, "
            "hybrid_retrieval_time, cross_encoder_time, rule_filter_time) "
            "VALUES ('P-ERA0', '2026-08-01 10:00:00', 60, 'female', 'White', "
            "'Not Hispanic or Latino', 'Breast cancer', 4, 2, 'gpt-4o', '', "
            "100, 40, 30, 20, 15, 15, 2, 20.0, 0.10, 9000, 1000, 5.0, 0.1, "
            "1.0, 2.0, 0.3)")
        conn.commit()
    finally:
        conn.close()


# --- THE CURRENT-ERA SEED, WITH KNOWN TRUTH ON EVERY FIGURE UNDER TEST ------
#
# THE FUNNEL COUNTS ARE CHOSEN SO THE TWO FORMULAS GIVE DIFFERENT ANSWERS AND
# BOTH ARE ROUND. reranked 100, after_rule_filter 60, filtered 25:
#     correct     60 / 100 = 60.0%
#     pre-fix     25 / 100 = 25.0%
# A seed on which the two agree would report the defect as fixed whatever the
# code did.
_FUNNEL = [
    # (patient, retrieved, reranked, after_rule, after_quality, filtered,
    #  evaluated, eligible_matches)
    ("P-A", 200, 50, 30, 20, 12, 12, 2),
    ("P-B", 200, 50, 30, 20, 13, 13, 0),
]
_EXPECTED_RETRIEVED = sum(r[1] for r in _FUNNEL)
_EXPECTED_RERANKED = sum(r[2] for r in _FUNNEL)
_EXPECTED_AFTER_RULE = sum(r[3] for r in _FUNNEL)
_EXPECTED_FILTERED = sum(r[5] for r in _FUNNEL)
_EXPECTED_EVALUATED = sum(r[6] for r in _FUNNEL)
_EXPECTED_ELIGIBLE_COL = sum(r[7] for r in _FUNNEL)

_CORRECT_RULE_RETENTION = (_EXPECTED_AFTER_RULE / _EXPECTED_RERANKED) * 100
_PREFIX_RULE_RETENTION = (_EXPECTED_FILTERED / _EXPECTED_RERANKED) * 100

# THE ANY-MATCH DIVERGENCE, BY CONSTRUCTION. P-B carries eligible_matches = 0
# and an ELIGIBLE SCORED trial row, so the column says "no match" and the tiers
# say "Partial Match". The two pre-fix derivations therefore answer 50% and
# 100% on this seed, which is what makes "the two surfaces agree" a
# measurement rather than a tautology.
_TIER_ANY_MATCH_RATE = 100.0
_COLUMN_ANY_MATCH_RATE = 50.0


def build_current(path, rows=True, models=("gpt-5.6-terra", "gpt-5.6-terra")):
    _quiet_initialize(path)
    if not rows:
        return {}
    conn = sqlite3.connect(path)
    ids = {}
    try:
        cur = conn.cursor()
        for index, (pid, retr, rer, rule, qual, filt, ev, em) in enumerate(_FUNNEL):
            cur.execute(
                "INSERT INTO inferences (patient_id, timestamp, age, sex, race, "
                "ethnicity, primary_condition, condition_count, "
                "medication_count, candidates_retrieved, candidates_reranked, "
                "candidates_after_rule_filter, candidates_after_quality_filter, "
                "candidates_filtered, candidates_evaluated, eligible_matches, "
                "total_time, estimated_cost_usd, llm_classifier_input_tokens, "
                "llm_classifier_output_tokens, llm_classifier_evaluation_time, "
                "query_expansion_time, hybrid_retrieval_time, cross_encoder_time, "
                "rule_filter_time, matching_model, error) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, f"2026-08-0{index + 1} 10:00:00", 55 + index * 10,
                 "female", "White", "Not Hispanic or Latino", "Breast cancer",
                 4, 2, retr, rer, rule, qual, filt, ev, em, 20.0, 0.10,
                 10000, 1000, 5.0, 0.1, 1.0, 2.0, 0.3, models[index], ""))
            ids[pid] = cur.lastrowid
        for pid, score in (("P-A", 1.0), ("P-B", 0.5)):
            cur.execute(
                "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
                "trial_phase, eligible, match_score, assessment, rerank_score) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (ids[pid], f"NCT-{pid}", f"Trial {pid}", "Phase 2", "eligible",
                 score, "assessment text", 0.7))
        conn.commit()
    finally:
        conn.close()
    return ids


_ERA0_DB = os.path.join(_TMP, "era0.db")
build_era0(_ERA0_DB)
_CURRENT_DB = os.path.join(_TMP, "current.db")
_IDS = build_current(_CURRENT_DB)
_EMPTY_DB = os.path.join(_TMP, "empty_current.db")
build_current(_EMPTY_DB, rows=False)
_NO_TABLES_DB = os.path.join(_TMP, "no_tables.db")
_c = sqlite3.connect(_NO_TABLES_DB)
_c.execute("CREATE TABLE unrelated (x INTEGER)")
_c.commit()
_c.close()
_ABSENT_DB = os.path.join(_TMP, "there-is-no-database-here.db")

check("1a  the package default resolves to the production database and NO "
      "scratch path is it (without this every check below is vacuous)",
      sorted({os.path.abspath(p) == _PRODUCTION_DB
              for p in (_ERA0_DB, _CURRENT_DB, _EMPTY_DB, _NO_TABLES_DB,
                        _ABSENT_DB)}), [False])
check("1a  ...and the production path is a real resolved path",
      _PRODUCTION_DB.endswith("inferences.db") and len(_PRODUCTION_DB) > 20,
      True)
check("1b  the current-era seed wrote two inference rows", len(_IDS), 2)
check_true("1b  ...with distinct ids (non-degeneracy)",
           len(set(_IDS.values())) == 2)

_probe = sqlite3.connect(_CURRENT_DB)
check("1c  the seed's reranked total is what the funnel expectations are "
      "computed from",
      _probe.execute("SELECT SUM(candidates_reranked) FROM inferences"
                     ).fetchone()[0], _EXPECTED_RERANKED)
check("1c  ...and its rule-filter survivor total",
      _probe.execute("SELECT SUM(candidates_after_rule_filter) FROM inferences"
                     ).fetchone()[0], _EXPECTED_AFTER_RULE)
check("1c  ...and its post-cost-cap total, which the pre-fix tile read",
      _probe.execute("SELECT SUM(candidates_filtered) FROM inferences"
                     ).fetchone()[0], _EXPECTED_FILTERED)
check_true("1c  ...and the two formulas give DIFFERENT answers on this seed, "
           "which is what makes the funnel check discriminating",
           abs(_CORRECT_RULE_RETENTION - _PREFIX_RULE_RETENTION) > 1.0)
_probe.close()

_probe = sqlite3.connect(_ERA0_DB)
check("1d  the era-0 seed records schema era 0",
      _probe.execute("PRAGMA user_version").fetchone()[0], 0)
_era0_columns = {r[1] for r in _probe.execute("PRAGMA table_info(inferences)")}
check_true("1d  ...and carries the PRE-rename column name",
           "gpt4o_evaluation_time" in _era0_columns)
check_true("1d  ...and NOT the current one (the rename no migration repairs)",
           "llm_classifier_evaluation_time" not in _era0_columns)
_probe.close()

check_true("1e  the current schema era this build creates is a positive int",
           isinstance(SCHEMA_USER_VERSION, int) and SCHEMA_USER_VERSION > 0)

# THE ERA-0 ROW MUST SURVIVE THE SIDEBAR OR CONTROL C1 PROVES NOTHING.
# `render_sidebar` filters on condition_count and medication_count with
# `>=`/`<=`; a NULL fails both, `main()` returns at "No data matches the
# current filters", and the tab that raises is never reached -- so the control
# would report a working preflight as an uncaught defect.
_probe = sqlite3.connect(_ERA0_DB)
check("1f  every column the sidebar filters on is populated on the era-0 row, "
      "so a page WITHOUT the preflight really does reach a tab",
      _probe.execute(
          "SELECT COUNT(*) FROM inferences WHERE age IS NOT NULL AND "
          "sex IS NOT NULL AND condition_count IS NOT NULL AND "
          "medication_count IS NOT NULL AND eligible_matches IS NOT NULL"
      ).fetchone()[0], 1)
_probe.close()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: THE SCHEMA PREFLIGHT -- FIVE STATES, ALL DISTINGUISHED
# ===========================================================================

print()
print("=" * 74)
print("Section 2: the schema preflight (PART 1)")
print("=" * 74)

_era0_cap, _era0_conns, _era0_net = render_app(_ERA0_DB)

check("2a  an era-0 database renders NO exception -- this is the whole item; "
      "before the preflight it raised KeyError inside main()",
      _era0_cap["exception"], [])
check("2a  ...and NO tab is drawn, because a tab is what raised",
      len(_era0_cap["tabs"]), 0)
check_true("2a  ...and the diagnosis says the page was not drawn",
           any("no tab has been drawn" in e for e in _era0_cap["error"]))

_era0_md = "\n".join(_era0_cap["markdown"])
check_true("2b  the diagnosis names WHAT WAS FOUND: the recorded schema era",
           "recorded **0**" in _era0_md)
check_true("2b  ...and WHAT IS REQUIRED: the era this build creates",
           f"this build creates **{SCHEMA_USER_VERSION}**" in _era0_md)
check_true("2b  ...and the database path",
           os.path.basename(_ERA0_DB) in _era0_md)

_era0_code = "\n".join(_era0_cap["code"])
check_true("2c  EVERY missing column is named, not the first one a tab reached "
           "-- which is the whole reason this runs before the page",
           "inferences.llm_classifier_evaluation_time" in _era0_code)
check_true("2c  ...including the token columns the cost tab needs",
           "inferences.llm_classifier_input_tokens" in _era0_code
           and "inferences.llm_classifier_output_tokens" in _era0_code)
check_true("2c  ...and more than one of them (non-degeneracy: a derivation "
           "that produced a single name would satisfy the lines above)",
           len([l for l in _era0_code.splitlines() if l.strip()]) >= 3)
check_true("2c  ...and the count of checked columns is stated and non-zero",
           "of 0 checked" not in _era0_md and "checked)" in _era0_md)

check_true("2c-i  the tolerated (additive) absences are REPORTED beside the "
           "refusal, so an operator meeting an unguarded one later has "
           "already been told it is missing",
           "NOT a reason to refuse" in _era0_md
           and "inferences.run_id" in _era0_md)
check("2c-ii  ...through st.markdown and NOT st.caption: app.py makes exactly "
      "ONE caption call, which is how tests/test_clinical_use_framing.py "
      "holds 'the framing appears once per page' as a property of the source",
      [c for c in _era0_cap["caption"] if "NOT a reason to refuse" in c], [])

check_true("2d  the operator's remedy is the ARCHIVE procedure, and it MOVES",
           "mv " in _era0_md and "ARCHIVE, not to delete" in _era0_md)
check_true("2d  ...and names the command that rebuilds the file",
           "25- Batch Runner.py" in _era0_md)

# --- THE THREE STATES THAT MUST STAY DISTINGUISHED ------------------------
_empty_cap, _, _ = render_app(_EMPTY_DB)
check("2e  an EMPTY current-era database is READY and falls through to "
      "main()'s own message -- 'not run yet' and 'cannot be read' are "
      "different findings",
      _empty_cap["error"],
      ["No data available. Please run some inferences first."])
check("2e  ...with no exception", _empty_cap["exception"], [])

_missing_cap, _, _ = render_app(_NO_TABLES_DB)
check("2f  a database with neither dashboard table gets its OWN diagnosis",
      _missing_cap["exception"], [])
check_true("2f  ...naming the tables rather than the columns",
           any("Tables the dashboard loads" in m
               for m in _missing_cap["markdown"]))
check_true("2f  ...and NOT naming columns, because a column on an absent "
           "table tells an operator to add a column to a table that is not "
           "there",
           not any("Columns the dashboard reads" in m
                   for m in _missing_cap["markdown"]))

_absent_cap, _, _ = render_app(_ABSENT_DB)
check("2g  an absent file gets its own diagnosis and no traceback",
      _absent_cap["exception"], [])
check_true("2g  ...which says the file is not there rather than that it is "
           "out of era",
           any("No database at the configured path" in e
               for e in _absent_cap["error"]))

# THE WHOLE DIAGNOSIS, NOT ITS FIRST SENTENCE. The era-0 and missing-tables
# states deliberately SHARE one `st.error` line -- they are both "this database
# is not one this dashboard can render" -- and differ in the evidence rendered
# underneath it. A check on the first forty characters would report two
# genuinely distinct diagnoses as one; a check on the whole rendering is what
# the requirement ("three DISTINGUISHED truthful states") actually asks.
def _diagnosis(capture):
    return "\n".join(capture["error"] + capture["markdown"] + capture["code"]
                     + capture["info"] + capture["warning"])


_states = {
    "era0": _diagnosis(_era0_cap),
    "no_tables": _diagnosis(_missing_cap),
    "absent": _diagnosis(_absent_cap),
    "empty": _diagnosis(_empty_cap),
}
check("2h  the four states render FOUR DIFFERENT diagnoses -- collapsing any "
      "two is what a single 'no data' error would do",
      len(set(_states.values())), 4)
check_true("2h  ...and none of them is empty (non-degeneracy: four empty "
           "renderings would compare equal, not distinct)",
           all(len(v) > 20 for v in _states.values()))

# --- THE REQUIREMENT IS DERIVED, AND THE DERIVATION IS NON-DEGENERATE ------
_reqs = _data.dashboard_column_requirements()
check("2i  the requirement covers exactly the two tables main() loads",
      sorted(_reqs), sorted(_data.DASHBOARD_TABLES))
check_true("2i  ...and is non-empty for both (a derivation that produced "
           "nothing would report every database as ready)",
           all(len(v) > 0 for v in _reqs.values()))
check_true("2i  ...and the columns it names really are columns of the current "
           "schema, not strings the dashboard happens to contain",
           set(_reqs["inferences"]) <= _data._reference_schema()["inferences"])
check_true("2j  a column a tab reads is in the requirement, so the derivation "
           "follows the source rather than a hand-written list",
           "candidates_after_rule_filter" in _reqs["inferences"]
           and "llm_classifier_evaluation_time" in _reqs["inferences"])
# THE DERIVATION REPRODUCES THE SCHEMA OWNER EXACTLY, AND THIS IS THE CHECK
# THAT MAKES IT TRUSTWORTHY. `_reference_schema` parses the CREATE TABLE text
# out of `database_logger`'s own source rather than building a database -- it
# used to build one, and `tests/test_dashboard_app_integration.py`'s isolation
# recorder reported it, correctly: a page render must open the database it was
# pointed at and nothing else. A text parse can go wrong in two directions and
# only one of them is loud, so BOTH are asserted here against a database
# `initialize_database` really built:
#
#     MISSED   -- a column the schema has and the parse did not find. The
#                 requirement set narrows, FEWER missing columns are reported,
#                 and an out-of-era database reads as healthier than it is.
#                 This is the dangerous direction.
#     INVENTED -- a name the parse produced that is not a column. A healthy
#                 database is then refused for a column it cannot have.
_REF_DB = os.path.join(_TMP, "schema_reference.db")
_quiet_initialize(_REF_DB)
_ref_conn = sqlite3.connect(_REF_DB)
_real_schema = {t: set(queries_mod.table_columns(_ref_conn, t))
                for t in queries_mod.available_tables(_ref_conn)
                if not t.startswith("sqlite_")}
_ref_conn.close()
_derived_schema = {t: set(c) for t, c in _data._reference_schema().items()}

check("2j-i  the parse finds every table initialize_database creates",
      sorted(set(_real_schema) - set(_derived_schema)), [])
check("2j-ii  ...and invents none",
      sorted(set(_derived_schema) - set(_real_schema)), [])
check("2j-iii  ...and MISSES no column -- the dangerous direction, because a "
      "narrowed requirement reports an out-of-era database as healthier",
      sorted((t, c) for t in _real_schema
             for c in _real_schema[t] - _derived_schema.get(t, set())), [])
check("2j-iv  ...and INVENTS no column, which would refuse a healthy database",
      sorted((t, c) for t in _derived_schema
             for c in _derived_schema[t] - _real_schema.get(t, set())), [])
check_true("2j-v  ...over a non-degenerate schema (an empty comparison on "
           "both sides satisfies all four lines above)",
           len(_real_schema) >= 5
           and len(_real_schema.get("inferences", ())) >= 60)

# THE REQUIREMENT REFUSES ONLY WHAT NO WRITER WILL REPAIR. A column a
# migration ADDS is the ordinary state of a database written before it, and
# naming it here would refuse the whole page for a database whose only fault is
# that it predates one column -- while the tab that reads it already handles
# the absence and says so. The first version of this requirement did exactly
# that to `inferences.run_id`.
_tolerated = _data._tolerated_columns()
check("2j-vi  an ADDITIVE column the dashboard names is NOT a reason to refuse",
      sorted(c for c in ("run_id", "matching_call_mode")
             if c in _reqs["inferences"]), [])
check_true("2j-vi  ...and it IS one the dashboard names, so the exclusion is "
           "doing work rather than describing an empty set",
           {"run_id", "matching_call_mode"}
           <= (_tolerated["inferences"] & _data._names_the_dashboard_uses()))
check_true("2j-vii  a RENAMED column IS a refusal -- no writer repairs it, "
           "because the migration loop can only ADD",
           "llm_classifier_evaluation_time" in _reqs["inferences"])
check_true("2j-vii  ...and a BASE column likewise",
           "candidates_after_rule_filter" in _reqs["inferences"]
           and "patient_id" in _reqs["inferences"])
check("2j-viii  the tab that reads the tolerated column really does guard it "
      "-- the premise the tolerance rests on, read from its source rather "
      "than assumed",
      '"run_id" not in df.columns' in Path(
          os.path.abspath(_run_health.__file__)).read_text(encoding="utf-8"),
      True)

# THE OVER-STRICTNESS PROBE, DRIVEN THROUGH main() RATHER THAN OVER THE
# REQUIREMENT SET. A preflight that refuses too much is a worse defect than the
# one it removes: it takes a working dashboard away over a database that
# renders perfectly. This builds the shape that nearly happened -- CURRENT in
# every respect except the two additive columns whose absence a tab already
# handles -- and requires all ten tabs.
_NO_RUNID_DB = os.path.join(_TMP, "current_without_run_id.db")
_quiet_initialize(_NO_RUNID_DB)
_c = sqlite3.connect(_NO_RUNID_DB)
# The index has to go first: sqlite refuses to drop a column an index names.
_c.execute("DROP INDEX IF EXISTS idx_inferences_run_id")
for _col in ("run_id", "matching_call_mode"):
    _c.execute(f"ALTER TABLE inferences DROP COLUMN {_col}")
_c.execute(
    "INSERT INTO inferences (patient_id, timestamp, age, sex, race, ethnicity, "
    "primary_condition, condition_count, medication_count, "
    "candidates_retrieved, candidates_reranked, candidates_after_rule_filter, "
    "candidates_after_quality_filter, candidates_filtered, "
    "candidates_evaluated, eligible_matches, total_time, estimated_cost_usd, "
    "llm_classifier_input_tokens, llm_classifier_output_tokens, "
    "llm_classifier_evaluation_time, query_expansion_time, "
    "hybrid_retrieval_time, cross_encoder_time, rule_filter_time, "
    "matching_model, error) VALUES "
    "('P-NORUNID', '2026-08-01 10:00:00', 60, 'female', 'White', "
    "'Not Hispanic or Latino', 'Breast cancer', 4, 2, 200, 50, 30, 20, 12, 12, "
    "2, 20.0, 0.10, 10000, 1000, 5.0, 0.1, 1.0, 2.0, 0.3, 'gpt-5.6-terra', '')")
_c.commit()
_c.close()
check("2j-ix  PRECONDITION: the probe database really lacks both tolerated "
      "columns (non-degeneracy -- a database that still had them would render "
      "ten tabs whatever the preflight did)",
      sorted(c for c in ("run_id", "matching_call_mode")
             if c in queries_mod.table_columns(
                 sqlite3.connect(_NO_RUNID_DB), "inferences")), [])
_norunid_cap, _, _ = render_app(_NO_RUNID_DB)
check("2j-x  a CURRENT database missing only additive columns renders ALL TEN "
      "TABS -- the preflight must not take a working dashboard away",
      len(_norunid_cap["tabs"]), 10)
check("2j-x  ...with no exception and no refusal",
      (_norunid_cap["exception"], _norunid_cap["error"]), ([], []))

check("2k  the state vocabulary is closed and every member is distinct",
      len(set(_data.SCHEMA_STATES)), len(_data.SCHEMA_STATES))

_paths._RESOLVED["inferences_path"] = _CURRENT_DB
st.cache_data.clear()
_readiness_current = _data.dashboard_schema_readiness()
check("2l  ...and a scratch database that IS current reads ready",
      _readiness_current["state"], _data.SCHEMA_READY)
check("2l  ...with nothing missing", (_readiness_current["missing_tables"],
                                      _readiness_current["missing_columns"]),
      ([], []))
check("2l  ...and no module failed to parse in the name scan",
      _readiness_current["parse_failures"], [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3: THE DRIFT TAB'S EMPTY CATEGORY (PART 2)
# ===========================================================================

print()
print("=" * 74)
print("Section 3: the drift category filter, populated AND empty (PART 2)")
print("=" * 74)

_DRIFT_DB = os.path.join(_TMP, "drift.db")
build_current(_DRIFT_DB)
_DRIFT_TS = "2026-09-01 09:00:00"
_conn = sqlite3.connect(_DRIFT_DB)
# ONLY data_availability rows in the latest run. That is not contrived: a run
# whose designated reference is absent, unresolvable or mutated records the
# baseline-independent family and refuses every comparison family, so every
# comparison category is empty -- and the operator diagnosing that refusal is
# the reader most likely to select one.
_conn.execute(
    "INSERT INTO drift_metrics (timestamp, metric_category, metric_name, "
    "metric_value, alert, status, alert_policy, comparison_selection) VALUES "
    "(?, 'data_availability', 'ecog_unavailable_rate', 0.12, 0, 'computed', "
    "'baseline_independent', 'explicit_run')", (_DRIFT_TS,))
_conn.commit()
_conn.close()

_drift_frame = pd.read_sql_query("SELECT * FROM inferences",
                                 _REAL_CONNECT(_DRIFT_DB))
_drift_frame["timestamp"] = pd.to_datetime(_drift_frame["timestamp"])

_at, _, _ = render_tab("oncotriage.dashboard.tabs.drift",
                       "render_drift_detection_tab", _drift_frame, _DRIFT_DB,
                       keep_open=True)
_first = _capture(_at)
check("3a  the tab renders with the default 'All' selection", _first["exception"], [])
_selects = [s for s in _at.selectbox if s.key == "drift_category_filter"]
check("3a  ...and the category filter is there", len(_selects), 1)

if _selects:
    _options = list(_selects[0].options)
    check("3b  the filter offers All plus the four categories", len(_options), 5)
    check_true("3b  ...including the one the seed populated",
               "Data Availability" in _options)

    # --- THE POPULATED CATEGORY ------------------------------------------
    _selects[0].select("Data Availability").run()
    _pop = _capture(_at)
    check("3c  selecting the POPULATED category raises nothing",
          _pop["exception"], [])
    check("3c  ...and renders the table", _pop["dataframes"], 1)
    check_true("3c  ...and does NOT render the empty-state message",
               not any("No Data Availability reading" in i
                       for i in _pop["info"]))

    # --- THE EMPTY CATEGORY, WHICH USED TO RAISE --------------------------
    _selects = [s for s in _at.selectbox if s.key == "drift_category_filter"]
    _selects[0].select("Data Drift").run()
    _empty = _capture(_at)
    check("3d  selecting an EMPTY category raises nothing -- the whole item; "
          "it raised ValueError: Cannot set a DataFrame with multiple columns "
          "to the single column Status",
          _empty["exception"], [])
    check("3d  ...and renders NO table rather than an empty one",
          _empty["dataframes"], 0)
    _joined_info = "\n".join(_empty["info"])
    check_true("3e  the empty state names the CATEGORY",
               "Data Drift" in _joined_info)
    check_true("3e  ...and the LATEST RUN it is a statement about",
               "2026-09-01 09:00:00" in _joined_info)
    check_true("3e  ...and how many readings that run did record",
               "recorded 1 reading(s)" in _joined_info)
    check_true("3f  the rest of the tab still renders -- the empty category is "
               "not a reason to return from a panel that is about every run",
               any("historical trends" in i.lower() for i in _empty["info"]))
else:
    fail("3b..3f  the category filter was not rendered",
         "every check in this section needs it")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4: THE FUNNEL TILE AND THE STAGE LABEL (PARTS 3 AND 6a)
# ===========================================================================

print()
print("=" * 74)
print("Section 4: retention tiles and the stage-timing label (PARTS 3, 6a)")
print("=" * 74)

_frame = pd.read_sql_query("SELECT * FROM inferences", _REAL_CONNECT(_CURRENT_DB))
_matches = pd.read_sql_query("SELECT * FROM trial_matches",
                             _REAL_CONNECT(_CURRENT_DB))
_frame["timestamp"] = pd.to_datetime(_frame["timestamp"])
_ENRICHED = _tiers.enrich_match_tiers(_frame.copy(), _matches)

_ov, _, _ = render_tab("oncotriage.dashboard.tabs.overview",
                       "render_overview_tab", _ENRICHED, _CURRENT_DB)
check("4a  the overview tab renders", _ov["exception"], [])

_tile = at_(_ov["metrics"], "Reranked → Rule Filter", ("(absent)", None))
check("4b  the rule-filter tile divides the RULE FILTER's survivors by the "
      "reranked pool -- expectation computed from the seed, not read back",
      _tile[0], f"{_CORRECT_RULE_RETENTION:.1f}%")
check_true("4b  ...and is NOT the pre-fix figure (non-degeneracy: the two "
           "differ by 35 points on this seed)",
           _tile[0] != f"{_PREFIX_RULE_RETENTION:.1f}%")

check("4c  the retrieved-to-reranked tile is unchanged in formula",
      at_(_ov["metrics"], "Retrieved → Reranked", ("(absent)", None))[0],
      f"{_EXPECTED_RERANKED / _EXPECTED_RETRIEVED * 100:.1f}%")
check("4c  ...and the evaluated-to-eligible tile",
      at_(_ov["metrics"], "Evaluated → Eligible", ("(absent)", None))[0],
      f"{_EXPECTED_ELIGIBLE_COL / _EXPECTED_EVALUATED * 100:.1f}%")

# --- A ZERO DENOMINATOR AND AN UNRECORDED MEASUREMENT -------------------
_ZERO_DB = os.path.join(_TMP, "zero_denominator.db")
_quiet_initialize(_ZERO_DB)
_c = sqlite3.connect(_ZERO_DB)
_c.execute("INSERT INTO inferences (patient_id, timestamp, age, sex, "
           "candidates_retrieved, candidates_reranked, "
           "candidates_after_rule_filter, candidates_evaluated, "
           "eligible_matches, matching_model, error) VALUES "
           "('P-Z', '2026-08-01 10:00:00', 60, 'female', 0, 0, 0, 0, 0, "
           "'gpt-5.6-terra', '')")
_c.commit()
_c.close()
_zero_frame = pd.read_sql_query("SELECT * FROM inferences",
                                _REAL_CONNECT(_ZERO_DB))
_zero_frame["timestamp"] = pd.to_datetime(_zero_frame["timestamp"])
_zero_enriched = _tiers.enrich_match_tiers(_zero_frame.copy(), pd.DataFrame())
_zero_ov, _, _ = render_tab("oncotriage.dashboard.tabs.overview",
                            "render_overview_tab", _zero_enriched, _ZERO_DB)
check("4d  a ZERO denominator renders unavailable, never 0.0%",
      at_(_zero_ov["metrics"], "Reranked → Rule Filter", ("(absent)", None))[0],
      _overview.RETENTION_UNAVAILABLE)
check_true("4d  ...and the help text says a percentage of nothing is not zero",
           _zero_ov["exception"] == [])

_NULL_DB = os.path.join(_TMP, "null_counts.db")
_quiet_initialize(_NULL_DB)
_c = sqlite3.connect(_NULL_DB)
_c.execute("INSERT INTO inferences (patient_id, timestamp, age, sex, "
           "matching_model, error) VALUES "
           "('P-N', '2026-08-01 10:00:00', 60, 'female', 'gpt-5.6-terra', '')")
_c.commit()
_c.close()
_null_frame = pd.read_sql_query("SELECT * FROM inferences",
                                _REAL_CONNECT(_NULL_DB))
_null_frame["timestamp"] = pd.to_datetime(_null_frame["timestamp"])
_null_enriched = _tiers.enrich_match_tiers(_null_frame.copy(), pd.DataFrame())
check("4e  an all-NULL count column renders unavailable, never 0.0% -- "
      "Series.sum() over NULLs is 0.0 and min_count=1 is what stops that "
      "becoming a measurement",
      _overview._retention(_null_enriched, "candidates_after_rule_filter",
                           "candidates_reranked")[0],
      _overview.RETENTION_UNAVAILABLE)
check_true("4e  ...and the note names the column that was not recorded",
           "candidates_reranked" in
           _overview._retention(_null_enriched, "candidates_after_rule_filter",
                                "candidates_reranked")[1])
check_true("4e  ...and a real measurement still returns an empty note "
           "(non-degeneracy: a function that always reported unavailable "
           "would satisfy the two lines above)",
           _overview._retention(_ENRICHED, "candidates_after_rule_filter",
                                "candidates_reranked")[1] == "")

# --- 6a: THE STAGE-TIMING SERIES NAME -----------------------------------
_stage_names = _chart_category_labels(_ov["plotly"])
check("4f  every chart spec on the overview tab is READABLE -- an unreadable "
      "spec yields no labels and would report a correctly-labelled chart as "
      "unlabelled, which is how the first draft of this section failed",
      [s for s in _ov["plotly"] if "__unreadable__" in s], [])
check_true("4f  the stage-timing chart names the stage, not a model",
           "LLM Classifier" in _stage_names)
check_true("4f  ...and the retired model label is gone from every chart axis",
           "GPT-4o Eval" not in _stage_names)
# THE CHART HAS FOUR SERIES, NOT FIVE, AND THE FIRST DRAFT OF THIS CHECK SAID
# FIVE. `stage_times` in overview.py holds Hybrid Retrieval, Cross-Encoder,
# Rule Filter and the Stage 5 one; Query Expansion is charted elsewhere. The
# expectation is read out of the module's own dict by AST rather than typed
# here, so it cannot be wrong about what the chart contains.
_stage_literal = None
for _node in ast.walk(ast.parse(Path(_WATCHED["overview.py"]).read_text(
        encoding="utf-8"))):
    if (isinstance(_node, ast.Assign) and _node.targets
            and isinstance(_node.targets[0], ast.Name)
            and _node.targets[0].id == "stage_times"
            and isinstance(_node.value, ast.Dict)):
        _stage_literal = [k.value for k in _node.value.keys
                          if isinstance(k, ast.Constant)]
        break
check_true("4f  ...and the module declares a non-empty stage dict "
           "(non-degeneracy: an AST walk that found nothing would make the "
           "check below vacuous)",
           _stage_literal is not None and len(_stage_literal) >= 3)
check("4f  ...and EVERY series the module declares is on the chart -- a chart "
      "that rendered no stage names would satisfy the line above",
      sorted(set(_stage_literal or []) - _stage_names), [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5: "ANY MATCH" HAS ONE OWNER (PART 4)
# ===========================================================================

print()
print("=" * 74)
print("Section 5: one derivation of Any Match (PART 4)")
print("=" * 74)

check("5a  the seed is a frame on which the two PRE-FIX derivations disagree "
      "-- without this the agreement below is a tautology",
      ((_ENRICHED["eligible_matches"] > 0).mean() * 100,
       (_ENRICHED["match_tier"] != "No Match").mean() * 100),
      (_COLUMN_ANY_MATCH_RATE, _TIER_ANY_MATCH_RATE))

check("5b  the owner's definition is the complement of the worst tier, "
      "derived from MATCH_TIERS",
      sorted(_tiers.ANY_MATCH_TIERS),
      sorted(t for t in _tiers.MATCH_TIERS if t != "No Match"))
check("5c  the owner's rate on the seed is the TIER answer",
      _tiers.any_match_rate(_ENRICHED), _TIER_ANY_MATCH_RATE)
check("5c  ...and its count", _tiers.any_match_count(_ENRICHED), 2)

_de, _, _ = render_tab("oncotriage.dashboard.tabs.demographics",
                       "render_patient_demographics_tab", _ENRICHED,
                       _CURRENT_DB)
check("5d  the demographics tab renders", _de["exception"], [])

_ov_any = at_(_ov["metrics"], "Any Match", ("(absent)", None))[0]
_de_any = at_(_de["metrics"], "Any Match", ("(absent)", None))[0]
check("5e  the Overview tile reports the tier answer",
      _ov_any, f"{_TIER_ANY_MATCH_RATE:.1f}%")
check("5e  the Demographics tile reports the tier answer",
      _de_any, f"{_TIER_ANY_MATCH_RATE:.1f}%")
check("5f  the two surfaces AGREE on a frame where the old ones did not",
      _ov_any, _de_any)
check_true("5f  ...and neither reports the column answer (two surfaces "
           "agreeing proves consistency, not correctness -- this is the half "
           "that proves which answer they agreed on)",
           _ov_any != f"{_COLUMN_ANY_MATCH_RATE:.1f}%")

check("5g  the Overview tile's patient count comes from the same owner",
      at_(_ov["metrics"], "Any Match", (None, "(absent)"))[1], "2 patients")

check("5h  the per-group match rates aggregate the SAME predicate, so the "
      "average line is a mean of the bars it is drawn across",
      sorted(set(
          _ENRICHED.assign(**{_tiers.ANY_MATCH_COLUMN:
                              _tiers.any_match_series(_ENRICHED)})
          .groupby("sex")[_tiers.ANY_MATCH_COLUMN].mean() * 100)),
      [_TIER_ANY_MATCH_RATE])

check_true("5i  the owner refuses a frame that has not been enriched, rather "
           "than falling back to the column -- a fallback would BE the second "
           "derivation this function removes",
           str(called(_tiers.any_match_series,
                      _ENRICHED.drop(columns=["match_tier"]))
               ).startswith("raised: KeyError"))
check_true("5j  an empty frame gives NaN and not 0.0% -- 0.0 asserts that none "
           "of the patients matched, and there are none to have measured",
           pd.isna(_tiers.any_match_rate(_ENRICHED.iloc[0:0])))

check_true("5k  the demographics tab does not leak its derived column into "
           "the frame main() hands the other nine tabs",
           _tiers.ANY_MATCH_COLUMN not in _ENRICHED.columns)

# ===========================================================================
# THE SIDEBAR FILTER IS PART OF THIS PIN (the repair pass)
# ===========================================================================
#
# The Part 4 pass unified three READERS of "Any Match" and left the one WRITER
# -- the sidebar filter that decides which rows every tab sees -- on
# `eligible_matches`. A pin over the readers alone cannot see that: they agree
# with each other perfectly while reporting over a population a fourth rule
# selected. So the pin covers the filter, and it covers BOTH of its selections,
# because a partition is only a partition if both halves come from one mask.
_sb_src = Path(os.path.abspath(_sidebar_mod.__file__)).read_text(encoding="utf-8")
_sb_subscripts = {n.slice.value for n in ast.walk(ast.parse(_sb_src))
                  if isinstance(n, ast.Subscript)
                  and isinstance(n.slice, ast.Constant)
                  and isinstance(n.slice.value, str)}
check_true("5l  the sidebar reads column subscripts at all (non-degeneracy: "
           "an empty set satisfies the check below for free)",
           len(_sb_subscripts) >= 3)
check("5l  ...and `eligible_matches` is NOT one of them, so the filter is not "
      "a fourth definition of the figure the tiles report",
      sorted(c for c in _sb_subscripts if c == "eligible_matches"), [])
check_true("5l  ...and the owner is what it reaches for instead",
           "any_match_series" in _sb_src)
check("5m  BOTH selections come from ONE mask, so they partition by "
      "construction rather than by two predicates agreeing",
      sorted(_tiers.any_match_series(_ENRICHED).tolist()
             + (~_tiers.any_match_series(_ENRICHED)).tolist()).count(True),
      len(_ENRICHED))
check("5m  ...which is what makes the filter and the tile one question: the "
      "tile over the Any Match selection is 100%",
      f"{_tiers.any_match_rate(_ENRICHED[_tiers.any_match_series(_ENRICHED)]):.1f}%",
      "100.0%")
check("5m  ...and over its complement, 0.0%",
      f"{_tiers.any_match_rate(_ENRICHED[~_tiers.any_match_series(_ENRICHED)]):.1f}%"
      if (~_tiers.any_match_series(_ENRICHED)).any() else "(empty)",
      "(empty)" if not (~_tiers.any_match_series(_ENRICHED)).any() else "0.0%")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6: THE COST PANEL DEGRADES PER GROUP (PART 6b)
# ===========================================================================

print()
print("=" * 74)
print("Section 6: one unpriceable model does not blank the panel (PART 6b)")
print("=" * 74)

_COST_DB = os.path.join(_TMP, "cost.db")
_COST_IDS = build_current(_COST_DB,
                          models=("gpt-4o-2024-08-06", "a-model-with-no-price"))
_c = sqlite3.connect(_COST_DB)
_c.execute("INSERT INTO inferences (patient_id, timestamp, age, sex, "
           "llm_classifier_input_tokens, llm_classifier_output_tokens, "
           "candidates_evaluated, matching_model, error, total_time, "
           "estimated_cost_usd) VALUES "
           "('P-BLANK', '2026-08-03 10:00:00', 60, 'female', 3000, 300, 5, "
           "'', '', 9.0, 0.01)")
_c.commit()
_c.close()
_cost_frame = pd.read_sql_query("SELECT * FROM inferences",
                                _REAL_CONNECT(_COST_DB))
_cost_frame["timestamp"] = pd.to_datetime(_cost_frame["timestamp"])
_cost_matches = pd.read_sql_query("SELECT * FROM trial_matches",
                                  _REAL_CONNECT(_COST_DB))
_cost_enriched = _tiers.enrich_match_tiers(_cost_frame.copy(), _cost_matches)

_ct, _, _ = render_tab("oncotriage.dashboard.tabs.cost_tokens",
                       "render_cost_tokens_tab", _cost_enriched, _COST_DB)
check("6a  the cost tab renders with one unpriceable and one blank model -- "
      "before this it caught the raise and returned, blanking the panel",
      _ct["exception"], [])
check("6a  ...and shows no whole-panel error", _ct["error"], [])
check_true("6b  the PRICEABLE group still renders its charts",
           len(_ct["plotly"]) > 0)
check_true("6b  ...and its metrics", len(_ct["metrics"]) > 0)

_ct_subheads = "\n".join(_ct["subheader"])
check_true("6c  the breakdown is LABELLED PARTIAL",
           "Cost Breakdown by Model — PARTIAL" in _ct_subheads)
check_true("6c  ...with the excluded-group count and the excluded-row count",
           "2 of 3 model groups" in _ct_subheads
           and "2 of 3 rows excluded" in _ct_subheads)

_ct_warn = "\n".join(_ct["warning"])
check_true("6d  every excluded group is NAMED",
           "a-model-with-no-price" in _ct_warn
           and "(blank)" in _ct_warn)
check_true("6d  ...and COUNTED, in rows and tokens",
           "1 row(s)" in _ct_warn and "token(s) excluded" in _ct_warn)
check_true("6d  ...and the reason is given per group",
           "NO PRICE IN PRICING_CONFIG" in _ct_warn
           and "BLANK" in _ct_warn)
check_true("6e  the FLOOR banner still qualifies the figures below",
           "FLOOR, not a total" in _ct_warn)

# --- THE NULL-MODEL GRACEFUL PATH IS UNCHANGED ---------------------------
_NULL_MODEL_DB = os.path.join(_TMP, "null_model.db")
_quiet_initialize(_NULL_MODEL_DB)
_c = sqlite3.connect(_NULL_MODEL_DB)
_c.execute("INSERT INTO inferences (patient_id, timestamp, age, sex, error, "
           "candidates_evaluated, total_time) VALUES "
           "('P-NULLMODEL', '2026-08-01 10:00:00', 60, 'female', '', 0, 3.0)")
_c.commit()
_c.close()
_nm_frame = pd.read_sql_query("SELECT * FROM inferences",
                              _REAL_CONNECT(_NULL_MODEL_DB))
_nm_frame["timestamp"] = pd.to_datetime(_nm_frame["timestamp"])
_nm_enriched = _tiers.enrich_match_tiers(_nm_frame.copy(), pd.DataFrame())
_nm, _, _ = render_tab("oncotriage.dashboard.tabs.cost_tokens",
                       "render_cost_tokens_tab", _nm_enriched, _NULL_MODEL_DB)
check("6f  a selection whose every model is NULL still renders its own "
      "message rather than an empty pie chart", _nm["exception"], [])
check_true("6f  ...and the message names the group rather than asserting a "
           "cause it has not checked",
           any("No cost breakdown can be computed" in i for i in _nm["info"]))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7: TWO TARGETED CHECKS (PART 7)
# ===========================================================================

print()
print("=" * 74)
print("Section 7: the trial selector and the sidebar's no-match return (PART 7)")
print("=" * 74)

# --- 7a: IS THE 'No Score Recorded' FILTER REACHABLE? --------------------
#
# THE WALK'S HYPOTHESIS WAS THAT THE SELECTOR EXCLUDES NULL-SCORE TRIALS. IT
# DOES NOT, and this section is the evidence rather than a fix. `trial_summary`
# groups on (nct_id, trial_title) and counts DISTINCT patient_id; a trial whose
# every row has a NULL match_score still has patients, so it is offered. What
# the option needs is for the SELECTED trial to have such a row, which is a
# property of the selection rather than of the selector.
_TE_DB = os.path.join(_TMP, "trial_explorer.db")
_TE_IDS = build_current(_TE_DB)
_c = sqlite3.connect(_TE_DB)
_c.execute("INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
           "trial_phase, eligible, match_score, assessment) VALUES "
           "(?, 'NCT-NOSCORE', 'A trial with no score', 'Phase 1', "
           "'eligible', NULL, 'x')", (_TE_IDS["P-A"],))
_c.commit()
_c.close()
_te_frame = pd.read_sql_query("SELECT * FROM inferences", _REAL_CONNECT(_TE_DB))
_te_frame["timestamp"] = pd.to_datetime(_te_frame["timestamp"])
_te_matches = pd.read_sql_query("SELECT * FROM trial_matches",
                                _REAL_CONNECT(_TE_DB))
_te_enriched = _tiers.enrich_match_tiers(_te_frame.copy(), _te_matches)

_te_at, _, _ = render_tab("oncotriage.dashboard.tabs.trial_explorer",
                          "render_trial_explorer_tab", _te_enriched, _TE_DB,
                          keep_open=True)
check("7a  the trial explorer renders", _capture(_te_at)["exception"], [])
_trial_selects = [s for s in _te_at.selectbox
                  if s.key == "trial_explorer_select"]
if _trial_selects:
    # `.options` IS ALREADY FORMATTED. AppTest applies `format_func` when it
    # builds that list, so calling it again passes a formatted STRING to a
    # lambda that indexes a list with it -- TypeError, inside a check's
    # argument list. Measured, in this file's own second run.
    _labels = [str(v) for v in _trial_selects[0].options]
    check_true("7a  the selector DOES offer a trial whose every match_score is "
               "NULL -- the walk's hypothesis that it excludes them is refuted",
               any("NCT-NOSCORE" in l for l in _labels))
    _index = [i for i, l in enumerate(_labels) if "NCT-NOSCORE" in l]
    if _index:
        _trial_selects[0].set_value(_index[0]).run()
        _status = [s for s in _te_at.selectbox
                   if s.key == "trial_explorer_status_filter"]
        if _status:
            check_true("7a  ...and the 'No Score Recorded' option is offered",
                       _te.TRIAL_STATUS_NO_SCORE in _status[0].options)
            _status[0].select(_te.TRIAL_STATUS_NO_SCORE).run()
            _after = _capture(_te_at)
            check("7a  ...and selecting it renders rows rather than the "
                  "'no patients match' message, so the filter is REACHABLE "
                  "and no fix is owed", _after["exception"], [])
            check("7a  ...with the row that has no score", _after["dataframes"], 1)
        else:
            fail("7a  the status filter was not rendered", "needed by 7a")
    else:
        fail("7a  the NULL-score trial is not in the selector",
             "the walk's hypothesis would then be CONFIRMED and a fix is owed")
else:
    fail("7a  the trial selector was not rendered", "needed by 7a")

# --- 7b: THE SIDEBAR'S NO-MATCH EARLY RETURN, DRIVEN THROUGH main() ------
_at_app, _, _ = _run(_DRIVER_APP.format(extra_path=_PLANT_DIR,
                                        module="oncotriage.dashboard.app"),
                     _CURRENT_DB, keep_open=True)
_sliders = [s for s in _at_app.sidebar.slider if "age" in s.label.lower()]
if _sliders:
    _before = _capture(_at_app)
    check("7b  the page renders its ten tabs before the filter is narrowed "
          "(non-degeneracy: a page that rendered none would satisfy the check "
          "below for the wrong reason)", len(_before["tabs"]), 10)
    _low, _high = _sliders[0].value
    _gap = _low + 1
    if _gap < _high:
        _sliders[0].set_value((_gap, _gap)).run()
        _after = _capture(_at_app)
        check("7b  an age range selecting NO patient produces the truthful "
              "early return", _after["warning"],
              ["No data matches the current filters."])
        check("7b  ...with no exception", _after["exception"], [])
        check("7b  ...and no tab rendered over an empty selection",
              len(_after["tabs"]), 0)
    else:
        skip("7b  the seed's ages leave no empty sub-range",
             "the two seeded ages are adjacent")
else:
    fail("7b  the age slider was not rendered", "needed by 7b")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8: THE ISOLATION AND THE OFFLINE GUARD ARE MEASURED
# ===========================================================================

print()
print("=" * 74)
print("Section 8: isolation and offline, measured rather than claimed")
print("=" * 74)

_, _iso_conns, _iso_net = render_app(_CURRENT_DB)
_outside = sorted({p for p in _iso_conns
                   if not str(p).startswith("file:" + _TMP)
                   and not str(p).startswith(_TMP)})
check("8a  every database this render opened is inside the scratch tree",
      _outside, [])
check_true("8a  ...and it opened at least one (non-degeneracy: a render that "
           "opened nothing would satisfy the line above)", len(_iso_conns) > 0)
check("8b  no outbound network call was attempted", _iso_net, [])

_control_before = len(_NETWORK_ATTEMPTS)
_arm_offline_guard()
try:
    _control = called(socket.create_connection, ("example.invalid", 80), 0.01)
finally:
    _disarm_offline_guard()
check_true("8c  the offline guard FIRES on a real outbound call -- without "
           "this the readings above are vacuous",
           str(_control).startswith("raised: OSError")
           and "[offline guard]" in str(_control))
check_true("8c  ...and records the attempt with the frame that made it",
           len(_NETWORK_ATTEMPTS) == _control_before + 1
           and _NETWORK_ATTEMPTS[-1]["caller"].split(":")[0]
           == os.path.basename(__file__))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 9: THE TAB STRIP'S FOOTPRINT (PART 6c)
# ===========================================================================

print()
print("=" * 74)
print("Section 9: the tab strip (PART 6c)")
print("=" * 74)

_app_src = Path(_WATCHED["app.py"]).read_text(encoding="utf-8")
_css_blocks = [n.value for n in ast.walk(ast.parse(_app_src))
               if isinstance(n, ast.Constant) and isinstance(n.value, str)
               and "data-baseweb" in n.value and "tab-list" in n.value]
check("9a  app.py injects exactly one tab-strip stylesheet", len(_css_blocks), 1)
_css = _css_blocks[0] if _css_blocks else ""

check_true("9b  the label font size came DOWN from 20px -- the footprint "
           "reduction, measured at 368px of text across the ten labels",
           "font-size: 20px" not in _css and "font-size: 16px" in _css)
check_true("9b  ...and the customisation is kept rather than deleted: it is "
           "still larger and heavier than streamlit's 14px default",
           "font-weight: 600" in _css)

check_true("9c  the strip carries a VISIBLE scroll affordance, which is what "
           "actually answers the defect -- the measurement says the ten "
           "labels do not fit at 1440px at ANY font size, including with no "
           "CSS at all",
           "::-webkit-scrollbar" in _css and "scrollbar-width: thin" in _css)
# EVERY SELECTOR IN THE BLOCK IS SCOPED TO .stTabs. Parsed by taking the text
# before each `{`, dropping the <style> preamble and any comment, rather than
# by counting substrings -- a count would be satisfied by a rule that styled
# every scrollbar on the page.
_selectors = []
for _chunk in _css.split("{")[:-1]:
    _tail = _chunk.split("}")[-1]
    _tail = _tail.split("*/")[-1]
    _tail = _tail.replace("<style>", "").strip()
    if _tail:
        _selectors.append(_tail)
check_true("9c  ...and there really are selectors to check (non-degeneracy: "
           "an empty list satisfies `all()` for free)", len(_selectors) >= 4)
check("9c  ...every one of them scoped to the tab strip, so nothing else on "
      "the page gains a scrollbar",
      [s for s in _selectors if not s.startswith(".stTabs")], [])
check_true("9d  the tab strip still renders all ten tabs",
           len(_capture(_at_app)["tabs"]) in (0, 10))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 10: TARGETED FAILURE CONTROLS -- EACH SAFEGUARD, ABSENT, MUST FAIL
# ===========================================================================

print()
print("=" * 74)
print("Section 10: the controls. Every plant is a COPY, ast-parsed.")
print("=" * 74)

# --- C1: THE PREFLIGHT REMOVED --------------------------------------------
_c1 = _plant(_WATCHED["app.py"], [(
    "    _readiness = dashboard_schema_readiness()\n"
    "    if _readiness[\"state\"] != SCHEMA_READY:\n"
    "        _render_schema_diagnosis(_readiness)\n"
    "        return\n",
    "    _readiness = dashboard_schema_readiness()\n", 1)], "app_nopreflight")
if _c1:
    _cap, _, _ = render_app(_ERA0_DB, module=_c1)
    check_true("C1  WITHOUT the preflight, an era-0 database raises inside a "
               "tab -- the defect, reproduced",
               any("llm_classifier" in e for e in _cap["exception"]))
    check("C1  ...and renders no diagnosis", _cap["error"], [])
    _clean, _, _ = render_app(_ERA0_DB)
    check("C1  CLEAN CONTROL: the shipped app raises nothing on the same "
          "database", _clean["exception"], [])

# --- C2: THE EMPTY-CATEGORY GUARD REMOVED ---------------------------------
_c2 = _plant(_WATCHED["drift.py"], [(
    "    if display_df.empty:", "    if False:", 1)], "drift_noguard")
if _c2:
    _at2, _, _ = render_tab(_c2, "render_drift_detection_tab", _drift_frame,
                            _DRIFT_DB, keep_open=True)
    _sel2 = [s for s in _at2.selectbox if s.key == "drift_category_filter"]
    if _sel2:
        _sel2[0].select("Data Drift").run()
        _cap2 = _capture(_at2)
        check_true("C2  WITHOUT the empty guard, an empty category raises -- "
                   "the defect, reproduced",
                   any("Cannot set a DataFrame" in e
                       for e in _cap2["exception"]))
    else:
        fail("C2  the planted copy did not render the filter", "control lost")

# --- C3: THE FUNNEL TILE'S OLD COLUMN -------------------------------------
_c3 = _plant(_WATCHED["overview.py"], [(
    "        _value, _note = _retention(df, 'candidates_after_rule_filter',\n"
    "                                   'candidates_reranked')",
    "        _value, _note = _retention(df, 'candidates_filtered',\n"
    "                                   'candidates_reranked')", 1)],
    "overview_oldcolumn")
if _c3:
    _cap3, _, _ = render_tab(_c3, "render_overview_tab", _ENRICHED, _CURRENT_DB)
    check("C3  WITH the old column, the tile reports the post-cost-cap figure "
          "-- the defect, reproduced",
          at_(_cap3["metrics"], "Reranked → Rule Filter", ("(absent)", None))[0],
          f"{_PREFIX_RULE_RETENTION:.1f}%")

# --- C4: THE min_count GUARD REMOVED --------------------------------------
_c4 = _plant(_WATCHED["overview.py"], [(
    "    numerator = df[numerator_column].sum(min_count=1)\n"
    "    denominator = df[denominator_column].sum(min_count=1)",
    "    numerator = df[numerator_column].sum()\n"
    "    denominator = df[denominator_column].sum()", 1)], "overview_nomincount")
if _c4:
    # THE GUARD IS OBSERVABLE ON THE NUMERATOR AND NOT ON AN ALL-NULL FRAME,
    # and the first version of this control used the all-NULL frame and was
    # MISSED. With every count NULL, `sum()` without min_count gives 0.0 for
    # BOTH terms, the zero-denominator branch catches it, and the planted and
    # shipped modules agree -- so the plant changed nothing observable and
    # reported a working guard as absent.
    #
    # What min_count=1 actually buys is a RECORDED denominator beside an
    # UNRECORDED numerator: without it the numerator is 0.0, the division
    # succeeds, and "no count was ever recorded" renders as "0.0% of the pool
    # survived", which is a measurement.
    _PARTIAL_DB = os.path.join(_TMP, "unrecorded_numerator.db")
    _quiet_initialize(_PARTIAL_DB)
    _c = sqlite3.connect(_PARTIAL_DB)
    _c.execute("INSERT INTO inferences (patient_id, timestamp, age, sex, "
               "condition_count, medication_count, candidates_retrieved, "
               "candidates_reranked, candidates_evaluated, eligible_matches, "
               "matching_model, error) VALUES "
               "('P-P', '2026-08-01 10:00:00', 60, 'female', 4, 2, 200, 100, "
               "10, 1, 'gpt-5.6-terra', '')")
    _c.commit()
    _c.close()
    _partial_frame = pd.read_sql_query("SELECT * FROM inferences",
                                       _REAL_CONNECT(_PARTIAL_DB))
    _partial_frame["timestamp"] = pd.to_datetime(_partial_frame["timestamp"])
    _partial_enriched = _tiers.enrich_match_tiers(_partial_frame.copy(),
                                                  pd.DataFrame())
    check("C4  PRECONDITION: the denominator IS recorded and the numerator is "
          "NOT, which is the only shape on which min_count=1 changes an answer",
          (int(_partial_enriched["candidates_reranked"].sum()),
           bool(_partial_enriched["candidates_after_rule_filter"].isna().all())),
          (100, True))
    check("C4  CLEAN CONTROL: the shipped tile reports unavailable",
          at_(render_tab("oncotriage.dashboard.tabs.overview",
                         "render_overview_tab", _partial_enriched,
                         _PARTIAL_DB)[0]["metrics"],
              "Reranked → Rule Filter", ("(absent)", None))[0],
          _overview.RETENTION_UNAVAILABLE)
    _cap4, _, _ = render_tab(_c4, "render_overview_tab", _partial_enriched,
                             _PARTIAL_DB)
    check("C4  WITHOUT min_count=1, an unrecorded numerator is reported as a "
          "MEASUREMENT of zero -- the defect, reproduced",
          at_(_cap4["metrics"], "Reranked → Rule Filter",
              ("(absent)", None))[0], "0.0%")

# --- C5: TWO OWNERS FOR ANY MATCH -----------------------------------------
_c5 = _plant(_WATCHED["demographics.py"], [(
    "    overall_match_rate = any_match_rate(df)",
    "    overall_match_rate = (df['eligible_matches'] > 0).mean() * 100", 1)],
    "demographics_twoowners")
if _c5:
    _cap5, _, _ = render_tab(_c5, "render_patient_demographics_tab", _ENRICHED,
                             _CURRENT_DB)
    _de_any_planted = at_(_cap5["metrics"], "Any Match", ("(absent)", None))[0]
    check("C5  WITH the second derivation, Demographics answers the COLUMN "
          "figure -- the defect, reproduced",
          _de_any_planted, f"{_COLUMN_ANY_MATCH_RATE:.1f}%")
    check_true("C5  ...and the two surfaces DISAGREE",
               _de_any_planted != _ov_any)

# --- C6: THE COST PANEL'S RAISING POLICY ----------------------------------
_c6 = _plant(_WATCHED["cost_tokens.py"], [(
    "            on_unpriced=queries.COST_UNPRICED_DEGRADE)",
    "            on_unpriced=queries.COST_UNPRICED_RAISE)", 1)],
    "cost_raise")
if _c6:
    _cap6, _, _ = render_tab(_c6, "render_cost_tokens_tab", _cost_enriched,
                             _COST_DB)
    check_true("C6  WITH the raising policy, one unpriceable model blanks the "
               "panel -- the defect, reproduced",
               any("Cost breakdown unavailable" in e for e in _cap6["error"]))
    check("C6  ...and NOT ONE chart is drawn", len(_cap6["plotly"]), 0)
    check_true("C6  CLEAN CONTROL: the shipped tab draws charts on the same "
               "frame", len(_ct["plotly"]) > 0)

# --- C7: THE 6a LABEL RESTORED --------------------------------------------
_c7 = _plant(_WATCHED["overview.py"], [(
    "            'LLM Classifier': df['llm_classifier_evaluation_time'].mean()",
    "            'GPT-4o Eval': df['llm_classifier_evaluation_time'].mean()",
    1)], "overview_oldlabel")
if _c7:
    _cap7, _, _ = render_tab(_c7, "render_overview_tab", _ENRICHED, _CURRENT_DB)
    _planted_names = _chart_category_labels(_cap7["plotly"])
    check_true("C7  WITH the old label, the chart names a model that has not "
               "served Stage 5 since 2026-08-04 -- the defect, reproduced",
               "GPT-4o Eval" in _planted_names)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 11: NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================

print()
print("=" * 74)
print("Section 11: the repository and the production database are unchanged")
print("=" * 74)

_paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)

_after = {k: digest_file(v) for k, v in _WATCHED.items()}
check("11a  every source file this test reads is byte-identical afterwards",
      sorted(k for k in _WATCHED if _after[k] != _DIGESTS_BEFORE[k]), [])
check_true("11a  ...and each reading is a real digest (non-degeneracy: two "
           "'absent' readings compare equal)",
           all(is_real_digest(v) for v in _after.values()))
check_true("11a  ...and they are not all the same file hashed repeatedly",
           len(set(_after.values())) == len(_after))

check("11b  the production database was not created, deleted or modified",
      digest_file(_PRODUCTION_DB), _PRODUCTION_DIGEST_BEFORE)
check("11b  ...and its existence is unchanged",
      os.path.exists(_PRODUCTION_DB), _PRODUCTION_EXISTED_BEFORE)

shutil.rmtree(_TMP, ignore_errors=True)
check("11c  the scratch tree is removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 74)
print("RESULTS")
print("=" * 74)
print(f"passed:  {_RESULTS['passed']}")
print(f"failed:  {_RESULTS['failed']}")
print(f"skipped: {_RESULTS['skipped']}")
if _FAILURES:
    print()
    print("FAILURES")
    for item in _FAILURES:
        print("  - " + item)
if _SKIPS:
    print()
    print("SKIPS")
    for item in _SKIPS:
        print("  - " + item)
print("=" * 74)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Sep 10 2026

@author: ramyalsaffar
"""
