# Runner Crash Record and Database-Path Unification Test
######################################################

"""What a crashed campaign leaves on the console, and which file a run writes to.

WHAT WAS MISSING
----------------
    1. A CRASHED CAMPAIGN LEFT NO CONSOLE RECORD. Both of ``main()``'s
       ``except BaseException`` handlers finalized the run row and re-raised,
       and neither printed anything. The periodic health flushes cover part of
       the loss -- ``run_metrics`` holds the DEGRADATION totals as of the last
       completed patient -- and they do not cover the CENSUS at all, which is
       excluded from that table by a closed-category ruling. So on a crash the
       census counts survived NOWHERE.

    2. THE RUN ROW AND THE PATIENT ROWS COULD END UP IN DIFFERENT FILES.
       ``main()`` resolved the database ONCE and threaded it to the `runs` row
       and the final flush, while FIVE other sites resolved per call:
       ``log_inference`` once per patient, ``flush_health`` once per completed
       patient in EACH of the two pools, ``reconcile_writes`` at the end, and
       the path ``print_summary`` reports. ``resolve_inference_db_path`` reads
       ONCOTRIAGE_INFERENCES_DB at CALL time, so a change while a run was in
       flight put the `runs` row in one file and the patient rows in another --
       every `run_id` in the second naming a run that is not there, the health
       record split across both, and the reconciliation reporting the other
       file's rows as lost. Nothing raised.

    3. `runs` HAD NO `resumed` COLUMN. The fact existed only as an MLflow tag,
       so the two things a resumed row misstates by construction -- its
       `started_at` is when the LAST process started, and its patient count
       covers only the patients THIS process wrote -- had nothing in the
       database qualifying them.

WHAT THIS FILE HOLDS
--------------------
    1. ``print_crash_record`` DRIVEN DIRECTLY: both blocks, in census-then-
       degradation order, and the contract that it NEVER raises -- including
       when a block raises ``BaseException``, which is the case that would
       otherwise displace the exception the operator actually needs.
    2. AN ``ast`` WALK OVER ``main()``: the call is in BOTH handlers, ABOVE the
       ``raise``, and NOT on the success path -- which is what says a clean run
       does not print twice, structurally, beside the behavioural check.
    3. ``main()`` DRIVEN END TO END, both directions. A planted mid-batch
       failure prints both blocks, leaves a KILLED row, and re-raises the
       ORIGINAL exception; a clean run prints each block EXACTLY ONCE.
    4. A MID-RUN ENVIRONMENT HIJACK, driven through the real ``run_batch`` and
       the real ``_on_done``: every worker is handed the file ``main()``
       resolved, the hijack target is never created, and the check records what
       a per-call resolve WOULD have returned at that moment -- so the arm is
       shown to be effective rather than assumed to be.
    5. ``runs.resumed`` written from the same boolean the MLflow tag reads,
       through a real ``main()``, for a fresh run and a resumed one.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, no spend, no live Qdrant, no model load, no corpus, no git
history, no live server. The BM25 index, the graph, the tracking module and
``process_patient`` are stand-ins; NO BILLED CALL IS POSSIBLE because the graph
is never invoked. Bucket A, ~3 s.

NOT IN THE COLLISION MATRIX, derived: every database and every FHIR file it
opens is inside a ``tempfile.mkdtemp`` it removes and then asserts gone, it
patches no repository file, and the two repository files it READS
(``oncotriage/batch/runner.py``, ``oncotriage/storage/database_logger.py``) are
written by neither of the suite's two writers. Both are sha256-compared at the
end.

IT EXECS NOTHING. Every control is a stand-in installed on the module inside
try/finally with the restore asserted, a real failing condition, or an ``ast``
walk over a parsed source file.

Run from terminal:
    python tests/test_runner_crash_record_and_db_unification.py

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

# The graph is a stand-in and is never invoked, so nothing here can reach a
# model. The flag is the second line of defence: a stand-in forgotten in a
# future edit becomes a named RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile

from oncotriage import degradation as _degradation
from oncotriage import paths as _paths
from oncotriage.batch import runner as _runner
from oncotriage.storage import database_logger as _dl


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
_FAILURES = []
_SKIPS = []


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


def fail(label, detail):
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def skip(label, reason):
    """Coverage that could NOT be exercised here. A SKIP IS NOT A PASS.

    Printed even at zero, for the reason this project records elsewhere: a skip
    count that appears only when non-zero is indistinguishable from a file with
    no skip mechanism at all.
    """
    _RESULTS["skipped"] += 1
    _SKIPS.append(f"{label}\n          {reason}")
    print(f"  SKIP  {label}")
    print(f"          {reason}")


class Raised:
    """What ``guarded`` returns instead of a result. Empty, falsy, NOT a dict.

    The shape matters: a marker that IS a dict passes an ``isinstance(x, dict)``
    check, which is how a sibling file's headline assertion once passed while
    the call under it had raised.
    """

    __slots__ = ("text",)

    def __init__(self, text):
        self.text = text

    def __bool__(self):
        return False

    def __len__(self):
        return 0

    def __contains__(self, item):
        return False

    def __iter__(self):
        return iter(())

    def __repr__(self):
        return f"<raised {self.text}>"


def guarded(fn, *args, **kwargs):
    """Call into production code, turning ANY raise into a value check() fails on.

    Eleven files in this suite have shipped the same defect: a bare call inside
    a ``check(...)`` argument, where a planted defect raises, the exception
    escapes while the argument is evaluated, and the run reports ONE TRACEBACK
    where it owed a summary and N results.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return Raised(f"{type(exc).__name__}: {exc}")


def at(seq, index, default="<absent>"):
    """``seq[index]`` or a named absence, so a shortened result FAILS rather
    than aborting the file."""
    try:
        return seq[index]
    except (IndexError, KeyError, TypeError):
        return default


@contextlib.contextmanager
def quiet():
    """Swallow console output. ``console.out`` writes to STDERR, so both go."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        yield buf


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


# The two block headings, read off the real renderers rather than retyped -- a
# retyped heading is a string that stops matching the day the block is reworded,
# and every count below would then read as "the block was not printed".
_DEG_HEADING = _degradation.report_lines(_degradation.snapshot())[0]
_CENSUS_HEADING = _degradation.census_report_lines(
    _degradation.census_snapshot())[0]

_TMP = tempfile.mkdtemp(prefix="oncotriage_runner_crash_")
_RUNNER_PY = os.path.abspath(_runner.__file__)
_LOGGER_PY = os.path.abspath(_dl.__file__)
_RUNNER_SHA_BEFORE = sha256(_RUNNER_PY)
_LOGGER_SHA_BEFORE = sha256(_LOGGER_PY)


#------------------------------------------------------------------------------


# ===========================================================================
# 1. print_crash_record: BOTH BLOCKS, AND IT NEVER RAISES
# ===========================================================================

print("\n=== 1. the crash-path console record ===")

with quiet() as _buf:
    _r = guarded(_runner.print_crash_record, where="probe")
_text = _buf.getvalue()

check("1a it returns normally", isinstance(_r, Raised), False)
check("1b it prints the census block", _text.count(_CENSUS_HEADING), 1)
check("1c ...and the degradation block", _text.count(_DEG_HEADING), 1)
check("1d ...with the census ABOVE the degradation block -- severity "
      "ascending, the same order print_summary uses",
      _text.index(_CENSUS_HEADING) < _text.index(_DEG_HEADING), True)

# THE CONTRACT: IT NEVER RAISES. It runs with an exception in flight, so
# anything escaping would REPLACE that exception with one about printing --
# destroying the diagnosis and the traceback that names where the run died.
_real_report = _degradation.print_report
_real_census = _degradation.print_census_report
try:
    def _boom(*a, **k):
        raise RuntimeError("planted formatting failure")
    _degradation.print_report = _boom
    with quiet() as _b1:
        _one = guarded(_runner.print_crash_record, where="probe1")
    _t1 = _b1.getvalue()
finally:
    _degradation.print_report = _real_report
check("1e a failing degradation block does not raise",
      isinstance(_one, Raised), False)
check("1f ...and is reported in one line naming the block, because a silent "
      "failure here is indistinguishable from a run with nothing to report",
      "degradation block could not be printed" in _t1, True)
check("1g ...while the CENSUS block still printed -- the two have separate "
      "guards, so one cannot take the other with it",
      _t1.count(_CENSUS_HEADING), 1)

try:
    _degradation.print_census_report = _boom
    with quiet() as _b2:
        _two = guarded(_runner.print_crash_record, where="probe2")
    _t2 = _b2.getvalue()
finally:
    _degradation.print_census_report = _real_census
check("1h a failing census block does not raise either",
      isinstance(_two, Raised), False)
check("1i ...and the DEGRADATION block still printed, which is the more "
      "valuable of the two",
      _t2.count(_DEG_HEADING), 1)

# THE BaseException CASE, which is the one that matters and the one a bare
# `except Exception` would miss. A KeyboardInterrupt arriving during these two
# print calls must not become the exception that propagates.
try:
    def _interrupt(*a, **k):
        raise KeyboardInterrupt("planted Ctrl-C during crash printing")
    _degradation.print_report = _interrupt
    with quiet():
        _three = guarded(_runner.print_crash_record, where="probe3")
finally:
    _degradation.print_report = _real_report
check("1j a KeyboardInterrupt raised INSIDE a block does not escape -- it "
      "would displace the original exception, which is the one thing this "
      "function exists not to do",
      isinstance(_three, Raised), False)

check("1k the two renderers were restored, so no later section is driving a "
      "stand-in",
      (_degradation.print_report is _real_report
       and _degradation.print_census_report is _real_census), True)


#------------------------------------------------------------------------------


# ===========================================================================
# 2. WHERE THE CALL SITS IN main(), BY ast
# ===========================================================================
#
# The behavioural checks below drive both paths and count blocks. This says the
# same thing STRUCTURALLY, and it catches what a count cannot: a second call
# added on the success path would still count 1 per block there (print_summary
# prints them once) while double-printing on some other path.

print("\n=== 2. where the call sits in main() ===")

with open(_RUNNER_PY, encoding="utf-8") as _handle:
    _RUNNER_TREE = ast.parse(_handle.read())

_main = next((n for n in ast.walk(_RUNNER_TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
if _main is None:
    fail("2a runner.main() was located", "no top-level def named main")
else:
    check("2a runner.main() was located", _main.name, "main")

_handlers = [n for n in ast.walk(_main)
             if isinstance(n, ast.ExceptHandler)
             and isinstance(n.type, ast.Name)
             and n.type.id == "BaseException"] if _main else []
check("2b main() has exactly the two BaseException handlers this pass covers "
      "(non-degeneracy: a walk finding none would satisfy every check below)",
      len(_handlers), 2)


def _calls_named(node, name):
    return [c for c in ast.walk(node)
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
            and c.func.id == name]


_missing = [i for i, h in enumerate(_handlers)
            if not _calls_named(h, "print_crash_record")]
check("2c EVERY BaseException handler prints the crash record -- including the "
      "tracking-start one, which does not flush health either and so leaves "
      "the console as its only record", _missing, [])

_wrong_order = []
for _i, _h in enumerate(_handlers):
    _print_at = min((c.lineno for c in _calls_named(_h, "print_crash_record")),
                    default=None)
    _raise_at = min((n.lineno for n in ast.walk(_h)
                     if isinstance(n, ast.Raise)), default=None)
    if _print_at is None or _raise_at is None or _print_at >= _raise_at:
        _wrong_order.append((_i, _print_at, _raise_at))
check("2d ...above the re-raise in each, so the record is emitted before the "
      "exception leaves", _wrong_order, [])

# THE SUCCESS PATH MUST NOT CALL IT. Everything in main() that is NOT inside one
# of the two handlers is the success path, and print_summary is what prints the
# blocks there.
_handler_lines = set()
for _h in _handlers:
    for _n in ast.walk(_h):
        if hasattr(_n, "lineno"):
            _handler_lines.add(_n.lineno)
_outside = [c.lineno for c in _calls_named(_main, "print_crash_record")
            if c.lineno not in _handler_lines] if _main else []
check("2e ...and NOTHING outside a handler calls it, which is what makes a "
      "clean run print once rather than twice", _outside, [])
check("2f ...while print_summary IS called on the success path, and exactly "
      "once (the other half of 'exactly once')",
      len([c for c in _calls_named(_main, "print_summary")
           if c.lineno not in _handler_lines]) if _main else -1, 1)
check("2g ...and print_summary is NOT called from a handler, which would be "
      "the other way to double-print",
      [c.lineno for c in _calls_named(_main, "print_summary")
       if c.lineno in _handler_lines] if _main else ["<no main>"], [])


#------------------------------------------------------------------------------


# ===========================================================================
# main() DRIVEN END TO END -- THE HARNESS
# ===========================================================================
#
# WHAT IS A STAND-IN AND WHY. Four things make main() unrunnable in bucket A and
# each is replaced by the smallest object that satisfies its caller:
#
#   build_bm25_index_from_qdrant  needs a live Qdrant
#   build_matching_graph          compiles LangGraph and pulls in the agent
#   tracking                      would open a real MLflow store
#   process_patient               is ONE LIVE BILLED Stage 5 CALL per patient
#
# THE GRAPH OBJECT IS NEVER INVOKED, so no billed call is reachable even if a
# stand-in were forgotten -- and ONCOTRIAGE_DEFER_LOCAL_MODELS above the imports
# is the second line of defence.
#
# EVERYTHING ELSE IS THE REAL THING: the real run_batch, the real _on_done, the
# real flush_health, the real start_run_record and finalize_run_record, the real
# reconcile_writes, the real print_summary and the real crash handlers. That is
# what makes sections 3 to 6 statements about the shipped code rather than about
# the harness.

def _make_corpus(root, count):
    os.makedirs(root, exist_ok=True)
    for index in range(count):
        with open(os.path.join(root, f"patient{index}.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"resourceType": "Bundle", "entry": []}, handle)
    return root


class _TrackingStandIn:
    """Records what main() asked of the tracking layer; opens nothing."""

    def __init__(self):
        self.calls = []

    def start_run(self, **kwargs):
        self.calls.append(("start_run", kwargs))

    def log_run_metrics(self, *args, **kwargs):
        self.calls.append(("log_run_metrics", None))

    def end_run(self, **kwargs):
        self.calls.append(("end_run", kwargs.get("status")))


_PATCHED = ("build_bm25_index_from_qdrant", "build_matching_graph", "tracking",
            "load_results", "clear_checkpoint", "load_checkpoint",
            "process_patient", "run_batch", "run_resample")


def drive_main(db_path, corpus, *, checkpoint_dir, process_patient=None,
               run_batch=None, load_checkpoint=None):
    """Run the REAL main() against a scratch tree. Returns (console text, exc).

    The restore is in a ``finally`` and is asserted by the caller: a stand-in
    left installed would make every later section a statement about the
    stand-in.
    """
    saved = {name: getattr(_runner, name) for name in _PATCHED}
    saved_resolved = dict(_paths._RESOLVED)
    tracking = _TrackingStandIn()
    buf = io.StringIO()
    try:
        _paths._RESOLVED["data_fhir_path"] = corpus + os.sep
        _paths._RESOLVED["inferences_path"] = db_path
        _paths._RESOLVED["checkpoint_path"] = checkpoint_dir + os.sep
        _runner.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
        _runner.build_matching_graph = lambda *a, **k: object()
        _runner.load_results = lambda *a, **k: []
        _runner.clear_checkpoint = lambda *a, **k: None
        _runner.load_checkpoint = load_checkpoint or (lambda *a, **k: set())
        _runner.tracking = tracking
        _runner.run_resample = lambda **k: None
        if run_batch is not None:
            _runner.run_batch = run_batch
        if process_patient is not None:
            _runner.process_patient = process_patient
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            try:
                _runner.main()
                return buf.getvalue(), None, tracking
            except BaseException as exc:               # noqa: BLE001 -- returned
                return buf.getvalue(), exc, tracking
    finally:
        for name, value in saved.items():
            setattr(_runner, name, value)
        _paths._RESOLVED.clear()
        _paths._RESOLVED.update(saved_resolved)
        _dl._INITIALIZED_DATABASES.discard(os.path.abspath(db_path))


def erroring_patient(fhir_path=None, graph=None, is_resample=False,
                     run_id=None, db_path=None):
    """A patient that fails. status='error' ON PURPOSE.

    A 'success' entry makes ``_on_done`` call ``save_checkpoint()``, which with
    no fingerprint argument resolves ``run_fingerprint.current()`` -- a LIVE
    QDRANT round trip, in a file that is bucket A. The same reasoning is written
    out at the sibling stand-in in tests/test_storage_run_metrics_flush.py.
    """
    return {"patient_id": os.path.basename(str(fhir_path)), "status": "error",
            "eligible_matches": 0, "near_misses": 0, "not_evaluable": 0,
            "total_time": 0.01, "timestamp": "2026-08-23T00:00:00",
            "error": "planted by the harness", "is_resample": is_resample}


#------------------------------------------------------------------------------


# ===========================================================================
# 3. A RUN THAT CRASHES MID-BATCH PRINTS BOTH BLOCKS AND RE-RAISES
# ===========================================================================

print("\n=== 3. a crashed run ===")

_CRASH_DB = os.path.join(_TMP, "crash.db")
_CRASH_CP = os.path.join(_TMP, "crash_cp")
os.makedirs(_CRASH_CP, exist_ok=True)


class _PlantedFailure(RuntimeError):
    """Distinct from anything the runner raises, so 3c cannot pass by accident."""


def _crashing_batch(**kwargs):
    raise _PlantedFailure("planted mid-batch failure")


_crash_text, _crash_exc, _crash_tracking = drive_main(
    _CRASH_DB, _make_corpus(os.path.join(_TMP, "crash_fhir"), 3),
    checkpoint_dir=_CRASH_CP, run_batch=_crashing_batch,
    process_patient=erroring_patient)

check("3a the run crashed", _crash_exc is not None, True)
check("3b ...and the ORIGINAL exception propagated, not one about printing -- "
      "which is what the crash printer's whole contract is for",
      type(_crash_exc).__name__, "_PlantedFailure")
check("3c ...unchanged, message included",
      str(_crash_exc), "planted mid-batch failure")
check("3d the census block was printed", _crash_text.count(_CENSUS_HEADING), 1)
check("3e ...and the degradation block", _crash_text.count(_DEG_HEADING), 1)
check("3f ...and neither more than once", 
      (_crash_text.count(_CENSUS_HEADING), _crash_text.count(_DEG_HEADING)),
      (1, 1))
check("3g ...and no formatting-failure note was emitted, so both were rendered "
      "rather than swallowed",
      "could not be printed" in _crash_text, False)

_crash_conn = sqlite3.connect(_CRASH_DB)
check("3h the run row was finalized KILLED, not FAILED -- a process that did "
      "not get to the end is a different finding from one that finished badly",
      _crash_conn.execute("SELECT status FROM runs").fetchall(), [("KILLED",)])
check("3i ...and the health record was persisted before the print, so a "
      "formatting failure could not have cost it",
      _crash_conn.execute(
          "SELECT COUNT(*) FROM run_metrics").fetchone()[0] > 0, True)
_crash_conn.close()
check("3j ...and the tracking run was closed FAILED, MLflow's vocabulary "
      "having no KILLED -- the divergence is stated, not smoothed over",
      [s for c, s in _crash_tracking.calls if c == "end_run"], ["FAILED"])


#------------------------------------------------------------------------------


# ===========================================================================
# 4. A CLEAN RUN PRINTS EACH BLOCK EXACTLY ONCE
# ===========================================================================

print("\n=== 4. a clean run ===")

_CLEAN_DB = os.path.join(_TMP, "clean.db")
_CLEAN_CP = os.path.join(_TMP, "clean_cp")
os.makedirs(_CLEAN_CP, exist_ok=True)

_clean_text, _clean_exc, _clean_tracking = drive_main(
    _CLEAN_DB, _make_corpus(os.path.join(_TMP, "clean_fhir"), 3),
    checkpoint_dir=_CLEAN_CP, process_patient=erroring_patient)

check("4a the run completed", _clean_exc, None)
check("4b the census block was printed EXACTLY once -- the crash printer must "
      "not fire on a path print_summary already covered",
      _clean_text.count(_CENSUS_HEADING), 1)
check("4c ...and the degradation block exactly once",
      _clean_text.count(_DEG_HEADING), 1)
check("4d ...and the run row is terminal and not KILLED",
      sqlite3.connect(_CLEAN_DB).execute(
          "SELECT status FROM runs").fetchall(), [("FAILED",)])
check("4e ...FAILED rather than FINISHED because every patient errored, which "
      "is the main pass's verdict and not the crash handler's",
      [s for c, s in _clean_tracking.calls if c == "end_run"], ["FAILED"])
check("4f ...and the crashed run's console really did carry the same blocks, "
      "so 4b/4c measure 'once' rather than 'never on either path'",
      (_crash_text.count(_CENSUS_HEADING), _crash_text.count(_DEG_HEADING)),
      (1, 1))
check("4g the module attributes were restored after both drives",
      all(getattr(_runner, n) is not None for n in _PATCHED)
      and _runner.process_patient.__name__ == "process_patient", True)


#------------------------------------------------------------------------------


# ===========================================================================
# 5. A MID-RUN ENVIRONMENT CHANGE CANNOT SPLIT THE WRITE FAMILIES
# ===========================================================================
#
# DRIVEN THROUGH THE REAL run_batch AND THE REAL _on_done. Only
# ``process_patient`` is a stand-in, and it is the stand-in that performs the
# HIJACK: it sets ONCOTRIAGE_INFERENCES_DB to a second file on its first call,
# from a WORKER thread, while the pool is running -- which is exactly when a
# per-call resolution would pick it up.
#
# THE ARM IS SHOWN TO BE EFFECTIVE. Each call records what
# ``resolve_inference_db_path(None)`` returns AT THAT MOMENT, which is the file
# the pre-unification code WOULD have written to. Without that, an assertion
# that everything landed in one file would pass just as well against a hijack
# that never took.

print("\n=== 5. a mid-run environment hijack ===")

_HOME_DB = os.path.join(_TMP, "home.db")
_HIJACK_DB = os.path.join(_TMP, "hijack.db")
_SPLIT_CP = os.path.join(_TMP, "split_cp")
os.makedirs(_SPLIT_CP, exist_ok=True)
_HANDED = []


def _hijacking_patient(fhir_path=None, graph=None, is_resample=False,
                       run_id=None, db_path=None):
    os.environ["ONCOTRIAGE_INFERENCES_DB"] = _HIJACK_DB
    _HANDED.append({
        "stem": os.path.basename(str(fhir_path)),
        "db_path": db_path,
        "would_resolve_to": _dl.resolve_inference_db_path(None),
        "run_id": run_id,
    })
    return erroring_patient(fhir_path=fhir_path, graph=graph,
                            is_resample=is_resample, run_id=run_id,
                            db_path=db_path)


_saved_env = os.environ.get("ONCOTRIAGE_INFERENCES_DB")
try:
    os.environ.pop("ONCOTRIAGE_INFERENCES_DB", None)
    _split_text, _split_exc, _split_tracking = drive_main(
        _HOME_DB, _make_corpus(os.path.join(_TMP, "split_fhir"), 4),
        checkpoint_dir=_SPLIT_CP, process_patient=_hijacking_patient)
finally:
    if _saved_env is None:
        os.environ.pop("ONCOTRIAGE_INFERENCES_DB", None)
    else:
        os.environ["ONCOTRIAGE_INFERENCES_DB"] = _saved_env

check("5a the run completed", _split_exc, None)
check("5b every pending patient was reached (non-degenerate: an empty pass "
      "would satisfy every assertion below)", len(_HANDED), 4)
check("5c THE HIJACK WAS EFFECTIVE -- at the moment each worker ran, a per-call "
      "resolve returned the OTHER file. This is the arm; without it the checks "
      "below would pass against a hijack that never took",
      sorted({h["would_resolve_to"] for h in _HANDED}), [_HIJACK_DB])
check("5d ...and every worker was nonetheless handed the file main() resolved",
      sorted({h["db_path"] for h in _HANDED}), [_HOME_DB])
check("5e ...so the two really differ, which is what makes 5d a finding",
      _HOME_DB != _HIJACK_DB, True)
check("5f every worker carried the same run id, one campaign",
      len({h["run_id"] for h in _HANDED}), 1)

check("5g THE HIJACK TARGET WAS NEVER CREATED -- nothing wrote there, and a "
      "plain sqlite3.connect on a missing path CREATES the file, so its "
      "absence is proof rather than inference",
      os.path.exists(_HIJACK_DB), False)

_home_conn = sqlite3.connect(_HOME_DB)
_home_tables = {r[0] for r in _home_conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table'")}
check("5h the run row is in main()'s file",
      _home_conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], 1)
check("5i ...and the health record is in the SAME file, which is the family "
      "that used to flush once per completed patient with no db_path at all",
      _home_conn.execute(
          "SELECT COUNT(*) FROM run_metrics").fetchone()[0] > 0, True)
check("5j ...and every run_metrics row names the run row that is there, so "
      "the two halves of the health record cannot be describing two files",
      _home_conn.execute(
          "SELECT COUNT(*) FROM run_metrics m "
          "LEFT JOIN runs r ON r.id = m.run_id WHERE r.id IS NULL"
      ).fetchone()[0], 0)
_home_conn.close()

# THE STRUCTURAL HALF. Every call site that used to resolve per call is now
# handed a path. An ast walk is what says a SIXTH site has not been added.
_resolvers = []
for _node in ast.walk(_RUNNER_TREE):
    if not (isinstance(_node, ast.Call) and isinstance(_node.func, ast.Name)):
        continue
    if _node.func.id not in ("log_inference", "flush_health", "reconcile_writes",
                             "print_summary", "start_run_record",
                             "finalize_run_record"):
        continue
    if not any(k.arg == "db_path" for k in _node.keywords):
        _resolvers.append((_node.func.id, _node.lineno))
check("5k every database-writing or database-reading call in the runner is "
      "handed an explicit db_path -- so a sixth per-call resolution cannot be "
      "added without failing here", _resolvers, [])
check("5l ...over a non-degenerate number of call sites (a walk matching "
      "nothing would report [] too)",
      len([c for c in ast.walk(_RUNNER_TREE)
           if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
           and c.func.id in ("log_inference", "flush_health", "reconcile_writes",
                             "print_summary", "start_run_record",
                             "finalize_run_record")]) >= 6, True)
check("5m ...and main() resolves the path exactly once",
      len([c for c in ast.walk(_main)
           if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
           and c.func.id == "resolve_inference_db_path"]) if _main else -1, 1)


#------------------------------------------------------------------------------


# ===========================================================================
# 6. runs.resumed, FROM THE SAME FACT THE MLflow TAG READS
# ===========================================================================

print("\n=== 6. the resumed column ===")

_FRESH_DB = os.path.join(_TMP, "fresh_run.db")
_FRESH_CP = os.path.join(_TMP, "fresh_cp")
os.makedirs(_FRESH_CP, exist_ok=True)
_fresh_text, _fresh_exc, _fresh_tracking = drive_main(
    _FRESH_DB, _make_corpus(os.path.join(_TMP, "fresh_fhir"), 2),
    checkpoint_dir=_FRESH_CP, process_patient=erroring_patient)

_RESUME_DB = os.path.join(_TMP, "resumed_run.db")
_RESUME_CP = os.path.join(_TMP, "resume_cp")
os.makedirs(_RESUME_CP, exist_ok=True)
_resume_corpus = _make_corpus(os.path.join(_TMP, "resume_fhir"), 3)
_resume_text, _resume_exc, _resume_tracking = drive_main(
    _RESUME_DB, _resume_corpus, checkpoint_dir=_RESUME_CP,
    process_patient=erroring_patient,
    load_checkpoint=lambda *a, **k: {"patient0"})


def _tag(tracking):
    for name, kwargs in tracking.calls:
        if name == "start_run":
            return (kwargs.get("tags") or {}).get("resumed")
    return "<no start_run>"


def _column(db):
    """`runs.resumed` for every row, or a named absence. NEVER RAISES.

    The column not existing is the exact state these checks catch, and a bare
    SELECT for it raises OperationalError -- aborting the file at the moment it
    owes a summary. Proved by a revert harness, not reasoned about.
    """
    try:
        return sqlite3.connect(db).execute("SELECT resumed FROM runs").fetchall()
    except BaseException as exc:                       # noqa: BLE001 -- reported
        return f"<raised {type(exc).__name__}: {exc}>"


check("6a both runs completed", (_fresh_exc, _resume_exc), (None, None))
check("6b a run whose checkpoint was empty records resumed=0 -- a MEASURED "
      "'not a resume', which is a different fact from the NULL a row written "
      "before the column carries", _column(_FRESH_DB), [(0,)])
check("6c ...and a run whose checkpoint named a completed patient records 1",
      _column(_RESUME_DB), [(1,)])
check("6d the MLflow tag says the same thing on the fresh run",
      _tag(_fresh_tracking), "false")
check("6e ...and on the resumed one", _tag(_resume_tracking), "true")
check("6f ...so the column and the tag AGREE, which is the property one "
      "boolean handed to both is for",
      [(_column(_FRESH_DB)[0][0], _tag(_fresh_tracking)),
       (_column(_RESUME_DB)[0][0], _tag(_resume_tracking))],
      [(0, "false"), (1, "true")])
check("6g ...and the two runs really differed, so 6f is not one value compared "
      "with itself", _column(_FRESH_DB) != _column(_RESUME_DB), True)

# THE ONE BOOLEAN, BY ast. Both records must read the SAME name -- a second
# read of `completed_ids` would agree today (both sit above run_batch, which
# mutates it) and would diverge the day either moved below it.
_resumed_names = set()
for _node in ast.walk(_main) if _main else []:
    if isinstance(_node, ast.Call) and isinstance(_node.func, ast.Name) \
            and _node.func.id == "start_run_record":
        for _kw in _node.keywords:
            if _kw.arg == "resumed" and isinstance(_kw.value, ast.Name):
                _resumed_names.add(_kw.value.id)
check("6h start_run_record is handed a NAME, not an inline expression -- which "
      "is what lets the tag read the same one", sorted(_resumed_names),
      ["_resumed"])
_tag_reads = [n.id for n in ast.walk(_main)
              if isinstance(n, ast.Name) and n.id == "_resumed"] if _main else []
check("6i ...and that name is read more than once in main(), so the tag is "
      "reading it too rather than re-deriving the fact",
      len(_tag_reads) >= 3, True)
# THE TAG'S OWN EXPRESSION, WALKED. The first version of this check tried to
# match `completed_ids` Names against the LINE of a `tags=` keyword, which is
# both fragile and unreadable -- and which nothing could have shown to fail.
# This locates the tags expression itself and asks what names are inside it.
_tags_expr = None
for _node in ast.walk(_main) if _main else []:
    if not (isinstance(_node, ast.Call)
            and isinstance(_node.func, ast.Attribute)
            and _node.func.attr == "start_run"):
        continue
    for _kw in _node.keywords:
        if _kw.arg == "tags":
            _tags_expr = _kw.value
check("6j the tracking tag expression was located (non-degeneracy: with None "
      "here the two checks below compare empty lists)",
      _tags_expr is not None, True)
_tag_names = sorted({n.id for n in ast.walk(_tags_expr)
                     if isinstance(n, ast.Name)}) if _tags_expr else []
check("6k the tag reads the shared boolean", "_resumed" in _tag_names, True)
check("6l ...and does NOT re-read `completed_ids`, which is the second "
      "derivation of one fact this removes -- it would agree today, because "
      "both reads sit above the run_batch that mutates it, and would diverge "
      "the day either moved below it",
      "completed_ids" in _tag_names, False)


#------------------------------------------------------------------------------


# ===========================================================================
# 7. THIS FILE WROTE NOTHING IN THE REPOSITORY
# ===========================================================================

print("\n=== 7. isolation ===")

check("7a oncotriage/batch/runner.py is byte-identical",
      sha256(_RUNNER_PY), _RUNNER_SHA_BEFORE)
check("7b oncotriage/storage/database_logger.py is byte-identical",
      sha256(_LOGGER_PY), _LOGGER_SHA_BEFORE)
check("7c ONCOTRIAGE_INFERENCES_DB is not left set by the hijack section",
      os.environ.get("ONCOTRIAGE_INFERENCES_DB"), _saved_env)
check_true("7d every database this file opened is inside its own temp directory",
           all(os.path.abspath(p).startswith(os.path.abspath(_TMP))
               for p in (_CRASH_DB, _CLEAN_DB, _HOME_DB, _FRESH_DB, _RESUME_DB)))

for _path in (_CRASH_DB, _CLEAN_DB, _HOME_DB, _FRESH_DB, _RESUME_DB):
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(_path))
shutil.rmtree(_TMP, ignore_errors=True)
check("7e the scratch directory was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
print(f"  skipped: {_RESULTS['skipped']}   (a skip is NOT a pass and is not "
      f"counted as one)")
if _SKIPS:
    print()
    print("SKIPPED:")
    for _s in _SKIPS:
        print(f"  - {_s}")
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
Created on Sat Aug 23 2026

@author: ramyalsaffar
"""
