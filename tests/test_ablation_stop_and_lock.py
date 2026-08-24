# Ablation Study: The Operator Controls
######################################

"""The ablation study's run lock, stop switch, executor lifecycle and signals.

WHAT THIS COVERS AND WHY IT IS A NEW FILE. `oncotriage/ablation/study.py` is the
harness the three-arm migration measurement runs through, and until the
operator-control pass it had none of the controls `25- Batch Runner.py` has: no
run lock, no stop switch, a `with ThreadPoolExecutor` that DRAINED the rest of a
configuration on any exception, a `KeyboardInterrupt` that was caught and NOT
re-raised so the study carried on to the next configuration, and no SIGTERM
disposition at all. Five gaps, each of which costs live Stage 5 calls, and not
one of them had a test.

It is separate from `tests/test_ablation_db_isolation.py` on that file's own
precedent (pass 20f-1 wrote four new files for four fixes): that one's subject is
`--db`, it installs registry STAND-INS to stay out of the collision matrix, and
adding two subprocess-and-signal scenarios to it would put ~25 s of process
launches behind every run of a 72-check path that needs none.

NO NETWORK, NO KEYS, **NO SPEND**, no live Qdrant, no model load, no corpus, no
git history and no live server. `match_patient_ablation`, the BM25 index, the
graph, the tracking module and `run_fingerprint.current` are stand-ins and THE
GRAPH IS NEVER INVOKED, so no billed call is reachable; `main()`, the
configuration loop, `_on_done`, `_create_run`, `_finalize_run`,
`log_ablation_result`, `save_ablation_checkpoint`, `load_ablation_checkpoint`,
`generate_summary` and both shutdown handlers are the real thing.

IT USES REAL SUBPROCESSES AND REAL SIGNALS ON PURPOSE, for the reason
`tests/test_runner_sigterm_shutdown.py` records: a signal cannot be delivered to
the process asserting about it, and an in-process `raise SystemExit` would test
the test rather than the shipped handler. The subprocess IS
`python "26- Ablation Study.py"`, so the `__main__` guard that installs the
handler and takes the lock is the shipped one.

THE STAND-INS ARRIVE THROUGH `usercustomize`, NOT THROUGH runpy OR exec, because
section 1c of `tests/test_package_invariants.py` forbids loading a module by
location -- unconditionally, with no allowlist escape -- and it CAUGHT the first
version of the sigterm file doing exactly that inside a string literal. A
`__main__` guard only runs when the file is executed as `__main__`, and the
stand-ins must already be installed by then, so the setup has to happen at
INTERPRETER STARTUP.

*** THE FOREGROUND-SIGNAL LESSON, AND IT IS CLOSED IN CODE RATHER THAN BY A
CONVENTION. *** A shell that runs a job in the background sets SIGINT (and
SIGQUIT) to SIG_IGN for it, and a child inherits that disposition -- and CPython
DOES NOT OVERRIDE AN INHERITED SIG_IGN at startup. So if this file is itself
launched in the background (`python tests/... &`, or any runner that
backgrounds it), every subprocess it starts is DEAF to SIGINT: the Ctrl-C
scenario delivers a signal that does nothing, the study runs to completion, and
the check reports the shipped fix as broken. "Do not background it" is an
unenforced convention, which is what this project distrusts, so the hook
RESTORES `default_int_handler` in the child and check 0b ASSERTS the disposition
it ended up with -- a scenario that cannot be built is a recorded failure here,
never a pass.

NOT IN THE COLLISION MATRIX, derived: every database, checkpoint, sentinel and
FHIR-ish file is inside a `tempfile.mkdtemp` this file removes and asserts gone,
and the two repository files it reads (`oncotriage/ablation/study.py`,
`26- Ablation Study.py`) are written by neither of the suite's two writers and
are sha256-compared at the end. IT EXECS NOTHING, so it needs no
`_EXEC_ALLOWLIST` entry.

Run from terminal:
    python tests/test_ablation_stop_and_lock.py
"""

import ast
import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_CODE_DIR = os.path.dirname(_TESTS_DIR)
if _CODE_DIR not in sys.path:
    sys.path.insert(0, _CODE_DIR)

# NO MODEL IS LOADED. Set above the imports, which is the ordering
# oncotriage/fixtures/replay.py had to establish: oncotriage/agent/deps.py reads
# this variable ONCE, at its own import, and `deps` arrives transitively on the
# first `oncotriage` import -- so an assignment underneath would reach nothing.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import oncotriage                                              # noqa: E402
from oncotriage import paths as _paths                         # noqa: E402
from oncotriage.ablation import study as _study                # noqa: E402
from oncotriage.config import MAX_WORKERS                      # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(oncotriage.__file__)))
_STUDY_PATH = os.path.abspath(_study.__file__)
_ENTRY_PATH = os.path.join(_REPO, "26- Ablation Study.py")

_STUDY_SRC = open(_STUDY_PATH, encoding="utf-8").read()
_ENTRY_SRC = open(_ENTRY_PATH, encoding="utf-8").read()
_SHA_STUDY_BEFORE = hashlib.sha256(_STUDY_SRC.encode("utf-8")).hexdigest()
_SHA_ENTRY_BEFORE = hashlib.sha256(_ENTRY_SRC.encode("utf-8")).hexdigest()

_TMP = tempfile.mkdtemp(prefix="ablationctl_")


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


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


def check_true(label, actual):
    return check(label, bool(actual), True)


def at(container, key, default="<absent>"):
    """Index without raising.

    EVERY READ OF A DRIVEN RUN'S OUTPUT GOES THROUGH THIS. A bare `rows[0]`
    raises IndexError exactly when a defect stops a row being written -- which
    is precisely when this file owes a recorded failure and a summary, not one
    traceback with every check below it unrun. That shape has shipped in this
    repository eleven times; it does not ship here.
    """
    try:
        return container[key]
    except (IndexError, KeyError, TypeError):
        return default


def drive(fn, *args, **kwargs):
    """Call into production code, converting a raise into a comparable value."""
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                                # noqa: BLE001
        return ("<raised>", type(exc).__name__, str(exc))


#------------------------------------------------------------------------------


# ===========================================================================
# 0. PRECONDITIONS
# ===========================================================================

section("0. preconditions")

import site as _site                                            # noqa: E402
check("0a  user-site imports are enabled, so the stand-in hook will run "
      "(without this every driven scenario below would run UNSTUBBED; it still "
      "could not bill anything -- see ONCOTRIAGE_QDRANT_URL -- but it would "
      "prove nothing)", _site.ENABLE_USER_SITE, True)


# ===========================================================================
# 1. THE STOP SWITCH MECHANISM, DRIVEN DIRECTLY
# ===========================================================================

section("1. the stop switch mechanism")

_STATE = os.path.join(_TMP, "state")
os.makedirs(_STATE, exist_ok=True)
_SAVED_CP = _paths._RESOLVED.get("checkpoint_path", "<unset>")
_paths._RESOLVED["checkpoint_path"] = _STATE + os.sep

try:
    _sentinel = _study.ablation_stop_switch_path()
    _cp = _study._ablation_checkpoint_path()

    check("1a  the sentinel is DERIVED from the checkpoint path, so the file an "
          "operator writes and the state it stops cannot drift apart",
          (str(_sentinel.parent), _sentinel.name),
          (str(_cp.parent), _cp.stem + _study.ABLATION_STOP_SUFFIX))

    check("1b  ...and its name is NOT the batch runner's `STOP`. With no --db "
          "this directory IS the batch runner's checkpoint directory, and one "
          "shared name would make each harness's stale-sentinel refusal fire "
          "for a request made of the other",
          _sentinel.name == "STOP", False)

    _db_sentinel = _study.ablation_stop_switch_path(
        os.path.join(_STATE, "scratch.db"))
    check("1c  ...and it is PER DATABASE exactly as the checkpoint is, so a "
          "--db study and a production study sharing a directory do not stop "
          "each other",
          (_db_sentinel.name, _db_sentinel != _sentinel),
          ("scratch_checkpoint" + _study.ABLATION_STOP_SUFFIX, True))

    check("1d  an unarmed switch never trips, and that is the true answer for "
          "a caller that is not main(): no operator has asked THIS to stop",
          (_study.STOP_SWITCH.requested, drive(_study.STOP_SWITCH.poll)),
          (False, False))

    _study.STOP_SWITCH.arm(_sentinel)
    check("1e  an armed switch with no sentinel present still answers False",
          drive(_study.STOP_SWITCH.poll), False)

    with open(_sentinel, "w", encoding="utf-8") as _fh:
        _fh.write("  stopping for the index rebuild  ")
    check("1f  ...and trips once the file appears, recording where it was "
          "noticed and the operator's note (stripped)",
          (drive(_study.STOP_SWITCH.poll, where="a probe"),
           _study.STOP_SWITCH.detected_in, _study.STOP_SWITCH.message),
          (True, "a probe", "stopping for the index rebuild"))

    os.unlink(_sentinel)
    check("1g  IT LATCHES: deleting the sentinel does not un-trip it. The "
          "answer is acted on by CANCELLING queued work, which is not "
          "reversible -- and deleting the file is exactly what an operator does "
          "to make the NEXT study start, which they must be able to do while "
          "this one is still finishing",
          drive(_study.STOP_SWITCH.poll), True)

    _study.STOP_SWITCH.reset()
    check("1h  reset() forgets it, which is what stops a second main() in one "
          "process inheriting the first study's stop and cancelling every pair "
          "without an operator having asked",
          (_study.STOP_SWITCH.requested, _study.STOP_SWITCH.message,
           _study.STOP_SWITCH._armed_path),
          (False, None, None))

    # --- an empty sentinel is the documented gesture ------------------------
    _study.STOP_SWITCH.arm(_sentinel)
    with open(_sentinel, "w", encoding="utf-8") as _fh:
        _fh.write("   \n\t ")
    check("1i  an EMPTY (or all-whitespace) sentinel is fully valid and is the "
          "documented `touch` form: None means 'no note', never 'no stop'",
          (drive(_study.STOP_SWITCH.poll), _study.STOP_SWITCH.message),
          (True, None))
    _study.STOP_SWITCH.reset()

    # --- the note is CAPPED, and the read is bounded ------------------------
    _study.STOP_SWITCH.arm(_sentinel)
    with open(_sentinel, "w", encoding="utf-8") as _fh:
        _fh.write("z" * (_study.STOP_MESSAGE_MAX_CHARS + 500))
    drive(_study.STOP_SWITCH.poll)
    _msg = _study.STOP_SWITCH.message or ""
    check("1j  a long note is CAPPED and says so, so an accidental `cat` of a "
          "log into the sentinel cannot put an unbounded read on a shutdown "
          "path",
          (len(_msg) > _study.STOP_MESSAGE_MAX_CHARS,
           "truncated" in _msg,
           _msg.startswith("z" * 50)),
          (True, True, True))
    _study.STOP_SWITCH.reset()

    # --- a poll that RAISES does not trip the switch ------------------------
    class _Exploding:
        def exists(self):
            raise OSError("the mount went away")

    _faults_before = dict(_study.STOP_SWITCH_FAULTS)
    _study.STOP_SWITCH.arm(None)
    _study.STOP_SWITCH._armed_path = _Exploding()
    check("1k  A POLL THAT RAISES DOES NOT TRIP THE SWITCH. `exists` already "
          "answers False for every ordinary 'not there' case, so a raise is "
          "something else -- an unreadable directory, a filesystem gone -- and "
          "reading it as a stop would cancel a paid study because a mount "
          "hiccuped",
          drive(_study.STOP_SWITCH.poll), False)
    _new = {k for k, v in _study.STOP_SWITCH_FAULTS.items()
            if v != _faults_before.get(k)}
    check("1l  ...and it is COUNTED under `poll:`, so the study's own closing "
          "block says the stop may have been missed rather than the fault "
          "being silent", sorted(_new), ["poll:OSError"])
    _study.STOP_SWITCH.reset()
    _study.STOP_SWITCH_FAULTS.clear()

    # --- the stale-sentinel refusal ----------------------------------------
    with open(_sentinel, "w", encoding="utf-8") as _fh:
        _fh.write("left from yesterday")
    _refusal = drive(_study.assert_no_stale_ablation_stop_switch)
    check("1m  a sentinel present at start is a REFUSAL, not a no-op: without "
          "it the switch is a trap that fires once and then silently every "
          "time, and on a cron entry that is a comparison that never advances "
          "while every run reports success",
          (at(_refusal, 0), at(_refusal, 1)),
          ("<raised>", "StaleAblationStopSwitch"))
    _text = str(at(_refusal, 2))
    check("1n  ...and the refusal is ACTIONABLE: it names the file, quotes the "
          "note, says nothing has been billed, and gives both the `rm` and the "
          "one-command form",
          (str(_sentinel) in _text, "left from yesterday" in _text,
           "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED" in _text,
           "--clear-stop" in _text),
          (True, True, True, True))

    # --- clear, all three outcomes -----------------------------------------
    check("1o  clear_ablation_stop_switch() removes it and reports that there "
          "WAS one",
          (drive(_study.clear_ablation_stop_switch), os.path.exists(_sentinel)),
          (_study.STOP_CLEAR_REMOVED, False))
    check("1p  ...and reports ABSENT when there was nothing to clear",
          drive(_study.clear_ablation_stop_switch), _study.STOP_CLEAR_ABSENT)
    check("1q  the vocabulary is CLOSED and its members are distinct, so a "
          "caller may branch on it exhaustively -- which is the whole reason it "
          "is not a bool: `False` would mean 'there was none' AND 'there is one "
          "and I could not remove it', and the second must refuse to run",
          (_study.STOP_CLEAR_OUTCOMES, len(set(_study.STOP_CLEAR_OUTCOMES))),
          ((_study.STOP_CLEAR_REMOVED, _study.STOP_CLEAR_ABSENT,
            _study.STOP_CLEAR_FAILED), 3))

    check("1r  ...and clearing does NOT touch the checkpoint: clearing a "
          "control file and discarding resume state are opposite operations "
          "and must not be one flag apart",
          sorted(os.listdir(_STATE)), [])

finally:
    if _SAVED_CP == "<unset>":
        _paths._RESOLVED.pop("checkpoint_path", None)
    else:
        _paths._RESOLVED["checkpoint_path"] = _SAVED_CP
    _study.STOP_SWITCH.reset()

check("1s  the paths seam was restored by identity, so nothing below this "
      "point is looking at the scratch directory",
      _paths._RESOLVED.get("checkpoint_path", "<unset>"), _SAVED_CP)


# ===========================================================================
# 2. A READ-ONLY STATE DIRECTORY IS DIAGNOSED, NOT A TRACEBACK
# ===========================================================================

section("2. --clear-stop against a directory it cannot write")

_RO = os.path.join(_TMP, "readonly")
os.makedirs(_RO, exist_ok=True)
_paths._RESOLVED["checkpoint_path"] = _RO + os.sep
try:
    _ro_sentinel = _study.ablation_stop_switch_path()
    with open(_ro_sentinel, "w", encoding="utf-8") as _fh:
        _fh.write("stop")
    os.chmod(_RO, 0o500)
    _probe = os.path.join(_RO, ".probe")
    try:
        open(_probe, "w").close()
        os.unlink(_probe)
        _readonly_real = False
    except OSError:
        _readonly_real = True
    check("2a  the directory really is unwritable (NON-DEGENERACY: as root the "
          "mode bits are ignored, the unlink SUCCEEDS, and the whole scenario "
          "below would measure nothing while reporting three passes)",
          _readonly_real, True)
    if _readonly_real:
        _faults_before = dict(_study.STOP_SWITCH_FAULTS)
        _outcome = drive(_study.clear_ablation_stop_switch)
        check("2b  it REPORTS the failure instead of raising, so an operator "
              "gets a diagnosis rather than a traceback printed INSTEAD of the "
              "study they asked for",
              _outcome, _study.STOP_CLEAR_FAILED)
        check("2c  ...and the sentinel is still there, which is why the "
              "outcome may not be reported as ABSENT",
              os.path.exists(_ro_sentinel), True)
        _new = {k for k, v in _study.STOP_SWITCH_FAULTS.items()
                if v != _faults_before.get(k)}
        check("2d  ...and it is counted under its OWN `clear:` phase. `poll:` "
              "means the study may have run THROUGH a stop; this means an "
              "operator asked to RESUME and the sentinel is still there -- "
              "opposite directions, different fixes",
              sorted(_new), ["clear:PermissionError"])
finally:
    os.chmod(_RO, 0o700)
    if _SAVED_CP == "<unset>":
        _paths._RESOLVED.pop("checkpoint_path", None)
    else:
        _paths._RESOLVED["checkpoint_path"] = _SAVED_CP
    _study.STOP_SWITCH_FAULTS.clear()


# ===========================================================================
# 3. THE RUN LOCK, AS A MECHANISM
# ===========================================================================

section("3. the run lock")

_LOCK_A = os.path.join(_TMP, "lockA")
os.makedirs(_LOCK_A, exist_ok=True)
_DB_A = os.path.join(_LOCK_A, "a.db")
_DB_B = os.path.join(_LOCK_A, "b.db")

_path_a = _study.ablation_run_lock_path(_DB_A)
_path_b = _study.ablation_run_lock_path(_DB_B)

check("3a  the lock file lives OUTSIDE the state directory -- whose other "
      "files are resumable state an operator reads a listing of, and which may "
      "be a network share where flock is advisory at best",
      os.path.dirname(_path_a) == os.path.realpath(tempfile.gettempdir())
      or os.path.dirname(_path_a) == tempfile.gettempdir(), True)

check("3b  THE KEY IS THE CHECKPOINT FILE, NOT ITS DIRECTORY, which is where "
      "this diverges from the batch runner: two independent --db studies in "
      "one directory must not refuse each other",
      _path_a != _path_b, True)

_paths._RESOLVED["checkpoint_path"] = _LOCK_A + os.sep
try:
    _default_lock = _study.ablation_run_lock_path(None)
finally:
    if _SAVED_CP == "<unset>":
        _paths._RESOLVED.pop("checkpoint_path", None)
    else:
        _paths._RESOLVED["checkpoint_path"] = _SAVED_CP

check("3c  ...and the FILENAME PREFIX differs from the batch runner's. With no "
      "--db the study's state directory IS paths.checkpoint_path, so a shared "
      "lock file would make a batch run and an ablation study block each other "
      "-- two harnesses that write different databases and read different "
      "checkpoints",
      "oncotriage-ablation-run-" in os.path.basename(_default_lock)
      and "oncotriage-batch-run-" not in os.path.basename(_default_lock), True)

check("3d  EXIT_LOCKED is 3, which collides with nothing this entry point "
      "already returns (1 refusal, 2 argparse, 130 Ctrl-C, 143 SIGTERM)",
      _study.EXIT_LOCKED, 3)

# Held, then refused, in-process. The two-real-subprocesses drive is section 6.
with _study.exclusive_run_lock(db_path=_DB_A) as _held_path:
    _second = drive(lambda: _study.exclusive_run_lock(db_path=_DB_A).__enter__())
    check("3e  a second acquisition of the SAME key is refused IMMEDIATELY "
          "rather than queueing: a study that waited would still run, hours "
          "later, against a checkpoint the first has by then completed",
          (at(_second, 0), at(_second, 1)),
          ("<raised>", "AlreadyRunning"))
    _other = drive(lambda: _study.exclusive_run_lock(db_path=_DB_B).__enter__())
    check("3f  ...while a DIFFERENT --db is not refused, which is what makes "
          "the per-database key more than a detail",
          isinstance(_other, tuple) and at(_other, 0) == "<raised>", False)
    with open(_held_path, encoding="utf-8") as _fh:
        _record = json.load(_fh)
    check("3g  the holder record names the pid, host, user, start time and THE "
          "STATE IT ACTUALLY LOCKED -- the batch runner's first version read "
          "its directory a second time when writing the record, so a caller "
          "passing an explicit path got a record naming a directory it had "
          "nothing to do with, which is worse than no record because an "
          "operator acts on it",
          (sorted(_record), _record["pid"],
           os.path.basename(_record["checkpoint"])),
          (["checkpoint", "host", "pid", "started", "user"], os.getpid(),
           "a_checkpoint.json"))

_after = drive(lambda: _study.exclusive_run_lock(db_path=_DB_A).__enter__())
check("3h  and the lock is free again once the block exits, released by the "
      "KERNEL rather than by a line of this program -- which is the property a "
      "pid file cannot have and the reason this is not one",
      isinstance(_after, tuple) and at(_after, 0) == "<raised>", False)

_lines = _study.run_lock_refusal_lines(
    _study.AlreadyRunning("/tmp/x.lock", {"pid": 4242, "host": "h",
                                          "user": "u", "started": "t",
                                          "checkpoint": "/s/c.json"}))
_text = "\n".join(_lines)
check("3i  the refusal is ACTIONABLE and says what the collision costs: the "
      "pid to kill or wait for, the duplicate billing, AND the thing the batch "
      "case cannot suffer -- two studies split each config's sample between "
      "two ablation_runs rows, so the configurations end up compared over "
      "different patient sets",
      ("4242" in _text, "/s/c.json" in _text,
       "different patient sets" in _text,
       "NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED" in _text),
      (True, True, True, True))


# ===========================================================================
# 4. THE RUN STATUS VOCABULARY AND ITS MIGRATION
# ===========================================================================

section("4. ablation_runs.status")

check("4a  the vocabulary is closed and its members are distinct",
      (_study.RUN_STATUSES, len(set(_study.RUN_STATUSES))),
      (("RUNNING", "COMPLETE", "STOPPED", "KILLED"), 4))

check("4b  ...and RUN_STATUSES_PARTIAL is DERIVED from it rather than retyped, "
       "so a member added to one and not the other cannot be silently treated "
       "as complete",
      (set(_study.RUN_STATUSES_PARTIAL) <= set(_study.RUN_STATUSES),
       set(_study.RUN_STATUSES) - set(_study.RUN_STATUSES_PARTIAL)),
      (True, {_study.RUN_STATUS_COMPLETE}))

_MIG = os.path.join(_TMP, "migrate")
os.makedirs(_MIG, exist_ok=True)
_fresh_db = os.path.join(_MIG, "fresh.db")
_study.init_ablation_db(db_path=_fresh_db)
_conn = sqlite3.connect(_fresh_db)
try:
    _cols_fresh = [r[1] for r in _conn.execute("PRAGMA table_info(ablation_runs)")]
finally:
    _conn.close()
check("4c  a FRESH database carries the column", "status" in _cols_fresh, True)

# A PRE-MIGRATION database, built by DROPPING the column from a real one rather
# than by retyping the old CREATE TABLE -- which would be a second declaration
# that can disagree with the shipped one.
_old_db = os.path.join(_MIG, "old.db")
shutil.copy2(_fresh_db, _old_db)
_conn = sqlite3.connect(_old_db)
try:
    _conn.execute("INSERT INTO ablation_runs (run_timestamp, config_name, "
                  "config_description, sample_size, status) "
                  "VALUES ('2026-01-01', 'full_pipeline', 'd', 10, 'COMPLETE')")
    _conn.execute("ALTER TABLE ablation_runs DROP COLUMN status")
    _conn.commit()
    _cols_old = [r[1] for r in _conn.execute("PRAGMA table_info(ablation_runs)")]
finally:
    _conn.close()
check("4d  the pre-migration shape really lacks it (non-degeneracy: without "
      "this the migration below would be migrating a database that already "
      "had the column)", "status" in _cols_old, False)

_study.init_ablation_db(db_path=_old_db)
_conn = sqlite3.connect(_old_db)
try:
    _cols_mig = [r[1] for r in _conn.execute("PRAGMA table_info(ablation_runs)")]
    _hist = _conn.execute("SELECT status FROM ablation_runs").fetchall()
finally:
    _conn.close()
check("4e  init_ablation_db MIGRATES it", "status" in _cols_mig, True)
check("4f  ...and the historical row is left NULL, NOT backfilled COMPLETE. "
      "Writing COMPLETE would assert that every historical run finished, which "
      "is false of any study that was ever interrupted -- and those are exactly "
      "the rows a reader most needs to distrust",
      [r[0] for r in _hist], [None])
check("4g  ...and a fresh database and a migrated one end up with the "
      "IDENTICAL physical column order, which is what makes the two "
      "indistinguishable to every reader and is why `status` is named ONLY in "
      "the migration and not also in the CREATE TABLE",
      _cols_mig, _cols_fresh)

# --- _finalize_run's contract ----------------------------------------------
_fin_db = os.path.join(_MIG, "fin.db")
_study.init_ablation_db(db_path=_fin_db)
_rid = _study._create_run("full_pipeline", "d", 3, db_path=_fin_db)
_conn = sqlite3.connect(_fin_db)
try:
    _opened = _conn.execute("SELECT status, total_time_seconds FROM "
                            "ablation_runs WHERE id = ?", (_rid,)).fetchone()
finally:
    _conn.close()
check("4h  _create_run opens the row RUNNING, so a row still reading RUNNING "
      "when a study is over is a configuration whose process had no chance to "
      "run a handler", _opened, ("RUNNING", None))

check("4i  _finalize_run stores a legal status and reports that one row moved",
      drive(_study._finalize_run, _rid, 12.0, _study.RUN_STATUS_STOPPED,
            db_path=_fin_db), True)
_conn = sqlite3.connect(_fin_db)
try:
    _fin = _conn.execute("SELECT status, total_time_seconds FROM "
                         "ablation_runs WHERE id = ?", (_rid,)).fetchone()
finally:
    _conn.close()
check("4j  ...and the row carries it", _fin, ("STOPPED", 12.0))

_rrf_before = dict(_study.RUN_RECORD_FAILURES)
check("4k  AN UNRECOGNISED STATUS IS REFUSED RATHER THAN STORED. A typo would "
      "put a value outside the closed vocabulary into a column readers branch "
      "on, silently",
      drive(_study._finalize_run, _rid, 1.0, "FINISHED", db_path=_fin_db),
      False)
check("4l  ...and the row keeps the status it had, rather than being "
      "overwritten with the typo",
      sqlite3.connect(_fin_db).execute(
          "SELECT status FROM ablation_runs WHERE id = ?", (_rid,)).fetchone(),
      ("STOPPED",))

check("4m  A ROW COUNT OF ZERO IS A FAILURE, NOT A SUCCESS. `UPDATE ... WHERE "
      "id = ?` against an id that is not there SUCCEEDS and updates nothing, "
      "and SQLite reports no error -- so a finalizer that did not read rowcount "
      "would report success for a run row that was never written",
      drive(_study._finalize_run, 999999, 1.0, _study.RUN_STATUS_COMPLETE,
            db_path=_fin_db), False)

_new_rrf = {k for k, v in _study.RUN_RECORD_FAILURES.items()
            if v != _rrf_before.get(k)}
check("4n  ...and both refusals are COUNTED, under keys that name which one "
      "happened",
      sorted(_new_rrf),
      ["finalize:NoSuchRun(0)", "finalize:UnknownStatus('FINISHED')"])

check("4o  _finalize_run NEVER RAISES, even against a database that cannot be "
      "opened -- it runs inside two shutdown handlers whose job is to leave a "
      "record, and a raise there would replace the record with a traceback",
      drive(_study._finalize_run, 1, 1.0, _study.RUN_STATUS_KILLED,
            db_path=os.path.join(_TMP, "no-such-dir", "x.db")), False)
_study.RUN_RECORD_FAILURES.clear()


# ===========================================================================
# 5. THE SUMMARY NAMES A PREFIX RATHER THAN AVERAGING IT SILENTLY
# ===========================================================================

section("5. _summary_status_warning")

_WARN = os.path.join(_TMP, "warn")
os.makedirs(_WARN, exist_ok=True)


def _seeded(name, statuses):
    """A database whose latest run per config carries each given status."""
    db = os.path.join(_WARN, name)
    _study.init_ablation_db(db_path=db)
    conn = sqlite3.connect(db)
    try:
        for i, (config_name, status) in enumerate(statuses):
            conn.execute(
                "INSERT INTO ablation_runs (run_timestamp, config_name, "
                "config_description, sample_size, status) VALUES (?,?,?,?,?)",
                (f"2026-01-0{i + 1}", config_name, "d", 10, status))
        conn.commit()
    finally:
        conn.close()
    return db


def _warn_lines(db):
    conn = sqlite3.connect(db)
    try:
        return drive(_study._summary_status_warning, conn)
    finally:
        conn.close()


_clean = _seeded("clean.db", [("full_pipeline", "COMPLETE"),
                              ("no_mesh_filter", "COMPLETE")])
check("5a  every latest run COMPLETE prints NOTHING AT ALL -- a clean line "
      "every study trains a reader to skip the place the real one appears",
      _warn_lines(_clean), [])

_partial = _seeded("partial.db", [("full_pipeline", "COMPLETE"),
                                  ("no_mesh_filter", "STOPPED"),
                                  ("bm25_only", "KILLED"),
                                  ("vector_only", "RUNNING")])
_pl = "\n".join(_warn_lines(_partial))
check("5b  ...and a configuration whose latest run was cut short is NAMED, "
      "with its status, above the deltas. The averages are over however many "
      "patients ran before the stop, printed beside full-sample averages as "
      "though comparable",
      ("no_mesh_filter" in _pl, "bm25_only" in _pl, "vector_only" in _pl,
       "full_pipeline" in _pl, "PREFIX" in _pl),
      (True, True, True, False, True))

_unrec = _seeded("unrec.db", [("full_pipeline", None)])
_ul = "\n".join(_warn_lines(_unrec))
check("5c  a NULL status is its own bucket and is NOT read as a failure: the "
      "row records nothing about how it ended, which is not the same as ending "
      "badly and not the same as COMPLETE either",
      ("NOT RECORDED" in _ul, "full_pipeline" in _ul, "PREFIX" in _ul),
      (True, True, False))

_nocol = os.path.join(_WARN, "nocol.db")
shutil.copy2(_clean, _nocol)
_conn = sqlite3.connect(_nocol)
try:
    _conn.execute("ALTER TABLE ablation_runs DROP COLUMN status")
    _conn.commit()
finally:
    _conn.close()
_nl = "\n".join(_warn_lines(_nocol))
check("5d  a database that PREDATES the column cannot answer, and that is "
      "reported as itself rather than swallowed -- and never as 'every "
      "configuration is complete'",
      ("could not be read" in _nl, "OperationalError" in _nl), (True, True))


# --- and it reaches the REAL report, in the right place ---------------------
#
# SECTIONS 5a-5d DRIVE THE HELPER; THIS DRIVES generate_summary(). A helper that
# returns the right lines and a report that never calls it are the same thing to
# every check above, which is the gap this closes.


class _Recorder:
    """Stands in for `console`, capturing what generate_summary prints."""

    def __init__(self):
        self.lines = []

    def out(self, text=""):
        self.lines.append(str(text))

    def banner(self, *a, **kw):                     # pragma: no cover
        pass

    def attach_bar(self, *a, **kw):                 # pragma: no cover
        return None

    def detach_bar(self, *a, **kw):                 # pragma: no cover
        pass


_REPORT_DB = _seeded("report.db", [("full_pipeline", "COMPLETE"),
                                   ("no_mesh_filter", "STOPPED")])
_conn = sqlite3.connect(_REPORT_DB)
try:
    for _cfg, _rid in (("full_pipeline", 1), ("no_mesh_filter", 2)):
        for _i in range(3):
            _conn.execute(
                "INSERT INTO ablation_results (run_id, config_name, patient_id, "
                "candidates_retrieved, candidates_reranked, "
                "candidates_after_rule_filter, candidates_evaluated, "
                "eligible_count, not_eligible_count, avg_match_score_all, "
                "estimated_cost_usd, total_time, error) "
                "VALUES (?,?,?,1,1,1,1,1,0,0.5,0.01,1.0,'')",
                (_rid, _cfg, f"P{_i}"))
    _conn.commit()
finally:
    _conn.close()

_saved_console = _study.console
_rec = _Recorder()
_study.console = _rec
try:
    _df = drive(_study.generate_summary, db_path=_REPORT_DB)
finally:
    _study.console = _saved_console
check("5e  the paths seam was restored by identity", _study.console,
      _saved_console)
_report = "\n".join(_rec.lines)
check("5f  generate_summary() ACTUALLY PRINTS the qualification -- sections 5a "
      "to 5d drive the helper, and a helper that returns the right lines while "
      "the report never calls it is indistinguishable to them",
      ("no_mesh_filter" in _report and "PREFIX OF THE SAMPLE" in _report), True)
check("5g  ...and it lands BETWEEN the table and the deltas, which is the "
      "position chosen rather than the convenient one: a reader who stops at "
      "the table has already seen it, and a reader who goes on to the deltas "
      "-- the number most likely to be quoted -- reads it immediately above "
      "them",
      (_report.index("ABLATION STUDY RESULTS")
       < _report.index("PREFIX OF THE SAMPLE")
       < _report.index("DELTAS vs FULL PIPELINE")), True)
check("5h  ...and the study still RETURNED its frame, so the qualification "
      "does not cost the caller the table",
      (hasattr(_df, "empty"), len(getattr(_df, "index", []))), (True, 2))


# --- the study-level vocabulary, which is printed and never stored ---------

check("5i  STUDY_STATUSES is closed and its members are distinct. It is a "
      "different question from RUN_STATUSES: a study whose last configuration "
      "was STOPPED is itself STOPPED, but a study that ran every configuration "
      "to COMPLETE says nothing about itself in any row",
      (_study.STUDY_STATUSES, len(set(_study.STUDY_STATUSES))),
      (("COMPLETE", "STOPPED", "INTERRUPTED", "CRASHED"), 4))

_lines = []
drive(_study.print_study_close, "NOT-A-STATUS", 1.0, 1, 0, 0,
      db_path=os.path.join(_TMP, "warn", "clean.db"), out=_lines.append)
_txt = "\n".join(_lines)
check("5j  ...and an unrecognised status is NAMED rather than falling through "
      "into silence. Without the guard the whole block prints with NO `Status:` "
      "line at all, which reads as a study that ended in a way nobody thought "
      "to describe",
      ("unrecognised study status" in _txt, "CRASHED" in _txt), (True, True))
_lines = []
drive(_study.print_study_close, _study.STUDY_STATUS_COMPLETE, 1.0, 1, 0, 0,
      db_path=os.path.join(_TMP, "warn", "clean.db"), out=_lines.append)
check("5k  ...and a RECOGNISED one is not (non-degeneracy: a guard that fired "
      "on everything would satisfy 5f for the wrong reason)",
      "unrecognised study status" in "\n".join(_lines), False)

check("5l  a CANCELLED count is named only when it is non-zero, so a clean "
      "study's block is byte-identical to what it has always printed and a "
      "stopped one cannot report pairs nobody ran as pairs that failed",
      ("Cancelled:" in "\n".join(_lines)), False)


# ===========================================================================
# 6. DRIVEN: THE REAL ENTRY POINT, REAL SUBPROCESSES, REAL SIGNALS
# ===========================================================================

section("6. the real entry point, driven")

# THE STAND-INS. `match_patient_ablation` is the ONE LIVE BILLED CALL per pair
# and is replaced by a recorder that PARKS -- parking rather than sleeping is
# the sigterm file's measured lesson: a queued pair can only start once a
# running one returns, so while every worker is parked NOTHING can advance and
# the started count is a statement about cancellation rather than about
# scheduling luck.
#
# NO BILLED CALL IS REACHABLE EVEN IF THE HOOK NEVER RUNS: every subprocess is
# handed ONCOTRIAGE_QDRANT_URL pointed at a closed port, so an unstubbed
# build_bm25_index_from_qdrant fails and main() exits before Stage 5 exists.
_HOOK = r'''
import os, signal, sys, threading, time

from oncotriage.ablation import study as S
from oncotriage import paths as P
from oncotriage import run_fingerprint as F

assert os.path.realpath(S.__file__).startswith(
    os.path.realpath(os.environ["ONC_REPO"])), (
    "PREFLIGHT: the study that imported is not the one this run targets: "
    + os.path.realpath(S.__file__))

# *** THE FOREGROUND-SIGNAL LESSON, CLOSED IN CODE. ***
# A shell that backgrounds a job sets SIGINT to SIG_IGN for it and children
# INHERIT that disposition; CPython does not override an inherited SIG_IGN at
# startup. So a subprocess started from a backgrounded test file is DEAF to
# SIGINT and the Ctrl-C scenario would deliver a signal that does nothing --
# reporting the shipped fix as broken. Restoring the default here makes the
# child behave as a foreground launch would, and the marker below lets the
# parent ASSERT that rather than assume it.
_inherited = signal.getsignal(signal.SIGINT)
signal.signal(signal.SIGINT, signal.default_int_handler)
with open(os.environ["ONC_SIGINT_MARKER"], "w") as _fh:
    _fh.write("inherited=%r restored=%r\n"
              % (_inherited is signal.SIG_IGN,
                 signal.getsignal(signal.SIGINT) is signal.default_int_handler))

S.build_bm25_index_from_qdrant = lambda *a, **k: (object(), ["NCT1"])
S.build_matching_graph = lambda *a, **k: object()


class _Tracking:
    def start_run(self, **kw): pass
    def log_run_metrics(self, *a, **kw): pass
    def end_run(self, **kw):
        print("[stand-in] tracking.end_run", kw.get("status"), flush=True)


S.tracking = _Tracking()

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
P._RESOLVED["checkpoint_path"] = os.environ["ONC_STATE"] + os.sep
P._RESOLVED["result_ablation_path"] = os.environ["ONC_STATE"] + os.sep

_STARTED = os.environ["ONC_STARTED"]
_READY = os.environ["ONC_READY"]
_RELEASE = os.environ["ONC_RELEASE"]
_PARK = os.environ["ONC_PARK"]
_CAP = float(os.environ["ONC_CAP"])
_lock = threading.Lock()


# THE SAMPLE IS FABRICATED rather than parsed, so no corpus is needed and the
# stratified draw is deterministic without one.
def _sample(patients, sample_size, seed):
    return [{"patient_id": "P%03d" % i, "conditions": [], "medications": [],
             "observations": [], "allergies": [], "procedures": [],
             "demographics": {"age": 60, "sex": "female"}}
            for i in range(sample_size)]


S.stratified_sample = _sample
S.load_all_patients = lambda *a, **k: [{"patient_id": "P000"}]


def _pipeline(patient_data, bm25_index, nct_ids, graph, ablation_flags):
    """Record that this PAIR started, then park if asked.

    THE LEDGER IS THE COST PROOF. One line per pair this stand-in is CALLED for
    -- which in production is one live billed Stage 5 call -- so "zero pairs
    started after the stop point" is a number read out of a file rather than a
    claim about a code path.
    """
    name = patient_data["patient_id"]
    with _lock:
        with open(_STARTED, "a") as fh:
            fh.write(name + "\n")
        n = sum(1 for _ in open(_STARTED))
    if _PARK == "yes":
        if n == 1:
            with open(_READY, "w") as fh:
                fh.write("go")
        _deadline = time.time() + _CAP
        while not os.path.exists(_RELEASE) and time.time() < _deadline:
            time.sleep(0.01)
    return {"matches": [], "near_misses": [], "not_evaluable": [],
            "stage_timings": {}, "primary_condition": "breast",
            "candidates_retrieved": 1, "candidates_reranked": 1,
            "candidates_after_rule_filter": 1,
            "candidates_after_quality_filter": 1, "candidates_evaluated": 1,
            "mesh_dropped": 0, "stage_dropped": 0, "histology_dropped": 0,
            "llm_classifier_input_tokens": 1, "llm_classifier_output_tokens": 1,
            "matching_model": os.environ.get("ONC_MODEL", "gpt-5.6-terra")}


S.match_patient_ablation = _pipeline

with open(os.environ["ONC_HOOK_MARKER"], "w") as _fh:
    _fh.write("installed")
'''

_HOOK_DIR = os.path.join(_TMP, "hook")
os.makedirs(_HOOK_DIR, exist_ok=True)
with open(os.path.join(_HOOK_DIR, "usercustomize.py"), "w",
          encoding="utf-8") as _fh:
    _fh.write(_HOOK)

_MARK_STOP = "[STOP] Stop requested by"
_MARK_SIGINT = "[INTERRUPTED] Waiting for active threads to finish"


def drive_entry(name, *, park=False, action=None, args=(), patients=6,
                configs=("full_pipeline", "no_mesh_filter"), timeout=180,
                state_dir=None, hold=False):
    """Run `26- Ablation Study.py` once and return everything worth asserting on.

    ``action`` is applied once the first pair has parked: "stop" writes the
    sentinel, "sigint" and "sigterm" send the real signal.

    ``hold=True`` parks the study and RETURNS WITHOUT RELEASING IT, so the
    caller holds a live process with the run lock taken. It is the only mode
    that does not wait for the child to exit, and the caller must write the
    ``release`` file itself.

    ``state_dir`` SHARES THE STUDY'S STATE (database, checkpoint, sentinel)
    between invocations while every CONTROL file -- the ledger, the ready and
    release flags, the log -- stays per invocation under this call's own root.
    That split is not tidiness: the first version put the control files in the
    shared directory too, so the SECOND invocation of the lock scenario wrote
    the release flag the FIRST was parked on, freed the holder, and then took
    the lock it was supposed to be refused. Measured -- the holder's log showed
    a completed study -- and it made a refusal that cannot happen look like a
    lock that does not work.
    """
    root = os.path.join(_TMP, "run-" + name)
    st = state_dir or os.path.join(root, "state")
    corpus = os.path.join(root, "corpus")
    for d in (st, corpus):
        os.makedirs(d, exist_ok=True)
    with open(os.path.join(corpus, "p.json"), "w", encoding="utf-8") as fh:
        json.dump({"resourceType": "Bundle", "entry": []}, fh)

    started = os.path.join(root, "started.txt")
    ready = os.path.join(root, "ready")
    release = os.path.join(root, "release")
    log = os.path.join(root, "log.txt")
    hook_marker = os.path.join(root, "hook")
    sigint_marker = os.path.join(root, "sigint")
    for f in (ready, release):
        if os.path.exists(f):
            os.unlink(f)

    env = dict(os.environ)
    env.update({
        # The hook dir FIRST so `usercustomize` resolves to ours.
        "PYTHONPATH": os.pathsep.join([_HOOK_DIR, _REPO]),
        "PYTHONDONTWRITEBYTECODE": "1",
        "ONCOTRIAGE_DEFER_LOCAL_MODELS": "1",
        # A CLOSED PORT: no billed call is reachable even if the hook fails.
        "ONCOTRIAGE_QDRANT_URL": "http://127.0.0.1:1",
        "ONC_REPO": _REPO,
        "ONC_STATE": st,
        "ONC_CORPUS": corpus,
        "ONC_STARTED": started,
        "ONC_READY": ready,
        "ONC_RELEASE": release,
        "ONC_PARK": "yes" if park else "no",
        "ONC_CAP": "90",
        "ONC_HOOK_MARKER": hook_marker,
        "ONC_SIGINT_MARKER": sigint_marker,
    })

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

    cmd = [sys.executable, _ENTRY_PATH, "--sample-size", str(patients),
           "--configs", *configs, *args]
    sentinel = None
    acted = False
    saturated = None
    handler_entered = None
    held = False

    with open(log, "w", encoding="utf-8") as sink:
        proc = subprocess.Popen(cmd, stdout=sink, stderr=subprocess.STDOUT,
                                text=True, env=env, cwd=_REPO)
        try:
            if park and (action is not None or hold):
                _wait(lambda: os.path.exists(ready), 120)
                saturated = _wait(
                    lambda: sum(1 for _ in open(started)) >= 1, 60)
                if proc.poll() is None:
                    if action == "stop":
                        # The sentinel path is derived the SAME way the study
                        # derives it, from the module rather than retyped.
                        sentinel = os.path.join(
                            st, "ablation_checkpoint"
                            + _study.ABLATION_STOP_SUFFIX)
                        with open(sentinel, "w", encoding="utf-8") as fh:
                            fh.write("stopping for the index rebuild")
                        acted = True
                    elif action in ("sigint", "sigterm"):
                        proc.send_signal(signal.SIGINT if action == "sigint"
                                         else signal.SIGTERM)
                        acted = True
            if hold:
                # THE CALLER HOLDS A LIVE PROCESS. Nothing below runs.
                #
                # `held = True` BEFORE THE RETURN, AND THAT IS NOT TIDINESS.
                # A `return` inside a `try` RUNS THE `finally`, which kills any
                # child still alive -- so the first version of this branch
                # handed the caller a process it had just killed, and the lock
                # scenario measured a refusal that could not happen. Found by
                # running: the holder's log was empty and its poll() was
                # already an integer.
                held = True
                return {"proc": proc, "root": root, "state": st,
                        "release": release, "log": log,
                        "hook": os.path.exists(hook_marker)}
            with open(release, "w", encoding="utf-8") as fh:
                fh.write("go")
            # THE MARKERS ARE WAITED FOR *AFTER* THE RELEASE, and the ordering
            # is a real defect the first version of this harness had. Both
            # shutdown handlers print only once the parked workers have
            # returned, so waiting for the marker BEFORE releasing them waits
            # for a line that cannot be written yet -- the wait times out, and
            # a working handler is reported as one that never ran.
            if action == "stop":
                handler_entered = _wait(
                    lambda: _MARK_STOP in _log_text(), 60)
            elif action == "sigint":
                handler_entered = _wait(
                    lambda: _MARK_SIGINT in _log_text(), 60)
            elif action == "sigterm":
                handler_entered = _wait(
                    lambda: "[SIGTERM]" in _log_text(), 60)
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:                       # pragma: no cover
            proc.kill()
            proc.wait()
        finally:
            if not held and proc.poll() is None:                # pragma: no cover
                proc.kill()
                proc.wait()

    started_names = []
    if os.path.exists(started):
        with open(started, encoding="utf-8") as fh:
            started_names = [l.strip() for l in fh if l.strip()]

    runs = []
    db = os.path.join(st, "ablation_results.db")
    if os.path.exists(db):
        conn = sqlite3.connect(db)
        try:
            runs = conn.execute("SELECT config_name, status FROM ablation_runs "
                                "ORDER BY id").fetchall()
        except sqlite3.Error as exc:                            # noqa: BLE001
            runs = [("<sqlite error>", str(exc))]
        finally:
            conn.close()

    checkpoint = None
    cp_file = os.path.join(st, "ablation_checkpoint.json")
    if os.path.exists(cp_file):
        try:
            with open(cp_file, encoding="utf-8") as fh:
                checkpoint = json.load(fh)
        except (OSError, ValueError) as exc:                    # noqa: BLE001
            checkpoint = {"<unreadable>": str(exc)}

    sigint_note = ""
    if os.path.exists(sigint_marker):
        with open(sigint_marker, encoding="utf-8") as fh:
            sigint_note = fh.read().strip()

    return {
        "exit": proc.returncode,
        "out": _log_text(),
        "hook": os.path.exists(hook_marker),
        "sigint_disposition": sigint_note,
        "saturated": saturated,
        "acted": acted,
        "handler_entered": handler_entered,
        "started": started_names,
        "runs": runs,
        "checkpoint": checkpoint,
        "sentinel": sentinel,
        "state": st,
        "db": db,
        "root": root,
        "summary_json": os.path.join(st, "ablation_summary.json"),
    }


# --- 6A: the control -- a clean study ---------------------------------------
_A = drive_entry("clean", patients=3)
check("6a  THE CONTROL: a clean study runs, exits 0, and both configurations "
      "are recorded COMPLETE. Without this every 'stopped' assertion below "
      "could be satisfied by a study that never worked at all",
      (_A["hook"], _A["exit"], _A["runs"]),
      (True, 0, [("full_pipeline", "COMPLETE"),
                 ("no_mesh_filter", "COMPLETE")]))
check("6b  ...and the SIGINT disposition really was restored in the child, so "
      "the Ctrl-C scenario below is reachable however this file was launched "
      "(a backgrounded shell hands its children SIG_IGN and CPython keeps it)",
      "restored=True" in _A["sigint_disposition"], True)
check("6c  ...and it ran every pair of both configurations",
      len(_A["started"]), 6)
check("6d  ...and a finished study CLEARS its checkpoint and writes the "
      "summary",
      (_A["checkpoint"], os.path.exists(_A["summary_json"])), (None, True))


# --- 6B: the stop switch, mid-configuration ---------------------------------
#
# THE SAMPLE IS LARGER THAN MAX_WORKERS ON PURPOSE. With 6 pairs and 12 worker
# threads every pair is already IN FLIGHT when the sentinel appears, so all six
# finish, nothing is cancelled, nothing goes unsubmitted -- and the
# configuration is genuinely COMPLETE rather than a prefix. That is the correct
# outcome and it is not the one this scenario is for. 40 pairs against 12
# workers leaves 28 queued, which is what makes a PARTIAL configuration
# reachable at all.
_STOP_N = MAX_WORKERS * 3
_B = drive_entry("stop", park=True, action="stop", patients=_STOP_N)
check("6e  a STOP written mid-configuration is noticed and announced",
      (_B["saturated"], _B["acted"], _B["handler_entered"]),
      (True, True, True))
check("6f  ...and the study EXITS 0. A stop is a clean end, not a crash",
      _B["exit"], 0)
check("6g  ...and NO FURTHER PAIR IS STARTED once the switch trips: the ledger "
      "counts what the stand-in was CALLED for, which in production is one "
      "live billed Stage 5 call each, so this is a statement about MONEY and "
      "not about a code path. The bound is MAX_WORKERS -- the pairs already "
      "running when the sentinel appeared -- and it is asserted as a bound "
      "rather than an exact number because which of them had started is a "
      "scheduling fact",
      (len(_B["started"]) <= MAX_WORKERS, len(_B["started"]) >= 1),
      (True, True))
check("6h  ...and the SECOND configuration was never opened at all -- the "
      "between-configurations poll is above _create_run, so a stop noticed in "
      "config 1 leaves config 2 with no ablation_runs row to explain later",
      [r[0] for r in _B["runs"]], ["full_pipeline"])
check("6i  ...and the configuration it cut short is recorded STOPPED, which is "
      "the whole reason the column exists: generate_summary reports the LATEST "
      "run per config, so without it a PREFIX of the sample is averaged beside "
      "full-sample averages as though comparable",
      [r[1] for r in _B["runs"]], ["STOPPED"])
check("6j  ...and the CHECKPOINT IS KEPT and the SUMMARY IS NOT WRITTEN. "
      "Clearing the checkpoint would throw away every completed pair and the "
      "next study would re-bill all of them",
      (_B["checkpoint"] is not None, os.path.exists(_B["summary_json"])),
      (True, False))
check("6k  ...and the closing block SAYS so, naming the sentinel and the "
      "resume gesture rather than leaving an operator to find them",
      ("Status:          STOPPED" in _B["out"],
       "--clear-stop" in _B["out"],
       "PREFIX" in _B["out"]),
      (True, True, True))

# --- 6C: the sentinel is STICKY, and --clear-stop is the resume gesture ------
_C_STALE = drive_entry("stale", state_dir=_B["state"], patients=_STOP_N)
check("6l  THE SENTINEL IS NOT DELETED BY THE STUDY THAT HONOURED IT, and the "
      "next study REFUSES while it is there -- a self-clearing switch would let "
      "a cron entry honour a stop nobody asked for that day and report success "
      "every time",
      (_C_STALE["exit"], "REFUSED (stop switch present)" in _C_STALE["out"]),
      (1, True))
check("6m  ...and the refusal started NO pair, so nothing was billed. The "
      "ledger is per invocation, so this is a count of what THIS run did "
      "rather than a file two runs appended to",
      len(_C_STALE["started"]), 0)

_C = drive_entry("resume", state_dir=_B["state"], patients=_STOP_N,
                 args=("--clear-stop",))
check("6n  --clear-stop satisfies the refusal and the study RESUMES to "
      "completion, exiting 0 and writing the summary it withheld",
      (_C["exit"], os.path.exists(_C["summary_json"])), (0, True))
check("6o  ...and every configuration is COMPLETE by the end. The STOPPED row "
      "the first invocation left is still there -- it is the record of what "
      "happened -- and it is no longer the LATEST for its config, which is what "
      "makes generate_summary read the finished one",
      sorted({name for name, status in _C["runs"] if status == "COMPLETE"}),
      ["full_pipeline", "no_mesh_filter"])
check("6p  ...and the resume RE-BILLED NOTHING it had already written: the "
      "pairs this invocation started, plus the pairs the stopped one started, "
      "sum to the whole study EXACTLY ONCE. The ledger is per invocation, so "
      "this is a sum of two disjoint counts rather than one file read twice",
      len(_B["started"]) + len(_C["started"]), _STOP_N * 2)
check("6q  ...and the checkpoint is cleared by the finished study",
      _C["checkpoint"], None)

# --- 6C-bis: --summary-only is exempt from the stale-sentinel refusal -------
#
# THE NATURAL NEXT COMMAND AFTER A STOP MUST NOT BE THE ONE COMMAND THAT
# UN-STOPS THE NEXT STUDY. `--summary-only` reads the database, runs nothing
# and bills nothing, so the refusal's premise ("this run would stop again at
# its first completed pair") is false of it -- and its remediation would tell
# an operator to delete a sentinel they had not withdrawn just to LOOK at what
# the stopped study produced.
_SUM_STATE = os.path.join(_TMP, "sumonly")
os.makedirs(_SUM_STATE, exist_ok=True)
_seed_run = drive_entry("sumseed", patients=3, state_dir=_SUM_STATE,
                        configs=("full_pipeline",))
with open(os.path.join(_SUM_STATE,
                       "ablation_checkpoint" + _study.ABLATION_STOP_SUFFIX),
          "w", encoding="utf-8") as _fh:
    _fh.write("stopped yesterday")
_SUM = drive_entry("sumonly", patients=3, state_dir=_SUM_STATE,
                   configs=("full_pipeline",), args=("--summary-only",))
check("6q-b a stale sentinel does NOT block --summary-only, which runs nothing "
      "and bills nothing",
      (_seed_run["exit"], _SUM["exit"],
       "REFUSED (stop switch present)" in _SUM["out"]),
      (0, 0, False))
check("6q-c ...and it started no pair, so the exemption did not smuggle a run "
      "past the refusal",
      len(_SUM["started"]), 0)
_SUM_FRESH = drive_entry("sumfresh", patients=3, state_dir=_SUM_STATE,
                         configs=("full_pipeline",),
                         args=("--summary-only", "--fresh-start"))
check("6q-d ...but --fresh-start PUTS THE REFUSAL BACK even combined with "
      "--summary-only, because that flag DELETES THE RESUME STATE whatever "
      "else the invocation does -- which is the destructive act the preflight "
      "is ordered above",
      (_SUM_FRESH["exit"],
       "REFUSED (stop switch present)" in _SUM_FRESH["out"]),
      (1, True))


# --- 6D: Ctrl-C ends the study rather than the configuration ----------------
_D = drive_entry("sigint", park=True, action="sigint", patients=6)
check("6r  Ctrl-C is HANDLED: the study announces it and exits 130 with no "
      "traceback -- a shutdown the operator asked for is not a fault report",
      (_D["handler_entered"], _D["exit"],
       "Traceback" in _D["out"]),
      (True, 130, False))
check("6s  *** IT ENDS THE STUDY, NOT THE CONFIGURATION. *** The old handler "
      "caught the interrupt and RETURNED NORMALLY, so the loop carried on to "
      "the NEXT configuration -- at one live billed call per pair -- after "
      "printing that the study had been interrupted",
      [r[0] for r in _D["runs"]], ["full_pipeline"])
check("6t  ...and the configuration in flight is recorded KILLED rather than "
      "left RUNNING forever, which is the shape reserved for a process that "
      "had no chance to run a handler",
      [r[1] for r in _D["runs"]], ["KILLED"])
check("6u  ...and the CLOSING BLOCK still prints. Re-raising skips Step 5, so "
      "without the handler printing it an interrupted study would report no "
      "wall time, no counts and NONE OF ITS THREE DEGRADATION COUNTERS",
      ("ABLATION STUDY SUMMARY" in _D["out"],
       "Status:          INTERRUPTED" in _D["out"]),
      (True, True))
check("6v  ...and the checkpoint is intact, so a resume costs nothing for what "
      "already ran", _D["checkpoint"] is not None, True)


# --- 6E: SIGTERM ------------------------------------------------------------
_E = drive_entry("sigterm", park=True, action="sigterm", patients=6)
check("6w  SIGTERM HAS A DISPOSITION. Python's default is SIG_DFL, so `docker "
      "stop`, systemd and a bare `kill` ran NOTHING here: no handler, no "
      "exception, no `finally`, the open configuration left RUNNING and every "
      "in-flight billed request abandoned unrecorded",
      (_E["handler_entered"], "[SIGTERM] Termination requested" in _E["out"]),
      (True, True))
check("6x  ...and it exits 143 (128 + SIGTERM), which a supervisor can tell "
      "apart from Ctrl-C's 130 and from this file's other codes",
      _E["exit"], 143)
check("6y  ...and the open configuration is recorded KILLED, with the second "
      "never opened",
      _E["runs"], [("full_pipeline", "KILLED")])
check("6z  ...and no traceback: an orchestrator-requested shutdown is not a "
      "fault report", "Traceback" in _E["out"], False)


# --- 6F: two real concurrent invocations ------------------------------------
#
# TWO REAL SUBPROCESSES, because a lock held by one process cannot be observed
# from inside it -- the argument tests/test_runner_preflight_and_state_faults.py
# records for the batch runner's lock.
_LOCK_STATE = os.path.join(_TMP, "lockstate")
os.makedirs(_LOCK_STATE, exist_ok=True)
_holder = drive_entry("holder", park=True, patients=_STOP_N,
                      state_dir=_LOCK_STATE, hold=True)
check("6ac the holder is alive and parked, so the lock really is held while "
      "the second invocation runs (non-degeneracy: a holder that had already "
      "exited would make the refusal below impossible and 6aa would report a "
      "PASS for a lock that does nothing)",
      _holder["proc"].poll() is None, True)
try:
    # The holder is parked with the lock held; start a second against the same
    # state and require it to be refused.
    _second = drive_entry("second", park=False, patients=_STOP_N,
                          state_dir=_LOCK_STATE)
    check("6aa A SECOND STUDY AGAINST THE SAME STATE IS REFUSED WITH EXIT 3, "
          "having started NOTHING. Two studies both read the same checkpoint "
          "at start and both run the SAME (config, patient) pairs at one live "
          "Stage 5 call each -- and a study's unit is a PAIR, so the same "
          "patient is paid for once per configuration",
          (_second["exit"], "another ablation study holds the lock"
           in _second["out"]),
          (3, True))
    check("6ab ...and the refusal names the holder's pid so an operator can "
          "act on it", "pid" in _second["out"], True)
finally:
    # Release the parked holder and let it finish, so the lock is free and no
    # process outlives this file.
    with open(_holder["release"], "w", encoding="utf-8") as _fh:
        _fh.write("go")
    try:
        _holder["proc"].wait(timeout=120)
    except subprocess.TimeoutExpired:                           # pragma: no cover
        _holder["proc"].kill()
        _holder["proc"].wait()

check("6ad ...and the holder then finishes normally, which says the refusal "
      "cost it nothing and that the lock was released by the process exiting "
      "rather than by anything this file did",
      _holder["proc"].returncode, 0)


# ===========================================================================
# 7. STRUCTURE: the things a driven run cannot see
# ===========================================================================

section("7. structural")

_TREE = ast.parse(_STUDY_SRC, _STUDY_PATH)
_ENTRY_TREE = ast.parse(_ENTRY_SRC, _ENTRY_PATH)


def _fn(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    return None


_main = _fn(_TREE, "main")
check_true("7a  study.main() was found (non-degeneracy)", _main is not None)

_withs = [n for n in ast.walk(_main or ast.parse(""))
          if isinstance(n, ast.With)
          for item in n.items
          if isinstance(item.context_expr, ast.Call)
          and isinstance(item.context_expr.func, ast.Name)
          and item.context_expr.func.id == "ThreadPoolExecutor"]
check("7b  THE EXECUTOR IS NOT A CONTEXT MANAGER. `with ThreadPoolExecutor(...)"
      "` calls shutdown(wait=True) -- WITHOUT cancel_futures -- from __exit__, "
      "which runs BEFORE any `except` below it, so it DRAINS the whole "
      "remaining configuration at one live billed call each and only then is "
      "the handler entered",
      len(_withs), 0)

_shutdowns = [n for n in ast.walk(_main or ast.parse(""))
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute)
              and n.func.attr == "shutdown"]
check("7c  ...and the explicit shutdown passes cancel_futures=True",
      [any(k.arg == "cancel_futures"
           and isinstance(k.value, ast.Constant) and k.value.value is True
           for k in c.keywords) for c in _shutdowns],
      [True] * len(_shutdowns) if _shutdowns else ["<no shutdown call>"])

_ki = [h for n in ast.walk(_main or ast.parse("")) if isinstance(n, ast.Try)
       for h in n.handlers
       if isinstance(h.type, ast.Name) and h.type.id == "KeyboardInterrupt"]
check("7d  there is exactly one KeyboardInterrupt handler in main()",
      len(_ki), 1)
check("7e  ...and it RE-RAISES. Swallowing it is what let an interrupted study "
      "carry on to the next configuration",
      any(isinstance(n, ast.Raise) and n.exc is None
          for h in _ki for n in ast.walk(h)), True)
check("7f  ...and it asks Stage 5 to stop issuing requests BEFORE anything "
      "else, which is what bounds the drain: the raise lands on the MAIN "
      "thread and the pipeline runs on WORKERS, so the pool's own "
      "cancel_futures cannot reach a request that is already queued",
      any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
          and n.func.id == "request_stage5_shutdown"
          for h in _ki for n in ast.walk(h)), True)

# --- the entry point ---------------------------------------------------------
_sig_calls = [n for n in ast.walk(_ENTRY_TREE)
              if isinstance(n, ast.Call)
              and isinstance(n.func, ast.Attribute)
              and n.func.attr == "signal"
              and isinstance(n.func.value, ast.Name)
              and n.func.value.id == "signal"]
_targets = {ast.unparse(c.args[0]) for c in _sig_calls if c.args}
check("7g  the entry point installs a disposition for SIGTERM and for NOTHING "
      "ELSE. No SIGINT handler is installed, deliberately: Ctrl-C keeps "
      "CPython's default so the KeyboardInterrupt lands wherever the main "
      "thread is rather than inside a handler that might hold a lock",
      sorted(_targets), ["signal.SIGTERM"])

check("7h  ...and the SIGTERM handler RESETS ITS OWN DISPOSITION FIRST. "
      "Everything the handlers then do catches Exception and not "
      "BaseException, so a SECOND SIGTERM would raise straight through them "
      "and leave the record half-written -- the one thing this handler exists "
      "to produce",
      any(isinstance(c.args[1], ast.Attribute)
          and c.args[1].attr == "SIG_DFL"
          for c in _sig_calls if len(c.args) > 1), True)

check("7i  the lock is taken in the __main__ guard and NOT in main(), on "
      "`25- Batch Runner.py`'s precedent: main() is directly callable, and an "
      "embedder driving several studies in one process would be refusing "
      "itself",
      ("exclusive_run_lock" in _ENTRY_SRC,
       any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
           and n.func.id == "exclusive_run_lock"
           for n in ast.walk(_main or ast.parse("")))),
      (True, False))

check("7j  the stale-sentinel preflight is ABOVE the destructive flag inside "
      "main(). A preflight below --fresh-start deletes the resume state and "
      "THEN refuses, printing 'NOTHING HAS BEEN BILLED' over a study the next "
      "invocation re-bills in full -- the ordering the pre-migration pass had "
      "to fix in the batch runner",
      _STUDY_SRC.index("assert_no_stale_ablation_stop_switch(db_path=db_path)")
      < _STUDY_SRC.index("clear_ablation_checkpoint(db_path=db_path)"), True)


# ===========================================================================
# 8. NOTHING IN THE REPOSITORY WAS WRITTEN
# ===========================================================================

section("8. the repository is unchanged")

check("8a  oncotriage/ablation/study.py is byte-identical",
      hashlib.sha256(open(_STUDY_PATH, "rb").read()).hexdigest(),
      _SHA_STUDY_BEFORE)
check("8b  26- Ablation Study.py is byte-identical",
      hashlib.sha256(open(_ENTRY_PATH, "rb").read()).hexdigest(),
      _SHA_ENTRY_BEFORE)
check("8c  ...and the two hashes are of DIFFERENT files (non-degeneracy: the "
      "first version of a comparison like this in another file hashed one file "
      "twice in one expression and was a tautology)",
      _SHA_STUDY_BEFORE == _SHA_ENTRY_BEFORE, False)

shutil.rmtree(_TMP, ignore_errors=True)
check("8d  the temp tree is gone", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print(f"\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 24 2026

@author: ramyalsaffar
"""
