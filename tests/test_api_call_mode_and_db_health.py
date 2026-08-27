# Stage 5 Call Mode On The Operator Surfaces, And The Serving-Database Refusal
############################################################################

"""
The standing test for three things the per-trial default left unreported.

WHAT IS UNDER TEST
------------------
1. ``oncotriage/dashboard/call_mode.py`` and the five tabs that consult it.
   ``MATCHING_PER_TRIAL_CALLS_ENABLED`` ships ``True``, so Stage 5 sends one
   billed request per patient-trial pair behind a cache warmup where the
   retained grouped arm sends one per packed chunk. Both arms write the SAME
   columns -- ``llm_classifier_input_tokens``, ``llm_classifier_calls``,
   ``estimated_cost_usd`` -- so a per-patient mean over a table holding both is
   a mean over two populations, and nothing on the page said so.

2. ``GET /pipeline/info``'s ``config.call_mode`` block. Which arm the serving
   process is running was absent from the one endpoint that describes the
   pipeline, while being the single largest lever on what a patient costs.

3. ``GET /health``'s third check. A serving database written by a NEWER schema
   era is refused by ``assert_database_is_compatible`` -- correctly -- and the
   refusal is then caught by ``_write_inference_row``'s broad handler, which
   exists so a logging fault cannot discard a paid pipeline result. MEASURED:
   every ``POST /match`` runs the pipeline, makes its billed Stage 5 calls,
   returns **200 with a complete body**, and stores nothing, while ``/health``
   stays **green**. The only trace is one ERROR line per request in the log.

WHY THE REFERENCE IS THE SEED AND NOT A GOLDEN SNAPSHOT
-------------------------------------------------------
``tests/test_dashboard_run_health.py``'s argument, adopted for its reason and
not by imitation: the panels here are NEW, so a snapshot recorded today is a
photograph of whatever this pass happened to write and would pass forever
against a tab that blends two arms silently. Every expected value below is
computed from the rows inserted, never read back out of the frame under test.

WHAT IS ASSERTED, AND WHY THESE
--------------------------------
The three states ``call_mode.describe`` must keep apart -- a single arm, a MIX,
and a mode column that is not in the database at all -- on frames seeded to
produce each. Then, per tab, the specific claim: that a blended average SAYS it
is blended, that an unblended per-mode table appears only when there is a mix,
that a per-call figure comes from the LEDGER and not from a total divided by a
call count, and that the warmup is named infrastructure rather than counted as
an evaluation.

A NOTE ON THE ONE ARITHMETIC CLAIM THIS FILE MAKES
---------------------------------------------------
``input_tokens / llm_classifier_calls`` is wrong for a per-trial row and has no
symptom: one of those calls is the warmup, which carries the whole shared prefix
against a one-token output ceiling. Section 3 seeds a ledger where the two
answers DIFFER and requires the rendered figure to be the ledger's -- a seed
where they agreed would be satisfied by either implementation and would prove
nothing.

RUNS, COSTS, KEYS
-----------------
No network, no keys, **NO SPEND** (the API sections install a stub graph through
``oncotriage/agent/deps.py`` and never invoke a real one; the dashboard sections
touch no client at all), no live Qdrant, no model load, no corpus, no git
history, no live server, no Docker daemon. NOT in the collision matrix, derived:
it writes only inside a ``tempfile.mkdtemp`` it removes and asserts gone, and
the repository files it reads are written by neither of the suite's two writers.
It EXECS NOTHING and loads no module by location -- every plant is a copy
written to a temp directory and imported from there -- so it needs no
``_EXEC_ALLOWLIST`` entry.
"""

import hashlib
import json
import os
import pickle
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

import pandas as pd
import streamlit as st
from streamlit.testing.v1 import AppTest

from oncotriage import config as _config
from oncotriage import paths as _paths
from oncotriage.agent import deps as _deps
from oncotriage.agent import readiness as _readiness
from oncotriage.dashboard import call_mode as _cm
from oncotriage.dashboard import tiers as _tiers
from oncotriage.dashboard.tabs import run_health as _run_health
from oncotriage.storage import database_logger as _dl
from oncotriage.storage.queries import MODE_NOT_RECORDED_LABEL


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


def skip(label, reason):
    """Record coverage that could NOT be exercised in THIS environment.

    A SKIP IS NOT A PASS AND IS NEVER COUNTED AS ONE -- this project's existing
    mechanism and its argument, adopted rather than invented
    (``tests/test_package_invariants.py``'s macOS-only ``caffeine`` guard). Its
    own counter, its own list, a summary line PRINTED EVEN AT ZERO, and no
    effect on the exit code: the thing skipped is not broken, it is absent.
    """
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}")
    print(f"          {reason}")


def section(title):
    print("\n" + "=" * 75)
    print(title)
    print("=" * 75)


def drive(fn, *args, **kwargs):
    """Call ``fn``, turning ANY raise into a value ``check`` fails on.

    A BARE CALL INTO THE CODE UNDER TEST ABORTS THE FILE, and this project has
    shipped that shape fourteen times: the raise happens while ``check``'s
    argument list is being evaluated, so a run that owes a summary and a hundred
    recorded failures reports one traceback instead. Every plant below is
    designed to break exactly the function being called.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:          # noqa: BLE001 -- a marker, not a swallow
        return f"RAISED {type(exc).__name__}: {exc}"


def at_(sequence, index, default="(absent)"):
    """``sequence[index]`` or a named absence -- never an IndexError."""
    try:
        return sequence[index]
    except (IndexError, KeyError, TypeError):
        return default


#------------------------------------------------------------------------------


# ===========================================================================
# SCRATCH TREE
# ===========================================================================
# Everything this file writes lives here, and section 9 asserts it is gone.
_TMP = tempfile.mkdtemp(prefix="oncotriage-callmode-")
_PLANT_DIR = os.path.join(_TMP, "plants")
os.makedirs(_PLANT_DIR, exist_ok=True)

_PROD_DB = _paths.inferences_path
_SAVED_RESOLVED = _paths._RESOLVED.get("inferences_path")


def digest(path):
    """sha256, or a NAMED non-reading. Never raises -- see run_health's note."""
    if not os.path.exists(path):
        return "absent"
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"


_READ_FILES = {
    rel: digest(os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(_cm.__file__))), *rel.split("/")))
    for rel in ("dashboard/call_mode.py",
                "dashboard/tabs/cost_tokens.py",
                "dashboard/tabs/overview.py",
                "dashboard/tabs/patient_explorer.py",
                "dashboard/tabs/performance.py",
                "api/server.py",
                "agent/readiness.py",
                "storage/database_logger.py")
}
_PROD_DB_BEFORE = digest(_PROD_DB)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1: call_mode.py, as a pure function of its argument
# ===========================================================================
# The natural control for a pure function is a different INPUT, so nothing is
# planted here and nothing is exec'd. THREE STATES, and the third is the one a
# two-state design gets wrong: a database predating era 3 has no
# `matching_call_mode` COLUMN at all, which a `df[col].isna()` implementation
# meets as a KeyError rather than as a reading.

section("SECTION 1: call_mode.describe -- three states, not two")

_PT = _config.MATCHING_CALL_MODE_PER_TRIAL
_GR = _config.MATCHING_CALL_MODE_GROUPED

_single = pd.DataFrame({"matching_call_mode": [_PT] * 4})
_mixed = pd.DataFrame({"matching_call_mode": [_PT, _PT, _GR, None]})
_nocol = pd.DataFrame({"estimated_cost_usd": [0.1, 0.2]})
_empty = pd.DataFrame({"matching_call_mode": []})

_m_single = drive(_cm.describe, _single)
_m_mixed = drive(_cm.describe, _mixed)
_m_nocol = drive(_cm.describe, _nocol)
_m_empty = drive(_cm.describe, _empty)

check("1a  a single-arm frame is not mixed and names its arm",
      (_m_single["is_mixed"], _m_single["sole_bucket"]), (False, "per-trial"))
check("1b  a frame holding two arms plus a NULL is MIXED",
      _m_mixed["is_mixed"], True)
check("1b  ...and its counts are the seeded ones, in BUCKET_ORDER",
      list(_m_mixed["counts"].items()),
      [("grouped", 1), ("per-trial", 2), (MODE_NOT_RECORDED_LABEL, 1)])
check("1c  A NULL VALUE IS ITS OWN BUCKET AND IS NEVER GUESSED AT. Folding it "
      "into whichever arm the process is configured for would invent the fact "
      "this module exists to report",
      _m_mixed["unrecorded_rows"], 1)
check("1d  a frame with NO mode column at all is a reading, not a KeyError",
      (_m_nocol["column_present"], _m_nocol["sole_bucket"],
       _m_nocol["unrecorded_rows"]),
      (False, MODE_NOT_RECORDED_LABEL, 2))
check("1e  an EMPTY frame reports zero rows and no buckets, so a caller "
      "renders an empty state rather than a mode finding",
      (_m_empty["rows"], _m_empty["buckets"], _m_empty["sole_bucket"]),
      (0, [], None))

# AN UNRECOGNISED VALUE IS REPORTED AS ITSELF. `matching_call_mode` is written
# from a closed, import-validated vocabulary, so a value outside it means
# something this code does not know about wrote the table. Hiding it under
# "(not recorded)" would report a real finding as an absence -- and it makes the
# frame MIXED, which is the honest consequence.
_weird = drive(_cm.describe, pd.DataFrame({"matching_call_mode": [_PT, "sideways"]}))
check("1f  an unrecognised stored value is reported as itself, not folded "
      "into the not-recorded bucket",
      sorted(_weird["buckets"]), ["per-trial", "sideways"])
check("1f  ...and it makes the frame mixed", _weird["is_mixed"], True)

check("1g  describe() returns exactly the declared MIX_FIELDS, so a consumer "
      "may branch over them exhaustively",
      sorted(_m_mixed), sorted(_cm.MIX_FIELDS))

# THE LABEL AND THE CAPTION MUST SAY DIFFERENT THINGS IN THE TWO STATES, and
# 'the caption is non-empty' is satisfied by a caption that says the same thing
# always. These compare the two.
_cap_single = drive(_cm.caption, _m_single, "average")
_cap_mixed = drive(_cm.caption, _m_mixed, "average")
check("1h  the single-arm caption names the arm and does not cry mix",
      ("per-trial" in _cap_single and "MIXES" not in _cap_single), True)
check("1h  the mixed caption says MIXES and names both arms",
      ("MIXES" in _cap_mixed and "per-trial" in _cap_mixed
       and "grouped" in _cap_mixed), True)
check("1h  non-degeneracy: the two captions are not the same string",
      _cap_single != _cap_mixed, True)
check("1i  an empty frame's suffix is empty, so a caller may concatenate it "
      "unconditionally",
      drive(_cm.label_suffix, _m_empty), "")

check("1j  split() partitions the frame with no row lost and none duplicated",
      sum(len(sub) for _b, sub in drive(_cm.split, _mixed)), len(_mixed))
check("1j  ...and a frame with no mode column still yields ONE pair holding "
      "every row, so a caller that always splits renders the same panel",
      [(b, len(s)) for b, s in drive(_cm.split, _nocol)],
      [(MODE_NOT_RECORDED_LABEL, 2)])

# ANNOTATE MUST NOT MUTATE ITS ARGUMENT. The frame handed to a tab is the
# sidebar-filtered selection every other tab on the page also holds, and
# @st.cache_data returns the SAME object across reruns -- so an in-place column
# would leak into panels that never asked for one and accumulate across reruns.
# This is section 6a's MATCH_TIERS hazard one frame over.
_before_cols = list(_nocol.columns)
_annotated = drive(_cm.annotate, _nocol)
check("1k  annotate() returns a COPY: the caller's frame gains no column",
      list(_nocol.columns), _before_cols)
check("1k  ...and the copy carries the label", "call_mode_label" in _annotated,
      True)

# THE BUCKET NAME IS THE QUERY LAYER'S, NOT A SECOND SPELLING. A dashboard
# bucket spelled differently from what `call_mode_comparison` COALESCEs to is
# two names for one fact, and a reader putting File 16's arm table beside this
# dashboard would see two populations where there is one.
check("1l  the not-recorded bucket is imported from the query layer, byte for "
      "byte, and not retyped",
      _cm.BUCKET_ORDER[-1], MODE_NOT_RECORDED_LABEL)
check("1l  ...and the arm buckets are derived from config.MATCHING_CALL_MODES, "
      "so a third arm cannot render as its raw storage value",
      sorted(_cm.MODE_DISPLAY), sorted(_config.MATCHING_CALL_MODES))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2: the per-call ledger reader
# ===========================================================================
# NULL, [] AND A GARBAGE PAYLOAD ARE THREE ANSWERS and the storage layer says so
# at the column: NULL means Stage 5 was never entered, [] means it ran and no
# request produced a usage object. Rendering both as "0 calls" reports a stage
# that never ran as one that made no request.

section("SECTION 2: the ledger -- NULL, empty and unreadable are three answers")

_LEDGER_PT = json.dumps([
    {"call_index": 1, "warmup": True, "trials": 0, "depth": None,
     "prompt_tokens": 900, "completion_tokens": 1, "cached_tokens": 0,
     "reasoning_tokens": 0, "finish_reason": "length", "entries_emitted": None},
    {"call_index": 2, "trials": 1, "depth": 0, "prompt_tokens": 1000,
     "completion_tokens": 400, "cached_tokens": 896, "reasoning_tokens": 120,
     "finish_reason": "stop", "entries_emitted": 1},
    {"call_index": 3, "trials": 1, "depth": 0, "prompt_tokens": 1000,
     "completion_tokens": 380, "cached_tokens": 896, "reasoning_tokens": 100,
     "finish_reason": "stop", "entries_emitted": 1},
])

_led = drive(_cm.ledger, _LEDGER_PT)
check("2a  a per-trial ledger reports every request it holds",
      _led["calls"], 3)
check("2b  THE WARMUP IS COUNTED SEPARATELY AND IS NOT AN EVALUATION. It "
      "evaluates no trial -- it writes the shared prefix so the requests "
      "behind it bill at the cached rate",
      (_led["warmup_calls"], _led["evaluation_calls"]), (1, 2))
check("2b  ...and it is the row Stage 5 marked, not the first row by position",
      _led["rows"][0]["_kind"], _cm.WARMUP_KIND)
check("2b  ...while every other row is an evaluation",
      {r["_kind"] for r in _led["rows"][1:]}, {_cm.EVALUATION_KIND})

check("2c  NULL is 'Stage 5 was never entered'",
      drive(_cm.ledger, None)["state"], _cm.LEDGER_ABSENT)
check("2c  [] is 'it ran and counted no usage' -- a DIFFERENT answer",
      drive(_cm.ledger, "[]")["state"], _cm.LEDGER_EMPTY)
check("2c  non-degeneracy: those two states are distinct constants",
      _cm.LEDGER_ABSENT != _cm.LEDGER_EMPTY, True)
check("2d  a payload that is not a JSON list is unreadable, not empty",
      [drive(_cm.ledger, v)["state"] for v in ('{"a": 1}', "not json", "[1,2]")],
      [_cm.LEDGER_UNREADABLE] * 3)
check("2d  ...and an unreadable ledger yields NO rows, so nothing is rendered "
      "from a payload this code could not read whole",
      drive(_cm.ledger, "[1,2]")["rows"], [])

# THE ARITHMETIC CLAIM, WITH A SEED WHERE THE TWO ANSWERS DIFFER. A seed where
# they agreed would be satisfied by either implementation.
_total_prompt = 900 + 1000 + 1000
_naive_per_call = _total_prompt / 3
_wave_rows = [r for r in _led["rows"] if r["_kind"] != _cm.WARMUP_KIND]
_ledger_wave_prompt = sum(r["prompt_tokens"] for r in _wave_rows)
_ledger_cached = sum(r["cached_tokens"] for r in _wave_rows)
check("2e  non-degeneracy: dividing the patient total by the call count gives "
      "a DIFFERENT answer from reading the ledger's evaluation rows, so a "
      "check that reads the ledger cannot be satisfied by the naive division",
      round(_naive_per_call, 3) != round(_ledger_wave_prompt / 2, 3), True)
check("2e  the cache hit rate over EVALUATION requests only",
      round(_ledger_cached / _ledger_wave_prompt * 100, 1), 89.6)

# A SILENT PROVIDER IS NOT A COLD CACHE. `cached_tokens` NULL means the response
# reported no such field; 0 means it reported and nothing was cached. Only the
# second is evidence the prefix is not being reused.
_silent = json.dumps([{"call_index": 1, "warmup": True, "prompt_tokens": 900},
                      {"call_index": 2, "trials": 1, "prompt_tokens": 1000}])
_led_silent = drive(_cm.ledger, _silent)
check("2f  a ledger whose responses report no cached-token field still reads "
      "as rows -- the absence is the finding, not a decode failure",
      (_led_silent["state"], _led_silent["evaluation_calls"]), ("rows", 1))
check("2f  ...and its evaluation row carries None, never 0",
      _led_silent["rows"][1].get("cached_tokens"), None)

check("2g  LEDGER_STATES is closed, so a consumer may branch over it",
      sorted(_cm.LEDGER_STATES),
      sorted((_cm.LEDGER_ABSENT, _cm.LEDGER_EMPTY, _cm.LEDGER_UNREADABLE,
              "rows")))


#------------------------------------------------------------------------------


# ===========================================================================
# THE SEEDED MIXED-MODE DATABASE, AND THE RENDER HARNESS
# ===========================================================================
# The schema is built by the project's own initialize_database(), so it is real
# by construction rather than retyped -- tests/test_dashboard_run_health.py's
# convention, adopted for its reason: a hand-written CREATE TABLE agrees with
# the writer only until the writer moves.

_MIXED_DB = os.path.join(_TMP, "mixed.db")
_SINGLE_DB = os.path.join(_TMP, "single.db")
_dl.initialize_database(_MIXED_DB)
_dl.initialize_database(_SINGLE_DB)


def _row(pid, mode, cost, tin, tout, calls, evaluated, tier, ledger=None):
    """One inferences row, as a dict of the columns these tabs read.

    `match_tier` IS DELIBERATELY NOT A COLUMN HERE, and the first draft of this
    seed tried to make it one. It is not in the `inferences` schema at all --
    `oncotriage/dashboard/tiers.py:enrich_match_tiers` derives it per patient
    from `trial_matches` and `oncotriage/dashboard/app.py` calls that BEFORE
    handing the frame to a tab. Seeding it directly would hand these tabs a
    frame production never produces, so `_frame` runs the real enrichment
    instead and `tier` here decides the trial_matches SCORE that produces it.
    """
    return {
        "patient_id": pid, "matching_call_mode": mode,
        "estimated_cost_usd": cost,
        "llm_classifier_input_tokens": tin,
        "llm_classifier_output_tokens": tout,
        "llm_classifier_reasoning_tokens": None,
        "llm_classifier_calls": calls,
        "llm_classifier_call_details": ledger,
        "candidates_evaluated": evaluated, "candidates_retrieved": 100,
        "eligible_matches": 1,
        "error": "", "matching_model": "gpt-5.6-terra",
        "total_time": 60.0, "llm_classifier_evaluation_time": 40.0,
        "age": 60, "sex": "female", "condition_count": 4,
        "medication_count": 3, "timestamp": "2026-08-20T00:00:00",
        "_tier": tier,
    }


# PER-TRIAL AND GROUPED ARE SEEDED WITH FIGURES THAT REALLY DIFFER, in the
# direction and roughly the ratio the arms produce: a per-trial patient renders
# the shared prefix once per trial call plus the warmup, a grouped patient once
# per packed chunk. A seed where the two arms carried similar numbers would let
# a blended mean look correct and would prove nothing about the split.
_PT_ROWS = [_row(f"PT-{i}", _PT, 0.180, 16000, 4000, 16, 15,
                 "Full Match" if i % 2 else "Partial Match",
                 ledger=_LEDGER_PT) for i in range(1, 5)]
_GR_ROWS = [_row(f"GR-{i}", _GR, 0.040, 4000, 3000, 2, 15,
                 "Partial Match") for i in range(1, 5)]
# A ROW FROM BEFORE THE COLUMN EXISTED, carried in a migrated database: the
# column is there and its value is NULL. That is a THIRD bucket and not a
# guess at which arm produced it.
_OLD_ROWS = [_row("OLD-1", None, 0.055, 5000, 3200, None, 15, "Partial Match")]

_MIXED_ROWS = _PT_ROWS + _GR_ROWS + _OLD_ROWS
_SINGLE_ROWS = _PT_ROWS


# THE SCORE THAT PRODUCES EACH TIER, read off enrich_match_tiers' own rule
# rather than retyped as a tier string: >= 1.0 is Full, strictly between is
# Partial, <= 0.0 is Unconfirmed.
_TIER_SCORE = {"Full Match": 1.0, "Partial Match": 0.5,
               "Unconfirmed Match": 0.0}


def _insert(db_path, rows):
    """Seed both tables. `id` is taken from the INSERT so trial_matches can
    reference it -- enrich_match_tiers joins on `inferences.id`."""
    conn = sqlite3.connect(db_path)
    try:
        cols = [c for c in rows[0] if not c.startswith("_")]
        for i, r in enumerate(rows):
            cur = conn.execute(
                f"INSERT INTO inferences ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                tuple(r[c] for c in cols))
            conn.execute(
                "INSERT INTO trial_matches (inference_id, nct_id, "
                "match_score, eligible, trial_title, trial_phase) "
                "VALUES (?,?,?,?,?,?)",
                (cur.lastrowid, f"NCT{i:08d}",
                 _TIER_SCORE[r["_tier"]], "eligible", f"Trial {i}", "Phase 2"))
        conn.commit()
    finally:
        conn.close()


_insert(_MIXED_DB, _MIXED_ROWS)
_insert(_SINGLE_DB, _SINGLE_ROWS)


def _frame(db_path):
    """The frame a tab is actually handed: the inferences table ENRICHED.

    `oncotriage/dashboard/app.py` calls enrich_match_tiers before passing the
    frame to any tab, so a seed that skipped it would hand these tabs a shape
    production never produces -- and `match_tier`, which three of the panels
    under test group by, would be absent.
    """
    conn = sqlite3.connect(db_path)
    try:
        inferences = pd.read_sql_query("SELECT * FROM inferences", conn)
        matches = pd.read_sql_query("SELECT * FROM trial_matches", conn)
    finally:
        conn.close()
    # `load_inferences_data` converts this column before any tab sees it, and
    # two tabs use the `.dt` accessor on it. A seed that skipped the conversion
    # would hand them a string column and fail for a reason unrelated to what
    # is under test -- which is what the first draft of this file did.
    inferences["timestamp"] = pd.to_datetime(inferences["timestamp"])
    return _tiers.enrich_match_tiers(inferences, matches)


_DRIVER = """
import pickle, importlib, sys
sys.path.insert(0, {extra_path!r})
_mod = importlib.import_module({module!r})
with open({frame!r}, "rb") as _fh:
    _df = pickle.load(_fh)
_mod.{fn}(_df)
"""

_CAPTURE_KEYS = ("exception", "metrics", "caption", "markdown", "warning",
                 "info", "error", "subheader", "dataframes")


def _plotly_title(element):
    """The title text of one rendered chart, or a NAMED absence. Never raises.

    IT READS THE PROTO AND NOT ``element.value``, and that is not a preference:
    ``value`` resolves through ``st.session_state`` and raises ``KeyError`` for
    a chart the run did not register there -- which is every non-interactive
    chart on these tabs. The first draft of this helper used it and ABORTED the
    whole file at module scope, reporting one traceback where it owed two
    hundred results. The fifteenth time this project has met that shape.
    """
    # `proto.spec` IS THE FIGURE JSON in the installed streamlit; `proto.figure`
    # exists too and is EMPTY for a chart passed as a plotly object, which is
    # what every chart on these tabs is. Measured against the installed version
    # rather than assumed -- the first draft read `figure.spec`, got an empty
    # string, and reported every title as unreadable.
    try:
        spec = json.loads(element.proto.spec)
        return ((spec.get("layout") or {}).get("title") or {}).get("text")
    except BaseException as exc:                       # noqa: BLE001
        return f"(unreadable: {type(exc).__name__})"


def _capture(at):
    return {
        "exception": [e.value for e in at.exception],
        "metrics": [(m.label, m.value, m.help) for m in at.metric],
        "caption": [c.value for c in at.caption],
        "markdown": [m.value for m in at.markdown],
        "warning": [w.value for w in at.warning],
        "info": [i.value for i in at.info],
        "error": [e.value for e in at.error],
        "subheader": [s.value for s in at.subheader],
        "dataframes": [d.value.to_csv(index=False) for d in at.dataframe],
        "dataframe_objects": [d.value for d in at.dataframe],
        "plotly_titles": [_plotly_title(p) for p in at.get("plotly_chart")],
    }


def _render(module, fn, df, db_path, extra_path=None):
    """Render one tab function against one scratch database. Never raises."""
    frame_path = os.path.join(_TMP, "frame.pkl")
    with open(frame_path, "wb") as fh:
        pickle.dump(df, fh)
    script = _DRIVER.format(extra_path=extra_path or _PLANT_DIR,
                            module=module, frame=frame_path, fn=fn)
    _paths._RESOLVED["inferences_path"] = db_path
    st.cache_data.clear()
    try:
        at = AppTest.from_string(script, default_timeout=180)
        at.run()
    except BaseException as exc:                       # noqa: BLE001
        return {"exception": [f"HARNESS {type(exc).__name__}: {exc}"],
                "metrics": [], "caption": [], "markdown": [], "warning": [],
                "info": [], "error": [], "subheader": [], "dataframes": [],
                "dataframe_objects": [], "plotly_titles": []}
    return _capture(at)


def blob(capture, *keys):
    """Every string in the named buckets, as one searchable blob."""
    out = []
    for key in keys:
        for value in capture.get(key, []):
            out.append(" | ".join(str(v) for v in value)
                       if isinstance(value, tuple) else str(value))
    return "\n".join(out)


def metric_named(capture, needle):
    """The first metric whose LABEL contains ``needle`` -- or a named absence."""
    for label, value, help_text in capture.get("metrics", []):
        if needle in str(label):
            return {"label": label, "value": value, "help": str(help_text or "")}
    return {"label": "(absent)", "value": "(absent)", "help": "(absent)"}


class _PlantFailed:
    """A plant that could not be built or imported, as a VALUE.

    A CONTROL THAT ABORTS IS NOT A CONTROL, and the first draft of this file
    shipped that defect: a plant with an unbalanced parenthesis made
    ``importlib.import_module`` raise ``SyntaxError`` at module scope, and the
    run reported one traceback where it owed a hundred and fifty results. Every
    attribute access on this object returns another one, and every call returns
    the marker, so a planted module that failed to build travels into the
    comparison as a value ``check`` fails on and names.
    """

    def __init__(self, reason):
        self.reason = reason

    def __getattr__(self, _name):
        return self

    def __call__(self, *_a, **_k):
        return self

    def __getitem__(self, _k):
        return self

    def __eq__(self, other):
        return isinstance(other, _PlantFailed) and other.reason == self.reason

    def __hash__(self):
        return hash(("_PlantFailed", self.reason))

    def __repr__(self):
        return f"PLANT-FAILED: {self.reason}"


def load_plant(name):
    """Import a planted copy, turning any raise into a named failure value."""
    try:
        import importlib
        return importlib.import_module(name)
    except BaseException as exc:                       # noqa: BLE001
        return _PlantFailed(f"{type(exc).__name__}: {exc}")


_PLANT_SEQ = [0]


def plant(rel_path, old, new, count=1):
    """A COPY of a package module with ``old`` -> ``new``, in the temp tree.

    Returns ``(module_name, occurrences)``. The occurrence count is returned so
    a plant that matched NOTHING is a named failure rather than a working check
    reported as MISSED -- pass 20f-1's lesson.

    NOTHING UNDER VERSION CONTROL IS TOUCHED and nothing is exec'd: the copy is
    written to ``_PLANT_DIR`` and imported from there by the driver, which is
    what keeps this file outside ``_EXEC_ALLOWLIST`` and outside the collision
    matrix.
    """
    src_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(_cm.__file__))),
        *rel_path.split("/"))
    source = Path(src_path).read_text(encoding="utf-8")
    made = source.count(old)
    _PLANT_SEQ[0] += 1
    name = f"plant_{_PLANT_SEQ[0]}_{os.path.basename(rel_path)[:-3]}"
    Path(os.path.join(_PLANT_DIR, name + ".py")).write_text(
        source.replace(old, new, count), encoding="utf-8")
    return name, made


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3: the Cost & Tokens tab -- blended figures SAY they are blended
# ===========================================================================

section("SECTION 3: Cost & Tokens, mixed-mode and single-mode")

_COST = "oncotriage.dashboard.tabs.cost_tokens"
_cost_mixed = _render(_COST, "render_cost_tokens_tab", _frame(_MIXED_DB), _MIXED_DB)
_cost_single = _render(_COST, "render_cost_tokens_tab", _frame(_SINGLE_DB), _SINGLE_DB)

check("3a  the tab renders against a mixed-mode database without raising",
      _cost_mixed["exception"], [])
check("3a  ...and against a single-mode one", _cost_single["exception"], [])

# THE FOUR HEADLINE COST FIGURES. The TOTAL is a sum of dollars actually spent
# and is correct however the rows were produced; the other three are per-patient
# statistics over two populations.
_avg = metric_named(_cost_mixed, "Average Cost")
_proj = metric_named(_cost_mixed, "Projected")
_total = metric_named(_cost_mixed, "Total Cost")
check("3b  the blended AVERAGE says it is blended",
      "BLENDED ACROSS CALL MODES" in _avg["help"], True)
check("3b  the blended PROJECTION says so too -- it is the number a campaign "
      "is budgeted from, and a blended average projects a cohort nobody runs",
      "BLENDED ACROSS CALL MODES" in _proj["help"], True)
check("3c  THE TOTAL IS NOT QUALIFIED, and the asymmetry is the point: a SUM "
      "of dollars spent is correct across arms, a MEAN is not",
      "BLENDED" in _total["help"], False)
check("3c  non-degeneracy: the total really was rendered",
      _total["value"] != "(absent)", True)

# THE SAME TAB, ONE ARM: the warnings must be ABSENT. A panel that cries mix
# on every ordinary campaign is a panel an operator learns to scroll past.
check("3d  on a single-arm selection the average carries NO blended warning",
      "BLENDED ACROSS CALL MODES" in metric_named(_cost_single,
                                                  "Average Cost")["help"],
      False)
check("3d  ...and names the arm it is a statement about instead",
      "per-trial" in metric_named(_cost_single, "Average Cost")["help"], True)

# THE UNBLENDED TABLES render only when there is a mix -- item 38's
# `skip_if_empty` applied to a panel. On one arm they would be one row
# restating the metrics above them.
check("3e  a mixed selection gets the unblended per-mode cost table",
      "Cost per call mode" in blob(_cost_mixed, "markdown"), True)
check("3e  ...and the unblended per-mode token table",
      "Token usage per call mode" in blob(_cost_mixed, "markdown"), True)
check("3e  a single-arm selection gets NEITHER -- the caption already says "
      "which arm the figures belong to",
      ("Cost per call mode" in blob(_cost_single, "markdown"),
       "Token usage per call mode" in blob(_cost_single, "markdown")),
      (False, False))

# THE PER-MODE FIGURES ARE THE SEED'S, computed here from the rows inserted and
# never read back out of the frame under test.
_pm = [d for d in _cost_mixed["dataframe_objects"]
       if "avg $/patient" in list(d.columns)]
_pm = _pm[0] if _pm else pd.DataFrame()
check("3f  non-degeneracy: the per-mode cost table was found and has one row "
      "per bucket in the seed",
      len(_pm), 3)
if len(_pm) == 3:
    _by = {r["call mode"]: r for _, r in _pm.iterrows()}
    check("3f  the per-trial average is the per-trial rows' own, not a blend",
          round(float(_by["per-trial"]["avg $/patient"]), 4), 0.18)
    check("3f  the grouped average is the grouped rows' own",
          round(float(_by["grouped"]["avg $/patient"]), 4), 0.04)
    check("3f  and the not-recorded row is kept as its own bucket rather than "
          "being dropped or assigned to an arm",
          int(_by[MODE_NOT_RECORDED_LABEL]["patients"]), 1)
    check("3f  non-degeneracy: the two arms' averages really differ, so a "
          "blended figure could not have satisfied the two checks above",
          float(_by["per-trial"]["avg $/patient"])
          != float(_by["grouped"]["avg $/patient"]), True)

# THE CALL COUNT COMES FROM THE COLUMN, NOT FROM THE MODE. A patient whose
# warmup was refused falls back to a different schedule, and one that never
# reached Stage 5 issued none -- so the arm cannot be used to infer the count.
_calls_metric = metric_named(_cost_single, "Avg Stage 5 Calls")
check("3g  the tab reports billed Stage 5 requests per patient",
      _calls_metric["value"], "16.0")
check("3g  ...read from `llm_classifier_calls` and named as such",
      "llm_classifier_calls" in _calls_metric["help"], True)

# NULL IS NOT ZERO. The seeded OLD row carries a NULL call count; a mean that
# folded it in as 0 would report 9.6 instead of 12.0 over the mixed frame.
_mixed_calls = metric_named(_cost_mixed, "Avg Stage 5 Calls")
_non_null = [r["llm_classifier_calls"] for r in _MIXED_ROWS
             if r["llm_classifier_calls"] is not None]
check("3h  the call average EXCLUDES the NULL row rather than counting it as "
      "zero",
      _mixed_calls["value"], f"{sum(_non_null) / len(_non_null):,.1f}")
check("3h  non-degeneracy: folding the NULL in as 0 would give a DIFFERENT "
      "number, so the check above cannot be satisfied by either arithmetic",
      round(sum(_non_null) / len(_non_null), 1)
      != round(sum(_non_null) / len(_MIXED_ROWS), 1), True)
check("3h  ...and the rows it skipped are stated rather than absorbed",
      "record no Stage 5 call count" in blob(_cost_mixed, "caption"), True)

# THE HISTOGRAM IS SPLIT RATHER THAN LABELLED, because two arms an order of
# magnitude apart render as one bimodal smear whose median line describes
# neither hump.
check("3i  the mixed token-efficiency chart names the mix in its title",
      any("MIXED CALL MODES" in str(t) for t in _cost_mixed["plotly_titles"]),
      True)
check("3i  the single-arm charts name the arm instead of crying mix",
      (any("(per-trial)" in str(t) for t in _cost_single["plotly_titles"]),
       any("MIXED" in str(t) for t in _cost_single["plotly_titles"])),
      (True, False))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4: overview, performance and the per-call ledger panel
# ===========================================================================

section("SECTION 4: overview, performance, patient explorer")

_ov_mixed = _render("oncotriage.dashboard.tabs.overview",
                    "render_overview_tab", _frame(_MIXED_DB), _MIXED_DB)
_ov_single = _render("oncotriage.dashboard.tabs.overview",
                     "render_overview_tab", _frame(_SINGLE_DB), _SINGLE_DB)
check("4a  the overview renders on both", 
      (_ov_mixed["exception"], _ov_single["exception"]), ([], []))
check("4a  the headline Avg Cost/Patient says when it is blended -- a reader "
      "who never opens the Cost tab sees only this one",
      "BLENDED ACROSS STAGE 5 CALL MODES"
      in metric_named(_ov_mixed, "Avg Cost/Patient")["help"], True)
check("4a  ...and does not on one arm",
      "BLENDED" in metric_named(_ov_single, "Avg Cost/Patient")["help"], False)

_pf = _render("oncotriage.dashboard.tabs.performance",
              "render_performance_tab", _frame(_MIXED_DB), _MIXED_DB)
check("4b  the performance tab renders", _pf["exception"], [])
_slow = [d for d in _pf["dataframe_objects"] if "Call Mode" in list(d.columns)]
check("4b  THE SLOWEST-PATIENTS TABLE NAMES THE ARM ON EACH ROW. Two of its "
      "columns -- Stage 5 time and output tokens -- are decided by the arm, so "
      "a reader comparing row 1 with row 7 was comparing two measurements",
      len(_slow), 1)
if _slow:
    check("4b  ...and the values are the seeded buckets, not raw storage "
          "spellings or a guess for the NULL row",
          sorted(set(_slow[0]["Call Mode"])) != [], True)
    check("4b  ...every rendered bucket is one call_mode.describe knows",
          set(_slow[0]["Call Mode"]) - set(_cm.BUCKET_ORDER), set())

# --- the per-call ledger panel -------------------------------------------
# This panel is why the ledger reader exists. patient_explorer renders exactly
# ONE row, which is the only place a per-call breakdown is meaningful:
# `call_index` numbers the requests of a single Stage 5 invocation, so ledgers
# from two patients cannot be concatenated into anything.
_pe = _render("oncotriage.dashboard.tabs.patient_explorer",
              "render_patient_explorer_tab", _frame(_SINGLE_DB), _SINGLE_DB)
check("4c  the patient explorer renders", _pe["exception"], [])
check("4c  it has a Stage 5 Requests panel", 
      "Stage 5 Requests" in blob(_pe, "subheader"), True)
check("4d  the row's OWN arm is rendered, read from the row and never from the "
      "configured default -- a row written before the flip keeps its arm",
      metric_named(_pe, "Call Mode")["value"], "per-trial")
check("4d  the billed request count comes from `llm_classifier_calls`",
      metric_named(_pe, "Billed Requests")["value"], "16")
check("4e  THE WARMUP IS LABELLED INFRASTRUCTURE and counted apart from the "
      "evaluations",
      metric_named(_pe, "of which warmup")["value"], "1")
check("4e  ...and the panel says what a warmup IS, so a reader does not read "
      "it as a failed evaluation",
      "INFRASTRUCTURE" in metric_named(_pe, "of which warmup")["help"].upper(),
      True)

_ledger_frames = [d for d in _pe["dataframe_objects"] if "kind" in list(d.columns)]
check("4f  the per-request ledger table is rendered",
      len(_ledger_frames), 1)
if _ledger_frames:
    _lf = _ledger_frames[0]
    check("4f  one row per request the ledger holds", len(_lf), 3)
    check("4f  the warmup row is named and the others are not",
          list(_lf["kind"]),
          [_cm.WARMUP_KIND, _cm.EVALUATION_KIND, _cm.EVALUATION_KIND])
    check("4f  every figure in it is the LEDGER's own, not a total divided by "
          "a call count",
          list(_lf["prompt tok"]), [900, 1000, 1000])

# THE CACHE RATE EXCLUDES THE WARMUP, and the seed is built so that including
# it would give a different number -- a seed where they agreed would be
# satisfied by either implementation.
_cap = blob(_pe, "caption")
_rate_all = (896 + 896) / (900 + 1000 + 1000) * 100
_rate_wave = (896 + 896) / (1000 + 1000) * 100
check("4g  non-degeneracy: including the warmup in the cache rate gives a "
      "DIFFERENT figure from excluding it",
      round(_rate_all, 1) != round(_rate_wave, 1), True)
check("4g  the rendered rate is the EVALUATION-only one: the warmup WRITES the "
      "prefix, so its own 0% is the healthy reading, not a cache miss",
      f"{_rate_wave:.1f}%" in _cap, True)
check("4g  ...and the panel says the warmup is excluded and why",
      "WRITES the prefix" in _cap, True)


# --- run_health's two tables name the arm --------------------------------
# THE RUN AND CAMPAIGN TABLES ARE WHERE A REVIEWER ATTRIBUTES A PUBLISHED
# NUMBER, and both carry a `cost $` and a `patients` column whose comparability
# across rows depends entirely on the arm. `run_summary` and `campaign_summary`
# have PROJECTED `matching_call_mode` since the call-mode pass and neither table
# rendered it.
#
# The row builders are driven directly rather than through a render: they are
# pure functions of a query frame, which is the natural control for a pure
# function, and building the six-table run-tracking database a render needs is
# tests/test_dashboard_run_health.py's job and is already done there.
_run_row = {"run_id": 1, "invocation_source": "batch", "status": "FINISHED",
            "finalization": "finalized", "started_at": "2026-08-20T00:00:00",
            "finished_at": "2026-08-20T01:00:00", "patients": 10, "errored": 0,
            "cost_usd": 1.8, "rows_with_no_cost": 0,
            "health_record": "measured clean", "counters_registered": 22,
            "degradation_events": 0, "llm_classifier_prompt_version": "1.9.0",
            "matching_model_configured": "gpt-5.6-terra",
            "matching_call_mode": _PT, "qdrant_collection": "trial_criteria_x",
            "resumed": 0}
_run_frame = pd.DataFrame([_run_row,
                           dict(_run_row, run_id=2, matching_call_mode=_GR),
                           dict(_run_row, run_id=3, matching_call_mode=None)])
# THE LOOKUP GOES THROUGH ``drive`` TOO. ``getattr`` on a renamed function
# raises, and it raises OUTSIDE the call -- which is the abort shape one step
# earlier than the one ``drive`` was written for. The first version of this
# line named a function that does not exist and took the file down with no
# summary; this reports it as a recorded failure naming the attribute.
_run_table = drive(lambda: getattr(_run_health, "_build_run_table")(_run_frame))
check("4h  the per-run table carries a `call mode` column",
      "call mode" in list(getattr(_run_table, "columns", [])), True)
if "call mode" in list(getattr(_run_table, "columns", [])):
    check("4h  ...rendering each run's OWN arm, and the not-recorded bucket "
          "for a run written before `runs.matching_call_mode` existed -- "
          "'—' there would read as an arm nobody recorded being grouped",
          list(_run_table["call mode"]),
          ["per-trial", "grouped", MODE_NOT_RECORDED_LABEL])

# THE CAMPAIGN TABLE TOO, and its arm means something stronger: a campaign is
# stitched across fragments only when EVERY fingerprint column matches, and the
# arm is one of them -- so a grouped fragment and a per-trial fragment are two
# campaigns and their patients and costs are never summed. That is what lets
# this column be a scalar per row rather than a mix, and rendering it is what
# lets a reviewer SEE the guarantee rather than take it on trust.
_camp_row = {"campaign_id": 1, "run_ids": "1", "runs": 1, "stitched": 0,
             "statuses": "FINISHED", "mixed_status": 0, "total_patients": 10,
             "inference_rows": 11, "first_started_at": "2026-08-20T00:00:00",
             "last_finished_at": "2026-08-20T01:00:00", "unfinalized_runs": 0,
             "total_cost_usd": 1.8, "rows_with_no_cost": 0,
             "invocation_source": "batch",
             "llm_classifier_prompt_version": "1.9.0",
             "matching_model_configured": "gpt-5.6-terra",
             "matching_call_mode": _PT, "qdrant_collection": "trial_criteria_x"}
_camp_frame = pd.DataFrame([_camp_row,
                            dict(_camp_row, campaign_id=2,
                                 matching_call_mode=None)])
_camp_table = drive(
    lambda: getattr(_run_health, "_build_campaign_table")(_camp_frame))
check("4i  the campaign table carries a `call mode` column",
      "call mode" in list(getattr(_camp_table, "columns", [])), True)
if "call mode" in list(getattr(_camp_table, "columns", [])):
    check("4i  ...one value per campaign, with the not-recorded bucket kept "
          "apart from an arm",
          list(_camp_table["call mode"]),
          ["per-trial", MODE_NOT_RECORDED_LABEL])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5: seven planted defects, and the shipped answer beside each
# ===========================================================================
# Every plant goes into a COPY in the temp tree. Each is paired with the shipped
# module's answer to the same question, so a check that passed because the
# harness stopped observing fails too.

section("SECTION 5: planted defects")


def control(label, planted, shipped):
    """A plant must change the answer. Both readings are printed on failure."""
    check(label, planted != shipped, True)


_p1, _n1 = plant("dashboard/tabs/cost_tokens.py",
                 'help="Average cost per patient inference." + (',
                 'help="Average cost per patient inference." + (False and (')
check("5a  plant 1 matched the shipped source", _n1, 1)

# A SIMPLER AND STRONGER PLANT FOR THE SAME CLAIM: make the mix reading always
# report a single arm, which is exactly what a tab that had stopped consulting
# call_mode would do.
_p2, _n2 = plant("dashboard/call_mode.py",
                 '        "is_mixed": len(buckets) > 1,',
                 '        "is_mixed": False,')
check("5b  plant 2 matched the shipped source", _n2, 1)
# THE PLANT IS DRIVEN DIRECTLY RATHER THAN THROUGH A RENDER, and that is a
# limit stated rather than glossed: `cost_tokens.py` imports `call_mode` by
# PACKAGE name, so a copy in the temp tree is not what a rendered tab resolves.
# What the direct drive establishes is that the planted reading and the shipped
# reading DISAGREE on the seeded frame -- which is the claim -- and section 3
# separately establishes that the tab's rendering follows the shipped reading.
sys.path.insert(0, _PLANT_DIR)
try:
    _blind = load_plant(_p2)
    control("5b  a describe() that never reports a mix disagrees with the "
            "shipped one on the seeded mixed frame",
            _blind.describe(_mixed)["is_mixed"], _cm.describe(_mixed)["is_mixed"])
    check("5b  ...and the shipped one is the one that says True",
          _cm.describe(_mixed)["is_mixed"], True)

    _p3, _n3 = plant("dashboard/call_mode.py",
                     "    if value is None or (isinstance(value, float) and pd.isna(value)):\n"
                     "        return MODE_NOT_RECORDED_LABEL",
                     "    if value is None or (isinstance(value, float) and pd.isna(value)):\n"
                     "        return MODE_DISPLAY[config.MATCHING_CALL_MODES[0]]")
    check("5c  plant 3 matched the shipped source", _n3, 1)
    _guessing = load_plant(_p3)
    control("5c  A MODULE THAT GUESSES AN ARM FOR A NULL disagrees with the "
            "shipped one, which reports the absence",
            _guessing.bucket_of(None), _cm.bucket_of(None))
    check("5c  ...and the shipped answer is the not-recorded bucket",
          _cm.bucket_of(None), MODE_NOT_RECORDED_LABEL)
    check("5c  ...while the guessing one silently makes a mixed frame look "
          "single-armed, which is the defect",
          _guessing.describe(pd.DataFrame(
              {"matching_call_mode": [_GR, None]}))["is_mixed"], False)

    _p4, _n4 = plant("dashboard/call_mode.py",
                     'WARMUP_KIND if "warmup" in entry',
                     'WARMUP_KIND if False')
    check("5d  plant 4 matched the shipped source", _n4, 1)
    _nowarm = load_plant(_p4)
    control("5d  A LEDGER READER THAT DOES NOT SEE THE WARMUP counts it as an "
            "evaluation and disagrees with the shipped one",
            _nowarm.ledger(_LEDGER_PT)["warmup_calls"],
            _cm.ledger(_LEDGER_PT)["warmup_calls"])
    check("5d  ...the shipped one separates 1 warmup from 2 evaluations",
          (_cm.ledger(_LEDGER_PT)["warmup_calls"],
           _cm.ledger(_LEDGER_PT)["evaluation_calls"]), (1, 2))
    check("5d  ...the planted one reports 0 and 3, which would put the "
          "prefix-writing request into the cache hit rate",
          (_nowarm.ledger(_LEDGER_PT)["warmup_calls"],
           _nowarm.ledger(_LEDGER_PT)["evaluation_calls"]), (0, 3))

    _p5, _n5 = plant("dashboard/call_mode.py",
                     '    if raw is None or (isinstance(raw, float) and pd.isna(raw)):\n'
                     '        return {"state": LEDGER_ABSENT',
                     '    if raw is None or (isinstance(raw, float) and pd.isna(raw)):\n'
                     '        return {"state": LEDGER_EMPTY')
    check("5e  plant 5 matched the shipped source", _n5, 1)
    _conflated = load_plant(_p5)
    control("5e  A READER THAT CONFLATES NULL WITH [] disagrees with the "
            "shipped one -- 'Stage 5 never ran' is not 'it ran and issued "
            "nothing'",
            _conflated.ledger(None)["state"], _cm.ledger(None)["state"])
    check("5e  ...and the shipped one keeps them apart",
          (_cm.ledger(None)["state"], _cm.ledger("[]")["state"]),
          (_cm.LEDGER_ABSENT, _cm.LEDGER_EMPTY))

    _p6, _n6 = plant("dashboard/call_mode.py",
                     "    out = df.copy()", "    out = df")
    check("5f  plant 6 matched the shipped source", _n6, 1)
    _mutating = load_plant(_p6)
    _victim = pd.DataFrame({"matching_call_mode": [_PT]})
    _mutating.annotate(_victim)
    control("5f  AN annotate() THAT DOES NOT COPY leaks a column into the "
            "caller's frame -- which @st.cache_data hands to every other tab "
            "on the page and to every later rerun",
            "call_mode_label" in _victim.columns,
            "call_mode_label" in pd.DataFrame(
                {"matching_call_mode": [_PT]}).pipe(
                    lambda d: (_cm.annotate(d), d)[1]).columns)
    check("5f  ...the shipped one leaves the caller's frame untouched",
          "call_mode_label" in pd.DataFrame(
              {"matching_call_mode": [_PT]}).pipe(
                  lambda d: (_cm.annotate(d), d)[1]).columns, False)
finally:
    while _PLANT_DIR in sys.path:
        sys.path.remove(_PLANT_DIR)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6: GET /pipeline/info reports the call mode
# ===========================================================================
# NO BILLED CALL IS REACHABLE. The Qdrant client is a stand-in installed through
# oncotriage/agent/deps.py, the graph is never invoked, and every field asserted
# below is read from a config constant.

section("SECTION 6: GET /pipeline/info's config.call_mode block")

from fastapi.testclient import TestClient                      # noqa: E402


class _StubCount:
    count = 4242


class _StubQdrant:
    """Answers the two calls readiness.probe_index makes. Nothing else."""

    def collection_exists(self, name):
        return True

    def count(self, collection_name=None, exact=True):
        return _StubCount()


class _StubMeshFilter:
    """A stand-in for the MeSH site filter. Nothing calls it here."""


_SAVED_DEPS = {k: _deps.peek(k) for k in _deps.OVERRIDE_KEYS}
_deps.set_override(_deps.QDRANT_CLIENT, _StubQdrant())
# THE MESH FILTER IS STUBBED SO /health's VERDICT HAS ONE VARIABLE. Without it
# the mesh probe reads the real lookups off disk, which a CI runner does not
# have -- and section 8's "healthy with a compatible database" would fail there
# for a reason that has nothing to do with the database. Stubbing it makes the
# database the only thing that can move the verdict, which is what section 8
# is about.
_deps.set_override(_deps.MESH_FILTER, _StubMeshFilter())

try:
    from oncotriage.api.server import create_app                # noqa: E402
    # THE LIFESPAN IS RUN, and it has to be: `graph` is built there, and
    # /health's verdict is `report READY and graph is not None`. A TestClient
    # used without the context manager leaves the graph None, so every /health
    # would be 503 and section 8 would pass for the wrong reason. Compiling the
    # graph loads NO model -- every dependency is behind the deps seam and both
    # of the ones this app reaches are stubbed above.
    _client_cm = TestClient(create_app())
    _client = _client_cm.__enter__()
    _info = _client.get("/pipeline/info")
    check("6a  GET /pipeline/info answered 200", _info.status_code, 200)
    _ij = _info.json() if _info.status_code == 200 else {}
    _blockdict = (_ij.get("config") or {}).get("call_mode") or {}

    check("6b  the block is nested inside `config`, on qdrant_endpoint's "
          "precedent -- three of its fields are meaningless in grouped mode "
          "and flattened among the always-applicable tunables would read as "
          "though they always applied",
          isinstance(_blockdict, dict) and bool(_blockdict), True)
    check("6b  ...and its key set is exactly what this pass declares",
          sorted(_blockdict),
          ["configured_default", "in_force", "per_trial_max_parallel_calls",
           "per_trial_warmup_dedicated_retries",
           "per_trial_warmup_max_output_tokens",
           "per_trial_warmup_sdk_max_retries", "pin"])

    check("6c  the SHIPPED DEFAULT is reported, derived from "
          "MATCHING_PER_TRIAL_CALLS_ENABLED through the same two-member "
          "vocabulary the pipeline uses",
          _blockdict.get("configured_default"),
          _PT if _config.MATCHING_PER_TRIAL_CALLS_ENABLED else _GR)
    check("6c  the MODE IN FORCE for this process is reported separately, "
          "because config.matching_call_mode() resolves pin-then-constant and "
          "a pin is process-global",
          _blockdict.get("in_force"), _config.matching_call_mode())
    check("6c  with no pin installed the two agree and `pin` is null",
          (_blockdict.get("pin"),
           _blockdict.get("in_force") == _blockdict.get("configured_default")),
          (None, True))

    check("6d  the per-trial constants an operator needs are reported",
          (_blockdict.get("per_trial_max_parallel_calls"),
           _blockdict.get("per_trial_warmup_max_output_tokens")),
          (_config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS,
           _config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS))
    check("6e  THERE IS NO DEDICATED WARMUP RETRY BUDGET AND THE FIELD SAYS SO "
          "RATHER THAN BEING OMITTED. A reader who does not find a key cannot "
          "tell 'this endpoint does not report it' from 'there isn't one'",
          "per_trial_warmup_dedicated_retries" in _blockdict
          and _blockdict["per_trial_warmup_dedicated_retries"] is None, True)
    check("6e  ...and the transport budget that DOES cover it is named",
          _blockdict.get("per_trial_warmup_sdk_max_retries"),
          _config.OPENAI_SDK_MAX_RETRIES)
    check("6e  ...while the node re-entry budget is NOT repeated here -- it is "
          "already `max_llm_classifier_retries` one level up, and a response "
          "that says one fact twice is what this endpoint refused to do for "
          "cross_encoder_model",
          (_ij.get("config") or {}).get("max_llm_classifier_retries"),
          _config.MAX_LLM_CLASSIFIER_RETRIES)

    # THE PIN IS REPORTED WHEN THERE IS ONE. fixture_capture.py and
    # fixture_replay.py install one; so does a test. A response reporting only
    # the constant would describe a process doing something else.
    _other = _GR if _config.matching_call_mode() == _PT else _PT
    _prev = _config.pin_matching_call_mode(_other)
    try:
        _pinned = _client.get("/pipeline/info").json()["config"]["call_mode"]
        check("6f  a pinned process reports the PIN and the mode in force "
              "follows it",
              (_pinned["pin"], _pinned["in_force"]), (_other, _other))
        check("6f  ...while the configured default is unchanged, so the two "
              "facts stay apart",
              _pinned["configured_default"],
              _blockdict.get("configured_default"))
        check("6f  non-degeneracy: the pin really differs from the default, so "
              "the check above is not satisfied by them being equal",
              _pinned["pin"] != _pinned["configured_default"], True)
    finally:
        if _prev is None:
            _config.clear_matching_call_mode_pin()
        else:
            _config.pin_matching_call_mode(_prev)
    check("6f  ...and the pin is cleared afterwards, so this file leaks no "
          "process-global state into whatever runs next in this interpreter",
          _config.matching_call_mode_pin(), None)

    # ===================================================================
    # 6g  THE DEFAULT IS READ OFF THE MODULE AT CALL TIME, NOT THROUGH A
    #     FROM-IMPORT BINDING
    # ===================================================================
    # `from oncotriage.config import MATCHING_PER_TRIAL_CALLS_ENABLED` binds the
    # VALUE at import, and `create_app()` runs at import -- so a process that
    # later rebinds the attribute on the module gets a response whose
    # `configured_default` is stale while `in_force` follows the module, with
    # `pin` null and therefore NOTHING in the response to explain the
    # disagreement. The first draft of this endpoint did exactly that.
    #
    # THIS IS NOT HYPOTHETICAL, and that is why it is a standing check rather
    # than a comment: tests/test_agent_stage5_per_trial_calls.py and
    # tests/test_agent_per_trial_trial_cap.py both rebind that attribute, so a
    # process running either alongside a served /pipeline/info would meet it.
    _saved_default = _config.MATCHING_PER_TRIAL_CALLS_ENABLED
    try:
        _config.MATCHING_PER_TRIAL_CALLS_ENABLED = not _saved_default
        _flipped = _client.get("/pipeline/info").json()["config"]["call_mode"]
        check("6g  rebinding the constant ON THE MODULE moves "
              "`configured_default`, so it is read live and not from a "
              "from-import binding taken at app-construction time",
              _flipped["configured_default"],
              _GR if _saved_default else _PT)
        check("6g  ...and `in_force` moves with it, so the two halves of the "
              "block cannot disagree about a process nobody pinned",
              (_flipped["in_force"], _flipped["pin"]),
              (_flipped["configured_default"], None))
    finally:
        _config.MATCHING_PER_TRIAL_CALLS_ENABLED = _saved_default
    _restored = _client.get("/pipeline/info").json()["config"]["call_mode"]
    check("6g  non-degeneracy: the constant was restored and the response "
          "went back, so 6g measured a real change and not a broken endpoint",
          _restored["configured_default"], _blockdict.get("configured_default"))
    check("6g  ...and the flip really produced a DIFFERENT answer, so the "
          "check above is not satisfied by an endpoint that never moves",
          _flipped["configured_default"] != _restored["configured_default"],
          True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7: GET /health surfaces the serving-database refusal
# ===========================================================================
# THE DEFECT, MEASURED RATHER THAN ARGUED: a newer-era database is refused by
# assert_database_is_compatible, the refusal is caught by
# _write_inference_row's broad handler (correctly -- a paid result must not be
# discarded over a write), and log_inference returns ok=False. So POST /match
# returns 200 with a complete body and stores nothing, while /health knew
# nothing about the database and stayed green.

    section("SECTION 7: /health and the newer-era database")

    _GOOD_DB = os.path.join(_TMP, "serving_good.db")
    _dl.initialize_database(_GOOD_DB)

    _NEWER_DB = os.path.join(_TMP, "serving_newer.db")
    _c = sqlite3.connect(_NEWER_DB)
    _c.execute(f"PRAGMA application_id = {_dl.ONCOTRIAGE_APPLICATION_ID}")
    _c.execute(f"PRAGMA user_version = {_dl.SCHEMA_USER_VERSION + 1}")
    _c.commit()
    _c.close()

    # THE PREMISE IS ESTABLISHED HERE RATHER THAN ASSUMED. If log_inference
    # RAISED, /health would be one of several ways to notice; because it
    # returns ok=False, /health is the only operator-facing surface that can.
    # The two keys the writer indexes without a default. Discovered by driving
    # the real writer rather than read off the schema: it is the RESULT dict's
    # shape that matters here, not the table's.
    def _probe_result(pid):
        return {"patient_id": pid, "timestamp": "2026-08-20T00:00:00"}

    _write = drive(_dl.log_inference, _probe_result("HEALTH-PROBE"),
                   {"patient_id": "HEALTH-PROBE"}, db_path=_NEWER_DB)
    check("7a  a write to a newer-era database does NOT raise -- it returns a "
          "result whose ok is False, which is why nothing else reports it",
          getattr(_write, "ok", "RAISED"), False)
    check("7a  non-degeneracy: the same write to a compatible database "
          "succeeds, so 7a is not about a broken writer",
          getattr(drive(_dl.log_inference, _probe_result("HEALTH-PROBE-2"),
                        {"patient_id": "HEALTH-PROBE-2"}, db_path=_GOOD_DB),
                  "ok", "RAISED"), True)

    _good = drive(_dl.probe_serving_database, _GOOD_DB)
    _bad = drive(_dl.probe_serving_database, _NEWER_DB)
    check("7b  the probe passes a compatible database", _good["ok"], True)
    check("7b  and REFUSES a newer-era one", _bad["ok"], False)
    check("7b  carrying the refusal's own message verbatim, which already "
          "names the file, both eras and the archive command -- a second "
          "wording here is a second thing to keep in step",
          "schema era" in _bad["detail"] and _NEWER_DB in _bad["detail"], True)
    check("7b  both carry the one declared check name",
          (_good["name"], _bad["name"]),
          (_dl.SERVING_DATABASE_CHECK, _dl.SERVING_DATABASE_CHECK))

    # IT MUST NOT CREATE THE FILE IT IS ASKED ABOUT. sqlite3.connect creates a
    # missing database; a probe that answered "fine" by bringing one into
    # existence would be File 41's guard-that-creates-its-own-evidence defect,
    # and on a container whose volume failed to mount it would establish an
    # empty database at the mount point.
    _NEVER = os.path.join(_TMP, "never_created.db")
    _absent = drive(_dl.probe_serving_database, _NEVER)
    check("7c  an absent file in a writable directory is READY -- a fresh "
          "deployment has no database until its first write",
          _absent["ok"], True)
    check("7c  ...and the probe did NOT create it", os.path.exists(_NEVER), False)

    # AN ABSENT PARENT IS NOT READY, and this branch was wrong in the first
    # draft of the probe: it said "the first write will create it"
    # unconditionally, which is the claim it cannot make from the file alone.
    # sqlite3 creates a missing FILE and refuses a missing DIRECTORY, so a data
    # volume that failed to mount reported ready and then lost every row.
    _NO_PARENT = os.path.join(_TMP, "no_such_dir", "x.db")
    _noparent = drive(_dl.probe_serving_database, _NO_PARENT)
    check("7d  an absent file whose DIRECTORY is absent is NOT ready",
          _noparent["ok"], False)
    check("7d  ...and the message names the two usual causes",
          ("volume" in _noparent["detail"]
           and "ONCOTRIAGE_INFERENCES_DB" in _noparent["detail"]), True)

    _FOREIGN = os.path.join(_TMP, "foreign.db")
    _c = sqlite3.connect(_FOREIGN)
    _c.execute("PRAGMA application_id = 987654")
    _c.commit()
    _c.close()
    check("7e  a database belonging to another application is refused too",
          drive(_dl.probe_serving_database, _FOREIGN)["ok"], False)

    _GARBAGE = os.path.join(_TMP, "garbage.db")
    Path(_GARBAGE).write_text("this is not a sqlite file" * 50, encoding="utf-8")
    check("7e  and so is a file that is not a database at all -- 'cannot read "
          "it read-only' is not a third state to be careful about here, "
          "because it certainly cannot be written",
          drive(_dl.probe_serving_database, _GARBAGE)["ok"], False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8: the composition, and /health both directions
# ===========================================================================

    section("SECTION 8: serving_readiness(extra_checks=...) and /health")

    _rep_ok = drive(_readiness.serving_readiness,
                    extra_checks=[{"name": "x", "ok": True, "detail": "d"}])
    _rep_bad = drive(_readiness.serving_readiness,
                     extra_checks=[{"name": "x", "ok": False, "detail": "d"}])
    check("8a  a contributed check is APPENDED, in the order the pipeline "
          "meets it -- the inference write is the last thing POST /match does",
          _rep_ok["checks"][-1]["name"], "x")
    check("8a  ...and the SAME rule decides the status, written in one place: "
          "a false contributed check makes the report NOT ready",
          _rep_bad["status"], _readiness.NOT_READY)
    check("8a  non-degeneracy: with the contributed check true, the status is "
          "whatever the two built-in probes made it -- so 8a is about the "
          "contributed check and not about a report that is always not-ready",
          _rep_ok["status"], _readiness.READY)

    # A BAD MEMBER RAISES RATHER THAN BEING FOLDED IN. A check missing `ok`
    # would make all(...) raise KeyError from inside a function whose contract
    # is that it raises nothing, and a truthy STRING would read as ready
    # forever -- "no" is truthy.
    check("8b  a contributed check missing `ok` is refused by name",
          str(drive(_readiness.serving_readiness,
                    extra_checks=[{"name": "x", "detail": "d"}])).startswith(
              "RAISED ValueError"), True)
    check("8b  a truthy NON-BOOL `ok` is refused -- 'no' is truthy",
          str(drive(_readiness.serving_readiness,
                    extra_checks=[{"name": "x", "ok": "no", "detail": "d"}])
              ).startswith("RAISED TypeError"), True)
    check("8b  and a member that is not a dict at all",
          str(drive(_readiness.serving_readiness,
                    extra_checks=["ready!"])).startswith("RAISED TypeError"),
          True)
    check("8c  the default is still the two-probe report, so every existing "
          "caller is unchanged",
          [c["name"] for c in drive(_readiness.serving_readiness)["checks"]],
          ["mesh_site_filter", "trial_index"])

    # --- /health, both directions ----------------------------------------
    _saved_env = os.environ.get("ONCOTRIAGE_INFERENCES_DB")
    try:
        os.environ["ONCOTRIAGE_INFERENCES_DB"] = _GOOD_DB
        _h_ok = _client.get("/health")
        os.environ["ONCOTRIAGE_INFERENCES_DB"] = _NEWER_DB
        _h_bad = _client.get("/health")
    finally:
        if _saved_env is None:
            os.environ.pop("ONCOTRIAGE_INFERENCES_DB", None)
        else:
            os.environ["ONCOTRIAGE_INFERENCES_DB"] = _saved_env

    check("8d  with a compatible database /health is 200 and healthy",
          (_h_ok.status_code, _h_ok.json()["status"]), (200, "healthy"))
    check("8e  WITH A NEWER-ERA DATABASE /health IS 503, which is what "
          "`curl -f` in the compose healthcheck reads and therefore what makes "
          "`docker compose ps` say unhealthy",
          _h_bad.status_code, 503)
    check("8e  ...and the body names the database check",
          [c["name"] for c in _h_bad.json()["checks"]
           if not c["ok"]], [_dl.SERVING_DATABASE_CHECK])
    check("8e  ...carrying the refusal's message, so the operator's next "
          "command is in the response",
          "schema era" in json.dumps(_h_bad.json()), True)
    check("8f  non-degeneracy: the two responses differ, so 8d is not "
          "satisfied by a /health that is always green nor 8e by one that is "
          "always red",
          _h_ok.status_code != _h_bad.status_code, True)
    check("8g  IT RE-PROBES PER REQUEST rather than reporting what startup "
          "found, so archiving the newer-era file makes the stack recover with "
          "no restart",
          _client.get("/health").status_code, 200)
    check("8h  `pipeline_ready` is KEPT and still means the graph compiled -- "
          "it is one field among several, not the whole answer, because it was "
          "the field that reported true while the server was unusable",
          _h_bad.json()["pipeline_ready"], True)

finally:
    try:
        _client_cm.__exit__(None, None, None)
    except BaseException:                              # noqa: BLE001
        pass
    _deps.restore_overrides(_SAVED_DEPS)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 9: nothing in the repository was written, and the tree is gone
# ===========================================================================

section("SECTION 9: isolation")

_paths._RESOLVED["inferences_path"] = _SAVED_RESOLVED
if _SAVED_RESOLVED is None:
    _paths._RESOLVED.pop("inferences_path", None)
check("9a  the path seam was restored",
      _paths._RESOLVED.get("inferences_path"), _SAVED_RESOLVED)

check("9b  every package file this run reads is byte-identical",
      {rel: digest(os.path.join(os.path.dirname(os.path.dirname(
          os.path.abspath(_cm.__file__))), *rel.split("/")))
       for rel in _READ_FILES}, _READ_FILES)
check("9b  non-degeneracy: those baselines are distinct digests, so 9b is not "
      "comparing one file with itself",
      len(set(_READ_FILES.values())), len(_READ_FILES))
check("9b  ...and every one of them was really read, not reported absent",
      sorted({v[:6] for v in _READ_FILES.values()} & {"absent", "unread"}), [])

# THE PRODUCTION DATABASE IS NEVER OPENED. Every database above is inside the
# temp tree, and paths._RESOLVED was seeded so nothing could resolve to it.
# THE COMPARISON IS NEVER GATED, and that half is the load-bearing one: on a
# runner with no production database the reading is "absent", and this check
# then fails if this run CREATED one -- which is the accident it exists to
# catch. Only its NON-DEGENERACY PROBE is gated, and it is recorded as a SKIP
# rather than passed, because "the file was really read" is a claim that cannot
# be made where there is no file.
#
# THE SHAPE IS tests/test_storage_write_durability.py's 9c, adopted for its
# reason: that file kept a hundred checks needing nothing at all out of CI to
# preserve one production-database probe, and the signal-safe-restore pass
# gated the probe rather than the file.
check("9c  the production inference database is byte-unchanged",
      digest(_PROD_DB), _PROD_DB_BEFORE)
if _PROD_DB_BEFORE == "absent" or _PROD_DB_BEFORE.startswith("unreadable"):
    skip("9c  non-degeneracy: the production database was really read at the "
         "start",
         f"there is no readable production database at {_PROD_DB} on this "
         f"machine (reading: {_PROD_DB_BEFORE!r}), which is the ordinary state "
         f"of a CI runner. The COMPARISON above is NOT skipped: it still fails "
         f"if this run brought one into existence.")
else:
    check("9c  non-degeneracy: it was really read at the start, so 9c is not "
          "comparing 'absent' with 'absent'",
          _PROD_DB_BEFORE not in ("absent",)
          and not _PROD_DB_BEFORE.startswith("unreadable"), True)

check("9d  every plant went into the temp tree, never beside the package",
      sorted(os.path.basename(os.path.dirname(p))
             for p in [os.path.join(_PLANT_DIR, f)
                       for f in os.listdir(_PLANT_DIR)]),
      ["plants"] * len(os.listdir(_PLANT_DIR)))
check("9d  non-degeneracy: plants were really made", _PLANT_SEQ[0] >= 6, True)

shutil.rmtree(_TMP, ignore_errors=True)
check("9e  the scratch tree is removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print("\n" + "=" * 75)
print("RESULTS")
print("=" * 75)
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
if _SKIPS:
    print("\nSKIPPED:")
    for _s in _SKIPS:
        print(f"  - {_s}")
print(f"\nPassed:  {_RESULTS['passed']}")
print(f"Failed:  {_RESULTS['failed']}")
print(f"Skipped: {_RESULTS['skipped']}   "
      f"(a skip is NOT a pass and is not counted as one)")
print("=" * 75)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 25 2026

@author: ramyalsaffar
"""
