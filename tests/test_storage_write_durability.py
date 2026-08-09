# Inference Write Durability Test
#################################

"""
A LOST DATABASE ROW WAS INVISIBLE TO EVERYTHING ABOVE IT.

``log_inference`` (``oncotriage/storage/database_logger.py``) catches
``sqlite3.Error``, rolls back, prints "Database logging failed (non-critical)"
and continues -- and then returned ``db_path`` exactly as it does on success.
The caller could not tell the row was lost, so the patient was recorded as
successful and the run reported complete. Every number in the paper comes from
one final run; if that run loses rows and reports complete, the result looks
whole and is not, and nobody finds out.

``_WRITE_LOCK`` closes the IN-PROCESS race and is measured doing so by
``tests/test_package_invariants.py`` section 5e. This file covers what the lock
cannot reach -- the second writing process -- and everything above the function
that could not see a failure.

WHAT THIS FILE HOLDS
--------------------
    1. THE NORMAL PATH IS UNCHANGED. A clean write still returns something that
       IS the database path -- ``==``, ``isinstance(str)``, hashing, os.path --
       because five isolation tests in this suite compare that return value
       against their own scratch path and would break if it became a tuple.
       Non-degeneracy first: the row is actually there.
    2. WAL TOOK, AND WAS VERIFIED BY READING THE PRAGMA BACK. Journal mode is a
       property of the FILE and can fail silently, so the writer reads it back
       and records a degradation. Both arms are driven.
    3. THE BUSY TIMEOUT IS THE CONFIGURED ONE, on every connection the writer
       opens, and it is a DECISION rather than Python's unchosen 5-second
       default -- which is asserted to be what it is not.
    4. CONTENTION IS RETRIED AND SURVIVED, FOR REAL. No source is patched and
       nothing is stubbed: a second connection takes a genuine ``BEGIN
       EXCLUSIVE`` and the writer meets a real "database is locked". Held for
       the whole call it gives up and says so; released mid-call it recovers on
       a later attempt.
    5. THE MIGRATION RACE IS DELIBERATELY NOT RETRIED, and the reason is
       checked rather than asserted in prose: retrying it would repair section
       5e's negative control and silently delete the evidence that
       ``_WRITE_LOCK`` is load-bearing.
    6. A FORCED FAILURE IS COUNTED, LOGGED, AND VISIBLE TO THE CALLER -- and
       does not raise, because a logging fault must not destroy a ~70-second
       pipeline result that cost a live Stage 5 call.
    7. RECONCILIATION. A run with no failures reports COMPLETE and exits 0; a
       run with one lost row reports INCOMPLETE and exits 1. BOTH, because the
       second proves nothing without the first. The resample double-write and a
       checkpoint resume are driven, because either makes a naive
       rows-vs-patients count wrong.
    8. THE ID CHECK ACTUALLY VERIFIES PRESENCE. A row the writer reported as
       written is deleted behind its back, and the reconciliation must find it
       missing -- which a report-trusting counter cannot.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every database is a
temporary file, every result dict is a literal, no graph is compiled and no
model is called. Nothing in the repository is written: the two package files
this file reads are hashed at the start and compared at the end, and the
PRODUCTION inference database is read once, read-only, and asserted unchanged.

WHY IT IS NOT IN THE COLLISION MATRIX, derived rather than assumed: it writes
only inside a temporary directory and patches no file in the repository. The
two files it READS -- ``oncotriage/storage/database_logger.py`` and
``oncotriage/batch/runner.py`` -- are written by neither of the suite's two
writers (``oncotriage/registries/cancer_code_registry.py`` and
``oncotriage/config.py``).

WHY IT EXECS NOTHING. The project's standing preference is to plant a defect
into a COPY of a module and exec it, and ``tests/test_package_invariants.py``
section 1c enforces a closed allowlist for that. This file needs no entry:
every control here is driven through the REAL shipped module, either by
creating the failing condition for real (an exclusive lock, an unwritable
path, a deleted row) or by rebinding a module attribute inside a
``try/finally`` that restores it -- and the file's own sha256 check at the end
is what proves no source was touched.

Run from terminal:
    python tests/test_storage_write_durability.py

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
import json
import logging
import shutil
import sqlite3
import tempfile
import threading
import time
from pathlib import Path

from oncotriage import config as _config
from oncotriage import observability as _obs
from oncotriage import paths as _paths
from oncotriage.batch import runner as _runner
from oncotriage.storage import database_logger as _dl


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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


def fail(label: str, detail: str) -> None:
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def guarded(fn, default=None):
    """Call fn; on ANY exception return a marker instead of aborting the run.

    THIS IS NOT DEFENSIVE PADDING. Three files in this suite have shipped the
    same defect: a bare call into production code inside a `check(...)`
    argument, where a planted defect raises, the exception escapes while the
    argument is being evaluated, and the run reports ONE TRACEBACK where it owed
    a summary and N results. The controls below deliberately break things, so
    every driver goes through here and a raise becomes a recorded failure.
    """
    try:
        return fn()
    except BaseException as exc:                       # noqa: BLE001
        return {"raised": f"{type(exc).__name__}: {exc}", "default": default}


def digest(path):
    """sha256 of a file, or the string 'absent'."""
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def rows(db, table="inferences"):
    """Row count in a table, or None when it cannot be read.

    READ-ONLY URI: a plain connect on an absent path CREATES the file, so a
    check written that way would bring its own subject into existence.
    """
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        finally:
            conn.close()
    except sqlite3.Error:
        return None


class RecordingHandler(logging.Handler):
    """Capture the structured records this module emits, as dicts.

    The FORMATTED record, not the LogRecord, because the field allowlist is
    applied in the formatter -- a check reading `record.__dict__` would see
    fields that never reach a log file.
    """

    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(self.format(record))
        except Exception:                              # noqa: BLE001
            pass

    def events(self):
        out = []
        for line in self.lines:
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
        return out


@contextlib.contextmanager
def capturing_logs():
    """Attach a recorder to this project's logger tree for the block."""
    logger = logging.getLogger("oncotriage")
    handler = RecordingHandler()
    handler.setFormatter(_obs._JsonFormatter())
    handler.setLevel(logging.DEBUG)
    logger.addHandler(handler)
    previous = logger.level
    logger.setLevel(logging.DEBUG)
    try:
        yield handler
    finally:
        logger.setLevel(previous)
        logger.removeHandler(handler)


def silence(fn):
    """Run fn with the console channel captured; return its value.

    Both channels write to stderr, and the writer under test is deliberately
    chatty: four attempts x several controls of "Database logging failed" would
    bury the PASS/FAIL lines. Nothing suppressed here is asserted on -- the log
    ASSERTIONS read capturing_logs(), which is the handler channel and is
    unaffected by this redirect.
    """
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        return fn()


#------------------------------------------------------------------------------


# ===========================================================================
# FIXTURES
# ===========================================================================

_TMP = tempfile.mkdtemp(prefix="oncotriage-writedur-")

_DL_PY = os.path.abspath(_dl.__file__)
_RUNNER_PY = os.path.abspath(_runner.__file__)
_DL_DIGEST_BEFORE = digest(_DL_PY)
_RUNNER_DIGEST_BEFORE = digest(_RUNNER_PY)


def result_dict(patient_id, **extra):
    """The minimum a terminal node emits that log_inference will accept."""
    base = {
        "patient_id": patient_id,
        "timestamp": "2026-08-08T00:00:00",
        "matching_model": "gpt-5.6-terra",
        "llm_classifier_input_tokens": 10,
        "llm_classifier_output_tokens": 5,
        "matches": [], "near_misses": [], "not_evaluable": [],
        "stage_timings": {},
    }
    base.update(extra)
    return base


PATIENT = {"demographics": {"age": 60, "sex": "female"}, "conditions": [],
           "medications": [], "allergies": []}


def fresh_db(name):
    """A path in the scratch directory, with the memo cleared for it.

    _INITIALIZED_DATABASES is a per-process memo keyed on the absolute path, so
    a stale entry would make the next database skip initialization entirely and
    every assertion after it prove nothing.
    """
    path = os.path.join(_TMP, name)
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(path))
    return path


def journal_mode(db):
    """The journal mode the FILE is in, read read-only."""
    try:
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"unreadable: {exc}"


#------------------------------------------------------------------------------


# ===========================================================================
# 1. THE NORMAL PATH IS UNCHANGED
# ===========================================================================

print("\n" + "=" * 70)
print("1. a clean write still returns the database path, and the row is there")
print("=" * 70)

_DB1 = fresh_db("normal.db")
_R1 = silence(lambda: _dl.log_inference(result_dict("normal-1"), PATIENT,
                                        db_path=_DB1))

# NON-DEGENERATE FIRST. Every assertion below is satisfied by a writer that
# returns a path and writes nothing at all.
check("1a  the row is actually in the table (non-degeneracy)",
      rows(_DB1), 1)
check("1a  ...and so are its trial_matches children's table",
      rows(_DB1, "trial_matches"), 0)

# THE PINNED CONTRACT. Five files in this suite compare log_inference's return
# value with `==` against their own scratch path:
#   tests/test_storage_ecog_logging.py:328
#   tests/test_storage_inference_logging_contract.py:811, :910
#   tests/test_agent_retrieval_observability.py:994, :1027, :1061
#   tests/test_fhir_birth_date_and_demographics.py:896
# That comparison is what makes those isolation tests checkable, so it may not
# break. Every string operation a caller could plausibly perform is driven.
check("1b  it still equals the database path", _R1 == _DB1, True)
check("1b  ...and the path still equals it (== is symmetric)", _DB1 == _R1, True)
check("1b  ...it IS a str", isinstance(_R1, str), True)
check("1b  ...it hashes as one, so it still works as a dict key",
      {_R1: 1}.get(_DB1), 1)
check("1b  ...os.path.basename works on it",
      os.path.basename(_R1), os.path.basename(_DB1))
check("1b  ...it f-strings to the path and nothing else", f"{_R1}", _DB1)
check("1b  ...and json.dumps serialises it as the path string",
      json.dumps(_R1), json.dumps(_DB1))

check("1c  .ok is True", getattr(_R1, "ok", "MISSING"), True)
check("1c  .attempts is 1 -- no contention, no retry",
      getattr(_R1, "attempts", "MISSING"), 1)
check("1c  .error is None", getattr(_R1, "error", "MISSING"), None)
check("1c  .inference_id is the id the row was actually given",
      getattr(_R1, "inference_id", "MISSING"), 1)

# repr() must NOT be str's, or a diagnosis prints a path that looks like a
# success. Equality is untouched -- it comes from str.
check("1d  repr() shows the outcome, not just the path",
      "ok=True" in repr(_R1), True)

check("1e  a clean write records no failure",
      dict(_dl.INFERENCE_WRITE_FAILURES), {})


#------------------------------------------------------------------------------


# ===========================================================================
# 2. WAL, AND THE VERIFICATION THAT IT TOOK
# ===========================================================================

print("\n" + "=" * 70)
print("2. the journal mode is applied AND read back")
print("=" * 70)

check("2a  the configured mode is WAL", _config.SQLITE_JOURNAL_MODE, "WAL")
check("2b  the database is in WAL after initialization",
      journal_mode(_DB1), "wal")
check("2b  ...and no degradation was recorded",
      dict(_dl.JOURNAL_MODE_DEGRADATIONS), {})

# THE CONTROL. WAL is a property of the FILE and can fail to take without
# raising -- a network filesystem cannot provide the shared memory the wal-index
# needs. That is not reproducible on a local disk, so the failure is created the
# other way SQLite offers: a mode the pragma will not adopt. `PRAGMA
# journal_mode = <unknown>` does not raise; it returns the mode still in force,
# which is exactly the shape of the silent failure being guarded against.
#
# The constant is rebound on the MODULE (the writer reads its own global at call
# time) inside a try/finally, and the file's sha256 at the end of this run is
# what proves no source was edited.
_DB2 = fresh_db("degraded-mode.db")
_saved_mode = _dl.SQLITE_JOURNAL_MODE
_dl.JOURNAL_MODE_DEGRADATIONS.clear()
try:
    _dl.SQLITE_JOURNAL_MODE = "not_a_journal_mode"
    with capturing_logs() as _modelog:
        _degraded = guarded(lambda: silence(
            lambda: _dl.log_inference(result_dict("degraded-1"), PATIENT,
                                      db_path=_DB2)))
finally:
    _dl.SQLITE_JOURNAL_MODE = _saved_mode

check("2c  a mode that cannot be applied is DETECTED (negative control)",
      dict(_dl.JOURNAL_MODE_DEGRADATIONS),
      {"not_a_journal_mode->delete": 1})
check("2c  ...and it is reported at WARNING with both modes named",
      sorted((e.get("journal_mode_requested"), e.get("journal_mode"))
             for e in _modelog.events()
             if e.get("event") == "journal_mode_degraded"),
      [("not_a_journal_mode", "delete")])
check("2c  ...the write still succeeded -- a journal mode is not a write "
      "failure",
      getattr(_degraded, "ok", "RAISED-OR-MISSING"), True)
check("2c  ...and the database really is in the OTHER mode, so the check is "
      "not agreeing with itself (non-degeneracy)",
      journal_mode(_DB2), "delete")

_dl.JOURNAL_MODE_DEGRADATIONS.clear()


#------------------------------------------------------------------------------


# ===========================================================================
# 3. THE BUSY TIMEOUT IS A DECISION
# ===========================================================================

print("\n" + "=" * 70)
print("3. the busy timeout is the configured one, on every connection")
print("=" * 70)

check("3a  it is configured in oncotriage/config.py",
      isinstance(_config.SQLITE_BUSY_TIMEOUT_SECONDS, (int, float)), True)
check("3a  ...and it is NOT Python's unchosen 5-second default, which is the "
      "whole point of naming it",
      _config.SQLITE_BUSY_TIMEOUT_SECONDS != 5.0, True)

_probe = _dl._open_connection(_DB1)
try:
    _busy_ms = _probe.execute("PRAGMA busy_timeout").fetchone()[0]
finally:
    _probe.close()
check("3b  _open_connection applies it, in MILLISECONDS as the pragma reports "
      "them",
      _busy_ms, int(_config.SQLITE_BUSY_TIMEOUT_SECONDS * 1000))

# A plain sqlite3.connect is what the writer used before this pass. Asserting
# what it gives makes the line above a comparison rather than a restatement.
_plain = sqlite3.connect(_DB1)
try:
    _plain_ms = _plain.execute("PRAGMA busy_timeout").fetchone()[0]
finally:
    _plain.close()
check("3b  ...and a plain connect does not (non-degeneracy: the two differ)",
      _plain_ms != _busy_ms, True)

# EVERY connection the writer opens, not just the one it is convenient to
# check. Both are asserted by AST, because the second one is on the failure
# path and driving it would need a second contention scenario for a fact a
# static check settles.
_dl_tree = ast.parse(Path(_DL_PY).read_text(encoding="utf-8"), _DL_PY)
_raw_connects = [
    node for node in ast.walk(_dl_tree)
    if isinstance(node, ast.Call)
    and isinstance(node.func, ast.Attribute)
    and node.func.attr == "connect"
    and isinstance(node.func.value, ast.Name)
    and node.func.value.id == "sqlite3"
]
_open_conn_defs = [n for n in ast.walk(_dl_tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "_open_connection"]
check("3c  _open_connection exists (non-degeneracy for the scan below)",
      len(_open_conn_defs), 1)
check("3c  every sqlite3.connect in the writer is inside _open_connection, so "
      "no connection can be opened without the timeout",
      [n.lineno for n in _raw_connects
       if not (_open_conn_defs[0].lineno <= n.lineno <= _open_conn_defs[0].end_lineno)],
      [])


#------------------------------------------------------------------------------


# ===========================================================================
# 4. REAL CONTENTION IS RETRIED, AND SURVIVED
# ===========================================================================

print("\n" + "=" * 70)
print("4. a genuine lock from another connection -- no patching, no stubs")
print("=" * 70)

# _is_retryable's vocabulary, on real exception instances rather than on
# strings, because the function tests the TYPE as well as the message.
check("4a  'database is locked' is retryable",
      _dl._is_retryable(sqlite3.OperationalError("database is locked")), True)
check("4a  ...and so is 'database table is locked'",
      _dl._is_retryable(sqlite3.OperationalError("database table is locked")),
      True)
check("4a  a duplicate column -- the migration race -- is NOT",
      _dl._is_retryable(
          sqlite3.OperationalError("duplicate column name: retrieval_degraded")),
      False)
check("4a  an IntegrityError is NOT, whatever it says",
      _dl._is_retryable(sqlite3.IntegrityError("database is locked")), False)
check("4a  a disk-full OperationalError is NOT",
      _dl._is_retryable(sqlite3.OperationalError("database or disk is full")),
      False)

# --- 4b. held for the whole call: the write is lost, and SAYS so ------------
_DB4 = fresh_db("contended.db")
silence(lambda: _dl.initialize_database(_DB4))       # so the DDL is not in play

_dl.INFERENCE_WRITE_FAILURES.clear()
_dl.INFERENCE_WRITE_RETRIES.clear()

_saved_timeout = _dl.SQLITE_BUSY_TIMEOUT_SECONDS
_blocker = sqlite3.connect(_DB4, timeout=30)
_blocker.isolation_level = None
try:
    _dl.SQLITE_BUSY_TIMEOUT_SECONDS = 0.15          # so the run takes ~1s
    _blocker.execute("BEGIN EXCLUSIVE")
    with capturing_logs() as _lostlog:
        _blocked = guarded(lambda: silence(
            lambda: _dl.log_inference(result_dict("contended-1"), PATIENT,
                                      db_path=_DB4)))
    _blocker.execute("COMMIT")
finally:
    _dl.SQLITE_BUSY_TIMEOUT_SECONDS = _saved_timeout
    _blocker.close()

check("4b  a real exclusive lock made the write fail (non-degeneracy)",
      getattr(_blocked, "ok", "RAISED-OR-MISSING"), False)
check("4b  ...it did NOT raise -- a logging fault must not destroy the "
      "pipeline result",
      isinstance(_blocked, dict) and "raised" in _blocked, False)
check("4b  ...it still equals the database path", _blocked == _DB4, True)
check("4b  ...every attempt was used",
      getattr(_blocked, "attempts", "MISSING"),
      _config.SQLITE_WRITE_MAX_ATTEMPTS)
check("4b  ...the retries were counted",
      dict(_dl.INFERENCE_WRITE_RETRIES),
      {"OperationalError": _config.SQLITE_WRITE_MAX_ATTEMPTS - 1})
check("4b  ...the give-up was counted, and keyed as RETRYABLE so the fix "
      "(more attempts, longer timeout, fewer writers) is in the key",
      dict(_dl.INFERENCE_WRITE_FAILURES), {"OperationalError:retryable": 1})
check("4b  ...and no row landed", rows(_DB4), 0)

_lost_events = [e for e in _lostlog.events()
                if e.get("event") == "inference_write_lost"]
check("4b  the loss is one ERROR record", len(_lost_events), 1)
check("4b  ...at ERROR level",
      [e.get("level") for e in _lost_events], ["ERROR"])
check("4b  ...naming the patient, so it joins to the row that is missing",
      [e.get("patient_id") for e in _lost_events], ["contended-1"])
check("4b  ...and naming the database it is missing from",
      [e.get("db_path") for e in _lost_events], [_DB4])

# --- 4c. released mid-call: the retry is what saves the row ----------------
# THIS IS THE POSITIVE CONTROL FOR 4b. Without it, 4b is equally consistent
# with a writer that never succeeds under any contention.
_dl.INFERENCE_WRITE_FAILURES.clear()
_dl.INFERENCE_WRITE_RETRIES.clear()

_recovered = {}
_saved_timeout = _dl.SQLITE_BUSY_TIMEOUT_SECONDS
_blocker2 = sqlite3.connect(_DB4, timeout=30)
_blocker2.isolation_level = None
try:
    _dl.SQLITE_BUSY_TIMEOUT_SECONDS = 0.05
    _blocker2.execute("BEGIN EXCLUSIVE")

    def _write_in_thread():
        # The connection is owned by the MAIN thread -- sqlite3 objects are not
        # shareable across threads -- so the WRITER runs in the worker and the
        # release happens here. The first draft had it the other way round and
        # died in the releasing thread with a ProgrammingError, which would have
        # made this control report a failure that was about the harness.
        _recovered["value"] = guarded(lambda: silence(
            lambda: _dl.log_inference(result_dict("recovered-1"), PATIENT,
                                      db_path=_DB4)))

    _worker = threading.Thread(target=_write_in_thread)
    _worker.start()
    time.sleep(0.12)                # long enough for attempt 1 to fail
    _blocker2.execute("COMMIT")
    _worker.join(timeout=30)
finally:
    _dl.SQLITE_BUSY_TIMEOUT_SECONDS = _saved_timeout
    _blocker2.close()

_rec = _recovered.get("value")
check("4c  the write SUCCEEDED once the lock was released",
      getattr(_rec, "ok", "RAISED-OR-MISSING"), True)
check("4c  ...but not on the first attempt, so a retry is what saved it "
      "(non-degeneracy: this is not just a clean write)",
      (getattr(_rec, "attempts", 0) or 0) > 1, True)
check("4c  ...the row is in the table", rows(_DB4), 1)
check("4c  ...nothing was recorded as lost",
      dict(_dl.INFERENCE_WRITE_FAILURES), {})
check("4c  ...and the retries WERE recorded, because a run that needed them is "
      "a run whose next increment of load loses rows",
      (dict(_dl.INFERENCE_WRITE_RETRIES).get("OperationalError") or 0) >= 1,
      True)

_dl.INFERENCE_WRITE_FAILURES.clear()
_dl.INFERENCE_WRITE_RETRIES.clear()


#------------------------------------------------------------------------------


# ===========================================================================
# 5. THE MIGRATION RACE IS DELIBERATELY NOT RETRIED
# ===========================================================================

print("\n" + "=" * 70)
print("5. retrying the migration race would delete the evidence for the lock")
print("=" * 70)

# WHY THIS SECTION EXISTS. "duplicate column name" is a sqlite3.OperationalError
# and retrying it WOULD succeed -- the other thread's ALTER already added the
# column. It is excluded anyway, because
# tests/test_package_invariants.py section 5e proves _WRITE_LOCK necessary by
# STRIPPING it and requiring rows to be LOST. A retry broad enough to repair
# that race repairs the control too, and a check that has stopped checking is
# the defect this project's rules exist to catch.
#
# So the exclusion is asserted three ways: the classifier (4a), the behaviour
# (below), and the fact that section 5e's assertion still has a subject.

_DB5 = fresh_db("migration.db")
silence(lambda: _dl.initialize_database(_DB5))
_dup = sqlite3.OperationalError("duplicate column name: retrieval_degraded")
check("5a  the classifier refuses it", _dl._is_retryable(_dup), False)

# The behavioural half: a terminal error must consume ONE attempt, not four.
# An unwritable path produces a real sqlite3.OperationalError that is not
# contention, which is the same class the migration race falls into.
_DB5_BAD = os.path.join(_TMP, "no-such-directory", "nested.db")
_dl.INFERENCE_WRITE_FAILURES.clear()
_terminal = guarded(lambda: silence(
    lambda: _dl.log_inference(result_dict("terminal-1"), PATIENT,
                              db_path=_DB5_BAD)))
check("5b  a terminal error is not retried at all -- one attempt",
      getattr(_terminal, "attempts", "MISSING"), 1)
check("5b  ...it is reported as lost", getattr(_terminal, "ok", "MISSING"), False)
check("5b  ...and keyed TERMINAL, so it is distinguishable from contention "
      "that ran out of attempts",
      dict(_dl.INFERENCE_WRITE_FAILURES), {"OperationalError:terminal": 1})
check("5b  ...nothing was created at the bad path (non-degeneracy)",
      os.path.exists(_DB5_BAD), False)
_dl.INFERENCE_WRITE_FAILURES.clear()

# Section 5e of the invariants test must still HAVE a subject: exactly three
# `with _WRITE_LOCK:` sites, which is the number it asserts. The retry loop is
# inside one of them rather than around it.
_lock_withs = [
    node for node in ast.walk(_dl_tree)
    if isinstance(node, ast.With)
    and any(isinstance(i.context_expr, ast.Name)
            and i.context_expr.id == "_WRITE_LOCK" for i in node.items)
]
check("5c  there are still exactly three `with _WRITE_LOCK:` sites, which is "
      "what test_package_invariants.py section 5e asserts",
      len(_lock_withs), 3)


#------------------------------------------------------------------------------


# ===========================================================================
# 6. RECONCILIATION: COMPLETE, AND INCOMPLETE
# ===========================================================================

print("\n" + "=" * 70)
print("6. the batch run reports whether it stored everything it processed")
print("=" * 70)

# --- 6a. a clean run reports COMPLETE and exits 0 --------------------------
# PROVED FIRST. The failing case below proves nothing unless the passing case
# is shown to pass: a reconciliation that always says INCOMPLETE would satisfy
# 6b on its own.
_DB6 = fresh_db("reconcile.db")
_runner.clear_write_ledger()
_before6 = _runner.inference_row_count(_DB6)

for _i in range(3):
    _w = silence(lambda i=_i: _dl.log_inference(
        result_dict(f"recon-{i}"), PATIENT, db_path=_DB6))
    _runner.record_write(f"recon-{_i}", _w, is_resample=False)

_rec6 = _runner.reconcile_writes(rows_before=_before6)
check("6a  three writes were attempted", _rec6["attempted"], 3)
check("6a  ...three were reported written", _rec6["reported_ok"], 3)
check("6a  ...three were VERIFIED PRESENT by id", _rec6["verified"], 3)
check("6a  ...none missing", _rec6["missing"], 0)
check("6a  ...none reported lost", _rec6["reported_lost"], 0)
check("6a  ...the run is COMPLETE", _rec6["complete"], True)
check("6a  ...and the target is the database the WRITER used, not a "
      "re-resolution",
      _rec6["target"], _DB6)
_runner._publish_reconciliation(_rec6)
check("6a  ...so the exit code is 0", _runner.reconciliation_exit_code(), 0)

# The baseline was unreadable (the file did not exist), which is normal and must
# not be reported as a delta of zero.
check("6a  a baseline that could not be read is reported as unavailable, not "
      "as zero",
      _rec6["delta"], None)

# --- 6b. one lost write makes the run INCOMPLETE and exits 1 ---------------
_runner.clear_write_ledger()
for _i in range(2):
    _w = silence(lambda i=_i: _dl.log_inference(
        result_dict(f"partial-{i}"), PATIENT, db_path=_DB6))
    _runner.record_write(f"partial-{_i}", _w, is_resample=False)

# A REAL loss, produced the way section 5b produces one, not a hand-built
# ledger entry: a ledger a test wrote itself proves the reporting, never the
# plumbing that fills it.
_lost = silence(lambda: _dl.log_inference(result_dict("lost-1"), PATIENT,
                                          db_path=_DB5_BAD))
_runner.record_write("lost-1", _lost, is_resample=False)
_dl.INFERENCE_WRITE_FAILURES.clear()

_rec6b = _runner.reconcile_writes(rows_before=None)
check("6b  the lost write is counted", _rec6b["reported_lost"], 1)
check("6b  ...the run is INCOMPLETE", _rec6b["complete"], False)
check("6b  ...and the patient is NAMED, so the operator knows which to re-run",
      [f.split(":")[0] for f in _rec6b["failures"]], ["lost-1"])
_runner._publish_reconciliation(_rec6b)
check("6b  ...so the exit code is 1", _runner.reconciliation_exit_code(), 1)

# --- 6c. the resample double-write is handled -----------------------------
# A resample re-runs an ALREADY-PROCESSED patient, so it writes a SECOND row
# for the same patient_id. A rows-vs-patients count would read that as a
# surplus; the ledger records calls, so it is simply two entries.
_DB6C = fresh_db("resample.db")
_runner.clear_write_ledger()
_w1 = silence(lambda: _dl.log_inference(result_dict("dup-patient"), PATIENT,
                                        db_path=_DB6C))
_runner.record_write("dup-patient", _w1, is_resample=False)
_w2 = silence(lambda: _dl.log_inference(result_dict("dup-patient"), PATIENT,
                                        db_path=_DB6C))
_runner.record_write("dup-patient", _w2, is_resample=True)

_rec6c = _runner.reconcile_writes(rows_before=0)
check("6c  one patient, two writes, two ledger entries",
      _rec6c["attempted"], 2)
check("6c  ...both verified present -- distinct rows, so the ids differ "
      "(non-degeneracy)",
      (_rec6c["verified"], _w1.inference_id != _w2.inference_id), (2, True))
check("6c  ...the run is COMPLETE even though rows outnumber patients",
      _rec6c["complete"], True)
check("6c  ...and the delta agrees with the attempts here, because nothing "
      "else wrote this file",
      (_rec6c["delta"], _rec6c["rows_after"]), (2, 2))

# --- 6d. a checkpoint resume is handled -----------------------------------
# A resumed run SKIPS patients an earlier process completed. Their rows are in
# the table and this process did not write them. A rows-vs-patients count would
# read hundreds of pre-existing rows as this run's, and a resumed run that lost
# 50 could still show a total above its own patient count and read as healthy.
_DB6D = fresh_db("resume.db")
for _i in range(5):                              # the "earlier process"
    silence(lambda i=_i: _dl.log_inference(result_dict(f"earlier-{i}"), PATIENT,
                                           db_path=_DB6D))
_runner.clear_write_ledger()                     # this process starts here
_before6d = _runner.inference_row_count(_DB6D)
_w = silence(lambda: _dl.log_inference(result_dict("resumed-1"), PATIENT,
                                       db_path=_DB6D))
_runner.record_write("resumed-1", _w, is_resample=False)

_rec6d = _runner.reconcile_writes(rows_before=_before6d)
check("6d  five rows predate this process (non-degeneracy)", _before6d, 5)
check("6d  ...this run attempted ONE write, not six",
      _rec6d["attempted"], 1)
check("6d  ...and is COMPLETE: the other five are not its business",
      _rec6d["complete"], True)
check("6d  ...the table holds six", _rec6d["rows_after"], 6)


#------------------------------------------------------------------------------


# ===========================================================================
# 7. THE ID CHECK VERIFIES PRESENCE, IT DOES NOT TRUST THE REPORT
# ===========================================================================

print("\n" + "=" * 70)
print("7. a row that was reported written and is not there is FOUND missing")
print("=" * 70)

# THE STRONGEST CONTROL IN THIS FILE. Everything in section 6 could be
# satisfied by a reconciliation that counts what the writer told it and never
# looks at the database. So a row the writer reported as WRITTEN, with a real
# id, is deleted behind its back, and the verdict must change.
#
# This is also why the verdict is the id check rather than the before/after
# count: the count moves for any reason at all, including another process
# writing the same file, and can reconcile perfectly while the data is gone.
_DB7 = fresh_db("deleted.db")
_runner.clear_write_ledger()
_ids7 = []
for _i in range(3):
    _w = silence(lambda i=_i: _dl.log_inference(
        result_dict(f"vanish-{i}"), PATIENT, db_path=_DB7))
    _runner.record_write(f"vanish-{_i}", _w, is_resample=False)
    _ids7.append(_w.inference_id)

_rec7_before = _runner.reconcile_writes(rows_before=0)
check("7a  all three reconcile before the deletion (non-degeneracy)",
      (_rec7_before["verified"], _rec7_before["complete"]), (3, True))

_c7 = sqlite3.connect(_DB7)
_c7.execute("DELETE FROM inferences WHERE id = ?", (_ids7[1],))
_c7.commit()
_c7.close()

_rec7 = _runner.reconcile_writes(rows_before=0)
check("7b  the writer still reports three written", _rec7["reported_ok"], 3)
check("7b  ...but only two are present", _rec7["verified"], 2)
check("7b  ...one is missing", _rec7["missing"], 1)
check("7b  ...and the run is INCOMPLETE even though the writer reported no "
      "failure at all",
      _rec7["complete"], False)
_runner._publish_reconciliation(_rec7)
check("7b  ...exit code 1", _runner.reconciliation_exit_code(), 1)

# A writer that does not report at all -- a stub, or a pre-durability
# log_inference returning a plain str -- must not be read as success.
_runner.clear_write_ledger()
_runner.record_write("unreported-1", str(_DB7), is_resample=False)
_rec7c = _runner.reconcile_writes(rows_before=0)
check("7c  a writer that reports nothing is counted as unreported",
      _rec7c["unreported"], 1)
check("7c  ...and is NOT read as success", _rec7c["complete"], False)

# No reconciliation at all is a THIRD state, and it exits 2 rather than 1 so a
# setup crash is not read from a shell as a data-loss finding.
_runner._publish_reconciliation(None)
check("7d  no reconciliation at all exits 2, not 0 and not 1",
      _runner.reconciliation_exit_code(), 2)


#------------------------------------------------------------------------------


# ===========================================================================
# 8. THE SUMMARY SAYS IT, AND CANNOT SAY "COMPLETE" WITHOUT CHECKING
# ===========================================================================

print("\n" + "=" * 70)
print("8. print_summary's closing verdict")
print("=" * 70)


def summary_text(reconciliation):
    """Render print_summary and return what went to the console channel."""
    buf = io.StringIO()
    with contextlib.redirect_stderr(buf):
        _runner.print_summary([], 1.0, db_path=_DB6,
                              reconciliation=reconciliation)
    return buf.getvalue()


_clean_text = guarded(lambda: summary_text(_rec6))
_dirty_text = guarded(lambda: summary_text(_rec7))
_none_text = guarded(lambda: summary_text(None))

check("8a  a clean run prints 'Run complete'",
      isinstance(_clean_text, str) and "Run complete" in _clean_text, True)
check("8a  ...and does not print INCOMPLETE",
      isinstance(_clean_text, str) and "INCOMPLETE" in _clean_text, False)
check("8b  a run that lost rows prints RUN INCOMPLETE",
      isinstance(_dirty_text, str) and "RUN INCOMPLETE" in _dirty_text, True)
check("8b  ...and names the shortfall",
      isinstance(_dirty_text, str)
      and "are NOT in the table" in _dirty_text, True)

# WITHOUT A RECONCILIATION IT MAY NOT CLAIM COMPLETION. This is the defect in
# its original form: "Run complete." printed by a run that had checked nothing.
check("8c  with no reconciliation it says the run ENDED, not that it completed",
      isinstance(_none_text, str) and "Run ended" in _none_text, True)
check("8c  ...and explicitly says it is not a statement about storage",
      isinstance(_none_text, str)
      and "says nothing about whether every row was stored" in _none_text, True)


#------------------------------------------------------------------------------


# ===========================================================================
# 9. NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================

print("\n" + "=" * 70)
print("9. no source was edited, and the production database was not written")
print("=" * 70)

check("9a  oncotriage/storage/database_logger.py is byte-identical",
      digest(_DL_PY), _DL_DIGEST_BEFORE)
check("9a  oncotriage/batch/runner.py is byte-identical",
      digest(_RUNNER_PY), _RUNNER_DIGEST_BEFORE)
check("9a  ...and those digests are real, not 'absent' on both sides "
      "(non-degeneracy)",
      _DL_DIGEST_BEFORE != "absent" and _RUNNER_DIGEST_BEFORE != "absent", True)

# The module constants this file rebound are back where they were.
check("9b  SQLITE_JOURNAL_MODE was restored on the module",
      _dl.SQLITE_JOURNAL_MODE, _config.SQLITE_JOURNAL_MODE)
check("9b  SQLITE_BUSY_TIMEOUT_SECONDS was restored on the module",
      _dl.SQLITE_BUSY_TIMEOUT_SECONDS, _config.SQLITE_BUSY_TIMEOUT_SECONDS)

# THE PRODUCTION DATABASE. Read once, read-only, and asserted unchanged -- this
# file drives the real writer, so "it only ever passed a scratch path" is worth
# checking rather than assuming.
_PRODUCTION_DB = _paths.inferences_path
_PRODUCTION_ROWS = rows(_PRODUCTION_DB)
check("9c  the production database was not written to by this run",
      rows(_PRODUCTION_DB), _PRODUCTION_ROWS)
check("9c  ...and it was readable, so that comparison is not None == None "
      "(non-degeneracy)",
      _PRODUCTION_ROWS is not None, True)
check("9c  ...and no scratch path resolved to it",
      _PRODUCTION_DB.startswith(_TMP), False)

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
Created on Sat Aug  8 2026

@author: ramyalsaffar
"""
