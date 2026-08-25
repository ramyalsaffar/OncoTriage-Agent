# Ablation Write Durability Test
###############################

"""The study database got the hardening the inference database already had.

WHAT WAS WRONG, MEASURED BEFORE ANYTHING MOVED.
``oncotriage/ablation/study.py`` writes ``ablation_results.db`` with the same
shape of concurrency ``inferences.db`` has -- a thread pool, a done-callback
inserting one row per completed patient -- and it had none of the hardening that
one grew over three passes: six bare ``sqlite3.connect`` calls carrying
sqlite3's 5-second default busy timeout, the rollback journal (so a reader
blocks the writer and the writer blocks a reader), and no retry of any kind. A
"database is locked" there was caught by ``log_ablation_result``'s broad handler,
printed as a WARNING and the row was gone -- and an ablation row is one live
Stage 5 call, which is the same money an inference row costs.

WHAT THIS FILE HOLDS

    1. THE POLICY HAS ONE OWNER. ``open_connection``, ``apply_journal_mode`` and
       ``run_with_write_retry`` come from
       ``oncotriage/storage/database_logger.py`` -- the same three functions,
       the same four SQLITE_* constants, the same ``_is_retryable`` -- rather
       than a second copy here. Asserted BY IDENTITY, because "it behaves the
       same" is what a second copy also does until it drifts.
    2. THE JOURNAL MODE IS APPLIED AND VERIFIED, once, at init.
    3. THE BUSY TIMEOUT IS ON EVERY CONNECTION the module opens, which is what
       ``open_connection`` is for.
    4. THE RETRY IS NARROW AND IT IS DRIVEN. Real contention -- a genuine
       ``BEGIN EXCLUSIVE`` from a second connection -- is survived; a TERMINAL
       error is not retried, because retrying one spends
       SQLITE_WRITE_MAX_ATTEMPTS x SQLITE_BUSY_TIMEOUT_SECONDS to arrive at the
       same failure.
    5. THE TWO INDEXES EXIST and the planner USES them -- an index nothing plans
       against is a write cost with no read benefit.
    6. THE READERS CANNOT CREATE A DATABASE. ``sqlite3.connect`` on an absent
       path CREATES an empty file, so ``--db`` pointed one directory wrong
       reported a study with no rows -- indistinguishable from a study that
       produced none. Both readers go through a read-only URI now.
    7. NO ``sqlite3.connect`` IS LEFT IN EITHER FILE, by AST, with the
       non-degeneracy probe that the scan can see one at all.

NO NETWORK, NO KEYS, NO SPEND, no live Qdrant, no model load, no corpus, no git
history, no live server: every database here is a scratch file inside a
``tempfile.mkdtemp`` that is removed and asserted gone, and no graph is compiled.

IT EXECS NOTHING, so it needs no ``_EXEC_ALLOWLIST`` entry: every control is a
real failing condition created on disk (a second connection holding an exclusive
lock, an absent path, a table that is not there) or a different INPUT to a pure
function.

NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes nothing in
the repository, and the two files it READS -- ``oncotriage/ablation/study.py``
and ``oncotriage/ablation/analysis.py`` -- are written by neither of the suite's
two writers (``oncotriage/registries/cancer_code_registry.py`` and
``oncotriage/config.py``). Both are sha256-compared at the end anyway.

Run from terminal:
    python tests/test_ablation_write_durability.py

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

import ast
import contextlib
import hashlib
import io
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from oncotriage import config as _config
from oncotriage.ablation import analysis as _analysis
from oncotriage.ablation import common as _common
from oncotriage.ablation import study as _study
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


def check_true(label, actual):
    check(label, bool(actual), True)


def check_raises(label, exc_type, fn, *args, **kwargs):
    """Require a raise. RETURNS the exception or a STRING marker, never None.

    Never re-raises: a helper that let the exception escape would abort this
    file at exactly the check that tests for it. Never returns None either, so
    the message assertions that follow a refusal fail with the failure they owe
    rather than with an AttributeError.
    """
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
        return exc
    except BaseException as exc:                       # noqa: BLE001 -- reported
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          raised {type(exc).__name__}: {exc}")
        print(f"  FAIL  {label} -- raised {type(exc).__name__}: {exc}")
        return f"<raised {type(exc).__name__}: {exc}>"
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          nothing was raised")
    print(f"  FAIL  {label} -- nothing was raised")
    return f"<did not raise {exc_type.__name__}>"


@contextlib.contextmanager
def quiet():
    """Swallow console output. ``console.out`` writes to STDERR, so both go.

    The schema migrations are chatty and this file calls them several times;
    the redirect is the same one tests/test_storage_schema_guards.py uses.
    """
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


_TMP = tempfile.mkdtemp(prefix="ablation-durability-")
_STUDY_PY = os.path.abspath(_study.__file__)
_ANALYSIS_PY = os.path.abspath(_analysis.__file__)
_STUDY_SHA = _digest(_STUDY_PY)
_ANALYSIS_SHA = _digest(_ANALYSIS_PY)


#------------------------------------------------------------------------------


# ===========================================================================
# 1. THE POLICY HAS ONE OWNER
# ===========================================================================
#
# BY IDENTITY, NOT BY BEHAVIOUR. A second copy of the retry loop here would
# behave identically today and would drift the first time either side's backoff
# or its definition of "transient" moved -- which is the shape this project has
# had to remove for the BM25 sparse model, the cross-encoder checkpoint and the
# latest-run-per-config SQL. `is` is the only assertion that rules it out.

print("=" * 74)
print("1. THE WRITE POLICY IS THE STORAGE LAYER'S, NOT A COPY")
print("=" * 74)

check("1a  open_connection is the storage layer's own function",
      _study.open_connection is _dl.open_connection, True)
check("1b  apply_journal_mode is too",
      _study.apply_journal_mode is _dl.apply_journal_mode, True)
check("1c  and so is the retry",
      _study.run_with_write_retry is _dl.run_with_write_retry, True)
# THE THREE NAMES THE RETRY READS, BY AST OVER ITS OWN BODY. Identity above
# says the study calls the storage layer's function; this says that function is
# built out of the storage layer's own classifier and the storage layer's own
# two constants, rather than out of a second definition of "transient" and a
# second backoff that happen to sit in the same file. Both halves are needed:
# one policy means one implementation AND one set of values.
_DL_TREE = ast.parse(Path(_dl.__file__).read_text(encoding="utf-8"), _dl.__file__)
_RETRY_DEF = next((n for n in ast.walk(_DL_TREE)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "run_with_write_retry"), None)
check_true("1d  run_with_write_retry was found (non-degeneracy for 1e)",
           _RETRY_DEF is not None)
_RETRY_NAMES = {n.id for n in ast.walk(_RETRY_DEF)
                if isinstance(n, ast.Name)} if _RETRY_DEF else set()
check("1e  it reads the storage layer's own classifier and its two constants, "
      "so 'one policy' is true of the values as well as of the code",
      sorted(_RETRY_NAMES & {"_is_retryable", "SQLITE_WRITE_MAX_ATTEMPTS",
                             "SQLITE_WRITE_RETRY_BASE_DELAY"}),
      ["SQLITE_WRITE_MAX_ATTEMPTS", "SQLITE_WRITE_RETRY_BASE_DELAY",
       "_is_retryable"])


#------------------------------------------------------------------------------


# ===========================================================================
# 2. THE JOURNAL MODE AND THE BUSY TIMEOUT
# ===========================================================================

print()
print("=" * 74)
print("2. WAL AND THE BUSY TIMEOUT")
print("=" * 74)

_DB = os.path.join(_TMP, "study.db")
with quiet():
    _study.init_ablation_db(_DB)

_conn = sqlite3.connect(_DB)
check("2a  init_ablation_db leaves the database in the configured journal mode",
      str(_conn.execute("PRAGMA journal_mode").fetchone()[0]).lower(),
      str(_config.SQLITE_JOURNAL_MODE).lower())
check_true("2b  ...which is NOT sqlite's default (non-degeneracy: at 'delete' "
           "this check passes without the pragma having been issued at all)",
           str(_config.SQLITE_JOURNAL_MODE).lower() != "delete")
_conn.close()

_probe = _study.open_connection(_DB)
check("2c  open_connection applies the busy timeout, in MILLISECONDS as the "
      "pragma reports it",
      _probe.execute("PRAGMA busy_timeout").fetchone()[0],
      int(_config.SQLITE_BUSY_TIMEOUT_SECONDS * 1000))
_probe.close()

_plain = sqlite3.connect(_DB)
check_true("2d  ...and a plain sqlite3.connect does NOT -- which is what the "
           "six bare connects this pass replaced were carrying "
           "(non-degeneracy)",
           _plain.execute("PRAGMA busy_timeout").fetchone()[0]
           != int(_config.SQLITE_BUSY_TIMEOUT_SECONDS * 1000))
_plain.close()


#------------------------------------------------------------------------------


# ===========================================================================
# 3. THE RETRY IS DRIVEN AGAINST REAL CONTENTION
# ===========================================================================
#
# NOT A PATCHED EXCEPTION. A second connection takes a genuine
# `BEGIN EXCLUSIVE`, a background thread releases it after a delay shorter than
# the retry budget, and the write has to survive. That is the fault this retry
# exists for, produced the way SQLite produces it.

print()
print("=" * 74)
print("3. THE RETRY, AGAINST A REAL LOCK")
print("=" * 74)

import threading                                                  # noqa: E402

_LOCK_DB = os.path.join(_TMP, "contended.db")
with quiet():
    _study.init_ablation_db(_LOCK_DB)

# `check_same_thread=False` IS LOAD-BEARING AND IT HUNG THE FIRST VERSION OF
# THIS FILE. sqlite3 refuses a connection used from a thread other than the one
# that created it, so the releasing thread's `rollback()` raised inside a daemon
# thread where nothing reads it -- the lock was never released, and the write
# under test then waited out four attempts x the 30-second busy timeout. A
# scenario that cannot release its own lock is a scenario that measures the
# timeout rather than the retry.
_blocker = sqlite3.connect(_LOCK_DB, timeout=30, check_same_thread=False)
_blocker.execute("BEGIN EXCLUSIVE")
_released = threading.Event()


def _release_soon():
    # SHORTER THAN THE RETRY BUDGET AND LONGER THAN THE FIRST ATTEMPT, so the
    # first attempt genuinely meets the lock. The busy timeout is 30 s, so the
    # first attempt would eventually win on its own -- which is why this is a
    # test of "the write survives contention" rather than of "the retry fired".
    time.sleep(0.4)
    _blocker.rollback()
    _released.set()


threading.Thread(target=_release_soon, daemon=True).start()

_attempts = {"n": 0}


def _contended_write():
    _attempts["n"] += 1
    conn = _study.open_connection(_LOCK_DB)
    try:
        conn.execute("INSERT INTO ablation_runs (run_timestamp, config_name, "
                     "status) VALUES (?, ?, ?)",
                     ("2026-08-25T00:00:00", "full_pipeline", "RUNNING"))
        conn.commit()
    finally:
        conn.close()


_dl.run_with_write_retry(_contended_write, "a contended probe row")
_released.wait(5)
_conn = sqlite3.connect(_LOCK_DB)
check("3a  a write meeting a real exclusive lock still lands",
      _conn.execute("SELECT COUNT(*) FROM ablation_runs").fetchone()[0], 1)
_conn.close()
_blocker.close()

# A TERMINAL ERROR IS NOT RETRIED, and the count is how that is measured:
# retrying a missing table spends the whole budget to arrive at the same
# failure, four times.
_terminal_attempts = {"n": 0}


def _terminal_write():
    _terminal_attempts["n"] += 1
    conn = _study.open_connection(_LOCK_DB)
    try:
        conn.execute("INSERT INTO no_such_table (a) VALUES (1)")
        conn.commit()
    finally:
        conn.close()


check_raises("3b  a TERMINAL error is re-raised rather than swallowed",
             sqlite3.OperationalError,
             _dl.run_with_write_retry, _terminal_write, "a doomed probe row")
check("3c  ...after exactly ONE attempt -- the retry is for contention, and "
      "retrying a missing table spends the whole budget to fail identically",
      _terminal_attempts["n"], 1)
check_true("3d  ...and the budget really is more than one, so 3c is a "
           "statement about the classifier rather than about the loop bound",
           int(_config.SQLITE_WRITE_MAX_ATTEMPTS) > 1)


#------------------------------------------------------------------------------


# ===========================================================================
# 4. THE TWO INDEXES, AND THE PLANNER USES THEM
# ===========================================================================

print()
print("=" * 74)
print("4. THE INDEXES ON ablation_results")
print("=" * 74)

_conn = sqlite3.connect(_DB)
_INDEXES = sorted(r[0] for r in _conn.execute(
    "SELECT name FROM sqlite_master WHERE type='index' "
    "AND tbl_name='ablation_results' AND name NOT LIKE 'sqlite_%'"))
check("4a  init_ablation_db creates exactly the two access paths this table is "
      "read by, and no others -- pinned EXACT so an addition has to be made "
      "here, beside its reason",
      _INDEXES, ["idx_ablation_results_config_patient",
                 "idx_ablation_results_run_id"])

check_true("4b  the run_id JOIN the whole analysis side reads through actually "
           "plans against its index",
           any("idx_ablation_results_run_id" in str(r) for r in _conn.execute(
               "EXPLAIN QUERY PLAN SELECT * FROM ablation_results "
               "WHERE run_id = 1")))
check_true("4c  ...and the (config_name, patient_id) pair -- the checkpoint's "
           "own membership question -- plans against the composite",
           any("idx_ablation_results_config_patient" in str(r)
               for r in _conn.execute(
                   "EXPLAIN QUERY PLAN SELECT * FROM ablation_results "
                   "WHERE config_name = 'x' AND patient_id = 'y'")))
check_true("4d  ...and config_name ALONE is served by the same index as a "
           "leftmost prefix, which is why generate_summary needs no third one",
           any("idx_ablation_results_config_patient" in str(r)
               for r in _conn.execute(
                   "EXPLAIN QUERY PLAN SELECT * FROM ablation_results "
                   "WHERE config_name = 'x'")))
# THE COST OF THE ORDER, STATED RATHER THAN GLOSSED: patient_id alone is NOT a
# leftmost prefix, so it is not served. That is deliberate -- a patient always
# appears with a configuration here -- and it is pinned so the trade is visible.
check_true("4e  ...while patient_id ALONE is NOT served, which is the stated "
           "cost of putting config_name first",
           not any("idx_ablation_results_config_patient" in str(r)
                   for r in _conn.execute(
                       "EXPLAIN QUERY PLAN SELECT * FROM ablation_results "
                       "WHERE patient_id = 'y'")))
_conn.close()

# IDEMPOTENT, like every other CREATE in that function.
with quiet():
    _study.init_ablation_db(_DB)
_conn = sqlite3.connect(_DB)
check("4f  re-initialising creates nothing new (IF NOT EXISTS)",
      sorted(r[0] for r in _conn.execute(
          "SELECT name FROM sqlite_master WHERE type='index' "
          "AND tbl_name='ablation_results' AND name NOT LIKE 'sqlite_%'")),
      _INDEXES)
_conn.close()


#------------------------------------------------------------------------------


# ===========================================================================
# 5. THE READERS CANNOT CREATE A DATABASE
# ===========================================================================

print()
print("=" * 74)
print("5. THE READ PATH IS READ-ONLY")
print("=" * 74)

_ABSENT = os.path.join(_TMP, "no_such_study.db")
check_true("5a-pre the fixture path really does not exist (non-degeneracy)",
           not os.path.exists(_ABSENT))
_ABSENT_RAISED = check_raises(
    "5a  the read-only opener REFUSES an absent path",
    _common.MissingAblationDatabaseError,
    _common.open_ablation_db_readonly, _ABSENT)
check("5b  ...and creates NOTHING there, which is the half a typed exception "
      "alone does not give you -- a plain sqlite3.connect would have made an "
      "empty database and reported a study with no results",
      os.path.exists(_ABSENT), False)
check_true("5c  ...naming the path",
           _ABSENT in str(_ABSENT_RAISED) or "no_such_study" in str(_ABSENT_RAISED))
check_true("5d  MissingAblationDatabaseError is NOT a sqlite3.Error, so the "
           "broad handlers both readers sit under cannot swallow it",
           not issubclass(_common.MissingAblationDatabaseError, sqlite3.Error))

_ro = _common.open_ablation_db_readonly(_DB)
check("5e  it READS a real database (non-degeneracy: a connection that refused "
      "everything would satisfy 5f too)",
      _ro.execute("SELECT COUNT(*) FROM ablation_results").fetchone()[0], 0)
check_raises("5f  ...and REFUSES to write",
             sqlite3.OperationalError, _ro.execute,
             "INSERT INTO ablation_runs (run_timestamp, config_name, status) "
             "VALUES ('x', 'y', 'z')")
_ro.close()


#------------------------------------------------------------------------------


# ===========================================================================
# 6. NO BARE CONNECT IS LEFT, BY AST
# ===========================================================================
#
# The behavioural checks above cover the paths this file drives. This covers the
# ones it does not: a connect site added tomorrow, or one of the six that was
# missed. It is a scan over the shipped source with its own non-degeneracy
# probe, because a scan that can no longer see a call would report zero.

print()
print("=" * 74)
print("6. NO sqlite3.connect LEFT IN EITHER FILE")
print("=" * 74)


def _raw_connect_lines(path):
    tree = ast.parse(Path(path).read_text(encoding="utf-8"), path)
    return sorted(
        n.lineno for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "connect"
        and isinstance(n.func.value, ast.Name) and n.func.value.id == "sqlite3")


check("6a  oncotriage/ablation/study.py opens no connection of its own",
      _raw_connect_lines(_STUDY_PY), [])
check("6b  oncotriage/ablation/analysis.py opens none either",
      _raw_connect_lines(_ANALYSIS_PY), [])
check("6c  the scan can SEE one when there is one (non-degeneracy: a walk that "
      "matched nothing would report both files clean)",
      len(_raw_connect_lines(os.path.abspath(_common.__file__))), 1)


#------------------------------------------------------------------------------


# ===========================================================================
# 7. NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================

print()
print("=" * 74)
print("7. THE REPOSITORY IS UNCHANGED")
print("=" * 74)

check("7a  oncotriage/ablation/study.py is byte-identical",
      _digest(_STUDY_PY), _STUDY_SHA)
check("7b  oncotriage/ablation/analysis.py is byte-identical",
      _digest(_ANALYSIS_PY), _ANALYSIS_SHA)
check_true("7c  ...and the two hashes are not the same value (non-degeneracy: "
           "comparing one file with itself would pass for free)",
           _STUDY_SHA != _ANALYSIS_SHA)

shutil.rmtree(_TMP, ignore_errors=True)
check("7d  the scratch directory was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 74)
print("SUMMARY")
print("=" * 74)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFAILURES:")
    for _failure in _FAILURES:
        print(f"  - {_failure}")
print()

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 25 2026

@author: ramyalsaffar
"""
