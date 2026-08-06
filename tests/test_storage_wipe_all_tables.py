# Database Wipe Test
####################

"""
``oncotriage/storage/maintenance.py:empty_database`` -- the only destructive
function in the project -- issued an UNCONDITIONAL ``DELETE FROM
sqlite_sequence``. That table exists only once something in the database has
been declared ``AUTOINCREMENT``, so a database without one raised
``sqlite3.OperationalError: no such table: sqlite_sequence`` instead of being
wiped. Pass 20b reported it and did not fix it; pass 20f-1 asks
``sqlite_master`` first.

THE FAILURE WAS TOTAL, NOT PARTIAL, and that is the sharp part. The raise
happened BEFORE ``conn.commit()``, so a wipe that hit it deleted nothing at all
-- every table's rows were still there afterwards. And ``sqlite3.connect``
CREATES a database file that does not exist, so a mistyped path produced an
empty database and then an error naming a table nobody had asked about.

WHAT THIS FILE HOLDS
--------------------
    1. THE GATE IS UNCHANGED. ``flag=False`` deletes nothing and says so;
       neither argument has a default, and calling with fewer raises TypeError.
       This is checked FIRST because everything below arms the flag.
    2. A database WITH sqlite_sequence still wipes, and the sequence is reset.
    3. A database WITHOUT it wipes rather than raising.
    4. A database with NO TABLES AT ALL wipes rather than raising -- the
       mistyped-path case.
    5. The presence check did not become a swallow: an unrelated
       ``OperationalError`` still propagates.
    6. The REAL production schema, read out of the live database's
       ``sqlite_master`` over a read-only URI rather than retyped here, wipes
       to three empty tables.

NO NETWORK, NO KEYS, NO SPEND, AND NOTHING IN THE PROJECT TREE IS WRITTEN.
Every database below lives in a temporary directory. The production
inferences.db is opened ONCE, ``mode=ro``, to read its schema text; it is never
connected to writably and never passed to ``empty_database``.

Run from terminal:
    python tests/test_storage_wipe_all_tables.py

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

import inspect
import shutil
import sqlite3
import stat
import tempfile

from oncotriage import paths as _paths
from oncotriage.storage.maintenance import empty_database


#------------------------------------------------------------------------------


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
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def raises(fn):
    """(exception type name, message) for a call that must raise, else (None, '')."""
    try:
        fn()
    except Exception as exc:            # noqa: BLE001 -- the type is the answer
        return type(exc).__name__, str(exc)
    return None, ""


_TMP = tempfile.mkdtemp(prefix="oncotriage-wipe-")


def _db(name, *statements):
    """Build a scratch database from `statements` and return its path."""
    path = os.path.join(_TMP, name)
    conn = sqlite3.connect(path)
    for statement in statements:
        conn.execute(statement)
    conn.commit()
    conn.close()
    return path


def _tables(path):
    conn = sqlite3.connect(path)
    try:
        return sorted(r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"))
    finally:
        conn.close()


def _count(path, table):
    conn = sqlite3.connect(path)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        conn.close()


# ===========================================================================
# SECTION 1: THE GATE IS UNCHANGED
# ===========================================================================
# Checked before anything below arms the flag. The two properties are the
# safety of the whole file: no default may mean "production", and no default
# may mean "do it".

print("=" * 70)
print("Section 1: the gate -- both arguments required, False deletes nothing")
print("=" * 70)

_signature = inspect.signature(empty_database)
check("empty_database takes exactly (db_path, flag)",
      list(_signature.parameters), ["db_path", "flag"])
check("...and NEITHER has a default",
      [p.default is inspect.Parameter.empty
       for p in _signature.parameters.values()],
      [True, True])
check("calling it with no arguments raises TypeError rather than wiping "
      "anything",
      raises(lambda: empty_database())[0], "TypeError")

_gated = _db("gated.db",
             "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)",
             "INSERT INTO t (v) VALUES ('keep me')")
empty_database(_gated, False)
check("flag=False leaves every row in place",
      _count(_gated, "t"), 1)


# ===========================================================================
# SECTION 2: A DATABASE WITH sqlite_sequence
# ===========================================================================

print()
print("=" * 70)
print("Section 2: a database WITH sqlite_sequence")
print("=" * 70)

_with = _db("with_sequence.db",
            "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)",
            "INSERT INTO t (v) VALUES ('a')",
            "INSERT INTO t (v) VALUES ('b')")

check("the fixture really carries sqlite_sequence (non-degeneracy)",
      "sqlite_sequence" in _tables(_with), True)
check("...and really carries rows (non-degeneracy)",
      _count(_with, "t"), 2)

empty_database(_with, True)

check("every row is gone", _count(_with, "t"), 0)
check("...the tables are preserved", "t" in _tables(_with), True)
check("...and sqlite_sequence was emptied, so ids restart at 1",
      _count(_with, "sqlite_sequence"), 0)


# ===========================================================================
# SECTION 3: A DATABASE WITHOUT sqlite_sequence
# ===========================================================================
# The defect. INTEGER PRIMARY KEY without AUTOINCREMENT is the ordinary rowid
# alias, and SQLite creates no sequence table for it.

print()
print("=" * 70)
print("Section 3: a database WITHOUT sqlite_sequence")
print("=" * 70)

_without = _db("without_sequence.db",
               "CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)",
               "CREATE TABLE u (v TEXT)",
               "INSERT INTO t (v) VALUES ('a')",
               "INSERT INTO u (v) VALUES ('b')")

check("the fixture really lacks sqlite_sequence (non-degeneracy)",
      "sqlite_sequence" in _tables(_without), False)

_type, _message = raises(lambda: empty_database(_without, True))
check("the wipe does not raise", (_type, _message), (None, ""))
check("...every row in every table is gone",
      (_count(_without, "t"), _count(_without, "u")), (0, 0))
check("...and both tables are preserved",
      _tables(_without), ["t", "u"])


# ===========================================================================
# SECTION 4: A DATABASE WITH NO TABLES AT ALL
# ===========================================================================
# What a mistyped path produces, because sqlite3.connect CREATES the file. The
# right answer is a clean no-op with the "Database cleared" message, not an
# error about a table the caller never mentioned.

print()
print("=" * 70)
print("Section 4: an empty database file")
print("=" * 70)

_empty = _db("empty.db")
check("the fixture really has no tables (non-degeneracy)", _tables(_empty), [])

_type4, _message4 = raises(lambda: empty_database(_empty, True))
check("wiping an empty database does not raise", (_type4, _message4), (None, ""))


# ===========================================================================
# SECTION 5: THE PRESENCE CHECK IS NOT A SWALLOW
# ===========================================================================
# A `try: DELETE FROM sqlite_sequence / except sqlite3.OperationalError: pass`
# would also have made sections 3 and 4 pass, and would have hidden every other
# OperationalError the wipe can meet. This shows a real one still propagates:
# the file is read-only, so the first DELETE cannot be applied.

print()
print("=" * 70)
print("Section 5: an unrelated OperationalError still propagates")
print("=" * 70)

_ro = _db("readonly.db",
          "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)",
          "INSERT INTO t (v) VALUES ('a')")
os.chmod(_ro, stat.S_IRUSR)
try:
    _type5, _message5 = raises(lambda: empty_database(_ro, True))
finally:
    os.chmod(_ro, stat.S_IRUSR | stat.S_IWUSR)

check("a read-only database still raises OperationalError",
      _type5, "OperationalError")
check("...and the message is about the write, not about sqlite_sequence",
      "readonly" in _message5.lower(), True)


# ===========================================================================
# SECTION 6: THE REAL PRODUCTION SCHEMA
# ===========================================================================
# The schema is READ OUT OF THE LIVE DATABASE rather than retyped here -- the
# same discipline Files 18 and 19 use for their row-count guard -- so this
# section keeps testing the shape the wipe will actually meet. The connection
# is a mode=ro URI: a plain connect on an absent path would CREATE the
# production database, and a guard that brings its own subject into existence
# is worse than no guard.

print()
print("=" * 70)
print("Section 6: the real production schema wipes")
print("=" * 70)

_production = _paths.inferences_path
_schema = []
if os.path.exists(_production):
    _conn = sqlite3.connect(f"file:{_production}?mode=ro", uri=True)
    try:
        _schema = [row[0] for row in _conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND sql IS NOT NULL")]
    finally:
        _conn.close()

check("the production schema was read and is non-degenerate",
      len(_schema) >= 3, True)

if _schema:
    _clone = _db("production_shape.db", *_schema)
    _clone_tables = [t for t in _tables(_clone) if not t.startswith("sqlite_")]
    check("the clone carries the production tables",
          sorted(_clone_tables), sorted(["drift_metrics", "inferences",
                                         "trial_matches"]))
    _type6, _message6 = raises(lambda: empty_database(_clone, True))
    check("the production shape wipes without raising",
          (_type6, _message6), (None, ""))
    check("...and every table survives with zero rows",
          sorted((t, _count(_clone, t)) for t in _clone_tables),
          sorted((t, 0) for t in _clone_tables))

# The production database itself must be exactly as it was: this file opened it
# read-only and never handed it to empty_database.
check("the production database still exists and was never wiped by this file",
      os.path.exists(_production), True)


shutil.rmtree(_TMP, ignore_errors=True)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

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
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
