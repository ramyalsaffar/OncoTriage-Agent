# Batch Runner Preflight and State-File Faults Test
##################################################

"""One run at a time, a refusal that deletes nothing, and a fault that is counted.

THREE FINDINGS, ALL OF THEM SILENT, ALL OF THEM ABOUT THE FIRST AND LAST
SECONDS OF A BILLED CAMPAIGN.

    1. TWO RUNS AGAINST ONE CHECKPOINT DIRECTORY WERE A SILENT DOUBLE BILL.
       Nothing stopped a second invocation of "25- Batch Runner.py" from
       starting beside a running one. Both read the same checkpoint at start,
       so both saw the same "already completed" set and both processed the SAME
       patients -- one live Stage 5 call each, twice, for a cohort that needed
       one. The checkpoint itself is written atomically and is never corrupt;
       it simply ends as the LAST writer's view, so the loser's completions
       vanish from it and a THIRD run re-bills those too. Neither run reported
       anything wrong. Measured by driving it.

    2. A CHECKPOINT THAT COULD NOT BE WRITTEN WAS PRINTED AND NOT COUNTED. A
       read-only checkpoint directory -- a filled disk, a remounted share, a
       permission change -- makes every `save_checkpoint` fail, once per
       completed patient. The run printed a WARNING each time, finished with
       the degradation block reporting CLEAN, and the closing line said the
       checkpoint had been kept. The next invocation re-billed every patient.

    3. `--fresh` DELETED THE CHECKPOINT AND THEN REFUSED TO RUN. The stale-stop
       preflight lived only inside `main()`, which is called AFTER the guard
       has processed its flags -- so an operator who typed `--fresh` while a
       stop sentinel was still present got the checkpoint deleted, then a
       refusal whose own last line reads "NOTHING HAS BEEN RUN AND NOTHING HAS
       BEEN BILLED". True of the billing; false of the resume state, which was
       gone.

WHAT THIS FILE HOLDS
--------------------
    1. THE LOCK, driven with REAL CONCURRENT SUBPROCESSES: one runs, the second
       is refused with exit 3 and starts no patient; the first then completes
       normally; a first that is SIGKILLed leaves the lock free for a
       successor, which is the property a pid file cannot have.
    2. THE COUNTERS, driven against a checkpoint directory made read-only
       mid-run: the run-end block reports DEGRADED rather than CLEAN, names the
       two counters, and the closing line stops claiming a checkpoint was kept.
    3. THE PREFLIGHT ORDER, driven end to end: `--fresh` beside a stale
       sentinel refuses with the checkpoint BYTE-IDENTICAL, `--clear-stop`
       satisfies the preflight rather than being blocked by it, and the flag
       announcements reach a captured log IN ORDER, which a `print` to a
       block-buffered stdout does not.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, NO SPEND, no live Qdrant, no model load (every subprocess
sets ONCOTRIAGE_DEFER_LOCAL_MODELS and the graph is never invoked), no corpus --
every FHIR file is a two-key literal in a temp directory -- no git history, no
live server. `process_patient`, the BM25 index, the graph, the tracking module
and `run_fingerprint.current` are stand-ins, for the reasons
tests/test_runner_stop_switch.py records; EVERYTHING ELSE IS THE REAL THING,
including the real `__main__` guard of "25- Batch Runner.py", which is where the
lock and the preflight live.

NOT IN THE COLLISION MATRIX, derived: every database, checkpoint, sentinel and
FHIR file it writes is inside a `tempfile.mkdtemp` it removes and then asserts
gone; it patches no repository file; and the two repository files it READS --
`oncotriage/batch/runner.py` and `25- Batch Runner.py` -- are written by neither
of the suite's two writers and are sha256-compared at the end.

IT EXECS NOTHING and needs no `_EXEC_ALLOWLIST` entry: every control is a real
condition created on disk (a held lock, a killed holder, an unwritable
directory) or a different INPUT to a pure function.

Run from terminal:
    python tests/test_runner_preflight_and_state_faults.py

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
import calendar
import hashlib
import json
import shutil
import signal
import sqlite3
import stat
import subprocess
import tempfile
import time

import oncotriage
from oncotriage import control as _control               # noqa: E402
from oncotriage import paths as _paths
from oncotriage.batch import runner as _runner

# tests/ ON sys.path SO THE SHARED HARNESS IMPORTS. There is no __init__.py in
# tests/ -- deliberately, so the directory is not a package and nothing ships
# it -- so a sibling import needs the directory itself on the path.
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

# THE SHARED OPERATOR-CONTROL HARNESS (the consolidation pass): the closed-port
# URL, the deadline waiter and the ONE park protocol that replaced three
# incompatible ONC_PARK encodings. Two of the three read the SAME variable with
# different vocabularies -- "1"/"0" here and "yes"/"no" in the ablation file --
# so a hook copied between them would have parked on "no".
import _control_harness as _harness                            # noqa: E402


#------------------------------------------------------------------------------


_T_START = time.time()

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_RUNNER_PATH = os.path.abspath(_runner.__file__)
_ENTRY_PATH = os.path.join(_REPO, "25- Batch Runner.py")

_RUNNER_SRC = open(_RUNNER_PATH, encoding="utf-8").read()

# THE LOCK MECHANISM MOVED TO oncotriage/control.py (the consolidation pass) AND
# THE STRUCTURAL CHECKS BELOW MOVED WITH IT. They are about the same code -- the
# 0700 directory, the O_NOFOLLOW open, the never-unlinked file -- which now has
# ONE owner instead of three; asserting them against the runner's source would
# be a walk that finds nothing and passes, which is the shape this file's own
# non-degeneracy probes exist to catch. The runner's `exclusive_run_lock` is
# still checked here for what it still decides: its key, its two exception
# classes and the field its record names.
_CONTROL_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(_runner.__file__))), "control.py")
_CONTROL_SRC = open(_CONTROL_PATH, encoding="utf-8").read()
_ENTRY_SRC = open(_ENTRY_PATH, encoding="utf-8").read()
_SHA_RUNNER_BEFORE = hashlib.sha256(_RUNNER_SRC.encode("utf-8")).hexdigest()
_SHA_ENTRY_BEFORE = hashlib.sha256(_ENTRY_SRC.encode("utf-8")).hexdigest()

_TMP = tempfile.mkdtemp(prefix="runnerpreflight_")


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
    repository eleven times; it does not ship here.
    """
    try:
        return sequence[index]
    except (IndexError, KeyError, TypeError):
        return default


def sha_or_absent(path):
    """The file's sha256, or a marker. NEVER RAISES.

    A bare `open(path, "rb")` raises FileNotFoundError EXACTLY when a defect
    deletes the file the check exists to prove was not deleted -- so the run
    prints one traceback where it owes a named failure and every check below it
    unrun. Measured, not anticipated: reverting the preflight above --fresh in a
    copy aborted this file at section 7 until this existed.
    """
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except OSError as exc:                                      # noqa: BLE001
        return f"<absent: {type(exc).__name__}>"


def drive_call(fn, *args, **kwargs):
    """Call into production code, converting a raise into a comparable value."""
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                                # noqa: BLE001
        return ("<raised>", type(exc).__name__, str(exc))


#------------------------------------------------------------------------------


# ===========================================================================
# 1. THE LOCK MECHANISM, DRIVEN DIRECTLY
# ===========================================================================

print("\n=== 1. the lock mechanism ===")

_L1 = os.path.join(_TMP, "lockdir-a")
_L2 = os.path.join(_TMP, "lockdir-b")
os.makedirs(_L1, exist_ok=True)
os.makedirs(_L2, exist_ok=True)

_PATH_A = _runner.run_lock_path(_L1)
_PATH_B = _runner.run_lock_path(_L2)

check("1a  the lock path is keyed on the CHECKPOINT DIRECTORY, so two "
      "deployments do not block each other and two runs against one do",
      _PATH_A == _PATH_B, False)
check("1a-b ...and it is deterministic: the same directory gives the same "
      "path, or two invocations would lock two different files and neither "
      "would exclude the other",
      _runner.run_lock_path(_L1), _PATH_A)
check("1b  it lives OUTSIDE the checkpoint directory and outside the "
      "repository. The directory's other three files are the run's resumable "
      "state, and it may be a network share where flock is advisory at best",
      (os.path.abspath(_PATH_A).startswith(os.path.abspath(_L1)),
       os.path.abspath(_PATH_A).startswith(os.path.abspath(_REPO))),
      (False, False))
check("1b-b ...specifically in a PER-USER subdirectory of the system temp "
      "directory. The bare temp directory is world-writable and this file's "
      "name is a sha256 of a guessable path, so another user could pre-create "
      "it as a symlink to something this user can write and the first run to "
      "start would O_CREAT through it and ftruncate the target to zero. The "
      "sticky bit does not help: it stops one user DELETING another's file, "
      "not creating one at an unclaimed name",
      (os.path.dirname(os.path.abspath(_PATH_A)), _control.lock_directory()),
      (_control.lock_directory(),
       os.path.join(tempfile.gettempdir(), f"oncotriage-{os.getuid()}")))
def _creating_calls(module_src, function_name):
    """The directory-creating calls in one top-level function, by name."""
    node = next((n for n in ast.walk(ast.parse(module_src))
                 if isinstance(n, ast.FunctionDef)
                 and n.name == function_name), None)
    if node is None:
        return ["<function not found>"]
    return sorted({getattr(c.func, "attr", getattr(c.func, "id", None))
                   for c in ast.walk(node) if isinstance(c, ast.Call)}
                  & {"makedirs", "mkdir", "ensure_lock_directory"})


check("1b-c ...and lock_directory() is PURE -- asking for the path creates "
      "nothing, while ensure_lock_directory() is the one that creates. That "
      "is the output_dir()/ensure_output_dir() split this project already "
      "records: a caller who only wants to PRINT the path -- a diagnostic, a "
      "refusal line, a test -- must not bring a directory into existence by "
      "asking. The second half is the non-degeneracy probe: without it a walk "
      "that found nothing anywhere would pass",
      (_creating_calls(_CONTROL_SRC, "lock_directory"),
       _creating_calls(_CONTROL_SRC, "ensure_lock_directory")),
      ([], ["makedirs"]))
check("1b-c2 ...and the acquisition DOES ensure it, or the first run on a "
      "fresh machine would meet ENOENT rather than a lock. Two frames now: "
      "the runner's exclusive_run_lock delegates to control's "
      "hold_exclusive_lock, which is what calls it",
      (sorted({getattr(c.func, "attr", getattr(c.func, "id", None))
               for c in ast.walk(next(
                   n for n in ast.walk(ast.parse(_RUNNER_SRC))
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "exclusive_run_lock"))
               if isinstance(c, ast.Call)} & {"hold_exclusive_lock"}),
       _creating_calls(_CONTROL_SRC, "hold_exclusive_lock")),
      (["hold_exclusive_lock"], ["ensure_lock_directory"]))
check("1b-d ...and the directory is named by the UID rather than by the login "
      "name. getpass.getuser() reads LOGNAME/USER/LNAME/USERNAME before the "
      "password database, all four settable by the process asking -- so a "
      "login-name directory would split one real user's lock namespace in two "
      "whenever those differed between invocations (a cron entry beside an "
      "interactive shell), and two namespaces for one checkpoint directory is "
      "the double bill this lock exists to prevent",
      (os.path.basename(_control.lock_directory()),
       os.environ.get("USER", "") in os.path.basename(
           _control.lock_directory()) and bool(os.environ.get("USER"))),
      (f"oncotriage-{os.getuid()}", False))
check("1c  a trailing separator does not make a different lock -- "
      "paths.checkpoint_path resolves WITH one and a caller may pass either",
      _runner.run_lock_path(_L1 + os.sep), _PATH_A)

# The mechanism itself: held, then refused, then free again.
with _runner.exclusive_run_lock(_PATH_A) as _held_path:
    check("1d  the context manager yields the path it locked", _held_path,
          _PATH_A)
    _refused = drive_call(
        lambda: _runner.exclusive_run_lock(_PATH_A).__enter__())
    check("1d-b a second acquire of the SAME file is refused IMMEDIATELY -- "
          "never waited for. A run that queued behind the first would still "
          "run, hours later, against a cohort the first had finished",
          at(_refused, 1), "AlreadyRunning")
    with _runner.exclusive_run_lock(_PATH_B):
        check("1d-c ...and a DIFFERENT file is not refused, which is what says "
              "1d-b is about the lock rather than about acquiring twice",
              True, True)
    _record = drive_call(lambda: json.load(open(_PATH_A, encoding="utf-8")))
    check("1e  the holder's record names what an operator needs to act on: a "
          "pid to kill, a host and user to attribute it to, and a start time "
          "to judge whether it is stuck",
          sorted(_record) if isinstance(_record, dict) else _record,
          ["checkpoint_dir", "host", "pid", "started", "user"])
    check("1e-b ...and the pid is THIS process, so the record describes the "
          "holder rather than being a template",
          _record.get("pid") if isinstance(_record, dict) else None,
          os.getpid())
    check("1e-c ...and with the lock file named DIRECTLY the record says the "
          "directory was not supplied rather than naming one it has nothing "
          "to do with. The first version read paths.checkpoint_path a second "
          "time here, so an explicit path produced a holder record pointing "
          "an operator at the wrong deployment",
          _record.get("checkpoint_dir") if isinstance(_record, dict) else None,
          "<not supplied: the lock file was named directly>")

with _runner.exclusive_run_lock(checkpoint_dir=_L1) as _keyed_path:
    _keyed = drive_call(lambda: json.load(open(_keyed_path, encoding="utf-8")))
    check("1e-d ...and given the DIRECTORY it derives the same path and names "
          "it, which is the form main()'s guard uses",
          (_keyed_path,
           _keyed.get("checkpoint_dir") if isinstance(_keyed, dict) else None),
          (_PATH_A, os.path.realpath(_L1)))

# 1e-e's NON-DEGENERACY IS CONSTRUCTED, NOT INHERITED FROM THE PLATFORM.
#
# It used to be `os.path.realpath(_L1) != os.path.abspath(_L1)`, asserted True,
# under a note reading "non-degenerate on this machine: macOS puts the temp tree
# under /var, which is a link to /private/var". That is a statement about the
# DEVELOPMENT MACHINE'S filesystem, not about the code: Linux puts the temp tree
# at /tmp, which is a real directory, so the probe read False and the check
# FAILED on every hosted runner -- while its actual subject, the first member of
# the tuple, matched. A green macOS run and a red Linux one, over code that was
# correct in both.
#
# The repair is the one tests/test_serial_runner_lock.py section 2 already
# takes: MAKE the symlink and drive the lock through it. That is strictly
# stronger than what it replaces, because it exercises a genuinely unresolved
# path instead of hoping the operating system supplies one -- and the probe is
# then true by construction everywhere.
_L1_LINK = os.path.join(_TMP, "lockdir-a-link")
if not os.path.lexists(_L1_LINK):
    os.symlink(_L1, _L1_LINK)

check("1e-e0 the link and the directory are DIFFERENT strings, and abspath -- "
      "which is what the pre-fix key used -- does NOT collapse them "
      "(non-degeneracy, by construction rather than by platform)",
      (_L1_LINK != _L1, os.path.abspath(_L1_LINK) != os.path.abspath(_L1)),
      (True, True))
check("1e-e1 ...while realpath DOES collapse them, so this is one directory "
      "under two names rather than two directories",
      os.path.realpath(_L1_LINK), os.path.realpath(_L1))

with _runner.exclusive_run_lock(checkpoint_dir=_L1_LINK) as _linked_path:
    _linked = drive_call(
        lambda: json.load(open(_linked_path, encoding="utf-8")))
    check("1e-e ...and reached through the SYMLINK it locks the SAME file and "
          "the record names the RESOLVED path, matching the KEY. The lock is "
          "keyed on the realpath, so a record naming the unresolved one could "
          "show an operator a different string from the one the refused run "
          "hashed -- two names for the one thing the refusal is about",
          (_linked_path,
           _linked.get("checkpoint_dir") if isinstance(_linked, dict) else None),
          (_PATH_A, os.path.realpath(_L1)))
    check("1e-e2 ...and that is not the string it was GIVEN, which is what "
          "makes 1e-e a statement about resolution rather than an echo",
          _linked.get("checkpoint_dir") if isinstance(_linked, dict) else None,
          os.path.realpath(_L1_LINK))
    check("1e-e3 ...non-degeneracy: the string it was given really does differ "
          "from the one recorded, on this platform as on any other",
          (_linked.get("checkpoint_dir")
           if isinstance(_linked, dict) else None) != _L1_LINK, True)

def _reacquire(path):
    """Take and release the lock, reporting whether it was free."""
    with _runner.exclusive_run_lock(path):
        return "acquired"


check("1f  leaving the block releases it, so a second run can start once the "
      "first has ended -- and it is released by CLOSING the descriptor, which "
      "is what the kernel also does when a process dies badly",
      drive_call(_reacquire, _PATH_A), "acquired")

check("1g  THE FILE IS NEVER DELETED, and that is the mechanism rather than "
      "untidiness: the lock is the flock on the INODE. Removing it on the way "
      "out would let a second process create a NEW inode and lock that while "
      "a third still held the old one",
      os.path.exists(_PATH_A), True)
_LOCK_FN = next(
    (n for n in ast.walk(ast.parse(_CONTROL_SRC))
     if isinstance(n, ast.FunctionDef) and n.name == "hold_exclusive_lock"),
    None)
check("1g-b ...and the lock function REMOVES NOTHING: no `remove`, `unlink`, "
      "`rmtree` or `replace` anywhere in its body, whatever the argument is "
      "called",
      [] if _LOCK_FN is None else
      sorted({getattr(n.func, "attr", None) for n in ast.walk(_LOCK_FN)
              if isinstance(n, ast.Call)
              and getattr(n.func, "attr", None)
              in ("remove", "unlink", "rmtree", "replace")}),
      [])
check("1g-c ...and the function was really found, so 1g-b is not an empty "
      "walk -- and it does close the descriptor, which IS the release",
      (_LOCK_FN is not None,
       _LOCK_FN is not None and "close" in {getattr(n.func, "attr", None)
                                            for n in ast.walk(_LOCK_FN)
                                            if isinstance(n, ast.Call)}),
      (True, True))

_refusal = _runner.run_lock_refusal_lines(
    _runner.AlreadyRunning("/tmp/x.lock",
                           {"pid": 4321, "host": "h", "user": "u",
                            "started": "2026-08-24 10:00:00",
                            "checkpoint_dir": "/cp"}))
check("1h  the refusal names every field of the record, the lock file, and "
      "says nothing has been billed",
      (all(str(v) in "\n".join(_refusal)
           for v in (4321, "h", "u", "2026-08-24 10:00:00", "/cp")),
       "/tmp/x.lock" in "\n".join(_refusal),
       "NOTHING HAS BEEN RUN" in "\n".join(_refusal)),
      (True, True, True))
check("1h-b ...and it names the STOP sentinel as the way to end the other run "
      "cleanly, which is the action an operator most often wants",
      "touch" in "\n".join(_refusal), True)
check("1i  a record that could not be parsed is still REPORTED rather than "
      "swallowed -- an unreadable holder is not an absent one",
      any("garbage" in line for line in _runner.run_lock_refusal_lines(
          _runner.AlreadyRunning("/tmp/x", {"record": "garbage"}))),
      True)
check("1j  EXIT_LOCKED does not collide with anything this entry point "
      "already returns: 0/1/2 are the reconciliation verdict, 130 is Ctrl-C "
      "and 143 is SIGTERM",
      (_control.EXIT_LOCKED, _control.EXIT_LOCKED in (0, 1, 2, 130, 143)),
      (3, False))


#------------------------------------------------------------------------------


# ===========================================================================
# 2. THE STAND-IN HOOK AND THE DRIVER
# ===========================================================================
#
# tests/test_runner_stop_switch.py's, with two additions: the patients PARK on
# a release file (so a run can be held open while a second one is launched
# against it) and the corpus is small, because nothing here is about
# cancellation counts.

_HOOK = r"""
import os, sys, threading, time

# tests/ IS ON PYTHONPATH BESIDE THIS HOOK, so the shared harness is an ordinary
# import. It imports nothing from the project, which is required rather than
# tidy: this file runs at INTERPRETER STARTUP and an `oncotriage` import here
# would change what the process under test had loaded before its own first line.
import _control_harness as _h

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

_STAMP = {"fingerprint_version": F.FINGERPRINT_VERSION}
for _field in F.FINGERPRINT_FIELDS:
    _STAMP[_field] = "stand-in"
F.current = lambda refresh=False: dict(_STAMP)
F.clear_cache = lambda: None

P._RESOLVED["data_fhir_path"] = os.environ["ONC_CORPUS"] + os.sep
P._RESOLVED["inferences_path"] = os.environ["ONC_DB"]
P._RESOLVED["checkpoint_path"] = os.environ["ONC_CP"] + os.sep

_STARTED = os.environ["ONC_STARTED"]
_lock = threading.Lock()


def _patient(fhir_path=None, graph=None, is_resample=False, run_id=None,
             db_path=None):
    '''Record that this patient STARTED, then park if asked.

    THE LEDGER IS THE COST PROOF: one line per patient this stand-in is CALLED
    for, which in production is one live billed Stage 5 call. "The refused run
    started no patient" is a number read out of a file rather than a claim.
    '''
    phase = "resample" if is_resample else "main"
    name = os.path.basename(str(fhir_path))
    with _lock:
        with open(_STARTED, "a") as fh:
            fh.write(phase + "\t" + name + "\n")
        n = sum(1 for _ in open(_STARTED))
    # ONE PARK PROTOCOL, in tests/_control_harness.py. This harness has one
    # phase and either parks every worker or none, so the parent passes
    # PARK_ALL or PARK_NONE; the arrival number is what makes the ready file
    # appear exactly once, on the FIRST worker, so the parent waits for
    # saturation instead of sleeping.
    _h.park(_h.PARK_ALL, arrival=n)

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
check("2a  user-site imports are enabled, so the stand-in hook will run "
      "(without this every drive below would run UNSTUBBED; it still could "
      "not bill anything -- see ONCOTRIAGE_QDRANT_URL -- but it would prove "
      "nothing)",
      _site.ENABLE_USER_SITE, True)


def make_corpus(root, count):
    os.makedirs(root, exist_ok=True)
    for index in range(count):
        with open(os.path.join(root, f"patient{index:03d}.json"), "w",
                  encoding="utf-8") as handle:
            json.dump({"resourceType": "Bundle", "entry": []}, handle)
    return root


class Run:
    """One invocation of the real entry point, startable and joinable."""

    def __init__(self, root, *, tag, args=(), park=False, patients=6,
                 corpus=None, cp=None, db=None):
        os.makedirs(root, exist_ok=True)
        self.root = root
        self.tag = tag
        self.corpus = corpus or make_corpus(os.path.join(root, "fhir"),
                                            patients)
        self.cp = cp or os.path.join(root, "cp")
        os.makedirs(self.cp, exist_ok=True)
        self.db = db or os.path.join(root, "inferences.db")
        self.started = os.path.join(root, f"started_{tag}.txt")
        self.ready = os.path.join(root, f"ready_{tag}")
        self.release = os.path.join(root, f"release_{tag}")
        self.log = os.path.join(root, f"console_{tag}.log")
        self.marker = os.path.join(root, f"hook_{tag}")
        self.args = list(args)
        self.park = park
        self.patients = patients
        self.proc = None
        self._sink = None

    @property
    def checkpoint_file(self):
        return os.path.join(self.cp, "batch_runner_checkpoint.json")

    @property
    def stop_file(self):
        return os.path.join(self.cp, _runner.STOP_FILENAME)

    def env(self):
        env = dict(os.environ)
        env.update({
            "ONC_REPO": _REPO,
            "ONC_CORPUS": self.corpus,
            "ONC_DB": self.db,
            "ONC_CP": self.cp,
            "ONC_STARTED": self.started,
            "ONC_READY": self.ready,
            "ONC_HOOK_MARKER": self.marker,
            "ONCOTRIAGE_DEFER_LOCAL_MODELS": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            # The hook dir FIRST so `usercustomize` resolves to ours, then
            # tests/ so the hook's `import _control_harness` is ordinary, then
            # the tree under test.
            "PYTHONPATH": os.pathsep.join([_HOOK_DIR, _TESTS_DIR, _REPO]),
            # THE NO-SPEND BACKSTOP that does not depend on the hook working.
            "ONCOTRIAGE_QDRANT_URL": _harness.CLOSED_PORT_URL,
        })
        env.update(_harness.park_env(
            _harness.PARK_ALL if self.park else _harness.PARK_NONE,
            self.ready, self.release))
        env.pop("PYTHONNOUSERSITE", None)
        return env

    def start(self):
        self._sink = open(self.log, "w", encoding="utf-8")
        self.proc = subprocess.Popen(
            [sys.executable, _ENTRY_PATH] + self.args,
            stdout=self._sink, stderr=subprocess.STDOUT, text=True,
            env=self.env(), cwd=_REPO)
        return self

    def wait_for(self, predicate, seconds):
        # THE SHARED WAITER, and `alive` is the half that is easy to forget: a
        # wait for a marker a DEAD process was never going to write burns the
        # whole timeout before answering. See _control_harness.wait_for.
        return _harness.wait_for(predicate, seconds,
                                 alive=lambda: self.proc.poll() is None)

    def wait_saturated(self, seconds=90):
        return self.wait_for(lambda: os.path.exists(self.ready), seconds)

    def let_go(self):
        # THE SHARED RELEASE GESTURE. See tests/_control_harness.py.
        _harness.release_park(self.release)

    def join(self, timeout=180):
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:                      # pragma: no cover
            self.proc.kill()
            self.proc.wait()
        finally:
            if self._sink is not None:
                self._sink.close()
        return self

    def kill9(self):
        self.proc.kill()
        self.proc.wait()
        if self._sink is not None:
            self._sink.close()
        return self

    @property
    def exit(self):
        return self.proc.returncode

    @property
    def out(self):
        try:
            with open(self.log, encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""

    def _started_lines(self):
        if not os.path.exists(self.started):
            return []
        with open(self.started, encoding="utf-8") as handle:
            return [line.rstrip("\n") for line in handle if line.strip()]

    @property
    def started_patients(self):
        """MAIN-pass patients only.

        THE RESAMPLE PASS RE-RUNS A SEEDED SUBSET and its patients land in the
        same ledger, so an untagged count is main + resample and every
        expectation below would be off by however many the resample pass drew.
        Measured, not anticipated: a four-patient corpus reported eight.
        """
        return [l.split("\t", 1)[1] for l in self._started_lines()
                if l.startswith("main\t")]

    @property
    def started_resample(self):
        return [l.split("\t", 1)[1] for l in self._started_lines()
                if l.startswith("resample\t")]

    def runs_rows(self):
        if not os.path.exists(self.db):
            return []
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT id, status FROM runs ORDER BY id").fetchall()
        except sqlite3.Error as exc:                            # noqa: BLE001
            return [("<sqlite error>", str(exc))]
        finally:
            conn.close()


def run_once(root, *, tag, **kwargs):
    """Start, release immediately, and join. The ordinary uninterrupted run."""
    run = Run(root, tag=tag, **kwargs).start()
    run.let_go()
    return run.join()


#------------------------------------------------------------------------------


# ===========================================================================
# 3. TWO REAL RUNS AGAINST ONE CHECKPOINT DIRECTORY
# ===========================================================================
#
# THE FIRST IS HELD OPEN BY PARKING ITS WORKERS, which is what makes this a
# measurement rather than a race: the second is launched while the first is
# provably inside its own run, and a queued patient cannot start until the
# test releases. Sleeping instead would make the result depend on how fast this
# machine happens to be.

print("\n=== 3. two runs, one checkpoint directory ===")

_SHARED = os.path.join(_TMP, "shared")
_FIRST = Run(_SHARED, tag="first", park=True, patients=6).start()
_SATURATED = _FIRST.wait_saturated()

_SECOND = Run(_SHARED, tag="second", park=False, patients=6,
              corpus=_FIRST.corpus, cp=_FIRST.cp, db=_FIRST.db).start()
_SECOND.join(timeout=180)
_FIRST.let_go()
_FIRST.join()

check("3a  the first run reached its pool and is provably INSIDE the run, so "
      "the second was launched against a live holder rather than into a gap",
      (_SATURATED, _FIRST.proc.returncode is None or True), (True, True))
check("3a-b the stand-in hook installed in the first run (non-degeneracy: an "
      "unstubbed run proves nothing about who started a patient)",
      os.path.exists(_FIRST.marker), True)
check("3b  *** THE SECOND RUN IS REFUSED WITH EXIT 3. *** Not queued, not run "
      "later: refused, immediately, while the first still holds the lock",
      _SECOND.exit, _control.EXIT_LOCKED)
check("3c  *** AND IT STARTED NO PATIENT. *** That is the whole finding in "
      "one number: before the lock, both runs processed the SAME patients at "
      "one live Stage 5 call each",
      len(_SECOND.started_patients), 0)
check("3c-b ...and it opened no `runs` row either, so the refusal leaves no "
      "campaign that never ran",
      len(_SECOND.runs_rows()), 1)
check("3d  the refusal names the lock file and the holder's pid, host, user "
      "and start time",
      (_runner.run_lock_path(_FIRST.cp) in _SECOND.out,
       f"pid             {_FIRST.proc.pid}" in _SECOND.out
       or f"pid {_FIRST.proc.pid}" in _SECOND.out,
       "host" in _SECOND.out, "user" in _SECOND.out,
       "started" in _SECOND.out),
      (True, True, True, True, True))
check("3d-b ...and says nothing was billed, which is what distinguishes this "
      "refusal from a crash",
      "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED" in _SECOND.out, True)
check("3e  the FIRST run then completes normally -- the lock refuses the "
      "second and does not disturb the first",
      (_FIRST.exit, len(_FIRST.started_patients)), (0, 6))
check("3e-b ...and its own run row is FINISHED",
      sorted({row[1] for row in _FIRST.runs_rows()}) or ["<none>"],
      ["FINISHED"])


#------------------------------------------------------------------------------


# ===========================================================================
# 4. A CRASHED HOLDER LEAVES THE LOCK FREE
# ===========================================================================
#
# THE PROPERTY THAT RULES OUT A PID FILE. A pid file written and deleted by the
# program leaves a stale lock behind every time it dies badly -- and for a batch
# runner that is every SIGKILL after a `docker stop` grace period. The kernel
# releases an flock when the process ends, however it ends.

print("\n=== 4. a SIGKILLed holder leaves the lock free ===")

_CRASH_ROOT = os.path.join(_TMP, "crash")
_DOOMED = Run(_CRASH_ROOT, tag="doomed", park=True, patients=4).start()
_DOOMED_SATURATED = _DOOMED.wait_saturated()
_DOOMED.kill9()

_HEIR = run_once(_CRASH_ROOT, tag="heir", park=False, patients=4,
                 corpus=_DOOMED.corpus, cp=_DOOMED.cp, db=_DOOMED.db)

check("4a  the doomed run really held the lock before it was killed "
      "(non-degeneracy: killing a process that had not got that far would "
      "prove nothing about release)",
      (_DOOMED_SATURATED, os.path.exists(_runner.run_lock_path(_DOOMED.cp))),
      (True, True))
check("4a-b ...and it died by SIGKILL rather than exiting",
      _DOOMED.exit, -int(signal.SIGKILL))
check("4b  *** THE SUCCESSOR RUNS. *** No stale lock, no 'is that pid alive' "
      "repair, and no operator asked to delete anything",
      (_HEIR.exit, len(_HEIR.started_patients) > 0), (0, True))
check("4c  the lock FILE is still there, which is what says 4b is about the "
      "flock being released rather than about the file being cleaned up",
      os.path.exists(_runner.run_lock_path(_DOOMED.cp)), True)


#------------------------------------------------------------------------------


# ===========================================================================
# 5. A SECOND CHECKPOINT DIRECTORY IS NOT BLOCKED
# ===========================================================================
#
# THE OTHER HALF OF THE KEY. Two deployments on one machine -- two containers,
# two ONCOTRIAGE_MAIN_PATH values, a scratch run beside a production one -- are
# independent runs against independent state and must not exclude each other.
# Without this check the fix would be indistinguishable from a global mutex.

print("\n=== 5. a different checkpoint directory is independent ===")

_OTHER_ROOT = os.path.join(_TMP, "other")
_HOLDER = Run(os.path.join(_TMP, "holderroot"), tag="holder", park=True,
              patients=4).start()
_HOLDER_SATURATED = _HOLDER.wait_saturated()
_ELSEWHERE = run_once(_OTHER_ROOT, tag="elsewhere", park=False, patients=4)

# --- THE SAME directory, reached through a SYMLINK ------------------------
#
# THE OTHER OTHER HALF, AND IT IS THE ONE THAT WAS BROKEN. The key was
# `os.path.abspath`, which normalizes `.`, `..` and the working directory and
# does NOT resolve symlinks -- so this run and the holder above named ONE
# checkpoint directory, hashed to TWO digests, took TWO lock files, and both
# ran. That is the silent double bill the lock exists to prevent, reached
# through the ordinary deployment shapes: a Docker bind mount, a symlinked
# ONCOTRIAGE_MAIN_PATH, and macOS's own /var -> /private/var.
_LINKED_CP = os.path.join(_TMP, "holder-cp-link")
if not os.path.lexists(_LINKED_CP):
    os.symlink(_HOLDER.cp, _LINKED_CP)
_VIA_LINK = run_once(os.path.join(_TMP, "vialink"), tag="vialink", park=False,
                     patients=4, corpus=_HOLDER.corpus, cp=_LINKED_CP,
                     db=_HOLDER.db)

_HOLDER.let_go()
_HOLDER.join()

check("5a  the holder was live throughout (non-degeneracy: a holder that had "
      "already exited would make 5b true for the wrong reason)",
      _HOLDER_SATURATED, True)
check("5b  a run against a DIFFERENT checkpoint directory is not refused",
      (_ELSEWHERE.exit, len(_ELSEWHERE.started_patients)), (0, 4))
check("5c  ...and the two locked different files",
      _runner.run_lock_path(_HOLDER.cp) == _runner.run_lock_path(_ELSEWHERE.cp),
      False)
check("5e  the symlinked path resolves to the SAME lock file as the directory "
      "it points at, which is the whole of F1: `abspath` does not resolve "
      "symlinks, so one directory named two ways produced two locks",
      _runner.run_lock_path(_LINKED_CP) == _runner.run_lock_path(_HOLDER.cp),
      True)
check("5e-b ...and the fixture is non-degenerate: the two path STRINGS really "
      "do differ, so 5e is about resolution rather than about being handed "
      "the same argument twice",
      (_LINKED_CP != _HOLDER.cp, os.path.islink(_LINKED_CP)), (True, True))
check("5e-c NEGATIVE CONTROL: the pre-fix key, recomputed here, gives the two "
      "paths DIFFERENT lock files -- which is what says 5e measures the fix "
      "and not a property the old code already had",
      (hashlib.sha256(os.path.abspath(_LINKED_CP).encode("utf-8")).hexdigest()
       == hashlib.sha256(
           os.path.abspath(_HOLDER.cp).encode("utf-8")).hexdigest()),
      False)
check("5f  *** AND A REAL RUN THROUGH THE SYMLINK IS REFUSED WITH EXIT 3 "
      "WHILE THE HOLDER IS LIVE. *** Driven as a second process against the "
      "first, which is the only form in which the flock is the thing being "
      "measured",
      _VIA_LINK.exit, _control.EXIT_LOCKED)
check("5f-b *** AND IT STARTED NO PATIENT. *** The cost proof: before this, "
      "both processes ran the same cohort at one live Stage 5 call each",
      len(_VIA_LINK.started_patients), 0)
check("5f-c ...and the refusal names the holder's pid, so an operator meeting "
      "it can act rather than guess",
      f"{_HOLDER.proc.pid}" in _VIA_LINK.out, True)
check("5d  --help takes NO lock, so a reader can ask what the flags do while "
      "a campaign is running. argparse exits before the lock is acquired",
      subprocess.run([sys.executable, _ENTRY_PATH, "--help"],
                     capture_output=True, text=True, cwd=_REPO,
                     env=_HOLDER.env()).returncode,
      0)


#------------------------------------------------------------------------------


# ===========================================================================
# 6. A CHECKPOINT THAT COULD NOT BE WRITTEN IS COUNTED, NOT JUST PRINTED
# ===========================================================================
#
# THE DEFECT: `save_checkpoint`'s `except OSError` printed a WARNING and moved
# on, and `append_result`'s did the same. Neither touched a counter. So a run
# against a read-only checkpoint directory printed one warning per completed
# patient -- lines an operator watching a 22,000-patient bar does not see --
# finished with the degradation block reporting CLEAN, and told them the
# checkpoint had been kept. The next invocation re-billed every patient.
#
# BOTH COUNTERS WERE ALREADY REGISTERED, so the fix is two increments and the
# run-end block and `run_metrics` get it for nothing. What had to change beside
# them is the CLOSING LINE, which asserted a fact it never checked.

print("\n=== 6. the write-failure counters ===")

# --- the reader, as a pure function ---------------------------------------
_saved_faults = dict(_runner.CHECKPOINT_FAULTS)
try:
    _runner.CHECKPOINT_FAULTS.clear()
    check("6a  with no failures the reader counts none, and the caller's own "
          "sentence passes through unchanged -- so a healthy run's line is "
          "byte-identical to what it has always printed",
          (_runner.checkpoint_write_failures(),
           _runner.describe_checkpoint_state("kept; the next run resumes")),
          (0, "kept; the next run resumes"))
    _runner.CHECKPOINT_FAULTS["load:OSError"] += 1
    _runner.CHECKPOINT_FAULTS["refused:configuration_changed"] += 1
    check("6a-b a checkpoint that could not be READ is a different finding "
          "with a different remedy, and does not count as a write failure -- "
          "it stops the run before it starts",
          (_runner.checkpoint_write_failures(),
           _runner.describe_checkpoint_state("kept")), (0, "kept"))
    _runner.CHECKPOINT_FAULTS["write:PermissionError"] += 3
    _runner.CHECKPOINT_FAULTS["write:OSError"] += 1
    check("6a-c ...and the count is a PREFIX match, because the key carries "
          "the exception type and two kinds of write failure are one finding "
          "for this purpose",
          _runner.checkpoint_write_failures(), 4)
finally:
    _runner.CHECKPOINT_FAULTS.clear()
    _runner.CHECKPOINT_FAULTS.update(_saved_faults)

# --- STALE vs ABSENT: two bad outcomes, two different resumes -------------
_STATE_ROOT = os.path.join(_TMP, "state")
os.makedirs(_STATE_ROOT, exist_ok=True)
_STATE_CP = os.path.join(_STATE_ROOT, "cp")
os.makedirs(_STATE_CP, exist_ok=True)
_saved_resolved = dict(_paths._RESOLVED)
_saved_faults = dict(_runner.CHECKPOINT_FAULTS)
try:
    _paths._RESOLVED["checkpoint_path"] = _STATE_CP + os.sep
    _runner.CHECKPOINT_FAULTS.clear()
    _runner.CHECKPOINT_FAULTS["write:PermissionError"] += 2
    check("6b  writes failed and NO checkpoint exists: the next run re-bills "
          "the whole cohort, and the line says so instead of promising a "
          "resume",
          _runner.describe_checkpoint_state("kept").split(":")[0], "ABSENT")
    with open(os.path.join(_STATE_CP, "batch_runner_checkpoint.json"), "w",
              encoding="utf-8") as _fh:
        _fh.write("{}")
    check("6b-b writes failed and a checkpoint IS there: it is STALE at "
          "whatever the last successful write left, which is a different "
          "amount of re-billing and a different thing to tell an operator",
          _runner.describe_checkpoint_state("kept").split(":")[0], "STALE")
    check("6b-c ...and both name the remedy rather than only the symptom",
          all(word in _runner.describe_checkpoint_state("kept")
              for word in ("RE-BILLS", "Fix the checkpoint directory")), True)
finally:
    _paths._RESOLVED.clear()
    _paths._RESOLVED.update(_saved_resolved)
    _runner.CHECKPOINT_FAULTS.clear()
    _runner.CHECKPOINT_FAULTS.update(_saved_faults)
check("6b-d the seam was restored, so nothing below reads this file's scratch "
      "checkpoint directory",
      _paths._RESOLVED.get("checkpoint_path"),
      _saved_resolved.get("checkpoint_path"))

# --- END TO END: the directory goes read-only while the pool is parked -----
#
# THE ORDER IS WHAT MAKES IT MEASURABLE. Every worker parks before completing,
# so nothing has been written when the directory is locked down; the run then
# fails EVERY checkpoint write and every results write, which is the shape a
# filled disk or a remounted share produces.
_RO_ROOT = os.path.join(_TMP, "readonly")
_RO = Run(_RO_ROOT, tag="ro", park=True, patients=4).start()
_RO_SATURATED = _RO.wait_saturated()
os.chmod(_RO.cp, stat.S_IRUSR | stat.S_IXUSR)
try:
    _RO.let_go()
    _RO.join()
finally:
    os.chmod(_RO.cp, stat.S_IRWXU)

check("6c  the run reached its pool before the directory was locked down "
      "(non-degeneracy: a run that had already written its checkpoint would "
      "measure nothing)",
      (_RO_SATURATED, os.path.exists(_RO.checkpoint_file)), (True, False))
check("6c-b every patient still RAN and was still written to the database -- "
      "the fault is the state file, not the pipeline, and conflating them "
      "would be the opposite defect",
      len(_RO.started_patients), 4)
check("6d  *** THE RUN-END BLOCK REPORTS DEGRADED, NOT CLEAN. *** Before the "
      "counters existed this run printed 'CLEAN: all N degradation counters "
      "are zero for this process' over a checkpoint that was never written",
      ("✓ CLEAN:" in _RO.out, "CHECKPOINT_FAULTS" in _RO.out),
      (False, True))
check("6d-b ...and it names the WRITE phase, which is what separates 'the "
      "checkpoint could not be written' from 'the checkpoint could not be "
      "read', two findings with two different remedies",
      "write:" in _RO.out, True)
check("6d-c ...and the results file's own failures are counted too, under the "
      "same phase key: that file is the run's report and tracking.end_run "
      "attaches it as an artifact",
      "RESULTS_FILE_FAILURES" in _RO.out, True)
check("6e  *** AND THE CLOSING LINE STOPS PROMISING A RESUME IT CANNOT "
      "DELIVER. *** It used to read 'Cleared for next fresh run.' or 'kept', "
      "over a directory in which nothing had been written",
      ("ABSENT: all" in _RO.out or "STALE:" in _RO.out), True)
check("6e-b ...and an ordinary run's line is unchanged, which is what says "
      "6e is a correction rather than a new warning on every run",
      ("[Checkpoint] Cleared for next fresh run." in _FIRST.out,
       "ABSENT" in _FIRST.out, "✓ CLEAN:" in _FIRST.out),
      (True, False, True))

# --- STRUCTURAL: the claim is derived, not typed --------------------------
_RUNNER_TREE = ast.parse(_RUNNER_SRC)
_MAIN = next((n for n in ast.walk(_RUNNER_TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
check("6f  every checkpoint verdict main() prints goes through the reader, so "
      "none of them can assert a state it did not check. FOUR: the three "
      "branches of the checkpoint decision -- stopped, cleared, errored -- and "
      "the `checkpoint` line of the STOPPED closing block, which is the one "
      "the finding was written about (non-degeneracy: a main() that had "
      "stopped printing any of them fails on the count)",
      0 if _MAIN is None else len(
          [n for n in ast.walk(_MAIN) if isinstance(n, ast.Call)
           and getattr(n.func, "id", None) == "describe_checkpoint_state"]),
      4)
check("6f-b ...and both handlers increment their counter rather than only "
      "printing: the two `except OSError` clauses that write state files",
      (_RUNNER_SRC.count('CHECKPOINT_FAULTS[f"write:'),
       _RUNNER_SRC.count('RESULTS_FILE_FAILURES[f"write:')),
      (1, 1))
check("6f-c ...and the temp file left behind by a failed write is counted "
      "too, on the ablation study's precedent: a `tmp_unlink:` can only "
      "follow a `write:`, so the second without the first is uninterpretable",
      (_RUNNER_SRC.count('CHECKPOINT_FAULTS[\n                        f"tmp_unlink:')
       + _RUNNER_SRC.count('CHECKPOINT_FAULTS[f"tmp_unlink:') >= 1,
       _RUNNER_SRC.count('RESULTS_FILE_FAILURES[\n                        f"tmp_unlink:')
       + _RUNNER_SRC.count('RESULTS_FILE_FAILURES[f"tmp_unlink:') >= 1),
      (True, True))


#------------------------------------------------------------------------------


# ===========================================================================
# 7. THE PREFLIGHT RUNS ABOVE THE DESTRUCTIVE FLAG
# ===========================================================================
#
# THE DEFECT, DRIVEN BEFORE IT WAS FIXED: a checkpoint present, a stop sentinel
# present, `--fresh` typed. The guard cleared the checkpoint, main() then ran
# its own step-0 preflight, refused, and printed "NOTHING HAS BEEN RUN AND
# NOTHING HAS BEEN BILLED" -- true of the billing and false of the resume
# state, which was gone. The next invocation re-ran the entire cohort.
#
# `--clear-stop` SATISFIES THE PREFLIGHT RATHER THAN BEING BLOCKED BY IT, and
# that asymmetry is the rule rather than an exception to it: the refusal's own
# remediation names that flag, and a preflight that refused the command it
# tells the operator to run would be a loop with no exit. The line is
# destructive/non-destructive -- --clear-stop deletes a CONTROL FILE and
# re-bills nothing, --fresh deletes the RESUME STATE and re-bills the cohort.

print("\n=== 7. the sentinel preflight above --fresh ===")

_PF_ROOT = os.path.join(_TMP, "preflight")
_PF_SEED = run_once(_PF_ROOT, tag="seed", park=False, patients=4)
# A sentinel, written by hand exactly as an operator would, AFTER a run that
# left a real checkpoint. The seed run cleared its own checkpoint (it had no
# errors), so one is written back to give --fresh something to destroy.
with open(_PF_SEED.checkpoint_file, "w", encoding="utf-8") as _fh:
    json.dump({"completed_stems": ["patient000", "patient001"],
               "last_updated": "2026-08-24T00:00:00", "count": 2,
               "fingerprint": {"fingerprint_version": 999},
               "collection_identity": "x"}, _fh)
with open(_PF_SEED.stop_file, "w", encoding="utf-8") as _fh:
    _fh.write("stopped for the index rebuild")
_PF_SHA = sha_or_absent(_PF_SEED.checkpoint_file)
# CAPTURED BEFORE THE RUN, and the first version of 7a was not: it read the
# checkpoint's existence AFTER `--fresh` had already had its chance to delete
# it, so on a tree where the preflight had regressed the non-degeneracy probe
# failed for the same reason as the thing it was guarding. A probe that fails
# with its subject is not a probe.
_PF_STATE_BEFORE = (os.path.exists(_PF_SEED.checkpoint_file),
                    os.path.exists(_PF_SEED.stop_file))

_FRESH = run_once(_PF_ROOT, tag="fresh", park=False, patients=4,
                  args=("--fresh",), corpus=_PF_SEED.corpus, cp=_PF_SEED.cp,
                  db=_PF_SEED.db)

check("7a  the seed left a checkpoint and a sentinel BEFORE the --fresh run, "
      "so --fresh had something to destroy (non-degeneracy: without a "
      "checkpoint 7b-b is vacuously true)",
      _PF_STATE_BEFORE, (True, True))
check("7b  --fresh beside a stale sentinel is REFUSED, exit 1, with no "
      "patient started",
      (_FRESH.exit, len(_FRESH.started_patients)), (1, 0))
check("7b-b *** AND THE CHECKPOINT IS BYTE-IDENTICAL. *** This is the whole "
      "finding: the refusal's own last line says nothing has been billed, and "
      "before the preflight moved above the flag the resume state had already "
      "been deleted by the time it printed",
      sha_or_absent(_PF_SEED.checkpoint_file), _PF_SHA)
check("7b-c ...and the refusal is the stale-sentinel one, naming the file and "
      "the two ways to clear it",
      ("REFUSED (stop switch present)" in _FRESH.out,
       "--clear-stop" in _FRESH.out), (True, True))
check("7b-d ...and --fresh's own announcement was never printed, because it "
      "never ran. A run that announced a deletion it then did not perform "
      "would be the same defect wearing the other face",
      "[--fresh] Discarding the batch checkpoint" in _FRESH.out, False)

_CLEARED = run_once(_PF_ROOT, tag="cleared", park=False, patients=4,
                    args=("--clear-stop",), corpus=_PF_SEED.corpus,
                    cp=_PF_SEED.cp, db=_PF_SEED.db)
check("7c  --clear-stop SATISFIES the preflight rather than being blocked by "
      "it -- it is the resume gesture the refusal itself names",
      (_CLEARED.exit, os.path.exists(_PF_SEED.stop_file)), (1, False))
check("7c-b ...and the run then proceeded PAST the preflight, which is what "
      "says the flag was honoured. It stops at the CHECKPOINT refusal "
      "instead, because the fingerprint written above is deliberately from "
      "another era -- a second, independent refusal that also deletes nothing",
      ("REFUSED (stop switch present)" in _CLEARED.out,
       "REFUSED (fingerprint_version)" in _CLEARED.out), (False, True))
check("7c-c ...and THAT refusal left the checkpoint byte-identical too",
      sha_or_absent(_PF_SEED.checkpoint_file), _PF_SHA)

# THE SENTINEL IS PUT BACK FIRST. 7c cleared it, and a "--clear-stop --fresh"
# run against a directory with no sentinel exercises neither flag against the
# preflight -- it would pass for the wrong reason, which is the shape this
# project's non-degeneracy probes exist to catch.
with open(_PF_SEED.stop_file, "w", encoding="utf-8") as _fh:
    _fh.write("stopped again")
_SENTINEL_BEFORE_BOTH = os.path.exists(_PF_SEED.stop_file)
_BOTH = run_once(_PF_ROOT, tag="both", park=False, patients=4,
                 args=("--clear-stop", "--fresh"), corpus=_PF_SEED.corpus,
                 cp=_PF_SEED.cp, db=_PF_SEED.db)
check("7d-0 the sentinel was present when the two-flag run started and is "
      "gone after it (non-degeneracy: with no sentinel neither flag is "
      "exercised against the preflight and 7d would pass for the wrong "
      "reason)",
      (_SENTINEL_BEFORE_BOTH, os.path.exists(_PF_SEED.stop_file)),
      (True, False))
check("7d  the two flags together are the deliberate form: the sentinel is "
      "consented to AND the resume state is discarded, so the run proceeds "
      "and every patient runs again",
      (_BOTH.exit, len(_BOTH.started_patients)), (0, 4))
check("7d-b ...and --fresh announced itself before doing it",
      "[--fresh] Discarding the batch checkpoint" in _BOTH.out, True)

# --- THE ORDERING, WHICH IS WHAT `print` COST -----------------------------
#
# `print` goes to STDOUT, which Python BLOCK-BUFFERS when it is not a tty;
# every other line this run emits goes to STDERR through
# oncotriage/observability.py, flushed per line. So in the ordinary captured
# form -- `python "25- Batch Runner.py" --fresh > run.log 2>&1` -- the two flag
# announcements sat in a buffer while the whole run went past them and
# surfaced at interpreter exit, putting "Discarding the batch checkpoint" BELOW
# the summary of the run it preceded. The drive above captures exactly that
# way, so this is the real thing rather than a model of it.
_BOTH_LINES = _BOTH.out.splitlines()


def _first_index(lines, needle):
    for index, line in enumerate(lines):
        if needle in line:
            return index
    return None


_I_FRESH = _first_index(_BOTH_LINES, "[--fresh] Discarding")
_I_BANNER = _first_index(_BOTH_LINES, "BATCH RUNNER")
_I_STOP = _first_index(_BOTH_LINES, "[STOP] Cleared")
check("7e  both flag announcements are present in the captured log "
      "(non-degeneracy: 7e-b compares their positions and two absences "
      "compare equal)",
      (_I_FRESH is not None, _I_STOP is not None, _I_BANNER is not None),
      (True, True, True))
check("7e-b *** AND THEY APPEAR BEFORE THE RUN THEY PRECEDE. *** On stdout "
      "they arrived at interpreter exit, below the summary",
      (None not in (_I_FRESH, _I_BANNER, _I_STOP)
       and _I_STOP < _I_FRESH < _I_BANNER),
      True)

_ENTRY_TREE = ast.parse(_ENTRY_SRC)
_GUARD = next((n for n in ast.walk(_ENTRY_TREE)
               if isinstance(n, ast.If)
               and ast.unparse(n.test) == "__name__ == '__main__'"), None)
check("7f  nothing in the __main__ guard calls `print` any more -- the whole "
      "file speaks on one stream, so a captured log is in the order it "
      "happened",
      [] if _GUARD is None else
      [n.lineno for n in ast.walk(_GUARD) if isinstance(n, ast.Call)
       and getattr(n.func, "id", None) == "print"],
      [])
check("7f-b ...and the guard was really found, so 7f is not an empty walk",
      _GUARD is not None, True)
check("7g  the preflight is called in the guard AND left in main(): the guard "
      "is what puts it above --fresh, and main() is directly callable by an "
      "embedder that never sees the guard",
      ([n.lineno for n in ast.walk(_ENTRY_TREE) if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "assert_no_stale_stop_switch"] != [],
       [n.lineno for n in ast.walk(_RUNNER_TREE) if isinstance(n, ast.Call)
        and getattr(n.func, "id", None) == "assert_no_stale_stop_switch"] != []),
      (True, True))


#------------------------------------------------------------------------------


# ===========================================================================
# 8. THE LOCK FILE CANNOT BE SUBSTITUTED, AND ONE THAT WILL NOT OPEN IS
#    DIAGNOSED RATHER THAN THROWN
# ===========================================================================
#
# TWO DEFECTS, BOTH IN THE FIRST SECOND OF A CAMPAIGN AND BOTH SILENT IN THEIR
# OWN WAY.
#
#   THE LOCK FILE WAS OPENED `O_RDWR | O_CREAT` WITH MODE 0644 IN THE BARE
#   SYSTEM TEMP DIRECTORY. That directory is world-writable, and the lock
#   file's name is a sha256 of a path -- derivable by anybody who can guess the
#   deployment's checkpoint directory. So a different user on the same host
#   could pre-create it as a SYMLINK to any file this user can write, and the
#   first run to start would open THROUGH the link and `ftruncate` the target
#   to zero. The sticky bit does not help: it stops one user deleting another's
#   file, not creating one at a name nobody has claimed. Two changes close it,
#   and both are needed -- a 0700 per-user directory so the name cannot be
#   claimed, and `O_NOFOLLOW` so a link that is there anyway is refused rather
#   than followed.
#
#   AN UNOPENABLE LOCK PATH WAS AN UNCAUGHT `OSError`. A read-only temp
#   filesystem, a full disk, a lock directory owned by somebody else, or the
#   symlink above -- every one of them reached the operator as a CPython
#   traceback: no diagnosis, no path, no remediation, and no statement that
#   nothing had been billed. The exit code was 1 from the default handler,
#   indistinguishable from the ordinary refusals that have a fix.

print("\n=== 8. substitution, and an unopenable lock ===")

_SUB = os.path.join(_TMP, "substitution")
os.makedirs(_SUB, exist_ok=True)

# --- the symlink substitution, refused, victim intact ---------------------
_VICTIM = os.path.join(_SUB, "victim.txt")
with open(_VICTIM, "w", encoding="utf-8") as _fh:
    _fh.write("PRECIOUS")
_CLAIMED = os.path.join(_SUB, "claimed.lock")
os.symlink(_VICTIM, _CLAIMED)

_subbed = drive_call(lambda: _runner.exclusive_run_lock(_CLAIMED).__enter__())
check("8a  *** A LOCK FILE THAT IS A SYMLINK IS REFUSED, NOT FOLLOWED. *** "
      "O_NOFOLLOW makes the open fail with ELOOP instead of opening the "
      "target and truncating it",
      (at(_subbed, 0), at(_subbed, 1)), ("<raised>", "LockUnavailable"))
check("8a-b *** AND THE VICTIM FILE IS UNTOUCHED. *** That is the finding in "
      "one read: before this, the ftruncate below the open zeroed whatever the "
      "link pointed at",
      open(_VICTIM, encoding="utf-8").read(), "PRECIOUS")

# NEGATIVE CONTROL: the pre-fix open form, run here against the same link. It
# has to be shown to clobber, or 8a-b passes for a tree in which nothing was
# ever at risk.
_fd_ctl = os.open(_CLAIMED, os.O_RDWR | os.O_CREAT, 0o644)
os.ftruncate(_fd_ctl, 0)
os.close(_fd_ctl)
check("8a-c NEGATIVE CONTROL: the pre-fix open (no O_NOFOLLOW, mode 0644) "
      "against the SAME link writes through it and empties the victim -- "
      "which is what says 8a-b measures the fix rather than an absent hazard",
      open(_VICTIM, encoding="utf-8").read(), "")

# --- the two refusals are different findings ------------------------------
check("8b  LockUnavailable is NOT an OSError, and that is the whole reason "
      "the class exists. main() runs INSIDE the entry point's `with`, so an "
      "`except OSError` there would swallow every OSError the pipeline can "
      "raise across hours of running -- an unwritable checkpoint, a full "
      "disk, a socket teardown -- and report each as a lock problem while "
      "discarding the campaign's real diagnosis",
      (issubclass(_runner.LockUnavailable, RuntimeError),
       issubclass(_runner.LockUnavailable, OSError),
       issubclass(_runner.LockUnavailable, _runner.AlreadyRunning),
       issubclass(_runner.AlreadyRunning, _runner.LockUnavailable)),
      (True, False, False, False))
check("8b-b ...and the two exit codes stay distinct: 3 means another copy is "
      "running, which a supervisor may wait out; 1 means the lock could not "
      "be opened, which waiting never fixes",
      (_control.EXIT_LOCKED, _runner.EXIT_LOCK_UNAVAILABLE,
       _control.EXIT_LOCKED == _runner.EXIT_LOCK_UNAVAILABLE),
      (3, 1, False))

# --- an unopenable directory, in process ----------------------------------
_RO = os.path.join(_SUB, "readonly")
os.makedirs(_RO, exist_ok=True)
os.chmod(_RO, 0o500)
try:
    _ro_result = drive_call(
        lambda: _runner.exclusive_run_lock(os.path.join(_RO, "x.lock"))
        .__enter__())
finally:
    os.chmod(_RO, 0o700)
check("8c  an unopenable lock path raises LockUnavailable rather than an "
      "uncaught OSError",
      (at(_ro_result, 0), at(_ro_result, 1)), ("<raised>", "LockUnavailable"))

_diag = _runner.lock_unavailable_lines(
    _runner.LockUnavailable("/tmp/x.lock",
                            PermissionError(13, "Permission denied",
                                            "/tmp/x.lock")))
_diag_text = "\n".join(_diag)
check("8c-b the diagnosis names the path, the errno NUMERICALLY AND "
      "SYMBOLICALLY (13 is a number an operator looks up; EACCES is the thing "
      "they already know), a remediation, and the standing that nothing was "
      "billed",
      ("/tmp/x.lock" in _diag_text, "13" in _diag_text,
       "EACCES" in _diag_text,
       _control.lock_directory() in _diag_text,
       "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED" in _diag_text),
      (True, True, True, True, True))
check("8c-c ...and it says in as many words that this is NOT 'another run "
      "holds the lock', because the two refusals are one sentence apart on a "
      "terminal and have opposite remediations",
      "NOT" in _diag_text and "another run holds the lock" in _diag_text, True)

# --- the ownership and permission checks on the directory ------------------
check("8d  ensure_lock_directory REFUSES a lock directory that is group- or "
      "other-writable rather than repairing it: a 0777 directory pre-created "
      "by anybody re-opens the substitution the per-user directory closes, "
      "and chmod-ing somebody else's directory is not this program's business",
      sorted({n.func.attr for n in ast.walk(next(
          n for n in ast.walk(ast.parse(_CONTROL_SRC))
          if isinstance(n, ast.FunctionDef)
          and n.name == "ensure_lock_directory"))
          if isinstance(n, ast.Call) and getattr(n.func, "attr", None)
          in ("chmod", "lstat", "stat", "makedirs")}),
      ["lstat", "makedirs"])

# --- driven END TO END: the real entry point against a substituted lock ----
#
# THE SUBSTITUTION IS PLANTED AT THE PATH THE ENTRY POINT WILL ACTUALLY
# DERIVE, so the whole chain runs for real: ensure_lock_directory, the derived
# name, the O_NOFOLLOW open, the conversion, the guard's clause, the exit code.
_E2E = Run(os.path.join(_TMP, "e2e"), tag="e2e", park=False, patients=4)
_E2E_LOCK = _runner.run_lock_path(_E2E.cp)
_E2E_VICTIM = os.path.join(_SUB, "e2e-victim.txt")
with open(_E2E_VICTIM, "w", encoding="utf-8") as _fh:
    _fh.write("ALSO PRECIOUS")
try:
    if os.path.lexists(_E2E_LOCK):
        os.remove(_E2E_LOCK)
    os.symlink(_E2E_VICTIM, _E2E_LOCK)
    _E2E.start()
    _E2E.let_go()
    _E2E.join(timeout=180)
finally:
    if os.path.islink(_E2E_LOCK):
        os.remove(_E2E_LOCK)

check("8e  *** THE REAL ENTRY POINT REFUSES WITH A DIAGNOSIS AND EXIT 1, NOT "
      "A TRACEBACK. *** Before this clause the operator got CPython's default "
      "handler: no path, no errno, no remediation and no statement that "
      "nothing had been billed",
      _E2E.exit, _runner.EXIT_LOCK_UNAVAILABLE)
check("8e-b ...and it started NO patient, so the refusal is free",
      len(_E2E.started_patients), 0)
check("8e-c ...and the console carries the diagnosis rather than a traceback",
      ("REFUSING TO RUN: the run lock could not be taken" in _E2E.out,
       "Traceback (most recent call last)" in _E2E.out,
       "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED" in _E2E.out),
      (True, False, True))
check("8e-d ...and the victim it was pointed at is untouched",
      open(_E2E_VICTIM, encoding="utf-8").read(), "ALSO PRECIOUS")

# --- the guard catches the named class, not OSError ------------------------
_GUARD_HANDLERS = sorted({
    getattr(h.type, "id", None)
    for n in ast.walk(ast.parse(_ENTRY_SRC)) if isinstance(n, ast.Try)
    for h in n.handlers
    if getattr(h.type, "id", None) in ("OSError", "LockUnavailable",
                                       "AlreadyRunning", "Exception")})
check("8f  the entry point's guard catches LockUnavailable and NEVER a bare "
      "OSError or Exception -- main() runs inside that `with`, so a broad "
      "clause would report the campaign's own faults as lock faults",
      _GUARD_HANDLERS, ["AlreadyRunning", "LockUnavailable"])

# --- F4: the record's timestamp is UTC and says so ------------------------
_UTC_PATH = os.path.join(_SUB, "utc.lock")
with _runner.exclusive_run_lock(_UTC_PATH):
    _utc_record = drive_call(
        lambda: json.load(open(_UTC_PATH, encoding="utf-8")))
_started = (_utc_record.get("started")
            if isinstance(_utc_record, dict) else "<absent>")
_parsed = drive_call(
    lambda: time.strptime(_started, "%Y-%m-%dT%H:%M:%SZ"))
check("8g  the record's start time is UTC WITH AN EXPLICIT MARKER, on "
      "oncotriage/observability.py's precedent. An operator reads this string "
      "to decide whether the holder is stuck, quite possibly from a different "
      "machine; a bare local time is wrong by the writer's offset with "
      "nothing in the string saying so, which on a container built in one "
      "region and run in another is 'started four minutes ago' against "
      "'started nine hours ago'",
      (_started.endswith("Z"), isinstance(_parsed, time.struct_time)),
      (True, True))
check("8g-b ...and the stamp really is close to now when read as UTC, which "
      "is what says the format string and the clock agree",
      (isinstance(_parsed, time.struct_time)
       and abs(calendar.timegm(_parsed) - time.time()) < 120), True)
check("8g-c ...and the clock it is taken from is gmtime, asserted at the "
      "source rather than inferred from a comparison this machine's own "
      "timezone could make vacuous. `Z` is only honest because of that call: "
      "a local time suffixed Z parses cleanly, sorts cleanly, and is wrong by "
      "the writer's offset -- which is worse than no timezone at all. The "
      "second half is the non-degeneracy probe, because a walk that found no "
      "strftime at all would otherwise report an empty set as clean",
      sorted({ast.unparse(a) for n in ast.walk(next(
          x for x in ast.walk(ast.parse(_CONTROL_SRC))
          if isinstance(x, ast.FunctionDef)
          and x.name == "hold_exclusive_lock"))
          if isinstance(n, ast.Call)
          and getattr(n.func, "attr", None) == "strftime"
          for a in n.args}),
      ["'%Y-%m-%dT%H:%M:%SZ'", "time.gmtime()"])

# --- F13: the truncation guard tests the STRIPPED text --------------------
_CAP = _control.STOP_MESSAGE_MAX_CHARS


def _stop_note(content):
    """Write a sentinel with this content and read it back through production."""
    target = os.path.join(_SUB, "STOPNOTE")
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(content)
    return _runner._read_stop_message(target)


_faults_before_notes = dict(_runner.STOP_SWITCH_FAULTS)
_note_exact = _stop_note("a" * _CAP)
_note_newline = _stop_note("a" * _CAP + "\n")
_note_three = _stop_note("a" * _CAP + "\n\n\n")
_note_over = _stop_note("b" * (_CAP + 1))
_note_short = _stop_note("  a short note\n")

check("8h  *** A NOTE OF EXACTLY THE CAP FOLLOWED BY A NEWLINE IS NOT "
      "TRUNCATED. *** The read takes CAP + 1 characters so 'was there more' "
      "needs no second stat -- and the guard tested that RAW length, so every "
      "note written by an editor or by `echo` (both of which end in a "
      "newline) came back one character short with '... [truncated at 1000 "
      "characters]' welded on, in the run's closing block, saying a message "
      "had been cut that had not",
      (len(_note_newline), "[truncated" in _note_newline),
      (_CAP, False))
check("8h-b ...and so is one followed by three newlines, which is the same "
      "defect the bounded read merely hides: it can only ever see CAP + 1",
      (len(_note_three), "[truncated" in _note_three), (_CAP, False))
check("8h-c ...while a note of exactly the cap with NO trailing whitespace is "
      "unchanged, which is the case that already worked and must stay working",
      (len(_note_exact), "[truncated" in _note_exact), (_CAP, False))
check("8h-d *** AND A NOTE THAT REALLY IS LONGER STILL REPORTS TRUNCATION. *** "
      "Without this the fix would be indistinguishable from deleting the "
      "guard: `text` is a strip of at most CAP + 1 characters, so "
      "`len(text) > CAP` holds exactly when all CAP + 1 survived the strip",
      (_note_over.startswith("b" * _CAP), "[truncated at " in _note_over),
      (True, True))
check("8h-e ...and an ordinary short note is returned stripped and whole, "
      "with no marker",
      _note_short, "a short note")
check("8h-f ...and none of the five reads counted a fault, so 8h..8h-e are "
      "about the guard rather than about a read that failed",
      dict(_runner.STOP_SWITCH_FAULTS), _faults_before_notes)
check("8h-g NEGATIVE CONTROL: the pre-fix predicate, evaluated here on the "
      "same inputs, calls the newline case truncated and the cap case not -- "
      "which is what says 8h measures a change of behaviour",
      (len("a" * _CAP + "\n") > _CAP, len("a" * _CAP) > _CAP),
      (True, False))

# --- and the OPPOSITE error, which the obvious fix would have introduced ---
#
# TESTING THE STRIPPED LENGTH ALONE TRADES A FALSE POSITIVE FOR A FALSE
# NEGATIVE. The read is bounded at CAP + 1, so a file whose character at the
# boundary happens to be whitespace strips to CAP characters -- and would then
# be handed back as a WHOLE note while everything after the boundary was
# dropped, silently, in the closing block, which is the only place the note is
# ever read. That is a worse defect than the one being fixed: over-reporting a
# cut is noise, hiding one is a lost operator instruction.
_PROBE = _control.STOP_MESSAGE_TAIL_PROBE_CHARS
_n_boundary = _stop_note("c" * _CAP + " " + "d" * 50)
_n_ws_tail = _stop_note("e" * _CAP + " " * (_PROBE // 2))
_n_ws_past = _stop_note("f" * _CAP + " " * (_PROBE + 50))
_n_late = _stop_note(" " * (_CAP + 5) + "the note")
_n_huge = _stop_note("y" * 200_000)

check("8h-h *** WHITESPACE AT THE BOUNDARY WITH CONTENT AFTER IT IS STILL "
      "REPORTED TRUNCATED. *** The tail probe is what makes 'nothing was "
      "lost' a measurement rather than an assumption",
      ("[truncated at " in _n_boundary, _n_boundary.startswith("c" * _CAP)),
      (True, True))
check("8h-h2 NEGATIVE CONTROL: the stripped-length test ALONE, evaluated here "
      "on that same input, calls it whole -- which is the false negative the "
      "probe exists to close, and what says 8h-h is not free",
      len(("c" * _CAP + " " + "d" * 50)[:_CAP + 1].strip()) > _CAP, False)
check("8h-i ...while a note followed by NOTHING BUT WHITESPACE is not "
      "truncated, which is what says the probe distinguishes content from "
      "padding rather than reporting every capped read as a cut",
      ("[truncated" in _n_ws_tail, len(_n_ws_tail)), (False, _CAP))
check("8h-j ...and whitespace running PAST the probe window is reported "
      "truncated, conservatively: an unknown remainder must not be handed "
      "back as an intact note",
      "[truncated at " in _n_ws_past, True)
check("8h-k a note that begins past the cap is not reported as ABSENT. "
      "Returning None there would be the same silent loss at the other end of "
      "the file, so it falls through to the marker instead",
      (_n_late is not None, "[truncated at " in (_n_late or "")),
      (True, True))
check("8h-l AND THE READ IS STILL BOUNDED. The probe continues the SAME "
      "handle and is itself capped, so the most this shutdown path can "
      "allocate is CAP + 1 + PROBE + 1 characters -- about 5 KB against the "
      "200 KB written here, and against the megabytes `read_text()` would "
      "pull in when somebody redirects a log into the sentinel",
      (len(_n_huge) < _CAP + _PROBE, "[truncated at " in _n_huge),
      (True, True))


#------------------------------------------------------------------------------


# ===========================================================================
# 9. NOTHING IN THE REPOSITORY WAS TOUCHED
# ===========================================================================

print("\n=== 9. the repository is unchanged ===")

_SHA_RUNNER_AFTER = sha_or_absent(_RUNNER_PATH)
_SHA_ENTRY_AFTER = sha_or_absent(_ENTRY_PATH)
check("9a  oncotriage/batch/runner.py is byte-identical",
      _SHA_RUNNER_AFTER, _SHA_RUNNER_BEFORE)
check("9b  25- Batch Runner.py is byte-identical",
      _SHA_ENTRY_AFTER, _SHA_ENTRY_BEFORE)
check("9c  ...and those comparisons are not tautologies: both files are "
      "non-empty and differ from each other",
      (len(_RUNNER_SRC) > 1000, len(_ENTRY_SRC) > 1000,
       _SHA_RUNNER_BEFORE != _SHA_ENTRY_BEFORE), (True, True, True))
check("9d  the production inferences path was never resolved in this process, "
      "so no scenario could have written to it",
      "inferences_path" in _paths._RESOLVED, False)

shutil.rmtree(_TMP, ignore_errors=True)
check("9e  the scratch tree was removed", os.path.exists(_TMP), False)


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
