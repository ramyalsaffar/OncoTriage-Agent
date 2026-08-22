# Run Identity Test
##################

"""The `runs` table, the `inferences.run_id` reference, and what NULL means in it.

WHAT WAS MISSING
----------------
``inferences`` and ``trial_matches`` are per-PATIENT records. Neither carried
anything about the CAMPAIGN that produced them, so "which rows belong to one
batch run" was recovered by looking for gaps between consecutive ``timestamp``
values. That heuristic is wrong in four ways and silent in all of them: a
RESUMED run reads as two campaigns; two campaigns started back to back read as
one; an API row written by "17- FastAPI Server.py" during a batch run is
indistinguishable from a batch row; and no gap between timestamps says anything
about the CONFIGURATION, which is what a run-level number has to be attributed
to.

WHAT THIS FILE HOLDS
--------------------
    1. THE TWO RESTATED VOCABULARIES ROUND-TRIP. ``RUN_FINGERPRINT_COLUMNS``
       equals ``("fingerprint_version",) + run_fingerprint.FINGERPRINT_FIELDS``
       and ``RUN_RECORD_TERMINAL_STATUSES`` equals
       ``tracking.RUN_STATUSES`` -- both restated in the storage layer because
       importing either module would make storage depend on the AGENT layer, and
       both therefore able to drift. A test may import all three because a test
       is in nobody's import graph; that is the whole reason the check lives
       here rather than there.
    2. THE MIGRATION IS ADDITIVE AND IDEMPOTENT, through the real
       ``initialize_database``: fresh, run twice, and against a PRE-MIGRATION
       database built with the old `inferences` shape and no `runs` table at
       all, whose existing rows are required to survive.
    3. THE RUN ROW IS CREATED WITH THE STAMP AS COLUMNS and finalized with a
       terminal status and a real ``finished_at``.
    4. THE COERCION RULE, which is the one place the storage of a stamp is not a
       straight copy: ``collection_points`` NULLs an unresolved ``"unknown"``
       rather than letting a TEXT value into an INTEGER-affinity column, where
       SQLite would sort it above every real count.
    5. ``run_id`` IS WRITTEN ON THE BATCH PATH AND NULL ON A DIRECT CALL, read
       back out of SQLite, with the two shown to be separable in SQL.
    6. A SECOND ``main()`` IN ONE PROCESS CREATES A DISTINCT RUN, behaviourally
       (two ``start_run_record`` calls, two ids, two rows) and structurally
       (``main()`` holds the id as a LOCAL, threads it into both passes, and no
       module-level "current run" exists for a second call to inherit).
    7. ``run_batch`` AND ``run_resample`` FORWARD IT TO EVERY WORKER, driven for
       real through the shipped functions with a recording stand-in.
    8. FINALIZATION NEVER RAISES: no id, an unknown status, a row that is not
       there, and a database that cannot be opened -- each driven by creating
       the real condition, each counted, none of them raising.
    9. THE CRASHED-RUN SHAPE IS DISTINGUISHABLE IN SQL, and the query is shown
       to stop matching once the row is finalized.
   10. THE PRODUCTION DATABASE IS NEVER TOUCHED: its sha256 is taken at the top
       of this file and compared at the bottom, and the isolation is asserted to
       be non-degenerate before anything is written.
   11. TEN NEGATIVE CONTROLS, each shown to fire.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY, and NOT in the collision matrix: every database is a temp file every
call is pointed at explicitly, ``paths._RESOLVED`` is seeded so nothing can
resolve to the production tree, and the two repository files it READS --
``oncotriage/storage/database_logger.py`` and ``oncotriage/batch/runner.py`` --
are written by neither of the suite's two writers.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry. Every control is
either a different INPUT to a pure function, a real failing condition created on
disk, or an ``ast`` walk over an in-memory COPY of a source file -- parsed,
never executed.

Run from terminal:
    python tests/test_storage_run_identity.py

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
# imported anyway: a stand-in forgotten in a future edit becomes a named
# RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
from pathlib import Path

from oncotriage import paths as _paths
from oncotriage import run_fingerprint as _rf
from oncotriage import tracking as _tracking
from oncotriage.batch import runner as _runner
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

    NOT DEFENSIVE PADDING. Nine files in this suite have shipped the same
    defect: a bare call inside a ``check(...)`` argument, where a planted or
    reverted defect raises, the exception escapes while the argument is being
    evaluated, and the run reports ONE TRACEBACK where it owed a summary and N
    results. Section 8 deliberately creates failing conditions, so every driver
    goes through here.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def silence(fn, *args, **kwargs):
    """Run fn with BOTH output channels captured; return its value.

    The writer announces every ALTER TABLE, every row and every run. Nothing
    suppressed is asserted on: every assertion below reads the DATABASE, a
    returned value or a counter.
    """
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


def columns_of(db, table):
    """Column names of `table`, read read-only.

    A plain sqlite3.connect on an absent path CREATES the file, so a check
    written that way would bring its own subject into existence.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"unreadable: {exc}"


def tables_of(db):
    """Every table this project declared, sorted. SQLite's own are excluded."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"))
    finally:
        conn.close()


def rows(db, sql, params=()):
    """Every row of `sql` as a list of dicts. Read-only."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    finally:
        conn.close()


def one(db, sql, params=()):
    """The first row of `sql`, or a NAMED absence."""
    found = rows(db, sql, params)
    return found[0] if found else {"__no_row__": sql}


#------------------------------------------------------------------------------


# ===========================================================================
# ISOLATION, ESTABLISHED BEFORE ANYTHING IS WRITTEN
# ===========================================================================
#
# The production database's sha256 is taken HERE, at the top, before this file
# has opened anything -- and compared at the very bottom. Taking it after the
# first write would compare a changed file with itself.
#
# paths._RESOLVED IS SEEDED, which is the seam tests/test_ablation_db_isolation.py
# and tests/test_dashboard_reproducibility_tab.py already use. Two keys:
# `inferences_path`, so a call that resolves rather than being told cannot reach
# production, and `checkpoint_path`, because run_batch's append_result writes
# the results file there.
#
# ONCOTRIAGE_INFERENCES_DB IS EXPLICITLY CLEARED. It outranks paths.inferences_path
# at tier 2 of resolve_inference_db_path, so an operator with it exported would
# otherwise redirect this file's "production" reading to their own scratch
# database and every isolation assertion below would compare two scratch paths
# and pass for the wrong reason.

_ENV_WAS = os.environ.pop("ONCOTRIAGE_INFERENCES_DB", None)

_PRODUCTION_DB = _paths.inferences_path
_PRODUCTION_SHA_BEFORE = digest(_PRODUCTION_DB)

_TMP = tempfile.mkdtemp(prefix="oncotriage-run-identity-")
_SCRATCH_DB = os.path.join(_TMP, "inferences.db")
_CHECKPOINT_DIR = os.path.join(_TMP, "checkpoint")
os.makedirs(_CHECKPOINT_DIR, exist_ok=True)

_PATHS_HAD_INF = "inferences_path" in _paths._RESOLVED
_PATHS_WAS_INF = _paths._RESOLVED.get("inferences_path")
_PATHS_HAD_CP = "checkpoint_path" in _paths._RESOLVED
_PATHS_WAS_CP = _paths._RESOLVED.get("checkpoint_path")

_paths._RESOLVED["inferences_path"] = _SCRATCH_DB
_paths._RESOLVED["checkpoint_path"] = _CHECKPOINT_DIR + os.sep

# The two source files this file READS. Hashed now, compared at the end: nothing
# here writes into the repository, and saying so is cheaper than being believed.
_DL_SRC = os.path.abspath(_dl.__file__)
_RUNNER_SRC = os.path.abspath(_runner.__file__)
_DL_SHA_BEFORE = digest(_DL_SRC)
_RUNNER_SHA_BEFORE = digest(_RUNNER_SRC)


# A stamp shaped exactly like run_fingerprint.current() but built from literals,
# so no Qdrant round trip and no model load happens anywhere in this file. The
# KEYS come from the module, not from a retyped list -- a field added to the
# stamp appears here automatically and is then required to appear as a column.
_STAMP_VALUES = {
    "fingerprint_version": _rf.FINGERPRINT_VERSION,
    "llm_classifier_prompt_version": "9.9.9-test",
    "llm_classifier_renderer_digest": "d" * 64,
    "matching_model_configured": "test-model",
    "qdrant_collection": "trial_criteria_test_0001",
    "collection_points": 12345,
    "data_snapshot_date": "2026-01-31",
}
_STAMP = {k: _STAMP_VALUES[k] for k in
          ("fingerprint_version",) + _rf.FINGERPRINT_FIELDS}


#------------------------------------------------------------------------------


print("=" * 78)
print("1. THE RESTATED VOCABULARIES ROUND-TRIP")
print("=" * 78)
print()

# WHY THIS IS THE FIRST SECTION. oncotriage/storage/database_logger.py restates
# two tuples it may not import: oncotriage.tracking and
# oncotriage.run_fingerprint both import oncotriage.agent.prompts (and
# run_fingerprint imports agent.readiness, which builds a Qdrant client), so a
# storage module importing either would put the AGENT layer -- and a network
# probe's import graph -- behind `import oncotriage.storage.database_logger`.
# That is the edge pass 20c-2c moved _resolve_primary_cancer out of that module
# to remove.
#
# A RESTATED CONSTANT IS A CONSTANT THAT CAN DRIFT. This file is the thing that
# stops it, and it can be, because a test is in nobody's import graph.

check("RUN_FINGERPRINT_COLUMNS is exactly the stamp's keys, in order",
      list(_dl.RUN_FINGERPRINT_COLUMNS),
      ["fingerprint_version"] + list(_rf.FINGERPRINT_FIELDS))

check("...and the stamp really has six gated fields (non-degenerate: a check "
      "against an empty tuple would pass for free)",
      len(_rf.FINGERPRINT_FIELDS), 6)

check("RUN_RECORD_TERMINAL_STATUSES equals tracking.RUN_STATUSES",
      tuple(_dl.RUN_RECORD_TERMINAL_STATUSES), tuple(_tracking.RUN_STATUSES))

check("...and RUNNING is deliberately NOT among them -- finalizing a run to "
      "'still going' is the one thing the end of a run must not do",
      _dl.RUN_RECORD_STATUS_RUNNING in _dl.RUN_RECORD_TERMINAL_STATUSES, False)

check("RUN_RECORD_STATUSES is the terminal set plus RUNNING, and nothing else",
      sorted(_dl.RUN_RECORD_STATUSES),
      sorted(set(_dl.RUN_RECORD_TERMINAL_STATUSES) |
             {_dl.RUN_RECORD_STATUS_RUNNING}))

check("RUN_COLUMNS is the four run facts followed by the stamp columns",
      list(_dl.RUN_COLUMNS),
      ["started_at", "finished_at", "status", "invocation_source"] +
      list(_dl.RUN_FINGERPRINT_COLUMNS))

check("the integer columns are named and are a SUBSET of the stamp columns",
      sorted(_dl.RUN_FINGERPRINT_INTEGER_COLUMNS),
      sorted(set(_dl.RUN_FINGERPRINT_INTEGER_COLUMNS) &
             set(_dl.RUN_FINGERPRINT_COLUMNS)))

# CONTROL 1: the round trip is not vacuous. Comparing against a stamp with one
# field removed must DISAGREE -- without this, a check written as
# `sorted(a) == sorted(a)` would look identical and pass forever.
_short = ("fingerprint_version",) + tuple(_rf.FINGERPRINT_FIELDS)[:-1]
check("CONTROL: a stamp missing one field no longer matches the column tuple",
      list(_dl.RUN_FINGERPRINT_COLUMNS) == list(_short), False)

# CONTROL 2: and neither does one with an extra field.
_long = ("fingerprint_version",) + tuple(_rf.FINGERPRINT_FIELDS) + ("invented",)
check("CONTROL: nor does one with a field the columns do not carry",
      list(_dl.RUN_FINGERPRINT_COLUMNS) == list(_long), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. THE MIGRATION IS ADDITIVE AND IDEMPOTENT")
print("=" * 78)
print()

# --- (a) a fresh database ---------------------------------------------------

_FRESH = os.path.join(_TMP, "fresh.db")
silence(_dl.initialize_database, _FRESH)

# FIVE AT THE HEALTH-PERSISTENCE PASS, which added `run_metrics`. The set is
# kept EXACT rather than widened to a subset test, for the reason the bedrock
# adapter's copy of this assertion states: exact is what makes it fail when a
# table is introduced under any name.
check("a fresh database carries all five tables",
      tables_of(_FRESH),
      ["drift_metrics", "inferences", "run_metrics", "runs", "trial_matches"])

check("...and `runs` carries exactly RUN_COLUMNS plus its id",
      sorted(columns_of(_FRESH, "runs")),
      sorted(set(_dl.RUN_COLUMNS) | {"id"}))

check("...and `inferences` carries run_id",
      "run_id" in columns_of(_FRESH, "inferences"), True)

check("run_id is declared INTEGER in INFERENCE_COLUMN_ADDITIONS",
      _dl.INFERENCE_COLUMN_ADDITIONS.get("run_id"), "INTEGER")

# The declared affinity of every runs column, read out of the real schema. This
# is what section 4's NULL rule rests on: `collection_points` has INTEGER
# affinity, so a TEXT value stored there would order above every real count.
_RUN_DECL = {}
_c = sqlite3.connect(f"file:{_FRESH}?mode=ro", uri=True)
_RUN_DECL = {r[1]: r[2] for r in _c.execute("PRAGMA table_info(runs)")}
_c.close()

check("the two integer stamp columns are declared INTEGER in the real schema",
      {c: _RUN_DECL.get(c) for c in sorted(_dl.RUN_FINGERPRINT_INTEGER_COLUMNS)},
      {c: "INTEGER" for c in sorted(_dl.RUN_FINGERPRINT_INTEGER_COLUMNS)})

check("...and every other stamp column is TEXT",
      {c: _RUN_DECL.get(c) for c in _dl.RUN_FINGERPRINT_COLUMNS
       if c not in _dl.RUN_FINGERPRINT_INTEGER_COLUMNS},
      {c: "TEXT" for c in _dl.RUN_FINGERPRINT_COLUMNS
       if c not in _dl.RUN_FINGERPRINT_INTEGER_COLUMNS})

# PRAGMA table_info's fourth field is `notnull`. Read once, into a dict, rather
# than re-opened per column.
_c = sqlite3.connect(f"file:{_FRESH}?mode=ro", uri=True)
_RUN_NOTNULL = {r[1]: bool(r[3]) for r in _c.execute("PRAGMA table_info(runs)")}
_c.close()

check("started_at, status and invocation_source are NOT NULL; finished_at is "
      "NULLABLE, which is the entire crashed-run shape",
      {c: _RUN_NOTNULL.get(c) for c in
       ("started_at", "finished_at", "status", "invocation_source")},
      {"started_at": True, "finished_at": False,
       "status": True, "invocation_source": True})

# --- (b) idempotence on an EXISTING scratch database ------------------------

_sql_before = sorted(r["sql"] for r in rows(
    _FRESH, "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%'"))
_cols_before = {t: columns_of(_FRESH, t) for t in tables_of(_FRESH)}

# The cache is cleared so initialize_database does the FULL work again rather
# than being short-circuited by _INITIALIZED_DATABASES -- otherwise "idempotent"
# would be measuring a set membership test.
_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_FRESH))
_second, _second_out = loud(_dl.initialize_database, _FRESH)
_dl._INITIALIZED_DATABASES.discard(os.path.abspath(_FRESH))
_third = silence(_dl.initialize_database, _FRESH)

check("a second full initialize_database does not raise",
      isinstance(_second, str), True)
check("...and issues NO schema migration -- every ALTER is already applied",
      "Schema migration:" in _second_out, False)
check("...and the CREATE text in sqlite_master is byte-identical",
      sorted(r["sql"] for r in rows(
          _FRESH, "SELECT sql FROM sqlite_master WHERE type='table' "
                  "AND name NOT LIKE 'sqlite_%'")),
      _sql_before)
check("...and no column moved on any table",
      {t: columns_of(_FRESH, t) for t in tables_of(_FRESH)}, _cols_before)

# --- (c) a PRE-MIGRATION database -------------------------------------------
#
# Built by hand with the shape a database written before this pass has: an
# `inferences` table with no run_id, and no `runs` table at all. Two rows are
# seeded, and they are required to survive -- an "additive" migration that
# rebuilt the table would pass every column check above and silently discard
# every row anybody had.

_LEGACY = os.path.join(_TMP, "legacy.db")
_c = sqlite3.connect(_LEGACY)
_c.execute("CREATE TABLE inferences ("
           "id INTEGER PRIMARY KEY AUTOINCREMENT, "
           "patient_id TEXT NOT NULL, timestamp TEXT NOT NULL)")
_c.execute("CREATE TABLE trial_matches ("
           "id INTEGER PRIMARY KEY AUTOINCREMENT, "
           "inference_id INTEGER NOT NULL, nct_id TEXT NOT NULL)")
_c.executemany("INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
               [("legacy-a", "2026-01-01T00:00:00"),
                ("legacy-b", "2026-01-02T00:00:00")])
_c.commit()
_c.close()

check("PRE-CHECK: the legacy database has no `runs` table",
      "runs" in tables_of(_LEGACY), False)
check("PRE-CHECK: ...and no inferences.run_id",
      "run_id" in columns_of(_LEGACY, "inferences"), False)
check("PRE-CHECK: ...and it holds two rows to lose",
      at(one(_LEGACY, "SELECT COUNT(*) AS n FROM inferences"), "n"), 2)

silence(_dl.initialize_database, _LEGACY)

check("the migration CREATES `runs` on an existing database",
      "runs" in tables_of(_LEGACY), True)
check("...and ADDS inferences.run_id",
      "run_id" in columns_of(_LEGACY, "inferences"), True)
check("...and the legacy rows survive, in order",
      [r["patient_id"] for r in
       rows(_LEGACY, "SELECT patient_id FROM inferences ORDER BY id")],
      ["legacy-a", "legacy-b"])
check("...carrying NULL run_id, which is the honest value for a row written "
      "before there was a run to attach it to",
      [r["run_id"] for r in
       rows(_LEGACY, "SELECT run_id FROM inferences ORDER BY id")],
      [None, None])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. THE RUN ROW IS CREATED, THEN FINALIZED")
print("=" * 78)
print()

_RUN_DB = os.path.join(_TMP, "runs.db")

check("PRE-CHECK: the scratch database is NOT the production one "
      "(non-degenerate isolation)",
      os.path.abspath(_RUN_DB) == os.path.abspath(_PRODUCTION_DB), False)
check("PRE-CHECK: ...and an unaimed resolve does not reach production either, "
      "because paths._RESOLVED is seeded",
      os.path.abspath(_dl.resolve_inference_db_path(None))
      == os.path.abspath(_PRODUCTION_DB), False)

_rid = silence(_dl.start_run_record, "test_source",
               db_path=_RUN_DB, fingerprint=_STAMP)

check("start_run_record returns an integer id", isinstance(_rid, int), True)

_row = one(_RUN_DB, "SELECT * FROM runs WHERE id = ?", (_rid,))
check("the row records the invocation source it was given",
      at(_row, "invocation_source"), "test_source")
check("...opens as RUNNING", at(_row, "status"), _dl.RUN_RECORD_STATUS_RUNNING)
check("...with a NULL finished_at", at(_row, "finished_at"), None)
check("...and a started_at that parses as an ISO timestamp",
      isinstance(at(_row, "started_at"), str)
      and len(at(_row, "started_at")) >= 19, True)

check("every stamp field landed in its own column, verbatim",
      {c: at(_row, c) for c in _dl.RUN_FINGERPRINT_COLUMNS}, dict(_STAMP))

check("...and collection_points came back as a NUMBER, not as text",
      isinstance(at(_row, "collection_points"), int), True)

# --- finalize ---------------------------------------------------------------

_ok = silence(_dl.finalize_run_record, _rid, "FINISHED", db_path=_RUN_DB)
check("finalize_run_record reports success", _ok, True)

_row = one(_RUN_DB, "SELECT * FROM runs WHERE id = ?", (_rid,))
check("...the status is the terminal one it was given",
      at(_row, "status"), "FINISHED")
check("...finished_at is no longer NULL",
      at(_row, "finished_at") is None, False)
check("...and started_at was NOT rewritten",
      at(_row, "started_at") <= at(_row, "finished_at"), True)
check("...and the stamp columns were not touched by the UPDATE",
      {c: at(_row, c) for c in _dl.RUN_FINGERPRINT_COLUMNS}, dict(_STAMP))

# --- an absent stamp ---------------------------------------------------------

_rid_nostamp = silence(_dl.start_run_record, "test_source", db_path=_RUN_DB)
_row_nostamp = one(_RUN_DB, "SELECT * FROM runs WHERE id = ?", (_rid_nostamp,))
check("a run opened with NO stamp leaves every stamp column NULL",
      {c: at(_row_nostamp, c) for c in _dl.RUN_FINGERPRINT_COLUMNS},
      {c: None for c in _dl.RUN_FINGERPRINT_COLUMNS})
check("...which is exactly what `fingerprint_version IS NULL` selects, and "
      "nothing else does",
      [r["id"] for r in rows(
          _RUN_DB, "SELECT id FROM runs WHERE fingerprint_version IS NULL")],
      [_rid_nostamp])

# --- invocation_source is required ------------------------------------------

for _bad, _label in ((None, "None"), ("", "an empty string"),
                     ("   ", "whitespace"), (7, "an integer")):
    _raised = guarded(_dl.start_run_record, _bad, db_path=_RUN_DB)
    check(f"invocation_source of {_label} is refused by name",
          isinstance(_raised, dict)
          and "ValueError" in str(at(_raised, "__raised__", "")), True)

check("...and nothing was written by any of those four refusals",
      at(one(_RUN_DB, "SELECT COUNT(*) AS n FROM runs"), "n"), 2)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. THE COERCION RULE: AN UNRESOLVED COUNT IS NULL, NEVER 'unknown'")
print("=" * 78)
print()

# WHY THIS IS NOT COSMETIC. run_fingerprint degrades an unresolvable field to
# the STRING "unknown". The five TEXT columns store that verbatim, which is
# right for them. Storing it in an INTEGER-affinity column is the ecog_date trap
# one column type over: SQLite keeps a non-numeric string as TEXT whatever the
# declared affinity, and orders EVERY text value above EVERY integer -- so
# `WHERE collection_points > 1000` would return the rows where the count could
# not be established, and ORDER BY DESC would rank them as the largest
# collections there are.

check("an int stays an int",
      _dl._run_fingerprint_value("collection_points", {"collection_points": 12067}),
      12067)
check("...including zero, which is a MEASUREMENT (an empty collection) and not "
      "an absence",
      _dl._run_fingerprint_value("collection_points", {"collection_points": 0}), 0)
check("UNKNOWN becomes NULL",
      _dl._run_fingerprint_value("collection_points",
                                 {"collection_points": _rf.UNKNOWN}), None)
check("...and so does any other non-int",
      _dl._run_fingerprint_value("collection_points",
                                 {"collection_points": "12067"}), None)
check("a bool becomes NULL, because isinstance(True, int) is True and a "
      "collection_points of 1 that was really a True is a number nobody measured",
      _dl._run_fingerprint_value("collection_points", {"collection_points": True}),
      None)
check("a TEXT field keeps its 'unknown' verbatim -- that column can hold it and "
      "it is the reader's evidence that the NULL beside it is a degradation, "
      "not a missing stamp",
      _dl._run_fingerprint_value("qdrant_collection",
                                 {"qdrant_collection": _rf.UNKNOWN}),
      _rf.UNKNOWN)
check("a stamp of None leaves the field NULL",
      _dl._run_fingerprint_value("qdrant_collection", None), None)
check("...and so does a stamp that simply omits the field",
      _dl._run_fingerprint_value("qdrant_collection", {}), None)

# Driven end to end, because a pure-function check cannot see a writer that
# bypasses the helper.
_degraded_stamp = dict(_STAMP)
_degraded_stamp["collection_points"] = _rf.UNKNOWN
_degraded_stamp["qdrant_collection"] = _rf.UNKNOWN
_rid_deg = silence(_dl.start_run_record, "test_source",
                   db_path=_RUN_DB, fingerprint=_degraded_stamp)
_row_deg = one(_RUN_DB, "SELECT * FROM runs WHERE id = ?", (_rid_deg,))
check("end to end: an unresolved count is stored as SQL NULL",
      at(_row_deg, "collection_points"), None)
check("...while the unresolved NAME is stored as the string it is",
      at(_row_deg, "qdrant_collection"), _rf.UNKNOWN)
check("...so the two questions are answered by two different predicates: "
      "'a stamp was recorded' is fingerprint_version IS NOT NULL",
      at(_row_deg, "fingerprint_version"), _rf.FINGERPRINT_VERSION)

check("CONTROL: the degraded row is NOT returned by a numeric comparison, "
      "which is the exact query the string 'unknown' would have poisoned",
      [r["id"] for r in rows(
          _RUN_DB, "SELECT id FROM runs WHERE collection_points > 1000")],
      [_rid])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. run_id ON THE ROW: WRITTEN ON THE BATCH PATH, NULL ON A DIRECT CALL")
print("=" * 78)
print()

_LOG_DB = os.path.join(_TMP, "log.db")


def _result(pid):
    """A minimal terminal-node-shaped result dict. No pipeline, no model."""
    return {"patient_id": pid, "timestamp": "2026-08-21T12:00:00",
            "matches": [], "near_misses": [], "not_evaluable": [],
            "stage_timings": {}}


_PATIENT = {"demographics": {}, "conditions": [], "medications": [],
            "allergies": []}

_run_for_log = silence(_dl.start_run_record, _runner.INVOCATION_SOURCE,
                       db_path=_LOG_DB, fingerprint=_STAMP)

_w_batch = silence(_dl.log_inference, _result("batch-1"), _PATIENT,
                   db_path=_LOG_DB, run_id=_run_for_log)
_w_direct = silence(_dl.log_inference, _result("direct-1"), _PATIENT,
                    db_path=_LOG_DB)

check("both writes landed",
      [getattr(_w_batch, "ok", None), getattr(_w_direct, "ok", None)],
      [True, True])

check("the batch-path row carries the run id",
      at(one(_LOG_DB, "SELECT run_id FROM inferences WHERE patient_id='batch-1'"),
         "run_id"),
      _run_for_log)
check("the direct call's row carries NULL -- 'not part of a recorded batch run'",
      at(one(_LOG_DB, "SELECT run_id FROM inferences WHERE patient_id='direct-1'"),
         "run_id"),
      None)

check("the two are separable in SQL by the join, not by a timestamp window",
      [r["patient_id"] for r in rows(
          _LOG_DB, "SELECT i.patient_id FROM inferences i "
                   "JOIN runs r ON r.id = i.run_id WHERE r.id = ?",
          (_run_for_log,))],
      ["batch-1"])

check("...and the API/direct population is `run_id IS NULL`",
      [r["patient_id"] for r in rows(
          _LOG_DB, "SELECT patient_id FROM inferences WHERE run_id IS NULL")],
      ["direct-1"])

check("both rows share a timestamp, which is what makes the heuristic this "
      "replaces unable to separate them",
      len({r["timestamp"] for r in
           rows(_LOG_DB, "SELECT timestamp FROM inferences")}), 1)

# The default is a value, not a fallback: a caller that omits run_id gets NULL
# and never a looked-up "current run".
check("run_id defaults to None in log_inference's signature",
      _dl.log_inference.__defaults__[-1], None)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("6. A SECOND main() IN ONE PROCESS CREATES A DISTINCT RUN")
print("=" * 78)
print()

# BEHAVIOURAL HALF. main() itself cannot be driven here -- it builds a BM25
# index from a live Qdrant, compiles the graph and makes one billed Stage 5 call
# per patient -- so what is driven is the thing main() does once per invocation,
# and what is asserted structurally is that main() does exactly that and holds
# no state between calls.

_SECOND_DB = os.path.join(_TMP, "second.db")
_r1 = silence(_dl.start_run_record, "batch_runner",
              db_path=_SECOND_DB, fingerprint=_STAMP)
_r2 = silence(_dl.start_run_record, "batch_runner",
              db_path=_SECOND_DB, fingerprint=_STAMP)

check("two invocations produce two different ids", _r1 == _r2, False)
check("...and two rows", at(one(_SECOND_DB, "SELECT COUNT(*) AS n FROM runs"), "n"), 2)
check("...both RUNNING until finalized",
      sorted(r["status"] for r in rows(_SECOND_DB, "SELECT status FROM runs")),
      ["RUNNING", "RUNNING"])

silence(_dl.finalize_run_record, _r1, "FINISHED", db_path=_SECOND_DB)
check("finalizing the first leaves the second alone",
      sorted((r["id"], r["status"]) for r in
             rows(_SECOND_DB, "SELECT id, status FROM runs")),
      sorted([(_r1, "FINISHED"), (_r2, "RUNNING")]))

# STRUCTURAL HALF, over the SHIPPED source of runner.main. The property is that
# there is nothing to carry over: the id is a LOCAL, so a second main() cannot
# inherit the first one's. Compare clear_write_ledger() and
# run_fingerprint.clear_cache() at the top of that function -- both exist
# because their state IS module-level and both are one forgotten line away from
# describing the wrong run.

# READ ONCE, HERE, and used by every structural check and every plant below.
# The first draft read it inside the third control, so the fourth -- written
# later and placed earlier -- died on a NameError at module level and took the
# summary and every remaining check with it. That is the abort class this suite
# has now met ten times; the fix is the same one every time, which is to make
# the value exist before anything can want it.
_RUNNER_TXT = Path(_RUNNER_SRC).read_text(encoding="utf-8")
_RUNNER_TREE = ast.parse(_RUNNER_TXT)
_MAIN = next((n for n in _RUNNER_TREE.body
              if isinstance(n, ast.FunctionDef) and n.name == "main"), None)

if _MAIN is None:
    fail("runner.main was located for the structural checks",
         "no top-level `def main` in oncotriage/batch/runner.py")
else:
    def _calls_named(node, name):
        return [n for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and ((isinstance(n.func, ast.Name) and n.func.id == name)
                     or (isinstance(n.func, ast.Attribute) and n.func.attr == name))]

    def _assign_targets(node):
        out = []
        for n in ast.walk(node):
            if isinstance(n, ast.Assign):
                for tgt in n.targets:
                    if isinstance(tgt, ast.Name):
                        out.append((tgt.id, n.value))
        return out

    _starts = _calls_named(_MAIN, "start_run_record")
    check("main() opens exactly one run row", len(_starts), 1)

    _targets = [name for name, value in _assign_targets(_MAIN)
                if isinstance(value, ast.Call)
                and isinstance(value.func, ast.Name)
                and value.func.id == "start_run_record"]
    check("...assigning it to exactly one LOCAL name", len(_targets), 1)

    _ID_NAME = _targets[0] if _targets else "<none>"

    # The name must not ALSO be a module-level global, which is the only way a
    # second main() could inherit the first one's run.
    _module_assigned = {t.id for n in _RUNNER_TREE.body
                        if isinstance(n, ast.Assign)
                        for t in n.targets if isinstance(t, ast.Name)}
    check(f"...and {_ID_NAME!r} is not assigned at module scope",
          _ID_NAME in _module_assigned, False)
    check("...and main() declares no `global` at all, so it cannot publish one",
          [n for n in ast.walk(_MAIN) if isinstance(n, ast.Global)], [])

    def _forwards(call_name):
        for call in _calls_named(_MAIN, call_name):
            for kw in call.keywords:
                if (kw.arg == "run_id" and isinstance(kw.value, ast.Name)
                        and kw.value.id == _ID_NAME):
                    return True
        return False

    check("main() forwards it to run_batch", _forwards("run_batch"), True)
    check("...and to run_resample", _forwards("run_resample"), True)

    _finals = _calls_named(_MAIN, "finalize_run_record")
    check("main() finalizes on more than one path",
          len(_finals) >= 2, True)

    # At least one finalize must live inside an exception handler: a run that
    # crashed must not be left RUNNING when a handler could have said KILLED.
    _in_handler = [f for h in ast.walk(_MAIN)
                   if isinstance(h, ast.ExceptHandler)
                   for f in _calls_named(h, "finalize_run_record")]
    # TWO, AND KNOWING WHICH TWO IS WHAT MAKES THE CONTROL BELOW HONEST: the
    # guard around tracking.start_run (which raises when tracking is
    # unavailable, at a point where the run row is already open and no other
    # handler exists yet) and the guard around the whole body (a crash, a
    # Ctrl-C, a SystemExit). Removing one leaves the other, so a control that
    # removed one and expected zero would be testing its own arithmetic.
    check("...and exactly two of them are inside an `except` handler",
          len(_in_handler), 2)

    # THE SUCCESS-PATH FINALIZE MUST BE THE LAST STATEMENT BEFORE THE RETURN,
    # and this is a correctness property rather than a style one. Every other
    # statement in that `try` can raise -- tracking_metrics walks the results
    # list, _results_path resolves a path, report_lines formats a snapshot --
    # and the handler finalizes to KILLED. With the finalize anywhere ABOVE
    # them, a raise in between overwrites a FINISHED row with KILLED and
    # reports a completed campaign as a crashed one. Being last makes the two
    # paths mutually exclusive by construction, which is stronger than a flag.
    def _try_with_return(fn):
        """The Try in fn whose body ends in a Return, or None."""
        for n in ast.walk(fn):
            if (isinstance(n, ast.Try) and n.body
                    and isinstance(n.body[-1], ast.Return)):
                return n
        return None

    def _finalize_is_last_before_return(fn):
        node = _try_with_return(fn)
        if node is None or len(node.body) < 2:
            return "<no try ending in a return>"
        prev = node.body[-2]
        if not (isinstance(prev, ast.Expr) and isinstance(prev.value, ast.Call)
                and isinstance(prev.value.func, ast.Name)):
            return f"<{type(prev).__name__}>"
        return prev.value.func.id

    check("main() finalizes the run row as the LAST statement before its "
          "return, so the crash handler can never overwrite a FINISHED row",
          _finalize_is_last_before_return(_MAIN), "finalize_run_record")

    # CONTROL 5a: moving that call one statement earlier must break the check.
    # An `ast` transformation on a COPY of the parsed tree -- nothing is written
    # and nothing is executed.
    _copy = ast.parse(_RUNNER_TXT)
    _cmain = next(n for n in _copy.body
                  if isinstance(n, ast.FunctionDef) and n.name == "main")
    _ctry = _try_with_return(_cmain)
    if _ctry is None or len(_ctry.body) < 3:
        fail("CONTROL: main()'s try was located for the reordering plant",
             "no try ending in a return, or too few statements to reorder")
    else:
        _ctry.body.insert(len(_ctry.body) - 2, _ctry.body.pop(len(_ctry.body) - 3))
        check("CONTROL: with one statement moved after it, the check fails",
              _finalize_is_last_before_return(_cmain) == "finalize_run_record",
              False)

    _statuses = sorted({a.value for f in _finals for a in f.args
                        if isinstance(a, ast.Constant)})
    check("...and every status it can write is a TERMINAL one",
          [s for s in _statuses
           if s not in _dl.RUN_RECORD_TERMINAL_STATUSES], [])
    check("...including KILLED, which is the crash path's own verdict and is "
          "NOT the same finding as FAILED",
          "KILLED" in _statuses, True)

    # CONTROL 3: the forwarding check is not vacuous -- it must FAIL against a
    # copy with the keyword removed. An `ast` walk over an in-memory copy;
    # nothing is exec'd and nothing on disk is touched.
    _txt = _RUNNER_TXT
    _planted = _txt.replace("                results_list=results_list,\n"
                            "                run_id=_run_record_id,\n",
                            "                results_list=results_list,\n", 1)
    if _planted == _txt:
        fail("CONTROL: the run_batch forwarding plant matched something",
             "the anchor text was not found in oncotriage/batch/runner.py")
    else:
        _pt = ast.parse(_planted)
        _pmain = next(n for n in _pt.body
                      if isinstance(n, ast.FunctionDef) and n.name == "main")
        _found = False
        for call in [n for n in ast.walk(_pmain)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                     and n.func.id == "run_batch"]:
            for kw in call.keywords:
                if kw.arg == "run_id":
                    _found = True
        check("CONTROL: with the keyword removed, the forwarding check fails",
              _found, False)

    # CONTROL 4: the except-handler check must fail when BOTH handler-side
    # finalizes are removed. Both, because there are two and the check asks
    # whether ANY handler finalizes -- a plant that removed one would leave the
    # property true and prove nothing.
    _planted2 = _txt.replace(
        '            finalize_run_record(_run_record_id, "KILLED", '
        'db_path=_reconcile_db)\n            tracking.end_run(status="FAILED")\n',
        '            tracking.end_run(status="FAILED")\n', 1)
    _planted2 = _planted2.replace(
        '            finalize_run_record(_run_record_id, "KILLED", '
        'db_path=_reconcile_db)\n            raise\n',
        '            raise\n', 1)
    # THE PLANT ASSERTS ITS OWN MATCH COUNT. A plant that matched nothing
    # produces a "control" that agrees with the shipped code and reports a
    # working check as broken -- the failure mode this project has met before
    # and writes down each time.
    _removed = _txt.count("finalize_run_record(_run_record_id, \"KILLED\"") \
        - _planted2.count("finalize_run_record(_run_record_id, \"KILLED\"")
    if _removed != 2:
        fail("CONTROL: the crash-path plant removed both handler calls",
             f"removed {_removed}, expected 2 -- an anchor was not found in "
             f"oncotriage/batch/runner.py, so this control would have reported "
             f"a working check as broken")
    else:
        _pt2 = ast.parse(_planted2)
        _pmain2 = next(n for n in _pt2.body
                       if isinstance(n, ast.FunctionDef) and n.name == "main")
        _handler_calls = [n for h in ast.walk(_pmain2)
                          if isinstance(h, ast.ExceptHandler)
                          for n in ast.walk(h)
                          if isinstance(n, ast.Call)
                          and isinstance(n.func, ast.Name)
                          and n.func.id == "finalize_run_record"]
        check("CONTROL: with the crash-path finalize removed, no `except` "
              "handler finalizes",
              len(_handler_calls), 0)

# process_patient must forward its argument to the writer rather than reading a
# global -- checked structurally, and driven for real in section 7.
_PP = next((n for n in _RUNNER_TREE.body
            if isinstance(n, ast.FunctionDef) and n.name == "process_patient"), None)
if _PP is None:
    fail("runner.process_patient was located", "no top-level def")
else:
    _li = [n for n in ast.walk(_PP) if isinstance(n, ast.Call)
           and isinstance(n.func, ast.Name) and n.func.id == "log_inference"]
    check("process_patient calls log_inference exactly once", len(_li), 1)
    check("...passing run_id through as its own parameter",
          [kw.value.id for c in _li for kw in c.keywords
           if kw.arg == "run_id" and isinstance(kw.value, ast.Name)],
          ["run_id"])

# THERE IS NO MODULE-LEVEL "CURRENT RUN" ANYWHERE. A scan of both modules for a
# global whose name suggests one, because the mechanism this pass relies on is
# precisely its absence.
for _mod, _src in (("runner", _RUNNER_SRC), ("database_logger", _DL_SRC)):
    _tree = ast.parse(Path(_src).read_text(encoding="utf-8"))
    _globals_assigned = {t.id for n in _tree.body if isinstance(n, ast.Assign)
                         for t in n.targets if isinstance(t, ast.Name)}
    _suspect = sorted(g for g in _globals_assigned
                      if "current_run" in g.lower()
                      or g.lower() in ("_run_id", "run_id", "_active_run",
                                       "_current_run_id"))
    check(f"{_mod} holds no module-level current-run state", _suspect, [])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("7. run_batch AND run_resample FORWARD THE ID TO EVERY WORKER")
print("=" * 78)
print()

# DRIVEN THROUGH THE SHIPPED FUNCTIONS, with process_patient replaced by a
# recording stand-in. That is the seam the threading actually crosses -- an
# executor.submit whose keyword was dropped is invisible to any check on the
# function it submits.
#
# THE STAND-IN RETURNS status="error" DELIBERATELY. A "success" entry makes
# _on_done call save_checkpoint(), which with no fingerprint argument resolves
# run_fingerprint.current() -- a live Qdrant round trip. This file is offline,
# and a test that quietly acquires a network dependency is a test that stops
# running in CI.

_SEEN = []


def _recording_process_patient(fhir_path=None, graph=None, is_resample=False,
                              run_id=None):
    _SEEN.append({"stem": Path(fhir_path).stem, "is_resample": is_resample,
                  "run_id": run_id})
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

_REAL_PP = _runner.process_patient
try:
    _runner.process_patient = _recording_process_patient

    _RUN_ID_UNDER_TEST = 4242
    _results = []
    silence(_runner.run_batch, fhir_files=_FILES, bm25_index=None, nct_ids=[],
            graph=None, completed_ids=set(), results_list=_results,
            run_id=_RUN_ID_UNDER_TEST)

    check("run_batch reached every pending patient (non-degenerate: an empty "
          "pass would satisfy every assertion below)",
          sorted(s["stem"] for s in _SEEN),
          sorted(Path(f).stem for f in _FILES))
    check("...and every worker was handed the run id",
          sorted({s["run_id"] for s in _SEEN}), [_RUN_ID_UNDER_TEST])

    _SEEN.clear()
    silence(_runner.run_resample, fhir_files=_FILES,
            completed_ids={Path(f).stem for f in _FILES},
            bm25_index=None, nct_ids=[], graph=None, results_list=_results,
            run_id=_RUN_ID_UNDER_TEST)

    check("the resample pass reached at least one patient (non-degenerate)",
          len(_SEEN) >= 1, True)
    check("...and it too carries the SAME run id -- a resample re-run is a "
          "second row of one campaign, not a second campaign",
          sorted({s["run_id"] for s in _SEEN}), [_RUN_ID_UNDER_TEST])
    check("...and is marked as a resample, so the two are still separable",
          sorted({s["is_resample"] for s in _SEEN}), [True])

    # CONTROL 5: the recorder can see an absent id, so the two checks above are
    # not satisfied by any value at all.
    _SEEN.clear()
    silence(_runner.run_batch, fhir_files=_FILES, bm25_index=None, nct_ids=[],
            graph=None, completed_ids=set(), results_list=_results)
    check("CONTROL: run_batch called with no run id forwards None, and the "
          "recorder sees the difference",
          sorted({s["run_id"] for s in _SEEN}), [None])
finally:
    _runner.process_patient = _REAL_PP

check("the real process_patient was restored",
      _runner.process_patient is _REAL_PP, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("8. FINALIZATION NEVER RAISES")
print("=" * 78)
print()

# IT RUNS AFTER THE MONEY IS SPENT. By the time main() reaches it the campaign
# has made one live Stage 5 call per patient and written its rows, and an index
# failure must not take those with it. Every condition below is created FOR
# REAL -- no source is patched and nothing is exec'd.

_dl.RUN_RECORD_FAILURES.clear()

# --- (a) no id --------------------------------------------------------------
_r = silence(_dl.finalize_run_record, None, "FINISHED", db_path=_RUN_DB)
check("finalize with no run id returns False and does not raise", _r, False)
check("...and is counted",
      _dl.RUN_RECORD_FAILURES.get("finalize:no_run_id"), 1)

# --- (b) an unknown status --------------------------------------------------
_rid_b = silence(_dl.start_run_record, "test_source", db_path=_RUN_DB)
_r = silence(_dl.finalize_run_record, _rid_b, "SPLENDID", db_path=_RUN_DB)
check("an unrecognised status does not raise", _r, True)
check("...and is replaced by FAILED, never by FINISHED -- a run whose ending "
      "could not be described is not a run that ended well",
      at(one(_RUN_DB, "SELECT status FROM runs WHERE id = ?", (_rid_b,)),
         "status"),
      "FAILED")
check("...and is counted, naming the value",
      _dl.RUN_RECORD_FAILURES.get("finalize:unknown_status:SPLENDID"), 1)

_rid_b2 = silence(_dl.start_run_record, "test_source", db_path=_RUN_DB)
_r = silence(_dl.finalize_run_record, _rid_b2,
             _dl.RUN_RECORD_STATUS_RUNNING, db_path=_RUN_DB)
check("RUNNING is unrecognised HERE even though it is a member of "
      "RUN_RECORD_STATUSES, and also becomes FAILED",
      at(one(_RUN_DB, "SELECT status FROM runs WHERE id = ?", (_rid_b2,)),
         "status"),
      "FAILED")

# --- (c) a row that is not there --------------------------------------------
#
# `UPDATE ... WHERE id = ?` against a missing id SUCCEEDS and updates nothing;
# SQLite reports no error for it. Reading rowcount is the entire mechanism.
_r = silence(_dl.finalize_run_record, 999_999, "FINISHED", db_path=_RUN_DB)
check("finalizing a row that is not there returns False", _r, False)
check("...and is counted",
      _dl.RUN_RECORD_FAILURES.get("finalize:row_not_found"), 1)

# --- (d) a database that cannot be opened -----------------------------------
#
# A DIRECTORY where the file should be. Real, unpatched, and sqlite3 answers it
# with an OperationalError out of connect().
_UNOPENABLE = os.path.join(_TMP, "not-a-database")
os.makedirs(_UNOPENABLE, exist_ok=True)
_r, _out = loud(_dl.finalize_run_record, 1, "FINISHED", db_path=_UNOPENABLE)
check("finalizing against an unopenable database returns False, and does not "
      "raise into a caller that has already spent the money", _r, False)
check("...and is counted under the exception type",
      _dl.RUN_RECORD_FAILURES.get("finalize:OperationalError"), 1)
check("...and says so on the console rather than failing silently",
      "could not be finalized" in _out, True)

check("CONTROL: the counter is not simply always moving -- a successful "
      "finalize adds nothing to it",
      (lambda before: (silence(_dl.finalize_run_record,
                               silence(_dl.start_run_record, "test_source",
                                       db_path=_RUN_DB),
                               "FINISHED", db_path=_RUN_DB),
                       sum(_dl.RUN_RECORD_FAILURES.values()) == before)[1]
       )(sum(_dl.RUN_RECORD_FAILURES.values())),
      True)

check("RUN_RECORD_FAILURES has no `start:` key, because start_run_record RAISES "
      "rather than counting and continuing",
      sorted(k for k in _dl.RUN_RECORD_FAILURES if k.startswith("start")), [])

check("the counter is on the run-end degradation report",
      "RUN_RECORD_FAILURES" in __import__(
          "oncotriage.degradation", fromlist=["x"]).registered_names(),
      True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("9. THE CRASHED-RUN SHAPE IS DISTINGUISHABLE IN SQL")
print("=" * 78)
print()

_CRASH_DB = os.path.join(_TMP, "crash.db")

_crashed = silence(_dl.start_run_record, "batch_runner",
                   db_path=_CRASH_DB, fingerprint=_STAMP)
_clean = silence(_dl.start_run_record, "batch_runner",
                 db_path=_CRASH_DB, fingerprint=_STAMP)
_killed = silence(_dl.start_run_record, "batch_runner",
                  db_path=_CRASH_DB, fingerprint=_STAMP)

silence(_dl.finalize_run_record, _clean, "FINISHED", db_path=_CRASH_DB)
silence(_dl.finalize_run_record, _killed, "KILLED", db_path=_CRASH_DB)
# _crashed is deliberately never finalized: that is the SIGKILL / power-loss
# shape, where no handler ran at all.

_UNFINISHED_SQL = ("SELECT id FROM runs WHERE finished_at IS NULL "
                   "AND status = 'RUNNING' ORDER BY id")

check("the never-finalized run is selected by the crashed-run query",
      [r["id"] for r in rows(_CRASH_DB, _UNFINISHED_SQL)], [_crashed])

check("...and the run that CRASHED BUT RAN ITS HANDLER is a different finding, "
      "carrying KILLED and a real finished_at",
      [(r["status"], r["finished_at"] is not None) for r in
       rows(_CRASH_DB, "SELECT status, finished_at FROM runs WHERE id = ?",
            (_killed,))],
      [("KILLED", True)])

check("...and the clean run is neither",
      at(one(_CRASH_DB, "SELECT status FROM runs WHERE id = ?", (_clean,)),
         "status"),
      "FINISHED")

# CONTROL 6: the query stops matching once the row is finalized, so it is
# selecting the STATE and not simply the oldest row.
silence(_dl.finalize_run_record, _crashed, "FINISHED", db_path=_CRASH_DB)
check("CONTROL: once finalized, the same query matches nothing",
      [r["id"] for r in rows(_CRASH_DB, _UNFINISHED_SQL)], [])

# A crashed run's PATIENTS are still attributable, which is the point of writing
# the row first.
_orphan = silence(_dl.start_run_record, "batch_runner",
                  db_path=_CRASH_DB, fingerprint=_STAMP)
silence(_dl.log_inference, _result("mid-crash"), _PATIENT,
        db_path=_CRASH_DB, run_id=_orphan)
check("rows written by a run that never finished are still joinable to it",
      [r["patient_id"] for r in rows(
          _CRASH_DB, "SELECT i.patient_id FROM inferences i "
                     "JOIN runs r ON r.id = i.run_id "
                     "WHERE r.finished_at IS NULL")],
      ["mid-crash"])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("10. NOTHING OUTSIDE THE SCRATCH DIRECTORY WAS TOUCHED")
print("=" * 78)
print()

# Restore the seams before the final comparison, so the production reading below
# is taken with the module in the state every other test will find it in.
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

check("the production database is byte-identical",
      digest(_PRODUCTION_DB), _PRODUCTION_SHA_BEFORE)
check("...and it was READABLE, so that comparison is not 'absent' == 'absent'",
      _PRODUCTION_SHA_BEFORE == "absent", False)
check("oncotriage/storage/database_logger.py is byte-identical",
      digest(_DL_SRC), _DL_SHA_BEFORE)
check("oncotriage/batch/runner.py is byte-identical",
      digest(_RUNNER_SRC), _RUNNER_SHA_BEFORE)
check("every database this file wrote is inside the scratch directory",
      sorted({p for p in (_FRESH, _LEGACY, _RUN_DB, _LOG_DB, _SECOND_DB,
                          _CRASH_DB, _SCRATCH_DB)
              if not os.path.abspath(p).startswith(os.path.abspath(_TMP))}),
      [])

# The scratch paths are dropped from the writer's initialized-database cache so
# a later import in the same process cannot believe a deleted file is migrated.
for _p in (_FRESH, _LEGACY, _RUN_DB, _LOG_DB, _SECOND_DB, _CRASH_DB,
           _SCRATCH_DB):
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(_p))

shutil.rmtree(_TMP, ignore_errors=True)
check("the scratch directory was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print()
    print("FAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 78)


if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Aug 21 2026

@author: ramyalsaffar
"""
