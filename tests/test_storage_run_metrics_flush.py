# Run Metrics Flush Test
#######################

"""``run_metrics``: the degradation registry, persisted per run while it runs.

WHAT WAS MISSING
----------------
``oncotriage/degradation.py`` holds twenty-odd module-level counters and has
exactly ONE reader: the block ``oncotriage/batch/runner.py:main()`` prints when a
run finishes. So the whole health record of a campaign lived in one process's
memory until it exited, with two consequences that this pass removes:

    a campaign that CRASHED printed nothing, so everything its counters held
    about the 19,000 patients it did complete was lost at exit; and

    nothing outside the process could ask a LIVE run how it was going, because
    the numbers existed only as Python objects.

WHAT THIS FILE HOLDS
--------------------
    1. THE MODULE SURFACE: the two category constants and the meta names are
       CLOSED sets, and the failure counter is on the run-end report -- which is
       the one place it can be read, since a row recording that the flush failed
       could only be written by the flush that failed.
    2. THE MIGRATION IS ADDITIVE AND IDEMPOTENT, through the real
       ``initialize_database``: fresh, run twice, and against a PRE-MIGRATION
       database with no `run_metrics` table at all, whose existing rows survive.
       The index exists and the DELETE actually uses it.
    3. A FLUSH WRITES THE TOTALS, read back out of SQLite: the right names, the
       right values, the right categories, one ``written_at`` per flush.
    4. A RE-FLUSH REPLACES RATHER THAN DUPLICATING, including the case an upsert
       keyed on (run_id, name) would get wrong -- a counter that leaves the set
       -- and it touches no other run's rows.
    5. A CLEAN RUN IS DISTINGUISHABLE IN SQL FROM A RUN THAT NEVER FLUSHED. That
       is the whole job of the meta rows and it is asserted as a query, not as a
       property of a dict.
    6. NO COUNTER KEY TEXT REACHES THE TABLE. Driven through the REAL registry
       with a counter carrying the kind of text these counters actually carry --
       a patient's recorded sex -- and with the ``snapshot()``-instead-of-
       ``totals()`` mistake shown to be REFUSED rather than stored.
    7. CONCURRENT FLUSHES ARE SAFE: MAX_WORKERS threads behind a barrier, all
       flushing one run id while another thread mutates the counters they read.
    8. A FLUSH FAILURE NEVER RAISES. Five conditions, every one of them created
       FOR REAL, each counted, each returning False.
    9. THE FINAL FLUSH DESCRIBES THE SAME INSTANT as the printed report and the
       logged summary -- behaviourally, and by an ``ast`` walk over ``main()``.
   10. THE PER-PATIENT FLUSH IS WIRED OUTSIDE THE SUCCESS BRANCH, which is the
       one thing about the wiring that could be silently wrong: a pass in which
       every patient ERRORED must still leave a health record, because errors
       are when the counters move.
   11. THE SAMPLE DATABASE GETS THE SCHEMA AND NOT THE ROWS, which is the
       ``drift_metrics`` treatment and is a decision -- a `runs` row holds a
       CONFIGURATION, equally true of any subset; a `run_metrics` row holds a
       COUNT over a population the sample does not contain.
   12. TEN CONTROLS, each shown to fire.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY, and NOT in the collision matrix: every database is a temp file, every
call is pointed at one explicitly or resolves through a seeded
``paths._RESOLVED``, and the three repository files it READS --
``oncotriage/storage/database_logger.py``, ``oncotriage/batch/runner.py`` and
``oncotriage/evaluation/sampling.py`` -- are written by neither of the suite's
two writers.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry. Every control is a
different INPUT to a pure function, a real failing condition created on disk, an
alternative implementation written out here for comparison, or an ``ast`` walk
over a source file that is parsed and never executed.

Run from terminal:
    python tests/test_storage_run_metrics_flush.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries; the candidate directory
# is the PARENT of this file's. `pip install -e .` makes it a no-op.
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

# No local model is reached here, and the flag is set before the agent is
# imported: a stand-in forgotten in a future edit becomes a named RuntimeError
# instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import contextlib
import hashlib
import io
import sqlite3
import tempfile
import threading
from collections import Counter
from pathlib import Path

from oncotriage import degradation as _degradation
from oncotriage import paths as _paths
from oncotriage.agent import filtering as _filtering
from oncotriage.batch import runner as _runner
from oncotriage.config import MAX_WORKERS
from oncotriage.evaluation import sampling as _sampling
from oncotriage.storage import database_logger as _dl


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


def fail(label, detail):
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def guarded(fn, *args, **kwargs):
    """Call into production code, turning ANY raise into a value check() fails on.

    NOT DEFENSIVE PADDING. Ten files in this suite have shipped the same defect:
    a bare call inside a ``check(...)`` argument, where a planted or reverted
    defect raises, the exception escapes while the argument is being evaluated,
    and the run reports ONE TRACEBACK where it owed a summary and N results.
    Section 8 deliberately creates failing conditions, so every driver goes
    through here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def silence(fn, *args, **kwargs):
    """Run fn with BOTH output channels captured; return its value."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        return guarded(fn, *args, **kwargs)


def loud(fn, *args, **kwargs):
    """silence(), but returning (value, captured_text) for output assertions."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
        value = guarded(fn, *args, **kwargs)
    return value, buf.getvalue()


def digest(path):
    """sha256 of a file, or the string 'absent'."""
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def at(mapping, key, default="<absent>"):
    """mapping[key] or a NAMED absence -- never a KeyError inside a check()."""
    try:
        return mapping[key]
    except (KeyError, IndexError, TypeError):
        return default


def rows(db, sql, params=()):
    """Every row of `sql` as a list of dicts. Read-only.

    A plain sqlite3.connect on an absent path CREATES the file, so a check
    written that way would bring its own subject into existence.
    """
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def tables_of(db):
    """Every table this project declared, sorted. SQLite's own are excluded."""
    return sorted(r["name"] for r in rows(
        db, "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"))


def metrics_of(db, run_id):
    """(category, name, value) for one run, sorted. The shape every check reads."""
    return sorted((r["category"], r["name"], r["value"]) for r in rows(
        db, "SELECT category, name, value FROM run_metrics WHERE run_id = ?",
        (run_id,)))


def degradation_totals_of(db, run_id):
    """{name: value} for one run's `degradation` rows only."""
    return {r["name"]: r["value"] for r in rows(
        db, "SELECT name, value FROM run_metrics "
            "WHERE run_id = ? AND category = ?",
        (run_id, _dl.RUN_METRIC_CATEGORY_DEGRADATION))}


def all_text_cells(db, table):
    """Every string value stored in `table`, for a text-leakage scan."""
    found = []
    for row in rows(db, f"SELECT * FROM {table}"):
        for value in row.values():
            if isinstance(value, str):
                found.append(value)
    return found


#------------------------------------------------------------------------------


# ===========================================================================
# ISOLATION, ESTABLISHED BEFORE ANYTHING IS WRITTEN
# ===========================================================================
#
# The production database's sha256 is taken HERE, before this file has opened
# anything, and compared at the very bottom. Taking it after the first write
# would compare a changed file with itself.
#
# paths._RESOLVED IS SEEDED -- the seam tests/test_ablation_db_isolation.py and
# tests/test_storage_run_identity.py already use. `inferences_path` so a call
# that RESOLVES rather than being told cannot reach production (the per-patient
# flush is exactly such a call, by design: it resolves the same way
# log_inference does from the same worker threads), and `checkpoint_path`
# because run_batch's append_result writes the results file there.
#
# ONCOTRIAGE_INFERENCES_DB IS EXPLICITLY CLEARED. It outranks paths.inferences_path
# at tier 2 of resolve_inference_db_path, so an operator with it exported would
# redirect this file's "production" reading to their own scratch database and
# every isolation assertion below would compare two scratch paths.

_ENV_WAS = os.environ.pop("ONCOTRIAGE_INFERENCES_DB", None)

_PRODUCTION_DB = _paths.inferences_path
_PRODUCTION_SHA_BEFORE = digest(_PRODUCTION_DB)

_TMP = tempfile.mkdtemp(prefix="oncotriage-run-metrics-")
_SCRATCH_DB = os.path.join(_TMP, "inferences.db")
_CHECKPOINT_DIR = os.path.join(_TMP, "checkpoint")
os.makedirs(_CHECKPOINT_DIR, exist_ok=True)

_PATHS_HAD_INF = "inferences_path" in _paths._RESOLVED
_PATHS_WAS_INF = _paths._RESOLVED.get("inferences_path")
_PATHS_HAD_CP = "checkpoint_path" in _paths._RESOLVED
_PATHS_WAS_CP = _paths._RESOLVED.get("checkpoint_path")

_paths._RESOLVED["inferences_path"] = _SCRATCH_DB
_paths._RESOLVED["checkpoint_path"] = _CHECKPOINT_DIR + os.sep

# The three source files this file READS. Hashed now, compared at the end:
# nothing here writes into the repository, and saying so is cheaper than being
# believed.
_DL_SRC = os.path.abspath(_dl.__file__)
_RUNNER_SRC = os.path.abspath(_runner.__file__)
_SAMPLING_SRC = os.path.abspath(_sampling.__file__)
_SHA_BEFORE = {p: digest(p) for p in (_DL_SRC, _RUNNER_SRC, _SAMPLING_SRC)}

_DL_TREE = ast.parse(Path(_DL_SRC).read_text(encoding="utf-8"))
_RUNNER_TREE = ast.parse(Path(_RUNNER_SRC).read_text(encoding="utf-8"))
_SAMPLING_TREE = ast.parse(Path(_SAMPLING_SRC).read_text(encoding="utf-8"))


def fresh_db(name):
    """An initialized scratch database at a path nothing else uses."""
    path = os.path.join(_TMP, name)
    silence(_dl.initialize_database, path)
    return path


def function_named(tree, name):
    """The FunctionDef called `name` at any nesting depth, or None."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


#------------------------------------------------------------------------------


print("=" * 78)
print("1. THE MODULE SURFACE")
print("=" * 78)
print()

# WHY FIRST. Everything below reads these constants; if the vocabularies are not
# what they claim, every later assertion is about something else.

check("the category vocabulary is exactly the two the writer uses",
      sorted(_dl.RUN_METRIC_CATEGORIES),
      sorted([_dl.RUN_METRIC_CATEGORY_DEGRADATION, _dl.RUN_METRIC_CATEGORY_META]))
check("...and the two are distinct, so `WHERE category='degradation'` excludes "
      "the meta rows rather than summing them in",
      _dl.RUN_METRIC_CATEGORY_DEGRADATION != _dl.RUN_METRIC_CATEGORY_META, True)
check("the meta vocabulary is closed and both members are named",
      sorted(_dl.RUN_METRIC_META_NAMES),
      sorted([_dl.RUN_METRIC_META_COUNTERS_REGISTERED,
              _dl.RUN_METRIC_META_COUNTERS_NONZERO]))

# THE COUNTER IS ON THE RUN-END REPORT, and that is not decoration here the way
# it is for its neighbours: a run_metrics row recording that the flush failed
# could only be written by the flush that just failed, so the console block is
# the ONLY place this number can be read.
check("RUN_METRICS_FLUSH_FAILURES is registered on the degradation report",
      "RUN_METRICS_FLUSH_FAILURES" in _degradation.registered_names(), True)
check("...and so is SNAPSHOT_CONTENTION, which is what says a concurrent "
      "registry read was retaken or abandoned rather than silently partial",
      "SNAPSHOT_CONTENTION" in _degradation.registered_names(), True)

# CONTROL 1: the membership test discriminates -- it is not satisfied by any
# string at all.
check("CONTROL: a name that is not registered is reported as not registered",
      "NOT_A_REAL_COUNTER" in _degradation.registered_names(), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. THE MIGRATION IS ADDITIVE AND IDEMPOTENT")
print("=" * 78)
print()

_FRESH = fresh_db("fresh.db")

check("a fresh database carries all five tables",
      tables_of(_FRESH),
      ["drift_metrics", "inferences", "run_metrics", "runs", "trial_matches"])

check("run_metrics carries exactly the narrow shape, plus its id",
      sorted(r["name"] for r in rows(_FRESH, "PRAGMA table_info(run_metrics)")),
      sorted(["id", "run_id", "category", "name", "value", "written_at"]))

# THE INDEX IS NOT DECORATION: the DELETE runs once per completed patient
# against a table that accumulates across every run the file has ever held.
check("the run_id index exists",
      [r["name"] for r in rows(
          _FRESH, "SELECT name FROM sqlite_master WHERE type='index' "
                  "AND tbl_name='run_metrics' AND name NOT LIKE 'sqlite_%'")],
      ["idx_run_metrics_run_id"])
check("...and the DELETE the flush issues actually USES it -- an index nothing "
      "plans against is a write cost with no read benefit",
      any("idx_run_metrics_run_id" in str(r)
          for r in rows(_FRESH, "EXPLAIN QUERY PLAN "
                                "DELETE FROM run_metrics WHERE run_id = 1")),
      True)

# --- run it twice -----------------------------------------------------------
_schema_before = sorted(r["sql"] for r in rows(
    _FRESH, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"))
silence(_dl.initialize_database, _FRESH)
check("initializing an initialized database changes no schema object",
      sorted(r["sql"] for r in rows(
          _FRESH, "SELECT sql FROM sqlite_master WHERE sql IS NOT NULL")),
      _schema_before)
check("...and creates no second run_metrics table",
      tables_of(_FRESH).count("run_metrics"), 1)

# --- a PRE-MIGRATION database ----------------------------------------------
# Built by hand with the four tables that existed before this pass and a row in
# one of them, so "additive" is a statement about data rather than about DDL.
_LEGACY = os.path.join(_TMP, "legacy.db")
_conn = sqlite3.connect(_LEGACY)
_conn.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, "
              "invocation_source TEXT NOT NULL)")
_conn.execute("INSERT INTO runs (started_at, status, invocation_source) "
              "VALUES ('2026-01-01T00:00:00', 'FINISHED', 'legacy')")
_conn.commit()
_conn.close()

check("the pre-migration database has no run_metrics table (non-degeneracy: "
      "without this the next check could pass against a table that was always "
      "there)",
      "run_metrics" in tables_of(_LEGACY), False)
_legacy_rows_before = rows(_LEGACY, "SELECT * FROM runs")
silence(_dl.initialize_database, _LEGACY)
check("...initializing it adds the table",
      "run_metrics" in tables_of(_LEGACY), True)
check("...and the rows it already held survive untouched",
      rows(_LEGACY, "SELECT id, status, invocation_source FROM runs"),
      [{"id": r["id"], "status": r["status"],
        "invocation_source": r["invocation_source"]}
       for r in _legacy_rows_before])
check("...and the new table is empty, not seeded with anything invented",
      rows(_LEGACY, "SELECT COUNT(*) AS n FROM run_metrics")[0]["n"], 0)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. A FLUSH WRITES THE TOTALS")
print("=" * 78)
print()

_DB3 = fresh_db("write.db")
_RID3 = silence(_dl.start_run_record, "batch_runner", db_path=_DB3)
check("a run row was opened (non-degeneracy: every check below is keyed on it)",
      isinstance(_RID3, int), True)

_TOTALS3 = {"AGE_PARSE_FAILURES": 7, "QDRANT_RETRIES": 2,
            "REFUSALS_OBSERVED": 1}
check("the flush reports success",
      silence(_dl.flush_run_metrics, _RID3, _TOTALS3, 24, db_path=_DB3), True)

check("every counter landed with its own total, read back out of SQLite",
      degradation_totals_of(_DB3, _RID3), _TOTALS3)
check("...under the degradation category",
      sorted({r["category"] for r in rows(
          _DB3, "SELECT category FROM run_metrics WHERE run_id=? AND name IN "
                "('AGE_PARSE_FAILURES','QDRANT_RETRIES','REFUSALS_OBSERVED')",
          (_RID3,))}),
      [_dl.RUN_METRIC_CATEGORY_DEGRADATION])

_meta3 = {r["name"]: r["value"] for r in rows(
    _DB3, "SELECT name, value FROM run_metrics WHERE run_id=? AND category=?",
    (_RID3, _dl.RUN_METRIC_CATEGORY_META))}
check("the meta rows say how many counters were consulted",
      at(_meta3, _dl.RUN_METRIC_META_COUNTERS_REGISTERED), 24)
check("...and how many of them were non-zero",
      at(_meta3, _dl.RUN_METRIC_META_COUNTERS_NONZERO), len(_TOTALS3))
check("...and there are exactly the two of them, so the vocabulary is closed "
      "in the data as well as in the constants",
      sorted(_meta3), sorted(_dl.RUN_METRIC_META_NAMES))

_stamps3 = {r["written_at"] for r in rows(
    _DB3, "SELECT written_at FROM run_metrics WHERE run_id=?", (_RID3,))}
check("one flush stamps one written_at across every row it writes -- a reader "
      "asking how current the picture is gets one answer, not five",
      len(_stamps3), 1)
check("...and it is an ISO timestamp, not a float or an empty string",
      bool(next(iter(_stamps3))[:4].isdigit() and "T" in next(iter(_stamps3))),
      True)

check("no flush failure was counted on the happy path (non-degeneracy for "
      "section 8, which asserts the same counter DOES move)",
      sum(_dl.RUN_METRICS_FLUSH_FAILURES.values()), 0)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. A RE-FLUSH REPLACES RATHER THAN DUPLICATING")
print("=" * 78)
print()

# A second run in the same file, written FIRST, so every assertion below about
# "only this run's rows moved" has something to be wrong about.
_RID4_OTHER = silence(_dl.start_run_record, "batch_runner", db_path=_DB3)
silence(_dl.flush_run_metrics, _RID4_OTHER, {"SEX_UNKNOWN_KEPT": 99}, 24,
        db_path=_DB3)
_other_before = metrics_of(_DB3, _RID4_OTHER)
check("the second run has rows of its own (non-degeneracy)",
      len(_other_before) > 0, True)

_TOTALS4 = {"AGE_PARSE_FAILURES": 11, "QDRANT_RETRIES": 2,
            "REFUSALS_OBSERVED": 1, "MALFORMED_EVALUATION_ENTRIES": 4}
silence(_dl.flush_run_metrics, _RID4_OTHER, {"SEX_UNKNOWN_KEPT": 99}, 24,
        db_path=_DB3)
silence(_dl.flush_run_metrics, _RID3, _TOTALS4, 24, db_path=_DB3)

check("the re-flush REPLACED the run's picture rather than appending to it",
      degradation_totals_of(_DB3, _RID3), _TOTALS4)
check("...so no name appears twice",
      len(metrics_of(_DB3, _RID3)), len(_TOTALS4) + len(_dl.RUN_METRIC_META_NAMES))
check("...and the meta count moved with it",
      [r["value"] for r in rows(
          _DB3, "SELECT value FROM run_metrics WHERE run_id=? AND name=?",
          (_RID3, _dl.RUN_METRIC_META_COUNTERS_NONZERO))],
      [len(_TOTALS4)])
check("...and the OTHER run's rows are untouched -- the DELETE is scoped to "
      "one run_id",
      metrics_of(_DB3, _RID4_OTHER), _other_before)

# --- the case an upsert would get wrong ------------------------------------
# THE ONE PLACE DELETE-AND-INSERT AND UPSERT-PER-NAME DIFFER. `totals()` drops
# zero counters, so a counter that is cleared LEAVES THE SET. An upsert keyed on
# (run_id, name) replaces the names the new flush carries and leaves the
# departed one behind, presenting a stale non-zero total as current.
_SHRUNK = {"AGE_PARSE_FAILURES": 11}
silence(_dl.flush_run_metrics, _RID3, _SHRUNK, 24, db_path=_DB3)
check("a counter that leaves the set leaves no residue behind",
      degradation_totals_of(_DB3, _RID3), _SHRUNK)

# CONTROL 2: the same shrink applied by an upsert-per-name -- the algorithm that
# was rejected -- written out here rather than argued, and shown to leave the
# residue. It writes into its own run id so nothing above is disturbed.
_RID4_UPSERT = silence(_dl.start_run_record, "batch_runner", db_path=_DB3)


def _upsert_flush(db, run_id, totals):
    """The REJECTED algorithm: replace only the names this flush carries."""
    conn = sqlite3.connect(db)
    try:
        for name, value in totals.items():
            updated = conn.execute(
                "UPDATE run_metrics SET value = ? WHERE run_id = ? AND name = ?",
                (value, run_id, name)).rowcount
            if not updated:
                conn.execute(
                    "INSERT INTO run_metrics "
                    "(run_id, category, name, value, written_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (run_id, _dl.RUN_METRIC_CATEGORY_DEGRADATION, name, value,
                     "control"))
        conn.commit()
    finally:
        conn.close()


_upsert_flush(_DB3, _RID4_UPSERT, {"AGE_PARSE_FAILURES": 11,
                                   "QDRANT_RETRIES": 2})
_upsert_flush(_DB3, _RID4_UPSERT, _SHRUNK)
check("CONTROL: an upsert-per-name leaves the departed counter behind, which "
      "is why the flush deletes the run's rows first",
      degradation_totals_of(_DB3, _RID4_UPSERT),
      {"AGE_PARSE_FAILURES": 11, "QDRANT_RETRIES": 2})


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. A CLEAN RUN IS DISTINGUISHABLE FROM A RUN THAT NEVER FLUSHED")
print("=" * 78)
print()

# THIS IS THE REQUIREMENT "SILENCE MUST NEVER LOOK LIKE HEALTH", AS A QUERY.
# `totals()` drops every zero counter, so a run that degraded in no way
# contributes no `degradation` rows -- which is exactly what a run whose
# flushing was never wired up contributes, and exactly what a run that crashed
# before its first flush contributes.

_DB5 = fresh_db("clean.db")
_RID_CLEAN = silence(_dl.start_run_record, "batch_runner", db_path=_DB5)
_RID_NEVER = silence(_dl.start_run_record, "batch_runner", db_path=_DB5)

check("a clean flush reports success", silence(
    _dl.flush_run_metrics, _RID_CLEAN, {}, 24, db_path=_DB5), True)

check("a clean run writes the meta rows and nothing else",
      metrics_of(_DB5, _RID_CLEAN),
      sorted([(_dl.RUN_METRIC_CATEGORY_META,
               _dl.RUN_METRIC_META_COUNTERS_REGISTERED, 24),
              (_dl.RUN_METRIC_CATEGORY_META,
               _dl.RUN_METRIC_META_COUNTERS_NONZERO, 0)]))
check("the run that never flushed has no rows at all",
      metrics_of(_DB5, _RID_NEVER), [])

# The query a reader actually writes. Both runs return zero `degradation` rows;
# only the meta row separates them.
_verdicts = {r["id"]: r["verdict"] for r in rows(_DB5, """
    SELECT r.id AS id,
           CASE WHEN m.value IS NULL THEN 'never flushed'
                WHEN m.value = 0     THEN 'measured clean'
                ELSE 'measured degraded' END AS verdict
      FROM runs r
      LEFT JOIN run_metrics m
             ON m.run_id = r.id AND m.category = ?
            AND m.name = ?
""", (_dl.RUN_METRIC_CATEGORY_META, _dl.RUN_METRIC_META_COUNTERS_NONZERO))}
check("...and one SQL query separates the two, which is the whole job of the "
      "meta row",
      (at(_verdicts, _RID_CLEAN), at(_verdicts, _RID_NEVER)),
      ("measured clean", "never flushed"))

# CONTROL 3: the query is not a constant -- a degraded run reads as degraded.
silence(_dl.flush_run_metrics, _RID_NEVER, {"QDRANT_RETRIES": 1}, 24,
        db_path=_DB5)
_verdicts2 = {r["id"]: r["verdict"] for r in rows(_DB5, """
    SELECT r.id AS id,
           CASE WHEN m.value IS NULL THEN 'never flushed'
                WHEN m.value = 0     THEN 'measured clean'
                ELSE 'measured degraded' END AS verdict
      FROM runs r
      LEFT JOIN run_metrics m
             ON m.run_id = r.id AND m.category = ?
            AND m.name = ?
""", (_dl.RUN_METRIC_CATEGORY_META, _dl.RUN_METRIC_META_COUNTERS_NONZERO))}
check("CONTROL: the same query reports a degraded run as degraded",
      at(_verdicts2, _RID_NEVER), "measured degraded")


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("6. NO COUNTER KEY TEXT REACHES THE TABLE")
print("=" * 78)
print()

# WHY THIS MATTERS ENOUGH TO BE ITS OWN SECTION. `degradation.snapshot()` is
# {counter name: {KEY: count}} and those KEYS carry third-party and clinical
# text -- SEX_UNKNOWN_KEPT is keyed by the patient's recorded sex,
# M_CATEGORY_UNREADABLE by a capped copy of an observation's display, and
# LAB_UNIT_DEGRADATIONS by a lab name and a unit. `run_metrics` is DURABLE and
# run-keyed, which is the kind of record LOGGABLE_FIELDS exists to keep that
# text out of. `totals()` is the safe projection, and this section drives the
# REAL registry rather than a fabricated dict.

_DB6 = fresh_db("leak.db")
_RID6 = silence(_dl.start_run_record, "batch_runner", db_path=_DB6)

_SECRET = "unconverted:Sodium [Moles/volume] in Serum::mmol/L"
_filtering.SEX_UNKNOWN_KEPT.clear()
_filtering.SEX_UNKNOWN_KEPT[_SECRET] += 3
try:
    _snap6 = _degradation.snapshot()
    check("the real registry does carry the text (non-degeneracy: without this "
          "the scan below would find nothing because there was nothing)",
          _SECRET in at(_snap6, "SEX_UNKNOWN_KEPT", {}), True)

    check("flush_health writes it as a total",
          silence(_runner.flush_health, _RID6, snapshot=_snap6, db_path=_DB6),
          True)
finally:
    _filtering.SEX_UNKNOWN_KEPT.clear()

check("...and the total is the SUM of the counter's keys, which is what "
      "totals() means",
      at(degradation_totals_of(_DB6, _RID6), "SEX_UNKNOWN_KEPT"), 3)

_cells6 = all_text_cells(_DB6, "run_metrics")
check("no cell of run_metrics contains the counter's KEY",
      [c for c in _cells6 if _SECRET in c], [])
check("...and no cell contains any fragment of it either -- 'Sodium' is the "
      "half a substring check would miss",
      [c for c in _cells6 if "Sodium" in c or "mmol" in c], [])

# THE MECHANICAL GUARANTEE, not the promise. Every `name` written is a Python
# identifier; no key any counter in this project produces is one.
check("every name in the table is a Python identifier, which counter NAMES are "
      "by construction and counter KEYS never are",
      sorted({r["name"] for r in rows(_DB6, "SELECT name FROM run_metrics")
              if not r["name"].isidentifier()}), [])
check("CONTROL: the offending key really would have failed that test",
      _SECRET.isidentifier(), False)

# THE IDENTIFIER GUARD, DRIVEN DIRECTLY. The nested-value refusal below covers a
# caller who passed snapshot(); this covers the OTHER shape -- a flat mapping
# whose NAMES are counter keys rather than counter names, which is what a
# hand-rolled "flatten the snapshot" helper would produce, and which would put
# clinical text in a `name` column with an int beside it. It is asserted
# separately because a revert that removed only this guard was MISSED by every
# check above: `totals()` produces identifier names, so no assertion about the
# TABLE can see a guard that has stopped guarding.
_dl.RUN_METRICS_FLUSH_FAILURES.clear()
_dl._RUN_METRIC_SHAPE_ANNOUNCED.clear()
_flat = {f"SEX_UNKNOWN_KEPT::{_SECRET}": 3}
check("a flat mapping whose NAMES are counter keys is refused",
      silence(_dl.flush_run_metrics, _RID6, _flat, 24, db_path=_DB6), False)
check("...counted under its own key",
      at(dict(_dl.RUN_METRICS_FLUSH_FAILURES), "flush:non_identifier_name"), 1)
check("...and none of that text reached the table",
      [c for c in all_text_cells(_DB6, "run_metrics") if _SECRET in c], [])
check("...while a well-formed name carrying the same value is ACCEPTED, so the "
      "guard rejects the shape rather than every flush (non-degeneracy)",
      silence(_dl.flush_run_metrics, _RID6, {"SEX_UNKNOWN_KEPT": 3}, 24,
              db_path=_DB6), True)
_dl.RUN_METRICS_FLUSH_FAILURES.clear()
_dl._RUN_METRIC_SHAPE_ANNOUNCED.clear()

# CONTROL 4: the scanner discriminates. The same text inserted DIRECTLY is found
# -- so "no cell contains it" is a statement about the writer, not about a scan
# that cannot see anything.
_conn = sqlite3.connect(_DB6)
_conn.execute("INSERT INTO run_metrics (run_id, category, name, value, written_at) "
              "VALUES (?,?,?,?,?)", (-1, "control", _SECRET, 1, "control"))
_conn.commit()
_conn.close()
check("CONTROL: the leakage scan FINDS the same text when it is really there",
      [c for c in all_text_cells(_DB6, "run_metrics") if _SECRET in c],
      [_SECRET])
_conn = sqlite3.connect(_DB6)
_conn.execute("DELETE FROM run_metrics WHERE run_id = -1")
_conn.commit()
_conn.close()

# --- the mistake that would carry the text ---------------------------------
# A caller reaching for snapshot() instead of totals() is the one shape that
# would put clinical text in. It is REFUSED at the writer rather than trusted at
# the call site.
_dl.RUN_METRICS_FLUSH_FAILURES.clear()
_dl._RUN_METRIC_SHAPE_ANNOUNCED.clear()
_nested, _text6 = loud(_dl.flush_run_metrics, _RID6,
                       {"SEX_UNKNOWN_KEPT": {_SECRET: 3}}, 24, db_path=_DB6)
check("the snapshot()-instead-of-totals() mistake is REFUSED", _nested, False)
check("...and counted under its own key, so it is diagnosable rather than "
      "arriving as a generic type error",
      at(dict(_dl.RUN_METRICS_FLUSH_FAILURES), "flush:nested_value"), 1)
check("...and the run's existing rows are left exactly as they were -- a "
      "refused flush must not destroy the last good picture",
      at(degradation_totals_of(_DB6, _RID6), "SEX_UNKNOWN_KEPT"), 3)
check("...and the offending value went to the CONSOLE, which is transient",
      "SEX_UNKNOWN_KEPT" in _text6, True)

# ONCE PER PROCESS. The flush runs once per completed patient, so a caller
# passing the wrong mapping would otherwise print an identical line 22,000
# times; the counter keeps counting either way.
_, _text6b = loud(_dl.flush_run_metrics, _RID6,
                  {"SEX_UNKNOWN_KEPT": {_SECRET: 3}}, 24, db_path=_DB6)
# THE PROBE IS THE CONSOLE LINE'S OWN WORDING, NOT THE WORD "run_metrics".
# The first version searched for that word and FAILED -- because the structured
# log record the same call emits carries event="run_metrics_flush_refused",
# which the same capture sees. A probe that matches the thing it is not about
# is a check that reports the wrong subject.
check("the console line is printed once per process, not once per patient",
      "deliberately not stored" in _text6b, False)
check("...and the first one really did carry it (non-degeneracy: without this "
      "the check above passes for a line that was never printed at all)",
      "deliberately not stored" in _text6, True)
check("...while the counter keeps counting, so the total stays honest",
      at(dict(_dl.RUN_METRICS_FLUSH_FAILURES), "flush:nested_value"), 2)
_dl.RUN_METRICS_FLUSH_FAILURES.clear()
_dl._RUN_METRIC_SHAPE_ANNOUNCED.clear()


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("7. CONCURRENT FLUSHES ARE SAFE")
print("=" * 78)
print()

# THE FLUSH IS CALLED FROM _on_done, A DONE-CALLBACK, so MAX_WORKERS threads can
# reach it at once for one run id. Without _WRITE_LOCK, two flushes interleave
# their DELETE and their INSERT and the loser's rows are wiped after they were
# written. Driven with real threads behind a barrier, and with a second thread
# mutating the counters they read -- which is the OTHER race this introduced,
# since snapshot() had never before been called while a worker was writing.

_DB7 = fresh_db("concurrent.db")
_RID7 = silence(_dl.start_run_record, "batch_runner", db_path=_DB7)

_THREADS = max(2, MAX_WORKERS)
_ROUNDS = 12
_barrier = threading.Barrier(_THREADS + 1)
_errors = []
_stop = threading.Event()

_filtering.SEX_UNKNOWN_KEPT.clear()


def _flusher():
    try:
        _barrier.wait()
        for _ in range(_ROUNDS):
            _runner.flush_health(_RID7, db_path=_DB7)
    except BaseException as exc:                        # noqa: BLE001
        _errors.append(f"flusher: {type(exc).__name__}: {exc}")


_MUTATOR_KEY_CAP = 4000
_mutator_keys = {"n": 0}


def _mutator():
    """Insert NEW keys while the flushers read the registry.

    A NEW key is the hazard rather than a bigger count: CPython's dict iterator
    raises when the dict's SIZE changes under it, and `counter[k] += 1` on an
    existing key does not change the size.

    IT IS CAPPED, AND THE CAP IS A CORRECTION RATHER THAN A TIDINESS. The first
    version inserted without bound for as long as the flushers ran, so the
    counter grew into the millions and every snapshot copied all of it -- the
    file did not finish. A harness that starves the thing it is measuring is
    measuring the harness. Four thousand distinct keys is far more churn than
    any real run produces and copies in microseconds; after the cap it keeps
    incrementing an EXISTING key, so there is still a live writer to race.
    """
    try:
        _barrier.wait()
        while not _stop.is_set():
            n = _mutator_keys["n"]
            if n < _MUTATOR_KEY_CAP:
                _filtering.SEX_UNKNOWN_KEPT[f"key-{n}"] += 1
                _mutator_keys["n"] = n + 1
            else:
                _filtering.SEX_UNKNOWN_KEPT["key-0"] += 1
    except BaseException as exc:                        # noqa: BLE001
        _errors.append(f"mutator: {type(exc).__name__}: {exc}")


_workers = [threading.Thread(target=_flusher) for _ in range(_THREADS - 1)]
_workers.append(threading.Thread(target=_mutator))
buf = io.StringIO()
with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
    for t in _workers:
        t.start()
    _barrier.wait()
    for t in _workers[:-1]:
        t.join()
    _stop.set()
    _workers[-1].join()

check("nothing raised anywhere in the pool", _errors, [])
check("it really ran with more than one flushing thread (non-degeneracy: one "
      "thread satisfies every assertion below)", _THREADS - 1 > 1, True)
check("...and the mutator really was inserting new keys under them, which is "
      "the hazard (non-degeneracy: a writer that never inserted one would make "
      "every check below vacuous)",
      _mutator_keys["n"] > 1, True)

_final7 = metrics_of(_DB7, _RID7)
_names7 = [name for _, name, _ in _final7]
check("no name appears twice -- the delete-and-insert was atomic with respect "
      "to every other flusher",
      sorted(set(_names7)), sorted(_names7))
check("...and the picture is coherent: exactly the meta rows plus the counters "
      "that were non-zero",
      sorted(n for c, n, _ in _final7 if c == _dl.RUN_METRIC_CATEGORY_META),
      sorted(_dl.RUN_METRIC_META_NAMES))
check("...written under exactly one written_at, so the surviving rows all come "
      "from ONE flush rather than being a mixture of several",
      len({r["written_at"] for r in rows(
          _DB7, "SELECT written_at FROM run_metrics WHERE run_id=?", (_RID7,))}),
      1)
check("no flush failed", sum(_dl.RUN_METRICS_FLUSH_FAILURES.values()), 0)
check("the registry read survived the concurrent writer without ABANDONING a "
      "counter (a retry is fine and is recorded; an abandonment would mean a "
      "counter was missing from a flush)",
      [k for k in _degradation.SNAPSHOT_CONTENTION if k.endswith(":abandoned")],
      [])
_filtering.SEX_UNKNOWN_KEPT.clear()

# --- the registry copy, DETERMINISTICALLY -----------------------------------
# THE THREADED CHECK ABOVE IS PROBABILISTIC AND THIS ONE IS NOT, which is why
# both are here. Reverting the copy to a comprehension over `counter.items()`
# -- the form that shipped first, and the one that CAN be interrupted between
# two items -- was NOT caught by the thread pool: four retries usually recover.
# It is caught here, with a counter that mutates ITSELF while its `items()` view
# is walked, so the failure is certain rather than timing-dependent.
#
# THE POINT OF `dict.copy(counter)` IS THAT IT NEVER CALLS `items()`. It is one
# call into CPython's PyDict_Copy: no Python bytecode runs inside it, so no
# other thread can be scheduled part-way through and no resize can be observed.


class _GrowsWhileRead(Counter):
    """A Counter that inserts a key while its `items()` view is being walked.

    Stands in for the real hazard, deterministically: a worker thread adding a
    key the registry has never seen -- a lab unit with no conversion, an
    exception type not met before -- in the window between two steps of a
    consumer's iteration.
    """

    def items(self):
        for index, pair in enumerate(dict.items(self)):
            if index == 0:
                dict.__setitem__(self, "inserted-under-you", 1)
            yield pair


_grows = _GrowsWhileRead({"a": 1, "b": 2, "c": 3})
_degradation.SNAPSHOT_CONTENTION.clear()
check("the shipped copy reads a counter that grows under it, in full",
      silence(_degradation._copy_counter, "GROWS", _grows),
      {"a": 1, "b": 2, "c": 3})
check("...without a retry or an abandonment, because it never iterates in "
      "Python at all",
      dict(_degradation.SNAPSHOT_CONTENTION), {})

# CONTROL 6: the comprehension form -- what shipped first -- really does raise
# on the same object, so the check above is about the implementation rather
# than about a stand-in that cannot misbehave.
_grows2 = _GrowsWhileRead({"a": 1, "b": 2, "c": 3})
_raced = guarded(lambda: {k: v for k, v in _grows2.items() if v})
check("CONTROL: a comprehension over the same counter raises",
      isinstance(_raced, dict) and "RuntimeError" in str(_raced.get("__raised__", "")),
      True)
_degradation.SNAPSHOT_CONTENTION.clear()

# --- the lock, structurally -------------------------------------------------
# A BEHAVIOURAL CHECK CANNOT SEE THIS ONE, and that was measured rather than
# assumed: stripping `with _WRITE_LOCK` from the flush and re-running everything
# above loses nothing, because with sqlite3's default isolation level the DELETE
# opens a transaction that the executemany and the commit finish, and SQLite's
# own file locking already refuses a second write transaction while one is open.
#
# THE LOCK IS STILL REQUIRED AND THE REQUIREMENT IS STRUCTURAL: the module's
# invariant is "every database statement in this file is issued under
# _WRITE_LOCK", and that invariant is what makes `isolation_level=None` -- one
# keyword on _open_connection, and a plausible future edit -- a safe change
# rather than a silently destructive one. So it is pinned where it lives.
_flush_fn = function_named(_DL_TREE, "flush_run_metrics")
check("flush_run_metrics was found (non-degeneracy for the two checks below)",
      _flush_fn is not None, True)
_sql_calls = [n for n in ast.walk(_flush_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
              and n.func.attr in ("execute", "executemany")] if _flush_fn else []
_locked_sql = [n for n in ast.walk(_flush_fn)
               if isinstance(n, ast.With)
               and any(isinstance(i.context_expr, ast.Name)
                       and i.context_expr.id == "_WRITE_LOCK" for i in n.items)
               for m in ast.walk(n)
               if isinstance(m, ast.Call) and isinstance(m.func, ast.Attribute)
               and m.func.attr in ("execute", "executemany")] if _flush_fn else []
check("it issues more than one SQL statement (non-degeneracy: a function with "
      "none would satisfy the next check for free)", len(_sql_calls) > 1, True)
check("...and EVERY one of them is inside `with _WRITE_LOCK:`",
      len(_locked_sql), len(_sql_calls))

# CONTROL 7: the duplicate check discriminates. Rows appended without the DELETE
# -- the shape a non-replacing writer produces -- are caught by the same
# assertion.
_conn = sqlite3.connect(_DB7)
_conn.execute("INSERT INTO run_metrics (run_id, category, name, value, written_at) "
              "VALUES (?,?,?,?,?)",
              (_RID7, _dl.RUN_METRIC_CATEGORY_META,
               _dl.RUN_METRIC_META_COUNTERS_NONZERO, 999, "control"))
_conn.commit()
_conn.close()
_dupe = [name for _, name, _ in metrics_of(_DB7, _RID7)]
check("CONTROL: the duplicate check FAILS on a table that really has one",
      sorted(set(_dupe)) == sorted(_dupe), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("8. A FLUSH FAILURE NEVER RAISES")
print("=" * 78)
print()

# IT RUNS INSIDE A DONE-CALLBACK, ON A WORKER THREAD, AFTER THE PATIENT HAS COST
# A LIVE STAGE 5 CALL. An exception there is swallowed by concurrent.futures and
# logged to a logger nothing in this project reads. Every condition below is
# created FOR REAL -- no source is patched and nothing is exec'd.

_dl.RUN_METRICS_FLUSH_FAILURES.clear()
_dl._RUN_METRIC_SHAPE_ANNOUNCED.clear()
_DB8 = fresh_db("failures.db")
_RID8 = silence(_dl.start_run_record, "batch_runner", db_path=_DB8)

# --- (a) no run id ----------------------------------------------------------
check("8a  a flush with no run id returns False and does not raise",
      silence(_dl.flush_run_metrics, None, {"QDRANT_RETRIES": 1}, 24,
              db_path=_DB8), False)
check("8a  ...and is counted",
      at(dict(_dl.RUN_METRICS_FLUSH_FAILURES), "flush:no_run_id"), 1)

# --- (b) totals that are not a mapping -------------------------------------
check("8b  a totals argument that is not a mapping is refused",
      silence(_dl.flush_run_metrics, _RID8, ["QDRANT_RETRIES", 1], 24,
              db_path=_DB8), False)
check("8b  ...keyed by the type it got, so the diagnosis names the mistake",
      at(dict(_dl.RUN_METRICS_FLUSH_FAILURES), "flush:not_a_mapping:list"), 1)

# --- (c) a value that is not an integer ------------------------------------
check("8c  a non-integer total is refused",
      silence(_dl.flush_run_metrics, _RID8, {"QDRANT_RETRIES": "3"}, 24,
              db_path=_DB8), False)
check("8c  ...and a bool is not an integer here, because a total of 1 that was "
      "really a True is a number nobody counted",
      silence(_dl.flush_run_metrics, _RID8, {"QDRANT_RETRIES": True}, 24,
              db_path=_DB8), False)
check("8c  ...both counted under the same key",
      at(dict(_dl.RUN_METRICS_FLUSH_FAILURES), "flush:non_integer_value"), 2)

# --- (d) a bad registered count --------------------------------------------
check("8d  a non-integer counters_registered is refused, because the meta row "
      "exists to say how much was measured",
      silence(_dl.flush_run_metrics, _RID8, {"QDRANT_RETRIES": 1}, None,
              db_path=_DB8), False)
check("8d  ...and so is a negative one",
      silence(_dl.flush_run_metrics, _RID8, {"QDRANT_RETRIES": 1}, -1,
              db_path=_DB8), False)

# --- (e) a database that cannot be written ---------------------------------
# A REAL condition: a path whose parent directory does not exist.
_BAD_DB = os.path.join(_TMP, "no-such-directory", "nested.db")
check("8e  an unwritable database path returns False and does not raise",
      silence(_dl.flush_run_metrics, _RID8, {"QDRANT_RETRIES": 1}, 24,
              db_path=_BAD_DB), False)
check("8e  ...counted under its exception type",
      sorted(k for k in _dl.RUN_METRICS_FLUSH_FAILURES
             if k.startswith("flush:") and "Error" in k) != [], True)
check("8e  ...and nothing was created at the bad path (non-degeneracy)",
      os.path.exists(_BAD_DB), False)

# CONTROL 8: the condition really is fatal. A raw sqlite3 call in the same place
# raises, so "did not raise" is a statement about the flush rather than about a
# condition that was never a problem.
_raw = guarded(lambda: sqlite3.connect(_BAD_DB).execute("SELECT 1"))
check("CONTROL: raw sqlite3 in the same condition DOES raise",
      isinstance(_raw, dict) and "__raised__" in _raw, True)

# --- (f) the table is gone --------------------------------------------------
# The other real fatal condition: a database whose run_metrics table was
# dropped. _ensure_database will not recreate it, because this process already
# initialized that path -- which is exactly the shape a partially-migrated file
# has.
_DB8F = fresh_db("dropped.db")
_RID8F = silence(_dl.start_run_record, "batch_runner", db_path=_DB8F)
_conn = sqlite3.connect(_DB8F)
_conn.execute("DROP TABLE run_metrics")
_conn.commit()
_conn.close()
check("8f  a missing run_metrics table returns False and does not raise",
      silence(_dl.flush_run_metrics, _RID8F, {"QDRANT_RETRIES": 1}, 24,
              db_path=_DB8F), False)
check("8f  ...and the run row it belongs to is untouched, so the campaign's "
      "own record survives a health-record failure",
      [r["status"] for r in rows(_DB8F, "SELECT status FROM runs WHERE id=?",
                                 (_RID8F,))],
      [_dl.RUN_RECORD_STATUS_RUNNING])

check("every failure above was counted; a silent return would be the defect "
      "this whole file is about",
      sum(_dl.RUN_METRICS_FLUSH_FAILURES.values()) >= 8, True)


# --- (g) the REGISTRY itself cannot be read ---------------------------------
# THE HALF flush_run_metrics' OWN CONTRACT DOES NOT COVER. Its guarantee starts
# at its arguments, and runner.flush_health builds those by calling
# degradation.snapshot(), degradation.totals() and registered_names() -- three
# calls that happen BEFORE it, in a done-callback on a worker thread, where a
# raise is swallowed by concurrent.futures and logged where nothing here reads
# it. "The flush never raises" has to mean the call site, not one frame of it.
#
# DRIVEN BY REBINDING degradation.snapshot INSIDE try/finally, with the restore
# asserted BY IDENTITY. Nothing is exec'd and no source is patched.
_dl.RUN_METRICS_FLUSH_FAILURES.clear()
_REAL_SNAPSHOT = _degradation.snapshot


def _exploding_snapshot():
    raise RuntimeError("the registry could not be read")


try:
    _degradation.snapshot = _exploding_snapshot
    check("8g  a registry that cannot be read returns False and does not raise",
          silence(_runner.flush_health, _RID8, db_path=_DB8), False)
finally:
    _degradation.snapshot = _REAL_SNAPSHOT
check("8g  ...the real snapshot was restored, by identity",
      _degradation.snapshot is _REAL_SNAPSHOT, True)
check("8g  ...and it is counted under a key naming WHICH half failed, so one "
      "counter still answers 'did this run's health record land'",
      at(dict(_dl.RUN_METRICS_FLUSH_FAILURES),
         "flush:registry_read:RuntimeError"), 1)
check("8g  CONTROL: with the real snapshot back, the same call succeeds -- so "
      "the refusal above was the rebinding and not the database",
      silence(_runner.flush_health, _RID8, db_path=_DB8), True)
_dl.RUN_METRICS_FLUSH_FAILURES.clear()
_dl._RUN_METRIC_SHAPE_ANNOUNCED.clear()
_dl.RUN_METRICS_FLUSH_FAILURES.clear()
_dl._RUN_METRIC_SHAPE_ANNOUNCED.clear()


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("9. THE FINAL FLUSH DESCRIBES THE SAME INSTANT AS THE REPORT")
print("=" * 78)
print()

# main() takes ONE snapshot and hands it to three consumers: the structured
# event, the persisted rows and the printed block. A fresh snapshot in any of
# them would let the table and the report disagree about their own subject --
# and they would disagree exactly when something moved between the two calls,
# which is when a reader most needs them to agree.

_DB9 = fresh_db("final.db")
_RID9 = silence(_dl.start_run_record, "batch_runner", db_path=_DB9)

_filtering.AGE_PARSE_FAILURES.clear()
_filtering.AGE_PARSE_FAILURES["ValueError:six months"] += 5
try:
    _snap9 = _degradation.snapshot()
    _expected9 = _degradation.totals(_snap9)
    # Something moves AFTER the snapshot -- the case that separates "the same
    # snapshot" from "a fresh one".
    _filtering.AGE_PARSE_FAILURES["ValueError:later"] += 100
    silence(_runner.flush_health, _RID9, snapshot=_snap9, db_path=_DB9)
    check("the flush wrote the SNAPSHOT it was handed, not a fresh reading",
          degradation_totals_of(_DB9, _RID9), _expected9)
    check("...and a fresh reading really would have differed (non-degeneracy: "
          "without this the check above passes for a flush that ignores its "
          "argument)",
          _degradation.totals() != _expected9, True)

    # CONTROL 9: called without the snapshot, it DOES take a fresh one -- so the
    # argument is what decided the result above.
    _RID9B = silence(_dl.start_run_record, "batch_runner", db_path=_DB9)
    silence(_runner.flush_health, _RID9B, db_path=_DB9)
    check("CONTROL: with no snapshot argument it reads the registry now",
          degradation_totals_of(_DB9, _RID9B), _degradation.totals())
finally:
    _filtering.AGE_PARSE_FAILURES.clear()

# --- the wiring, by ast -----------------------------------------------------
_main = function_named(_RUNNER_TREE, "main")
check("main() was found (non-degeneracy for every structural check below)",
      _main is not None, True)

_flush_calls = [n for n in ast.walk(_main)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "flush_health"] if _main else []
check("main() flushes twice -- once on the success path with the shared "
      "snapshot, once on the crash path",
      len(_flush_calls), 2)
check("...and exactly one of them is handed the shared snapshot by keyword",
      sorted(sorted(k.arg for k in c.keywords) for c in _flush_calls),
      sorted([sorted(["db_path"]), sorted(["snapshot", "db_path"])]))

_snapshot_call_names = [
    n.func.attr for n in ast.walk(_main)
    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    and isinstance(n.func.value, ast.Name) and n.func.value.id == "degradation"
] if _main else []
check("main() calls degradation.snapshot() exactly ONCE -- a second call is "
      "how the three outputs would come to describe two instants",
      _snapshot_call_names.count("snapshot"), 1)

# The crash-path flush must run BEFORE finalize_run_record, so the rows are
# current at the moment the run is marked KILLED.
_handlers = [h for h in ast.walk(_main)
             if isinstance(h, ast.ExceptHandler)] if _main else []
_killed = None
for _h in _handlers:
    _src = ast.dump(_h)
    if "'KILLED'" in _src or '"KILLED"' in _src:
        _killed = _h
_order = []
if _killed is not None:
    for _n in ast.walk(_killed):
        if isinstance(_n, ast.Call) and isinstance(_n.func, ast.Name):
            if _n.func.id in ("flush_health", "finalize_run_record"):
                _order.append((_n.lineno, _n.func.id))
check("the crash handler flushes the health record BEFORE it marks the run "
      "KILLED",
      [name for _, name in sorted(_order)],
      ["flush_health", "finalize_run_record"])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("10. THE PER-PATIENT FLUSH IS OUTSIDE THE SUCCESS BRANCH")
print("=" * 78)
print()

# THE ONE THING ABOUT THE WIRING THAT COULD BE SILENTLY WRONG. The natural host
# is save_checkpoint(), which sits inside `if entry["status"] == "success":` --
# so a pass in which every patient ERRORED would flush nothing at all. Errors
# are exactly when REFUSALS_OBSERVED, MALFORMED_EVALUATION_ENTRIES and
# INFERENCE_WRITE_FAILURES move.
#
# DRIVEN THROUGH THE SHIPPED FUNCTIONS with process_patient replaced by a
# stand-in that returns status="error" for EVERY patient, which is the case the
# wrong wiring would leave empty.

_DB10 = _SCRATCH_DB          # the per-patient flush resolves rather than being told
silence(_dl.initialize_database, _DB10)
_RID10 = silence(_dl.start_run_record, "batch_runner", db_path=_DB10)

_SEEN = []


def _erroring_process_patient(fhir_path=None, graph=None, is_resample=False,
                              run_id=None):
    """Every patient fails. Also moves a counter, so there is health to record.

    THE STAND-IN RETURNS status="error" FOR TWO REASONS. It is the case the
    wrong wiring would miss; and a "success" entry makes _on_done call
    save_checkpoint(), which with no fingerprint argument resolves
    run_fingerprint.current() -- a live Qdrant round trip, in a file that is
    offline.
    """
    _SEEN.append(Path(fhir_path).stem)
    _filtering.AGE_PARSE_FAILURES[f"ValueError:{Path(fhir_path).stem}"] += 1
    return {"patient_id": Path(fhir_path).stem, "status": "error",
            "eligible_matches": 0, "near_misses": 0, "not_evaluable": 0,
            "total_time": 0.01, "timestamp": "2026-08-21T12:00:00",
            "error": "stand-in", "is_resample": is_resample}


_FHIR_DIR = os.path.join(_TMP, "fhir")
os.makedirs(_FHIR_DIR, exist_ok=True)
_FILES = []
for _i in range(4):
    _p = os.path.join(_FHIR_DIR, f"patient-{_i}.json")
    Path(_p).write_text("{}")
    _FILES.append(_p)

_filtering.AGE_PARSE_FAILURES.clear()
_REAL_PP = _runner.process_patient
try:
    _runner.process_patient = _erroring_process_patient
    _results = []
    silence(_runner.run_batch, fhir_files=_FILES, bm25_index=None, nct_ids=[],
            graph=None, completed_ids=set(), results_list=_results,
            run_id=_RID10)

    check("run_batch reached every patient (non-degeneracy: an empty pass "
          "would satisfy every assertion below)",
          sorted(_SEEN), sorted(Path(f).stem for f in _FILES))
    check("...every one of them FAILED, which is the case the wrong wiring "
          "would leave with no health record at all",
          sorted({r["status"] for r in _results}), ["error"])
    check("...and the health record was written anyway",
          at(degradation_totals_of(_DB10, _RID10), "AGE_PARSE_FAILURES"),
          len(_FILES))
    check("...with the meta row, so the record is readable as a measurement",
          at({r["name"]: r["value"] for r in rows(
              _DB10, "SELECT name, value FROM run_metrics "
                     "WHERE run_id=? AND category=?",
              (_RID10, _dl.RUN_METRIC_CATEGORY_META))},
             _dl.RUN_METRIC_META_COUNTERS_REGISTERED),
          len(_degradation.registered_names()))

    # The resample pass shares the counters and the run id: cumulative totals,
    # one campaign.
    _SEEN.clear()
    silence(_runner.run_resample, fhir_files=_FILES,
            completed_ids={Path(f).stem for f in _FILES},
            bm25_index=None, nct_ids=[], graph=None, results_list=_results,
            run_id=_RID10)
    check("the resample pass reached at least one patient (non-degeneracy)",
          len(_SEEN) >= 1, True)
    check("...and its flushes ADD to the same run's record rather than "
          "resetting it -- the counters are cumulative across both passes and "
          "are never cleared between them",
          at(degradation_totals_of(_DB10, _RID10), "AGE_PARSE_FAILURES"),
          len(_FILES) + len(_SEEN))
finally:
    _runner.process_patient = _REAL_PP
    _filtering.AGE_PARSE_FAILURES.clear()

check("the real process_patient was restored",
      _runner.process_patient is _REAL_PP, True)

# --- the structural half ----------------------------------------------------
# A behavioural check cannot see WHERE the call is, only that it happened; an
# ast walk cannot see whether it works. Both.
for _fn_name in ("run_batch", "run_resample"):
    _fn = function_named(_RUNNER_TREE, _fn_name)
    _on_done = function_named(_fn, "_on_done") if _fn else None
    if _on_done is None:
        fail(f"{_fn_name}._on_done was not found", "the structural check has "
             "no subject")
        continue
    _in_success = []
    for _node in ast.walk(_on_done):
        if isinstance(_node, ast.If):
            for _branch in ast.walk(_node):
                if (isinstance(_branch, ast.Call)
                        and isinstance(_branch.func, ast.Name)
                        and _branch.func.id == "flush_health"):
                    _in_success.append(_branch.lineno)
    _all_calls = [n.lineno for n in ast.walk(_on_done)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "flush_health"]
    check(f"{_fn_name}._on_done flushes exactly once", len(_all_calls), 1)
    check(f"{_fn_name}._on_done's flush is NOT inside any `if`, so an errored "
          f"patient is recorded too", _in_success, [])
    # CONTROL 10: the walk can see a call that IS inside an `if` -- so the
    # emptiness above is a finding rather than a scan that matched nothing.
    _guarded = [n.lineno for n in ast.walk(_on_done)
                if isinstance(n, ast.If)
                for m in ast.walk(n)
                if isinstance(m, ast.Call) and isinstance(m.func, ast.Name)
                and m.func.id == "save_checkpoint"]
    check(f"CONTROL: the same walk DOES find {_fn_name}'s guarded calls when "
          f"there are any (run_resample has none, which is itself the point: "
          f"it never checkpoints)",
          bool(_guarded), _fn_name == "run_batch")


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("11. THE SAMPLE DATABASE GETS THE SCHEMA AND NOT THE ROWS")
print("=" * 78)
print()

# THE DECISION, ARGUED AT COPIED_TABLES. A `runs` row holds a CONFIGURATION,
# which is equally true of any subset of the run's patients. A `run_metrics` row
# holds a COUNT aggregated over every patient of the campaign, and copied beside
# a 30-patient extract of a 22,000-patient run it invites exactly one reading --
# "this sample had 412 age-unit assumptions" -- with no column in the narrow
# shape able to carry the denominator that would contradict it.

check("run_metrics is in COPIED_TABLES, so the sample opens in a tool built "
      "against the production schema",
      "run_metrics" in _sampling.COPIED_TABLES, True)

# Which tables get their ROWS copied is a property of the code, not of the
# tuple: the schema query is driven by COPIED_TABLES and the row copies are
# hand-written per table.
_row_copied = sorted({
    node.value.split()[2].strip("(")
    for node in ast.walk(_SAMPLING_TREE)
    if isinstance(node, ast.Constant) and isinstance(node.value, str)
    and node.value.startswith("INSERT INTO ")
})
check("exactly three tables have their rows copied, and run_metrics is not one "
      "of them",
      _row_copied, ["inferences", "runs", "trial_matches"])
check("...which is the drift_metrics treatment (non-degeneracy: that table is "
      "the precedent this follows and it is not row-copied either)",
      "drift_metrics" in _row_copied, False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("12. NOTHING OUTSIDE THE TEMP DIRECTORY WAS TOUCHED")
print("=" * 78)
print()

check("the seeded scratch database is not the production one (non-degeneracy: "
      "without this every isolation check compares a path with itself)",
      os.path.abspath(_SCRATCH_DB) != os.path.abspath(_PRODUCTION_DB), True)
check("...and the flush's own default resolves to the scratch one",
      os.path.abspath(_dl.resolve_inference_db_path(None)),
      os.path.abspath(_SCRATCH_DB))
check("the production database is byte-identical",
      digest(_PRODUCTION_DB), _PRODUCTION_SHA_BEFORE)
for _src, _sha in _SHA_BEFORE.items():
    check(f"{os.path.basename(_src)} is byte-identical", digest(_src), _sha)

# Restore what was borrowed, so an embedder importing this file leaves no trace.
if _PATHS_HAD_INF:
    _paths._RESOLVED["inferences_path"] = _PATHS_WAS_INF
else:
    _paths._RESOLVED.pop("inferences_path", None)
if _PATHS_HAD_CP:
    _paths._RESOLVED["checkpoint_path"] = _PATHS_WAS_CP
else:
    _paths._RESOLVED.pop("checkpoint_path", None)
if _ENV_WAS is not None:
    os.environ["ONCOTRIAGE_INFERENCES_DB"] = _ENV_WAS

import shutil
shutil.rmtree(_TMP, ignore_errors=True)
check("the temp directory was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print(f"SUMMARY: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 78)
if _FAILURES:
    print()
    for _f in _FAILURES:
        print(f"  FAILED: {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 22 2026

@author: ramyalsaffar
"""
