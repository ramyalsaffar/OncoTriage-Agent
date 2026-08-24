# Batch Runner SIGTERM Shutdown Test
###################################

"""What `docker stop` does to a batch run, driven with a real signal.

WHAT WAS MISSING
----------------
    1. SIGTERM HAD NO DISPOSITION AT ALL. Python's default is SIG_DFL, so the
       FIRST signal every orchestrator sends -- `docker stop`, `kubectl delete
       pod`, systemd's stop, a bare `kill <pid>` -- ran NOTHING in this project:
       no handler, no exception, no `finally`, no `except BaseException`. The
       process died at exit -15 leaving no crash record on the console, no final
       health flush, a `runs` row at RUNNING with a NULL `finished_at`, and every
       in-flight Stage 5 request abandoned mid-read while still billed and
       recorded by nothing. SIGINT had a path; SIGTERM had none.

    2. THE POOLS DRAINED THE WHOLE CORPUS ON ANY INTERRUPT, AND THE
       CANCELLATION THAT WAS SUPPOSED TO STOP THAT WAS DEAD CODE.
       ``run_batch`` and ``run_resample`` ran their thread pool as
       ``with ThreadPoolExecutor(...) as executor:``. ``__exit__`` calls
       ``shutdown(wait=True)`` -- WITHOUT ``cancel_futures`` -- and it runs
       BEFORE any ``except`` clause, so by the time the
       ``except KeyboardInterrupt`` handler reached its own
       ``shutdown(wait=True, cancel_futures=True)`` there was nothing left to
       cancel: every future is submitted up front, so the entire remaining
       corpus had already run at one live billed Stage 5 call each. Measured
       below, both ways.

WHAT THIS FILE HOLDS
--------------------
    1. THE STRUCTURE, by ``ast``: no ``with ThreadPoolExecutor`` survives in
       ``oncotriage/batch/runner.py``; both pool sites shut down with
       ``cancel_futures=True`` in a ``finally``; the SIGTERM handler lives
       INSIDE ``25- Batch Runner.py``'s ``__main__`` guard, raises
       ``SystemExit`` with 128 + SIGTERM, and NO handler is installed for
       SIGINT. The last one is the load-bearing negative: the whole argument
       for using SystemExit rather than KeyboardInterrupt is that the SIGINT
       path is not touched, and a scan that only looked for the SIGTERM call
       could not see it being touched.
    2. A REAL SIGTERM AGAINST A REAL SUBPROCESS running the REAL entry point.
       Exit 143, the ``[SIGTERM]`` line, BOTH crash blocks, a ``runs`` row
       finalized KILLED with a non-NULL ``finished_at``, ``run_metrics`` rows
       written by the crash-path flush, and queued patients CANCELLED rather
       than drained.
    3. A REAL SIGINT, in the same harness. THIS SECTION USED TO PIN THE
       OPPOSITE OF WHAT IT PINS NOW, and what it pinned was a defect: both
       pool handlers CAUGHT the KeyboardInterrupt, printed "Checkpoint saved.
       Safe to resume." and RETURNED NORMALLY, so main() carried on into the
       RESAMPLE pass at one live billed Stage 5 call per patient and finalized
       the run FINISHED. Ctrl-C now stops the run: the handlers re-raise, the
       run row is KILLED, both crash blocks print, the resample pass does not
       run, and the entry point exits 130 with no traceback.
    4. THE CONTROL FOR THE DRAIN FIX: the same scenario against a COPY of the
       package whose ``run_batch`` has the ``with`` form restored. It drains
       every queued patient; the shipped form does not. Without it, "queued
       patients were cancelled" would be a claim about a number nobody
       compared.
    4b. THE CONTROL FOR THE RE-RAISE: a second copy with the `raise` deleted
       from ``run_batch``'s KeyboardInterrupt handler, driven with the same
       SIGINT. It records the interrupted run as ended normally and exits
       through the reconciliation verdict; the shipped tree records KILLED and
       exits 130.

THE THIRD WAY TO STOP A RUN -- the operator STOP sentinel, which records the run
STOPPED rather than KILLED -- is ``tests/test_runner_stop_switch.py``'s subject,
not this file's. This file's SIGINT section names it only where an operator
meeting an interrupt message needs to learn it exists.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, NO SPEND, no live Qdrant, no model load (the subprocesses
set ONCOTRIAGE_DEFER_LOCAL_MODELS and the graph is never invoked), no corpus --
every FHIR file is a two-key literal written into a temp directory -- no git
history, no live server. ``process_patient`` is a stand-in that sleeps; the BM25
index, the graph and the tracking module are stand-ins. EVERYTHING ELSE IS THE
REAL THING: the real ``main()``, the real ``run_batch``, the real ``_on_done``,
the real ``flush_health``, the real ``start_run_record`` /
``finalize_run_record``, the real crash handlers, and the real ``__main__``
guard of ``25- Batch Runner.py``, reached through ``runpy`` so the handler under
test is the shipped one. NO BILLED CALL IS REACHABLE: the graph object is never
invoked.

It DOES use subprocesses and signals, which is the point -- a signal cannot be
delivered to the process that is asserting about it, and an in-process
``raise SystemExit`` would test this file rather than the shipped handler.

NOT IN THE COLLISION MATRIX, derived: every database, checkpoint, FHIR file and
package copy it writes is inside a ``tempfile.mkdtemp`` it removes and then
asserts gone; it patches no repository file; and the two repository files it
READS -- ``oncotriage/batch/runner.py`` and ``25- Batch Runner.py`` -- are
written by neither of the suite's two writers and are sha256-compared at the
end.

IT EXECS NOTHING. The one control is a COPY of the package written into that
temp directory and imported from there by a subprocess whose PYTHONPATH points
at it, with a realpath preflight asserting the copy is what imported.

Run from terminal:
    python tests/test_runner_sigterm_shutdown.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries. The candidate directory is
# the PARENT of this file's, because the package sits beside tests/.
import os
import sys

# ABOVE THE PACKAGE IMPORTS, on oncotriage/fixtures/replay.py's precedent:
# oncotriage/agent/deps.py reads this once, at ITS OWN import, and `deps`
# arrives transitively on the first `oncotriage` import. An assignment
# underneath would reach nothing. It is belt-and-braces here -- this file
# imports the runner only to locate and parse it, and the subprocesses set it
# in their own environment -- but a forgotten stand-in must become a named
# RuntimeError rather than a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

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
import hashlib
import json
import re
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import time

import oncotriage
from oncotriage.batch import runner as _runner
from oncotriage.config import MAX_WORKERS


#------------------------------------------------------------------------------


_T_START = time.time()

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_RUNNER_PATH = os.path.abspath(_runner.__file__)
_ENTRY_PATH = os.path.join(_REPO, "25- Batch Runner.py")


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


def fail(label, message):
    """Record a failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {message}")
    print(f"  FAIL  {label}")
    print(f"          {message}")


def at(sequence, index, default="<absent>"):
    """Index without raising.

    A bare ``sequence[index]`` inside a ``check()`` argument list raises while
    the argument is being EVALUATED, so the file dies with a traceback in
    exactly the state it owes recorded failures -- which is the abort shape this
    project has shipped ten times. Every indexed read below goes through here.
    """
    try:
        return sequence[index]
    except (IndexError, KeyError, TypeError):
        return default


print("=" * 78)
print("BATCH RUNNER SIGTERM SHUTDOWN")
print("=" * 78)
print(f"Runner: {_RUNNER_PATH}")
print(f"Entry:  {_ENTRY_PATH}")
print(f"MAX_WORKERS: {MAX_WORKERS}")

_SHA_RUNNER_BEFORE = hashlib.sha256(
    open(_RUNNER_PATH, "rb").read()).hexdigest()
_SHA_ENTRY_BEFORE = hashlib.sha256(
    open(_ENTRY_PATH, "rb").read()).hexdigest()

_TMP = tempfile.mkdtemp(prefix="oncotriage-sigterm-")
print(f"Scratch: {_TMP}")


#------------------------------------------------------------------------------


# ===========================================================================
# 1. THE STRUCTURE
# ===========================================================================
#
# A BEHAVIOURAL CHECK CANNOT SEE SOME OF THIS. Section 3 shows SIGINT still
# absorbed, which is equally true of a tree where somebody ALSO installed a
# SIGINT handler that happens to raise KeyboardInterrupt -- so "no SIGINT
# handler is installed" is pinned structurally. And the executor lifecycle is
# pinned in both places because the resample pool has no scenario of its own
# here (reaching it needs a completed main pass).

print("\n=== 1. the structure ===")

_RUNNER_SRC = open(_RUNNER_PATH, encoding="utf-8").read()
_ENTRY_SRC = open(_ENTRY_PATH, encoding="utf-8").read()
_RUNNER_TREE = ast.parse(_RUNNER_SRC)
_ENTRY_TREE = ast.parse(_ENTRY_SRC)


def function_named(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                and node.name == name:
            return node
    return None


def _pool_functions():
    return {name: function_named(_RUNNER_TREE, name)
            for name in ("run_batch", "run_resample")}


_POOLS = _pool_functions()
check("1a  both pool functions were located (non-degeneracy for every check "
      "below: a walk that found neither would satisfy them all for free)",
      sorted(k for k, v in _POOLS.items() if v is not None),
      ["run_batch", "run_resample"])

# --- 1b: no `with ThreadPoolExecutor(...)` survives -------------------------
_with_pools = []
for _name, _fn in _POOLS.items():
    if _fn is None:
        continue
    for _node in ast.walk(_fn):
        if not isinstance(_node, (ast.With, ast.AsyncWith)):
            continue
        for _item in _node.items:
            _expr = _item.context_expr
            if isinstance(_expr, ast.Call) and (
                    getattr(_expr.func, "id", None) == "ThreadPoolExecutor"
                    or getattr(_expr.func, "attr", None) == "ThreadPoolExecutor"):
                _with_pools.append(_name)
check("1b  no pool is run as a context manager (its __exit__ drains every "
      "queued future BEFORE any except clause, which is the whole defect)",
      sorted(set(_with_pools)), [])

# --- 1c: every pool site shuts down with cancel_futures, in a finally -------
def _cancelling_shutdowns(fn):
    """`executor.shutdown(..., cancel_futures=True)` calls inside `fn`."""
    out = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "shutdown"):
            continue
        if any(kw.arg == "cancel_futures"
               and isinstance(kw.value, ast.Constant) and kw.value.value is True
               for kw in node.keywords):
            out.append(node)
    return out


def _finally_cancels(fn):
    """True when a `finally` block of `fn` cancels queued futures."""
    for node in ast.walk(fn):
        if isinstance(node, ast.Try) and node.finalbody:
            for stmt in node.finalbody:
                if any(_cancelling_shutdowns_in(stmt)):
                    return True
    return False


def _cancelling_shutdowns_in(stmt):
    for node in ast.walk(stmt):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "shutdown"
                and any(kw.arg == "cancel_futures"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True for kw in node.keywords)):
            yield node


check("1c  every pool cancels queued futures from a `finally`, so the "
      "cancellation is reached on EVERY exit path and not only on Ctrl-C",
      {name: _finally_cancels(fn) for name, fn in _POOLS.items() if fn},
      {"run_batch": True, "run_resample": True})
check("1c  ...and each pool has more than one cancelling shutdown (the "
      "handler's and the finally's), so the finally is an addition rather "
      "than a move",
      sorted({name: len(_cancelling_shutdowns(fn))
              for name, fn in _POOLS.items() if fn}.values()), [2, 2])

# --- 1d: the handler is in the guard, raises SystemExit(128 + SIGTERM) ------
_GUARD = [n for n in _ENTRY_TREE.body
          if isinstance(n, ast.If)
          and isinstance(n.test, ast.Compare)
          and getattr(n.test.left, "id", None) == "__name__"]
check("1d  the entry point's `__main__` guard was located (non-degeneracy)",
      len(_GUARD), 1)

_signal_calls = []
for _node in ast.walk(_ENTRY_TREE):
    if (isinstance(_node, ast.Call)
            and isinstance(_node.func, ast.Attribute)
            and _node.func.attr == "signal"
            and getattr(_node.func.value, "id", None) == "signal"):
        _signal_calls.append(_node)

# TWO CALLS, NOT ONE, AND THE SECOND IS THE POINT: the install, and the reset to
# SIG_DFL that the handler performs on entry (1e). The count is pinned so a third
# appearing anywhere is a named failure, and the SIGNALS TOUCHED are pinned
# separately -- which is the property that actually matters.
check("1d  exactly two signal.signal calls exist in the entry point: the "
      "install and the handler's own reset to the default",
      len(_signal_calls), 2)
check("1d  ...and every one of them targets SIGTERM",
      sorted({getattr(at(c.args, 0), "attr", None) for c in _signal_calls}),
      ["SIGTERM"])
# THE LOAD-BEARING NEGATIVE. The entire argument for SystemExit over
# KeyboardInterrupt is that the SIGINT path is UNTOUCHED. A tree that installed
# a SIGINT handler would still pass Section 3 (whose stand-in raises
# KeyboardInterrupt from the pool either way), so it is pinned here.
check("1d  ...and NO disposition is installed for SIGINT: Ctrl-C keeps "
      "CPython's default, which is what makes 'SIGINT is untouched' a fact "
      "rather than a claim",
      sorted({getattr(c.args[0], "attr", None) for c in _signal_calls
              if c.args}), ["SIGTERM"])

_guard_signal_calls = [n for g in _GUARD for n in ast.walk(g)
                       if n in _signal_calls]
check("1d  ...and the installation is INSIDE the guard, so importing or "
       "embedding this file cannot rebind a caller's SIGTERM",
      len(_guard_signal_calls), len(_signal_calls))

_sysexit_raises = [n for g in _GUARD for n in ast.walk(g)
                   if isinstance(n, ast.Raise)
                   and isinstance(n.exc, ast.Call)
                   and getattr(n.exc.func, "id", None) == "SystemExit"]
check("1d  the handler raises SystemExit -- not KeyboardInterrupt, which the "
      "pool handlers SWALLOW",
      len(_sysexit_raises) >= 1, True)
check("1d  ...and no KeyboardInterrupt is raised anywhere in the entry point",
      [n for n in ast.walk(_ENTRY_TREE) if isinstance(n, ast.Raise)
       and (getattr(getattr(n.exc, "func", None), "id", None)
            == "KeyboardInterrupt"
            or getattr(n.exc, "id", None) == "KeyboardInterrupt")], [])

# TWO DERIVED EXIT CODES NOW, NOT ONE, AND THE SECOND IS SIGINT's. The pool
# handlers used to SWALLOW KeyboardInterrupt, so Ctrl-C could not produce an
# exit code of its own at all; they re-raise now, and this guard turns the
# resulting interrupt into 128 + SIGINT with no traceback -- the SIGINT half of
# what the SIGTERM handler already did.
#
# THE SIGNAL EACH ONE IS COMPUTED FROM IS PINNED, not just the count. A pair of
# `128 + signal.SIGTERM` expressions would satisfy a bare count of two while
# leaving Ctrl-C reporting a SIGTERM code, which is the confusion the two
# codes exist to prevent.
_exit_consts = [n for g in _GUARD for n in ast.walk(g)
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.Add)
                and isinstance(n.left, ast.Constant) and n.left.value == 128]
check("1d  both exit codes are derived as 128 + <signal> rather than typed as "
      "literal 143/130 (the shell convention, computed from the signal)",
      len(_exit_consts), 2)
# THE SIGNAL IS READ OUT OF THE RIGHT OPERAND BY WALKING IT, not by reading an
# attribute off it. Both sites are written `128 + int(signal.SIGx)`, so the
# right operand is a Call and `n.right.attr` is absent -- the first version of
# this check asked for that attribute, got two ast dumps, and failed against a
# tree that is correct. A walk finds the signal name in either form.
def _signal_names_in(node):
    return sorted({sub.attr for sub in ast.walk(node)
                   if isinstance(sub, ast.Attribute)
                   and getattr(sub.value, "id", None) == "signal"})


check("1d  ...and they are computed from SIGTERM and SIGINT respectively, so "
      "neither reports the other's code",
      sorted(name for n in _exit_consts for name in _signal_names_in(n.right)),
      ["SIGINT", "SIGTERM"])
check("1d  ...(non-degeneracy: the extractor really does find a signal name "
      "in each operand rather than returning nothing twice)",
      [len(_signal_names_in(n.right)) for n in _exit_consts], [1, 1])

# THE SIGINT DISPOSITION IS A `try/except KeyboardInterrupt` AND NOT A HANDLER,
# and that distinction is what 1d's "NO disposition is installed for SIGINT"
# above still guarantees. Installing one would change WHERE the interrupt lands
# (inside a handler, on the main thread, possibly mid-lock); catching it after
# main() returns changes only what is printed and what is exited with, and it
# runs after main()'s own crash handler has already written the record.
_sigint_handlers = [n for g in _GUARD for n in ast.walk(g)
                    if isinstance(n, ast.ExceptHandler)
                    and getattr(n.type, "id", None) == "KeyboardInterrupt"]
check("1d  the entry point catches KeyboardInterrupt exactly once, in the "
      "guard, so a re-raised Ctrl-C does not reach CPython's default handler "
      "and print a traceback for a shutdown the operator asked for",
      len(_sigint_handlers), 1)

# --- 1e: the handler is re-entrancy-safe ------------------------------------
#
# TWO PROPERTIES A BEHAVIOURAL CHECK ALONE CANNOT PIN. The deadlock one is the
# sharper: `print` and `console.out` both take a Python-level lock, and a signal
# handler that re-enters a lock the main thread already holds -- which is where
# a SIGTERM landing mid-progress-bar puts it -- hangs the process on the path
# whose whole job is to work when things are going wrong. It cannot be tested by
# running, because it depends on WHERE the signal lands; so it is pinned by
# construction instead.
_handler_fn = None
for _g in _GUARD:
    _handler_fn = _handler_fn or function_named(_g, "_terminate_on_sigterm")
check("1e  the handler function was located (non-degeneracy)",
      _handler_fn is not None, True)

if _handler_fn is not None:
    _first = at(_handler_fn.body, 1 if isinstance(at(_handler_fn.body, 0),
                                                  ast.Expr) else 0)
    check("1e  its FIRST statement after the docstring restores the default "
          "disposition, so a second SIGTERM terminates instead of raising "
          "SystemExit through a crash handler that catches only Exception",
          (isinstance(_first, ast.Expr)
           and isinstance(_first.value, ast.Call)
           and getattr(_first.value.func, "attr", None) == "signal"
           and [getattr(a, "attr", None) for a in _first.value.args]),
          ["SIGTERM", "SIG_DFL"])
    _writes = [n for n in ast.walk(_handler_fn)
               if isinstance(n, ast.Call)
               and getattr(n.func, "attr", None) == "write"
               and getattr(n.func.value, "id", None) == "os"]
    check("1e  the record is written with a raw os.write, which takes no "
          "Python-level lock and therefore cannot deadlock against a main "
          "thread mid-write",
          len(_writes), 1)
    _write_fd = None
    if _writes:
        _fd_arg = at(_writes[0].args, 0)
        _write_fd = getattr(_fd_arg, "value", None)
    check("1e  ...to fd 2, the same stream every line it explains goes to, so "
          "no redirection can separate the cause from its consequences",
          _write_fd, 2)
    check("1e  ...and the handler calls neither print nor console.out",
          sorted({getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                  for n in ast.walk(_handler_fn) if isinstance(n, ast.Call)}
                 & {"print", "out"}), [])


#------------------------------------------------------------------------------


# ===========================================================================
# THE HARNESS -- A REAL SUBPROCESS RUNNING THE REAL ENTRY POINT
# ===========================================================================
#
# WHAT IS A STAND-IN AND WHY, unchanged from
# tests/test_runner_crash_record_and_db_unification.py's list:
#
#   build_bm25_index_from_qdrant  needs a live Qdrant
#   build_matching_graph          compiles LangGraph and pulls in the agent
#   tracking                      would open a real MLflow store
#   process_patient               is ONE LIVE BILLED Stage 5 CALL per patient
#
# process_patient here SLEEPS and returns status="error". Sleeping is what makes
# a signal landable at a known point; "error" is what keeps `_on_done` off
# save_checkpoint(), which with no fingerprint argument resolves
# run_fingerprint.current() -- a live Qdrant round trip, in a bucket-A file. The
# same reasoning is written at the sibling stand-ins in that file and in
# tests/test_storage_run_metrics_flush.py.
#
# THE ENTRY POINT IS REACHED THROUGH runpy WITH run_name="__main__", so the
# handler under test is the shipped one in the shipped file rather than a copy
# of it in this test.

# THE STAND-INS ARRIVE THROUGH `usercustomize`, NOT THROUGH runpy OR exec, AND
# THAT IS AN INVARIANT OF THIS REPOSITORY RATHER THAN A PREFERENCE.
# tests/test_package_invariants.py section 1c forbids `exec()` outside a closed
# allowlist AND forbids loading a module BY LOCATION -- unconditionally, with
# `runpy` in its marker list and no allowlist escape. The first version of this
# file used `runpy.run_path(entry, run_name="__main__")` and that check FOUND IT,
# inside a string literal, by re-parsing string literals as Python. It was right
# to: the rule is the rule, and weakening a project invariant to accommodate a
# test is the wrong direction.
#
# THE CONSTRAINT IS REAL, THOUGH: a `__main__` guard only runs when the file is
# executed as `__main__`, and the four stand-ins must already be installed when
# it does. `importlib.import_module("25- Batch Runner")` imports the file fine
# (a space and a leading digit are no obstacle -- File 17's note) but with
# `__name__` set to the module name, so the guard is skipped and the handler
# under test is never installed. So the setup has to happen at INTERPRETER
# STARTUP, before the script runs, which is exactly what `usercustomize` is for.
#
# `usercustomize` RATHER THAN `sitecustomize`, measured: this interpreter ships
# its own `sitecustomize` (a conda path workaround) and a PYTHONPATH entry would
# SHADOW it in every subprocess here. `usercustomize` does not exist, so nothing
# is shadowed.
#
# ITS EXECUTION IS A HARD PRECONDITION, NOT AN ASSUMPTION. `usercustomize` is
# imported only when `site.ENABLE_USER_SITE` is true (`-s`, `PYTHONNOUSERSITE`
# and some distro builds turn it off), so the hook writes a MARKER FILE and
# every drive asserts it. A run whose stand-ins never installed would otherwise
# look like a run whose fix does not work.
#
# AND NO BILLED CALL IS REACHABLE EVEN THEN. Every subprocess is handed
# ONCOTRIAGE_QDRANT_URL pointed at a closed port, so an unstubbed
# build_bm25_index_from_qdrant fails and main() exits before Stage 5 exists.
# Belt and braces: the stubbed path never reads that variable, and the unstubbed
# path cannot get past it.

_HOOK = r"""
import os, sys, threading, time

# The repo is on PYTHONPATH beside this hook, so this import is an ordinary one.
from oncotriage.batch import runner as R
from oncotriage import paths as P

assert os.path.realpath(R.__file__).startswith(
    os.path.realpath(os.environ["ONC_REPO"])), (
    "PREFLIGHT: the runner that imported is not the one this run targets: "
    + os.path.realpath(R.__file__))

R.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
R.build_matching_graph = lambda *a, **k: object()
R.load_results = lambda *a, **k: []
R.clear_checkpoint = lambda *a, **k: None
R.load_checkpoint = lambda *a, **k: set()


class _Tracking:
    def start_run(self, **kw): pass
    def log_run_metrics(self, *a, **kw): pass
    def end_run(self, **kw): print("[stand-in] tracking.end_run", kw.get("status"))


R.tracking = _Tracking()

P._RESOLVED["data_fhir_path"] = os.environ["ONC_CORPUS"] + os.sep
P._RESOLVED["inferences_path"] = os.environ["ONC_DB"]
P._RESOLVED["checkpoint_path"] = os.environ["ONC_CP"] + os.sep

_STARTED = os.environ["ONC_STARTED"]
_READY = os.environ["ONC_READY"]
_RELEASE = os.environ["ONC_RELEASE"]
_CAP = float(os.environ["ONC_CAP"])
_lock = threading.Lock()


def _patient(fhir_path=None, graph=None, is_resample=False, run_id=None,
             db_path=None):
    '''Record that this patient STARTED, then block until the test releases it.

    BLOCKING RATHER THAN SLEEPING IS WHAT MAKES THE MEASUREMENT DETERMINISTIC.
    A queued patient can only start once a running one returns, so while every
    worker is parked here NOTHING can advance -- the test sends its signal,
    waits until the process has provably ENTERED its handler, and only then
    releases. With a sleep instead, a main thread starved of CPU could have its
    signal raised after the whole corpus had already run, which is exactly what
    a loaded machine produced: 40 of 40 started and no cancellation, on a tree
    where cancellation works.

    The cap is a deadlock guard, not a timing knob: if the test dies without
    releasing, these threads exit rather than hanging the run forever.
    '''
    name = os.path.basename(str(fhir_path))
    with _lock:
        with open(_STARTED, "a") as fh:
            fh.write(name + "\n")
        n = sum(1 for _ in open(_STARTED))
    if n == 1:
        with open(_READY, "w") as fh:
            fh.write("go")
    _deadline = time.time() + _CAP
    while not os.path.exists(_RELEASE) and time.time() < _deadline:
        time.sleep(0.01)
    return {"patient_id": name, "status": "error", "eligible_matches": 0,
            "near_misses": 0, "not_evaluable": 0, "total_time": 0.01,
            "timestamp": "2026-08-23T00:00:00",
            "error": "stand-in: no model was called", "is_resample": is_resample}


R.process_patient = _patient

# LAST: the marker every drive asserts. Written only once everything above has
# succeeded, so its presence means the stand-ins are installed rather than that
# the hook merely started.
with open(os.environ["ONC_HOOK_MARKER"], "w") as _fh:
    _fh.write("installed")
"""

_HOOK_DIR = os.path.join(_TMP, "hook")
os.makedirs(_HOOK_DIR, exist_ok=True)
with open(os.path.join(_HOOK_DIR, "usercustomize.py"), "w",
          encoding="utf-8") as _fh:
    _fh.write(_HOOK)

import site as _site
check("0a  user-site imports are enabled, so the stand-in hook will run "
      "(without this every drive below would run UNSTUBBED; it still could not "
      "bill anything -- see ONCOTRIAGE_QDRANT_URL below -- but it would prove "
      "nothing)",
      _site.ENABLE_USER_SITE, True)


def make_corpus(root, count):
    os.makedirs(root, exist_ok=True)
    for index in range(count):
        with open(os.path.join(root, f"patient{index:03d}.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"resourceType": "Bundle", "entry": []}, handle)
    return root


# THE HANDLER MARKER PER ARM. The test does not release the parked workers until
# the string that only that arm's handler can print has appeared, which is what
# turns "the signal was sent" into "the signal was RAISED and the handler ran".
_MARK_SIGTERM = "[SIGTERM] Termination requested"
_MARK_SIGINT = "[INTERRUPTED] Waiting for active threads to finish"
_MARKERS = {signal.SIGTERM: _MARK_SIGTERM, signal.SIGINT: _MARK_SIGINT}


def drive(name, *, sig, repo=None, patients=40, timeout=180, double=False):
    """Start the real entry point, deliver `sig` while the pool is saturated.

    THE SEQUENCE IS THE WHOLE DESIGN, and it replaced a sleep-based race that
    was measured flaky under load:

        1. every worker starts a patient and PARKS -- nothing can advance;
        2. wait until MAX_WORKERS (or the whole corpus) have started, so the
           pool is provably saturated and the queue provably non-empty;
        3. send the signal;
        4. wait until this arm's handler marker appears in the process's own
           output -- proof that the interrupt was RAISED, not merely sent;
        5. release the parked workers.

    A queued patient cannot start before step 5, so the started count is a
    statement about cancellation and about nothing else. `sig=None` skips 2-4
    and releases immediately, which is the arm that says the harness itself
    can run a batch to completion.

    stdout goes to a FILE rather than a pipe so step 4 can poll it without
    blocking on a read.
    """
    root = os.path.join(_TMP, name)
    os.makedirs(root, exist_ok=True)
    corpus = make_corpus(os.path.join(root, "fhir"), patients)
    db = os.path.join(root, "inferences.db")
    cp = os.path.join(root, "cp")
    os.makedirs(cp, exist_ok=True)
    started = os.path.join(root, "started.txt")
    ready = os.path.join(root, "ready.txt")
    release = os.path.join(root, "release.txt")
    log = os.path.join(root, "console.log")

    hook_marker = os.path.join(root, "hook_installed.txt")
    env = dict(os.environ)
    env.update({
        "ONC_REPO": repo or _REPO,
        "ONC_CORPUS": corpus,
        "ONC_DB": db,
        "ONC_CP": cp,
        "ONC_STARTED": started,
        "ONC_READY": ready,
        "ONC_RELEASE": release,
        "ONC_CAP": "120",
        "ONC_HOOK_MARKER": hook_marker,
        "ONCOTRIAGE_DEFER_LOCAL_MODELS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        # The hook dir FIRST so `usercustomize` resolves to ours, then the tree
        # under test so the hook's own `from oncotriage...` import is ordinary.
        "PYTHONPATH": os.pathsep.join([_HOOK_DIR, repo or _REPO]),
        # THE NO-SPEND BACKSTOP, and it does not depend on the hook working: a
        # closed port means an UNSTUBBED build_bm25_index_from_qdrant fails and
        # main() exits before Stage 5 exists. On the stubbed path this variable
        # is never read.
        "ONCOTRIAGE_QDRANT_URL": "http://127.0.0.1:1",
    })
    env.pop("PYTHONNOUSERSITE", None)

    def _count_started():
        try:
            with open(started, encoding="utf-8") as handle:
                return sum(1 for line in handle if line.strip())
        except OSError:
            return 0

    def _log_text():
        try:
            with open(log, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    def _wait(predicate, seconds):
        deadline = time.time() + seconds
        while time.time() < deadline:
            if predicate():
                return True
            if proc.poll() is not None:
                return predicate()
            time.sleep(0.02)
        return predicate()

    signalled = False
    handler_entered = None
    saturated = None
    with open(log, "w", encoding="utf-8") as _sink:
        # THE REAL ENTRY POINT, RUN AS A SCRIPT -- so its `__main__` guard, and
        # therefore the shipped SIGTERM handler, is what runs.
        proc = subprocess.Popen([sys.executable, _ENTRY_PATH],
                                stdout=_sink, stderr=subprocess.STDOUT,
                                text=True, env=env, cwd=_REPO)
        try:
            if sig is not None:
                # 1-2: the pool is saturated and the queue is not empty.
                _wait(lambda: os.path.exists(ready), 90)
                want = min(MAX_WORKERS, patients)
                saturated = _wait(lambda: _count_started() >= want, 60)
                # 3: the signal.
                if proc.poll() is None:
                    proc.send_signal(sig)
                    signalled = True
                # 4: proof it was RAISED. Released regardless afterwards, so a
                # marker that never appears is a recorded failure below rather
                # than a hung run.
                mark = _MARKERS.get(sig)
                # 30s, NOT 90: a real handler writes its marker within
                # milliseconds of the signal, so a longer budget buys nothing
                # when the fix is intact and costs a minute per arm when it is
                # not -- measured while reverting the raw os.write to a buffered
                # `print`, where the marker never appears at all and every
                # signalled arm burned the full wait.
                handler_entered = _wait(
                    lambda: (mark in _log_text()) if mark else True, 30)
                if double and proc.poll() is None:
                    # THE SECOND SIGNAL LANDS WHILE THE MAIN THREAD IS BLOCKED
                    # IN executor.shutdown(wait=True), waiting on workers this
                    # test has not released -- which is the window the
                    # disposition reset exists for, made deterministic by the
                    # barrier rather than raced for.
                    proc.send_signal(sig)
                    _wait(lambda: proc.poll() is not None, 30)
            # 5: release.
            with open(release, "w", encoding="utf-8") as handle:
                handle.write("go")
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        finally:
            if proc.poll() is None:                       # pragma: no cover
                proc.kill()
                proc.wait()

    out = _log_text()
    started_names = ([line.strip() for line in open(started, encoding="utf-8")]
                     if os.path.exists(started) else [])
    runs = []
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        try:
            runs = conn.execute(
                "SELECT id, status, finished_at FROM runs ORDER BY id").fetchall()
            metrics = conn.execute(
                "SELECT COUNT(*) FROM run_metrics").fetchone()[0]
        except sqlite3.Error as exc:                            # noqa: BLE001
            metrics = f"<sqlite error: {exc}>"
        finally:
            conn.close()
    else:
        metrics = "<no database>"

    return {"exit": proc.returncode, "out": out, "signalled": signalled,
            "hook": os.path.exists(hook_marker),
            "saturated": saturated, "handler_entered": handler_entered,
            "started": [n for n in started_names if n],
            "runs": runs, "metrics": metrics, "patients": patients, "db": db}


#------------------------------------------------------------------------------


# ===========================================================================
# 2. A REAL SIGTERM
# ===========================================================================

print("\n=== 2. a real SIGTERM against the real entry point ===")

_TERM = drive("sigterm", sig=signal.SIGTERM)

check("2a-0 the stand-in hook installed (non-degeneracy for everything below: "
      "an unstubbed run proves nothing about cancellation or about the "
      "handler)",
      _TERM["hook"], True)
check("2a  the pool was saturated, the signal was delivered, and the handler "
      "was provably ENTERED before any queued patient could start -- which is "
      "what makes the counts below a statement about cancellation rather than "
      "about scheduling luck",
      (_TERM["saturated"], _TERM["signalled"], _TERM["handler_entered"]),
      (True, True, True))
check("2b  the process exits 128 + SIGTERM, not the reconciliation verdict",
      _TERM["exit"], 128 + int(signal.SIGTERM))
check("2c  the handler announced itself, so the crash blocks below it have a "
      "stated cause",
      "[SIGTERM] Termination requested" in _TERM["out"], True)
check("2d  no traceback: an orchestrator-requested shutdown is not a crash "
      "report",
      "Traceback (most recent call last)" in _TERM["out"], False)

# --- the crash record -------------------------------------------------------
check("2e  BOTH crash blocks printed (census and degradation), which is the "
       "only place a killed campaign's census counts have ever existed",
      (_TERM["out"].count("CENSUS") >= 1,
       _TERM["out"].count("DEGRADATION") >= 1), (True, True))

# --- the run row ------------------------------------------------------------
check("2f  exactly one run row was created",
      len(_TERM["runs"]), 1)
check("2g  ...and it is finalized KILLED",
      at(at(_TERM["runs"], 0, ()), 1), "KILLED")
check("2h  ...with a non-NULL finished_at, which is what separates a process "
      "that ran a handler from one that was SIGKILLed",
      at(at(_TERM["runs"], 0, ()), 2) not in (None, "", "<absent>"), True)

# --- the health record ------------------------------------------------------
check("2i  the crash path flushed a health record (run_metrics rows exist)",
      isinstance(_TERM["metrics"], int) and _TERM["metrics"] > 0, True)

# --- the cancellation -------------------------------------------------------
#
# THE NUMBER, NOT A FLAG. `started` is appended to by the stand-in as each
# patient BEGINS, so its length is exactly how many patients this run paid for.
# With MAX_WORKERS workers and a signal delivered shortly after the first one
# starts, a cancelling pool starts a small multiple of MAX_WORKERS; a draining
# one starts all of them.
_STARTED_TERM = len(_TERM["started"])
print(f"        [info] SIGTERM: {_STARTED_TERM} of {_TERM['patients']} "
      f"patients started")
# EXACT, NOT AN INEQUALITY, because the barrier makes it exact: only the
# MAX_WORKERS patients parked when the signal landed can ever have started, and
# every one of the remaining 28 was queued and cancelled.
check("2j  queued patients were CANCELLED, not drained: exactly the saturated "
      "pool ran and the whole queue was discarded",
      (_STARTED_TERM, _TERM["patients"] - _STARTED_TERM),
      (min(MAX_WORKERS, _TERM["patients"]),
       _TERM["patients"] - min(MAX_WORKERS, _TERM["patients"])))
# DERIVED FROM MAX_WORKERS rather than typed as 2: the floor is "more than one
# worker was in flight", and a future MAX_WORKERS of 1 would make a literal 2
# fail for a reason that is about configuration rather than about cancellation.
# A CANCELLED PATIENT IS NOT AN ERROR, AND THAT LINE IS A NUMBER SOMEBODY READS.
# `concurrent.futures.CancelledError` subclasses Exception, so before the pool
# started cancelling -- which is this pass -- the generic done-callback handler
# absorbed it and reported every queued patient as a failure, one
# "[CALLBACK ERROR] CancelledError:" line each. Measured on the drain arm before
# the fix: 28 such lines for 40 patients.
# THE COUNT ITSELF IS ASSERTED ON THE SIGINT ARM (3f-b), not here: SystemExit
# propagates out of the pool's `finally`, so run_batch's "MAIN BATCH COMPLETE"
# line is never reached on this path. What IS observable here is that the
# per-patient noise is gone.
check("2j-b cancelled patients printed no [CALLBACK ERROR] lines: they were "
      "never attempted, so they are not failures",
      _TERM["out"].count("[CALLBACK ERROR] CancelledError"), 0)
check("2k  ...and the pool really was running when the signal landed "
      "(non-degeneracy: 0 or 1 started would make 2j true for the wrong "
      "reason)",
      _STARTED_TERM >= min(2, MAX_WORKERS), True)


#------------------------------------------------------------------------------


# ===========================================================================
# 2b. A SECOND SIGTERM GIVES UP, DELIBERATELY
# ===========================================================================
#
# The disposition reset in 1e, driven. The second signal lands while the main
# thread is blocked in `executor.shutdown(wait=True)` waiting on workers this
# test has not released -- the exact window in which a still-installed handler
# would raise SystemExit through `flush_health` and `print_crash_record`, both of
# which catch `Exception` and not `BaseException`, and leave a half-written
# record. With the default restored the process is terminated by the signal
# instead: an operator who asks twice gets what they asked for.

print("\n=== 2b. a second SIGTERM terminates ===")

_TWICE = drive("sigterm-twice", sig=signal.SIGTERM, double=True)

check("2b-0 the stand-in hook installed", _TWICE["hook"], True)
check("2b-1 the process was TERMINATED BY THE SIGNAL rather than exiting "
      "through the handler a second time (a negative returncode is the signal "
      "number)",
      _TWICE["exit"], -int(signal.SIGTERM))
check("2b-2 ...and it is not the handler's own 143, which is what a "
      "still-installed handler would have produced",
      _TWICE["exit"] == 128 + int(signal.SIGTERM), False)
check("2b-3 ...and the first signal's record line was still written, so the "
      "give-up is the SECOND request and not a lost first one",
      "[SIGTERM] Termination requested" in _TWICE["out"], True)
check("2b-4 ...and the handler said so in that line",
      "Send it again to give up on the record" in _TWICE["out"], True)


#------------------------------------------------------------------------------


# ===========================================================================
# 3. A REAL SIGINT -- THE CONTRACT THAT CHANGED
# ===========================================================================
#
# THIS SECTION USED TO PIN THE OPPOSITE, AND THE THING IT PINNED WAS A DEFECT.
# It read: "the two signals have DIFFERENT dispositions on purpose ... the pool
# absorbs it, says so, and the run reaches its own end", and check 3d asserted
# that a Ctrl-C'd run was recorded FAILED rather than KILLED. That was an
# accurate description of what the code did and it is what made the code's
# behaviour indefensible:
#
#   * both pool handlers caught KeyboardInterrupt, tore the pool down, printed
#     "[INTERRUPTED] Checkpoint saved. Safe to resume." and RETURNED NORMALLY;
#   * so main() carried straight on into the RESAMPLE pass -- RESAMPLE_COUNT is
#     100 -- at ONE LIVE BILLED Stage 5 CALL PER PATIENT, after the operator
#     had asked the run to stop;
#   * and then finalized the `runs` row and the tracking run as a completed
#     campaign, so an interrupted run's rows were indistinguishable from a
#     covered cohort's.
#
# "25- Batch Runner.py"'s own SIGTERM note cites this file's section 3 as the
# measured reason SIGTERM had to be a SystemExit rather than a
# KeyboardInterrupt. That reasoning was right about the code and the right fix
# was the other one: make Ctrl-C stop the run too.
#
# THE DIVERGENCE THAT REMAINS IS SMALLER AND IS STILL PINNED. Both signals now
# stop the run and record it KILLED; they differ in exit code (130 vs 143) and
# in whether a disposition is installed (SIGINT keeps CPython's default -- see
# 1d). The THIRD request, "stop cleanly and record it as STOPPED", is the
# operator stop switch, and it is tests/test_runner_stop_switch.py's subject.

print("\n=== 3. a real SIGINT: it now STOPS the run ===")

_INT = drive("sigint", sig=signal.SIGINT)

check("3a-0 the stand-in hook installed", _INT["hook"], True)
check("3a  the pool was saturated, the signal was delivered, and the pool's "
      "own interrupt handler was provably entered",
      (_INT["saturated"], _INT["signalled"], _INT["handler_entered"]),
      (True, True, True))
check("3b  the pool still announces the teardown, unchanged",
      "[INTERRUPTED] Waiting for active threads to finish" in _INT["out"], True)
# THE OLD LINE IS ASSERTED ABSENT, not merely replaced. "Checkpoint saved. Safe
# to resume." was true about the checkpoint and false about the run: what the
# pair implied -- a tidy pause the run would carry on from -- is what the
# operator acted on. A test that only checked for the new string would pass on a
# tree that printed both.
check("3c  the message that claimed the run continues is GONE",
      "[INTERRUPTED] Checkpoint saved. Safe to resume." in _INT["out"], False)
check("3c-b ...and is replaced by one that says the checkpoint is current AND "
      "that the run is stopping",
      ("[INTERRUPTED] Checkpoint saved: every completed patient is in it"
       in _INT["out"],
       "[INTERRUPTED] STOPPING THE RUN." in _INT["out"]),
      (True, True))
check("3c-c ...and it names the stop switch as the way to stop WITHOUT being "
      "recorded KILLED, so an operator meeting this line learns the third "
      "option exists",
      "use the stop switch: touch" in _INT["out"], True)
# --- THE CONTRACT CHANGE ITSELF ---------------------------------------------
check("3d  THE RUN IS RECORDED KILLED. Ctrl-C now reaches main()'s crash "
      "handler, which is what makes an interrupted campaign distinguishable "
      "from a completed one in `runs`",
      sorted({row[1] for row in _INT["runs"]}) or ["<no run row>"], ["KILLED"])
check("3d-b ...and it is FINALIZED, not left at RUNNING with a NULL "
      "finished_at -- that shape is reserved for a process that got to run no "
      "handler at all",
      [row[2] is not None for row in _INT["runs"]] or ["<no run row>"], [True])
check("3d-c ...and BOTH crash blocks printed (census and degradation), which "
      "on this path are the only record the census counters ever have -- the "
      "same two blocks section 2e reads for SIGTERM",
      (_INT["out"].count("CENSUS") >= 1,
       _INT["out"].count("DEGRADATION") >= 1,
       "[Run] Closed run" in _INT["out"]),
      (True, True, True))
# --- THE MONEY ---------------------------------------------------------------
# THE RESAMPLE PASS IS WHAT THE OLD BEHAVIOUR PAID FOR. At the shipped
# RESAMPLE_COUNT of 100 an interrupted run went on to make ~100 further live
# Stage 5 calls. Asserting the pass never STARTS is the cost proof: the
# stand-in's started-file is the ledger, and it is checked below too, but the
# header is what says main() never entered the pass at all.
check("3d-d THE RESAMPLE PASS NEVER RAN -- the single most expensive "
      "consequence of the old swallow",
      ("RESAMPLE PASS" in _INT["out"], "RESAMPLE COMPLETE" in _INT["out"]),
      (False, False))
check("3e  the exit code is 128 + SIGINT, the shell convention, and NOT the "
      "SIGTERM one and NOT the reconciliation verdict",
      (_INT["exit"], _INT["exit"] == 128 + int(signal.SIGTERM)),
      (128 + int(signal.SIGINT), False))
check("3e-b ...with NO traceback: an operator-requested stop is not a crash "
      "report, which is the same ruling the SIGTERM handler already made",
      "Traceback (most recent call last)" in _INT["out"], False)
check("3e-c ...and the entry point said what happened and how to resume",
      "[INTERRUPTED] Stopped by Ctrl-C." in _INT["out"], True)
# THE DRAIN FIX APPLIES TO CTRL-C TOO, and that IS a change: the handler's own
# `cancel_futures=True` finally bites. Nothing is lost by it -- a cancelled
# patient is never checkpointed, so a resume runs it -- and the two printed
# lines above are unchanged.
_STARTED_INT = len(_INT["started"])
print(f"        [info] SIGINT: {_STARTED_INT} of {_INT['patients']} "
      f"patients started")
check("3f  Ctrl-C also cancels queued patients now, which is what the pool "
      "handler always SAID it did",
      (_STARTED_INT, _STARTED_INT < _INT["patients"]),
      (min(MAX_WORKERS, _INT["patients"]), True))
# THE ARM THAT REACHES THE SUMMARY LINE, so this is where the count is read.
# `concurrent.futures.CancelledError` subclasses Exception (it is
# futures.Error, not asyncio's BaseException-derived class of the same name --
# MEASURED), so the generic done-callback handler used to absorb it: every
# cancelled patient printed a "[CALLBACK ERROR] CancelledError:" line and was
# counted as a failure. On a 22,000-patient corpus interrupted early that is a
# summary claiming 22,000 errors for work nobody ran. The branch became
# REACHABLE in this same pass -- before the pool cancelled, no future was ever
# cancelled and the branch would have been dead code.
# THE TALLY MOVED, AND THAT IS A CONSEQUENCE OF THE RE-RAISE WORTH PINNING.
# run_batch's "MAIN BATCH COMPLETE: ..." line sits BELOW the try/finally, so
# re-raising skips it -- and losing the three numbers would have been an
# information regression bought by a correctness fix. The interrupt handler
# prints them itself, in the summary line's own wording, BEFORE it re-raises.
check("3f-b the cancelled patients are reported as cancelled rather than as "
      "errors, both in the count and in the absence of per-patient error lines",
      (f", {_INT['patients'] - _STARTED_INT} cancelled (never attempted)"
       in _INT["out"],
       _INT["out"].count("[CALLBACK ERROR] CancelledError")),
      (True, 0))
check("3f-c ...and the tally survives the re-raise, printed by the handler "
      "rather than by the summary line the raise skips",
      "[INTERRUPTED] MAIN BATCH INTERRUPTED:" in _INT["out"], True)
check("3f-d ...and the normal-path summary line is correspondingly ABSENT, so "
      "an interrupted pass cannot be read as a completed one",
      "MAIN BATCH COMPLETE:" in _INT["out"], False)

# --- THE COST PROOF, BY COUNTING WHAT THE STUB WAS ASKED TO DO --------------
#
# The stand-in appends one line per patient it is CALLED for, so this file is
# the ledger of every would-be billed call. Under the old swallow it carried the
# main pass's started patients AND ~RESAMPLE_COUNT more; under the fix it can
# carry nothing beyond the main pass's, because run_resample is never entered.
#
# A CORPUS OF 40 AND RESAMPLE_COUNT OF 100 means the resample pass would have
# re-run min(100, completed) patients -- but the stand-in returns status="error"
# so NOTHING is ever completed here and the resample pass would have found no
# candidates. THAT IS WHY THE HEADER CHECK ABOVE (3d-d) IS THE LOAD-BEARING ONE
# and this is the corroboration rather than the proof: it says no call was made
# after the ones the main pass had already started, which is the property the
# started count already establishes. Stated plainly rather than dressed up as
# more than it is; the STOP switch's own file drives a corpus that DOES complete
# patients and therefore DOES reach a resample pass with candidates.
check("3f-e no patient was started beyond the pool's own saturation -- so "
      "nothing was called after the interrupt was raised",
      _STARTED_INT, min(MAX_WORKERS, _INT["patients"]))


#------------------------------------------------------------------------------


# ===========================================================================
# 4. THE CONTROL FOR THE DRAIN FIX
# ===========================================================================
#
# 2j and 3f are claims about a NUMBER, and a number means nothing without the
# other arm. The pre-fix DRAIN is reconstructed in a COPY of the package and the
# same scenario is driven against it.
#
# THE REVERT IS THE CANCELLATION, NOT THE `with` STATEMENT, and that is a
# deliberate choice of instrument. The defect is behavioural -- queued futures
# are DRAINED rather than cancelled -- and `shutdown(wait=True)` without
# `cancel_futures` drains identically to `__exit__`'s, because `__exit__` IS
# that call. Reverting two keywords reproduces the exact defect with no
# re-indentation, so the control cannot fail for a reason that is about
# whitespace; the STRUCTURAL half (no context manager, a cancelling `finally`)
# is pinned separately by 1b and 1c, where a text edit cannot satisfy it by
# accident.
#
# IT IS SCOPED TO run_batch's AST SPAN, so run_resample -- which has no scenario
# of its own here, because reaching it needs a completed main pass -- is left
# alone and cannot absorb the plant.
#
# A copy in the temp directory, imported by a subprocess whose sys.path points
# at it, with a realpath preflight in the driver asserting the copy is what
# imported. Nothing in the repository is written.

print("\n=== 4. the control: without cancel_futures the pool drains ===")

_CTRL_REPO = os.path.join(_TMP, "pkgcopy")
os.makedirs(_CTRL_REPO, exist_ok=True)
shutil.copytree(os.path.join(_REPO, "oncotriage"),
                os.path.join(_CTRL_REPO, "oncotriage"),
                ignore=shutil.ignore_patterns("__pycache__"))

_CTRL_RUNNER = os.path.join(_CTRL_REPO, "oncotriage", "batch", "runner.py")
_ctrl_src = open(_CTRL_RUNNER, encoding="utf-8").read()
_ctrl_fn = function_named(ast.parse(_ctrl_src), "run_batch")

check("4a  run_batch was located in the copied package (non-degeneracy: "
      "without a span the plant below would have nowhere to go)",
      _ctrl_fn is not None, True)

_CANCELLING = "shutdown(wait=True, cancel_futures=True)"
_DRAINING = "shutdown(wait=True)"

if _ctrl_fn is None:
    fail("4b  the plant applied",
         "PLANT-FAILED: run_batch not found in the copy, so the drain control "
         "did not run and 2j/3f are unverified.")
else:
    _lines = _ctrl_src.splitlines(keepends=True)
    _span = "".join(_lines[_ctrl_fn.lineno - 1:_ctrl_fn.end_lineno])
    _n_in_span = _span.count(_CANCELLING)
    check("4b  the plant site occurs exactly twice inside run_batch -- the "
          "interrupt handler's and the finally's -- and reverting BOTH is what "
          "reproduces the drain (a plant that matched nothing would report the "
          "fix as ineffective)",
          _n_in_span, 2)
    if _n_in_span == 2:
        _reverted_span = _span.replace(_CANCELLING, _DRAINING)
        _ctrl_new = ("".join(_lines[:_ctrl_fn.lineno - 1]) + _reverted_span
                     + "".join(_lines[_ctrl_fn.end_lineno:]))
        try:
            ast.parse(_ctrl_new)
            _parsed = True
        except SyntaxError as _exc:                              # noqa: BLE001
            _parsed = False
            fail("4c  the reverted copy parses",
                 f"PLANT-FAILED: {_exc}. A control that does not compile is "
                 f"not a control; 2j and 3f are unverified until it does.")
        if _parsed:
            _ctrl_reverted_fn = function_named(ast.parse(_ctrl_new), "run_batch")
            _ctrl_reverted_span = "".join(
                _ctrl_new.splitlines(keepends=True)
                [_ctrl_reverted_fn.lineno - 1:_ctrl_reverted_fn.end_lineno])
            check("4c  the reverted copy parses and run_batch no longer "
                  "cancels anything, while run_resample is untouched",
                  (_ctrl_reverted_span.count(_CANCELLING),
                   _ctrl_new.count(_CANCELLING)), (0, 2))
            open(_CTRL_RUNNER, "w", encoding="utf-8").write(_ctrl_new)
            _CTRL = drive("control", sig=signal.SIGTERM, repo=_CTRL_REPO)
            _STARTED_CTRL = len(_CTRL["started"])
            print(f"        [info] control (no cancel_futures): "
                  f"{_STARTED_CTRL} of {_CTRL['patients']} patients started")
            check("4c-0 the stand-in hook installed in the control too",
                  _CTRL["hook"], True)
            check("4d  the pre-fix form DRAINS: every queued patient runs "
                  "before the process can exit, at one live billed Stage 5 "
                  "call each in production",
                  _STARTED_CTRL, _CTRL["patients"])
            check("4e  ...and the shipped form started strictly fewer, which "
                  "is the fix MEASURED rather than asserted",
                  _STARTED_TERM < _STARTED_CTRL, True)
            check("4f  ...and the control still reaches the crash record, so "
                  "4d is a statement about the drain alone and not about a "
                  "copy that failed to run",
                  (_CTRL["exit"], sorted({r[1] for r in _CTRL["runs"]})),
                  (128 + int(signal.SIGTERM), ["KILLED"]))


#------------------------------------------------------------------------------


# ===========================================================================
# 4b. THE CONTROL FOR THE RE-RAISE -- THE OLD SWALLOW, SHOWN TO FAIL
# ===========================================================================
#
# Section 3 asserts a CONTRACT CHANGE, and a contract change asserted without
# its other arm is a description of whatever the code happens to do. The
# pre-fix SWALLOW is reconstructed in a second copy of the package -- the
# `raise` deleted from run_batch's `except KeyboardInterrupt` and from nowhere
# else -- and the same SIGINT scenario is driven against it.
#
# THE PLANT IS STRUCTURAL, NOT TEXTUAL. `raise` is a bare keyword that appears
# in several handlers in this module, so a string replace would either hit the
# wrong one or need an anchor long enough to be its own maintenance problem.
# The handler is located by AST inside run_batch's span, its trailing
# `ast.Raise` is located by node, and that node's LINES are removed -- so the
# plant cannot silently match nothing, and a `raise` that has moved out of the
# handler is a PLANT-FAILED rather than a control that quietly tests the
# shipped tree against itself.
#
# WHAT THIS CONTROL CAN AND CANNOT SHOW, stated rather than implied. The
# stand-in returns status="error", so no patient is ever COMPLETED here and
# main() skips the resample pass in BOTH arms ("no successfully completed
# patients"). So this control proves the RECORD half of the defect -- an
# interrupted campaign finalized as though it had ended normally -- and not the
# SPEND half. The spend half needs a corpus that completes patients and
# therefore reaches a resample pass with candidates, and that is driven in
# tests/test_runner_stop_switch.py, which has exactly that harness.

print("\n=== 4b. the control: without the re-raise, Ctrl-C is swallowed ===")

_RR_REPO = os.path.join(_TMP, "pkgcopy_reraise")
os.makedirs(_RR_REPO, exist_ok=True)
shutil.copytree(os.path.join(_REPO, "oncotriage"),
                os.path.join(_RR_REPO, "oncotriage"),
                ignore=shutil.ignore_patterns("__pycache__"))

_RR_RUNNER = os.path.join(_RR_REPO, "oncotriage", "batch", "runner.py")
_rr_src = open(_RR_RUNNER, encoding="utf-8").read()
_rr_fn = function_named(ast.parse(_rr_src), "run_batch")


def _ki_raise_lines(fn):
    """The line numbers of the bare `raise` closing run_batch's KI handler.

    Returns an empty list when there is none, which is what makes a moved or
    deleted `raise` a named PLANT-FAILED instead of a silent no-op.
    """
    if fn is None:
        return []
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if getattr(handler.type, "id", None) != "KeyboardInterrupt":
                continue
            for stmt in handler.body:
                if isinstance(stmt, ast.Raise) and stmt.exc is None:
                    out.append((stmt.lineno, stmt.end_lineno))
    return out


_rr_targets = _ki_raise_lines(_rr_fn)
check("4b-a run_batch's `except KeyboardInterrupt` closes with exactly one "
      "bare `raise` (non-degeneracy: the plant below has a target, and a tree "
      "where the re-raise had been removed would fail HERE rather than "
      "reporting an ineffective control)",
      len(_rr_targets), 1)

if len(_rr_targets) != 1:
    fail("4b-b the plant applied",
         "PLANT-FAILED: run_batch's KeyboardInterrupt handler does not end in "
         "a single bare `raise`, so the swallow control did not run and every "
         "section 3 contract check is unverified.")
else:
    _rr_lines = _rr_src.splitlines(keepends=True)
    _lo, _hi = _rr_targets[0]
    _rr_new = "".join(_rr_lines[:_lo - 1] + _rr_lines[_hi:])
    _rr_reverted_fn = function_named(ast.parse(_rr_new), "run_batch")
    check("4b-b the reverted copy parses and run_batch's handler no longer "
          "re-raises, while run_resample's is untouched",
          (len(_ki_raise_lines(_rr_reverted_fn)),
           len(_ki_raise_lines(function_named(ast.parse(_rr_new),
                                              "run_resample")))),
          (0, 1))
    open(_RR_RUNNER, "w", encoding="utf-8").write(_rr_new)
    _RR = drive("control-reraise", sig=signal.SIGINT, repo=_RR_REPO)
    check("4b-c the stand-in hook installed in the control too",
          _RR["hook"], True)
    check("4b-d the control really was interrupted -- the pool handler ran, "
          "so 4b-e/f are about the swallow and not about a copy that never "
          "saw the signal",
          ("[INTERRUPTED] Waiting for active threads to finish" in _RR["out"],
           _RR["saturated"], _RR["handler_entered"]),
          (True, True, True))
    check("4b-e THE PRE-FIX FORM RECORDS THE INTERRUPTED RUN AS ENDED "
          "NORMALLY -- FAILED here (every stand-in patient errors), never "
          "KILLED. This is the defect: an interrupted campaign and a completed "
          "one are the same row.",
          sorted({r[1] for r in _RR["runs"]}) or ["<no run row>"], ["FAILED"])
    check("4b-f ...and it prints the normal-path summary line and reaches the "
          "reconciliation exit code rather than 128 + SIGINT",
          ("MAIN BATCH COMPLETE:" in _RR["out"],
           _RR["exit"] == 128 + int(signal.SIGINT)),
          (True, False))
    check("4b-g ...and the shipped tree does the opposite on all three, which "
          "is the fix MEASURED rather than asserted",
          (sorted({r[1] for r in _INT["runs"]}),
           "MAIN BATCH COMPLETE:" in _INT["out"],
           _INT["exit"] == 128 + int(signal.SIGINT)),
          (["KILLED"], False, True))


#------------------------------------------------------------------------------


# ===========================================================================
# 5. NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================

print("\n=== 5. the repository is unchanged ===")

_SHA_RUNNER_AFTER = hashlib.sha256(open(_RUNNER_PATH, "rb").read()).hexdigest()
_SHA_ENTRY_AFTER = hashlib.sha256(open(_ENTRY_PATH, "rb").read()).hexdigest()
check("5a  oncotriage/batch/runner.py is byte-identical",
      _SHA_RUNNER_AFTER, _SHA_RUNNER_BEFORE)
check("5b  25- Batch Runner.py is byte-identical",
      _SHA_ENTRY_AFTER, _SHA_ENTRY_BEFORE)
check("5c  ...and those comparisons are not tautologies: both files are "
      "non-empty and were re-read from disk",
      (len(_RUNNER_SRC) > 1000, len(_ENTRY_SRC) > 1000,
       _SHA_RUNNER_BEFORE != _SHA_ENTRY_BEFORE), (True, True, True))

shutil.rmtree(_TMP, ignore_errors=True)
check("5d  the scratch tree was removed", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
print(f"Runtime: {time.time() - _T_START:.2f}s")

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
Created on Sun Aug 23 12:00:00 2026

@author: ramyalsaffar
"""
