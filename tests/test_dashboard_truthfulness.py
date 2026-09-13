# Dashboard Truthfulness: Not-Evaluated Verdicts, First Attempts, Named Populations
####################################################################################

"""
WHAT THIS FILE PINS (the dashboard-truthfulness pass)
-----------------------------------------------------
An audit of the rendered dashboard against a five-patient smoke run found two
defects repeated across the tabs, each with one root:

  C1/C4  A trial whose Stage 5 call FAILED (``not_evaluable``, reason
         ``per_trial_call_failed``) was displayed as "Not Eligible" with a score
         of 0, and the reproducibility tab compared it against a real answer as
         the model changing its mind.
  C2/C3  Rows were counted as patients: a resample rerun counted a patient
         twice, an errored retry counted as a clinical "No Match", and cost and
         token means were taken over whichever rows happened to be selected.

Both are fixed in ``oncotriage/dashboard/populations.py`` and read by every
tab. This file proves the fixes by driving them, and every safeguard has a
CONTROL that puts the old behaviour back -- in an in-memory rebind restored by
identity, or in a COPY of a module written to a temp directory -- and shows the
same assertion failing.

HOW IT RUNS
-----------
Sections 1-5 are pure functions over literal frames. Sections 6-9 render the
real dashboard through streamlit's ``AppTest`` against DISPOSABLE databases
built by ``initialize_database()`` inside a ``tempfile.mkdtemp`` that is removed
at the end; ``paths._RESOLVED`` is repointed and restored. Section 10 renders
against the SMOKE database at its recorded location -- the production
``inferences.db`` path -- with every ``sqlite3.connect`` rewritten to
``mode=ro&immutable=1`` and every other connect REFUSED, and its sha256 compared
before and after. On a checkout without that database section 10 records SKIPS,
never passes.

No network (every render runs under a socket guard that RAISES, fired once as a
control), no keys, no spend, no live Qdrant, no model load, no corpus, no git
history. It EXECS NOTHING and loads no module by location: a plant is a copy
imported by name from the temp directory.
"""

import ast
import base64
import contextlib
import hashlib
import importlib
import io as _io
import json
import os
import pickle
import re
import shutil
import socket
import sqlite3
import sys
import tempfile
import traceback
import urllib.parse
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from oncotriage import paths as _paths
from oncotriage.agent import state as _state
from oncotriage.dashboard import populations as _pop
from oncotriage.dashboard import sidebar as _sidebar
from oncotriage.dashboard import tiers as _tiers
from oncotriage.dashboard import app as _app
from oncotriage.dashboard.tabs import demographics as _demo
from oncotriage.dashboard.tabs import match_quality as _mq
from oncotriage.dashboard.tabs import overview as _overview
from oncotriage.dashboard.tabs import patient_explorer as _pe
from oncotriage.dashboard.tabs import reproducibility as _repro
from oncotriage.storage import queries as _queries
from oncotriage.storage.database_logger import initialize_database


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(label)
        print(f"  FAIL  {label}")
        print(f"          expected: {expected!r}")
        print(f"          actual:   {actual!r}")


def check_true(label, condition):
    check(label, bool(condition), True)


def fail(label, detail):
    _RESULTS["failed"] += 1
    _FAILURES.append(label)
    print(f"  FAIL  {label}\n          {detail}")


def skip(label, reason):
    """Coverage that could NOT be exercised here. NEVER counted as a pass."""
    _RESULTS["skipped"] += 1
    print(f"  SKIP  {label}\n          {reason}")


def called(fn, *args, **kwargs):
    """``fn(*args)``, or a marker string when it raises -- never an abort."""
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


def section(title):
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")
_SMOKE_DB = os.path.abspath(_paths.inferences_path)
_SMOKE_DIGEST_BEFORE = digest_file(_SMOKE_DB)

_WATCHED = {name: os.path.abspath(mod.__file__) for name, mod in (
    ("populations.py", _pop), ("tiers.py", _tiers), ("sidebar.py", _sidebar),
    ("app.py", _app), ("overview.py", _overview), ("reproducibility.py", _repro),
    ("patient_explorer.py", _pe), ("match_quality.py", _mq),
    ("demographics.py", _demo), ("queries.py", _queries))}
_DIGESTS_BEFORE = {k: digest_file(v) for k, v in _WATCHED.items()}

_TMP = tempfile.mkdtemp(prefix="oncotriage-dash-truth-")
_PLANT_DIR = os.path.join(_TMP, "plants")
os.makedirs(_PLANT_DIR, exist_ok=True)
_SEQ = [0]

_FAILED = "per_trial_call_failed"
_DECLARED = _queries.NOT_EVALUABLE_REASONS_DECLARED[0]


def _plant(source_path, replacements, prefix):
    """A COPY of ``source_path`` with ``(old, new)`` applied, importable by name.

    Each ``old`` must occur EXACTLY once and the result must parse: a plant that
    matched nothing is a working check reported as broken.
    """
    text = Path(source_path).read_text(encoding="utf-8")
    for old, new in replacements:
        if text.count(old) != 1:
            fail(f"PLANT-FAILED in {os.path.basename(source_path)}",
                 f"anchor occurs {text.count(old)} time(s): {old[:80]!r}")
            return None
        text = text.replace(old, new)
    try:
        ast.parse(text)
    except SyntaxError as exc:
        fail(f"PLANT-FAILED in {os.path.basename(source_path)}",
             f"the planted copy does not parse: {exc}")
        return None
    _SEQ[0] += 1
    name = f"{prefix}_plant_{_SEQ[0]}"
    Path(os.path.join(_PLANT_DIR, name + ".py")).write_text(text,
                                                            encoding="utf-8")
    return name


def _line_block(source_path, marker, n_lines):
    """The ``n_lines`` source lines starting at the one line containing ``marker``.

    Anchors are LIFTED from the shipped file rather than retyped, so a plant
    cannot fail over a whitespace difference the reader cannot see.
    """
    lines = Path(source_path).read_text(encoding="utf-8").split("\n")
    hits = [i for i, line in enumerate(lines) if marker in line]
    if len(hits) != 1:
        return None
    return "\n".join(lines[hits[0]:hits[0] + n_lines])


#------------------------------------------------------------------------------


# ===========================================================================
# THE RENDER HARNESS, THE CONNECT RECORDER AND THE OFFLINE GUARD
# ===========================================================================

_DRIVER_APP = """
import importlib, json, sys
import streamlit as _st
# THE EXPORT IS RECORDED, NOT RE-BUILT: the tab's own CSV string is written to
# a file as the real st.download_button receives it. AppTest has no accessor
# for a download button, and rebuilding the CSV here would test a copy.
if not getattr(_st.download_button, "_truth_recorder", False):
    _real_download = _st.download_button
    def _recording_download(label, data=None, *args, **kwargs):
        with open({downloads!r}, "a", encoding="utf-8") as _fh:
            _fh.write(json.dumps({{"label": label, "data": data
                                  if isinstance(data, str) else None}}) + "\\n")
        return _real_download(label, data, *args, **kwargs)
    _recording_download._truth_recorder = True
    _st.download_button = _recording_download
sys.path.insert(0, {extra_path!r})
importlib.import_module({module!r}).main()
"""

_DRIVER_TAB = """
import pickle, importlib, sys
sys.path.insert(0, {extra_path!r})
_mod = importlib.import_module({module!r})
with open({frame!r}, "rb") as _fh:
    _df = pickle.load(_fh)
getattr(_mod, {fn!r})(_df)
"""

_REAL_CONNECT = sqlite3.connect
_CONNECTS = []
_NETWORK = []
_REAL_SOCK = (socket.socket.connect, socket.socket.connect_ex,
              socket.create_connection, socket.getaddrinfo)


_DOWNLOADS = os.path.join(_TMP, "downloads.jsonl")
_DL = {}


def _clear_downloads():
    if os.path.exists(_DOWNLOADS):
        os.remove(_DOWNLOADS)


def _last_download(fragment):
    """The data of the LAST recorded download whose label contains ``fragment``."""
    if not os.path.exists(_DOWNLOADS):
        return None
    hits = [json.loads(line) for line in Path(_DOWNLOADS).read_text(
        encoding="utf-8").splitlines() if line.strip()]
    hits = [h for h in hits if fragment in h["label"]]
    return hits[-1]["data"] if hits else None


def _arr(values):
    """A plotly spec array as a list: plain, or base64 typed-array encoded."""
    if isinstance(values, dict) and "bdata" in values:
        out = np.frombuffer(base64.b64decode(values["bdata"]),
                            dtype=np.dtype(values["dtype"]))
        return out.tolist()
    return list(values) if values is not None else []


def _charts(b):
    """``{title: first trace}`` for every plotly chart in a container."""
    out = {}
    for element in b.get("plotly_chart"):
        try:
            spec = json.loads(element.proto.spec)
        except Exception:                               # noqa: BLE001 -- skipped
            continue
        title = (spec.get("layout", {}).get("title") or {}).get("text")
        data = spec.get("data") or [{}]
        out[title] = {k: _arr(data[0].get(k)) for k in ("x", "y", "text")}
        out[title]["orientation"] = data[0].get("orientation")
    return out


def _where():
    for frame in reversed(traceback.extract_stack()):
        if os.path.basename(frame.filename) != "socket.py" and \
                not frame.name.startswith("_guard") and frame.name != "_where":
            return f"{os.path.basename(frame.filename)}:{frame.lineno}"
    return "unknown"


def _guard(call):
    def _blocked(*args, **kwargs):
        _NETWORK.append((call, _where()))
        raise OSError(f"[offline guard] {call} blocked")
    return _blocked


def _arm():
    socket.socket.connect = _guard("socket.connect")
    socket.socket.connect_ex = _guard("socket.connect_ex")
    socket.create_connection = _guard("socket.create_connection")
    socket.getaddrinfo = _guard("socket.getaddrinfo")


def _disarm():
    (socket.socket.connect, socket.socket.connect_ex,
     socket.create_connection, socket.getaddrinfo) = _REAL_SOCK


def _recording_connect(database, *args, **kwargs):
    _CONNECTS.append(str(database))
    return _REAL_CONNECT(database, *args, **kwargs)


def _smoke_connect(database, *args, **kwargs):
    """Read-only, immutable, and only the smoke database."""
    target = str(database)
    path = (urllib.parse.unquote(target[5:].split("?")[0])
            if target.startswith("file:") else target)
    if os.path.realpath(path) != os.path.realpath(_SMOKE_DB):
        _CONNECTS.append("REFUSED:" + target)
        raise sqlite3.OperationalError(f"truthfulness harness refused {target}")
    _CONNECTS.append("smoke-ro")
    kwargs.pop("uri", None)
    return _REAL_CONNECT("file:" + urllib.parse.quote(_SMOKE_DB)
                         + "?mode=ro&immutable=1", *args, uri=True, **kwargs)


def _block(b):
    """Everything a figure check reads out of one rendered container."""
    def safe(kind):
        try:
            return [e.value for e in getattr(b, kind)]
        except Exception as exc:                       # noqa: BLE001 -- reported
            return [f"<capture failed {exc!r}>"]
    return {"metrics": [(m.label, m.value, m.delta) for m in b.metric],
            "caption": safe("caption"), "info": safe("info"),
            "markdown": safe("markdown"),
            "frames": [d.value for d in b.dataframe],
            "plotly": _charts(b)}


def _session(script, db_path, actions=None, connect=_recording_connect):
    """Run ``script`` then ``actions(at, capture)``; return captures and records.

    The guard and the connect recorder stay armed for EVERY rerun ``actions``
    triggers, not only the first -- a selectbox change is a rerun.
    """
    _paths._RESOLVED["inferences_path"] = db_path
    st.cache_data.clear()
    del _CONNECTS[:]
    del _NETWORK[:]
    _clear_downloads()
    captures = {}
    sqlite3.connect = connect
    _arm()
    try:
        at = AppTest.from_string(script, default_timeout=300)
        at.run()
        captures["exception"] = [str(e.value).splitlines()[0]
                                 for e in at.exception]

        def capture(key):
            captures[key] = {"tabs": {t.label: _block(t) for t in at.tabs},
                             "root": _block(at), "sidebar": _block(at.sidebar),
                             "exception": [str(e.value).splitlines()[0]
                                           for e in at.exception]}
        capture("initial")
        if actions is not None:
            try:
                actions(at, capture)
            except Exception as exc:                   # noqa: BLE001 -- reported
                captures["action_error"] = f"{type(exc).__name__}: {exc}"
    finally:
        _disarm()
        sqlite3.connect = _REAL_CONNECT
    return captures, list(_CONNECTS), list(_NETWORK)


def render_app(db_path, module="oncotriage.dashboard.app", actions=None,
               connect=_recording_connect):
    return _session(_DRIVER_APP.format(extra_path=_PLANT_DIR, module=module,
                                       downloads=_DOWNLOADS),
                    db_path, actions, connect)


def render_tab(module, fn, frame, db_path):
    _SEQ[0] += 1
    path = os.path.join(_TMP, f"frame_{_SEQ[0]}.pkl")
    with open(path, "wb") as fh:
        pickle.dump(frame, fh)
    caps, connects, network = _session(
        _DRIVER_TAB.format(extra_path=_PLANT_DIR, module=module, frame=path,
                           fn=fn), db_path)
    return caps["initial"]["root"], caps["exception"], network


def connected_databases(connects):
    """The distinct database FILES a render opened, whatever the spelling.

    The run loaders open ``file:<path>?mode=ro`` URIs and the original three
    open the bare path; both name one file, and a check comparing strings would
    count it twice.
    """
    out = set()
    for target in connects:
        if target.startswith("file:"):
            target = urllib.parse.unquote(target[5:].split("?")[0])
        out.add(os.path.realpath(target))
    return sorted(out)


def tab(capture, fragment):
    for label, block in capture["tabs"].items():
        if fragment in label:
            return block
    return {"metrics": [], "caption": [], "info": [], "markdown": [],
            "frames": [], "plotly": {}}


def metric(block, label, n=0, prefix=False):
    """``(value, delta)`` of the n-th metric so labelled, or a named absence."""
    hits = [(v, d) for (lab, v, d) in block["metrics"]
            if (lab.startswith(label) if prefix else lab == label)]
    return hits[n] if len(hits) > n else ("(absent)", None)


def frame_with(block, column):
    for frame in block["frames"]:
        if isinstance(frame, pd.DataFrame) and column in frame.columns:
            return frame
    return pd.DataFrame()


def joined(block, kind="caption"):
    return "\n".join(str(x) for x in block[kind])


#------------------------------------------------------------------------------


# ===========================================================================
# DISPOSABLE DATABASES
# ===========================================================================

def _new_db(name):
    path = os.path.join(_TMP, name + ".db")
    with contextlib.redirect_stdout(_io.StringIO()), \
            contextlib.redirect_stderr(_io.StringIO()):
        initialize_database(path)
    return path


def _inference(cur, pid, n, *, evaluated, error="", eligible_matches=None,
               tokens_in=9000, tokens_out=900, cost=0.10, cached=0.06,
               age=60, sex="female", race="White",
               ethnicity="Not Hispanic or Latino", condition="Breast cancer",
               conditions=4, medications=2):
    """One inference row. ``n`` orders the timestamps, so MIN(id) is first."""
    cur.execute(
        "INSERT INTO inferences (patient_id, timestamp, age, sex, race, "
        "ethnicity, primary_condition, condition_count, medication_count, "
        "candidates_retrieved, candidates_reranked, "
        "candidates_after_rule_filter, candidates_after_quality_filter, "
        "candidates_filtered, candidates_evaluated, eligible_matches, "
        "total_time, estimated_cost_usd, estimated_cost_cached_usd, "
        "llm_classifier_input_tokens, llm_classifier_output_tokens, "
        "llm_classifier_calls, llm_classifier_evaluation_time, "
        "query_expansion_time, hybrid_retrieval_time, cross_encoder_time, "
        "rule_filter_time, matching_model, error, qdrant_collection, "
        "patient_data_hash, run_id) VALUES "
        "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (pid, f"2026-09-01 10:{n:02d}:00", age, sex, race, ethnicity,
         condition, conditions, medications, 200, 50, 30, 20, 12,
         evaluated, eligible_matches, 20.0 + n, cost, cached, tokens_in,
         tokens_out, 3, 5.0, 0.1, 1.0, 2.0, 0.3, "gpt-5.6-terra", error,
         "trial_criteria_20260901", f"hash-{pid}", 1))
    return cur.lastrowid


def _trial(cur, inference_id, nct, eligible, score=0.0, reason=None):
    cur.execute(
        "INSERT INTO trial_matches (inference_id, nct_id, trial_title, "
        "trial_phase, eligible, match_score, assessment, rerank_score, "
        "not_evaluable_reason) VALUES (?,?,?,?,?,?,?,?,?)",
        (inference_id, nct, f"Title of {nct}", "PHASE2", eligible, score,
         "assessment text", 0.7, reason))


def _run_row(cur):
    cur.execute("INSERT INTO runs (id, started_at, finished_at, status, "
                "invocation_source) VALUES (1, '2026-09-01T10:00:00Z', "
                "'2026-09-01T11:00:00Z', 'FINISHED', 'test')")


_NO_USABLE_REASONS = (tuple(_queries.NOT_EVALUABLE_REASONS_CONSTRUCTED)
                      + tuple(_queries.NOT_EVALUABLE_REASONS_CORRECTED))


def _expected_export_rows(conn, patient_id):
    """What the patient CSV's trial rows must say, from SQL and the ruling alone.

    Which inference: the patient's MOST RECENT row, the Patient Explorer's
    default selection. Status, reason and score are written from the verdict
    and the stored reason directly -- never through populations.py.
    """
    inf = conn.execute("SELECT id FROM inferences WHERE patient_id = ? "
                       "ORDER BY timestamp DESC, id ASC LIMIT 1",
                       (patient_id,)).fetchone()
    if inf is None:
        return []
    out = []
    for nct, verdict, score, reason in conn.execute(
            "SELECT nct_id, eligible, match_score, not_evaluable_reason "
            "FROM trial_matches WHERE inference_id = ?", (inf[0],)):
        if verdict == "eligible":
            status = ("✅ Eligible" if score >= 1.0 else
                      _tiers.TRIAL_STATUS_PARTIAL if score > 0 else
                      _tiers.TRIAL_STATUS_UNCONFIRMED)
            cells = ("", f"{score * 100:.0f}%")
        elif verdict == "not_eligible":
            status, cells = _tiers.TRIAL_STATUS_REJECTED, ("", f"{score * 100:.0f}%")
        else:
            if verdict == "not_evaluable" and reason in _queries.NOT_EVALUABLE_REASONS_DECLARED:
                status = _tiers.TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN
            elif verdict == "not_evaluable" and reason in _NO_USABLE_REASONS:
                status = _tiers.TRIAL_STATUS_NOT_EVALUATED_FAILED
            elif verdict == "not_evaluable":
                status = _tiers.TRIAL_STATUS_NOT_EVALUATED_UNKNOWN
            else:
                status = _tiers.TRIAL_STATUS_UNRECOGNISED
            cells = (reason if reason is not None else _pop.REASON_NOT_RECORDED_TEXT,
                     "—")
        out.append((nct, status) + cells)
    return sorted(out)


def _rendered_export_rows(csv_text):
    if not csv_text:
        return "(no export recorded)"
    frame = pd.read_csv(_io.StringIO(csv_text), dtype=str, keep_default_na=False)
    trials = frame[frame["Section"] == "Trial Match"]
    return sorted(zip(trials["NCT ID"], trials["Status"],
                      trials["Not Evaluated Reason"], trials["Match Score"]))


def _complete_first_attempts(conn):
    """First attempts whose evaluation is COMPLETE, by the ruling, in SQL + Python.

    Complete: no error; every trial row a definite verdict or a declared
    uncertainty; no fewer trial rows than trials evaluated. One campaign is
    assumed (MIN(id) per patient), which is true of every database this file
    reads.
    """
    out = []
    for (inf_id, err, evaluated, age, sex, race, eth, cond, n_cond,
         n_med) in conn.execute(
            "SELECT id, error, candidates_evaluated, age, sex, race, ethnicity, "
            "primary_condition, condition_count, medication_count FROM inferences "
            "WHERE id IN (SELECT MIN(id) FROM inferences GROUP BY patient_id)"):
        trials = conn.execute("SELECT eligible, not_evaluable_reason FROM "
                              "trial_matches WHERE inference_id = ?",
                              (inf_id,)).fetchall()
        usable = all(v in ("eligible", "not_eligible") or
                     (v == "not_evaluable"
                      and r in _queries.NOT_EVALUABLE_REASONS_DECLARED)
                     for v, r in trials)
        short = evaluated is not None and len(trials) < int(evaluated)
        if err or not usable or short:
            continue
        out.append({"age": age, "sex": sex, "race": race, "ethnicity": eth,
                    "condition": cond, "conditions": n_cond, "medications": n_med,
                    "match": any(v == "eligible" for v, _ in trials)})
    return out


def _bucket(value, edges, labels):
    for (lo, hi), label in zip(zip(edges, edges[1:]), labels):
        if lo < value <= hi:
            return label
    return None


_GROUP_KEYS = {
    "Match Rate by Age Group": (False, lambda r: f"{int(r['age'] // 10 * 10)}s", "n"),
    "Match Rate by Sex": (False, lambda r: r["sex"], "n"),
    "Match Rate by Race": (True, lambda r: r["race"], "n+rate"),
    "Match Rate by Ethnicity": (True, lambda r: r["ethnicity"], "n+rate"),
    "Match Rate by Condition Count": (False, lambda r: _bucket(
        r["conditions"], [-1, 2, 5, 10, 20, 100],
        ["0-2", "3-5", "6-10", "11-20", "20+"]), "n"),
    "Match Rate by Medication Count": (False, lambda r: _bucket(
        r["medications"], [-1, 3, 7, 12, 20, 100],
        ["0-3", "4-7", "8-12", "13-20", "20+"]), "n"),
}


def _expected_group_chart(rows, title):
    horizontal, key, text_kind = _GROUP_KEYS[title]
    groups = {}
    for row in rows:
        k = key(row)
        n, m = groups.get(k, (0, 0))
        groups[k] = (n + 1, m + int(row["match"]))
    out = {}
    for k, (n, m) in groups.items():
        rate = m / n * 100
        text = f"n={n}" if text_kind == "n" else f"n={n}  ({rate:.0f}%)"
        out[k] = (round(rate, 4), text)
    return out


def _rendered_group_chart(charts, title):
    chart = charts.get(title)
    if chart is None:
        return "(chart absent)"
    horizontal = _GROUP_KEYS[title][0]
    labels = chart["y"] if horizontal else chart["x"]
    values = chart["x"] if horizontal else chart["y"]
    return {str(l): (round(float(v), 4), t)
            for l, v, t in zip(labels, values, chart["text"])}


def _frames(db_path):
    conn = _REAL_CONNECT(db_path)
    try:
        inferences = pd.read_sql_query("SELECT * FROM inferences", conn)
        matches = pd.read_sql_query("SELECT * FROM trial_matches", conn)
    finally:
        conn.close()
    inferences["timestamp"] = pd.to_datetime(inferences["timestamp"])
    return inferences, matches


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 0: PREMISES AND THE OFFLINE GUARD'S CONTROL
# ===========================================================================

section("Section 0: premises")
print(f"Scratch root: {_TMP}")

check("0a  per_trial_call_failed is a CONSTRUCTED reason -- the seed's premise "
      "that a failed call is an infrastructure failure",
      _FAILED in _queries.NOT_EVALUABLE_REASONS_CONSTRUCTED, True)
check("0b  exactly one DECLARED reason exists -- clinical uncertainty",
      len(_queries.NOT_EVALUABLE_REASONS_DECLARED), 1)

_arm()
_guard_fired = called(socket.getaddrinfo, "example.invalid", 443)
_guard_records = list(_NETWORK)
_disarm()
del _NETWORK[:]
check_true("0c  CONTROL: the offline guard RAISES its own error on a real lookup, "
           "so a render recording zero attempts is a measurement",
           isinstance(_guard_fired, str) and "[offline guard]" in _guard_fired)
check("0d  ...and it recorded the attempt", len(_guard_records), 1)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1: THE VERDICT VOCABULARY AND THE ONE PER-TRIAL CLASSIFIER (C1)
# ===========================================================================

section("Section 1: the verdict vocabulary")

check("1a  the dashboard's three verdict values are the agent's",
      (_pop.VERDICT_ELIGIBLE, _pop.VERDICT_NOT_ELIGIBLE,
       _pop.VERDICT_NOT_EVALUABLE),
      (_state.TRIAL_VERDICT_ELIGIBLE, _state.TRIAL_VERDICT_NOT_ELIGIBLE,
       _state.TRIAL_VERDICT_NOT_EVALUABLE))
check("1b  DEFINITE_VERDICTS is eligible and not_eligible and nothing else",
      set(_pop.DEFINITE_VERDICTS),
      {_state.TRIAL_VERDICT_ELIGIBLE, _state.TRIAL_VERDICT_NOT_ELIGIBLE})

# TWO POPULATIONS, TWO LABELS. "Answered" meant a definite verdict on one tile
# and a usable result on another; the difference is a declared uncertainty.
check("1b-i the two population labels are the ruled words",
      (_pop.DEFINITE_VERDICT_LABEL, _pop.USABLE_RESULT_LABEL),
      ("definite eligibility verdict",
       "usable result, including clinical uncertainty"))
_POP_CASES = [  # (verdict, reason, definite verdict?, usable result?)
    ("eligible", None, True, True),
    ("not_eligible", None, True, True),
    ("not_evaluable", _DECLARED, False, True),
    ("not_evaluable", _FAILED, False, False),
    ("not_evaluable", None, False, False),
    ("maybe", None, False, False),
    (None, None, False, False),
]
check("1b-ii definite verdicts and usable results are DIFFERENT populations, "
      "parting exactly at a declared clinical uncertainty",
      [(v, r) for v, r, d, u in _POP_CASES
       if (called(_pop.is_definite_verdict, v),
           called(_pop.is_usable_result, v, r)) != (d, u)], [])
check("1b-iii CONTROL: a 'definite verdict' reading that admits clinical "
      "uncertainty is caught, on the declared row alone",
      [(v, r) for v, r, d, u in _POP_CASES
       if called(_pop.is_usable_result, v, r) != d], [("not_evaluable", _DECLARED)])
check("1b-iv CONTROL: a 'usable result' reading that drops clinical uncertainty "
      "is caught, on the declared row alone",
      [(v, r) for v, r, d, u in _POP_CASES
       if called(_pop.is_definite_verdict, v) != u], [("not_evaluable", _DECLARED)])

_ALL_REASONS = (tuple(_queries.NOT_EVALUABLE_REASONS_CONSTRUCTED)
                + tuple(_queries.NOT_EVALUABLE_REASONS_CORRECTED)
                + tuple(_queries.NOT_EVALUABLE_REASONS_DECLARED))
check_true("1c  non-degeneracy: the reason vocabulary under test is not empty",
           len(_ALL_REASONS) >= 11)


def _kind_defects(kind_fn):
    expected = {r: _pop.NOT_EVALUATED_NO_USABLE_VERDICT for r in
                tuple(_queries.NOT_EVALUABLE_REASONS_CONSTRUCTED)
                + tuple(_queries.NOT_EVALUABLE_REASONS_CORRECTED)}
    expected[_DECLARED] = _pop.NOT_EVALUATED_CLINICAL_UNCERTAINTY
    for unknown in (None, "", "a reason this pipeline never wrote"):
        expected[unknown] = _pop.NOT_EVALUATED_REASON_UNKNOWN
    return sorted(str(r) for r, want in expected.items()
                  if called(kind_fn, r) != want)


check("1d  every stored reason reads as its own kind: constructed and corrected "
      "are NO USABLE VERDICT, declared is CLINICAL UNCERTAINTY, anything else "
      "is REASON UNKNOWN", _kind_defects(_pop.not_evaluated_kind), [])
check_true("1e  CONTROL: a reading that calls every not-evaluable row clinical "
           "uncertainty is caught",
           _kind_defects(lambda r: _pop.NOT_EVALUATED_CLINICAL_UNCERTAINTY) != [])

_STATUS_CASES = [
    ("eligible", 1.0, None, "✅ Eligible"),
    ("eligible", 0.5, None, _tiers.TRIAL_STATUS_PARTIAL),
    ("eligible", 0.0, None, _tiers.TRIAL_STATUS_UNCONFIRMED),
    ("eligible", None, None, _tiers.TRIAL_STATUS_NO_SCORE),
    ("not_eligible", 0.0, None, _tiers.TRIAL_STATUS_REJECTED),
    ("not_evaluable", 0.0, _FAILED, _tiers.TRIAL_STATUS_NOT_EVALUATED_FAILED),
    ("not_evaluable", 0.0, _DECLARED,
     _tiers.TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN),
    ("not_evaluable", 0.0, None, _tiers.TRIAL_STATUS_NOT_EVALUATED_UNKNOWN),
    ("maybe", 0.0, None, _tiers.TRIAL_STATUS_UNRECOGNISED),
    (None, 0.0, None, _tiers.TRIAL_STATUS_UNRECOGNISED),
]


def _status_defects(status_fn):
    return [(v, r) for v, s, r, want in _STATUS_CASES
            if called(status_fn, v, s, r) != want]


def _pre_fix_status(verdict, score, reason=None):
    """The rule three tabs carried: every non-eligible value is a rejection."""
    if verdict != "eligible":
        return _tiers.TRIAL_STATUS_REJECTED
    return _pop.trial_display_status(verdict, score, reason)


check("1f  trial_display_status gives every verdict its own status and never "
      "calls a failed call a rejection",
      _status_defects(_pop.trial_display_status), [])
_pre_fix_defects = _status_defects(_pre_fix_status)
check_true("1g  CONTROL: the pre-fix rule is caught -- and specifically on the "
           "failed-call row",
           ("not_evaluable", _FAILED) in _pre_fix_defects)
check("1h  no not-evaluated status is the rejection string",
      [s for s in (_tiers.TRIAL_STATUS_NOT_EVALUATED_FAILED,
                   _tiers.TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN,
                   _tiers.TRIAL_STATUS_NOT_EVALUATED_UNKNOWN)
       if s == _tiers.TRIAL_STATUS_REJECTED], [])
check("1i  a not-evaluated row's stored 0.0 is NOT displayed as a score",
      _pop.display_score("not_evaluable", 0.0), None)
check("1j  ...while a definite verdict's 0.0 is (the control: display_score is not "
      "blanket-None)", _pop.display_score("eligible", 0.0), 0.0)
check("1k  the reason is shown VERBATIM for a not-evaluated row",
      _pop.reason_text("not_evaluable", _FAILED), _FAILED)
check("1l  ...and a missing one is named rather than blank",
      _pop.reason_text("not_evaluable", None), _pop.REASON_NOT_RECORDED_TEXT)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: THE INCOMPLETENESS RULE (C2)
# ===========================================================================

section("Section 2: evaluation completeness")

_UNIT_INF = pd.DataFrame([
    {"id": 1, "patient_id": "U1", "error": "timeout", "candidates_evaluated": 0},
    {"id": 2, "patient_id": "U2", "error": "", "candidates_evaluated": 2},
    {"id": 3, "patient_id": "U3", "error": "", "candidates_evaluated": 2},
    {"id": 4, "patient_id": "U4", "error": None, "candidates_evaluated": 1},
    {"id": 5, "patient_id": "U5", "error": "", "candidates_evaluated": 3},
    {"id": 6, "patient_id": "U6", "error": "", "candidates_evaluated": None},
    {"id": 7, "patient_id": "U7", "error": "", "candidates_evaluated": 1},
    {"id": 8, "patient_id": "U8", "error": "", "candidates_evaluated": 1},
])
_UNIT_TM = pd.DataFrame([
    {"inference_id": 2, "nct_id": "A", "eligible": "not_eligible",
     "match_score": 0.0, "not_evaluable_reason": None},
    {"inference_id": 2, "nct_id": "B", "eligible": "not_evaluable",
     "match_score": 0.0, "not_evaluable_reason": _DECLARED},
    {"inference_id": 3, "nct_id": "A", "eligible": "eligible",
     "match_score": 1.0, "not_evaluable_reason": None},
    {"inference_id": 3, "nct_id": "B", "eligible": "not_evaluable",
     "match_score": 0.0, "not_evaluable_reason": _FAILED},
    {"inference_id": 4, "nct_id": "A", "eligible": "not_evaluable",
     "match_score": 0.0, "not_evaluable_reason": None},
    {"inference_id": 5, "nct_id": "A", "eligible": "eligible",
     "match_score": 1.0, "not_evaluable_reason": None},
    {"inference_id": 6, "nct_id": "A", "eligible": "eligible",
     "match_score": 1.0, "not_evaluable_reason": None},
    {"inference_id": 7, "nct_id": "A", "eligible": "maybe",
     "match_score": 0.0, "not_evaluable_reason": None},
    {"inference_id": 8, "nct_id": "A", "eligible": "not_evaluable",
     "match_score": 0.0, "not_evaluable_reason": _DECLARED},
])
_EXPECTED_STATES = {
    1: _tiers.EVALUATION_ERRORED,           # the attempt errored
    2: _tiers.EVALUATION_COMPLETE,          # clinical uncertainty is an answer
    3: _tiers.EVALUATION_MISSING_VERDICTS,  # a failed call
    4: _tiers.EVALUATION_MISSING_VERDICTS,  # a reason nobody recorded
    5: _tiers.EVALUATION_MISSING_VERDICTS,  # 1 trial row of 3 evaluated
    6: _tiers.EVALUATION_COMPLETE,          # evaluated unknown: not presumed short
    7: _tiers.EVALUATION_MISSING_VERDICTS,  # an unrecognised verdict value
    8: _tiers.EVALUATION_COMPLETE,          # declared-only
}


def _states():
    out = called(_pop.annotate_evaluation_state, _UNIT_INF, _UNIT_TM)
    if isinstance(out, str):
        return out
    return dict(zip(out["id"], out[_tiers.EVALUATION_STATE_COLUMN]))


check("2a  the incompleteness rule gives each of the eight shapes its state",
      _states(), _EXPECTED_STATES)

_SHIPPED_STATE_OF = _pop._state_of


def _state_any_not_evaluable(row):
    """CONTROL: the rule the ruling forbids -- ANY not_evaluable is incomplete."""
    if row.get("error"):
        return _tiers.EVALUATION_ERRORED
    if row["trials_definite_verdict"] < row["trial_rows"]:
        return _tiers.EVALUATION_MISSING_VERDICTS
    return _tiers.EVALUATION_COMPLETE


def _state_ignoring_verdicts(row):
    """CONTROL: the pre-fix reading -- only an error makes a row incomplete."""
    return (_tiers.EVALUATION_ERRORED if row.get("error")
            else _tiers.EVALUATION_COMPLETE)


try:
    _pop._state_of = _state_any_not_evaluable
    _ctl_a = _states()
    _pop._state_of = _state_ignoring_verdicts
    _ctl_b = _states()
    _pop._state_of = lambda row: "not a state"
    _ctl_c = called(_pop.annotate_evaluation_state, _UNIT_INF, _UNIT_TM)
finally:
    _pop._state_of = _SHIPPED_STATE_OF
check_true("2b  the rebind seam is restored BY IDENTITY",
           _pop._state_of is _SHIPPED_STATE_OF)
check_true("2c  CONTROL: 'any not_evaluable is incomplete' is caught on the "
           "clinical-uncertainty rows",
           isinstance(_ctl_a, dict)
           and _ctl_a.get(2) != _tiers.EVALUATION_COMPLETE
           and _ctl_a.get(8) != _tiers.EVALUATION_COMPLETE)
check_true("2d  CONTROL: 'verdicts do not matter' is caught on the failed-call row",
           isinstance(_ctl_b, dict)
           and _ctl_b.get(3) == _tiers.EVALUATION_COMPLETE)
check_true("2e  a state outside the closed vocabulary RAISES rather than being "
           "absorbed", isinstance(_ctl_c, str) and "RuntimeError" in _ctl_c)

_annotated = _pop.annotate_evaluation_state(_UNIT_INF, _UNIT_TM)
_enriched = _tiers.enrich_match_tiers(_annotated, _UNIT_TM)
_tier_by_id = dict(zip(_enriched["id"], _enriched["match_tier"]))
check("2f  errored and verdict-less rows carry the Incomplete tier, never a "
      "clinical one", [_tier_by_id[i] for i in (1, 3, 4, 5, 7)],
      [_tiers.MATCH_TIER_INCOMPLETE] * 5)
check("2g  a clinical-uncertainty row is tiered clinically (No Match here)",
      _tier_by_id[2], _tiers.MATCH_TIER_NO_MATCH)
_bare = _tiers.enrich_match_tiers(
    _annotated.drop(columns=_tiers.EVALUATION_STATE_COLUMN), _UNIT_TM)
check_true("2h  CONTROL: without the state column the failed-call row is tiered "
           "as a clinical outcome -- the state is what prevents it",
           dict(zip(_bare["id"], _bare["match_tier"]))[3]
           != _tiers.MATCH_TIER_INCOMPLETE)
_ensured = _pop.ensure_evaluated(_bare, _UNIT_TM)
check("2i  ensure_evaluated repairs a frame nobody annotated",
      dict(zip(_ensured["id"], _ensured["match_tier"]))[3],
      _tiers.MATCH_TIER_INCOMPLETE)
check_true("2j  ...and is a no-op (the same object) on an annotated frame",
           _pop.ensure_evaluated(_enriched, _UNIT_TM) is _enriched)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3: FIRST ATTEMPTS, CAMPAIGNS AND THE OUTCOME-FILTER ORDER (C2)
# ===========================================================================

section("Section 3: first attempts and campaigns")

_FA = pd.DataFrame({"id": [3, 1, 7, 2, 9, 8],
                    "patient_id": ["A", "A", "A", "B", "C", "C"]})
_fa_ids = sorted(_pop.first_attempts(_FA)["id"])
check("3a  unequal rerun counts (3, 1, 2): one first attempt per patient, the "
      "MIN(id)", _fa_ids, [1, 2, 8])

_CAMPAIGNS = pd.DataFrame({"campaign_id": [1, 4], "run_ids": ["1 -> 3", "4"]})
_KEYED = _pop.attach_campaign_key(
    pd.DataFrame({"id": [1, 2, 3, 4, 5],
                  "patient_id": ["A", "A", "A", "B", "C"],
                  "run_id": [1, 3, 4, None, 99]}), _CAMPAIGNS)
check("3b  runs map to their stitched campaign; NULL and unknown runs are named",
      list(_KEYED[_pop.CAMPAIGN_KEY_COLUMN]),
      ["campaign 1", "campaign 1", "campaign 4", _pop.CAMPAIGN_KEY_NO_RUN_ID,
       "run 99 (unstitched)"])
check("3c  a patient in two campaigns has one first attempt in EACH",
      sorted(_pop.first_attempts(_KEYED)["id"]), [1, 3, 4, 5])

_ORDER = pd.DataFrame({
    "id": [10, 11], "patient_id": ["P", "P"],
    "match_tier": [_tiers.MATCH_TIER_NO_MATCH, "Full Match"],
    _tiers.EVALUATION_STATE_COLUMN: [_tiers.EVALUATION_COMPLETE] * 2})
_designated_then_filtered = _pop.designate_first_attempts(_ORDER)
_designated_then_filtered = _designated_then_filtered[
    _tiers.any_match_series(_designated_then_filtered)]
_filtered_then_designated = _pop.designate_first_attempts(
    _ORDER[_tiers.any_match_series(_ORDER)])
check("3d  designated BEFORE the outcome filter, a matched rerun is not promoted "
      "to first attempt", len(_pop.first_attempts(_designated_then_filtered)), 0)
check("3e  CONTROL: designated AFTER it, the rerun becomes 'first' -- the "
      "population chosen by its outcome",
      len(_pop.first_attempts(_filtered_then_designated)), 1)


def _designation_precedes_filter(source_text):
    """In render_sidebar: the designation call's line < the first ``_mask =``."""
    tree = ast.parse(source_text)
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == "render_sidebar"), None)
    if fn is None:
        return None
    designate = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)
                 and n.func.id == "designate_first_attempts"]
    masks = [n.lineno for n in ast.walk(fn) if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "_mask"
                     for t in n.targets)]
    if len(designate) != 1 or not masks:
        return None
    return designate[0] < min(masks)


_SIDEBAR_SRC = Path(_WATCHED["sidebar.py"]).read_text(encoding="utf-8")
check("3f  the shipped sidebar designates first attempts ABOVE its outcome filter",
      _designation_precedes_filter(_SIDEBAR_SRC), True)
_DESIGNATE_LINE = "    filtered_df = designate_first_attempts(filtered_df)\n"
_MASK_LINE = "        filtered_df = filtered_df[_mask]\n"
_moved = _SIDEBAR_SRC.replace(_DESIGNATE_LINE, "", 1).replace(
    _MASK_LINE, _MASK_LINE + _DESIGNATE_LINE, 1)
check_true("3g  non-degeneracy: both anchors exist exactly once in the shipped "
           "sidebar", _SIDEBAR_SRC.count(_DESIGNATE_LINE) == 1
           and _SIDEBAR_SRC.count(_MASK_LINE) == 1)
check("3h  CONTROL: the same check on a copy with the designation moved below "
      "the filter fails", _designation_precedes_filter(_moved), False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4: REPRODUCIBILITY OVER ANSWERED PAIRS ONLY (C1/C4)
# ===========================================================================

section("Section 4: reproducibility comparisons")

_GROUPS = [
    {"patient_id": "P1", "qdrant_collection": "c", "inference_ids": [11, 12],
     "num_inferences": 2},
    {"patient_id": "P2", "qdrant_collection": "c", "inference_ids": [21, 22],
     "num_inferences": 2},
    # THREE RUNS, TWO ANSWERED. Found by the tree-level revert matrix: with only
    # two-run groups, removing the definite-verdict-only filter changed nothing any
    # check could see, because the exact-category rule already skips a mixed
    # PAIR. With three runs the filter is what keeps the two answers comparable.
    {"patient_id": "P3", "qdrant_collection": "c",
     "inference_ids": [31, 32, 33], "num_inferences": 3},
]
_MATCHES = pd.DataFrame([
    (11, "A", "eligible", 1.0, None), (12, "A", "not_evaluable", 0.0, _FAILED),
    (11, "B", "not_evaluable", 0.0, _FAILED),
    (12, "B", "not_evaluable", 0.0, _FAILED),
    (11, "C", "eligible", 0.5, None), (12, "C", "not_eligible", 0.0, None),
    (11, "D", "eligible", 1.0, None), (12, "D", "eligible", 1.0, None),
    (21, "E", "not_evaluable", 0.0, _FAILED), (22, "E", "eligible", 0.8, None),
    (31, "F", "eligible", 1.0, None), (32, "F", "eligible", 1.0, None),
    (33, "F", "not_evaluable", 0.0, _FAILED),
], columns=["inference_id", "nct_id", "eligible", "match_score",
            "not_evaluable_reason"])


def _categories(module):
    comps = called(module._build_comparisons, _GROUPS, _MATCHES)
    if isinstance(comps, str):
        return comps
    return {(c["patient_id"], c["nct_id"]): c["category"] for c in comps}


check("4a  only pairs with two DEFINITE-VERDICT runs are compared, and each gets its "
      "own category", _categories(_repro),
      {("P1", "C"): "flipped", ("P1", "D"): "eligible_all",
       ("P3", "F"): "eligible_all"})
_comps = _repro._build_comparisons(_GROUPS, _MATCHES)
_f = [c for c in _comps if c["nct_id"] == "F"]
check("4a-i the three-run trial is compared over its TWO definite-verdict runs, with no "
      "spread from the failed call's stored 0.0",
      [(c["num_inferences"], c["score_spread"]) for c in _f], [(2, 0.0)])
check("4b  the exclusions are counted: 5 observations without a definite verdict, 3 pairs "
      "short of two answers, 1 re-run group with nothing comparable",
      _repro._comparison_exclusions(_GROUPS, _MATCHES, _comps),
      {"observations_without_definite_verdict": 5, "pairs_without_two_definite_verdicts": 3,
       "retested_without_comparison": 1})
_stats = _repro._summary_statistics(pd.DataFrame(_comps))
check("4c  the flip rate is over the 3 definite-verdict comparisons, not the 6 "
      "evaluated pairs", (_stats["total_comparisons"], int(_stats["flip_count"]),
                          round(_stats["flip_rate"], 4)), (3, 1, 33.3333))
check("4d  a rejection beside a failed call is NOT 'Rejection ↔ Zero Score'",
      _repro._classify_flip_type(["not_eligible", "not_evaluable"], [0.0, 0.0]),
      "Other")
check("4e  ...and a failed call cannot turn a partial flip into a zero-score one",
      _repro._classify_flip_type(["eligible", "not_eligible", "not_evaluable"],
                                 [0.5, 0.0, 0.0]),
      "Rejection ↔ Partial Match")

_REPRO_PATH = _WATCHED["reproducibility.py"]
_filter_line = _line_block(
    _REPRO_PATH, "& relevant_matches['eligible'].isin(DEFINITE_VERDICTS)]", 1)
_else_block = _line_block(_REPRO_PATH, "# unreachable after the filter", 2)
_flip_skip = _line_block(_REPRO_PATH, "if cls not in DEFINITE_VERDICTS:", 2)
check_true("4f  non-degeneracy: the three plant anchors were each found once",
           None not in (_filter_line, _else_block, _flip_skip))
_repro_old = _plant(_REPRO_PATH, [
    (_filter_line, _filter_line.split("&")[0] + "]"),
    (_else_block, _else_block.split("else:")[0] + "else:\n"
     + _else_block.split("else:")[0] + "    category = 'flipped'"),
    (_flip_skip, _flip_skip.split("if")[0] + "pass"),
], "repro_prefix") if None not in (_filter_line, _else_block, _flip_skip) else None
if _repro_old:
    if _PLANT_DIR not in sys.path:
        sys.path.insert(0, _PLANT_DIR)
    _old_mod = importlib.import_module(_repro_old)
    _old_cats = _categories(_old_mod)
    check_true("4g  CONTROL: with the pre-fix inclusion and catch-all, a failed "
               "call against an answer is a 'flip'",
               isinstance(_old_cats, dict)
               and _old_cats.get(("P1", "A")) == "flipped")
    check_true("4h  CONTROL: ...and two failed calls are a 'flip' too",
               isinstance(_old_cats, dict)
               and _old_cats.get(("P1", "B")) == "flipped")
    check("4i  CONTROL: the pre-fix flip typing reads a failed call as a zero score",
          _old_mod._classify_flip_type(["not_eligible", "not_evaluable"],
                                       [0.0, 0.0]),
          "Rejection ↔ Zero Score")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5: DISTINCT PATIENTS IN THE RUN SUMMARY (C2, F33)
# ===========================================================================

section("Section 5: run summary patients")

_SQL_DB = _new_db("run_summary")
with _REAL_CONNECT(_SQL_DB) as _c:
    _cur = _c.cursor()
    _run_row(_cur)
    for _n, _pid in enumerate(["R1", "R1", "R1", "R2"]):       # a resampled patient
        _inference(_cur, _pid, _n, evaluated=0,
                   error="boom" if _n == 3 else "")
_conn = _REAL_CONNECT(_SQL_DB)
try:
    _rs = _queries.run(_conn, "run_summary")
    _row = _rs[_rs["run_id"] == 1] if "run_id" in _rs.columns else _rs
    check("5a  run_summary counts DISTINCT patients and keeps the rows beside them",
          (int(_row["patients"].iloc[0]), int(_row["inference_rows"].iloc[0])),
          (2, 4))
    _pat_sql = _queries._RUN_HEALTH_PATIENTS_SQL
    check("5b  non-degeneracy: the DISTINCT count is in the shipped SQL exactly once",
          _pat_sql.count("COUNT(DISTINCT patient_id)"), 1)
    _old = _pat_sql.replace("COUNT(DISTINCT patient_id)", "COUNT(*)")
    _old_patients = _conn.execute(
        f"SELECT patients FROM ({_old}\n) t WHERE run_id = 1").fetchone()[0]
    check("5c  CONTROL: the pre-fix COUNT(*) reports the rows as patients",
          _old_patients, 4)
finally:
    _conn.close()


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6: THE WHOLE APP ON A DISPOSABLE DATABASE
# ===========================================================================
#
# SEVEN PATIENTS, ELEVEN INFERENCE ROWS, EVERY SHAPE THE BRIEF NAMES:
#
#   id  patient   evaluated  trials                                    state
#    1  P-CM      2          T1 eligible 1.0, T2 not_eligible          complete, Full
#    2  P-UNC     2          T1 not_eligible, T3 DECLARED               complete, No Match
#    3  P-FAILED  3          T1 eligible 0.5, T2+T3 FAILED CALL         missing verdicts
#    4  P-ERR     0          (error)                                    errored
#    5  P-RERUN   1          T2 not_eligible                            complete, No Match
#    6  P-TRIPLE  1          T1 eligible 1.0                            complete, Full
#    7  P-FLAKY   1          T4 eligible 0.5                            complete, Partial
#    8  P-RERUN   1          T2 eligible 1.0     (rerun)
#    9  P-TRIPLE  1          T1 eligible 1.0     (rerun)
#   10  P-TRIPLE  1          T1 eligible 1.0     (rerun)
#   11  P-FLAKY   1          T4 FAILED CALL      (rerun)
#
# First attempts 7 (rows 1-7); complete 5; Full 2 (40.0%), Partial 1 (20.0%),
# No Match 2 (40.0%), Any Match 3 (60.0%); incomplete 2 (1 errored, 1 missing
# verdicts), of which 1 has an eligible trial.
#
# Evaluated -> Eligible over all 14 trial rows: eligible 7; usable results 11
# (10 definite verdicts + 1 declared uncertainty); without one 3 -> 63.6%.

section("Section 6: the whole app on a disposable database")

_MAIN_DB = _new_db("main")
with _REAL_CONNECT(_MAIN_DB) as _c:
    _cur = _c.cursor()
    _run_row(_cur)
    _i = _inference(_cur, "P-CM", 1, evaluated=2, eligible_matches=1)
    _trial(_cur, _i, "T1", "eligible", 1.0)
    _trial(_cur, _i, "T2", "not_eligible")
    _i = _inference(_cur, "P-UNC", 2, evaluated=2, eligible_matches=0)
    _trial(_cur, _i, "T1", "not_eligible")
    _trial(_cur, _i, "T3", "not_evaluable", reason=_DECLARED)
    _i = _inference(_cur, "P-FAILED", 3, evaluated=3, eligible_matches=1)
    _trial(_cur, _i, "T1", "eligible", 0.5)
    _trial(_cur, _i, "T2", "not_evaluable", reason=_FAILED)
    _trial(_cur, _i, "T3", "not_evaluable", reason=_FAILED)
    _inference(_cur, "P-ERR", 4, evaluated=0, error="timeout")
    _i = _inference(_cur, "P-RERUN", 5, evaluated=1, eligible_matches=0)
    _trial(_cur, _i, "T2", "not_eligible")
    _i = _inference(_cur, "P-TRIPLE", 6, evaluated=1, eligible_matches=1)
    _trial(_cur, _i, "T1", "eligible", 1.0)
    _i = _inference(_cur, "P-FLAKY", 7, evaluated=1, eligible_matches=1)
    _trial(_cur, _i, "T4", "eligible", 0.5)
    _i = _inference(_cur, "P-RERUN", 8, evaluated=1, eligible_matches=1)
    _trial(_cur, _i, "T2", "eligible", 1.0)
    for _n in (9, 10):
        _i = _inference(_cur, "P-TRIPLE", _n, evaluated=1, eligible_matches=1)
        _trial(_cur, _i, "T1", "eligible", 1.0)
    _i = _inference(_cur, "P-FLAKY", 11, evaluated=1, eligible_matches=0)
    _trial(_cur, _i, "T4", "not_evaluable", reason=_FAILED)


def _main_actions(at, capture):
    options = list(at.selectbox(key="trial_explorer_select").options)
    t3 = next(i for i, o in enumerate(options) if str(o).startswith("T3"))
    at.selectbox(key="trial_explorer_select").set_value(t3).run()
    capture("trial_t3")
    _clear_downloads()
    at.selectbox(key="patient_explorer_select").set_value("P-FAILED").run()
    capture("patient_failed")
    _DL["P-FAILED"] = _last_download("Export Patient Report")
    _clear_downloads()
    at.selectbox(key="patient_explorer_select").set_value("P-UNC").run()
    capture("patient_unc")
    _DL["P-UNC"] = _last_download("Export Patient Report")


_main, _main_connects, _main_network = render_app(_MAIN_DB,
                                                  actions=_main_actions)
_init = _main["initial"]
check("6a  the app renders with no exception", _main["exception"], [])
check("6b  ...and every scripted interaction ran", _main.get("action_error"), None)
check("6c  every connect went to the disposable database, none to production",
      connected_databases(_main_connects), [os.path.realpath(_MAIN_DB)])
check("6d  no network attempt", _main_network, [])

_ov = tab(_init, "Overview")
check("6e  Overview: rows and distinct patients",
      metric(_ov, "Total Inferences"), ("11", "7 patients"))
check("6f  Overview Full Match: 2 of 5 COMPLETE first attempts",
      metric(_ov, "✅ Full Match"), ("40.0%", "2 of 5 complete"))
check("6g  Overview Partial Match", metric(_ov, "🟡 Partial Match"),
      ("20.0%", "1 of 5 complete"))
check("6h  Overview No Match: the errored and verdict-less patients are NOT in it",
      metric(_ov, "❌ No Match"), ("40.0%", "2 of 5 complete"))
check("6i  Overview Incomplete Evaluation is its own count over first attempts",
      metric(_ov, _tiers.PATIENT_OUTCOME_INCOMPLETE), ("2", "of 7 first attempts"))
check("6j  Overview Any Match over the same complete population",
      metric(_ov, "Any Match"), ("60.0%", "3 of 5 complete"))
check("6k  Overview Evaluated → Eligible divides by USABLE RESULTS, including "
      "clinical uncertainty, and names what it excluded",
      metric(_ov, "Evaluated → Eligible"),
      ("63.6%", "3 without a usable result excluded"))
check_true("6l  the Overview caption states the population and the split of the "
           "incomplete", "(1 errored, 1 with at least one trial that has no "
           "usable verdict)" in joined(_ov)
           and "1 of the incomplete had at least one eligible trial" in joined(_ov))
check("6m  label: the cost tile names its population",
      metric(_ov, "Cost/Patient (first attempt)")[0], "$0.1000")
check_true("6n  sidebar: distinct patients and first attempts are both stated",
           "7 distinct patient(s); 7 first attempt(s)" in joined(_init["sidebar"]))

_te_initial = tab(_init, "Trial Explorer")
_te_t3 = tab(_main.get("trial_t3", _init), "Trial Explorer")
check("6o  Trial Explorer: a trial with no definite verdict has 0 patients with one",
      metric(_te_t3, "Patients With a Definite Eligibility Verdict")[0], "0")
check("6p  ...and its failed-call patient is Not Evaluated, the declared one "
      "named apart", metric(_te_t3, "Not Evaluated"),
      ("1", "+1 clinically uncertain"))
check("6q  ...and neither is a rejection", metric(_te_t3, "Not Eligible")[0], "0")
check_true("6r  the T3 caption counts rows and distinct patients",
           "2 row(s) from 2 distinct patient(s)" in joined(_te_t3))

_pe_failed = tab(_main.get("patient_failed", _init), "Patient Explorer")
_pe_frame = frame_with(_pe_failed, "Not Evaluated Reason")
_statuses = sorted(_pe_frame["Status"].tolist()) if len(_pe_frame) else []
check("6s  Patient Explorer: a failed call is 'Not Evaluated — no usable "
      "verdict', never 'Not Eligible'", _statuses,
      sorted([_tiers.TRIAL_STATUS_PARTIAL,
              _tiers.TRIAL_STATUS_NOT_EVALUATED_FAILED,
              _tiers.TRIAL_STATUS_NOT_EVALUATED_FAILED]))
check("6t  ...its score cell is blank, not 0%",
      [None if pd.isna(v) else v for v, s in
       zip(_pe_frame.get("Match Score", []), _pe_frame.get("Status", []))
       if s == _tiers.TRIAL_STATUS_NOT_EVALUATED_FAILED], [None, None])
check("6u  ...and the stored reason is shown verbatim",
      sorted(set(_pe_frame.get("Not Evaluated Reason", pd.Series()).tolist())),
      sorted({"", _FAILED}))
check("6v  ...and the patient's tier is Incomplete Evaluation",
      metric(_pe_failed, "Match Tier")[0], _tiers.MATCH_TIER_INCOMPLETE)
_pe_unc = tab(_main.get("patient_unc", _init), "Patient Explorer")
_unc_frame = frame_with(_pe_unc, "Status")
check("6w  a declared clinical uncertainty is its own status beside a real "
      "rejection", sorted(_unc_frame["Status"].tolist()) if len(_unc_frame) else [],
      sorted([_tiers.TRIAL_STATUS_REJECTED,
              _tiers.TRIAL_STATUS_NOT_EVALUABLE_UNCERTAIN]))
check("6x  ...and leaves the evaluation complete: tier No Match",
      metric(_pe_unc, "Match Tier")[0], _tiers.MATCH_TIER_NO_MATCH)

# --- THE PATIENT CSV EXPORT, DRIVEN: the tab's own CSV string, recorded ------
_main_conn = _REAL_CONNECT(_MAIN_DB)
try:
    _exp_failed = _expected_export_rows(_main_conn, "P-FAILED")
    _exp_unc = _expected_export_rows(_main_conn, "P-UNC")
finally:
    _main_conn.close()
_csv_failed = _DL.get("P-FAILED")
_csv_columns = (list(pd.read_csv(_io.StringIO(_csv_failed), dtype=str).columns)
                if _csv_failed else [])
check_true("6x-i the CSV export carries the new columns",
           {"Status", "Not Evaluated Reason", "Match Score"} <= set(_csv_columns))
check("6x-ii non-degeneracy: independent SQL finds P-FAILED's two failed calls",
      sum(1 for row in _exp_failed
          if row[1] == _tiers.TRIAL_STATUS_NOT_EVALUATED_FAILED), 2)
check("6x-iii the exported trial rows are what SQL and the ruling say: status, "
      "verbatim reason, and no score for a failed call",
      _rendered_export_rows(_csv_failed), _exp_failed)
check("6x-iv ...and a declared clinical uncertainty exports as its own status "
      "with the stored reason", _rendered_export_rows(_DL.get("P-UNC")), _exp_unc)
_summary = (pd.read_csv(_io.StringIO(_csv_failed), dtype=str,
                        keep_default_na=False) if _csv_failed else pd.DataFrame())
check("6x-v the export's patient summary row carries the Incomplete tier",
      _summary[_summary["Section"] == "Patient Summary"]["Match Tier"].tolist()
      if len(_summary) else "(absent)", [_tiers.MATCH_TIER_INCOMPLETE])
check_true("6x-vi CONTROL: the pre-fix export (every non-eligible row 'Not "
           "Eligible' with its stored 0%) disagrees with the rendered rows",
           _rendered_export_rows(_csv_failed) != sorted(
               (n, s if s in ("✅ Eligible", _tiers.TRIAL_STATUS_PARTIAL,
                              _tiers.TRIAL_STATUS_UNCONFIRMED)
                else _tiers.TRIAL_STATUS_REJECTED, "", sc if sc != "—" else "0%")
               for n, s, _r, sc in _exp_failed))

_mqb = tab(_init, "Match Quality")
_top = frame_with(_mqb, "Patients Matched")
check("6y  label: Top Matched Trials counts 'Patients Matched', DISTINCT "
      "patients (T1 is 5 eligible rows from 3 patients)",
      int(_top[_top["NCT ID"] == "T1"]["Patients Matched"].iloc[0])
      if len(_top) else "(absent)", 3)
check("6z  score distribution is over first-attempt eligible VERDICTS and says so",
      metric(_mqb, "Full Match Rate"), ("50.0%", "2 / 4 eligible trial verdicts"))
check("6z-a Match Quality Incomplete tile", metric(_mqb,
      _tiers.PATIENT_OUTCOME_INCOMPLETE), ("2", "of 7 first attempts"))

_ct = tab(_init, "Cost")
check("6z-b tokens per DEFINITE-VERDICT trial over first attempts: (4950 + 5 × 9900) / 6",
      metric(_ct, "Avg Tokens/Definite-Verdict Trial", prefix=True)[0], "9075")
check("6z-c Total Cost includes the errored row", metric(_ct, "Total Cost")[0],
      "$1.10")

_rp = tab(_init, "Reproducibility")
check("6z-d Reproducibility: re-tested = groups with a comparable trial, of all "
      "re-run groups", metric(_rp, "Patients Re-Tested"), ("2", "of 3 re-run"))
check("6z-e the flip rate is over definite-verdict pairs, and 'trial' is spelled right",
      metric(_rp, "Eligibility Decision Changed"),
      ("50.0%", "1 flips out of 2 trial evaluations"))
check_true("6z-f the exclusion caption counts the flaky rerun's failed call",
           "Removed before comparing: 1 trial observation(s) without a definite "
           "eligibility verdict"
           in joined(_rp)
           and "1 (patient, trial) pair(s) had fewer than two runs with a definite "
           "eligibility verdict"
           in joined(_rp)
           and "1 re-run patient group(s) have no comparable trial" in joined(_rp))

_rh = tab(_init, "Run Health")
_runs = frame_with(_rh, "inference rows")
check("6z-g Run Health: patients are distinct, rows are rows, errors are rows",
      (int(_runs["patients"].iloc[0]), int(_runs["inference rows"].iloc[0]),
       int(_runs["errored rows"].iloc[0])) if len(_runs) else "(absent)",
      (7, 11, 1))

_pf = tab(_init, "Performance")
check_true("6z-h Performance: the retrieval panel excludes the 4 trial rows without "
           "a definite verdict and names the failed calls and the declared uncertainty APART",
           "4 trial row(s) in the current selection have no definite eligibility "
           "verdict — 3 with no "
           "usable verdict (for example a failed call), 1 declared clinically "
           "uncertain by the model, 0 with no classifiable reason" in joined(_pf))

_RETIRED_WORD = re.compile(r"\b(un)?answer(ed|s)?\b", re.I)


def _retired_word_hits(capture, fragments):
    """Rendered labels, deltas, captions and notices that still say 'answer(ed)'."""
    hits = []
    for fragment in fragments:
        block = tab(capture, fragment)
        texts = ([lab for lab, _v, _d in block["metrics"]]
                 + [str(d) for _l, _v, d in block["metrics"] if d]
                 + [str(c) for c in block["caption"]]
                 + [str(i) for i in block["info"]])
        hits += [f"{fragment}: {t[:90]}" for t in texts
                 if _RETIRED_WORD.search(t)]
    return hits


_TOUCHED_TABS = ("Overview", "Trial Explorer", "Patient Explorer", "Cost",
                 "Performance", "Reproducibility")
check("6z-h-i no rendered label, delta, caption or notice on the six touched "
      "tabs says 'answered' -- including the not-evaluated trial and patient views",
      sorted(set(_retired_word_hits(_init, _TOUCHED_TABS)
                 + _retired_word_hits(_main.get("trial_t3", _init),
                                      ("Trial Explorer",))
                 + _retired_word_hits(_main.get("patient_failed", _init),
                                      ("Patient Explorer",)))), [])
check_true("6z-h-ii CONTROL: the scan finds the retired wording",
           _retired_word_hits({"tabs": {"Overview": {
               "metrics": [("Evaluated → Eligible", "58.8%",
                            "64 unanswered excluded")],
               "caption": [], "info": []}}}, ("Overview",)) != [])

# --- THE OUTCOME FILTER, THROUGH THE REAL SIDEBAR ------------------------------


def _pick_any_match(at, capture):
    boxes = [s for s in at.sidebar.selectbox
             if _sidebar.MATCH_FILTER_ANY in list(s.options)]
    boxes[0].set_value(_sidebar.MATCH_FILTER_ANY).run()
    capture("any")


# Shipped: rows 1, 6, 9, 10 (P-TRIPLE), 8 (P-RERUN's rerun), 7 -> 6 rows from 4
# patients, of which 3 are first attempts: P-RERUN's first attempt was No Match.
_filt, _, _filt_net = render_app(_MAIN_DB, actions=_pick_any_match)
_filt_ov = tab(_filt.get("any", _filt["initial"]), "Overview")
check("6z-i filtered to Any Match: 6 rows from 4 patients",
      metric(_filt_ov, "Total Inferences"), ("6", "4 patients"))
check("6z-j ...but only 3 first attempts -- a matched rerun is not promoted",
      metric(_filt_ov, _tiers.PATIENT_OUTCOME_INCOMPLETE),
      ("0", "of 3 first attempts"))
check("6z-k ...and no network attempt", _filt_net, [])

_side_plant = _plant(_WATCHED["sidebar.py"], [
    (_DESIGNATE_LINE + "\n", ""),
    (_MASK_LINE, _MASK_LINE + _DESIGNATE_LINE)], "sidebar_after_filter")
_app_plant = (_plant(_WATCHED["app.py"], [
    ("from oncotriage.dashboard.sidebar import render_sidebar",
     f"from {_side_plant} import render_sidebar")], "app_sidebar")
    if _side_plant else None)
if _app_plant:
    _pl, _, _ = render_app(_MAIN_DB, module=_app_plant,
                           actions=_pick_any_match)
    check("6z-l CONTROL: with the designation below the filter, P-RERUN's rerun "
          "is promoted to first attempt",
          metric(tab(_pl.get("any", _pl["initial"]), "Overview"),
                 _tiers.PATIENT_OUTCOME_INCOMPLETE),
          ("0", "of 4 first attempts"))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6b: DEMOGRAPHICS PER-GROUP CHARTS, AGAINST INDEPENDENT SQL
# ===========================================================================
#
# Eight patients over four ages, both sexes, three races, both ethnicities and
# four conditions. D4 has a failed call (incomplete) and D6 errored, so both
# must be ABSENT from every group; D3 has a later not-eligible rerun that must
# not count. Every expectation is computed from the database by
# `_complete_first_attempts` -- the ruling in SQL, not the tab's code.

section("Section 6b: demographics per-group charts")

_DEMO_DB = _new_db("demographics")
with _REAL_CONNECT(_DEMO_DB) as _c:
    _cur = _c.cursor()
    _run_row(_cur)

    def _patient(pid, n, trials, evaluated=None, error="", **demo):
        _i = _inference(_cur, pid, n, evaluated=(len(trials) if evaluated is None
                                                 else evaluated),
                        error=error, **demo)
        for nct, verdict, score, reason in trials:
            _trial(_cur, _i, nct, verdict, score, reason)

    _patient("D1", 1, [("T1", "eligible", 1.0, None)], age=34, sex="female",
             race="White", ethnicity="Not Hispanic or Latino",
             condition="Breast cancer", conditions=2, medications=3)
    _patient("D2", 2, [("T1", "not_eligible", 0.0, None)], age=38, sex="female",
             race="Black", ethnicity="Hispanic or Latino",
             condition="Breast cancer", conditions=4, medications=5)
    _patient("D3", 3, [("T2", "eligible", 0.5, None)], age=52, sex="male",
             race="White", ethnicity="Not Hispanic or Latino",
             condition="Prostate cancer", conditions=7, medications=9)
    _patient("D4", 4, [("T2", "eligible", 1.0, None),
                       ("T3", "not_evaluable", 0.0, _FAILED)], age=57, sex="male",
             race="Asian", ethnicity="Not Hispanic or Latino",
             condition="Prostate cancer", conditions=12, medications=15)
    _patient("D5", 5, [("T3", "not_evaluable", 0.0, _DECLARED),
                       ("T1", "not_eligible", 0.0, None)], age=61, sex="female",
             race="White", ethnicity="Hispanic or Latino",
             condition="Colon cancer", conditions=25, medications=25)
    _patient("D6", 6, [], evaluated=0, error="timeout", age=66, sex="male",
             race="Black", ethnicity="Not Hispanic or Latino",
             condition="Colon cancer", conditions=3, medications=1)
    _patient("D7", 7, [("T4", "eligible", 0.0, None)], age=69, sex="female",
             race="Asian", ethnicity="Hispanic or Latino",
             condition="Lung cancer", conditions=8, medications=13)
    _patient("D8", 8, [("T1", "eligible", 0.5, None)], age=45, sex="male",
             race="White", ethnicity="Not Hispanic or Latino",
             condition="Breast cancer", conditions=1, medications=0)
    _patient("D3", 9, [("T2", "not_eligible", 0.0, None)], age=52, sex="male",
             race="White", ethnicity="Not Hispanic or Latino",
             condition="Prostate cancer", conditions=7, medications=9)

_demo, _demo_connects, _demo_net = render_app(_DEMO_DB)
check("6b-a the demographics database renders with no exception, offline, "
      "touching only itself",
      (_demo["exception"], _demo_net, connected_databases(_demo_connects)),
      ([], [], [os.path.realpath(_DEMO_DB)]))
_demo_conn = _REAL_CONNECT(_DEMO_DB)
try:
    _demo_rows = _complete_first_attempts(_demo_conn)
    _demo_all = [{"age": a, "sex": s, "race": r, "ethnicity": e, "condition": c,
                  "conditions": nc, "medications": nm, "match": bool(m)}
                 for a, s, r, e, c, nc, nm, m in _demo_conn.execute(
                     "SELECT i.age, i.sex, i.race, i.ethnicity, i.primary_condition, "
                     "i.condition_count, i.medication_count, EXISTS (SELECT 1 FROM "
                     "trial_matches t WHERE t.inference_id = i.id AND t.eligible = "
                     "'eligible') FROM inferences i")]
finally:
    _demo_conn.close()
check("6b-b non-degeneracy: independent SQL keeps 6 complete first attempts of "
      "9 rows -- the failed call, the error and the rerun are out",
      (len(_demo_rows), len(_demo_all)), (6, 9))
_demo_charts = tab(_demo["initial"], "Demographics")["plotly"]
for _title in _GROUP_KEYS:
    check(f"6b-c {_title}: every bar's rate and n equal independent SQL over "
          f"complete first attempts",
          _rendered_group_chart(_demo_charts, _title),
          _expected_group_chart(_demo_rows, _title))
check_true("6b-d CONTROL: the same chart computed over EVERY ROW -- the pre-fix "
           "population -- disagrees with the render",
           _rendered_group_chart(_demo_charts, "Match Rate by Sex")
           != _expected_group_chart(_demo_all, "Match Rate by Sex"))
_by_condition = {}
for _row in _demo_rows:
    _n, _m = _by_condition.get(_row["condition"], (0, 0))
    _by_condition[_row["condition"]] = (_n + 1, _m + int(_row["match"]))
_top = _demo_charts.get("Top Conditions (by patient count)")
check("6b-e Top Conditions: each condition's patient count and match rate equal SQL",
      ({str(l): (int(v), t) for l, v, t in zip(_top["y"], _top["x"], _top["text"])}
       if _top else "(chart absent)"),
      {c[:40]: (n, f"{m / n * 100:.0f}% match") for c, (n, m) in _by_condition.items()})
_bw = _demo_charts.get("Match Rate by Condition (best & worst)")
check("6b-f best & worst: only conditions with 2+ complete first attempts, at "
      "their SQL rate",
      ({str(l): (round(float(v), 4), t)
        for l, v, t in zip(_bw["y"], _bw["x"], _bw["text"])}
       if _bw else "(chart absent)"),
      {c[:40]: (round(m / n * 100, 4), f"n={n} ({m / n * 100:.0f}%)")
       for c, (n, m) in _by_condition.items() if n >= 2})


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7: ZERO DENOMINATORS
# ===========================================================================

section("Section 7: zero denominators")

_ZERO_DB = _new_db("all_failed")
with _REAL_CONNECT(_ZERO_DB) as _c:
    _cur = _c.cursor()
    _run_row(_cur)
    for _n, _pid in enumerate(("Z-1", "Z-2"), start=1):
        _i = _inference(_cur, _pid, _n, evaluated=2, eligible_matches=0)
        _trial(_cur, _i, "T1", "not_evaluable", reason=_FAILED)
        _trial(_cur, _i, "T2", "not_evaluable", reason=_FAILED)
    _inference(_cur, "Z-3", 3, evaluated=0, error="timeout")

_zero, _zero_connects, _zero_net = render_app(_ZERO_DB)
_zi = _zero["initial"]
check("7a  every evaluation incomplete: the app renders with no exception",
      _zero["exception"], [])
check("7b  no network attempt, no connect outside the disposable database",
      (_zero_net, connected_databases(_zero_connects)),
      ([], [os.path.realpath(_ZERO_DB)]))
_zov = tab(_zi, "Overview")
check("7c  a tier with no complete evaluation shows '—', never 0%",
      metric(_zov, "✅ Full Match"), ("—", "0 of 0 complete"))
check("7d  Any Match likewise", metric(_zov, "Any Match"), ("—", "0 of 0 complete"))
check("7e  every first attempt is counted as incomplete",
      metric(_zov, _tiers.PATIENT_OUTCOME_INCOMPLETE), ("3", "of 3 first attempts"))
check("7f  a retention with no usable result has no denominator: '—', and the "
      "excluded count is still shown", metric(_zov, "Evaluated → Eligible"),
      ("—", "4 without a usable result excluded"))
check("7g  tokens per definite-verdict trial with no such trial is N/A, not a division",
      metric(tab(_zi, "Cost"), "Avg Tokens/Definite-Verdict Trial", prefix=True)[0], "N/A")
check_true("7h  the outcome pie declines instead of drawing an empty chart",
           any("No complete first-attempt evaluation to distribute" in str(i)
               for i in tab(_zi, "Match Quality")["info"]))
check("7i  Demographics Any Match '—'", metric(tab(_zi, "Demographics"),
                                               "Any Match")[0], "—")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8: A TAB RENDERED OUTSIDE main() CANNOT TIER AN INCOMPLETE ROW
# ===========================================================================

section("Section 8: ensure_evaluated in the tier-reading tabs")

_inf, _tm = _frames(_MAIN_DB)
_unannotated = _tiers.enrich_match_tiers(_inf, _tm)
_direct, _direct_exc, _direct_net = render_tab(
    "oncotriage.dashboard.tabs.overview", "render_overview_tab", _unannotated,
    _MAIN_DB)
check("8a  a direct Overview render of an UN-annotated frame still counts the "
      "incomplete evaluations", metric(_direct, _tiers.PATIENT_OUTCOME_INCOMPLETE),
      ("2", "of 7 first attempts"))
check("8b  ...renders cleanly and offline", (_direct_exc, _direct_net), ([], []))

_ENSURE_LINE = "    df = ensure_evaluated(df, load_trial_matches_data())\n"
_ov_plant = _plant(_WATCHED["overview.py"], [(_ENSURE_LINE, "")],
                   "overview_no_ensure")
if _ov_plant:
    _no_ensure, _, _ = render_tab(_ov_plant, "render_overview_tab",
                                  _unannotated, _MAIN_DB)
    check("8c  CONTROL: without it the errored and verdict-less rows are tiered "
          "clinically -- 0 incomplete, Full Match 2 of 7",
          (metric(_no_ensure, _tiers.PATIENT_OUTCOME_INCOMPLETE)[0],
           metric(_no_ensure, "✅ Full Match")),
          ("0", ("28.6%", "2 of 7 complete")))


def _calls_ensure(source_text, fn_name):
    tree = ast.parse(source_text)
    fn = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
               and n.name == fn_name), None)
    return fn is not None and any(
        isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
        and isinstance(n.value.func, ast.Name)
        and n.value.func.id == "ensure_evaluated"
        and any(isinstance(t, ast.Name) and t.id == "df" for t in n.targets)
        for n in ast.walk(fn))


for _file, _fn in (("overview.py", "render_overview_tab"),
                   ("match_quality.py", "render_match_quality_tab"),
                   ("demographics.py", "render_patient_demographics_tab"),
                   ("patient_explorer.py", "render_patient_explorer_tab")):
    check(f"8d  {_file}: {_fn} rebinds df through ensure_evaluated",
          _calls_ensure(Path(_WATCHED[_file]).read_text(encoding="utf-8"), _fn),
          True)
if _ov_plant:
    check("8e  CONTROL: the same scan on the planted Overview copy fails",
          _calls_ensure(Path(os.path.join(_PLANT_DIR, _ov_plant + ".py"))
                        .read_text(encoding="utf-8"), "render_overview_tab"),
          False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 9: WATCHED FILES
# ===========================================================================

section("Section 9: nothing in the repository was written")

check("9a  every watched package file is byte-identical",
      {k: digest_file(v) for k, v in _WATCHED.items()}, _DIGESTS_BEFORE)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 10: THE SMOKE DATABASE, READ-ONLY, AT ITS RECORDED LOCATION
# ===========================================================================
#
# EVERY EXPECTATION BELOW IS COMPUTED HERE, FROM THE DATABASE, BY SQL THE
# DASHBOARD DOES NOT RUN -- never copied out of a render. The audit's own
# figures are REFERENCE: where the dashboard's corrected population differs from
# the audit's (F24: first attempts rather than completed rows), both are computed
# and the difference is asserted, so the population is what the check is about.

section("Section 10: the smoke database, read-only")

if not os.path.isfile(_SMOKE_DB) or _SMOKE_DIGEST_BEFORE in ("absent",) \
        or _SMOKE_DIGEST_BEFORE.startswith("unreadable"):
    skip("10  smoke-database figures",
         f"no readable database at the recorded location {_SMOKE_DB}")
else:
    _ro = _REAL_CONNECT("file:" + urllib.parse.quote(_SMOKE_DB)
                        + "?mode=ro&immutable=1", uri=True)
    _sql_error = None
    try:
        q = lambda sql: _ro.execute(sql).fetchall()        # noqa: E731
        _runs_seen = q("SELECT COUNT(DISTINCT run_id), SUM(run_id IS NULL) "
                       "FROM inferences")[0]
        _one_campaign = (_runs_seen[0] == 1 and not _runs_seen[1])
        _fa = "SELECT MIN(id) FROM inferences GROUP BY patient_id"
        _n_rows, _n_patients = q("SELECT COUNT(*), COUNT(DISTINCT patient_id) "
                                 "FROM inferences")[0]
        _declared = tuple(_queries.NOT_EVALUABLE_REASONS_DECLARED)
        _placeholders = ",".join("?" * len(_declared))
        _usable_result_sql = (
            "(eligible IN ('eligible','not_eligible') OR (eligible = "
            f"'not_evaluable' AND not_evaluable_reason IN ({_placeholders})))")
        _ret = _ro.execute(
            f"SELECT SUM(eligible='eligible'), SUM({_usable_result_sql}), "
            f"COUNT(*) FROM trial_matches", _declared).fetchone()
        _elig, _usable = int(_ret[0] or 0), int(_ret[1] or 0)
        _without_usable = int(_ret[2]) - _usable
        _incomplete_fa = 0
        for (inf_id, err, evaluated) in q(
                f"SELECT id, error, candidates_evaluated FROM inferences "
                f"WHERE id IN ({_fa})"):
            trows = _ro.execute(
                "SELECT eligible, not_evaluable_reason FROM trial_matches "
                "WHERE inference_id = ?", (inf_id,)).fetchall()
            bad = any(not (e in ("eligible", "not_eligible")
                           or (e == "not_evaluable" and r in _declared))
                      for e, r in trows)
            short = evaluated is not None and len(trows) < int(evaluated)
            _incomplete_fa += int(bool(err) or bad or short)
        _fa_cost = q(f"SELECT AVG(estimated_cost_usd), "
                     f"AVG(estimated_cost_cached_usd) FROM inferences "
                     f"WHERE id IN ({_fa})")[0]
        _total_cost = q("SELECT SUM(estimated_cost_usd) FROM inferences")[0][0]
        _definite_sql = ("WITH a AS (SELECT inference_id, SUM(eligible IN "
                         "('eligible','not_eligible')) n FROM trial_matches "
                         "GROUP BY inference_id) SELECT AVG((i.llm_classifier_"
                         "input_tokens + i.llm_classifier_output_tokens)*1.0/a.n)"
                         " FROM inferences i JOIN a ON a.inference_id = i.id "
                         "WHERE a.n > 0 AND ")
        _tpt_fa = q(_definite_sql + f"i.id IN ({_fa})")[0][0]
        _tpt_done = q(_definite_sql + "COALESCE(i.error,'') = ''")[0][0]
        _old_retention = q("SELECT SUM(eligible_matches)*100.0/"
                           "SUM(candidates_evaluated) FROM inferences")[0][0]
        _no_usable = tuple(_queries.NOT_EVALUABLE_REASONS_CONSTRUCTED) + \
            tuple(_queries.NOT_EVALUABLE_REASONS_CORRECTED)
        _perf_counts = _ro.execute(
            "SELECT SUM(eligible NOT IN ('eligible','not_eligible') OR eligible IS "
            "NULL), SUM(eligible = 'not_evaluable' AND not_evaluable_reason IN "
            f"({','.join('?' * len(_no_usable))})), SUM(eligible = "
            f"'not_evaluable' AND not_evaluable_reason IN ({_placeholders})) "
            "FROM trial_matches", _no_usable + _declared).fetchone()
        _probe_nct = q("SELECT nct_id FROM trial_matches GROUP BY nct_id HAVING "
                       "SUM(eligible IN ('eligible','not_eligible')) = 0 "
                       "ORDER BY nct_id LIMIT 1")
        _probe_nct = _probe_nct[0][0] if _probe_nct else None
        _repro_without_definite = q(
            "SELECT COUNT(*) FROM trial_matches WHERE eligible NOT IN "
            "('eligible','not_eligible') AND inference_id IN (SELECT id FROM "
            "inferences WHERE (patient_id, qdrant_collection, patient_data_hash) "
            "IN (SELECT patient_id, qdrant_collection, patient_data_hash FROM "
            "inferences GROUP BY 1,2,3 HAVING COUNT(*) >= 2))")[0][0]
    except sqlite3.Error as exc:
        _sql_error = f"{type(exc).__name__}: {exc}"
    finally:
        _ro.close()
    check("10- the independent SQL ran (a query error is a recorded failure, "
          "not an abort)", _sql_error, None)

if os.path.isfile(_SMOKE_DB) and _SMOKE_DIGEST_BEFORE != "absent" \
        and not _SMOKE_DIGEST_BEFORE.startswith("unreadable") and _sql_error is None:
    def _smoke_actions(at, capture):
        if _probe_nct is None:
            return
        options = list(at.selectbox(key="trial_explorer_select").options)
        idx = next(i for i, o in enumerate(options)
                   if str(o).startswith(_probe_nct))
        at.selectbox(key="trial_explorer_select").set_value(idx).run()
        capture("probe_trial")

    _smoke, _smoke_connects, _smoke_net = render_app(
        _SMOKE_DB, actions=_smoke_actions, connect=_smoke_connect)
    _si = _smoke["initial"]
    _smoke_csv = _last_download("Export Patient Report")
    check("10a the smoke render raises nothing", _smoke["exception"], [])
    check("10b every connect was the read-only immutable smoke database; none "
          "refused", sorted(set(_smoke_connects)), ["smoke-ro"])
    check("10c no network attempt", _smoke_net, [])
    check("10d the smoke database is byte-identical after the render",
          digest_file(_SMOKE_DB), _SMOKE_DIGEST_BEFORE)

    _sov = tab(_si, "Overview")
    check("10e F1: rows and DISTINCT patients",
          metric(_sov, "Total Inferences"),
          (f"{_n_rows:,}", f"{_n_patients:,} patients"))
    check("10f F1: the incomplete first attempts are counted, by the independent "
          "rule", metric(_sov, _tiers.PATIENT_OUTCOME_INCOMPLETE),
          (f"{_incomplete_fa:,}", f"of {_n_patients:,} first attempts")
          if _one_campaign else metric(_sov, _tiers.PATIENT_OUTCOME_INCOMPLETE))
    if _one_campaign and _incomplete_fa == _n_patients:
        check("10g F1: with no complete evaluation every tier is '—'",
              [metric(_sov, lab)[0] for lab in
               ("✅ Full Match", "🟡 Partial Match", "🔶 Unconfirmed",
                "❌ No Match", "Any Match")], ["—"] * 5)
    check("10h F2: Cost/Patient is the first-attempt mean, with the cached-rate "
          "estimate beside it", metric(_sov, "Cost/Patient (first attempt)"),
          (f"${_fa_cost[0]:.4f}", f"cached-rate est. ${_fa_cost[1]:.4f}"))
    _ret_expected = (f"{_elig / _usable * 100:.1f}%" if _usable else "—",
                     f"{_without_usable:,} without a usable result excluded"
                     if _without_usable
                     else None)
    check("10i F5: Evaluated → Eligible is eligible / usable results",
          metric(_sov, "Evaluated → Eligible"), _ret_expected)
    check_true("10j F5 CONTROL: the pre-fix formula gives a different figure on "
               "this database, so 10i is discriminating",
               _old_retention is not None
               and f"{_old_retention:.1f}%" != _ret_expected[0])
    _fields = metric(_sov, "Fields 100% Complete")[0]
    if isinstance(_fields, str) and "/" in _fields and \
            _fields.split("/")[0] == _fields.split("/")[1]:
        check("10k F6: every field complete -> the weakest field is 'None'",
              metric(_sov, "Weakest Field"), ("None", "every field 100% complete"))

    _sct = tab(_si, "Cost")
    check("10l F21: Total Cost is every row, errored included",
          metric(_sct, "Total Cost")[0], f"${_total_cost:.2f}")
    check("10m F24: tokens per definite-verdict trial is the FIRST-ATTEMPT population",
          metric(_sct, "Avg Tokens/Definite-Verdict Trial", prefix=True)[0],
          f"{_tpt_fa:.0f}")
    check_true("10n F24: the audit's completed-rows population differs on this "
               "database -- the disagreement is population, not arithmetic",
               f"{_tpt_done:.0f}" != f"{_tpt_fa:.0f}")

    _smq = tab(_si, "Match Quality")
    check_true("10o F7: the score distribution names its unit",
               str(metric(_smq, "Full Match Rate")[1]).endswith(
                   "eligible trial verdicts"))
    check_true("10p F10: 'Patients Matched' replaces 'Match Count'",
               len(frame_with(_smq, "Patients Matched")) > 0
               and len(frame_with(_smq, "Match Count")) == 0)

    if _probe_nct is not None:
        _ste = tab(_smoke.get("probe_trial", _si), "Trial Explorer")
        check("10q F13: a trial with no definite-verdict row shows 0 patients "
              "with one", metric(_ste, "Patients With a Definite Eligibility "
                                        "Verdict")[0], "0")
        check("10r F13: ...and is not a rejection", metric(_ste, "Not Eligible")[0],
              "0")

    _srp = tab(_si, "Reproducibility")
    check_true("10s F27: the flip delta says 'trial evaluations'",
               "trial evaluations" in str(metric(
                   _srp, "Eligibility Decision Changed")[1]))
    check_true("10t F26: the exclusion caption counts the independent SQL's "
               "not-evaluated observations",
               f"Removed before comparing: {_repro_without_definite} trial "
               f"observation(s) without a definite"
               in joined(_srp))

    _srh = tab(_si, "Run Health")
    _srun = frame_with(_srh, "inference rows")
    check("10u F33: the run table's patients are distinct and rows are rows",
          (int(_srun["patients"].sum()), int(_srun["inference rows"].sum()))
          if len(_srun) else "(absent)", (_n_patients, _n_rows))
    check_true("10v F34: the degradation figure is labelled as a mixed-units total",
               metric(_srh, "Degradation total (mixed units)")[0] != "(absent)")
    _pn, _pf_fail, _pf_unc = (int(v or 0) for v in _perf_counts)
    check_true("10x F20: the retrieval panel excludes the trial rows without a definite verdict "
               "and splits them by stored reason, from the independent SQL",
               f"{_pn} trial row(s) in the current selection have no definite "
               f"eligibility verdict — "
               f"{_pf_fail} with no usable verdict (for example a failed call), "
               f"{_pf_unc} declared clinically uncertain"
               in joined(tab(_si, "Performance")))
    _smoke_conn = _REAL_CONNECT("file:" + urllib.parse.quote(_SMOKE_DB)
                                + "?mode=ro&immutable=1", uri=True)
    try:
        _default_patient = _smoke_conn.execute(
            "SELECT MIN(patient_id) FROM inferences").fetchone()[0]
        _smoke_export = _expected_export_rows(_smoke_conn, _default_patient)
        _smoke_complete = _complete_first_attempts(_smoke_conn)
    finally:
        _smoke_conn.close()
    check("10y CSV export on the smoke database: the default patient's trial rows "
          "equal independent SQL", _rendered_export_rows(_smoke_csv), _smoke_export)
    check_true("10y-i non-degeneracy: that patient has trial rows to export",
               len(_smoke_export) > 0)
    _smoke_sex = tab(_si, "Demographics")["plotly"].get("Match Rate by Sex")
    check("10z demographics on the smoke database: the sex chart's n sums to the "
          "complete first attempts independent SQL finds",
          sum(int(t.split("=")[1]) for t in _smoke_sex["text"])
          if _smoke_sex else "(chart absent)", len(_smoke_complete))
    check_true("10w F19: Performance says 'Rows with a Usable Score'",
               metric(tab(_si, "Performance"),
                      "Rows with a Usable Score")[0] != "(absent)")


#------------------------------------------------------------------------------


# ===========================================================================
# TEARDOWN AND SUMMARY
# ===========================================================================

section("Teardown")

if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)
else:
    _paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
check("T1  paths._RESOLVED is restored",
      _paths._RESOLVED.get("inferences_path"), _SAVED_RESOLVED)
check("T2  the database at the recorded location is unchanged by the whole file",
      digest_file(_SMOKE_DB), _SMOKE_DIGEST_BEFORE)
if _PLANT_DIR in sys.path:
    sys.path.remove(_PLANT_DIR)
shutil.rmtree(_TMP, ignore_errors=True)
check("T3  the scratch directory is gone", os.path.exists(_TMP), False)

print()
print("=" * 78)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
print(f"Skipped: {_RESULTS['skipped']}   (a skip is NOT a pass and is not "
      f"counted as one)")
if _FAILURES:
    print("FAILURES:")
    for label in _FAILURES:
        print(f"  - {label}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Sep 13 2026

@author: ramyalsaffar
"""
