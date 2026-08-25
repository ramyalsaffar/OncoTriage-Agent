# Health-Flush-On-Failure Test
#############################

"""
A PASS IN WHICH EVERY PATIENT FAILS AT ``future.result()`` NOW PERSISTS ITS
HEALTH RECORD.

``flush_health`` is hosted in ``_on_done`` rather than in ``save_checkpoint``
for a reason the runner writes out in full: the checkpoint call is inside
``if entry["status"] == "success":``, so hanging the health record off it would
leave a pass in which every patient ERRORED with nothing persisted -- and errors
are exactly when REFUSALS_OBSERVED, MALFORMED_EVALUATION_ENTRIES and
INFERENCE_WRITE_FAILURES move. Silence looking like health.

THE SAME ARGUMENT REACHED ONE BRANCH FURTHER OUT AND STOPPED. ``_on_done`` has
three EARLY returns above the line that flushes -- a cancelled future, a
``MatchingModelMismatchError``, and anything else escaping the worker -- and a
callback taking any of them returned before the flush. So a campaign in which
every patient died at ``future.result()`` ran for hours with an EMPTY health
record while the counters climbed, and nothing outside the process could see any
of it. The runner's own note recorded this as "only the liveness of the record
is [lost]", which understated it: the record was empty, not stale.

WHAT THIS FILE HOLDS
--------------------
    1. THE REAL ``run_batch``, driven with a ``process_patient`` stand-in that
       RAISES for every patient. ``run_metrics`` must carry that run's rows.
    2. THE SAME DRIVE WITH THE MISMATCH ERROR, which has its own branch.
    3. THE NON-DEGENERACY ARM: patients that SUCCEED also flush, so the check
       above is about the failing path rather than about flushing at all.
    4. THE CancelledError BRANCH DELIBERATELY DOES NOT FLUSH, asserted
       structurally AND behaviourally -- it is an exclusion with a cost
       argument behind it, not an omission, and an exclusion nothing checks is
       one somebody deletes.
    5. CONTROLS -- the flush sites located by AST, with the branch each sits
       in named, and a planted removal shown to be visible.

NO NETWORK, NO KEYS, **NO SPEND**, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO
GIT HISTORY, NO LIVE SERVER: ``process_patient`` is a stand-in and THE GRAPH IS
NEVER INVOKED, so no billed call is reachable. ``save_checkpoint`` is a stand-in
too and that is FORCED rather than convenient -- the real one resolves
``run_fingerprint.current()``, which probes the index over the wire. Everything
else is the real thing: ``run_batch``, ``_on_done``, ``flush_health``,
``append_result``, ``start_run_record``. NOT in the collision matrix -- every
file it writes is inside a ``tempfile.mkdtemp`` it removes and asserts gone, and
the one package file it reads is written by neither of the suite's two writers
and is sha256-compared at the end. It EXECS NOTHING.

Run from terminal:
    python tests/test_runner_health_flush_on_failure.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
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

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import contextlib
import hashlib
import io
import shutil
import sqlite3
import tempfile


#------------------------------------------------------------------------------


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


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


def drive(fn, *args, **kwargs):
    """Call `fn`, turning a raise into a value `check` can fail on.

    Never a bare call inside a check() argument list: a raise there escapes
    while the argument is being evaluated and the run reports one traceback
    where it owes a summary.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return ("<RAISED>", type(exc).__name__, str(exc)[:200])


def silence(fn, *args, **kwargs):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        return fn(*args, **kwargs)


_TMP = tempfile.mkdtemp(prefix="oncotriage-flushfail-")

from oncotriage import paths as _paths                          # noqa: E402
_PATHS_SAVED = dict(_paths._RESOLVED)
_paths._RESOLVED["checkpoint_path"] = _TMP + os.sep
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "never-written.db")

from oncotriage.batch import runner as _runner                  # noqa: E402
from oncotriage.storage import database_logger as _dl           # noqa: E402
from oncotriage.agent.evaluation import MatchingModelMismatchError  # noqa: E402

_RUNNER_FILE = os.path.abspath(_runner.__file__)
_HASH_BEFORE = hashlib.sha256(open(_RUNNER_FILE, "rb").read()).hexdigest()

_DB = os.path.join(_TMP, "health.db")
silence(_dl.initialize_database, _DB)


def _metric_rows(run_id):
    conn = sqlite3.connect(_DB)
    try:
        return conn.execute(
            "SELECT category, name, value FROM run_metrics WHERE run_id = ? "
            "ORDER BY category, name", (run_id,)).fetchall()
    finally:
        conn.close()


# THE THREE STAND-INS, AND WHY EACH IS ONE.
#
#   process_patient   ONE LIVE BILLED Stage 5 CALL per patient. Replaced, and
#                     the graph is never invoked, so no spend is reachable.
#   save_checkpoint   the real one resolves run_fingerprint.current(), which
#                     PROBES THE INDEX OVER THE WIRE. A file whose header says
#                     "no network" may not call it. It is reached only on the
#                     success arm, which is section 3.
#   tqdm progress     left alone: run_batch builds it and it writes to stderr,
#                     which silence() captures.
_REAL_PATIENT = _runner.process_patient
_REAL_SAVE = _runner.save_checkpoint
_SAVES = []


def _install(patient):
    _runner.process_patient = patient
    _runner.save_checkpoint = lambda *a, **k: _SAVES.append(a)


def _restore():
    _runner.process_patient = _REAL_PATIENT
    _runner.save_checkpoint = _REAL_SAVE


def _run(patient, files=("a.json", "b.json", "c.json")):
    """Drive the REAL run_batch with `patient` as the worker, return the run id.

    ``run_batch`` ITSELF RAISES ON A FAILING FUTURE and that is the shipped
    design, not an accident of this harness: its wait loop catches
    ``CancelledError`` and nothing else, so anything escaping a worker leaves
    the function by exception and lands in ``main()``'s crash handler. The
    outcome is captured rather than allowed to escape, because what this file
    asserts is what the CALLBACK did before that -- and on the failing arm the
    callback's flush is the only health record that run will ever have.
    """
    run_id = silence(_dl.start_run_record, "flush-test", db_path=_DB)
    _install(patient)
    try:
        outcome = drive(silence, _runner.run_batch, list(files), object(), [],
                        object(), set(), [], run_id=run_id, db_path=_DB)
    finally:
        _restore()
    return run_id, outcome


def _raising_patient(**kwargs):
    raise RuntimeError("the worker died")


def _mismatch_patient(**kwargs):
    # TWO ARGUMENTS, BOTH REQUIRED. `MatchingModelMismatchError(configured,
    # returned)` builds its own message; a one-argument call raises TypeError
    # from the constructor, which then travels the GENERIC branch instead of
    # the mismatch one -- so the arm would have measured the wrong handler and
    # passed. Found by driving it, not by reading.
    raise MatchingModelMismatchError("gpt-5.6-terra", "gpt-4o-2024-08-06")


def _succeeding_patient(**kwargs):
    return {"patient_id": os.path.basename(kwargs.get("fhir_path", "p")),
            "status": "success", "eligible_matches": 0, "near_misses": 0,
            "not_evaluable": 0, "total_time": 0.0,
            "timestamp": "2026-08-25T00:00:00",
            "is_resample": kwargs.get("is_resample", False)}


#------------------------------------------------------------------------------


print("=" * 78)
print("1. EVERY PATIENT DIES IN THE GENERIC HANDLER -- THE RECORD IS STILL "
      "WRITTEN")
print("=" * 78)
print()

_rid_err, _out_err = _run(_raising_patient)
_rows_err = _metric_rows(_rid_err)

check("run_batch really left by exception, which is what makes the "
      "callback's flush the only record (non-degeneracy)",
      isinstance(_out_err, tuple) and _out_err[:2] == ("<RAISED>",
                                                       "RuntimeError"), True)
check("the run produced run_metrics rows even though not one patient "
      "succeeded, and run_batch never returned",
      len(_rows_err) > 0, True)
check("...and the meta row says how many counters were registered, which is "
      "what separates 'measured clean' from 'no health record'",
      [r for r in _rows_err
       if r[0] == _dl.RUN_METRIC_CATEGORY_META
       and r[1] == _dl.RUN_METRIC_META_COUNTERS_REGISTERED] != [], True)
check("...and it recorded a non-degenerate registry (a zero here would make "
      "the row above true of a registry with nothing in it)",
      [r[2] for r in _rows_err
       if r[1] == _dl.RUN_METRIC_META_COUNTERS_REGISTERED][0] > 0, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. THE MISMATCH BRANCH, WHICH IS ITS OWN early return")
print("=" * 78)
print()

_rid_mis, _out_mis = _run(_mismatch_patient)
check("run_batch left by exception on this arm too (non-degeneracy)",
      isinstance(_out_mis, tuple)
      and _out_mis[:2] == ("<RAISED>", "MatchingModelMismatchError"), True)
check("a pass in which every patient died with MatchingModelMismatchError "
      "also persisted its health record",
      len(_metric_rows(_rid_mis)) > 0, True)
check("...and it is a DIFFERENT run's rows, so the check above is not reading "
      "the previous run's (non-degeneracy)",
      _rid_mis != _rid_err, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. THE NON-DEGENERACY ARM: SUCCESS FLUSHES TOO")
print("=" * 78)
print()

# Without this the two sections above are equally satisfied by a flush that
# happens unconditionally at the top of the callback -- which would ALSO fire
# for a cancelled patient, the one case section 4 says must not.
_SAVES.clear()
_rid_ok, _out_ok = _run(_succeeding_patient)
check("run_batch RETURNED on this arm rather than raising, which is the "
      "difference between it and the two above",
      isinstance(_out_ok, tuple) and _out_ok and _out_ok[0] == "<RAISED>",
      False)
check("a pass in which every patient SUCCEEDED persisted its health record",
      len(_metric_rows(_rid_ok)) > 0, True)
check("...and the success path really ran (non-degeneracy: the stand-in "
      "checkpoint was called once per patient)",
      len(_SAVES), 3)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. THE CANCELLED BRANCH DELIBERATELY DOES NOT FLUSH")
print("=" * 78)
print()

# A cancelled patient WAS NEVER ATTEMPTED, so no counter moved because of it and
# its flush would write byte-identical rows. What it would add is one
# DELETE-plus-INSERT per QUEUED patient at the moment the operator has asked the
# process to go away. That is an exclusion with an argument behind it; an
# exclusion nothing checks is one the next reader deletes for symmetry.

_src = open(_RUNNER_FILE, encoding="utf-8").read()
_tree = ast.parse(_src)

_on_dones = [n for n in ast.walk(_tree)
             if isinstance(n, ast.FunctionDef) and n.name == "_on_done"]
check("the module defines two _on_done callbacks -- run_batch's and "
      "run_resample's (non-degeneracy: a walk that found none would make "
      "every structural check below pass for free)",
      len(_on_dones), 2)


def _handler_map(fn):
    """{exception name: does this handler call flush_health} for `fn`'s try."""
    out = {}
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            # THE TYPE ONLY. `ast.unparse(handler.type)` is "Exception", never
            # "Exception as e" -- the bound name is `handler.name`, a separate
            # field -- and the first version of this map expected the latter,
            # so every structural check reported None instead of a verdict.
            # Caught by running.
            name = (ast.unparse(handler.type) if handler.type else "<bare>")
            calls = {
                c.func.id for c in ast.walk(handler)
                if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
            out[name] = "flush_health" in calls
    return out


for _fn in _on_dones:
    _map = _handler_map(_fn)
    check(f"_on_done's handlers are the three this file is about "
          f"({len(_map)} found)",
          sorted(_map), ["CancelledError", "Exception",
                         "MatchingModelMismatchError"])
    check("...the two FAILING handlers flush",
          (_map.get("MatchingModelMismatchError"),
           _map.get("Exception")), (True, True))
    check("...and the CANCELLED handler does not, which is the argued "
          "exclusion rather than an omission",
          _map.get("CancelledError"), False)

# CONTROL: the map really discriminates. Strip the flush call out of an
# in-memory AST copy of one handler and the map must report False for it --
# without this, a _handler_map that always answered True would pass every line
# above.
_copy = ast.parse(_src)
_target = [n for n in ast.walk(_copy)
           if isinstance(n, ast.FunctionDef) and n.name == "_on_done"][0]
_stripped = 0
for _node in ast.walk(_target):
    if isinstance(_node, ast.Try):
        for _h in _node.handlers:
            if _h.type is not None and ast.unparse(_h.type) == "Exception":
                _keep = []
                for _stmt in _h.body:
                    if (isinstance(_stmt, ast.Expr)
                            and isinstance(_stmt.value, ast.Call)
                            and isinstance(_stmt.value.func, ast.Name)
                            and _stmt.value.func.id == "flush_health"):
                        _stripped += 1
                        continue
                    _keep.append(_stmt)
                _h.body = _keep
check("CONTROL: the plant removed exactly one flush call (a plant that "
      "matched nothing is a working check reported as broken)", _stripped, 1)
check("CONTROL: with it removed the generic handler reports no flush, so the "
      "map above is measuring something",
      _handler_map(_target).get("Exception"), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. ISOLATION")
print("=" * 78)
print()

check("process_patient was restored BY IDENTITY",
      _runner.process_patient is _REAL_PATIENT, True)
check("...and so was save_checkpoint",
      _runner.save_checkpoint is _REAL_SAVE, True)
check("no database outside the scratch directory was named",
      os.path.abspath(_DB).startswith(_TMP)
      and _paths._RESOLVED["inferences_path"].startswith(_TMP), True)

_paths._RESOLVED.clear()
_paths._RESOLVED.update(_PATHS_SAVED)
check("paths._RESOLVED was restored",
      _paths._RESOLVED.get("checkpoint_path"),
      _PATHS_SAVED.get("checkpoint_path"))

check("oncotriage/batch/runner.py is byte-identical afterwards",
      hashlib.sha256(open(_RUNNER_FILE, "rb").read()).hexdigest(),
      _HASH_BEFORE)

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
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 78)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 25 2026

@author: ramyalsaffar
"""
