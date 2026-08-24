# Batch Runner Operator Stop Switch Test
#######################################

"""The STOP sentinel, and the Ctrl-C leak it was built beside.

WHAT WAS MISSING
----------------
    1. THERE WAS NO WAY TO STOP A BATCH RUN CLEANLY. A run is hours long and
       costs one live Stage 5 call per patient, and the two available gestures
       were both wrong for "finish what you started and stop":

         * Ctrl-C needed a terminal the process is attached to, so it was
           unavailable under nohup, screen, systemd, a container or cron; and
         * SIGTERM is deliberately ABRUPT -- it is what an orchestrator sends
           before SIGKILL -- so it records the run KILLED and abandons
           in-flight billed requests mid-read.

    2. AND Ctrl-C DID NOT EVEN STOP THE RUN. Both pool handlers CAUGHT the
       KeyboardInterrupt, shut the pool down, printed "[INTERRUPTED] Checkpoint
       saved. Safe to resume." and RETURNED NORMALLY -- so main() carried
       straight on into the RESAMPLE pass at ONE LIVE BILLED CALL PER PATIENT
       (RESAMPLE_COUNT is 100) and then finalized the `runs` row FINISHED. An
       interrupted campaign was indexed as a completed one, and every rate
       computed over its rows was a rate about a cohort prefix presented as a
       rate about the cohort.

WHAT THIS FILE HOLDS
--------------------
    1. THE MECHANISM, driven directly: one owner for the sentinel path, a
       latching thread-safe poll, an empty file valid, a note read and capped,
       an unreadable note counted WITHOUT losing the stop, a poll that RAISES
       counted WITHOUT inventing one, and the per-run reset.
    2. THE INTERACTION MATRIX, driven END TO END against the REAL entry point
       in REAL subprocesses:

         A  STOP mid-batch      in-flight finish, queue cancelled, run STOPPED,
                                checkpoint KEPT, resample never entered, exit 0
         B  resume after A      the sentinel deleted, the remaining patients run
         C  STOP mid-resample   the resample pass stops too
         D  Ctrl-C mid-batch    run KILLED, ZERO resample calls, resume works
         E  STOP at start       refused, exit 1, nothing started, no run row
         F  --clear-stop        the same invocation clears it and runs

    3. THE COST PROOF, by counting what the stand-in was ASKED to do. Every
       would-be billed call appends a line naming its phase, so "zero calls
       fired after the stop point" is a number read out of a file rather than a
       claim. Scenario D is the money one: its CONTROL -- the same scenario
       against a copy of the package with the `raise` deleted from run_batch's
       KeyboardInterrupt handler -- makes MAIN_WORKERS further billed calls
       after the operator asked the run to stop, and the shipped tree makes
       none.
    4. CAMPAIGN STITCHING over the new status, at the SQL level: a
       STOPPED-then-resumed chain must be one campaign exactly as a
       KILLED-then-resumed chain is, or a stopped-and-resumed cohort reports as
       two fragments neither of which covers it.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, NO SPEND, no live Qdrant, no model load (every subprocess
sets ONCOTRIAGE_DEFER_LOCAL_MODELS and the graph is never invoked), no corpus --
every FHIR file is a two-key literal in a temp directory -- no git history, no
live server. `process_patient`, the BM25 index, the graph, the tracking module
and `run_fingerprint.current` are stand-ins; EVERYTHING ELSE IS THE REAL THING:
the real `main()`, `run_batch`, `run_resample`, `_on_done`, `save_checkpoint`,
`load_checkpoint`, `flush_health`, `start_run_record`, `finalize_run_record`,
`reconcile_writes`, both crash handlers, and the real `__main__` guard of
`25- Batch Runner.py`.

WHY `run_fingerprint.current` IS A STAND-IN and the other four are not: this
file's stand-in patients SUCCEED (they must, or no patient is ever completed and
the resample pass is unreachable, which is the gap the sigterm file's own
control records). A successful patient makes `_on_done` call the REAL
`save_checkpoint`, which resolves the configuration stamp -- a live Qdrant round
trip. A literal stamp keeps `save_checkpoint` and `load_checkpoint` real, which
is what scenarios B and D's resume halves are about.

IT USES SUBPROCESSES AND A REAL SIGNAL for scenario D, for the sigterm file's
reason: a signal cannot be delivered to the process asserting about it.

NOT IN THE COLLISION MATRIX, derived: every database, checkpoint, sentinel, FHIR
file and package copy it writes is inside a `tempfile.mkdtemp` it removes and
then asserts gone; it patches no repository file; and the two repository files it
READS -- `oncotriage/batch/runner.py` and `25- Batch Runner.py` -- are written by
neither of the suite's two writers and are sha256-compared at the end.

IT EXECS NOTHING, and needs no `_EXEC_ALLOWLIST` entry: the one control is a
COPY of the package written into that temp directory and imported from there by
a subprocess whose PYTHONPATH points at it, with a realpath preflight asserting
the copy is what imported.

Run from terminal:
    python tests/test_runner_stop_switch.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
import os
import sys

# ABOVE THE PACKAGE IMPORTS, on oncotriage/fixtures/replay.py's precedent:
# oncotriage/agent/deps.py reads this once, at ITS OWN import.
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
import shutil
import signal
import sqlite3
import subprocess
import tempfile
import threading
import time

import oncotriage
from oncotriage import paths as _paths
from oncotriage.batch import runner as _runner
from oncotriage.config import MAX_WORKERS, RESAMPLE_COUNT
from oncotriage import degradation as _degradation
from oncotriage.storage import database_logger as _dblog

# `oncotriage.storage.queries` IS IMPORTED IN SECTION 9, NOT HERE, AND THAT IS
# A CORRECTION THE REVERT HARNESS FORCED. That module raises a RuntimeError AT
# IMPORT when CAMPAIGN_RESUMABLE_STATUSES stops being a proper subset of
# RUN_RECORD_TERMINAL_STATUSES -- which is exactly the state a revert of the
# STOPPED vocabulary produces, and which is the guard working. With the import
# at module scope this file then reported ONE TRACEBACK where it owed a summary
# and 118 results: the abort shape this repository has shipped eleven times and
# forbids. Deferred and wrapped, the same revert produces a NAMED failure and
# every other section still runs.


#------------------------------------------------------------------------------


_T_START = time.time()

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_RUNNER_PATH = os.path.abspath(_runner.__file__)
_ENTRY_PATH = os.path.join(_REPO, "25- Batch Runner.py")

_RUNNER_SRC = open(_RUNNER_PATH, encoding="utf-8").read()
_ENTRY_SRC = open(_ENTRY_PATH, encoding="utf-8").read()
_SHA_RUNNER_BEFORE = hashlib.sha256(_RUNNER_SRC.encode("utf-8")).hexdigest()
_SHA_ENTRY_BEFORE = hashlib.sha256(_ENTRY_SRC.encode("utf-8")).hexdigest()

_TMP = tempfile.mkdtemp(prefix="stopswitch_")


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
        return True
    _RESULTS["failed"] += 1
    print(f"  FAIL  {label}")
    print(f"          expected: {expected!r}")
    print(f"          actual:   {actual!r}")
    return False


def fail(label, message):
    _RESULTS["failed"] += 1
    print(f"  FAIL  {label}")
    print(f"          {message}")


def at(sequence, index, default="<absent>"):
    """Index without raising.

    EVERY READ OF A DRIVEN RUN'S OUTPUT GOES THROUGH THIS. A bare `rows[0]`
    raises IndexError exactly when a defect stops a row being written -- which
    is precisely when this file owes a recorded failure and a summary, not one
    traceback with every check below it unrun. That shape has shipped in this
    repository ten times; it does not ship here.
    """
    try:
        return sequence[index]
    except (IndexError, KeyError, TypeError):
        return default


def _function_named(tree, name):
    """The top-level (or nested) FunctionDef called `name`, or None.

    DEFINED WITH THE HARNESS RATHER THAN BESIDE ITS FIRST USE, because two
    controls in two sections now plant into run_batch by AST and a helper that
    lives inside one of them is a NameError the first time the other runs
    before it.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


def drive_call(fn, *args, **kwargs):
    """Call into production code, converting a raise into a comparable value.

    `check()` evaluates its arguments before it runs, so a planted defect that
    makes a call raise would escape through the argument list and abort the
    file. The marker is a tuple so it can never equal an expected string.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                                # noqa: BLE001
        return ("<raised>", type(exc).__name__, str(exc))


#------------------------------------------------------------------------------


# ===========================================================================
# 1. THE MECHANISM, DRIVEN DIRECTLY
# ===========================================================================
#
# `paths._RESOLVED` IS THE SEAM, and it is the one this repository already uses
# (tests/test_ablation_db_isolation.py, tests/test_dashboard_run_health.py).
# `stop_switch_path()` reads `paths.checkpoint_path`, so seeding that key points
# the whole mechanism at a scratch directory with no environment variable and no
# monkeypatch of the function under test.
#
# IT IS RESTORED IN A `finally`, and the restore is ASSERTED, because a leaked
# _RESOLVED entry would silently repoint every later reader in this process.

print("\n=== 1. the mechanism ===")

_CP_DIR = os.path.join(_TMP, "cp")
os.makedirs(_CP_DIR, exist_ok=True)

_SAVED_CP = _paths._RESOLVED.get("checkpoint_path", "<unset>")
_paths._RESOLVED["checkpoint_path"] = _CP_DIR + os.sep

try:
    _stop_path = _runner.stop_switch_path()

    check("1a  stop_switch_path() resolves inside the checkpoint directory and "
          "is named STOP",
          (os.path.dirname(str(_stop_path)), os.path.basename(str(_stop_path))),
          (_CP_DIR, "STOP"))
    check("1a-b ...and it is the filename constant rather than a literal, so "
          "the entry point's help, the banner and the refusal cannot name a "
          "different file from the one the poll reads",
          os.path.basename(str(_stop_path)), _runner.STOP_FILENAME)

    # --- ONE OWNER, PINNED BY AST ------------------------------------------
    #
    # THE CHECK THAT ACTUALLY MATTERS. An operator creates this file by hand,
    # so every message naming it -- the banner, the refusal, the stop report,
    # the interrupt message, the entry point's two -- has to name the SAME
    # path. A second expression of it is an operator writing a file the runner
    # never reads, which looks exactly like a switch that does not work.
    _runner_tree = ast.parse(_RUNNER_SRC)
    _entry_tree = ast.parse(_ENTRY_SRC)

    def _string_constants(tree, needle):
        """Non-docstring string constants containing `needle`."""
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
                first = at(node.body, 0, None)
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add(id(first.value))
        return [n.value for n in ast.walk(tree)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and needle in n.value and id(n) not in docstrings]

    # The bare name "STOP" appears in prose everywhere; what must be unique is
    # the VALUE of the filename constant. It is asserted to be assigned exactly
    # once and to be the only bare "STOP" string literal in the module.
    _stop_literals = [n for n in ast.walk(_runner_tree)
                      if isinstance(n, ast.Constant) and n.value == "STOP"]
    check("1b  the sentinel filename is a bare literal exactly once in the "
          "runner -- STOP_FILENAME's own assignment",
          len(_stop_literals), 1)
    check("1b-b ...and no other module in the package spells it: the path is "
          "reached through stop_switch_path(), never rebuilt",
          sorted(
              os.path.relpath(os.path.join(root, name), _REPO)
              for root, _dirs, files in os.walk(os.path.join(_REPO, "oncotriage"))
              for name in files
              if name.endswith(".py")
              and os.path.join(root, name) != _RUNNER_PATH
              and any(c.value == "STOP"
                      for c in ast.walk(ast.parse(
                          open(os.path.join(root, name), encoding="utf-8").read()))
                      if isinstance(c, ast.Constant))),
          [])

    # --- LATCHING ----------------------------------------------------------
    _runner.STOP_SWITCH.reset()
    check("1c  with no sentinel, poll() is False and nothing is recorded",
          (drive_call(_runner.STOP_SWITCH.poll, where="probe"),
           _runner.STOP_SWITCH.requested,
           _runner.STOP_SWITCH.message,
           _runner.STOP_SWITCH.detected_in),
          (False, False, None, None))

    open(_stop_path, "w", encoding="utf-8").close()
    check("1d  an EMPTY sentinel trips the switch -- `touch STOP` is the "
          "documented gesture, so an empty file must be fully valid and must "
          "not be read as 'no stop'",
          (drive_call(_runner.STOP_SWITCH.poll, where="main pass"),
           _runner.STOP_SWITCH.requested,
           _runner.STOP_SWITCH.message,
           _runner.STOP_SWITCH.detected_in),
          (True, True, None, "main pass"))

    os.unlink(_stop_path)
    check("1e  IT LATCHES: deleting the sentinel does not un-stop the run. The "
          "answer has already been acted on by cancelling work, which is not "
          "reversible, and deleting the file is exactly what an operator does "
          "to make the NEXT run start",
          (os.path.exists(_stop_path),
           drive_call(_runner.STOP_SWITCH.poll, where="later"),
           _runner.STOP_SWITCH.detected_in),
          (False, True, "main pass"))

    # --- THE NOTE ----------------------------------------------------------
    _runner.STOP_SWITCH.reset()
    check("1f  reset() forgets a stop, which is what stops a second main() in "
          "one process inheriting the first run's",
          (_runner.STOP_SWITCH.requested, _runner.STOP_SWITCH.message,
           _runner.STOP_SWITCH.path),
          (False, None, None))

    with open(_stop_path, "w", encoding="utf-8") as _fh:
        _fh.write("  pausing for the index rebuild\n")
    check("1g  a note is read, stripped, and recorded",
          (drive_call(_runner.STOP_SWITCH.poll, where="main pass"),
           _runner.STOP_SWITCH.message),
          (True, "pausing for the index rebuild"))

    _runner.STOP_SWITCH.reset()
    with open(_stop_path, "w", encoding="utf-8") as _fh:
        _fh.write("x" * (_runner.STOP_MESSAGE_MAX_CHARS + 500))
    _long = drive_call(_runner.STOP_SWITCH.poll, where="main pass")
    check("1h  an over-long note is CAPPED and says so in the same string, "
          "rather than an unbounded operator-written value reaching a "
          "structured log field",
          (_long,
           len(_runner.STOP_SWITCH.message or "")
           > _runner.STOP_MESSAGE_MAX_CHARS,
           str(_runner.STOP_SWITCH.message or "").endswith(
               f"... [truncated at {_runner.STOP_MESSAGE_MAX_CHARS} characters]"),
           str(_runner.STOP_SWITCH.message or "").startswith("x" * 50)),
          (True, True, True, True))

    # THE READ IS BOUNDED, NOT READ-THEN-TRUNCATED. `Path.read_text()` was the
    # obvious form and it pulls the WHOLE file in before any cap applies -- so
    # an operator who redirected a log into this file would have the shutdown
    # path allocate the lot. Measured by asking for a note far larger than the
    # cap and requiring the process not to have carried it.
    _runner.STOP_SWITCH.reset()
    with open(_stop_path, "w", encoding="utf-8") as _fh:
        _fh.write("y" * 5_000_000)
    _huge = drive_call(_runner.STOP_SWITCH.poll, where="main pass")
    check("1h-b a note far larger than the cap is still capped, and the read "
          "itself is bounded rather than read-then-truncated",
          (_huge, len(_runner.STOP_SWITCH.message or ""),
           os.path.getsize(_stop_path) > 1_000_000),
          (True,
           _runner.STOP_MESSAGE_MAX_CHARS
           + len(f"... [truncated at {_runner.STOP_MESSAGE_MAX_CHARS} "
                 f"characters]"),
           True))

    _runner.STOP_SWITCH.reset()
    with open(_stop_path, "w", encoding="utf-8") as _fh:
        _fh.write("   \n\t  \n")
    check("1h-c a whitespace-only note reads as NO NOTE rather than as a "
          "blank one, and still stops the run",
          (drive_call(_runner.STOP_SWITCH.poll, where="main pass"),
           _runner.STOP_SWITCH.message),
          (True, None))

    # --- THE TWO FAULT DIRECTIONS ------------------------------------------
    #
    # These are the two places the switch could be wrong, and they must be
    # wrong in OPPOSITE directions:
    #
    #   an unreadable NOTE   -> still stop. The sentinel is there; refusing to
    #                           stop because a note would not decode is the
    #                           worst available outcome.
    #   a failing POLL       -> do NOT stop. `Path.exists()` already answers
    #                           False for every ordinary "not there", so a
    #                           RAISE is something else -- a mount gone -- and
    #                           reading it as a stop would cancel a paid
    #                           campaign because a filesystem hiccuped.
    _runner.STOP_SWITCH.reset()
    _runner.STOP_SWITCH_FAULTS.clear()
    shutil.rmtree(_stop_path, ignore_errors=True)
    os.path.exists(_stop_path) and os.unlink(_stop_path)
    # A DIRECTORY named STOP: it exists, so the switch trips; read_text on it
    # raises IsADirectoryError, so the note is lost. One real failing condition
    # rather than a patched function.
    os.makedirs(_stop_path, exist_ok=True)
    check("1i  an UNREADABLE sentinel still stops the run, and only the note "
          "is lost -- counted under `message:`, which is the key that says "
          "'the run stopped and lost the note' rather than 'the run may have "
          "missed a stop'",
          (drive_call(_runner.STOP_SWITCH.poll, where="main pass"),
           _runner.STOP_SWITCH.message,
           [k for k in _runner.STOP_SWITCH_FAULTS if k.startswith("message:")]
           != []),
          (True, None, True))
    os.rmdir(_stop_path)

    _runner.STOP_SWITCH.reset()
    _runner.STOP_SWITCH_FAULTS.clear()
    _boom = _runner.stop_switch_path

    class _Exploding:
        def exists(self):
            raise OSError("stand-in: the mount went away")

    _runner.stop_switch_path = lambda: _Exploding()
    try:
        check("1j  a poll that RAISES does NOT invent a stop -- it is counted "
              "under `poll:` and the run continues",
              (drive_call(_runner.STOP_SWITCH.poll, where="main pass"),
               _runner.STOP_SWITCH.requested,
               [k for k in _runner.STOP_SWITCH_FAULTS if k.startswith("poll:")]),
              (False, False, ["poll:OSError"]))
        check("1j-b ...and the same direction is taken by the start-of-run "
              "check, which counts under its own `preflight:` key and does not "
              "refuse",
              (drive_call(_runner.assert_no_stale_stop_switch),
               [k for k in _runner.STOP_SWITCH_FAULTS
                if k.startswith("preflight:")]),
              (None, ["preflight:OSError"]))
    finally:
        _runner.stop_switch_path = _boom
    check("1j-c the stand-in was restored BY IDENTITY, so nothing below is "
          "measuring a patched function",
          _runner.stop_switch_path is _boom, True)

    # --- THE COUNTER IS ON THE RUN-END REPORT ------------------------------
    check("1k  STOP_SWITCH_FAULTS is in the degradation registry, so a switch "
          "that could not be read reaches an operator instead of dying with "
          "the process",
          "STOP_SWITCH_FAULTS" in _degradation._REGISTRY, True)
    check("1k-b ...and it is in the DEGRADATION registry rather than the "
          "census one: a run that may have missed a stop, or lost the note it "
          "was given, is a degradation and not an observation",
          "STOP_SWITCH_FAULTS" in getattr(_degradation, "_CENSUS_SPEC", {}),
          False)
    _runner.STOP_SWITCH_FAULTS.clear()

    # --- THE START-OF-RUN REFUSAL ------------------------------------------
    _runner.STOP_SWITCH.reset()
    check("1l  with no sentinel the start check passes silently",
          drive_call(_runner.assert_no_stale_stop_switch), None)

    with open(_stop_path, "w", encoding="utf-8") as _fh:
        _fh.write("left over from yesterday")
    _refusal = drive_call(_runner.assert_no_stale_stop_switch)
    check("1m  a STALE sentinel is REFUSED -- without this the switch is a "
          "trap that fires once and then silently every time, so a cron entry "
          "or a restart loop would honour a stop nobody asked for that day and "
          "report success each run",
          at(_refusal, 1), "StaleStopSwitch")
    _refusal_text = str(at(_refusal, 2, ""))
    check("1m-b ...and the message names the exact path, the note, and BOTH "
          "remediations -- a refusal an operator cannot act on is a hang with "
          "extra steps",
          (str(_stop_path) in _refusal_text,
           "left over from yesterday" in _refusal_text,
           "rm " in _refusal_text,
           "--clear-stop" in _refusal_text,
           "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED" in _refusal_text),
          (True, True, True, True, True))
    check("1m-c ...and StaleStopSwitch is a RuntimeError rather than an "
          "OSError or a ValueError, so a stray `except OSError` around a path "
          "check cannot eat a refusal",
          (issubclass(_runner.StaleStopSwitch, RuntimeError),
           issubclass(_runner.StaleStopSwitch, OSError),
           issubclass(_runner.StaleStopSwitch, ValueError)),
          (True, False, False))

    # THE SHUTDOWN-PATH RENDERER. Two callers -- run_batch's interrupt message
    # and the entry point's -- run where a path that cannot resolve would raise
    # INSIDE the handler explaining the interrupt, turning a clean exit into a
    # traceback about globbing. Found by reading the handler after it was
    # written, and it is the one place a helpful message must not be able to
    # fire from.
    check("1m-d describe_stop_switch_path() returns the real path when it "
          "resolves",
          _runner.describe_stop_switch_path(), str(_stop_path))
    _saved_owner = _runner.stop_switch_path
    _runner.stop_switch_path = lambda: (_ for _ in ()).throw(
        RuntimeError("stand-in: no data tree here"))
    try:
        _described = drive_call(_runner.describe_stop_switch_path)
    finally:
        _runner.stop_switch_path = _saved_owner
    check("1m-e ...and a DESCRIPTION rather than a raise when it cannot, so an "
          "interrupt arriving before any path had resolved still exits "
          "cleanly",
          (isinstance(_described, str),
           _runner.STOP_FILENAME in str(_described),
           "RuntimeError" in str(_described)),
          (True, True, True))
    check("1m-f ...and the owner was restored by identity",
          _runner.stop_switch_path is _saved_owner, True)

    check("1n  clear_stop_switch() removes it and reports that there was one",
          (drive_call(_runner.clear_stop_switch), os.path.exists(_stop_path)),
          (True, False))
    check("1n-b ...and reports False when there was nothing to clear, which is "
          "what lets the entry point say so rather than implying it deleted "
          "something",
          drive_call(_runner.clear_stop_switch), False)
    check("1n-c ...and it does NOT touch the checkpoint or the results file: "
          "clearing a control file and discarding a run's resume state are "
          "opposite operations and must not be one flag apart",
          sorted(os.listdir(_CP_DIR)), [])

finally:
    if _SAVED_CP == "<unset>":
        _paths._RESOLVED.pop("checkpoint_path", None)
    else:
        _paths._RESOLVED["checkpoint_path"] = _SAVED_CP
    _runner.STOP_SWITCH.reset()

check("1o  paths._RESOLVED was restored, so nothing below is reading a "
      "repointed checkpoint directory",
      _paths._RESOLVED.get("checkpoint_path", "<unset>"), _SAVED_CP)


#------------------------------------------------------------------------------


# ===========================================================================
# 1B. THE THREE GUARDS NO END-TO-END SCENARIO CAN REACH
# ===========================================================================
#
# THESE THREE WERE ADDED BECAUSE A REVERT HARNESS REPORTED THEM MISSED, and
# that is recorded rather than quietly fixed: deleting the resample entry gate
# and deleting the submit-loop guard both left the whole driven matrix below
# GREEN. Neither was a weak check -- each guard is simply unreachable from a
# scenario that creates the sentinel while the pool is saturated:
#
#   * run_resample's ENTRY GATE runs before its first patient, which in every
#     driven scenario is BEFORE the sentinel exists. Its real subjects are a
#     stop asked for BETWEEN the two passes and a caller reaching this public
#     function directly -- neither of which a subprocess scenario produces.
#   * the SUBMIT-LOOP guard needs the sentinel to exist WHILE submission is
#     running, and submission of a corpus takes microseconds. Making it exist
#     that early means it exists at start, which the stale-sentinel refusal
#     stops before main() ever gets to a pool.
#   * the IN-WORKER guard is the backstop for the one-patient window between
#     the latch and the submit loop's next poll, which is smaller still.
#
# So all three are driven directly, in-process, with the switch already
# latched. A guard whose only test is a scenario that cannot reach it is a
# guard with no test.

print("\n=== 1B. the guards a scenario cannot reach ===")

_G_DIR = os.path.join(_TMP, "guards")
os.makedirs(_G_DIR, exist_ok=True)
_SAVED_CP2 = _paths._RESOLVED.get("checkpoint_path", "<unset>")
_paths._RESOLVED["checkpoint_path"] = _G_DIR + os.sep
_REAL_PATIENT = _runner.process_patient
_G_CALLS = []


def _recording_patient(**kwargs):
    _G_CALLS.append(kwargs.get("fhir_path"))
    return {"patient_id": "p", "status": "success", "eligible_matches": 0,
            "near_misses": 0, "not_evaluable": 0, "total_time": 0.0,
            "timestamp": "2026-08-24T00:00:00",
            "is_resample": kwargs.get("is_resample", False)}


_runner.process_patient = _recording_patient
try:
    # --- the in-worker guard ------------------------------------------------
    _runner.STOP_SWITCH.reset()
    _G_CALLS.clear()
    _ok = drive_call(_runner._start_patient_unless_stopped, fhir_path="a.json",
                     graph=None, is_resample=False, run_id=None, db_path=None)
    check("1p  with no stop, the submitted callable runs the patient -- the "
          "non-degeneracy half, without which the check below passes for a "
          "wrapper that never calls anything",
          (at(_ok, "status", "<no status>"), _G_CALLS), ("success", ["a.json"]))

    with open(_runner.stop_switch_path(), "w", encoding="utf-8") as _fh:
        _fh.write("")
    _runner.STOP_SWITCH.poll(where="probe")
    _G_CALLS.clear()
    _blocked = drive_call(_runner._start_patient_unless_stopped,
                          fhir_path="b.json", graph=None, is_resample=False,
                          run_id=None, db_path=None)
    check("1q  ONCE THE SWITCH HAS TRIPPED THE CALLABLE REFUSES TO START, and "
          "it refuses by raising CancelledError -- which is what already means "
          "'never attempted' at both consumers, rather than returning an entry "
          "that would be appended to the results and counted as a patient",
          (at(_blocked, 1), _G_CALLS), ("CancelledError", []))

    # --- run_batch's submit-loop guard -------------------------------------
    #
    # The switch is ALREADY latched, so the loop breaks at index 0: nothing is
    # submitted, nothing is started, and the count of what was skipped is
    # reported rather than left as a bar that stops short.
    # THE "NEVER SUBMITTED" REPORT IS WHAT DISCRIMINATES HERE, and that is the
    # same correction 1s needed, for the same reason: the IN-WORKER guard
    # (1p/1q) refuses every one of these patients too, so a check that only
    # counted calls passed with the submit-loop guard deleted -- measured by a
    # revert harness, not reasoned about.
    #
    # WHAT THE SUBMIT GUARD UNIQUELY BUYS, since the in-worker guard already
    # stops the spending: it does not CREATE 21,988 futures, queue them, hand
    # each to a worker and take a raise back; the progress bar is resized to
    # what will actually be accounted for rather than stopping short and
    # reading as a hang; and the operator is TOLD how many never entered the
    # pool, which is a different number from how many were cancelled inside it.
    import contextlib
    import io

    _G_CALLS.clear()
    _files = [os.path.join(_G_DIR, f"p{i}.json") for i in range(5)]
    _submit_out = io.StringIO()
    with contextlib.redirect_stderr(_submit_out):
        _out = drive_call(_runner.run_batch, fhir_files=_files,
                          bm25_index=None, nct_ids=[], graph=None,
                          completed_ids=set(), results_list=[])
    _submit_text = _submit_out.getvalue()
    check("1r  THE SUBMIT LOOP HONOURS THE SWITCH: with a stop already in "
          "effect, not one patient is submitted and not one is started",
          (_G_CALLS, isinstance(_out, tuple) and len(_out) == 2), ([], True))
    check("1r-b ...and the patients are reported as NEVER SUBMITTED rather "
          "than as cancelled inside the pool -- the two are different numbers "
          "and only the submit guard can produce the first",
          (f"[STOP] {len(_files)} patients were never submitted."
           in _submit_text,
           f"{len(_files)} never submitted" in _submit_text),
          (True, True))
    check("1r-c ...and run_batch reports the pass as STOPPED rather than "
          "COMPLETE",
          (isinstance(_out, tuple) and _out[1] is False,
           "MAIN BATCH STOPPED:" in _submit_text,
           "MAIN BATCH COMPLETE:" in _submit_text),
          (True, True, False))
    check("1r-d ...(non-degeneracy: the redirect captured this run's console "
          "channel at all)",
          len(_submit_text) > 0, True)

    # --- run_resample's entry gate ------------------------------------------
    #
    # THE GATE AND THE 'no candidates' EXIT ARE DIFFERENT EXITS and the control
    # is what separates them: with the switch reset and an empty cohort the
    # function leaves by the second, so the first is not being credited with
    # work the second did.
    # THE MESSAGE IS WHAT DISCRIMINATES, NOT THE CALL COUNT, and that is a
    # correction the revert harness forced. The first version of this check
    # asserted only that no patient was called -- and deleting the entry gate
    # outright left it PASSING, because the SUBMIT-LOOP guard four lines lower
    # catches the same stop and submits nothing either. The two guards overlap
    # on "no call is made" and differ on everything else: without the entry
    # gate the pass still resolves its cohort, builds a progress bar, opens a
    # thread pool and prints "Resampling N patients" before stopping, and the
    # operator is told nothing about why. `console.out` reads `sys.stderr` at
    # call time precisely so a redirect can see it.
    #
    # A CHECK THAT PASSES BECAUSE A DIFFERENT GUARD CAUGHT THE SAME THING IS
    # NOT A CHECK ON THE GUARD IT NAMES.
    _G_CALLS.clear()
    _gate_out = io.StringIO()
    with contextlib.redirect_stderr(_gate_out):
        _gate = drive_call(_runner.run_resample, fhir_files=_files,
                           completed_ids={os.path.basename(f).split(".")[0]
                                          for f in _files},
                           bm25_index=None, nct_ids=[], graph=None,
                           results_list=[])
    _gate_text = _gate_out.getvalue()
    check("1s  RUN_RESAMPLE HONOURS THE SWITCH ON ITS OWN, before its first "
          "call AND before it resolves a cohort, builds a bar or opens a pool. "
          "main() already skips the pass, so this gate's subjects are a stop "
          "asked for BETWEEN the two passes and a caller reaching this public "
          "function directly",
          (_gate, _G_CALLS,
           "Resample pass SKIPPED: an operator stop is in effect."
           in _gate_text,
           "Resampling " in _gate_text),
          (None, [], True, False))

    _runner.STOP_SWITCH.reset()
    os.unlink(_runner.stop_switch_path())
    _G_CALLS.clear()
    _cohort_out = io.StringIO()
    with contextlib.redirect_stderr(_cohort_out):
        _no_cohort = drive_call(_runner.run_resample, fhir_files=[],
                                completed_ids=set(), bm25_index=None,
                                nct_ids=[], graph=None, results_list=[])
    _cohort_text = _cohort_out.getvalue()
    check("1s-b CONTROL: with no stop, an empty cohort leaves by the OTHER "
          "exit and says so in different words -- so 1s is about the gate "
          "rather than about a function that returns early for any reason",
          (_no_cohort, _G_CALLS,
           "No completed patients available for resampling." in _cohort_text,
           "an operator stop is in effect" in _cohort_text),
          (None, [], True, False))
    check("1s-c ...and the redirect really captured this module's console "
          "channel (non-degeneracy: without this, both checks above would "
          "compare two empty strings and pass for a capture that saw nothing)",
          (len(_gate_text) > 0, len(_cohort_text) > 0), (True, True))
finally:
    _runner.process_patient = _REAL_PATIENT
    _runner.STOP_SWITCH.reset()
    if _SAVED_CP2 == "<unset>":
        _paths._RESOLVED.pop("checkpoint_path", None)
    else:
        _paths._RESOLVED["checkpoint_path"] = _SAVED_CP2

check("1t  the real process_patient was restored BY IDENTITY, and "
      "paths._RESOLVED with it",
      (_runner.process_patient is _REAL_PATIENT,
       _paths._RESOLVED.get("checkpoint_path", "<unset>")),
      (True, _SAVED_CP2))


#------------------------------------------------------------------------------


# ===========================================================================
# THE DRIVEN HARNESS -- A REAL SUBPROCESS RUNNING THE REAL ENTRY POINT
# ===========================================================================
#
# THE STAND-INS ARRIVE THROUGH `usercustomize`, NOT THROUGH runpy OR exec, for
# the reason tests/test_runner_sigterm_shutdown.py records: section 1c of
# tests/test_package_invariants.py forbids loading a module by location,
# unconditionally and with no allowlist escape, and it CAUGHT the first version
# of that file doing exactly that inside a string literal. A `__main__` guard
# only runs when the file is executed as `__main__`, and the stand-ins must
# already be installed by then -- so the setup has to happen at INTERPRETER
# STARTUP, which is what `usercustomize` is for.
#
# WHAT IS A STAND-IN HERE, AND THE ONE THAT DIFFERS FROM THAT FILE'S:
#
#   build_bm25_index_from_qdrant  needs a live Qdrant
#   build_matching_graph          compiles LangGraph and pulls in the agent
#   tracking                      would open a real MLflow store
#   process_patient               is ONE LIVE BILLED Stage 5 CALL per patient
#   run_fingerprint.current       *** THE ONE THAT IS NEW ***
#
# run_fingerprint.current IS STUBBED BECAUSE THE PATIENTS SUCCEED. The sigterm
# file's stand-in returns status="error" specifically so `_on_done` never
# reaches save_checkpoint, which with no fingerprint argument would resolve the
# stamp over the wire. This file NEEDS successful patients -- without them
# nothing is ever completed, the resample pass has no candidates and scenarios C
# and D's whole subject is unreachable -- so the stamp is a literal instead and
# save_checkpoint / load_checkpoint stay REAL, which is what scenarios B and D's
# resume halves are about.
#
# NO BILLED CALL IS REACHABLE EVEN IF THE HOOK NEVER RUNS: every subprocess is
# handed ONCOTRIAGE_QDRANT_URL pointed at a closed port, so an unstubbed
# build_bm25_index_from_qdrant fails and main() exits before Stage 5 exists.
#
# THE PARKING IS PHASE-KEYED, and that is what makes three different scenarios
# out of one harness. A worker parks only in the phase named by ONC_PARK_PHASE,
# so the main pass can be driven to completion before a stop is asked for in the
# RESAMPLE pass. Parking rather than sleeping is the sigterm file's measured
# lesson: a queued patient can only start once a running one returns, so while
# every worker is parked NOTHING can advance and the started count is a
# statement about cancellation rather than about scheduling luck.

_HOOK = r"""
import os, sys, threading, time

from oncotriage.batch import runner as R
from oncotriage import paths as P
from oncotriage import run_fingerprint as F

assert os.path.realpath(R.__file__).startswith(
    os.path.realpath(os.environ["ONC_REPO"])), (
    "PREFLIGHT: the runner that imported is not the one this run targets: "
    + os.path.realpath(R.__file__))

R.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
R.build_matching_graph = lambda *a, **k: object()


class _Tracking:
    def start_run(self, **kw): pass
    def log_run_metrics(self, *a, **kw): pass
    def end_run(self, **kw): print("[stand-in] tracking.end_run", kw.get("status"))


R.tracking = _Tracking()

# A LITERAL STAMP, BUILT FROM THE MODULE'S OWN FIELD TUPLE so a newly gated
# field cannot make this hook hand back a short stamp that compare() reads as
# FP_UNRESOLVED -- which would refuse every resume for a reason with nothing to
# do with what is being tested.
_STAMP = {"fingerprint_version": F.FINGERPRINT_VERSION}
for _field in F.FINGERPRINT_FIELDS:
    _STAMP[_field] = "stand-in"
F.current = lambda refresh=False: dict(_STAMP)
F.clear_cache = lambda: None

P._RESOLVED["data_fhir_path"] = os.environ["ONC_CORPUS"] + os.sep
P._RESOLVED["inferences_path"] = os.environ["ONC_DB"]
P._RESOLVED["checkpoint_path"] = os.environ["ONC_CP"] + os.sep

_STARTED = os.environ["ONC_STARTED"]
_READY = os.environ["ONC_READY"]
_RELEASE = os.environ["ONC_RELEASE"]
_PARK = os.environ["ONC_PARK_PHASE"]
_CAP = float(os.environ["ONC_CAP"])
_lock = threading.Lock()


def _patient(fhir_path=None, graph=None, is_resample=False, run_id=None,
             db_path=None):
    '''Record that this patient STARTED (with its phase), then park if asked.

    THE LEDGER IS THE COST PROOF. One line per patient this stand-in is CALLED
    for -- which in production is one live billed Stage 5 call -- tagged with
    the phase, so "zero calls fired after the stop point" is a number read out
    of a file rather than a claim about a code path.

    The cap is a deadlock guard, not a timing knob: if the test dies without
    releasing, these threads exit rather than hanging the run forever.
    '''
    phase = "resample" if is_resample else "main"
    name = os.path.basename(str(fhir_path))
    with _lock:
        with open(_STARTED, "a") as fh:
            fh.write(phase + "\t" + name + "\n")
        n = sum(1 for line in open(_STARTED) if line.startswith(_PARK + "\t"))
    if _PARK != "none" and phase == _PARK:
        if n == 1:
            with open(_READY, "w") as fh:
                fh.write("go")
        _deadline = time.time() + _CAP
        while not os.path.exists(_RELEASE) and time.time() < _deadline:
            time.sleep(0.01)

    # THE REAL log_inference AND THE REAL LEDGER, AND THAT IS NOT DECORATION.
    # The reconciliation is the shipped exit code: with no write attempted at
    # all it reports INCOMPLETE -- correctly, "if patients were processed,
    # log_inference was never reached" -- and the process exits 1. So a
    # stand-in that skipped the write would make every stopped run in this file
    # exit 1 for a reason with nothing to do with stopping, and the claim that
    # a stop exits 0 could not be measured here at all. The row is the smallest
    # one the shipped writer accepts; the LEDGER ENTRY is what reconcile_writes
    # actually verifies by id.
    _row = {"patient_id": name, "status": "success", "eligible_matches": [],
            "near_misses": [], "not_evaluable_trials": 0,
            "timestamp": "2026-08-24T00:00:00"}
    R.record_write(name, R.log_inference(_row, {"patient_id": name},
                                         db_path=db_path), is_resample)

    return {"patient_id": name, "status": "success", "eligible_matches": 1,
            "near_misses": 0, "not_evaluable": 0, "total_time": 0.01,
            "timestamp": "2026-08-24T00:00:00", "is_resample": is_resample}


R.process_patient = _patient

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
      "bill anything -- see ONCOTRIAGE_QDRANT_URL -- but it would prove "
      "nothing)",
      _site.ENABLE_USER_SITE, True)


def make_corpus(root, count):
    os.makedirs(root, exist_ok=True)
    for index in range(count):
        with open(os.path.join(root, f"patient{index:03d}.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"resourceType": "Bundle", "entry": []}, handle)
    return root


_MARK_STOP = "[STOP] Stop requested by"
_MARK_SIGINT = "[INTERRUPTED] Waiting for active threads to finish"


def drive(root, *, park="none", action=None, args=(), repo=None, patients=40,
          timeout=240, fresh_corpus=True):
    """Run the real entry point once and return everything worth asserting on.

    THE SEQUENCE, when `park` names a phase:

        1. every worker of that phase starts a patient and PARKS -- nothing can
           advance;
        2. wait until MAX_WORKERS have started in that phase, so the pool is
           provably saturated and the queue provably non-empty;
        3. perform `action` -- create the sentinel, or send SIGINT;
        4. for a SIGNAL, wait until the handler's marker appears in the
           process's own output, proving the interrupt was RAISED and not
           merely sent. FOR THE SENTINEL THERE IS NOTHING TO WAIT FOR AND THAT
           IS NOT A WEAKNESS: the switch is polled when a patient COMPLETES, so
           its marker cannot appear until step 5 -- what has to be true before
           the release is that the FILE EXISTS, which step 3 established;
        5. release the parked workers.

    `root` is reused deliberately across a scenario and its resume, so the
    second invocation reads the first's real checkpoint.
    """
    os.makedirs(root, exist_ok=True)
    corpus = os.path.join(root, "fhir")
    if fresh_corpus:
        make_corpus(corpus, patients)
    db = os.path.join(root, "inferences.db")
    cp = os.path.join(root, "cp")
    os.makedirs(cp, exist_ok=True)
    started = os.path.join(root, f"started_{len(os.listdir(root))}.txt")
    ready = os.path.join(root, f"ready_{os.path.basename(started)}")
    release = os.path.join(root, f"release_{os.path.basename(started)}")
    log = os.path.join(root, f"console_{os.path.basename(started)}.log")
    hook_marker = os.path.join(root, f"hook_{os.path.basename(started)}")

    env = dict(os.environ)
    env.update({
        "ONC_REPO": repo or _REPO,
        "ONC_CORPUS": corpus,
        "ONC_DB": db,
        "ONC_CP": cp,
        "ONC_STARTED": started,
        "ONC_READY": ready,
        "ONC_RELEASE": release,
        "ONC_PARK_PHASE": park,
        "ONC_CAP": "150",
        "ONC_HOOK_MARKER": hook_marker,
        "ONCOTRIAGE_DEFER_LOCAL_MODELS": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": os.pathsep.join([_HOOK_DIR, repo or _REPO]),
        # THE NO-SPEND BACKSTOP that does not depend on the hook working.
        "ONCOTRIAGE_QDRANT_URL": "http://127.0.0.1:1",
    })
    env.pop("PYTHONNOUSERSITE", None)

    def _count(phase):
        try:
            with open(started, encoding="utf-8") as handle:
                return sum(1 for line in handle
                           if line.startswith(phase + "\t"))
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

    saturated = None
    acted = False
    handler_entered = None
    stop_file = os.path.join(cp, _runner.STOP_FILENAME)

    with open(log, "w", encoding="utf-8") as _sink:
        proc = subprocess.Popen(
            [sys.executable, _ENTRY_PATH] + list(args),
            stdout=_sink, stderr=subprocess.STDOUT, text=True, env=env,
            cwd=_REPO)
        try:
            if park != "none" and action is not None:
                _wait(lambda: os.path.exists(ready), 120)
                want = min(MAX_WORKERS, patients)
                saturated = _wait(lambda: _count(park) >= want, 90)
                if proc.poll() is None:
                    if action == "stop":
                        with open(stop_file, "w", encoding="utf-8") as handle:
                            handle.write("stopping for the index rebuild")
                        acted = True
                    elif action == "sigint":
                        proc.send_signal(signal.SIGINT)
                        acted = True
                        handler_entered = _wait(
                            lambda: _MARK_SIGINT in _log_text(), 30)
            with open(release, "w", encoding="utf-8") as handle:
                handle.write("go")
            if action == "stop":
                handler_entered = _wait(
                    lambda: _MARK_STOP in _log_text(), 60)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:                      # pragma: no cover
            proc.kill()
            proc.wait()
        finally:
            if proc.poll() is None:                            # pragma: no cover
                proc.kill()
                proc.wait()

    started_lines = []
    if os.path.exists(started):
        with open(started, encoding="utf-8") as handle:
            started_lines = [line.rstrip("\n") for line in handle
                             if line.strip()]

    runs = []
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        try:
            runs = conn.execute("SELECT id, status, finished_at, resumed "
                                "FROM runs ORDER BY id").fetchall()
        except sqlite3.Error as exc:                            # noqa: BLE001
            runs = [("<sqlite error>", str(exc), None, None)]
        finally:
            conn.close()

    checkpoint = None
    cp_file = os.path.join(cp, "batch_runner_checkpoint.json")
    if os.path.exists(cp_file):
        try:
            with open(cp_file, encoding="utf-8") as handle:
                checkpoint = json.load(handle)
        except (OSError, ValueError) as exc:                    # noqa: BLE001
            checkpoint = {"<unreadable>": str(exc)}

    return {
        "exit": proc.returncode,
        "out": _log_text(),
        "hook": os.path.exists(hook_marker),
        "saturated": saturated,
        "acted": acted,
        "handler_entered": handler_entered,
        "main": [l for l in started_lines if l.startswith("main\t")],
        "resample": [l for l in started_lines if l.startswith("resample\t")],
        "runs": runs,
        "checkpoint": checkpoint,
        "stop_file": stop_file,
        "db": db,
        "cp": cp,
        "patients": patients,
    }


#------------------------------------------------------------------------------


# ===========================================================================
# 2. SCENARIO A -- THE SENTINEL MID-BATCH
# ===========================================================================

print("\n=== 2. scenario A: STOP mid-batch ===")

_A_ROOT = os.path.join(_TMP, "A")
_A = drive(_A_ROOT, park="main", action="stop")
_A_MAIN = len(_A["main"])
print(f"        [info] A: {_A_MAIN} of {_A['patients']} main-pass patients "
      f"started, {len(_A['resample'])} resample")

check("2a-0 the stand-in hook installed (non-degeneracy: an unstubbed run "
      "proves nothing about cancellation, and this file's whole cost proof is "
      "the stand-in's ledger)",
      _A["hook"], True)
check("2a  the pool was saturated and the sentinel was written before any "
      "queued patient could start -- which is what makes the count below a "
      "statement about cancellation rather than about scheduling luck",
      (_A["saturated"], _A["acted"], _A["handler_entered"]),
      (True, True, True))
check("2b  the switch announced itself, naming the file and the operator's "
      "note",
      (_MARK_STOP in _A["out"],
       "stopping for the index rebuild" in _A["out"]),
      (True, True))
check("2c  EXACTLY THE IN-FLIGHT PATIENTS RAN. No queued patient was started, "
      "so none of them was billed",
      (_A_MAIN, _A_MAIN < _A["patients"]),
      (min(MAX_WORKERS, _A["patients"]), True))
# THE ANNOUNCEMENT AND THE SWEEP HAPPEN ONCE, UNDER MAX_WORKERS CALLBACKS
# ARRIVING AT THE SAME INSTANT. Every worker released together polls, and the
# switch is latched by the first -- so without the latch each of the twelve
# would print the three-line block, and without the sweep guard each would
# report the same cancellation count again. Read off a real concurrent run
# rather than argued.
check("2b-b the block is printed ONCE and the sweep reported ONCE, though "
      f"{min(MAX_WORKERS, _A['patients'])} done-callbacks polled the switch "
      "within the same instant",
      (_A["out"].count(_MARK_STOP),
       _A["out"].count("queued patients cancelled before they started")),
      (1, 1))
check("2b-c ...and the one sweep accounted for every patient that did not "
      "start, so the number an operator reads is the work actually cancelled",
      f"[STOP] {_A['patients'] - _A_MAIN} queued patients cancelled"
      in _A["out"], True)

check("2c-b ...and the cancelled ones are reported as cancelled rather than "
      "as errors, in the count and in the absence of per-patient error lines",
      (f", {_A['patients'] - _A_MAIN} cancelled (never attempted)" in _A["out"],
       _A["out"].count("[CALLBACK ERROR]")),
      (True, 0))
# THE HEADING IS READ, NOT THE PHRASE. `print_summary` prints its own
# "--- RESAMPLE PASS ---" section on every run (it says "No resample records."),
# so testing for the bare phrase reports a pass that never ran as one that did
# -- measured, and the first version of this check failed for exactly that. What
# run_resample prints, and nothing else does, is the banner line.
check("2d  THE RESAMPLE PASS WAS NEVER ENTERED -- zero resample calls, which "
      "at the shipped RESAMPLE_COUNT is up to "
      f"{min(RESAMPLE_COUNT, MAX_WORKERS)} live billed calls not made",
      (len(_A["resample"]),
       "Resampling " in _A["out"],
       "RESAMPLE COMPLETE:" in _A["out"],
       "[Resample] SKIPPED: an operator stop is in effect." in _A["out"]),
      (0, False, False, True))
check("2e  the run row is finalized STOPPED -- not KILLED (the process did not "
      "die) and not FINISHED (the cohort was not covered)",
      sorted({row[1] for row in _A["runs"]}) or ["<no run row>"], ["STOPPED"])
check("2e-b ...with a non-NULL finished_at, so it is a finalized row and not "
      "the RUNNING/NULL shape reserved for a process that ran no handler",
      [row[2] is not None for row in _A["runs"]] or ["<no run row>"], [True])
check("2f  THE CHECKPOINT WAS KEPT, and this is the most expensive thing in "
      "the item. A stopped run's cancelled patients produce NO result entry, "
      "so `main_errors` is EMPTY -- the old condition would have read 'no "
      "errors, clear it', deleted the resume state for a cohort deliberately "
      "half-run, and re-billed every remaining patient on the next invocation",
      (os.path.exists(os.path.join(_A["cp"],
                                   "batch_runner_checkpoint.json")),
       "[Checkpoint] KEPT: the run was stopped" in _A["out"],
       "[Checkpoint] Cleared" in _A["out"]),
      (True, True, False))
check("2f-b ...and it holds exactly the patients that completed",
      len((_A["checkpoint"] or {}).get("completed_stems", [])), _A_MAIN)
check("2g  the process exits 0. A stop is a clean end, so it falls through to "
      "the reconciliation verdict -- which is 0 because every row this run "
      "produced is in the database",
      _A["exit"], 0)
check("2g-b ...with no traceback: an operator-requested stop is not a crash "
      "report",
      "Traceback (most recent call last)" in _A["out"], False)
check("2h  BOTH console report blocks printed -- the normal summary path is "
      "taken, so the census and degradation blocks are the run's own rather "
      "than a crash record's",
      (_A["out"].count("CENSUS") >= 1, _A["out"].count("DEGRADATION") >= 1,
       "BATCH RUN SUMMARY" in _A["out"].upper()),
      (True, True, True))
check("2h-b ...and the closing block tells the operator how to resume, naming "
      "the sentinel it must delete",
      ("RUN STOPPED AT THE OPERATOR'S REQUEST." in _A["out"],
       f"rm {_A['stop_file']}" in _A["out"]),
      (True, True))
check("2i  THE SENTINEL WAS NOT DELETED by the run that honoured it. A "
      "self-clearing switch would let a restart loop honour a stop nobody "
      "asked for and report success every time -- which is what the "
      "start-of-run refusal exists to prevent, and it needs the file to still "
      "be there",
      os.path.exists(_A["stop_file"]), True)
check("2j  the tracking index records KILLED -- MLflow's own 'run killed by "
      "user', the closest TRUE statement its three-member vocabulary can "
      "carry, rather than FINISHED (false) or a bare 'STOPPED' that end_run "
      "would silently replace with FAILED",
      "[stand-in] tracking.end_run KILLED" in _A["out"], True)


#------------------------------------------------------------------------------


# ===========================================================================
# 3. SCENARIO E -- A STALE SENTINEL AT START IS REFUSED
# ===========================================================================
#
# Driven BEFORE scenario B on purpose: scenario A left the sentinel in place, so
# the very next invocation against that root is the real-world stale case rather
# than a fabricated one.

print("\n=== 3. scenario E: the sentinel is still there ===")

_E = drive(_A_ROOT, park="none", action=None, fresh_corpus=False)

check("3a-0 the stand-in hook installed",
      _E["hook"], True)
check("3a  THE RUN REFUSED TO START, naming the path",
      ("REFUSED (stop switch present)" in _E["out"],
       _E["stop_file"] in _E["out"]),
      (True, True))
check("3b  ...and it exits 1",
      _E["exit"], 1)
check("3c  NOTHING WAS BILLED: not one patient was started, in either pass",
      (len(_E["main"]), len(_E["resample"])), (0, 0))
check("3d  ...and no run row was opened, so a refusal leaves no campaign "
      "record to explain -- the refusal is above start_run_record for exactly "
      "this reason",
      len(_E["runs"]), len(_A["runs"]))
check("3e  ...and the refusal happened before the BM25 index was even built, "
      "which is what makes it the cheapest refusal available",
      "[Setup] BM25 index ready" in _E["out"], False)
check("3f  the message names both remediations",
      ("--clear-stop" in _E["out"], f"rm {_E['stop_file']}" in _E["out"]),
      (True, True))
check("3g  the checkpoint is untouched by the refusal",
      len((_E["checkpoint"] or {}).get("completed_stems", [])), _A_MAIN)


#------------------------------------------------------------------------------


# ===========================================================================
# 4. SCENARIO B -- RESUME AFTER A STOP
# ===========================================================================
#
# THE SAME ROOT AS A AND E, so the checkpoint this reads is the one scenario A
# actually wrote rather than a fabricated one -- which is the only way to say
# anything about a resume at all.

print("\n=== 4. scenario B: delete the sentinel and resume ===")

os.unlink(_A["stop_file"])
_B = drive(_A_ROOT, park="none", action=None, fresh_corpus=False)
_B_MAIN = len(_B["main"])
print(f"        [info] B: {_B_MAIN} main-pass patients started on the resume")

check("4a-0 the stand-in hook installed", _B["hook"], True)
check("4a  the run starts once the sentinel is gone",
      ("REFUSED (stop switch present)" in _B["out"],
       "[Setup] BM25 index ready" in _B["out"]),
      (False, True))
check("4b  IT RESUMED: the patients scenario A completed were skipped, and "
      "exactly the remainder ran. Nothing was re-billed and nothing was "
      "dropped",
      (_B_MAIN, _B_MAIN + _A_MAIN),
      (_A["patients"] - _A_MAIN, _A["patients"]))
check("4b-b ...and the runner said so, reading the checkpoint scenario A left",
      f"[Checkpoint] Resuming: {_A_MAIN} patients already completed."
      in _B["out"], True)
check("4c  the resumed run reaches its own end: the resample pass RUNS this "
      "time, which is what says the skip in scenario A was the switch and not "
      "an unrelated block",
      (len(_B["resample"]) > 0, "RESAMPLE COMPLETE:" in _B["out"]),
      (True, True))
check("4d  it is finalized FINISHED, and the STOPPED row from scenario A is "
      "still beside it -- two processes, two rows, which is what the campaign "
      "stitch in section 8 puts back together",
      [row[1] for row in _B["runs"]], ["STOPPED", "FINISHED"])
check("4d-b ...and the resumed row records `resumed = 1`, which is the column "
      "the stitch keys on",
      at(at(_B["runs"], 1, ()), 3), 1)
check("4e  the checkpoint is CLEARED now that the cohort is covered -- the "
      "stop guard is a guard on being stopped and not a permanent block on "
      "cleanup",
      ("[Checkpoint] Cleared for next fresh run." in _B["out"],
       os.path.exists(os.path.join(_B["cp"],
                                   "batch_runner_checkpoint.json"))),
      (True, False))
check("4f  exit 0", _B["exit"], 0)


#------------------------------------------------------------------------------


# ===========================================================================
# 5. SCENARIO C -- THE SENTINEL MID-RESAMPLE
# ===========================================================================
#
# THE RESAMPLE PASS HAS TO HONOUR THE SWITCH ON ITS OWN, and this is what says
# it does. main()'s check runs ONCE, immediately after run_batch returns; an
# operator who writes the file a second later would otherwise have their stop
# honoured only after the whole resample pass had been billed.

print("\n=== 5. scenario C: STOP mid-resample ===")

_C_ROOT = os.path.join(_TMP, "C")
_C = drive(_C_ROOT, park="resample", action="stop")
_C_RES = len(_C["resample"])
print(f"        [info] C: {len(_C['main'])} main, {_C_RES} resample started")

check("5a-0 the stand-in hook installed", _C["hook"], True)
check("5a  the main pass ran to completion unparked, so the pass under test "
      "is genuinely the resample one",
      (len(_C["main"]), "MAIN BATCH COMPLETE:" in _C["out"]),
      (_C["patients"], True))
check("5b  the pool was saturated in the RESAMPLE pass and the sentinel landed "
      "before any queued resample patient could start",
      (_C["saturated"], _C["acted"], _C["handler_entered"]),
      (True, True, True))
check("5c  EXACTLY THE IN-FLIGHT RESAMPLE PATIENTS RAN; the rest were "
      "cancelled before they were billed",
      (_C_RES, _C_RES < _C["patients"]),
      (min(MAX_WORKERS, _C["patients"]), True))
check("5c-b ...and the pass reports itself STOPPED rather than COMPLETE",
      ("RESAMPLE STOPPED:" in _C["out"], "RESAMPLE COMPLETE:" in _C["out"]),
      (True, False))
# ── WHAT A STOP THAT LANDED IN THE RESAMPLE PASS IS RECORDED AS ────────────
#
# THIS PAIR IS THE FIX AND IT REVERSES WHAT THIS FILE USED TO ASSERT. Until the
# pre-migration pass main() read `STOP_SWITCH.requested` directly, so ANY stop
# recorded the run STOPPED and KEPT the checkpoint -- including this one, where
# the main pass had already covered every patient in the cohort. Two things
# were then false in the artifact:
#
#   * `runs.status` said STOPPED, whose entire meaning is "this campaign covers
#     a PREFIX of the cohort, so no rate computed over it is a rate about the
#     cohort". This campaign covers all of it. `campaign_summary` and every
#     reader of that column acted on the wrong one.
#   * the checkpoint was KEPT "because patients remain", and none did -- so the
#     next invocation loaded a checkpoint listing the whole cohort, found
#     nothing pending, and printed a main pass of zero.
#
# The old comment here called keeping the checkpoint "the conservative
# direction, and the only one whose failure mode is cheap". The first half was
# true of the checkpoint alone; the second was not true of the STATUS, which is
# read by things that cannot see this file.
check("5d  the run row is FINISHED, NOT STOPPED. The stop cost the resample "
      "pass and nothing else, and STOPPED means the campaign covers a PREFIX "
      "of the cohort -- which this one does not",
      sorted({row[1] for row in _C["runs"]}) or ["<no run row>"], ["FINISHED"])
check("5d-b the tracking index agrees with the row. MLflow's vocabulary maps a "
      "stop to KILLED, so a run indexed KILLED here would say the campaign was "
      "cut short in the one place a reviewer looks first",
      ("[stand-in] tracking.end_run FINISHED" in _C["out"],
       "[stand-in] tracking.end_run KILLED" in _C["out"]),
      (True, False))
check("5e  exit 0, no traceback",
      (_C["exit"], "Traceback (most recent call last)" in _C["out"]),
      (0, False))
check("5f  the checkpoint is CLEARED, because the cohort really was covered. "
      "Keeping it left the next invocation loading a full checkpoint with "
      "nothing pending",
      ("[Checkpoint] Cleared for next fresh run." in _C["out"],
       os.path.exists(os.path.join(_C["cp"],
                                   "batch_runner_checkpoint.json"))),
      (True, False))
check("5f-b the stop is STILL ANNOUNCED, and the announcement says which of "
      "the two things it cut short. Silence would be the other failure: an "
      "operator wrote a file, the run obeyed it, and nothing said so",
      ("STOP REQUESTED AFTER THE COHORT WAS COVERED." in _C["out"],
       "RUN STOPPED AT THE OPERATOR'S REQUEST." in _C["out"],
       "the RESAMPLE pass, and nothing else" in _C["out"]),
      (True, False, True))
# THE NON-DEGENERACY PAIR, AND IT IS THE WHOLE POINT OF THE FIX. Scenario A
# stopped the MAIN pass, so its cohort really is a prefix -- and it must still
# be STOPPED. A change that simply stopped ever writing STOPPED would satisfy
# every check above and fail here.
check("5f-c ...and scenario A, whose stop DID cut the cohort short, is still "
      "recorded STOPPED. Without this the fix would be indistinguishable from "
      "deleting the status",
      (sorted({row[1] for row in _A["runs"]}) or ["<no run row>"],
       "RUN STOPPED AT THE OPERATOR'S REQUEST." in _A["out"]),
      (["STOPPED"], True))


#------------------------------------------------------------------------------


# ===========================================================================
# 5B. THE CONTROL FOR SCENARIO C -- THE SWITCH READ INSTEAD OF THE COHORT
# ===========================================================================
#
# 5d and 5f are claims that a status CHANGED, and a change means nothing
# without the other arm. The pre-fix form is reconstructed in a COPY of the
# package -- run_batch's returned `main_pass_complete` put back to
# `not STOP_SWITCH.requested`, and nothing else touched -- and scenario C is
# driven against it.
#
# THE PLANT IS STRUCTURAL, on section 7's precedent and for its reason: the
# final `return` of run_batch is located by AST and its LINES are replaced, so
# a return that has moved or changed shape is a named PLANT-FAILED rather than
# a control that quietly tests the shipped tree against itself.

print("\n=== 5b. the control: the switch read instead of the cohort ===")

_C5_REPO = os.path.join(_TMP, "pkgcopy_c5")
os.makedirs(_C5_REPO, exist_ok=True)
shutil.copytree(os.path.join(_REPO, "oncotriage"),
                os.path.join(_C5_REPO, "oncotriage"),
                ignore=shutil.ignore_patterns("__pycache__"))
_C5_RUNNER = os.path.join(_C5_REPO, "oncotriage", "batch", "runner.py")
_c5_src = open(_C5_RUNNER, encoding="utf-8").read()


def _final_return(fn):
    """The last top-level `return` statement of `fn`, or None."""
    if fn is None:
        return None
    tail = [s for s in fn.body if isinstance(s, ast.Return)]
    return tail[-1] if tail else None


def _assign_named(fn, name):
    """The (single) assignment to `name` anywhere inside `fn`, or None."""
    found = [n for n in ast.walk(fn) if isinstance(n, ast.Assign)
             and any(getattr(t, "id", None) == name for t in n.targets)]
    return found[0] if len(found) == 1 else None


# ── THE STRUCTURAL HALF: run_batch's return asks about the COHORT ──────────
#
# Pinned separately from the plant below because the two say different things.
# This says the returned boolean is computed from what the pass DID; the plant
# says main() ACTS on it. Either one alone can be satisfied while the other has
# regressed -- a return that is honest and discarded is exactly the state this
# runner was in before the pre-migration pass.
_c5_run_batch = _function_named(ast.parse(_c5_src), "run_batch")
_c5_ret = _final_return(_c5_run_batch)
_c5_ret_src = ast.unparse(_c5_ret) if _c5_ret is not None else "<no return>"
check("5b-a run_batch's returned `main_pass_complete` is computed from what "
      "the pass DID -- the patients it never submitted and the ones it "
      "cancelled -- and NOT from whether a sentinel was seen. A stop that "
      "arrives after the last patient completed cancels nothing and submits "
      "nothing, and only these two counts say so",
      ("stop_unsubmitted" in _c5_ret_src, "batch_cancelled" in _c5_ret_src,
       "STOP_SWITCH" in _c5_ret_src),
      (True, True, False))

# ── THE PLANT: main() reads the SWITCH again ───────────────────────────────
#
# THIS IS THE PRE-FIX FORM EXACTLY, and the first version of this control was
# not. Reverting run_batch's RETURN changes nothing here, because in this
# scenario the stop arrives AFTER run_batch has returned -- the returned
# boolean is True under either form. What was actually wrong before was that
# main() never read it: all four consumers asked `STOP_SWITCH.requested`
# directly, which is a question about whether a sentinel was seen and not about
# whether the cohort was covered. The plant restores that single read.
#
# Recorded rather than quietly corrected, because it is this project's own
# rule met as an event: a control that reports MISSED can mean the check is
# weak OR that the revert never took effect, and those are not the same
# finding. Here it was the second.
_c5_main = _function_named(ast.parse(_c5_src), "main")
_c5_assign = _assign_named(_c5_main, "_stopped_mid_cohort")
check("5b-b main() derives `_stopped_mid_cohort` exactly once, from the "
      "switch AND the returned completeness (non-degeneracy: the plant has a "
      "target, and a tree where that assignment had moved or been duplicated "
      "fails HERE rather than reporting an ineffective control)",
      (_c5_assign is not None,
       "_main_pass_complete" in (ast.unparse(_c5_assign.value)
                                 if _c5_assign is not None else "")),
      (True, True))

if _c5_assign is None:
    fail("5b-c the plant applied",
         "PLANT-FAILED: main() has no single `_stopped_mid_cohort` "
         "assignment, so 5d and 5f are unverified.")
    _C5 = None
else:
    _c5_lines = _c5_src.splitlines(keepends=True)
    _c5_new = "".join(
        _c5_lines[:_c5_assign.lineno - 1]
        + [" " * _c5_assign.col_offset
           + "_stopped_mid_cohort = STOP_SWITCH.requested\n"]
        + _c5_lines[_c5_assign.end_lineno:])
    _c5_tree = ast.parse(_c5_new)
    check("5b-c the reverted copy parses and its main() now reads the SWITCH "
          "rather than the cohort",
          ast.unparse(_assign_named(_function_named(_c5_tree, "main"),
                                    "_stopped_mid_cohort")),
          "_stopped_mid_cohort = STOP_SWITCH.requested")
    open(_C5_RUNNER, "w", encoding="utf-8").write(_c5_new)

    _C5 = drive(os.path.join(_TMP, "Cctl"), park="resample", action="stop",
                repo=_C5_REPO)
    print(f"        [info] control (switch): {len(_C5['main'])} main, "
          f"{len(_C5['resample'])} resample started")

    check("5b-d the stand-in hook installed in the control too, and the copy "
          "is what imported",
          _C5["hook"], True)
    check("5b-e the control really was stopped in the resample pass, so 5b-f "
          "is about the reverted read and not about a copy that never saw the "
          "sentinel",
          (_C5["saturated"], _C5["acted"],
           len(_C5["main"]) == _C5["patients"]),
          (True, True, True))
    check("5b-f *** THE PRE-FIX FORM RECORDS A COVERED COHORT AS STOPPED. *** "
          "That is the defect: a campaign that ran every patient is filed "
          "under the status whose meaning is 'this covers a prefix'",
          sorted({row[1] for row in _C5["runs"]}) or ["<no run row>"],
          ["STOPPED"])
    check("5b-g ...and KEEPS a checkpoint with nothing left in it to resume",
          "[Checkpoint] KEPT: the run was stopped" in _C5["out"], True)
    check("5b-h THE SHIPPED TREE DOES THE OPPOSITE ON BOTH, which is the fix "
          "MEASURED rather than asserted",
          (sorted({row[1] for row in _C["runs"]}),
           "[Checkpoint] Cleared for next fresh run." in _C["out"]),
          (["FINISHED"], True))


#------------------------------------------------------------------------------


# ===========================================================================
# 6. SCENARIO D -- Ctrl-C MID-BATCH, AND THE MONEY IT USED TO COST
# ===========================================================================
#
# THIS IS THE SCENARIO tests/test_runner_sigterm_shutdown.py CANNOT DRIVE. Its
# stand-in returns status="error" on purpose (so `_on_done` never reaches
# save_checkpoint and therefore never resolves the stamp over the wire), so no
# patient is ever COMPLETED there, the resample pass has no candidates, and
# main() skips it in BOTH arms. That file's own control says so and points here.
#
# Here the patients SUCCEED, so the resample pass is reachable and the SPEND
# half of the old defect can be measured rather than argued.

print("\n=== 6. scenario D: Ctrl-C mid-batch ===")

_D_ROOT = os.path.join(_TMP, "D")
_D = drive(_D_ROOT, park="main", action="sigint")
_D_MAIN = len(_D["main"])
print(f"        [info] D: {_D_MAIN} main, {len(_D['resample'])} resample "
      f"started")

check("6a-0 the stand-in hook installed", _D["hook"], True)
check("6a  the pool was saturated, the signal was delivered, and the pool's "
      "own handler was provably ENTERED before any queued patient could start",
      (_D["saturated"], _D["acted"], _D["handler_entered"]),
      (True, True, True))
check("6b  exactly the in-flight patients ran; the queue was cancelled",
      (_D_MAIN, _D_MAIN < _D["patients"]),
      (min(MAX_WORKERS, _D["patients"]), True))
check("6c  THE RUN IS RECORDED KILLED. Ctrl-C reaches main()'s crash handler "
      "now, so an interrupted campaign is distinguishable from a completed one",
      sorted({row[1] for row in _D["runs"]}) or ["<no run row>"], ["KILLED"])
check("6c-b ...and it is FINALIZED rather than left at RUNNING with a NULL "
      "finished_at",
      [row[2] is not None for row in _D["runs"]] or ["<no run row>"], [True])
check("6d  *** ZERO RESAMPLE CALLS FIRED. *** This is the money: under the old "
      "swallow the run carried straight on into the resample pass at one live "
      "billed Stage 5 call per patient, immediately after printing that it had "
      "been interrupted",
      (len(_D["resample"]), "Resampling " in _D["out"],
       "RESAMPLE COMPLETE:" in _D["out"]),
      (0, False, False))
check("6e  exit 130 (128 + SIGINT), with no traceback",
      (_D["exit"], "Traceback (most recent call last)" in _D["out"]),
      (128 + int(signal.SIGINT), False))
check("6f  the checkpoint holds exactly the completed patients, so the "
      "interrupt is resume-safe",
      len((_D["checkpoint"] or {}).get("completed_stems", [])), _D_MAIN)

# --- the resume half --------------------------------------------------------
_D2 = drive(_D_ROOT, park="none", action=None, fresh_corpus=False)
print(f"        [info] D-resume: {len(_D2['main'])} main-pass patients started")
check("6g  a Ctrl-C'd run RESUMES exactly like a stopped one: the completed "
      "patients are skipped and the remainder run, with nothing re-billed",
      (len(_D2["main"]), len(_D2["main"]) + _D_MAIN),
      (_D["patients"] - _D_MAIN, _D["patients"]))
check("6g-b ...and no stop sentinel was involved -- the checkpoint alone "
      "carries the resume, which is why a Ctrl-C needs no cleanup gesture",
      os.path.exists(_D["stop_file"]), False)
check("6h  ...and the resumed run finalizes FINISHED beside the KILLED row",
      [row[1] for row in _D2["runs"]], ["KILLED", "FINISHED"])


#------------------------------------------------------------------------------


# ===========================================================================
# 7. THE CONTROL FOR SCENARIO D -- THE OLD SWALLOW, MEASURED IN DOLLARS
# ===========================================================================
#
# Scenario D's 6d is a claim about a NUMBER, and a number means nothing without
# the other arm. The pre-fix SWALLOW is reconstructed in a COPY of the package
# -- the `raise` deleted from run_batch's `except KeyboardInterrupt` and from
# nowhere else -- and the same scenario is driven against it.
#
# THE PLANT IS STRUCTURAL. `raise` is a bare keyword appearing in several
# handlers in that module, so a string replace would either hit the wrong one or
# need an anchor long enough to be its own maintenance problem. The handler is
# located by AST inside run_batch's span, its trailing `ast.Raise` is located by
# node, and that node's LINES are removed -- so a `raise` that has moved is a
# named PLANT-FAILED rather than a control that quietly tests the shipped tree
# against itself.

print("\n=== 7. the control: without the re-raise, the resample pass bills ===")

_CTRL_REPO = os.path.join(_TMP, "pkgcopy")
os.makedirs(_CTRL_REPO, exist_ok=True)
shutil.copytree(os.path.join(_REPO, "oncotriage"),
                os.path.join(_CTRL_REPO, "oncotriage"),
                ignore=shutil.ignore_patterns("__pycache__"))
_CTRL_RUNNER = os.path.join(_CTRL_REPO, "oncotriage", "batch", "runner.py")
_ctrl_src = open(_CTRL_RUNNER, encoding="utf-8").read()


def _ki_raise_lines(fn):
    """Line spans of the bare `raise` closing `fn`'s KeyboardInterrupt handler."""
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


_ctrl_fn = _function_named(ast.parse(_ctrl_src), "run_batch")
_targets = _ki_raise_lines(_ctrl_fn)
check("7a  run_batch's KeyboardInterrupt handler closes with exactly one bare "
      "`raise` (non-degeneracy: the plant has a target, and a tree where the "
      "re-raise had been removed fails HERE rather than reporting an "
      "ineffective control)",
      len(_targets), 1)

if len(_targets) != 1:
    fail("7b  the plant applied",
         "PLANT-FAILED: run_batch's KeyboardInterrupt handler does not end in "
         "a single bare `raise`, so the swallow control did not run and 6c/6d "
         "are unverified.")
else:
    _lines = _ctrl_src.splitlines(keepends=True)
    _lo, _hi = _targets[0]
    _ctrl_new = "".join(_lines[:_lo - 1] + _lines[_hi:])
    _ctrl_tree = ast.parse(_ctrl_new)
    check("7b  the reverted copy parses, run_batch no longer re-raises, and "
          "run_resample is untouched",
          (len(_ki_raise_lines(_function_named(_ctrl_tree, "run_batch"))),
           len(_ki_raise_lines(_function_named(_ctrl_tree, "run_resample")))),
          (0, 1))
    open(_CTRL_RUNNER, "w", encoding="utf-8").write(_ctrl_new)

    _CTRL = drive(os.path.join(_TMP, "Dctl"), park="main", action="sigint",
                  repo=_CTRL_REPO)
    _CTRL_MAIN = len(_CTRL["main"])
    _CTRL_RES = len(_CTRL["resample"])
    print(f"        [info] control (swallow): {_CTRL_MAIN} main, "
          f"{_CTRL_RES} resample started")

    check("7c  the stand-in hook installed in the control too, and the copy is "
          "what imported",
          _CTRL["hook"], True)
    check("7c-b the control really was interrupted -- the pool handler ran -- "
          "so 7d/7e are about the swallow and not about a copy that never saw "
          "the signal",
          ("[INTERRUPTED] Waiting for active threads to finish"
           in _CTRL["out"], _CTRL["saturated"]),
          (True, True))
    check("7d  *** THE PRE-FIX FORM BILLS AFTER THE INTERRUPT. *** The "
          "resample pass runs and makes one live Stage 5 call per re-run "
          "patient -- here one per patient the main pass had completed -- all "
          "of it after the operator asked the run to stop",
          (_CTRL_RES, _CTRL_RES > 0, "RESAMPLE COMPLETE:" in _CTRL["out"]),
          (min(RESAMPLE_COUNT, _CTRL_MAIN), True, True))
    check("7e  ...and it records the interrupted run as ended normally -- "
          "FINISHED, indistinguishable from a campaign that covered its cohort",
          sorted({r[1] for r in _CTRL["runs"]}) or ["<no run row>"],
          ["FINISHED"])
    check("7f  ...and exits through the reconciliation verdict rather than "
          "128 + SIGINT",
          (_CTRL["exit"], _CTRL["exit"] == 128 + int(signal.SIGINT)),
          (0, False))
    check("7g  THE SHIPPED TREE DOES THE OPPOSITE ON ALL THREE, which is the "
          "fix MEASURED rather than asserted",
          (len(_D["resample"]), sorted({r[1] for r in _D["runs"]}),
           _D["exit"]),
          (0, ["KILLED"], 128 + int(signal.SIGINT)))


#------------------------------------------------------------------------------


# ===========================================================================
# 8. SCENARIO F -- --clear-stop CLEARS AND RUNS IN ONE COMMAND
# ===========================================================================

print("\n=== 8. scenario F: --clear-stop ===")

_F_ROOT = os.path.join(_TMP, "F")
os.makedirs(os.path.join(_F_ROOT, "cp"), exist_ok=True)
_F_STOP = os.path.join(_F_ROOT, "cp", _runner.STOP_FILENAME)
with open(_F_STOP, "w", encoding="utf-8") as _fh:
    _fh.write("yesterday's stop")

_F = drive(_F_ROOT, park="none", action=None, args=("--clear-stop",),
           patients=6)

check("8a-0 the stand-in hook installed", _F["hook"], True)
check("8a  the sentinel is gone and the run started -- the refusal is not "
      "reached, because the flag clears it first",
      (os.path.exists(_F_STOP),
       "REFUSED (stop switch present)" in _F["out"],
       "[STOP] Cleared" in _F["out"]),
      (False, False, True))
check("8b  ...and the run then behaves like any other: the whole cohort ran "
      "and it finalized FINISHED",
      (len(_F["main"]), sorted({r[1] for r in _F["runs"]})),
      (6, ["FINISHED"]))
check("8c  exit 0", _F["exit"], 0)


#------------------------------------------------------------------------------


# ===========================================================================
# 9. CAMPAIGN STITCHING OVER THE NEW STATUS
# ===========================================================================
#
# A `runs` ROW IS A PROCESS, NOT A CAMPAIGN. One cohort stopped and resumed is
# TWO rows, each reporting a FRAGMENT of it -- so `campaign_summary` has to
# stitch a STOPPED-then-resumed chain exactly as it stitches a
# KILLED-then-resumed one, or a stopped-and-resumed cohort reports as two
# campaigns neither of which covers it and no rate over either is a rate about
# the cohort.
#
# THE ROWS ARE WRITTEN BY THE REAL WRITER -- start_run_record and
# finalize_run_record against a scratch database built by the real
# initialize_database -- rather than by hand-typed INSERTs, so the column set,
# the stamp columns and the `resumed` flag are the shipped ones by construction.

print("\n=== 9. campaign stitching ===")

try:
    from oncotriage.storage import queries as _queries
except Exception as _queries_exc:                              # noqa: BLE001
    _queries = None
    fail("9-import oncotriage.storage.queries imports",
         f"{type(_queries_exc).__name__}: {_queries_exc}. That module raises "
         f"at import when CAMPAIGN_RESUMABLE_STATUSES stops being a proper "
         f"subset of RUN_RECORD_TERMINAL_STATUSES, so this is very likely the "
         f"STOPPED vocabulary having been changed on one side only. Every "
         f"campaign-stitch check below is unverified.")
else:
    check("9-import oncotriage.storage.queries imports, so the vocabularies "
          "its own guard compares are still consistent",
          _queries is not None, True)

_STITCH_DB = os.path.join(_TMP, "stitch.db")
_dblog.initialize_database(_STITCH_DB)

def _stamp(tag):
    """A full stamp whose prompt-version field is `tag`.

    EVERY GROUP BELOW GETS ITS OWN TAG, and that is a correction rather than
    caution. The first version of this seed reused one stamp for all six rows,
    and the query legitimately stitched `1 -> 2 -> 4`: the rule is "the nearest
    preceding run satisfying BOTH halves", so row 4 reached back PAST the
    FINISHED row 3 to the STOPPED row 1, whose stamp it shared. That is the
    documented behaviour (see the note at CAMPAIGN_RESUMABLE_STATUSES), it is
    pinned deliberately as group 4 below, and it made the control for it
    ambiguous -- a seed whose groups can reach each other cannot say which half
    of the rule produced an answer.
    """
    out = {"fingerprint_version": 3}
    for field in _dblog.RUN_FINGERPRINT_COLUMNS:
        if field != "fingerprint_version":
            out[field] = "stand-in"
    out["llm_classifier_prompt_version"] = tag
    return out


def _row(status, resumed, tag):
    rid = _dblog.start_run_record("batch", db_path=_STITCH_DB,
                                  fingerprint=_stamp(tag), resumed=resumed)
    _dblog.finalize_run_record(rid, status, db_path=_STITCH_DB)
    return rid


# group 1 -- a stop and its resume, same configuration     -> ONE campaign
_row("STOPPED", False, "A")                                          # 1
_row("FINISHED", True, "A")                                          # 2
# group 2 -- a completed run and a later resume, same configuration -> TWO
_row("FINISHED", False, "C")                                         # 3
_row("FINISHED", True, "C")                                          # 4
# group 3 -- a stop and a resume under a DIFFERENT configuration    -> TWO
_row("STOPPED", False, "D")                                          # 5
_row("FINISHED", True, "E")                                          # 6
# group 4 -- a stop, an unrelated run between, then its resume      -> ONE
_row("STOPPED", False, "F")                                          # 7
_row("FINISHED", False, "G")                                         # 8
_row("FINISHED", True, "F")                                          # 9

check("9a  the scratch database holds the nine run rows the real writer wrote "
      "(non-degeneracy: without them every campaign assertion below is about "
      "an empty frame)",
      sqlite3.connect(_STITCH_DB).execute(
          "SELECT COUNT(*) FROM runs").fetchone(), (9,))
check("9a-b ...and STOPPED really is what landed in the column -- the writer "
      "accepts it as terminal rather than replacing it with FAILED, which is "
      "what an unrecognised status would have become",
      [r[0] for r in sqlite3.connect(_STITCH_DB).execute(
          "SELECT status FROM runs ORDER BY id").fetchall()],
      ["STOPPED", "FINISHED", "FINISHED", "FINISHED", "STOPPED", "FINISHED",
       "STOPPED", "FINISHED", "FINISHED"])

if _queries is None:
    _campaigns = ("<absent>", "ImportError", "queries did not import")
else:
    _conn = sqlite3.connect(_STITCH_DB)
    try:
        _campaigns = drive_call(_queries.run, _conn, "campaign_summary")
    finally:
        _conn.close()

if not hasattr(_campaigns, "to_dict"):
    fail("9b  campaign_summary ran",
         f"the query did not return a frame: {_campaigns!r}. Every stitch "
         f"assertion below is unverified.")
else:
    _by_ids = {row["run_ids"]: row for _, row in _campaigns.iterrows()}
    print(f"        [info] campaigns: {sorted(_by_ids)}")

    check("9b  A STOPPED RUN AND ITS RESUME ARE ONE CAMPAIGN, exactly as a "
          "KILLED one and its resume are. Without this a stopped-and-resumed "
          "cohort reports as two fragments neither of which covers it",
          ("1 -> 2" in _by_ids,
           int(_by_ids["1 -> 2"]["runs"]) if "1 -> 2" in _by_ids else None,
           int(_by_ids["1 -> 2"]["stitched"]) if "1 -> 2" in _by_ids else None),
          (True, 2, 1))
    # THE SEPARATOR IS THE QUERY'S OWN " -> ", not a comma: `statuses` is built
    # by the same ordered recursion `run_ids` is, so it reads in campaign order
    # rather than as a set. Measured rather than assumed -- the first version
    # of this check split on ", " and got the whole string back as one member.
    check("9b-b ...and the campaign reports BOTH statuses IN ORDER, so the "
          "stitch does not hide that its first fragment was stopped",
          _by_ids.get("1 -> 2", {}).get("statuses"), "STOPPED -> FINISHED")
    check("9b-c ...and it is flagged as mixed-status, which is the column a "
          "reader filters on to find campaigns that did not run straight "
          "through",
          int(_by_ids["1 -> 2"]["mixed_status"]) if "1 -> 2" in _by_ids else None,
          1)

    check("9c  A RESUME AFTER A **FINISHED** RUN DOES NOT STITCH -- the "
          "control for 9b. A completed campaign has nothing left to resume, so "
          "gluing a later invocation onto one would turn a re-run into a "
          "continuation",
          ("3" in _by_ids, "4" in _by_ids, "3 -> 4" in _by_ids),
          (True, True, False))

    check("9d  A RESUME UNDER A DIFFERENT CONFIGURATION DOES NOT STITCH TO A "
          "STOPPED RUN either -- the stop status does not weaken the "
          "fingerprint half of the rule, which is the half that stops "
          "fragments produced under different configurations from summing",
          ("5" in _by_ids, "6" in _by_ids, "5 -> 6" in _by_ids),
          (True, True, False))

    # THIS IS THE BEHAVIOUR THAT MADE THE FIRST VERSION OF THIS SEED
    # AMBIGUOUS, and it is pinned rather than merely avoided. "Nearest
    # preceding" means nearest among runs satisfying BOTH halves of the rule,
    # so a resume attaches ACROSS an intervening run of another configuration.
    # The module argues for that reading in place: the alternative -- refuse to
    # stitch when the immediately preceding terminal run has a different
    # fingerprint -- would report a genuine resume as a whole campaign, which
    # IS a misattribution, whereas this is a reporting artifact whose gap is
    # visible in `run_ids`.
    check("9d-b a STOPPED run's resume stitches ACROSS an unrelated run "
          "between them, and the gap is visible in the id list",
          ("7 -> 9" in _by_ids, "8" in _by_ids,
           int(_by_ids["7 -> 9"]["runs"]) if "7 -> 9" in _by_ids else None),
          (True, True, 2))

if _queries is None:
    fail("9e  the stitch predicate's status list is GENERATED from "
         "CAMPAIGN_RESUMABLE_STATUSES",
         "oncotriage.storage.queries did not import; see 9-import.")
    fail("9e-b ...and the resumable set is still a PROPER subset of the "
         "terminal set",
         "oncotriage.storage.queries did not import; see 9-import.")
else:
    check("9e  the stitch predicate's status list is GENERATED from "
          "CAMPAIGN_RESUMABLE_STATUSES rather than retyped, so STOPPED "
          "reached the SQL by being added to the vocabulary and not by a "
          "second edit that could have been forgotten",
          (_dblog.RUN_RECORD_STATUS_STOPPED
           in _queries.CAMPAIGN_RESUMABLE_STATUSES,
           f"'{_dblog.RUN_RECORD_STATUS_STOPPED}'"
           in _queries._CAMPAIGN_STATUS_LIST_SQL),
          (True, True))
    check("9e-b ...and the resumable set is still a PROPER subset of the "
          "terminal set, which is what keeps FINISHED out of it -- the "
          "module's own import-time guard, restated here so a widened "
          "vocabulary fails as a check rather than as an ImportError three "
          "files away",
          (set(_queries.CAMPAIGN_RESUMABLE_STATUSES)
           < set(_dblog.RUN_RECORD_TERMINAL_STATUSES),
           "FINISHED" in _queries.CAMPAIGN_RESUMABLE_STATUSES),
          (True, False))


#------------------------------------------------------------------------------


# ===========================================================================
# 10. NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================

print("\n=== 10. the repository is unchanged ===")

_SHA_RUNNER_AFTER = hashlib.sha256(open(_RUNNER_PATH, "rb").read()).hexdigest()
_SHA_ENTRY_AFTER = hashlib.sha256(open(_ENTRY_PATH, "rb").read()).hexdigest()
check("10a oncotriage/batch/runner.py is byte-identical",
      _SHA_RUNNER_AFTER, _SHA_RUNNER_BEFORE)
check("10b 25- Batch Runner.py is byte-identical",
      _SHA_ENTRY_AFTER, _SHA_ENTRY_BEFORE)
check("10c ...and those comparisons are not tautologies: both files are "
      "non-empty and were re-read from disk",
      (len(_RUNNER_SRC) > 1000, len(_ENTRY_SRC) > 1000,
       _SHA_RUNNER_BEFORE != _SHA_ENTRY_BEFORE), (True, True, True))
check("10d the production inferences path was never resolved in this process, "
      "so no scenario could have written to it",
      "inferences_path" in _paths._RESOLVED, False)

shutil.rmtree(_TMP, ignore_errors=True)
check("10e the scratch tree was removed", os.path.exists(_TMP), False)


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
print("=" * 78)

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 24 2026

@author: ramyalsaffar
"""
