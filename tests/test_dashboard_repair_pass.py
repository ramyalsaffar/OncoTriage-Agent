# Dashboard Repair Pass: Filter Ownership, Latency Truth, Trial Preservation
############################################################################

"""
Three user-facing defects, all of them SILENT, all reproduced before they were
repaired.

  1. A THIRD DEFINITION OF "ANY MATCH", IN THE ONE PLACE THAT DECIDES WHAT
     EVERY TAB SEES. The Part 4 pass gave the Overview tile, the Demographics
     tile and the demographics charts one owner -- ``tiers.any_match_series``,
     which is ``match_tier != 'No Match'`` -- and left ``sidebar.py`` filtering
     on ``eligible_matches > 0`` / ``== 0``. So a reader could narrow to "Any
     Match" and then read an Any Match percentage computed over a population
     selected by a different rule.

     THE PAIR WAS NOT EVEN A PARTITION. ``NaN > 0`` and ``NaN == 0`` are both
     False, so a patient whose ``eligible_matches`` is NULL -- a row written
     before the column, or by a failure return -- was in NEITHER selection and
     vanished from the page under both.

     AND THE LABEL WAS FALSE IN BOTH DIRECTIONS. It read "(Full + Partial)"
     over a filter that included Unconfirmed, beside charts that also include
     Unconfirmed.

  2. AN ALL-NULL LATENCY COLUMN, TWO WAYS, AND ONLY ONE OF THEM WAS LOUD.
     ``df.nlargest(10, 'total_time')`` RAISES on the object dtype
     ``pd.read_sql_query`` returns for an all-NULL REAL column -- inside a tab,
     with no handler between it and ``main()``. On a float64 all-NaN column it
     does NOT raise: it returns rows with blank time cells, ranked 1..n, under
     a heading promising a latency ranking. And on a PARTLY measured frame it
     pads the requested ten with unmeasured rows -- measured on pandas 2.2.3, a
     12-row frame with 5 NaN returns 10 rows of which 3 are NaN.

  3. TRIALS WITH NO RECORDED TITLE WERE DROPPED, AND MIXED ONES UNDERCOUNTED.
     ``groupby(['nct_id', 'trial_title'])`` discards NaN group keys, so a trial
     whose every row had a NULL title had no selector entry at all and every
     patient evaluated against it was unreachable; a trial with some titled and
     some untitled rows appeared once with only the titled ones counted.

     THE EARLIER REPORT BLAMED ``r['trial_title'][:55]`` FOR RAISING. It does
     not: nothing with a NULL title ever reached the slice. Section 4 measures
     which mechanism is live rather than repeating the attribution.

RUNS, COSTS, KEYS
-----------------
No network (measured, section 6, with a control that fires), no keys, no spend,
no live Qdrant, no model load, no corpus, no git history, no live server. NOT
in the collision matrix, derived: it writes only inside a ``tempfile.mkdtemp``
it removes and asserts gone, ``paths._RESOLVED`` is repointed and restored, and
the five repository files it READS are written by neither of the suite's two
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

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from oncotriage import paths as _paths
from oncotriage.dashboard import app as _app
from oncotriage.dashboard import data as _data
from oncotriage.dashboard import sidebar as _sidebar
from oncotriage.dashboard import tiers as _tiers
from oncotriage.dashboard.tabs import match_quality as _mq
from oncotriage.dashboard.tabs import performance as _perf
from oncotriage.dashboard.tabs import trial_explorer as _te
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
    """Coverage that could NOT be exercised here. NEVER counted as a pass."""
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}\n          {reason}")


def at_(sequence, index, default="(absent)"):
    """``sequence[index]`` or a named absence.

    Every plant below removes an element or makes a render raise, so every read
    of a rendered list is exactly the expression that raises when the defect
    under test fires -- the abort shape this project has shipped nineteen
    times.
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
    if not os.path.exists(path):
        return "absent"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"


def is_real_digest(reading):
    """True only for a real sha256 -- asked instead of ``!= "absent"`` because
    there are TWO non-readings and a predicate written against one reports the
    other as a real digest."""
    return (isinstance(reading, str) and len(reading) == 64
            and all(c in "0123456789abcdef" for c in reading))


def _pkg_files_declaring(name):
    """Every package file with a MODULE-LEVEL assignment to `name`, sorted.

    BY AST AND BY EXACT NAME, never by substring: `MISSING_TITLE_LABEL` is a
    substring of `TRIAL_MISSING_TITLE_LABEL`, so a text scan for the retired
    spelling reports the surviving one. And an ASSIGNMENT rather than any
    reference, so a `from ... import` -- which is how both tabs reach the
    label's owner -- is not counted as a second declaration.
    """
    root = os.path.dirname(os.path.dirname(os.path.abspath(_tiers.__file__)))
    found = []
    for base, _dirs, files in os.walk(root):
        if "__pycache__" in base:
            continue
        for fname in files:
            if not fname.endswith(".py"):
                continue
            path = os.path.join(base, fname)
            try:
                tree = ast.parse(Path(path).read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in tree.body:
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target] if isinstance(node, ast.AnnAssign)
                           else [])
                if any(isinstance(t, ast.Name) and t.id == name
                       for t in targets):
                    found.append(path)
                    break
    return sorted(found)


_WATCHED = {
    "app.py": os.path.abspath(_app.__file__),
    "sidebar.py": os.path.abspath(_sidebar.__file__),
    "tiers.py": os.path.abspath(_tiers.__file__),
    "performance.py": os.path.abspath(_perf.__file__),
    "trial_explorer.py": os.path.abspath(_te.__file__),
    "match_quality.py": os.path.abspath(_mq.__file__),
}
_DIGESTS_BEFORE = {k: digest_file(v) for k, v in _WATCHED.items()}

_TMP = tempfile.mkdtemp(prefix="oncotriage-dash-repair-")
_PLANT_DIR = os.path.join(_TMP, "plants")
os.makedirs(_PLANT_DIR, exist_ok=True)
_PLANT_SEQ = [0]

print("=" * 74)
print("DASHBOARD REPAIR PASS: FILTER OWNERSHIP, LATENCY TRUTH, TRIAL PRESERVATION")
print("=" * 74)
print(f"Scratch root: {_TMP}")
print()


def _plant(source_path, replacements, prefix):
    """A COPY of `source_path` with `replacements` applied, in the plant dir.

    ``replacements`` is a sequence of ``(old, new, expected_occurrences)``. The
    count is asserted and the result is ``ast.parse``d: a plant that matched
    nothing is a WORKING check reported as broken, and a plant that does not
    parse is an ABORT reported as a failure.
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


def _plotly_spec(element):
    """The chart's figure spec as a dict, or a NAMED absence -- never a raise.

    ``element.proto.spec``, which is what
    tests/test_dashboard_reproducibility_tab.py already reads. A ChartElement's
    ``.value`` reads a widget out of session state that a non-interactive
    ``st.plotly_chart`` never writes, so it raises KeyError for every render.
    """
    try:
        return json.loads(element.proto.spec)
    except Exception as exc:                           # noqa: BLE001 -- reported
        return {"__unreadable__": f"{type(exc).__name__}: {exc}"}


def _capture(at):
    return {
        "exception": [str(e.value).splitlines()[0] for e in at.exception],
        "metrics": {m.label: (m.value, m.delta) for m in at.metric},
        "warning": [w.value for w in at.warning],
        "info": [i.value for i in at.info],
        "error": [e.value for e in at.error],
        "caption": [c.value for c in at.caption],
        "markdown": [m.value for m in at.markdown],
        "subheader": [s.value for s in at.subheader],
        "dataframe_objects": [d.value for d in at.dataframe],
        "dataframes": len(at.dataframe),
        "tabs": [t.label for t in at.get("tab")],
        "plotly": [_plotly_spec(f) for f in at.get("plotly_chart")],
    }


def _run(script, db_path, keep_open=False):
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


def render_app(db_path, module="oncotriage.dashboard.app", keep_open=False):
    return _run(_DRIVER_APP.format(extra_path=_PLANT_DIR, module=module),
                db_path, keep_open=keep_open)


def render_tab(module, fn, frame, db_path, keep_open=False):
    _PLANT_SEQ[0] += 1
    path = os.path.join(_TMP, f"frame_{_PLANT_SEQ[0]}.pkl")
    with open(path, "wb") as handle:
        pickle.dump(frame, handle)
    return _run(_DRIVER_TAB.format(extra_path=_PLANT_DIR, module=module,
                                   frame=path, fn=fn), db_path,
                keep_open=keep_open)


def _quiet_initialize(path):
    with contextlib.redirect_stderr(_io.StringIO()):
        with contextlib.redirect_stdout(_io.StringIO()):
            initialize_database(path)


def _frames(db_path):
    conn = _REAL_CONNECT(db_path)
    try:
        inferences = pd.read_sql_query("SELECT * FROM inferences", conn)
        matches = pd.read_sql_query("SELECT * FROM trial_matches", conn)
    finally:
        conn.close()
    if not inferences.empty:
        inferences["timestamp"] = pd.to_datetime(inferences["timestamp"])
    return inferences, matches


def _enriched(db_path):
    inferences, matches = _frames(db_path)
    return _tiers.enrich_match_tiers(inferences, matches)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1: THE SEED, AND NONE OF IT IS THE PRODUCTION DATABASE
# ===========================================================================

print("=" * 74)
print("Section 1: the seed")
print("=" * 74)

_PRODUCTION_DB = os.path.abspath(_paths.inferences_path)
_PRODUCTION_DIGEST_BEFORE = digest_file(_PRODUCTION_DB)
_PRODUCTION_EXISTED_BEFORE = os.path.exists(_PRODUCTION_DB)
_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")


def _insert_patient(cur, pid, timestamp, eligible_matches, total_time=20.0,
                    age=60):
    """One inference row. ``eligible_matches`` and ``total_time`` may be None."""
    cur.execute(
        "INSERT INTO inferences (patient_id, timestamp, age, sex, race, "
        "ethnicity, primary_condition, condition_count, medication_count, "
        "candidates_retrieved, candidates_reranked, "
        "candidates_after_rule_filter, candidates_after_quality_filter, "
        "candidates_filtered, candidates_evaluated, eligible_matches, "
        "total_time, estimated_cost_usd, llm_classifier_input_tokens, "
        "llm_classifier_output_tokens, llm_classifier_evaluation_time, "
        "query_expansion_time, hybrid_retrieval_time, cross_encoder_time, "
        "rule_filter_time, matching_model, error) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, timestamp, age, "female", "White", "Not Hispanic or Latino",
         "Breast cancer", 4, 2, 200, 50, 30, 20, 12, 12, eligible_matches,
         total_time, 0.10, 9000, 900, 5.0, 0.1, 1.0, 2.0, 0.3,
         "gpt-5.6-terra", ""))
    return cur.lastrowid


def _insert_trial(cur, inference_id, nct_id, title, eligible="eligible",
                  score=1.0):
    cur.execute(
        "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
        "trial_phase, eligible, match_score, assessment, rerank_score) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (inference_id, nct_id, title, "Phase 2", eligible, score,
         "assessment text", 0.7))


# --- THE DIVERGENCE SEED ---------------------------------------------------
#
# THREE PATIENTS, CHOSEN SO THE OLD PREDICATE AND THE OWNER DISAGREE ON TWO OF
# THEM AND THE OLD PAIR IS NOT A PARTITION. A seed on which they agree would
# make every check below a tautology.
#
#   P-AGREE    eligible_matches 2, an eligible scored trial
#              old: Any Match      owner: Any Match          (agree)
#   P-COLZERO  eligible_matches 0, an eligible scored trial
#              old: No Match       owner: Any Match          (DISAGREE)
#   P-COLNULL  eligible_matches NULL, no trial row at all
#              old: NEITHER selection -- NaN is not > 0 and not == 0
#              owner: No Match                               (THE HOLE)
_P_AGREE, _P_COLZERO, _P_COLNULL = "P-AGREE", "P-COLZERO", "P-COLNULL"

_DIVERGENCE_DB = os.path.join(_TMP, "divergence.db")
_quiet_initialize(_DIVERGENCE_DB)
_conn = sqlite3.connect(_DIVERGENCE_DB)
_cur = _conn.cursor()
_ids = {
    _P_AGREE: _insert_patient(_cur, _P_AGREE, "2026-08-01 10:00:00", 2, 30.0, 50),
    _P_COLZERO: _insert_patient(_cur, _P_COLZERO, "2026-08-02 10:00:00", 0, 20.0, 60),
    _P_COLNULL: _insert_patient(_cur, _P_COLNULL, "2026-08-03 10:00:00", None, 10.0, 70),
}
_insert_trial(_cur, _ids[_P_AGREE], "NCT-A", "Trial A", score=1.0)
_insert_trial(_cur, _ids[_P_COLZERO], "NCT-B", "Trial B", score=0.5)
# P-COLNULL deliberately gets NO trial row: that is what leaves it 'No Match'
# under the owner while its NULL column leaves it in neither old selection.
_conn.commit()
_conn.close()

_DIVERGENCE = _enriched(_DIVERGENCE_DB)

check("1a  the production path is a real resolved path and NO scratch database "
      "is it (without this every check below is vacuous)",
      sorted({os.path.abspath(p) == _PRODUCTION_DB
              for p in (_DIVERGENCE_DB,)}) + [_PRODUCTION_DB.endswith(".db")],
      [False, True])
check("1b  the seed wrote three inference rows", len(_ids), 3)

_old_any = _DIVERGENCE["eligible_matches"] > 0
_old_none = _DIVERGENCE["eligible_matches"] == 0
_owner_any = _tiers.any_match_series(_DIVERGENCE)

check("1c  under the OLD predicate the two selections do NOT partition -- one "
      "row is in neither, which is the hole this repair closes",
      sorted(_DIVERGENCE.loc[~(_old_any | _old_none), "patient_id"]),
      [_P_COLNULL])
# THE NULL ROW IS NOT A DISAGREEMENT AND THE FIRST DRAFT SAID IT WAS. `NaN > 0`
# is False and the owner calls that row 'No Match', so the two AGREE about
# whether it is a match; what the old pair got wrong about it is that it was in
# NEITHER selection, which 1c above is the measurement of. Keeping the two
# findings apart is the point: one is a wrong answer, the other is a missing
# row, and they need different checks.
check("1d  ...and the OLD 'Any Match' gives a DIFFERENT answer from the owner "
      "on a row that IS a match by every tile on the page",
      sorted(_DIVERGENCE.loc[_old_any != _owner_any, "patient_id"]),
      [_P_COLZERO])
check("1d  ...while agreeing with it on the NULL row, whose defect is the "
      "hole above rather than a wrong verdict",
      bool(_old_any[_DIVERGENCE["patient_id"] == _P_COLNULL].iloc[0]
           == _owner_any[_DIVERGENCE["patient_id"] == _P_COLNULL].iloc[0]),
      True)
check("1e  the owner's two selections DO partition: every row is in exactly "
      "one",
      sorted(_DIVERGENCE.loc[(_owner_any & ~_owner_any)
                             | ~(_owner_any | ~_owner_any), "patient_id"]), [])
check("1e  ...and the owner calls exactly these rows a match",
      sorted(_DIVERGENCE.loc[_owner_any, "patient_id"]),
      sorted([_P_AGREE, _P_COLZERO]))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: ONE OWNER FOR THE SIDEBAR FILTER (FIX 1)
# ===========================================================================

print()
print("=" * 74)
print("Section 2: the sidebar filter reads the owner (FIX 1)")
print("=" * 74)

_EXPECTED_ANY = sorted(_DIVERGENCE.loc[_owner_any, "patient_id"])
_EXPECTED_NONE = sorted(_DIVERGENCE.loc[~_owner_any, "patient_id"])


def _rows_on_screen(capture):
    """Every patient_id the Patient Explorer's own table rendered.

    READ OFF A RENDERED FRAME rather than off the filter, because "the filter
    returned these rows" and "the page shows these rows" are different claims
    and only the second is what a reader sees.
    """
    seen = set()
    for frame in capture["dataframe_objects"]:
        if isinstance(frame, pd.DataFrame) and "Patient ID" in frame.columns:
            seen.update(str(v) for v in frame["Patient ID"])
        elif isinstance(frame, pd.DataFrame) and "patient_id" in frame.columns:
            seen.update(str(v) for v in frame["patient_id"])
    return sorted(seen)


_at_app, _, _ = render_app(_DIVERGENCE_DB, keep_open=True)
_base = _capture(_at_app)
check("2a  the page renders with the default 'All' selection",
      _base["exception"], [])
check("2a  ...and draws its ten tabs", len(_base["tabs"]), 10)

_match_selects = [s for s in _at_app.sidebar.selectbox
                  if "match status" in (s.label or "").lower()]
check("2b  the match-status filter is offered", len(_match_selects), 1)

if _match_selects:
    _options = list(_match_selects[0].options)
    check("2c  its options are exactly the module's own closed vocabulary",
          _options, list(_sidebar.MATCH_FILTER_OPTIONS))
    check_true("2c  the 'Any Match' label states how Unconfirmed is treated, "
               "which the old '(Full + Partial)' did not -- and was false "
               "about, because that filter included Unconfirmed",
               "Unconfirmed" in _sidebar.MATCH_FILTER_ANY)
    check("2c  ...and the retired label is gone",
          [o for o in _options if "Full + Partial" in o], [])

    # THE LABEL IS DERIVED FROM THE DEFINITION, NOT TYPED BESIDE IT. A
    # hand-written label is how "(Full + Partial)" came to describe a filter
    # that included Unconfirmed, and replacing one correct literal with another
    # leaves that mechanism in place: the next tier change breaks it again.
    check("2c-i  the Any Match label is built from ANY_MATCH_TIERS, so it "
          "cannot describe a definition the filter no longer uses",
          _sidebar.MATCH_FILTER_ANY,
          f"Any Match ({_sidebar._tier_phrase(_tiers.ANY_MATCH_TIERS)})")
    check("2c-i  ...and the complement label from the tier it selects",
          _sidebar.MATCH_FILTER_NONE, f"{_tiers.MATCH_TIER_NO_MATCH} Only")
    check_true("2c-i  ...and neither is a literal in the module's executable "
               "source (non-degeneracy for the two lines above, which a pair "
               "of retyped literals would also satisfy)",
               not any(isinstance(n, ast.Assign)
                       and any(getattr(t, "id", "") in
                               ("MATCH_FILTER_ANY", "MATCH_FILTER_NONE")
                               for t in n.targets)
                       and isinstance(n.value, ast.Constant)
                       for n in ast.walk(ast.parse(
                           Path(_WATCHED["sidebar.py"]).read_text(
                               encoding="utf-8")))))
    check("2c-ii  the phrase builder is total over one, two and three tiers -- "
          "MATCH_TIERS is a list somebody can edit, and a [:-1]/[-1] slice "
          "renders ', or x' on a two-member one",
          [_sidebar._tier_phrase(t) for t in
           (("A Match",), ("A Match", "B Match"),
            ("A Match", "B Match", "C Match"))],
          ["A", "A or B", "A, B or C"])

    # --- SELECTION 1: ANY MATCH -------------------------------------------
    _match_selects[0].select(_sidebar.MATCH_FILTER_ANY).run()
    _any_cap = _capture(_at_app)
    check("2d  'Any Match' raises nothing", _any_cap["exception"], [])
    check("2d  ...and the rows ON SCREEN are exactly the owner's Any Match set",
          _rows_on_screen(_any_cap), _EXPECTED_ANY)
    check_true("2d  ...which is NOT the old predicate's set (non-degeneracy: "
               "on a frame where they agreed this check would pass whatever "
               "the code did)",
               _EXPECTED_ANY
               != sorted(_DIVERGENCE.loc[_old_any, "patient_id"]))
    _any_tile = at_(_any_cap["metrics"], "Any Match", ("(absent)", None))[0]
    check("2e  ...and the Any Match tile over that selection reads 100%, "
          "because every row in it IS a match by the definition that selected "
          "it -- the filter and the figure are one question asked once",
          _any_tile, "100.0%")

    # --- SELECTION 2: NO MATCH ONLY ---------------------------------------
    _match_selects = [s for s in _at_app.sidebar.selectbox
                      if "match status" in (s.label or "").lower()]
    _match_selects[0].select(_sidebar.MATCH_FILTER_NONE).run()
    _none_cap = _capture(_at_app)
    check("2f  'No Match Only' raises nothing", _none_cap["exception"], [])
    check("2f  ...and the rows ON SCREEN are exactly the owner's complement",
          _rows_on_screen(_none_cap), _EXPECTED_NONE)
    check_true("2f  ...including the NULL-eligible_matches row, which the old "
               "pair put in NEITHER selection",
               _P_COLNULL in _rows_on_screen(_none_cap))
    _none_tile = at_(_none_cap["metrics"], "Any Match", ("(absent)", None))[0]
    check("2g  ...and the Any Match tile over that selection reads 0.0%",
          _none_tile, "0.0%")

    # --- THE TWO SELECTIONS PARTITION -------------------------------------
    check("2h  the two selections PARTITION the cohort -- no row is in both",
          sorted(set(_rows_on_screen(_any_cap))
                 & set(_rows_on_screen(_none_cap))), [])
    check("2h  ...and none is in neither, which the old pair could not say",
          sorted(set(_DIVERGENCE["patient_id"])
                 - set(_rows_on_screen(_any_cap))
                 - set(_rows_on_screen(_none_cap))), [])
else:
    fail("2c..2h  the match-status filter was not rendered",
         "every check in this section needs it")

# --- THE ORDERING THAT MAKES IT POSSIBLE ---------------------------------
_app_tree = ast.parse(Path(_WATCHED["app.py"]).read_text(encoding="utf-8"))
_main = next((n for n in ast.walk(_app_tree)
              if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
if _main is None:
    fail("2i  main() was not found in app.py", "the ordering check needs it")
else:
    _calls = [ast.unparse(n.func) for n in ast.walk(_main)
              if isinstance(n, ast.Call) and not isinstance(n.func, ast.Attribute)]
    _order = [c for c in _calls
              if c in ("enrich_match_tiers", "render_sidebar")]
    check("2i  main() enriches BEFORE it calls the sidebar -- the filter reads "
          "match_tier, which enrichment is what adds",
          _order[:2], ["enrich_match_tiers", "render_sidebar"])
    check_true("2i  ...and calls each exactly once (non-degeneracy: a walk "
               "that found one call would satisfy a prefix test)",
               _order.count("enrich_match_tiers") == 1
               and _order.count("render_sidebar") == 1)

# --- A FRAME WITHOUT THE COLUMN IS A NAMED STATE, NOT A RAISE ------------
_bare = _DIVERGENCE.drop(columns=["match_tier"])
check_true("2j  the sidebar does NOT raise on a frame it cannot filter -- it "
           "is reached before any tab renders, so a raise there is a blank "
           "page for a caller-ordering mistake",
           not str(called(_sidebar.render_sidebar, _bare)
                   ).startswith("raised:"))
# READ AS A COLUMN SUBSCRIPT, NOT AS A SUBSTRING. `ast.unparse` does NOT strip
# docstrings -- they are ordinary `Expr(Constant(str))` statements -- and this
# module's own prose names `eligible_matches` four times while arguing that it
# must not be read. A substring test therefore reported the ARGUMENT as the
# thing it argues against, which is the seventh time this project has met that
# shape. What a column read IS, is a string constant in a subscript slice.
def _column_subscripts(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Subscript)
                and isinstance(node.slice, ast.Constant)
                and isinstance(node.slice.value, str)):
            found.add(node.slice.value)
    return found


_sidebar_columns = _column_subscripts(_WATCHED["sidebar.py"])
check_true("2j  ...and there ARE column subscripts to look at (non-degeneracy: "
           "an empty set satisfies the check below for free)",
           len(_sidebar_columns) >= 3)
check("2j  ...and it does NOT fall back to eligible_matches, which is the "
      "third definition this repair removes -- checked in the SOURCE, because "
      "a fallback that happened to agree on one frame would pass a "
      "behavioural test",
      sorted(c for c in _sidebar_columns if c == "eligible_matches"), [])
check_true("2j  ...while the columns it DOES read are still there, so the "
           "scan is looking at real subscripts",
           {"age", "sex", "timestamp"} <= _sidebar_columns)
check_true("2j  ...and the owner is what it does read",
           "any_match_series" in Path(_WATCHED["sidebar.py"]).read_text(
               encoding="utf-8"))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3: THE LATENCY TABLE IS TRUTHFUL (FIX 2)
# ===========================================================================

print()
print("=" * 74)
print("Section 3: latency ranks only what was measured (FIX 2)")
print("=" * 74)

# THREE SHAPES, AND TWO OF THEM WERE WRONG IN DIFFERENT WAYS. The dtype is the
# whole reason both exist: pd.read_sql_query returns OBJECT for a REAL column
# that is NULL on every row, and float64 as soon as one row has a number.
_ALLNULL_DB = os.path.join(_TMP, "all_null_timing.db")
_quiet_initialize(_ALLNULL_DB)
_conn = sqlite3.connect(_ALLNULL_DB)
_cur = _conn.cursor()
for _i in range(3):
    _insert_patient(_cur, f"P-NT{_i}", f"2026-08-0{_i + 1} 10:00:00", 1,
                    total_time=None)
_conn.commit()
_conn.close()
_ALLNULL = _enriched(_ALLNULL_DB)

check("3a  PRECONDITION: an all-NULL REAL column really does arrive as OBJECT "
      "dtype from sqlite -- the shape that RAISED",
      str(_ALLNULL["total_time"].dtype), "object")
check("3a  ...and nlargest on it raises, which is the defect",
      str(called(_ALLNULL.nlargest, 10, "total_time")
          ).startswith("raised: TypeError"), True)

_allnull_cap, _, _ = render_tab("oncotriage.dashboard.tabs.performance",
                                "render_performance_tab", _ALLNULL,
                                _ALLNULL_DB)
check("3b  the tab renders with every timing NULL -- before this it raised "
      "inside main(), taking all ten tabs down", _allnull_cap["exception"], [])
check_true("3b  ...and says TIMING UNAVAILABLE rather than drawing a ranking",
           any("Timing unavailable" in i for i in _allnull_cap["info"]))
check_true("3b  ...naming how many rows are unmeasured",
           any("3 row(s) are unmeasured" in i or "3 row(s)" in i
               for i in _allnull_cap["info"]))

# THE SECOND ALL-UNMEASURED SHAPE: float64 all-NaN, which did NOT raise and
# rendered a table of blanks ranked 1..n.
_ALLNAN = _ALLNULL.copy()
_ALLNAN["total_time"] = np.nan
check("3c  PRECONDITION: the float64 all-NaN shape is a REAL second shape and "
      "it did NOT raise -- a silent table of blanks, which is the worse of "
      "the two",
      (str(_ALLNAN["total_time"].dtype),
       str(called(_ALLNAN.nlargest, 10, "total_time")).startswith("raised:")),
      ("float64", False))
_allnan_cap, _, _ = render_tab("oncotriage.dashboard.tabs.performance",
                               "render_performance_tab", _ALLNAN, _ALLNULL_DB)
check("3c  ...and the tab treats it as the SAME condition, because it is the "
      "same thing to a reader", _allnan_cap["exception"], [])
check_true("3c  ...saying timing unavailable rather than ranking blanks",
           any("Timing unavailable" in i for i in _allnan_cap["info"]))

# --- PARTIAL: RANK THE MEASURED, STATE THE REST --------------------------
_PARTIAL_DB = os.path.join(_TMP, "partial_timing.db")
_quiet_initialize(_PARTIAL_DB)
_conn = sqlite3.connect(_PARTIAL_DB)
_cur = _conn.cursor()
_MEASURED_TIMES = [90.0, 80.0, 70.0, 60.0]
_UNMEASURED_N = 5
for _i, _t in enumerate(_MEASURED_TIMES):
    _insert_patient(_cur, f"P-M{_i}", f"2026-08-0{_i + 1} 10:00:00", 1,
                    total_time=_t)
for _i in range(_UNMEASURED_N):
    _insert_patient(_cur, f"P-U{_i}", f"2026-08-0{_i + 1} 11:00:00", 1,
                    total_time=None)
_conn.commit()
_conn.close()
_PARTIAL = _enriched(_PARTIAL_DB)

check("3d  PRECONDITION: nlargest PADS the requested ten with unmeasured rows "
      "rather than dropping them -- which is how unmeasured patients used to "
      "appear in a latency ranking",
      int(_PARTIAL.nlargest(10, "total_time")["total_time"].isna().sum()),
      _UNMEASURED_N)

_partial_cap, _, _ = render_tab("oncotriage.dashboard.tabs.performance",
                                "render_performance_tab", _PARTIAL,
                                _PARTIAL_DB)
check("3e  the partly-measured frame renders", _partial_cap["exception"], [])
_ranked = None
for _frame in _partial_cap["dataframe_objects"]:
    if isinstance(_frame, pd.DataFrame) and "Total Time (s)" in _frame.columns:
        _ranked = _frame
        break
if _ranked is None:
    fail("3e  the latency table was not rendered", "3f and 3g need it")
else:
    check("3f  ONLY the measured rows are ranked",
          len(_ranked), len(_MEASURED_TIMES))
    check("3f  ...and not one of them is blank",
          int(_ranked["Total Time (s)"].isna().sum()), 0)
    check("3f  ...in descending order, which is what 'Rank 1 = slowest' means",
          [float(v) for v in _ranked["Total Time (s)"]],
          sorted(_MEASURED_TIMES, reverse=True))
_partial_caption = "\n".join(_partial_cap["caption"])
check_true("3g  the caption STATES the unmeasured count -- a ranking over 4 of "
           "9 and a ranking over 4 of 4 look identical on screen",
           f"{_UNMEASURED_N:,} row(s) recorded no timing" in _partial_caption)
check_true("3g  ...and states the denominator it ranked over",
           f"{len(_MEASURED_TIMES):,} of {len(_PARTIAL):,} row(s)"
           in _partial_caption)

# --- FULLY MEASURED: NO EXCLUSION CLAUSE --------------------------------
_FULL_DB = os.path.join(_TMP, "full_timing.db")
_quiet_initialize(_FULL_DB)
_conn = sqlite3.connect(_FULL_DB)
_cur = _conn.cursor()
for _i, _t in enumerate(_MEASURED_TIMES):
    _insert_patient(_cur, f"P-F{_i}", f"2026-08-0{_i + 1} 10:00:00", 1,
                    total_time=_t)
_conn.commit()
_conn.close()
_FULL = _enriched(_FULL_DB)
_full_cap, _, _ = render_tab("oncotriage.dashboard.tabs.performance",
                             "render_performance_tab", _FULL, _FULL_DB)
check("3h  a fully-measured frame renders its ranking", _full_cap["exception"], [])
_full_caption = "\n".join(_full_cap["caption"])
check_true("3h  ...and says nothing about excluded rows, because there are "
           "none (non-degeneracy: a caption that always carried the clause "
           "would satisfy 3g for free)",
           "recorded no timing" not in _full_caption)
check_true("3h  ...while still stating the denominator",
           f"{len(_MEASURED_TIMES):,} of {len(_FULL):,} row(s)" in _full_caption)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4: NULL-TITLE TRIALS SURVIVE, ONCE EACH (FIX 3)
# ===========================================================================

print()
print("=" * 74)
print("Section 4: trial preservation (FIX 3)")
print("=" * 74)

_TRIALS_DB = os.path.join(_TMP, "trials.db")
_quiet_initialize(_TRIALS_DB)
_conn = sqlite3.connect(_TRIALS_DB)
_cur = _conn.cursor()
_t_ids = {p: _insert_patient(_cur, p, f"2026-08-0{i + 1} 10:00:00", 1)
          for i, p in enumerate(("PT-A", "PT-B"))}
_RECORDED_TITLE = "A recorded title"
for _p in ("PT-A", "PT-B"):
    _insert_trial(_cur, _t_ids[_p], "NCT-TITLED", "A perfectly titled trial")
    _insert_trial(_cur, _t_ids[_p], "NCT-NULLT", None)          # (a) all NULL
_insert_trial(_cur, _t_ids["PT-A"], "NCT-MIXED", _RECORDED_TITLE)  # (b) mixed
_insert_trial(_cur, _t_ids["PT-B"], "NCT-MIXED", None)
_conn.commit()
_conn.close()
_TRIALS = _enriched(_TRIALS_DB)

# THE PRE-FIX MECHANISM, MEASURED RATHER THAN ATTRIBUTED. The earlier report
# blamed `r['trial_title'][:55]` for raising on a NULL; it does not, because
# the groupby discards the NaN key first and nothing untitled reaches the
# slice. This reproduces the real mechanism.
_merged = pd.read_sql_query("SELECT * FROM trial_matches",
                            _REAL_CONNECT(_TRIALS_DB)).merge(
    _TRIALS[["id", "patient_id"]], left_on="inference_id", right_on="id",
    how="left", suffixes=("", "_inf")).drop(columns="id_inf", errors="ignore")
_prefix_groups = _merged.groupby(["nct_id", "trial_title"]).agg(
    total_patients=("patient_id", "nunique")).reset_index()

check("4a  PRE-FIX MECHANISM: grouping on (nct_id, trial_title) DROPS the "
      "all-NULL-title trial entirely -- no entry, no error",
      "NCT-NULLT" in set(_prefix_groups["nct_id"]), False)
check("4a  ...and undercounts the mixed one, silently",
      int(_prefix_groups[_prefix_groups.nct_id == "NCT-MIXED"]
          ["total_patients"].sum()), 1)
check("4a  ...against its true distinct-patient count",
      int(_merged[_merged.nct_id == "NCT-MIXED"]["patient_id"].nunique()), 2)
check("4a  ...and NOTHING with a NULL title ever reached the [:55] slice, so "
      "the earlier attribution to it was wrong",
      int(_prefix_groups["trial_title"].isna().sum()), 0)

_tr_at, _, _ = render_tab("oncotriage.dashboard.tabs.trial_explorer",
                          "render_trial_explorer_tab", _TRIALS, _TRIALS_DB,
                          keep_open=True)
_tr_cap = _capture(_tr_at)
check("4b  the tab renders", _tr_cap["exception"], [])
_selects = [s for s in _tr_at.selectbox if s.key == "trial_explorer_select"]
if not _selects:
    fail("4b  the trial selector was not rendered", "the section needs it")
else:
    # `.options` is already formatted by AppTest -- calling format_func on it
    # again passes a string to a lambda that indexes a list with it.
    _labels = [str(v) for v in _selects[0].options]
    check("4c  every trial appears, including the one with no title at all",
          sorted(l.split(" — ")[0] for l in _labels),
          ["NCT-MIXED", "NCT-NULLT", "NCT-TITLED"])
    check("4c  ...each EXACTLY ONCE",
          sorted({l.split(" — ")[0] for l in _labels}),
          sorted(l.split(" — ")[0] for l in _labels))
    check_true("4d  the untitled trial is named by the module's own label "
               "rather than by a blank",
               any(_tiers.TRIAL_MISSING_TITLE_LABEL in l
                   and "NCT-NULLT" in l for l in _labels))
    check_true("4d  ...and the mixed trial by the title it DOES have, not by "
               "the absence of one",
               any(_RECORDED_TITLE in l and "NCT-MIXED" in l for l in _labels))
    check_true("4e  ...and the mixed trial's patient count is its TRUE one, "
               "which the pre-fix grouping halved",
               any("NCT-MIXED" in l and "(2 patients)" in l for l in _labels))

    # --- EVERY PATIENT ROW IS REACHABLE UNDER THE ONE ENTRY --------------
    for _nct, _expected_rows in (("NCT-NULLT", 2), ("NCT-MIXED", 2)):
        _index = [i for i, l in enumerate(_labels) if _nct in l]
        if not _index:
            fail(f"4f  {_nct} is not in the selector", "cannot select it")
            continue
        _selects_now = [s for s in _tr_at.selectbox
                        if s.key == "trial_explorer_select"]
        _selects_now[0].set_value(_index[0]).run()
        _sel_cap = _capture(_tr_at)
        check(f"4f  selecting {_nct} renders its details with no exception",
              _sel_cap["exception"], [])
        _patients = _rows_on_screen(_sel_cap)
        check(f"4f  ...and ALL {_expected_rows} of its patient rows are "
              f"reachable under that single entry",
              sorted(_patients), sorted(_t_ids))
        check_true(f"4f  ...with a heading rather than a blank",
                   any(s.strip() for s in _sel_cap["subheader"]))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4B: THE SAME TRIAL IDENTITY ON THE MATCH QUALITY RANKING
# ===========================================================================

print()
print("=" * 74)
print("Section 4b: Top Matched Trials groups by trial ID (FIX 3, 2nd panel)")
print("=" * 74)

# A SECOND PANEL RANKED TRIALS BY THE (nct_id, trial_title) PAIR, so repairing
# the Trial Explorer's selector left this one holding the identical defect --
# and here it is worse, because this panel's whole job is to answer "which
# trials matched the most patients" and a dropped trial is not ranked low, it
# is absent.
#
# THE FIXTURE IS BUILT SO THE THREE CANDIDATE GROUPINGS DISAGREE ON EVERY
# COLUMN THE PANEL SHOWS. A fixture on which they agreed would make every
# check below a tautology, which is how the pre-fix defect survived a suite
# that already rendered this tab.
#
#   NCT-Q-TITLED   3 eligible rows, all titled, all 1.0
#                    TRUTH 3 / 100% / 0 unconfirmed   (every grouping agrees)
#   NCT-Q-MIXED    4 eligible rows: ONE titled at 1.0, THREE untitled at 0.0
#                    TRUTH        4 /  25% / 3
#                    pre-fix      1 / 100% / 0        (ranked on one row)
#                    fillna-split TWO entries, 1 and 3
#   NCT-Q-NULLT    2 eligible rows, no title at all, 0.5 each
#                    TRUTH 2 / 50% / 0     pre-fix ABSENT
#   NCT-Q-NOSCORE  2 eligible rows, no title AND no match_score
#                    TRUTH 2 / <NA> / 0    pre-fix ABSENT
#                    -- the group the repair NEWLY EXPOSES, and the one that
#                    could have raised: `mean` over an all-NULL group is NaN
#                    and `.astype('Int64')` carries it as <NA>. 0% would
#                    assert a measured average of nothing.
#   NCT-Q-BLANK    3 eligible rows: ONE titled at 1.0, TWO carrying the
#                    EMPTY STRING at 0.0
#                    TRUTH        3 /  33% / 2
#                    pre-fix      SPLIT into two entries, 1 and 2
#                    -- THE REACHABLE POPULATION, and a DIFFERENT pre-fix
#                    defect from NCT-Q-MIXED's. `storage/database_logger.py`
#                    writes `match.get("title", "")`, so the value this
#                    pipeline produces for a titleless trial is "" and never
#                    NaN -- and `groupby` does NOT discard an empty-string
#                    key, so these rows were never DROPPED, they were split
#                    off into a second ranked entry. Rows preserved, identity
#                    halved. It also fixes `first_valid_index()`, which called
#                    "" a recorded title and named this trial with a BLANK
#                    cell beside a title the database holds.
#   NCT-Q-INELIG   1 row, eligible='not_eligible'
#                    must be ABSENT under every grouping: this panel ranks
#                    eligible matches, and a fix that preserved rows by
#                    widening the filter would preserve the wrong ones.

_MQ_DB = os.path.join(_TMP, "match_quality_titles.db")
_quiet_initialize(_MQ_DB)
_conn = sqlite3.connect(_MQ_DB)
_cur = _conn.cursor()
_mq_ids = {p: _insert_patient(_cur, p, f"2026-08-1{i} 10:00:00", 1)
           for i, p in enumerate(("MQ-A", "MQ-B", "MQ-C", "MQ-D"))}
_MQ_TITLE = "A titled Q trial"
_MQ_MIXED_TITLE = "The one recorded mixed title"
for _p in ("MQ-A", "MQ-B", "MQ-C"):
    _insert_trial(_cur, _mq_ids[_p], "NCT-Q-TITLED", _MQ_TITLE, score=1.0)
_insert_trial(_cur, _mq_ids["MQ-A"], "NCT-Q-MIXED", _MQ_MIXED_TITLE, score=1.0)
for _p in ("MQ-B", "MQ-C", "MQ-D"):
    _insert_trial(_cur, _mq_ids[_p], "NCT-Q-MIXED", None, score=0.0)
for _p in ("MQ-A", "MQ-B"):
    _insert_trial(_cur, _mq_ids[_p], "NCT-Q-NULLT", None, score=0.5)
for _p in ("MQ-C", "MQ-D"):
    _insert_trial(_cur, _mq_ids[_p], "NCT-Q-NOSCORE", None, score=None)
_MQ_BLANK_TITLE = "The blank trial's one recorded title"
_insert_trial(_cur, _mq_ids["MQ-A"], "NCT-Q-BLANK", _MQ_BLANK_TITLE, score=1.0)
for _p in ("MQ-B", "MQ-C"):
    _insert_trial(_cur, _mq_ids[_p], "NCT-Q-BLANK", "", score=0.0)
_insert_trial(_cur, _mq_ids["MQ-D"], "NCT-Q-INELIG", "An ineligible trial",
              eligible="not_eligible", score=1.0)
_conn.commit()
_conn.close()
_MQ = _enriched(_MQ_DB)

# THE KNOWN TRUTH, CONSTRUCTED. Every number here is read off the fixture
# above and never off the frame under test.
_MQ_TRUTH = {
    "NCT-Q-TITLED":  (3, 100, 0),
    "NCT-Q-MIXED":   (4,  25, 3),
    "NCT-Q-NULLT":   (2,  50, 0),
    "NCT-Q-NOSCORE": (2, None, 0),
    "NCT-Q-BLANK":   (3,  33, 2),
}

# --- THE PRE-FIX MECHANISM, MEASURED ON THE SAME FRAME --------------------
_mq_raw = pd.read_sql_query("SELECT * FROM trial_matches",
                            _REAL_CONNECT(_MQ_DB))
_mq_elig_raw = _mq_raw[_mq_raw["eligible"] == "eligible"]
_mq_prefix = _mq_elig_raw.groupby(["nct_id", "trial_title"]).agg(
    match_count=("inference_id", "count"),
    avg_score=("match_score", "mean"),
    unconfirmed=("match_score", lambda s: int((s <= 0).sum())),
).reset_index()
_mq_prefix_ids = set(_mq_prefix["nct_id"])

check("4g  PRE-FIX MECHANISM: grouping on the pair DROPS the two trials whose "
      "title is NULL out of the ranking entirely",
      sorted(set(_MQ_TRUTH) - _mq_prefix_ids),
      ["NCT-Q-NOSCORE", "NCT-Q-NULLT"])
check("4g  ...and SPLITS the one carrying the writer's own empty-string "
      "default into two entries instead, which is a DIFFERENT defect on the "
      "only population this pipeline's writer can actually produce",
      sorted(int(v) for v in
             _mq_prefix[_mq_prefix.nct_id == "NCT-Q-BLANK"]["match_count"]),
      [1, 2])
check("4g  ...so its rows were preserved and its identity halved -- which is "
      "why an empty-string title is NOT the same finding as a NULL one",
      int(_mq_prefix[_mq_prefix.nct_id == "NCT-Q-BLANK"]
          ["match_count"].sum()), 3)
check("4g  ...and ranks the mixed trial on its ONE titled row",
      int(_mq_prefix[_mq_prefix.nct_id == "NCT-Q-MIXED"]
          ["match_count"].sum()), 1)
check("4g  ...against a true count of",
      int(_mq_elig_raw[_mq_elig_raw.nct_id == "NCT-Q-MIXED"].shape[0]), 4)
check("4g  ...so its Avg Score was a mean over that one row, reading 100% "
      "where the truth is 25%",
      int(round(float(_mq_prefix[_mq_prefix.nct_id == "NCT-Q-MIXED"]
                      ["avg_score"].iloc[0]) * 100)), 100)
check("4g  ...and its Unconfirmed count read 0 where three of its four "
      "eligible inferences confirmed nothing",
      int(_mq_prefix[_mq_prefix.nct_id == "NCT-Q-MIXED"]
          ["unconfirmed"].iloc[0]), 0)

# --- THE SHIPPED PANEL, AT THE RENDERED SURFACE ---------------------------
_mq_at, _, _ = render_tab("oncotriage.dashboard.tabs.match_quality",
                          "render_match_quality_tab", _MQ, _MQ_DB,
                          keep_open=True)
_mq_cap = _capture(_mq_at)
check("4h  the tab renders", _mq_cap["exception"], [])


def _top_table(cap):
    """The Top Matched Trials frame, selected BY ITS COLUMNS.

    Positionally is how tests/test_dashboard_run_health.py came to point three
    checks at the wrong table when a panel was inserted above them.
    """
    for frame in cap["dataframe_objects"]:
        if (isinstance(frame, pd.DataFrame)
                and "NCT ID" in frame.columns
                and "Match Count" in frame.columns):
            return frame
    return None


_top = _top_table(_mq_cap)
if _top is None:
    fail("4h  the Top Matched Trials table was not rendered",
         "the section needs it")
else:
    check("4h  it carries the five columns the panel names, in order",
          list(_top.columns),
          ["NCT ID", "Trial", "Match Count", "Avg Score", "Unconfirmed"])
    check("4i  every eligible trial is ranked, the untitled ones included",
          sorted(_top["NCT ID"]), sorted(_MQ_TRUTH))
    check("4i  ...each EXACTLY ONCE",
          sorted(set(_top["NCT ID"])), sorted(_top["NCT ID"]))
    check("4i  ...and the ineligible trial is NOT ranked",
          "NCT-Q-INELIG" in set(_top["NCT ID"]), False)

    _by_id = {r["NCT ID"]: r for _, r in _top.iterrows()}
    for _nct, (_count, _pct, _unconf) in sorted(_MQ_TRUTH.items()):
        _row = _by_id.get(_nct)
        if _row is None:
            fail(f"4j  {_nct} is absent from the rendered ranking",
                 "its displayed counts cannot be checked")
            continue
        check(f"4j  {_nct}: the DISPLAYED Match Count is its constructed "
              f"known truth", int(_row["Match Count"]), _count)
        check(f"4j  {_nct}: the DISPLAYED Unconfirmed count is too",
              int(_row["Unconfirmed"]), _unconf)
        if _pct is None:
            check_true(f"4j  {_nct}: Avg Score renders as <NA> rather than "
                       f"0%, because no score was recorded for any of its "
                       f"rows -- 0% would assert a measured average",
                       pd.isna(_row["Avg Score"]))
        else:
            check(f"4j  {_nct}: the DISPLAYED Avg Score is the mean over ALL "
                  f"its eligible rows", int(_row["Avg Score"]), _pct)

    check_true("4k  the mixed trial is named by the title it HAS rather than "
               "by the absence of one",
               _by_id.get("NCT-Q-MIXED", {}).get("Trial") == _MQ_MIXED_TITLE)
    check_true("4k  ...and an untitled trial by the owner's own label",
               _by_id.get("NCT-Q-NULLT", {}).get("Trial")
               == _tiers.TRIAL_MISSING_TITLE_LABEL)
    check("4k  ...and the EMPTY-STRING trial by the title it HAS, where "
          "`first_valid_index()` accepted the empty string as a recorded "
          "title and named it with a blank cell",
          _by_id.get("NCT-Q-BLANK", {}).get("Trial"), _MQ_BLANK_TITLE)

# --- ONE OWNER FOR THE LABEL AND THE RULE, ASSERTED --------------------
#
# Both panels reach the SAME object, by identity. Two copies of a
# user-visible label is the shape PATIENT_OUTCOME_FULL was introduced to
# remove, and a copy would satisfy an equality test.
check_true("4l  both tabs read ONE display-title function, by identity",
           _te.display_trial_title is _tiers.display_trial_title
           and _mq.display_trial_title is _tiers.display_trial_title)
check("4l  ...and the label is declared exactly once in the package",
      sorted(os.path.basename(p) for p in _pkg_files_declaring(
          "TRIAL_MISSING_TITLE_LABEL")), ["tiers.py"])
check("4l  ...and the retired bare spelling is declared NOWHERE in the "
      "package, so a reinstated copy fails here (non-degeneracy: the check "
      "above proves this scanner finds a declaration that IS there)",
      _pkg_files_declaring("MISSING_TITLE_LABEL"), [])

# THE HELPER'S OWN CONTRACT, driven on the three shapes a group can have.
check("4m  display_trial_title: a fully titled group is named by its title",
      _tiers.display_trial_title(pd.Series(["T", "T"])), "T")
check("4m  ...a mixed group by the FIRST RECORDED title, not by the NaN",
      _tiers.display_trial_title(pd.Series([None, "Second", None])), "Second")
check("4m  ...and a group with none at all by the label, rather than raising",
      _tiers.display_trial_title(pd.Series([None, None])),
      _tiers.TRIAL_MISSING_TITLE_LABEL)
check("4m  ...an EMPTY group too, which `dropna().iloc[0]` would raise on",
      _tiers.display_trial_title(pd.Series([], dtype=object)),
      _tiers.TRIAL_MISSING_TITLE_LABEL)
check("4m  ...THE WRITER'S OWN DEFAULT is not a recorded title: an all-empty "
      "group answers the label, where `first_valid_index()` answered ''",
      _tiers.display_trial_title(pd.Series(["", ""])),
      _tiers.TRIAL_MISSING_TITLE_LABEL)
check("4m  ...and a sibling's recorded title outranks it whichever comes "
      "first in the group",
      (_tiers.display_trial_title(pd.Series(["", "Recorded"])),
       _tiers.display_trial_title(pd.Series(["Recorded", ""]))),
      ("Recorded", "Recorded"))
check("4m  ...whitespace-only is blank too, so no panel prints an entry a "
      "reader cannot name",
      _tiers.display_trial_title(pd.Series(["   "])),
      _tiers.TRIAL_MISSING_TITLE_LABEL)
check("4m  ...the surviving title is returned VERBATIM rather than stripped, "
      "because this renders RECORDED data",
      _tiers.display_trial_title(pd.Series(["  Real  "])), "  Real  ")
check("4m  ...and `pandas.NA` is skipped without raising, which a `value != "
      "value` NaN test would not survive",
      _tiers.display_trial_title(pd.Series([pd.NA, "Recorded"], dtype=object)),
      "Recorded")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5: TARGETED FAILURE CONTROLS
# ===========================================================================

print()
print("=" * 74)
print("Section 5: the controls. Every plant is a COPY, ast-parsed.")
print("=" * 74)

# --- C1: THE THIRD DEFINITION RESTORED, BOTH SELECTIONS -------------------
_c1 = _plant(_WATCHED["sidebar.py"], [(
    """    if match_tier_available and match_status_option != MATCH_FILTER_ALL:
        _any_match = any_match_series(filtered_df)
        filtered_df = filtered_df[
            _any_match if match_status_option == MATCH_FILTER_ANY
            else ~_any_match]""",
    """    if match_status_option == MATCH_FILTER_ANY:
        filtered_df = filtered_df[filtered_df['eligible_matches'] > 0]
    elif match_status_option == MATCH_FILTER_NONE:
        filtered_df = filtered_df[filtered_df['eligible_matches'] == 0]""",
    1)], "sidebar_thirddef")
if _c1:
    _c1_app = _plant(_WATCHED["app.py"], [(
        "from oncotriage.dashboard.sidebar import render_sidebar",
        f"from {_c1} import render_sidebar", 1)], "app_thirddef")
    if _c1_app:
        _at_c1, _, _ = render_app(_DIVERGENCE_DB, module=_c1_app,
                                  keep_open=True)
        _sel_c1 = [s for s in _at_c1.sidebar.selectbox
                   if "match status" in (s.label or "").lower()]
        if _sel_c1:
            _sel_c1[0].select(_sidebar.MATCH_FILTER_ANY).run()
            _c1_any = _rows_on_screen(_capture(_at_c1))
            check("C1  WITH the third definition, 'Any Match' selects a "
                  "DIFFERENT population from the owner's -- the defect, "
                  "reproduced for selection 1",
                  _c1_any != _EXPECTED_ANY, True)
            check("C1  ...specifically the old predicate's set",
                  _c1_any, sorted(_DIVERGENCE.loc[_old_any, "patient_id"]))

            _sel_c1 = [s for s in _at_c1.sidebar.selectbox
                       if "match status" in (s.label or "").lower()]
            _sel_c1[0].select(_sidebar.MATCH_FILTER_NONE).run()
            _c1_none = _rows_on_screen(_capture(_at_c1))
            check("C1  ...and for selection 2 as well",
                  _c1_none != _EXPECTED_NONE, True)
            check("C1  ...dropping the NULL row from BOTH selections, which "
                  "is the hole rather than merely a different answer",
                  _P_COLNULL in set(_c1_any) | set(_c1_none), False)
        else:
            fail("C1  the planted sidebar rendered no filter", "control lost")

# --- C2: THE ALL-UNMEASURED GUARD REMOVED --------------------------------
# THE PLANT HAS TO RESTORE BOTH HALVES, and the first draft restored one.
# Disabling only the `_measured == 0` branch leaves the ELSE still ranking
# `df[_measured_mask]`, so the all-NaN frame produced an EMPTY table rather
# than the table of blanks the pre-fix code produced -- a plant that does not
# reproduce the defect reports a working guard as caught for the wrong reason.
_c2 = _plant(_WATCHED["performance.py"], [
    ("    if _measured == 0:", "    if False:", 1),
    ("        slowest = df[_measured_mask].nlargest(10, 'total_time').copy()",
     "        slowest = df.nlargest(10, 'total_time').copy()", 1),
], "perf_noguard")
if _c2:
    _c2_cap, _, _ = render_tab(_c2, "render_performance_tab", _ALLNULL,
                               _ALLNULL_DB)
    check_true("C2  WITHOUT the guard, an all-NULL object column RAISES -- the "
               "defect, reproduced",
               any("dtype object" in e for e in _c2_cap["exception"]))
    _c2_nan_cap, _, _ = render_tab(_c2, "render_performance_tab", _ALLNAN,
                                   _ALLNULL_DB)
    _c2_ranked = [f for f in _c2_nan_cap["dataframe_objects"]
                  if isinstance(f, pd.DataFrame)
                  and "Total Time (s)" in f.columns]
    check("C2  ...and the float64 all-NaN shape renders a SILENT table of "
          "blanks instead, which is the quieter half of the same defect",
          (len(_c2_nan_cap["exception"]),
           int(at_(_c2_ranked, 0, pd.DataFrame({"Total Time (s)": []}))
               ["Total Time (s)"].isna().sum())),
          (0, 3))

# --- C3: THE UNMEASURED ROWS BACK IN THE RANKING -------------------------
_c3 = _plant(_WATCHED["performance.py"], [(
    "        slowest = df[_measured_mask].nlargest(10, 'total_time').copy()",
    "        slowest = df.nlargest(10, 'total_time').copy()", 1)],
    "perf_unfiltered")
if _c3:
    _c3_cap, _, _ = render_tab(_c3, "render_performance_tab", _PARTIAL,
                               _PARTIAL_DB)
    _c3_ranked = [f for f in _c3_cap["dataframe_objects"]
                  if isinstance(f, pd.DataFrame)
                  and "Total Time (s)" in f.columns]
    check("C3  WITHOUT the measured-only filter, unmeasured patients are "
          "ranked among the measured ones -- the defect, reproduced",
          int(at_(_c3_ranked, 0, pd.DataFrame({"Total Time (s)": []}))
              ["Total Time (s)"].isna().sum()), _UNMEASURED_N)

# --- C4: THE DROPPED AND SPLIT TRIALS ------------------------------------
_c4 = _plant(_WATCHED["trial_explorer.py"], [(
    """    trial_summary = filtered_matches.groupby('nct_id').agg(
        trial_title=('trial_title', display_trial_title),
        total_patients=('patient_id', 'nunique'),""",
    """    trial_summary = filtered_matches.groupby(['nct_id', 'trial_title']).agg(
        total_patients=('patient_id', 'nunique'),""", 1)], "te_oldgroup")
if _c4:
    _at_c4, _, _ = render_tab(_c4, "render_trial_explorer_tab", _TRIALS,
                              _TRIALS_DB, keep_open=True)
    _sel_c4 = [s for s in _at_c4.selectbox if s.key == "trial_explorer_select"]
    _labels_c4 = [str(v) for v in _sel_c4[0].options] if _sel_c4 else []
    check("C4  WITH the old grouping, the all-NULL-title trial DISAPPEARS from "
          "the selector -- the defect, reproduced",
          any("NCT-NULLT" in l for l in _labels_c4), False)
    check("C4  ...and the mixed trial is undercounted",
          any("NCT-MIXED" in l and "(1 patients)" in l for l in _labels_c4),
          True)
    check_true("C4  CLEAN CONTROL: the shipped tab shows all three and counts "
               "the mixed one correctly",
               len(_labels) == 3
               and any("NCT-MIXED" in l and "(2 patients)" in l
                       for l in _labels))

# --- C5: A SPLIT ENTRY, WHICH IS THE OTHER WAY TO GET IT WRONG -----------
#
# The obvious "preserve NULL titles" fix is to fillna the title BEFORE
# grouping and keep the pair as the key. That preserves the rows and SPLITS a
# mixed trial into two selector entries -- one titled, one labelled -- which
# is the defect the single-entry requirement exists to forbid.
_c5 = _plant(_WATCHED["trial_explorer.py"], [
    ("                                        classify_trial_score, "
     "display_trial_title)",
     "                                        classify_trial_score, "
     "display_trial_title,\n"
     "                                        TRIAL_MISSING_TITLE_LABEL)", 1),
    ("""    trial_summary = filtered_matches.groupby('nct_id').agg(
        trial_title=('trial_title', display_trial_title),
        total_patients=('patient_id', 'nunique'),""",
     """    filtered_matches['trial_title'] = filtered_matches['trial_title'].fillna(
        TRIAL_MISSING_TITLE_LABEL)
    trial_summary = filtered_matches.groupby(['nct_id', 'trial_title']).agg(
        total_patients=('patient_id', 'nunique'),""", 1)], "te_split")
if _c5:
    _at_c5, _, _ = render_tab(_c5, "render_trial_explorer_tab", _TRIALS,
                              _TRIALS_DB, keep_open=True)
    _sel_c5 = [s for s in _at_c5.selectbox if s.key == "trial_explorer_select"]
    _labels_c5 = [str(v) for v in _sel_c5[0].options] if _sel_c5 else []
    _mixed_entries = [l for l in _labels_c5 if "NCT-MIXED" in l]
    check("C5  WITH a fillna-before-grouping fix, the mixed trial appears "
          "TWICE -- rows preserved, identity split, which is the other way to "
          "get this wrong",
          len(_mixed_entries), 2)
    check("C5  CLEAN CONTROL: the shipped tab gives it exactly one entry",
          len([l for l in _labels if "NCT-MIXED" in l]), 1)


# --- C6: THE DROPPED TRIALS, ON THE MATCH QUALITY RANKING ----------------
#
# The Trial Explorer's C4 with a different victim: a panel whose job is to
# rank trials by how many patients they matched, silently missing two of the
# five eligible trials in the corpus, ranking a third on a quarter of its
# rows and a fourth twice.
_c6 = _plant(_WATCHED["match_quality.py"], [(
    """                top = elig.groupby('nct_id').agg(
                    trial_title=('trial_title', display_trial_title),
                    match_count=('inference_id', 'count'),""",
    """                top = elig.groupby(['nct_id', 'trial_title']).agg(
                    match_count=('inference_id', 'count'),""", 1)],
    "mq_oldgroup")
if _c6:
    _at_c6, _, _ = render_tab(_c6, "render_match_quality_tab", _MQ, _MQ_DB,
                              keep_open=True)
    _cap_c6 = _capture(_at_c6)
    _top_c6 = _top_table(_cap_c6)
    _ids_c6 = sorted(_top_c6["NCT ID"]) if _top_c6 is not None else ["<none>"]
    check("C6  WITH the old grouping, both NULL-title trials vanish from the "
          "ranking -- the defect, reproduced at the rendered surface",
          sorted(set(_MQ_TRUTH) - set(_ids_c6)),
          ["NCT-Q-NOSCORE", "NCT-Q-NULLT"])
    check("C6  ...and the empty-string trial is ranked TWICE, which is the "
          "same grouping producing a different defect on the reachable "
          "population",
          len([v for v in _ids_c6 if v == "NCT-Q-BLANK"]), 2)
    check("C6  ...and the mixed trial is ranked on its ONE titled row",
          int(_top_c6[_top_c6["NCT ID"] == "NCT-Q-MIXED"]
              ["Match Count"].iloc[0]) if _top_c6 is not None else -1, 1)
    check("C6  ...with an Avg Score of 100% where the truth is 25%",
          int(_top_c6[_top_c6["NCT ID"] == "NCT-Q-MIXED"]
              ["Avg Score"].iloc[0]) if _top_c6 is not None else -1, 100)
    check_true("C6  CLEAN CONTROL: the shipped panel ranks every eligible trial "
               "ONCE and gets the mixed one's three numbers right",
               _top is not None
               and sorted(_top["NCT ID"]) == sorted(_MQ_TRUTH)
               and (int(_by_id["NCT-Q-MIXED"]["Match Count"]),
                    int(_by_id["NCT-Q-MIXED"]["Avg Score"]),
                    int(_by_id["NCT-Q-MIXED"]["Unconfirmed"])) == (4, 25, 3))

# --- C7: THE SPLIT, WHICH IS THE OTHER WAY TO GET IT WRONG ---------------
#
# The obvious "preserve the untitled rows" fix is to fillna the title BEFORE
# grouping and keep the pair as the key. It preserves every row -- the counts
# SUM to the truth -- and SPLITS the mixed trial into two ranked entries, so
# the panel reports one trial twice, each time with a fraction of its patients
# and a mean over that fraction. Rows preserved, identity split.
_c7 = _plant(_WATCHED["match_quality.py"], [(
    """                top = elig.groupby('nct_id').agg(
                    trial_title=('trial_title', display_trial_title),
                    match_count=('inference_id', 'count'),""",
    """                elig['trial_title'] = elig['trial_title'].fillna(
                    TRIAL_MISSING_TITLE_LABEL)
                top = elig.groupby(['nct_id', 'trial_title']).agg(
                    match_count=('inference_id', 'count'),""", 1),
    ("    PATIENT_OUTCOME_LABELS,\n    display_trial_title,\n)",
     "    PATIENT_OUTCOME_LABELS,\n    display_trial_title,\n"
     "    TRIAL_MISSING_TITLE_LABEL,\n)", 1)], "mq_split")
if _c7:
    _at_c7, _, _ = render_tab(_c7, "render_match_quality_tab", _MQ, _MQ_DB,
                              keep_open=True)
    _cap_c7 = _capture(_at_c7)
    _top_c7 = _top_table(_cap_c7)
    _mixed_c7 = ([] if _top_c7 is None
                 else list(_top_c7[_top_c7["NCT ID"] == "NCT-Q-MIXED"]
                           ["Match Count"]))
    check("C7  WITH a fillna-before-grouping fix, the mixed trial is ranked "
          "TWICE, its four rows split across the two entries",
          sorted(int(v) for v in _mixed_c7), [1, 3])
    check("C7  ...so no entry carries its true count, even though the two "
          "SUM to it -- which is why a row-preservation check alone would "
          "pass this shape",
          (sum(int(v) for v in _mixed_c7), 4 in [int(v) for v in _mixed_c7]),
          (4, False))
    check("C7  CLEAN CONTROL: the shipped panel gives it exactly one entry",
          len([1 for v in (_top["NCT ID"] if _top is not None else [])
               if v == "NCT-Q-MIXED"]), 1)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6: ISOLATION AND OFFLINE, MEASURED
# ===========================================================================

print()
print("=" * 74)
print("Section 6: isolation and offline")
print("=" * 74)

_, _iso_conns, _iso_net = render_app(_DIVERGENCE_DB)
check("6a  every database this render opened is inside the scratch tree",
      sorted({p for p in _iso_conns
              if not str(p).startswith("file:" + _TMP)
              and not str(p).startswith(_TMP)}), [])
check_true("6a  ...and it opened at least one (non-degeneracy)",
           len(_iso_conns) > 0)
check("6b  no outbound network call was attempted", _iso_net, [])

_control_before = len(_NETWORK_ATTEMPTS)
_arm_offline_guard()
try:
    _control = called(socket.create_connection, ("example.invalid", 80), 0.01)
finally:
    _disarm_offline_guard()
check_true("6c  the offline guard FIRES on a real outbound call -- without "
           "this the reading above is vacuous",
           str(_control).startswith("raised: OSError")
           and "[offline guard]" in str(_control))
check_true("6c  ...and names the frame that made it",
           len(_NETWORK_ATTEMPTS) == _control_before + 1
           and _NETWORK_ATTEMPTS[-1]["caller"].split(":")[0]
           == os.path.basename(__file__))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7: NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================

print()
print("=" * 74)
print("Section 7: the repository and the production database are unchanged")
print("=" * 74)

_paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)

_after = {k: digest_file(v) for k, v in _WATCHED.items()}
check("7a  every source file this test reads is byte-identical afterwards",
      sorted(k for k in _WATCHED if _after[k] != _DIGESTS_BEFORE[k]), [])
check_true("7a  ...and each reading is a real digest (non-degeneracy: two "
           "'absent' readings compare equal)",
           all(is_real_digest(v) for v in _after.values()))
check_true("7a  ...and they are not all the same file hashed repeatedly",
           len(set(_after.values())) == len(_after))
check("7b  the production database was not created, deleted or modified",
      digest_file(_PRODUCTION_DB), _PRODUCTION_DIGEST_BEFORE)
check("7b  ...and its existence is unchanged",
      os.path.exists(_PRODUCTION_DB), _PRODUCTION_EXISTED_BEFORE)

shutil.rmtree(_TMP, ignore_errors=True)
check("7c  the scratch tree is removed", os.path.exists(_TMP), False)


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
