# The serial runner's lock, hardened the way the batch runner's already was
##########################################################################

"""Serial Runner Lock Test

WHAT THIS IS FOR. `tests/run_serial_tests.py` guards the one thing in this suite
that can silently corrupt the repository: two of the five tests it runs rewrite
`oncotriage/` IN PLACE and restore from a backup taken at their own start, so
two overlapping runs leave a PLANTED tree behind with both runs reporting
success. Its lock is what makes that impossible.

`oncotriage/batch/runner.py`'s run lock was modelled the same way and was
hardened four ways that this one was not. The mechanism is identical -- a name
in a world-writable directory, derived from a path anybody can guess -- so the
defects were identical, and this file's blast radius is arguably the worse of
the two: a batch run that overlaps bills a cohort twice, and a serial run that
overlaps leaves a deliberate defect in `cancer_code_registry.py`.

THE FOUR, AND THE DEFECT BEHIND EACH.

1.  `realpath`, NOT `abspath`, AS THE KEY (section 2). `abspath` normalizes `.`,
    `..` and the working directory and STOPS THERE -- it does not resolve
    symlinks -- so one checkout reached through two names hashed to two digests,
    took two lock files, and BOTH RAN. That is the exact overlap the lock exists
    to prevent, arriving through the one route the lock could not see. Not
    hypothetical: a CI job checking out to a symlinked workspace beside a
    developer running `make serial-tests` in the real path is two names for one
    tree, and on macOS `/var` is itself a link to `/private/var`.

2.  A 0700 UID-KEYED DIRECTORY, `O_NOFOLLOW`, AND 0600 (sections 3 and 4). The
    lock file's name is a SHA-256 of a path, so another user on the same host
    could pre-create it as a SYMLINK to any file this user can write, and the
    first run to start would `O_CREAT` through it and `ftruncate` the target to
    zero. The sticky bit does not help -- it stops one user DELETING another's
    file, not creating a new one at an unclaimed name.

3.  A UTC RECORD WITH AN EXPLICIT MARKER (section 5). The holder's start time is
    read by somebody deciding whether that run is stuck, often from a CI log
    written in another region. A bare local time is wrong by the writer's offset
    with nothing in the string saying so -- and `strftime` with a `Z` over
    `localtime` is the exact defect the structured logger had to fix once
    already, which is why the `Z` and `gmtime` are asserted TOGETHER.

4.  A TYPED REFUSAL, NOT AN `OSError` (section 6). "The lock could not be
    OPENED" is `LockUnavailable`, a `RuntimeError`, converted at the ACQUISITION
    site. The obvious form is `except OSError` around the `with` block in
    `main()` -- and `_run_all()` runs INSIDE that block, so the clause would
    swallow every `OSError` the five subprocess launches can raise and report it
    as a lock failure with the run's real diagnosis discarded.

AND THE ONE THAT IS NOT A PORT (section 7): the two-process drive. A lock held
by one process cannot be observed from inside it, so the refusal is measured
with TWO REAL CONCURRENT INVOCATIONS OF THE SHIPPED ENTRY POINT.

WHY THE DRIVE IS SAFE, WHICH IS THE DESIGN DECISION IN THIS FILE. It does NOT
run the real serial suite -- that takes nine minutes and rewrites `oncotriage/`.
It builds a THROWAWAY CHECKOUT in a `tempfile.mkdtemp()`: a BYTE-IDENTICAL copy
of `tests/run_serial_tests.py` (sha256-compared, so the lock code under test is
the shipped lock code) beside five one-line STUB scripts at the five paths
`SERIAL_TESTS` names. The entry point is then the real one, its missing-file
preflight passes, and `_run_all` really does launch subprocesses -- but the
payload is harmless. THAT IS WHAT MAKES THE FAILURE MODE SAFE TOO: if the lock
were broken and the second process took it, what would run is two stubs, not two
source-rewriting tests.

THE HOLDER PARKS ON A FILE RATHER THAN SLEEPING. Stub 1 writes a `ready` marker
and waits (bounded) for a `release` marker, so the second invocation is provably
launched WHILE the first holds the lock. A sleep would make the assertion a
statement about this machine's scheduler, which is how
`tests/test_runner_sigterm_shutdown.py` recorded a flake once already.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO
DATABASE, NO GIT HISTORY, NO LIVE SERVER. IT IMPORTS NOTHING FROM `oncotriage`,
for the reason its subject records: `run_serial_tests.py` is a process launcher
that imports nothing from the project so that it still reports a missing test
file rather than dying on an ImportError when the package is what is broken --
and a test that imported the package to check it would not survive the state it
exists to survive. IT EXECS NOTHING AND LOADS NO MODULE BY LOCATION: the module
under test is reached with an ORDINARY `import` statement, after putting
`tests/` on `sys.path`. The first version of this file used
`importlib.util.spec_from_file_location` and argued that
`tests/test_package_invariants.py` section 1c did not apply to a non-package
file; SECTION 1c CAUGHT IT, because that rule is unconditional and has no
allowlist escape. See the note above the import.

NOT in `tests/run_serial_tests.py`'s collision matrix, derived rather than
asserted: it writes nothing in the repository -- every file it creates is inside
a `tempfile.mkdtemp()` it removes and then asserts gone, plus lock files under
this user's own 0700 lock directory keyed on those temp paths -- and the one
repository file it READS, `tests/run_serial_tests.py`, is written by neither of
the suite's two writers and is sha256-compared at the end.

    python tests/test_serial_runner_lock.py
"""

import ast
import copy
import errno
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time


# ===========================================================================
# HARNESS
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


def check_true(label, condition):
    check(label, bool(condition), True)


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


def guarded(fn, *args, **kwargs):
    """Call `fn` and convert a raise into a value `check` can fail on.

    A CHECK THAT ABORTS IS NOT A CHECK. This project has shipped that shape a
    dozen times: a defect makes the thing under test raise, the raise escapes
    while `check()`'s argument is being evaluated, and the run reports one
    traceback where it owed a summary and every remaining result.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                               # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


def raised(fn, *args, **kwargs):
    """The exception `fn` raised, or None. For checks ABOUT the exception."""
    try:
        fn(*args, **kwargs)
        return None
    except BaseException as exc:                               # noqa: BLE001
        return exc


#------------------------------------------------------------------------------


# ===========================================================================
# THE MODULE UNDER TEST
# ===========================================================================
#
# IMPORTED BY NAME, WITH `tests/` ON sys.path -- NOT LOADED BY LOCATION.
#
# THE FIRST VERSION OF THIS FILE USED `importlib.util.spec_from_file_location`
# AND ARGUED THAT `tests/test_package_invariants.py` SECTION 1c DID NOT APPLY,
# on the reasoning that the check is about `oncotriage` PACKAGE modules being
# reached by location instead of by `import`. THAT REASONING WAS WRONG AND THE
# CHECK CAUGHT IT: section 1c forbids a by-location module load
# UNCONDITIONALLY, with no allowlist escape, and it re-parses string literals so
# it cannot be evaded by hiding the call in one.
# `tests/test_runner_sigterm_shutdown.py` records being caught by exactly the
# same check for exactly the same reason, and answered it the same way -- by
# finding a mechanism that is not a by-location load.
#
# `tests/` HAS NO `__init__.py`, so it is not a package -- but a DIRECTORY on
# `sys.path` makes the `.py` files in it ordinary top-level modules, and
# `import run_serial_tests` is then an ordinary import statement. That is the
# shape the invariant asks for, and it is stronger than the one it replaces:
# the module goes into `sys.modules` under one name, so a second import is the
# same object rather than a second copy with its own state.
#
# IMPORTING IT RUNS NOTHING. Everything it does at module scope is definitions;
# the run is behind an `if __name__ == "__main__"` guard, which is asserted
# below rather than assumed -- because if that guard were ever removed, this
# import would launch the nine-minute source-rewriting suite as a side effect of
# a test.

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_RUNNER_PATH = os.path.join(_THIS_DIR, "run_serial_tests.py")

if not os.path.isfile(_RUNNER_PATH):
    # A HARD GUARD, NOT A check(). A wrong path here is not one failure but
    # every failure, each with a misleading message.
    raise SystemExit(f"[SerialLock] run_serial_tests.py not found at "
                     f"{_RUNNER_PATH}")

_RUNNER_TEXT = open(_RUNNER_PATH, encoding="utf-8").read()
_RUNNER_SHA = hashlib.sha256(_RUNNER_TEXT.encode("utf-8")).hexdigest()

# THE GUARD IS CHECKED BEFORE THE IMPORT, not after. After would be too late.
if "__name__ == \"__main__\"" not in _RUNNER_TEXT:
    raise SystemExit(
        "[SerialLock] run_serial_tests.py has no `__main__` guard; importing "
        "it would RUN the serial suite. Refusing.")

if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)

import run_serial_tests as srt                                 # noqa: E402

# THE MODULE THAT IMPORTED IS THE FILE THIS TEST NAMES, asserted on realpaths.
# Without this, a `run_serial_tests.py` earlier on `sys.path` would be tested
# instead and every result below would be about the wrong file -- the preflight
# this project's revert harnesses all carry, applied to an import.
if os.path.realpath(srt.__file__) != os.path.realpath(_RUNNER_PATH):
    raise SystemExit(
        f"[SerialLock] imported {srt.__file__!r}, wanted {_RUNNER_PATH!r}")

_REPO_ROOT = os.path.dirname(_THIS_DIR)
"""The repository root, derived from this file's own location.

The same derivation the runner makes for `_CODE_DIR`, and NOT read off `srt`:
section 1's pair checks are about whether the runner's declared table matches
the tree, and taking the tree's location from the thing under test would make
half of that comparison agree with itself by construction.
"""

_TMP = tempfile.mkdtemp(prefix="serial-lock-")


#------------------------------------------------------------------------------


# ===========================================================================
# 1.  THE SURFACE EXISTS
# ===========================================================================

section("1. The ported surface")

for _name in ("lock_directory", "ensure_lock_directory", "lock_path",
              "exclusive_run_lock", "AlreadyRunning", "LockUnavailable",
              "already_running_lines", "lock_unavailable_lines",
              "LOCK_DIRECTORY_MODE", "LOCK_FILE_MODE",
              "EXIT_LOCKED", "EXIT_LOCK_UNAVAILABLE"):
    check_true(f"1a  {_name} exists", hasattr(srt, _name))

check("1b  the two lock exit codes are distinct -- 'wait' and 'fix your temp "
      "directory' are different instructions",
      srt.EXIT_LOCKED == srt.EXIT_LOCK_UNAVAILABLE, False)
check("1c  the directory mode is owner-only", oct(srt.LOCK_DIRECTORY_MODE),
      oct(0o700))
check("1d  the file mode is owner-only", oct(srt.LOCK_FILE_MODE), oct(0o600))

# --- the pristine-copy guard's surface, and its table checked pair by pair ---

for _name in ("WRITER_OWNED_FILES", "BACKUP_MARKER", "EXIT_BACKUP_UNAVAILABLE",
              "EXIT_SIGNALLED", "BackupUnavailable", "backup_path",
              "backup_owner", "file_digest", "atomic_copy",
              "find_pristine_backups", "repair_pristine_backups",
              "repair_lines", "pristine_guard", "release_pristine_backups",
              "release_lines", "backup_unavailable_lines",
              "shutdown_signals_reach_cleanup", "_terminate_child",
              "CHILD_SHUTDOWN_GRACE_SECONDS"):
    check_true(f"1e  {_name} exists", hasattr(srt, _name))


def writer_evidence(writer_rel, target_rel, code_dir=_REPO_ROOT):
    """What the named writer's OWN SOURCE says about the target it rewrites.

    Returns (names_the_target, writes_a_file, is_serial). By AST, so a mention
    in a comment does not count and a mention in a DOCSTRING does -- string
    constants are what a path expression is built from, and separating the two
    would mean re-implementing the collision matrix's path resolution here.

    THIS IS THE HALF OF `WRITER_OWNED_FILES` THAT CAN BE CHECKED. An entry
    pointing at a file that moved, or at a test that stopped rewriting it, fails
    here. A brand-new third writer that nobody declared does NOT, and the
    runner's own docstring says so and says why -- a scan wide enough to catch
    one flags nine files in this suite that write only into a temp directory.
    """
    path = os.path.join(code_dir, writer_rel)
    if not os.path.isfile(path):
        return (False, False, False)
    tree = ast.parse(open(path, encoding="utf-8").read())
    names = any(isinstance(n, ast.Constant) and isinstance(n.value, str)
                and target_rel in n.value for n in ast.walk(tree))
    writes = False
    for n in ast.walk(tree):
        if not isinstance(n, ast.Call):
            continue
        func = getattr(n.func, "id", "") or getattr(n.func, "attr", "")
        if func == "open" and len(n.args) >= 2:
            mode = n.args[1]
            if isinstance(mode, ast.Constant) and isinstance(mode.value, str) \
                    and ("w" in mode.value or "a" in mode.value):
                writes = True
        if func in ("copy2", "copyfile", "write_text", "write_bytes"):
            writes = True
    serial = writer_rel in {rel for rel, _why in srt.SERIAL_TESTS}
    return (names, writes, serial)


check("1f  WRITER_OWNED_FILES is not empty and every entry is a (file, "
      "writer) pair (non-degeneracy: the loop below iterates nothing "
      "otherwise)",
      (len(srt.WRITER_OWNED_FILES) > 0,
       all(len(e) == 2 for e in srt.WRITER_OWNED_FILES)), (True, True))
for _target_rel, _writer_rel in srt.WRITER_OWNED_FILES:
    check(f"1g  {_target_rel} really is there to be copied",
          os.path.isfile(os.path.join(_REPO_ROOT, _target_rel)), True)
    check(f"1g  ...and {_writer_rel} names it, writes a file, and is in "
          f"SERIAL_TESTS", writer_evidence(_writer_rel, _target_rel),
          (True, True, True))
check("1h  control: writer_evidence reports a writer that does not name the "
      "target, so 1g is a statement about the pair rather than about the file "
      "existing", writer_evidence(srt.WRITER_OWNED_FILES[0][1],
                                  "oncotriage/no_such_module.py")[0], False)
check("1h  control: ...and a writer path that is not there at all",
      writer_evidence("tests/no_such_test.py",
                      srt.WRITER_OWNED_FILES[0][0]),
      (False, False, False))
check("1h  control: ...and a test in this suite that reads the package "
      "without rewriting it is NOT in SERIAL_TESTS, so the third member "
      "discriminates",
      writer_evidence("tests/test_serial_runner_lock.py",
                      srt.WRITER_OWNED_FILES[0][0])[2], False)
check("1i  the copy's marker cannot make a copy look like a Python module -- "
      "the package walk in tests/test_package_invariants.py is over `*.py`",
      srt.backup_path("/x/config.py", 1).endswith(".py"), False)


#------------------------------------------------------------------------------


# ===========================================================================
# 2.  THE KEY IS realpath: ONE CHECKOUT, ONE LOCK, HOWEVER IT IS NAMED
# ===========================================================================

section("2. realpath keying")

_real = os.path.join(_TMP, "checkout")
os.makedirs(_real, exist_ok=True)
_link = os.path.join(_TMP, "link-to-checkout")
os.symlink(_real, _link)

check_true("2a  the symlink and the real directory are DIFFERENT strings "
           "(non-degeneracy)", _real != _link)
check_true("2b  ...and abspath does not collapse them, which is why the old "
           "key produced two locks (non-degeneracy)",
           os.path.abspath(_real) != os.path.abspath(_link))
check("2c  realpath DOES collapse them",
      os.path.realpath(_real), os.path.realpath(_link))
check("2d  so both names give the SAME lock file",
      srt.lock_path(_real), srt.lock_path(_link))

# THE OLD KEY IS SHOWN TO PRODUCE TWO, so 2d is a statement about the fix rather
# than about two paths that happen to agree.
def _abspath_key(code_dir):
    """The PRE-FIX key, written out once. `abspath`, not `realpath`."""
    digest = hashlib.sha256(
        os.path.abspath(code_dir).encode("utf-8")).hexdigest()
    return f"oncotriage-serial-tests-{digest[:16]}.lock"


check("2e  CONTROL: the pre-fix abspath key gives two DIFFERENT lock files for "
      "the same checkout -- two locks, and both runs proceed",
      _abspath_key(_real) == _abspath_key(_link), False)

# A TRAILING SEPARATOR STILL MAKES NO DIFFERENCE, which is a property the old
# form had and the fix must not lose.
check("2f  a trailing separator changes nothing",
      srt.lock_path(_real), srt.lock_path(_real + os.sep))

# DETERMINISTIC.
check("2g  the key is deterministic across calls",
      srt.lock_path(_real), srt.lock_path(_real))


#------------------------------------------------------------------------------


# ===========================================================================
# 3.  THE LOCK DIRECTORY IS PER-USER, 0700, AND VERIFIED RATHER THAN ASSUMED
# ===========================================================================

section("3. The lock directory")

check_true("3a  it is named by the UID, not by the login name -- which "
           "LOGNAME/USER/LNAME/USERNAME can each change under one real user",
           srt.lock_directory().endswith(f"oncotriage-{os.getuid()}"))
check_true("3b  it is under the system temp directory, not in the repository",
           srt.lock_directory().startswith(tempfile.gettempdir()))
check_true("3c  every lock path is INSIDE it",
           os.path.dirname(srt.lock_path(_real)) == srt.lock_directory())

# `lock_directory` IS PURE. A caller who only wants to PRINT the path must not
# bring a directory into existence by asking -- the output_dir()/
# ensure_output_dir() lesson this project recorded once already.
_probe_dir = srt.lock_directory()
_existed = os.path.isdir(_probe_dir)
srt.lock_directory()
srt.lock_path(_real)
check("3d  lock_directory() and lock_path() CREATE NOTHING",
      os.path.isdir(_probe_dir), _existed)

_root = guarded(srt.ensure_lock_directory)
check("3e  ensure_lock_directory() returns the directory", _root, _probe_dir)
_info = os.lstat(_probe_dir)
check("3f  ...and it is a real directory, not a symlink (lstat, never stat)",
      stat.S_ISDIR(_info.st_mode), True)
check("3g  ...owned by this uid", _info.st_uid, os.getuid())
check("3h  ...and not writable by group or other",
      bool(_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)), False)

# THE THREE VERIFICATIONS FIRE. `exist_ok=True` does not chmod an existing
# directory, so creating it 0700 is only half the guarantee; these are the other
# half, and each is driven by pointing the function at a bad path.
_bad_parent = os.path.join(_TMP, "bad-lock-roots")
os.makedirs(_bad_parent, exist_ok=True)

_not_a_dir = os.path.join(_bad_parent, "a-file")
open(_not_a_dir, "w").close()
_wide = os.path.join(_bad_parent, "world-writable")
os.makedirs(_wide, exist_ok=True)
os.chmod(_wide, 0o777)

_saved_lock_dir = srt.lock_directory
try:
    srt.lock_directory = lambda: _not_a_dir
    _exc = raised(srt.ensure_lock_directory)
    check("3i  a FILE where the directory goes is refused",
          isinstance(_exc, OSError), True)
    srt.lock_directory = lambda: _wide
    _exc = raised(srt.ensure_lock_directory)
    check("3j  a group/other-writable directory is refused -- it re-opens the "
          "symlink substitution the per-user directory closes",
          isinstance(_exc, OSError) and _exc.errno == errno.EPERM, True)

    # A SYMLINK TO A PERFECTLY GOOD DIRECTORY IS STILL REFUSED, and this is the
    # ONLY case that separates `lstat` from `stat`. The other two checks pass
    # under either: a file is not a directory to both, and the mode bits of a
    # real directory read the same to both. `stat` FOLLOWS a link and reports on
    # the TARGET, so a symlinked lock directory would be accepted -- which is
    # precisely the substitution the per-user directory exists to close, arriving
    # one level up. MEASURED, not assumed: the first version of this section had
    # no such case and reported `lstat` -> `stat` as CORRECT.
    _sym_target = os.path.join(_bad_parent, "good-target")
    os.makedirs(_sym_target, mode=0o700, exist_ok=True)
    _sym_dir = os.path.join(_bad_parent, "symlinked-lock-dir")
    os.symlink(_sym_target, _sym_dir)
    check_true("3k  the substitute really is a symlink to a usable 0700 "
               "directory owned by this user (non-degeneracy: `stat` would "
               "accept it)",
               os.path.islink(_sym_dir) and os.path.isdir(_sym_dir)
               and os.stat(_sym_dir).st_uid == os.getuid()
               and not (os.stat(_sym_dir).st_mode
                        & (stat.S_IWGRP | stat.S_IWOTH)))
    srt.lock_directory = lambda: _sym_dir
    _exc = raised(srt.ensure_lock_directory)
    check("3l  a SYMLINK where the lock directory goes is refused (ENOTDIR) -- "
          "which is what `lstat` buys and `stat` cannot",
          isinstance(_exc, OSError) and _exc.errno == errno.ENOTDIR, True)
finally:
    srt.lock_directory = _saved_lock_dir
check("3m  the accessor was restored", srt.lock_directory, _saved_lock_dir)


#------------------------------------------------------------------------------


# ===========================================================================
# 4.  O_NOFOLLOW AND 0600 ON THE FILE
# ===========================================================================

section("4. The lock file itself")

_own_dir = os.path.join(_TMP, "own-locks")
os.makedirs(_own_dir, exist_ok=True)
_plain = os.path.join(_own_dir, "plain.lock")

with srt.exclusive_run_lock(path=_plain) as _held:
    check("4a  the lock yields the path it took", _held, _plain)
    check("4b  ...created 0600", oct(stat.S_IMODE(os.lstat(_plain).st_mode)),
          oct(0o600))

# THE SUBSTITUTION, DRIVEN FOR REAL. A symlink where the lock file goes, aimed
# at a file with contents: without O_NOFOLLOW the open follows it and the
# ftruncate zeroes the TARGET.
_victim = os.path.join(_own_dir, "victim.txt")
open(_victim, "w").write("this file must survive")
_victim_before = open(_victim).read()
_sym = os.path.join(_own_dir, "symlinked.lock")
os.symlink(_victim, _sym)

_exc = raised(lambda: srt.exclusive_run_lock(path=_sym).__enter__())
check("4c  a SYMLINK where the lock file goes is refused",
      type(_exc).__name__, "LockUnavailable")
check("4d  ...with ELOOP, which is what O_NOFOLLOW reports",
      getattr(_exc, "errno", None), errno.ELOOP)
check("4e  ...and the target it pointed at is UNTOUCHED -- this is the whole "
      "point: without O_NOFOLLOW the ftruncate would have emptied it",
      open(_victim).read(), _victim_before)

# NON-DEGENERACY: the victim really was reachable through the link, so 4e is
# about the guard rather than about a link that pointed nowhere.
check_true("4f  the symlink really did resolve to the victim (non-degeneracy)",
           os.path.realpath(_sym) == os.path.realpath(_victim))


#------------------------------------------------------------------------------


# ===========================================================================
# 5.  THE RECORD: UTC, MARKED, AND NAMING THE RESOLVED PATH
# ===========================================================================

section("5. The holder record")

_rec_lock = os.path.join(_own_dir, "record.lock")
with srt.exclusive_run_lock(path=_rec_lock):
    _record = json.loads(open(_rec_lock).read())

check("5a  it names the holder's pid", _record.get("pid"), os.getpid())
for _key in ("host", "user", "started", "code_dir"):
    check_true(f"5b  ...and its {_key}", bool(_record.get(_key)))

_started = _record.get("started", "")
check_true("5c  the timestamp carries an explicit Z", _started.endswith("Z"))
check_true("5d  ...in ISO-8601 with a T separator, not a space",
           len(_started) == 20 and _started[10] == "T")

# THE Z IS ONLY HONEST BECAUSE OF gmtime, AND THAT IS ASSERTED RATHER THAN
# TRUSTED. `strftime` with a `Z` over `localtime` parses cleanly, sorts cleanly
# and is wrong by the writer's UTC offset -- the exact defect the structured
# logger had to fix. So the stamp is compared against real UTC.
#
# THROUGH `guarded`, AND THE FIRST VERSION WAS NOT. A defect that changes the
# stamp's FORMAT -- which is one of the two defects this section exists to catch
# -- makes `time.strptime` raise, and the raise escaped while `check()`'s
# argument was being evaluated: the run reported one traceback where it owed a
# summary and thirty more results. That is the abort shape this project has
# shipped a dozen times, met again here and closed the same way.
def _utc_skew_seconds(stamp):
    """Seconds between `stamp` read as UTC and now, or a marker string."""
    return abs(time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ"))
               - time.mktime(time.strptime(
                   time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                   "%Y-%m-%dT%H:%M:%SZ")))


_utc_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
_skew = guarded(_utc_skew_seconds, _started)
check_true(f"5e  ...and it really is UTC, not local time wearing a Z "
           f"(stamp {_started}, utc now {_utc_now}, skew {_skew})",
           isinstance(_skew, float) and _skew < 120)

# NON-DEGENERACY FOR 5e: local time here must actually DIFFER from UTC, or the
# comparison passes on a machine at UTC+0 whatever the code does.
_local_now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime())
if _local_now == _utc_now:
    print("  NOTE  this machine is at UTC+0, so 5e cannot discriminate here; "
          "the assertion still holds and is meaningful elsewhere")
else:
    check_true("5f  local time differs from UTC on this machine, so 5e is "
               "discriminating (non-degeneracy)", _local_now != _utc_now)

# THE RECORD NAMES THE RESOLVED PATH, MATCHING THE KEY. A record naming the
# unresolved one would show an operator a different string from the one the
# refused run derived its digest from -- two names for the one thing the
# refusal is about.
#
# DRIVEN THROUGH A PATH THAT IS GENUINELY UNRESOLVED, which the shipped
# `_CODE_DIR` is not: `os.getcwd()` already resolves symlinks on this platform,
# so comparing the record against `realpath(_CODE_DIR)` is `x == x` and passes
# whatever the writer does. MEASURED, not assumed: the first version of this
# check reported a reverted writer as CORRECT. So `_CODE_DIR` is repointed at a
# symlinked name for one lock, inside try/finally, with the restore asserted.
_code_link = os.path.join(_TMP, "code-dir-link")
os.symlink(_real, _code_link)
check_true("5g  the substitute code dir really is unresolved "
           "(non-degeneracy)",
           os.path.realpath(_code_link) != _code_link)

_saved_code_dir = srt._CODE_DIR
_link_lock = os.path.join(_own_dir, "linked-code-dir.lock")
try:
    srt._CODE_DIR = _code_link
    with srt.exclusive_run_lock(path=_link_lock):
        _link_record = json.loads(open(_link_lock).read())
finally:
    srt._CODE_DIR = _saved_code_dir
check("5h  _CODE_DIR was restored", srt._CODE_DIR, _saved_code_dir)
check("5i  code_dir in the record is the REALPATH, not the name it was given",
      _link_record.get("code_dir"), os.path.realpath(_code_link))
check("5j  ...which is NOT the unresolved name (so 5i discriminates)",
      _link_record.get("code_dir") == _code_link, False)


#------------------------------------------------------------------------------


# ===========================================================================
# 6.  THE REFUSALS ARE TYPED, AND NEITHER IS AN OSError
# ===========================================================================

section("6. Typed refusals")

check_true("6a  AlreadyRunning is a RuntimeError",
           issubclass(srt.AlreadyRunning, RuntimeError))
check("6b  ...and NOT an OSError -- a stray `except OSError` around a path "
      "check must not be able to eat a refusal",
      issubclass(srt.AlreadyRunning, OSError), False)
check_true("6c  LockUnavailable is a RuntimeError",
           issubclass(srt.LockUnavailable, RuntimeError))
check("6d  ...and NOT an OSError. `_run_all()` runs INSIDE the `with`, so an "
      "`except OSError` there would swallow every OSError the five subprocess "
      "launches can raise and report it as a lock failure",
      issubclass(srt.LockUnavailable, OSError), False)
check("6e  ...and it is not a subclass of AlreadyRunning either: 'wait' and "
      "'fix your temp directory' are different findings",
      issubclass(srt.LockUnavailable, srt.AlreadyRunning), False)

# BOTH DIAGNOSES ARE FUNCTIONS, so they can be driven without arranging the
# condition -- and so that what an operator reads is asserted rather than
# assumed to exist.
_au = srt.AlreadyRunning("/tmp/x.lock", {"pid": 4242, "host": "h", "user": "u",
                                         "started": "2026-01-01T00:00:00Z",
                                         "code_dir": "/c"})
_au_lines = "\n".join(guarded(srt.already_running_lines, _au))
check_true("6f  the AlreadyRunning text names the holder's pid",
           "4242" in _au_lines)
check_true("6g  ...and says what overlapping would cost",
           "PLANTED" in _au_lines)

_lu = srt.LockUnavailable("/tmp/x.lock",
                          OSError(errno.EACCES, "Permission denied"))
_lu_lines = "\n".join(guarded(srt.lock_unavailable_lines, _lu))
check_true("6h  the LockUnavailable text names the errno numerically",
           "13" in _lu_lines)
check_true("6i  ...and symbolically, which is the thing an operator knows",
           "EACCES" in _lu_lines)
check_true("6j  ...and says it is NOT the other refusal",
           "NOT 'another run holds the lock'" in _lu_lines)
check_true("6k  ...and that nothing has run",
           "NOTHING HAS BEEN RUN" in _lu_lines)


#------------------------------------------------------------------------------


# ===========================================================================
# 7.  TWO REAL CONCURRENT INVOCATIONS OF THE SHIPPED ENTRY POINT
# ===========================================================================
#
# A lock held by one process cannot be observed from inside it. See the module
# docstring for why the payload is stubs: the entry point is the shipped one and
# the lock code under test is byte-identical, but what `_run_all` launches is
# harmless -- so a BROKEN lock costs two stub runs rather than two source
# rewrites.

section("7. Two processes, one checkout, reached through two names")

_CHECKOUT = os.path.join(_TMP, "fake-checkout")
os.makedirs(os.path.join(_CHECKOUT, "tests"), exist_ok=True)
_COPIED = os.path.join(_CHECKOUT, "tests", "run_serial_tests.py")
shutil.copy2(_RUNNER_PATH, _COPIED)

check("7a  the copied entry point is BYTE-IDENTICAL to the shipped one, so "
      "what is driven below is the shipped lock",
      hashlib.sha256(open(_COPIED, "rb").read()).hexdigest(), _RUNNER_SHA)

_READY = os.path.join(_TMP, "ready")
_RELEASE = os.path.join(_TMP, "release")

_PARKING_STUB = "\n".join([
    "import os, sys, time",
    f"open({_READY!r}, 'w').write(str(os.getpid()))",
    "_deadline = time.time() + 60",
    f"while not os.path.exists({_RELEASE!r}) and time.time() < _deadline:",
    "    time.sleep(0.02)",
    "sys.exit(0)",
])
_NOOP_STUB = "import sys\nsys.exit(0)\n"

# THE FIVE PATHS `SERIAL_TESTS` NAMES, READ OFF THE MODULE rather than retyped:
# a rename in the tuple must not leave this drive silently building the wrong
# files and failing the entry point's missing-file preflight instead.
_names = [n for n, _ in srt.SERIAL_TESTS]
check("7b  SERIAL_TESTS names five tests", len(_names), 5)
for _i, _rel in enumerate(_names):
    _dest = os.path.join(_CHECKOUT, _rel)
    os.makedirs(os.path.dirname(_dest), exist_ok=True)
    open(_dest, "w").write(_PARKING_STUB if _i == 0 else _NOOP_STUB)

# THE WRITER-OWNED FILES HAVE TO EXIST IN THE FAKE CHECKOUT TOO, and they are
# derived from `WRITER_OWNED_FILES` rather than retyped for the same reason the
# five test paths are: an entry added there must not leave this drive building
# the wrong tree and failing the guard's preflight instead of the lock.
# `pristine_guard` refuses to start a run it cannot take a copy for, so without
# these the entry point would exit EXIT_BACKUP_UNAVAILABLE before ever reaching
# the lock and section 7 would be measuring the wrong refusal.
for _rel, _writer in srt.WRITER_OWNED_FILES:
    _dest = os.path.join(_CHECKOUT, _rel)
    os.makedirs(os.path.dirname(_dest), exist_ok=True)
    open(_dest, "w").write(f"# stand-in for {_rel} in a throwaway checkout\n")
check("7b  ...and the fake checkout carries every writer-owned file, so the "
      "refusal measured below is the LOCK's and not the pristine guard's",
      sorted(os.path.isfile(os.path.join(_CHECKOUT, r))
             for r, _ in srt.WRITER_OWNED_FILES), [True] * len(srt.WRITER_OWNED_FILES))

_LINKED = os.path.join(_TMP, "fake-checkout-link")
os.symlink(_CHECKOUT, _LINKED)

_env = dict(os.environ)
_env["PYTHONDONTWRITEBYTECODE"] = "1"

_holder = subprocess.Popen(
    [sys.executable, os.path.join(_CHECKOUT, "tests", "run_serial_tests.py")],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_env)

_deadline = time.time() + 60
while not os.path.exists(_READY) and time.time() < _deadline:
    if _holder.poll() is not None:
        break
    time.sleep(0.02)

check("7c  the first invocation reached its first test, so it is HOLDING the "
      "lock right now (parked on a file, never a sleep)",
      os.path.exists(_READY) and _holder.poll() is None, True)

# THE SECOND INVOCATION REACHES THE SAME CHECKOUT THROUGH A SYMLINK. Under the
# pre-fix `abspath` key this took a DIFFERENT lock file and ran; under `realpath`
# it is the same key and is refused.
_second = subprocess.run(
    [sys.executable, os.path.join(_LINKED, "tests", "run_serial_tests.py")],
    capture_output=True, text=True, env=_env)

check("7d  the second invocation is REFUSED with EXIT_LOCKED, reaching the "
      "same checkout through a symlink",
      _second.returncode, srt.EXIT_LOCKED)
check_true("7e  ...naming the holder's pid, so the refusal is actionable",
           str(_holder.pid) in _second.stdout)
check_true("7f  ...and naming the lock file",
           "lock file:" in _second.stdout)
check_true("7g  ...and it ran NONE of the tests",
           "SERIAL TEST RUN" not in _second.stdout)

# THE HOLDER IS RELEASED AND COMPLETES NORMALLY, which is what says the refusal
# was the lock working rather than the first process having died.
open(_RELEASE, "w").close()
_holder_out, _ = _holder.communicate(timeout=120)
check("7h  the holder then completes normally", _holder.returncode, 0)
check_true("7i  ...having actually run its five stubs",
           "SERIAL TEST SUMMARY" in _holder_out)

# AND ONCE IT IS GONE, THE LOCK IS FREE -- released by the KERNEL, which is the
# property that rules out a pid file.
_third = subprocess.run(
    [sys.executable, os.path.join(_CHECKOUT, "tests", "run_serial_tests.py")],
    capture_output=True, text=True, env=_env)
check("7j  a later invocation takes the lock and runs (the kernel released it)",
      _third.returncode, 0)

# `--list` TAKES NO LOCK. Driven while nothing holds it is not a test of that;
# it is asserted structurally instead, by requiring the listing to complete
# while a lock IS held.
_holder2 = subprocess.Popen(
    [sys.executable, os.path.join(_CHECKOUT, "tests", "run_serial_tests.py")],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=_env)
os.remove(_READY)
os.remove(_RELEASE)
_deadline = time.time() + 60
while not os.path.exists(_READY) and time.time() < _deadline:
    if _holder2.poll() is not None:
        break
    time.sleep(0.02)
_listing = subprocess.run(
    [sys.executable, os.path.join(_CHECKOUT, "tests", "run_serial_tests.py"),
     "--list"], capture_output=True, text=True, env=_env)
check("7k  `--list` takes no lock: it succeeds while another run holds one",
      _listing.returncode, 0)
check_true("7l  ...and prints the order", "Serial order" in _listing.stdout)
open(_RELEASE, "w").close()
_holder2.communicate(timeout=120)


#------------------------------------------------------------------------------


# ===========================================================================
# 8.  AN UNOPENABLE LOCK EXITS 4, NOT 3
# ===========================================================================
#
# Driven through the shipped `main()`, by putting a SYMLINK at the derived lock
# path for this throwaway checkout. That path is inside this user's own 0700
# lock directory and is keyed on a temp directory, so nothing outside this test
# can be affected -- and it is removed afterwards.

section("8. EXIT_LOCK_UNAVAILABLE")

_derived = srt.lock_path(_CHECKOUT)
_nowhere = os.path.join(_TMP, "does-not-exist-target")
if os.path.lexists(_derived):
    os.remove(_derived)
os.symlink(_nowhere, _derived)
try:
    _unavailable = subprocess.run(
        [sys.executable,
         os.path.join(_CHECKOUT, "tests", "run_serial_tests.py")],
        capture_output=True, text=True, env=_env)
    check("8a  a symlinked lock file exits EXIT_LOCK_UNAVAILABLE, not "
          "EXIT_LOCKED", _unavailable.returncode, srt.EXIT_LOCK_UNAVAILABLE)
    check_true("8b  ...and says it is NOT the other refusal",
               "NOT 'another run holds the lock'" in _unavailable.stdout)
    check_true("8c  ...and that nothing has run and nothing has been restored",
               "NOTHING HAS BEEN RUN" in _unavailable.stdout)
    check_true("8d  ...and it ran NONE of the tests",
               "SERIAL TEST RUN" not in _unavailable.stdout)
finally:
    if os.path.lexists(_derived):
        os.remove(_derived)
check("8e  the planted symlink is removed", os.path.lexists(_derived), False)


#------------------------------------------------------------------------------


# ===========================================================================
# 9.  THE COPY IS PINNED AGAINST ITS ORIGINAL  (the consolidation pass)
# ===========================================================================
#
# WHY THIS FILE STILL HAS A COPY AT ALL, AND WHY THAT WAS RE-DECIDED RATHER
# THAN INHERITED. The consolidation pass moved the run lock into
# `oncotriage/control.py`, which imports NOTHING from the project -- so the
# first half of this file's no-project-imports rule (an import of the batch
# runner would drag the graph into a process launcher) is dissolved. The SECOND
# half is not. That rule exists so `python tests/run_serial_tests.py` still
# reports a missing test file rather than dying on an ImportError WHEN THE
# PACKAGE IS WHAT IS BROKEN, and `import oncotriage.control` executes
# `oncotriage/__init__.py` and needs the package on `sys.path`, which this
# launcher deliberately does not arrange. The by-location escape is closed too:
# `tests/test_package_invariants.py` section 1c forbids loading a module by
# location UNCONDITIONALLY, with no allowlist, and has already caught one test
# file doing exactly that.
#
# SO THE COPY STAYS AND STOPS BEING UNPINNED. Sections 1-8 assert the four
# hardenings HERE, which is what keeps them true of this file; this section
# asserts they are the SAME four as the original's, so the two cannot drift in
# the direction sections 1-8 cannot see -- a fix applied to `control.py` and
# not to this file would leave every check above passing.
#
# WHAT IS COMPARED, AND WHAT IS DECLARED DIFFERENT. A whole-module comparison
# is impossible and would be dishonest to fake: the two differ in the lock
# file's prefix, the key (a checkout here, a checkpoint there), the record's
# extra field, the exit code for an unopenable lock, and every line of refusal
# prose. What must NOT differ is the mechanism, so:
#
#   * `lock_directory`  -- byte-identical after `ast.unparse`, docstring
#     stripped. There is nothing in it to legitimately diverge.
#   * `ensure_lock_directory` -- identical after `ast.unparse` with every
#     string CONSTANT replaced by a placeholder. That tolerates the one
#     declared difference (this file says "serial-test lock directory" where
#     control says "run-lock directory") and tolerates NOTHING else: an lstat
#     turned into a stat, a dropped uid check, a changed mode mask and a
#     removed raise all survive the placeholder and fail the comparison.
#   * the two exception classes' `__init__` -- byte-identical after
#     `ast.unparse`. Those bodies are the refusal's PAYLOAD, which a caller
#     reads; the docstrings above them are where the two programs differ.
#   * the acquisition -- compared by FACTS rather than by text, because
#     control's is parameterized and this one is not. The open flags, the file
#     mode, the flock flags, the ftruncate-after-flock ordering, the fsync, the
#     close-in-finally, the `realpath` key and the `gmtime` stamp are each
#     asserted on both sides.
#   * the two mode constants -- equal by value.
#
# EVERY COMPARISON CARRIES A PLANTED CONTROL, because a structural comparison
# that has stopped comparing looks exactly like two files that agree.

section("9. The copy is pinned against oncotriage/control.py")

_CONTROL_PATH = os.path.join(os.path.dirname(_THIS_DIR), "oncotriage",
                             "control.py")
if not os.path.isfile(_CONTROL_PATH):
    # A HARD GUARD, NOT A check(), on this file's own rule: a wrong path here
    # is not one failure but every failure, each with a misleading message --
    # and the misleading message would be "the copy matches", because every
    # comparison below would be between two things that do not exist.
    raise SystemExit(f"[SerialLock] oncotriage/control.py not found at "
                     f"{_CONTROL_PATH}; section 9 cannot compare the copy "
                     f"against an original it could not read.")

_CONTROL_TEXT = open(_CONTROL_PATH, encoding="utf-8").read()
_CONTROL_SHA = hashlib.sha256(_CONTROL_TEXT.encode("utf-8")).hexdigest()
_CONTROL_TREE = ast.parse(_CONTROL_TEXT, _CONTROL_PATH)
_RUNNER_TREE = ast.parse(_RUNNER_TEXT, _RUNNER_PATH)


def _definition(tree, name):
    """One top-level function or class by name, or None."""
    for node in tree.body:
        if (isinstance(node, (ast.FunctionDef, ast.ClassDef))
                and node.name == name):
            return node
    return None


def _method(tree, class_name, method_name):
    cls = _definition(tree, class_name)
    if cls is None:
        return None
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == method_name:
            return node
    return None


def _strip_docstring(node):
    """A COPY of `node` with a leading string statement removed.

    A copy, because these trees are compared several times and mutating one in
    place would make the second comparison a comparison with the first
    comparison's leftovers.
    """
    clone = copy.deepcopy(node)
    body = getattr(clone, "body", [])
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        clone.body = body[1:]
    return clone


class _Blank(ast.NodeTransformer):
    """Replace every string constant with a placeholder.

    IT IS NOT A WEAKENING AND THE LIST OF WHAT SURVIVES IT IS THE ARGUMENT.
    The one declared difference between the two `ensure_lock_directory`
    implementations is the noun in three error messages. Everything that
    decides anything -- `os.lstat` against `os.stat`, the uid comparison, the
    `S_IWGRP | S_IWOTH` mask, the mode argument to `makedirs`, whether a branch
    raises at all -- is a Name, an Attribute, a numeric Constant or a
    statement, and none of those is a string.
    """

    def visit_Constant(self, node):                            # noqa: N802
        if isinstance(node.value, str):
            return ast.copy_location(ast.Constant(value="<str>"), node)
        return node


def _rendered(node, blank_strings=False):
    if node is None:
        return "<definition not found>"
    clone = _strip_docstring(node)
    if blank_strings:
        clone = ast.fix_missing_locations(_Blank().visit(clone))
    return ast.unparse(clone)


def _calls(node, attr):
    """Every `x.attr(...)` call in `node`, unparsed. [] when node is None."""
    if node is None:
        return []
    return sorted(ast.unparse(n) for n in ast.walk(node)
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "attr", None) == attr)


def _names_used(node):
    if node is None:
        return set()
    return {n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)}


# --- 9a  the two pure functions, byte for byte -----------------------------

_ld_here = _definition(_RUNNER_TREE, "lock_directory")
_ld_there = _definition(_CONTROL_TREE, "lock_directory")
check("9a  lock_directory is IDENTICAL to oncotriage/control.py's after "
      "ast.unparse, docstring aside. There is nothing in it that may "
      "legitimately diverge -- it is one return of one join -- so anything but "
      "equality here is drift",
      _rendered(_ld_here), _rendered(_ld_there))
check("9a-b ...and the comparison is non-degenerate: both definitions were "
      "actually found, so 9a is not two 'not found' strings agreeing",
      (_ld_here is not None, _ld_there is not None), (True, True))

_ed_here = _definition(_RUNNER_TREE, "ensure_lock_directory")
_ed_there = _definition(_CONTROL_TREE, "ensure_lock_directory")
check("9b  ensure_lock_directory is IDENTICAL after ast.unparse with string "
      "constants blanked. The blanking tolerates the ONE declared difference "
      "-- the noun in three error messages -- and nothing else: an lstat "
      "turned into a stat, a dropped uid check, a changed mode mask or a "
      "removed raise all survive it and fail here",
      _rendered(_ed_here, blank_strings=True),
      _rendered(_ed_there, blank_strings=True))
check("9b-b ...and both were found", (_ed_here is not None,
                                      _ed_there is not None), (True, True))
check("9b-c ...and the blanking really did blank something, or 9b is a "
      "comparison of two unmodified bodies wearing a tolerance it never used",
      "'<str>'" in _rendered(_ed_here, blank_strings=True), True)

# --- 9c  the refusal payloads ----------------------------------------------

for _cls in ("AlreadyRunning", "LockUnavailable"):
    _m_here = _method(_RUNNER_TREE, _cls, "__init__")
    _m_there = _method(_CONTROL_TREE, _cls, "__init__")
    check(f"9c  {_cls}.__init__ is IDENTICAL after ast.unparse. The body is "
          f"the refusal's PAYLOAD -- what an operator is shown and what a "
          f"caller reads off the exception -- and the two programs differ in "
          f"the docstring above it, not in what they carry",
          _rendered(_m_here), _rendered(_m_there))
    check(f"9c-b ...and both {_cls}.__init__ definitions were found",
          (_m_here is not None, _m_there is not None), (True, True))

# --- 9d  the acquisition, fact by fact -------------------------------------
#
# NOT COMPARED AS TEXT, and that is forced rather than lazy: control's takes the
# two exception classes and the record's extra field as PARAMETERS because it
# serves three programs, and this one hardcodes its own. So each fact the
# hardening pass established is asserted on both sides separately, which is
# also the form that names WHICH fact drifted when one does.

_acq_here = _definition(_RUNNER_TREE, "exclusive_run_lock")
_acq_there = _definition(_CONTROL_TREE, "hold_exclusive_lock")
check("9d  both acquisitions were found -- this file's exclusive_run_lock and "
      "control's hold_exclusive_lock. Without this every fact below compares "
      "two empty sets and passes",
      (_acq_here is not None, _acq_there is not None), (True, True))

_OPEN_FLAGS = {"O_RDWR", "O_CREAT", "O_NOFOLLOW"}
check("9d-b the open flags are the same set on both sides, and O_NOFOLLOW is "
      "in it. Without that flag O_CREAT on an existing symlink opens the "
      "TARGET and the ftruncate below zeroes it -- the substitution the 0700 "
      "directory closes from the other end",
      (_names_used(_acq_here) & _OPEN_FLAGS,
       _names_used(_acq_there) & _OPEN_FLAGS),
      (_OPEN_FLAGS, _OPEN_FLAGS))

_FLOCK_FLAGS = {"LOCK_EX", "LOCK_NB"}
check("9d-c the flock flags are the same set on both sides: EXCLUSIVE and "
      "NON-BLOCKING. Drop NB on either side and a second run WAITS instead of "
      "being refused, which is the outcome both locks exist to prevent",
      (_names_used(_acq_here) & _FLOCK_FLAGS,
       _names_used(_acq_there) & _FLOCK_FLAGS),
      (_FLOCK_FLAGS, _FLOCK_FLAGS))

_SEQUENCE = ("flock", "ftruncate", "write", "fsync", "close")
check("9d-d the same five syscalls are made on both sides, and each exactly "
      "once: flock, ftruncate, write, fsync, close",
      tuple(len(_calls(_acq_here, _c)) for _c in _SEQUENCE),
      tuple(len(_calls(_acq_there, _c)) for _c in _SEQUENCE))
check("9d-e ...and that count is 1 for each rather than 0 for each, which two "
      "empty walks would also satisfy",
      tuple(len(_calls(_acq_there, _c)) for _c in _SEQUENCE),
      (1, 1, 1, 1, 1))


def _truncate_after_flock(node):
    """Is the ftruncate BELOW the flock? Line order, on the one function.

    THE ORDERING IS THE WHOLE SAFETY PROPERTY AND A CALL-COUNT CANNOT SEE IT.
    Truncate first and a run that is about to be REFUSED has already zeroed the
    holder's record on its way to being told no -- so the refusal names nobody
    and an operator has nothing to act on.
    """
    if node is None:
        return None
    flock = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "flock"]
    trunc = [n.lineno for n in ast.walk(node) if isinstance(n, ast.Call)
             and getattr(n.func, "attr", None) == "ftruncate"]
    if not flock or not trunc:
        return None
    return min(trunc) > max(flock)


check("9d-f THE RECORD IS WRITTEN ONLY AFTER THE LOCK IS HELD, on both sides: "
      "the ftruncate sits BELOW the flock. Reversed, a run that is about to be "
      "refused zeroes the holder's record on its way to being told no, and the "
      "refusal then names nobody",
      (_truncate_after_flock(_acq_here), _truncate_after_flock(_acq_there)),
      (True, True))

check("9d-g the close is in a `finally`, on both sides -- which is what makes "
      "the release unconditional rather than dependent on the block returning "
      "normally. The kernel releases on process death; this is what releases "
      "on an exception",
      (bool(_acq_here is not None and any(
           any(getattr(c.func, "attr", None) == "close"
               for c in ast.walk(t) if isinstance(c, ast.Call))
           for n in ast.walk(_acq_here) if isinstance(n, ast.Try)
           for t in n.finalbody)),
       bool(_acq_there is not None and any(
           any(getattr(c.func, "attr", None) == "close"
               for c in ast.walk(t) if isinstance(c, ast.Call))
           for n in ast.walk(_acq_there) if isinstance(n, ast.Try)
           for t in n.finalbody))),
      (True, True))

check("9d-h the timestamp is taken from gmtime on both sides and carries an "
      "explicit Z. A local time suffixed Z parses cleanly, sorts cleanly and "
      "is wrong by the writer's offset -- which is read by somebody deciding "
      "whether the holder is stuck, often from a log written in another region",
      (sorted({ast.unparse(a) for n in ast.walk(_acq_here) if isinstance(
           n, ast.Call) and getattr(n.func, "attr", None) == "strftime"
           for a in n.args}),
       sorted({ast.unparse(a) for n in ast.walk(_acq_there) if isinstance(
           n, ast.Call) and getattr(n.func, "attr", None) == "strftime"
           for a in n.args})),
      (["'%Y-%m-%dT%H:%M:%SZ'", "time.gmtime()"],
       ["'%Y-%m-%dT%H:%M:%SZ'", "time.gmtime()"]))

check("9d-i the file mode passed to os.open is the same named constant on "
      "both sides",
      ("LOCK_FILE_MODE" in {n.id for n in ast.walk(_acq_here)
                            if isinstance(n, ast.Name)},
       "LOCK_FILE_MODE" in {n.id for n in ast.walk(_acq_there)
                            if isinstance(n, ast.Name)}),
      (True, True))


def _module_constant(tree, name):
    """A module-level literal by name, or a marker. NEVER imports the module.

    READ OUT OF THE AST RATHER THAN IMPORTED, which is what keeps this file's
    subject the launcher. `oncotriage/control.py` imports nothing from the
    project so importing it would be cheap -- and it would still make a test
    about `tests/run_serial_tests.py` fail when the PACKAGE is broken, which is
    the whole property that file's no-project-imports rule exists to preserve.
    """
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        return ast.literal_eval(node.value)
                    except ValueError:
                        return "<not a literal>"
    return "<not found>"


# THE TWO MODE CONSTANTS ARE PINNED ON BOTH SIDES, AND THAT CLOSES A GAP THIS
# CONSOLIDATION INHERITED RATHER THAN INTRODUCED. At HEAD these values were
# asserted for THIS file's copy alone: `oncotriage/batch/runner.py` and
# `oncotriage/ablation/study.py` each declared their own 0o700 and 0o600 and no
# test anywhere pinned either. Measured by the consolidation pass's revert
# matrix, which planted 0o777 and 0o666 into the shared constants and found
# them MISSED by every existing test.
#
# WIDENING EITHER IS THE SUBSTITUTION, RE-OPENED. A 0o777 lock directory is one
# another user can claim a name in, which is exactly what the per-user
# directory closes; a 0o666 lock file is one that can be rewritten under a
# holder that is still running, so a refused run reads a record somebody else
# planted.
_MODE_HERE = (srt.LOCK_DIRECTORY_MODE, srt.LOCK_FILE_MODE)
_MODE_THERE = (_module_constant(_CONTROL_TREE, "LOCK_DIRECTORY_MODE"),
               _module_constant(_CONTROL_TREE, "LOCK_FILE_MODE"))
check("9d-j the directory and file modes are OWNER-ONLY on both sides -- 0700 "
      "and 0600 -- and the two sides agree. Widening either re-opens the "
      "substitution the per-user directory and O_NOFOLLOW close between them",
      (_MODE_HERE, _MODE_THERE, _MODE_HERE == _MODE_THERE),
      ((0o700, 0o600), (0o700, 0o600), True))

# --- 9e  the KEY is realpath on both sides ---------------------------------
#
# THIS ONE IS NOT IN THE ACQUISITION on either side -- it is in whatever derives
# the lock file's NAME -- so it is asked of those functions rather than of the
# lock. `abspath` does not resolve symlinks, so one tree reached through two
# names hashed to two digests, took two lock files, and BOTH RAN.

check("9e  the lock file's name is derived from a REALPATH on both sides, and "
      "abspath appears in neither. abspath normalizes `.`, `..` and the "
      "working directory and STOPS THERE, so two names for one tree take two "
      "locks and both runs proceed -- the exact overlap through the one route "
      "the lock could not see",
      (_names_used(_definition(_RUNNER_TREE, "lock_path")) & {"realpath",
                                                              "abspath"},
       _names_used(_definition(_CONTROL_TREE, "lock_file_path")) & {"realpath",
                                                                    "abspath"}),
      ({"realpath"}, {"realpath"}))
check("9e-b ...and both name-deriving functions were found",
      (_definition(_RUNNER_TREE, "lock_path") is not None,
       _definition(_CONTROL_TREE, "lock_file_path") is not None),
      (True, True))

# --- 9f  THE CONTROLS ------------------------------------------------------
#
# EVERY COMPARISON ABOVE IS ALSO WHAT TWO FILES THAT HAVE STOPPED BEING
# COMPARED RETURN. Each is planted into an in-memory COPY of control.py's tree
# -- never the file, which 9g hashes -- and required to fail.

_PLANTS = 0
_CAUGHT = 0


def _planted(source, find, replace):
    """control.py's source with one substitution, asserted to have taken."""
    global _PLANTS
    _PLANTS += 1
    if source.count(find) != 1:
        return None
    return source.replace(find, replace, 1)


def _control_after(find, replace):
    src = _planted(_CONTROL_TEXT, find, replace)
    return None if src is None else ast.parse(src)


def _fires(label, tree, compare):
    """Require `compare(tree)` to DIFFER from what the shipped tree gives."""
    global _CAUGHT
    if tree is None:
        check(label + " [PLANT-FAILED: the marker was not found exactly once]",
              "planted", "not planted")
        return
    same = compare(tree) == compare(_CONTROL_TREE)
    if not same:
        _CAUGHT += 1
    check(label, same, False)


_fires("9f-1 a control that turns control.py's lstat into a stat is CAUGHT by "
       "9b -- the string blanking does not hide it",
       _control_after("info = os.lstat(root)", "info = os.stat(root)"),
       lambda t: _rendered(_definition(t, "ensure_lock_directory"),
                           blank_strings=True))

_fires("9f-2 a control that drops O_NOFOLLOW is CAUGHT by 9d-b",
       _control_after("os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW",
                      "os.O_RDWR | os.O_CREAT"),
       lambda t: _names_used(_definition(t, "hold_exclusive_lock"))
       & _OPEN_FLAGS)

_fires("9f-3 a control that drops LOCK_NB -- turning a refusal into a WAIT -- "
       "is CAUGHT by 9d-c",
       _control_after("fcntl.LOCK_EX | fcntl.LOCK_NB", "fcntl.LOCK_EX"),
       lambda t: _names_used(_definition(t, "hold_exclusive_lock"))
       & _FLOCK_FLAGS)

_fires("9f-4 a control that moves the ftruncate ABOVE the flock -- so a "
       "refused run zeroes the holder's record -- is CAUGHT by 9d-f",
       _control_after(
           """        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)""",
           """        os.ftruncate(fd, 0)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)"""),
       lambda t: _truncate_after_flock(_definition(t, "hold_exclusive_lock")))

_fires("9f-5 a control that swaps gmtime for localtime while KEEPING the Z is "
       "CAUGHT by 9d-h -- which is the whole reason that check reads the "
       "source rather than comparing a rendered stamp this machine's own "
       "timezone could make agree",
       _control_after('time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())',
                      'time.strftime("%Y-%m-%dT%H:%M:%SZ", time.localtime())'),
       lambda t: sorted({ast.unparse(a) for n in ast.walk(
           _definition(t, "hold_exclusive_lock")) if isinstance(n, ast.Call)
           and getattr(n.func, "attr", None) == "strftime" for a in n.args}))

_fires("9f-6 a control that swaps realpath for abspath in the key is CAUGHT "
       "by 9e",
       _control_after("os.path.realpath(str(key))", "os.path.abspath(str(key))"),
       lambda t: _names_used(_definition(t, "lock_file_path"))
       & {"realpath", "abspath"})

_fires("9f-7 a control that drops the uid ownership check is CAUGHT by 9b",
       _control_after("    if info.st_uid != os.getuid():",
                      "    if False and info.st_uid != os.getuid():"),
       lambda t: _rendered(_definition(t, "ensure_lock_directory"),
                           blank_strings=True))

_fires("9f-8 a control that widens the group/other-writable mask is CAUGHT "
       "by 9b",
       _control_after("stat.S_IWGRP | stat.S_IWOTH", "stat.S_IWOTH"),
       lambda t: _rendered(_definition(t, "ensure_lock_directory"),
                           blank_strings=True))

_fires("9f-9 a control that drops the fsync is CAUGHT by 9d-d -- an unsynced "
       "record is one a refused run can read as empty",
       _control_after("        os.fsync(fd)\n", ""),
       lambda t: tuple(len(_calls(_definition(t, "hold_exclusive_lock"), c))
                       for c in _SEQUENCE))

_fires("9f-10 a control that widens control.py's LOCK_DIRECTORY_MODE to 0777 "
       "is CAUGHT by 9d-j. Planted by the consolidation pass's revert matrix "
       "and MISSED by every test in the suite before that check existed",
       _control_after("LOCK_DIRECTORY_MODE = 0o700",
                      "LOCK_DIRECTORY_MODE = 0o777"),
       lambda t: _module_constant(t, "LOCK_DIRECTORY_MODE"))

_fires("9f-11 a control that widens control.py's LOCK_FILE_MODE to 0666 -- so "
       "a holder's record can be rewritten under it -- is CAUGHT by 9d-j",
       _control_after("LOCK_FILE_MODE = 0o600", "LOCK_FILE_MODE = 0o666"),
       lambda t: _module_constant(t, "LOCK_FILE_MODE"))

# --- 9h  THE THREE PREFIXES ARE PAIRWISE DISTINCT --------------------------
#
# NOTHING ASSERTED THIS BEFORE, in any of the three files, although the code and
# the project notes both call it LOAD-BEARING. Measured by the consolidation
# pass's revert matrix: renaming the BATCH runner's prefix to a shared one was
# MISSED by every test in the suite, and so was renaming this file's to the
# batch runner's. Only the ablation study's was pinned, in its own test, and
# only against the batch runner's -- so the pair it could not see was exactly
# the pair that had no owner.
#
# WHAT A COLLISION COSTS. All three locks live in ONE per-user directory (see
# `lock_directory`), so the prefix is the only thing that separates them. Two
# programs sharing one would refuse each other while guarding entirely
# different things -- and with no --db, the ablation study's state directory IS
# the batch runner's checkpoint directory, so the KEY does not separate them
# either. The symptom is a refusal naming a holder that has nothing to do with
# the thing being started, and the remediation it prints is the wrong program's.
#
# THIS FILE IS THE ONE PLACE ALL THREE ARE VISIBLE. It already reads
# `oncotriage/control.py` as text for section 9, so reading two more package
# sources costs nothing and still imports no package -- which is what keeps the
# subject of this file the launcher.

def _prefix_of(rel_path, const_name):
    """A package module's lock-file prefix, read from its source. No import."""
    full = os.path.join(os.path.dirname(_THIS_DIR), rel_path)
    if not os.path.isfile(full):
        return f"<{rel_path} not found>"
    return _module_constant(ast.parse(open(full, encoding="utf-8").read()),
                            const_name)


_PREFIXES = {
    "batch": _prefix_of("oncotriage/batch/runner.py", "LOCK_FILE_PREFIX"),
    "ablation": _prefix_of("oncotriage/ablation/study.py", "LOCK_FILE_PREFIX"),
    "serial": srt.lock_path(_CHECKOUT).rsplit(os.sep, 1)[-1].rsplit("-", 1)[0]
              + "-",
}
check("9h  the three programs' lock-file prefixes are PAIRWISE DISTINCT. All "
      "three locks live in one per-user directory, so the prefix is the only "
      "thing separating them -- and with no --db the study's state directory "
      "IS the batch runner's checkpoint directory, so the key does not "
      "separate them either. Two programs sharing a prefix refuse each other "
      "while guarding different things",
      len(set(_PREFIXES.values())), 3)
check("9h-b ...and each is the one it is supposed to be, so 9h is not three "
      "'not found' markers that happen to differ",
      _PREFIXES,
      {"batch": "oncotriage-batch-run-",
       "ablation": "oncotriage-ablation-run-",
       "serial": "oncotriage-serial-tests-"})
check("9h-c ...and the SERIAL one is read off a real lock path this file "
      "derived rather than off a literal, so it is what the shipped launcher "
      "would actually create",
      os.path.basename(srt.lock_path(_CHECKOUT)).startswith(
          "oncotriage-serial-tests-"), True)

check("9f-x every plant was applied and every one fired. A plant that matched "
      "nothing is a PLANT-FAILED above rather than a check reported as weak",
      (_PLANTS, _CAUGHT), (11, 11))

check("9g  oncotriage/control.py is byte-unchanged: every plant above went "
      "into an in-memory copy",
      hashlib.sha256(open(_CONTROL_PATH, "rb").read()).hexdigest(),
      _CONTROL_SHA)


#------------------------------------------------------------------------------


# ===========================================================================
# 10.  THE PRISTINE-COPY GUARD, DRIVEN WITH A REAL SIGKILL
# ===========================================================================
#
# THE LOCK IS ABOUT TWO RUNS. THIS IS ABOUT ONE RUN DYING, which is a different
# defect with the same consequence: `oncotriage/config.py` or
# `cancer_code_registry.py` left holding a deliberately planted defect, silently,
# with the only copy of what it replaced destroyed along with the temp directory
# the dead test had put it in.
#
# EVERY ARM BELOW IS DRIVEN THROUGH THE SHIPPED ENTRY POINT AS A REAL
# SUBPROCESS, WITH A REAL SIGNAL. A SIGKILL cannot be delivered to the process
# asserting about it, and an in-process `raise SystemExit` would test the test
# rather than the shipped handler -- the argument
# `tests/test_runner_sigterm_shutdown.py` makes for its own subprocesses,
# adopted here.
#
# THE PAYLOAD IS STUBS, on section 7's design and for its reason: the checkout
# is a `tempfile.mkdtemp()` carrying a BYTE-IDENTICAL copy of the runner beside
# stand-ins at every path `SERIAL_TESTS` and `WRITER_OWNED_FILES` name. So a
# BROKEN guard costs a corrupted stand-in in a temp directory rather than a
# corrupted `oncotriage/`.

section("10. The pristine-copy guard, driven")

_PRISTINE_TEXT = "# PRISTINE\nDATA_SNAPSHOT_DATE = '2026-08-04'\n"
_PLANTED_TEXT = "# PLANTED BY A TEST THAT NEVER GOT TO RESTORE IT\nDATA_SNAPSHOT_DATE = '1999-01-01'\n"
_PRISTINE_SHA = hashlib.sha256(_PRISTINE_TEXT.encode()).hexdigest()
_PLANTED_SHA = hashlib.sha256(_PLANTED_TEXT.encode()).hexdigest()
check("10a the two contents differ, so every comparison below discriminates "
      "(non-degeneracy)", _PRISTINE_SHA != _PLANTED_SHA, True)

# THE TARGET IS THE FIRST WRITER-OWNED FILE, read off the module rather than
# named here: an entry reordered or renamed in WRITER_OWNED_FILES must not
# leave this drive planting into a file the guard does not cover, which would
# report the guard as broken when it is the drive that moved.
_TARGET_REL = srt.WRITER_OWNED_FILES[0][0]


def build_guard_checkout(root, first_stub):
    """A throwaway checkout: the real runner, five stubs, the writer files.

    Returns the path to the copied entry point. The copy is sha256-compared by
    the caller, so what is driven is the shipped guard rather than a paraphrase
    of it.
    """
    os.makedirs(os.path.join(root, "tests"), exist_ok=True)
    entry = os.path.join(root, "tests", "run_serial_tests.py")
    shutil.copy2(_RUNNER_PATH, entry)
    for i, (rel, _why) in enumerate(srt.SERIAL_TESTS):
        dest = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as fh:
            fh.write(first_stub if i == 0 else _NOOP_STUB)
    for rel, _writer in srt.WRITER_OWNED_FILES:
        dest = os.path.join(root, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w") as fh:
            fh.write(_PRISTINE_TEXT)
    return entry


def planting_stub(root, ready, release):
    """A stub that CORRUPTS the writer-owned file, then parks on a file.

    PARKS RATHER THAN SLEEPS, on section 7's measured lesson: a sleep makes the
    assertion a statement about this machine's scheduler. While it is parked the
    tree is provably corrupt and the runner is provably alive, which is the
    exact instant every arm below needs.
    """
    target = os.path.join(root, _TARGET_REL)
    return "\n".join([
        "import os, sys, time",
        f"open({target!r}, 'w').write({_PLANTED_TEXT!r})",
        f"open({ready!r}, 'w').write(str(os.getpid()))",
        "_deadline = time.time() + 90",
        f"while not os.path.exists({release!r}) and time.time() < _deadline:",
        "    time.sleep(0.02)",
        "sys.exit(0)",
    ])


def restoring_stub(root, ready, release):
    """A stub that corrupts, parks, and then restores -- an honest writer."""
    target = os.path.join(root, _TARGET_REL)
    return "\n".join([
        "import os, sys, time",
        f"open({target!r}, 'w').write({_PLANTED_TEXT!r})",
        f"open({ready!r}, 'w').write(str(os.getpid()))",
        "_deadline = time.time() + 90",
        f"while not os.path.exists({release!r}) and time.time() < _deadline:",
        "    time.sleep(0.02)",
        f"open({target!r}, 'w').write({_PRISTINE_TEXT!r})",
        "sys.exit(0)",
    ])


def wait_for(path, proc, timeout=90):
    """Wait for `path` while `proc` is alive. Returns True if it appeared."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(path):
            return True
        if proc.poll() is not None:
            return os.path.exists(path)
        time.sleep(0.02)
    return os.path.exists(path)


def position(text, needle):
    """Where `needle` starts in `text`, or a NAMED absence -- never a raise.

    `str.index` RAISES, AND IT RAISES ON EXACTLY THE DEFECT THE ORDERING CHECK
    BELOW EXISTS TO CATCH: a revert that stops the repair being announced makes
    the announcement absent, and the ordering assertion built on `.index` then
    dies while `check`'s argument is being evaluated -- one traceback where the
    run owed a summary and a hundred and eighty results. MEASURED, not reasoned
    about: the first version of this file did exactly that under two of its own
    eight reverts. This project has shipped that abort shape thirteen times.
    """
    at = text.find(needle)
    return at if at >= 0 else f"<absent: {needle[:32]!r}>"


def pid_in(path):
    """The pid a stub wrote into `path`, or a named absence -- never a raise.

    Same argument as `position`: `int(open(path).read())` raises for a file that
    is not there, which is what a revert that stops the run reaching its first
    test produces.
    """
    try:
        with open(path) as fh:
            return int(fh.read().strip())
    except (OSError, ValueError):
        return None


def sha_of(path):
    if not os.path.isfile(path):
        return "absent"
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def copies_in(root):
    """Every pristine copy sitting in `root`, as basenames, sorted."""
    return sorted(os.path.basename(b)
                  for b, _t, _p in srt.find_pristine_backups(root))


_GUARD_ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
_GUARD_LOGS = os.path.join(_TMP, "guard-logs")
os.makedirs(_GUARD_LOGS, exist_ok=True)


def start_runner(entry, tag):
    """Launch the entry point with its output going to a FILE, not a pipe.

    A PIPE IS A HANG WAITING TO HAPPEN HERE, and the revert matrix is what found
    it rather than reading. Every arm below deliberately stops the runner while
    a test subprocess is still alive, and that child INHERITS the pipe -- so
    `communicate()` on the parent blocks until the ORPHAN exits, not until the
    runner does. Under a revert that removes the signal handlers the orphan
    holds it for its full park and the file does not finish in two minutes: a
    hang, which reads exactly like an abort and reports neither a summary nor a
    failure.

    A file has no reader to block, so `wait()` returns the moment the runner is
    gone and the output is read afterwards from disk.
    """
    path = os.path.join(_GUARD_LOGS, f"{tag}.log")
    handle = open(path, "wb")
    proc = subprocess.Popen([sys.executable, entry], stdout=handle,
                            stderr=subprocess.STDOUT, env=_GUARD_ENV)
    proc._oncotriage_log = path                       # noqa: SLF001 -- ours
    proc._oncotriage_handle = handle                  # noqa: SLF001 -- ours
    return proc


def runner_output(proc):
    """Everything the runner wrote, read off disk. Safe after a kill."""
    try:
        proc._oncotriage_handle.close()               # noqa: SLF001 -- ours
    except OSError:
        pass
    try:
        with open(proc._oncotriage_log, "rb") as fh:  # noqa: SLF001 -- ours
            return fh.read().decode("utf-8", "replace")
    except OSError:
        return ""


# --- 10b-10h  SIGKILL: nothing runs, and the NEXT invocation repairs --------
#
# SIGKILL IS THE ARM THAT MATTERS AND IT IS WHY THE COPY IS A FILE. No handler,
# no `finally` and no context manager executes when the kernel removes the
# process, so the in-process restore is unreachable BY CONSTRUCTION here. What
# has to work instead is that the copy outlived the run and that the successor
# finds it.

_KILL_ROOT = os.path.join(_TMP, "guard-sigkill")
_KILL_READY = os.path.join(_TMP, "guard-kill-ready")
_KILL_RELEASE = os.path.join(_TMP, "guard-kill-release")
_KILL_ENTRY = build_guard_checkout(
    _KILL_ROOT, planting_stub(_KILL_ROOT, _KILL_READY, _KILL_RELEASE))
_KILL_TARGET = os.path.join(_KILL_ROOT, _TARGET_REL)

check("10b the driven entry point is BYTE-IDENTICAL to the shipped one, so "
      "what is measured below is the shipped guard",
      sha_of(_KILL_ENTRY), _RUNNER_SHA)

_killed = start_runner(_KILL_ENTRY, "kill")
_reached = wait_for(_KILL_READY, _killed)
check("10c the run reached its first test and that test has corrupted the "
      "tree (non-degeneracy: every assertion below is about a tree that is "
      "really damaged)",
      (_reached, sha_of(_KILL_TARGET)), (True, _PLANTED_SHA))
# DERIVED FROM `WRITER_OWNED_FILES`, NOT WRITTEN OUT: the guard takes one copy
# per declared file, so a hand-written list here would fail the day a third
# writer is declared and would name the guard rather than itself.
_EXPECTED_COPIES = sorted(
    os.path.basename(srt.backup_path(os.path.join(_KILL_ROOT, rel),
                                     _killed.pid))
    for rel, _writer in srt.WRITER_OWNED_FILES)
check("10d ...and the runner took a pristine copy of EVERY writer-owned file "
      "BEFORE that test ran, each carrying its own pid",
      copies_in(_KILL_ROOT), _EXPECTED_COPIES)
check("10d ...and there is more than one of them, so 10d is not a statement "
      "about a single-entry table (non-degeneracy)",
      len(_EXPECTED_COPIES) > 1, True)

_STUB_PID = pid_in(_KILL_READY)
os.kill(_killed.pid, signal.SIGKILL)
_killed.wait(timeout=60)
if _STUB_PID is not None:
    try:
        os.kill(_STUB_PID, signal.SIGKILL)
    except OSError:
        pass
check("10e a real SIGKILL leaves the tree CORRUPT and the copy in place -- no "
      "handler, no `finally`, no context manager ran, which is the whole "
      "reason the copy is a file beside its target rather than a temp "
      "directory in a dead process",
      (_killed.returncode, sha_of(_KILL_TARGET), copies_in(_KILL_ROOT)),
      (-signal.SIGKILL, _PLANTED_SHA, _EXPECTED_COPIES))

# THE SUCCESSOR. Its first test is a no-op, so the only thing that can change
# the file is the repair.
with open(os.path.join(_KILL_ROOT, srt.SERIAL_TESTS[0][0]), "w") as _fh:
    _fh.write(_NOOP_STUB)
_next = subprocess.run([sys.executable, _KILL_ENTRY], capture_output=True,
                       text=True, env=_GUARD_ENV)
check("10f the NEXT invocation repairs the tree, byte-identically",
      (_next.returncode, sha_of(_KILL_TARGET)), (0, _PRISTINE_SHA))
check("10g ...and it ANNOUNCES what it repaired, naming the file and the pid "
      "of the run that left it",
      ("A PREVIOUS RUN DID NOT REACH ITS CLEANUP" in _next.stdout,
       _TARGET_REL in _next.stdout,
       f"pid {_killed.pid}" in _next.stdout), (True, True, True))
_AT_REPAIR = position(_next.stdout, "A PREVIOUS RUN DID NOT REACH ITS CLEANUP")
_AT_RUN = position(_next.stdout, "SERIAL TEST RUN")
check("10h ...BEFORE it runs anything -- a repair announced after the suite "
      "has already read the tree it repaired is a report, not a repair",
      (isinstance(_AT_REPAIR, int), isinstance(_AT_RUN, int),
       _AT_REPAIR < _AT_RUN if isinstance(_AT_REPAIR, int)
       and isinstance(_AT_RUN, int) else (_AT_REPAIR, _AT_RUN)),
      (True, True, True))
check("10i ...and the copy is gone, so the run after THIS one announces "
      "nothing", copies_in(_KILL_ROOT), [])


# --- 10j-10n  SIGTERM reaches the cleanup, in this process's own lifetime ---
#
# THE ARM SIGKILL CANNOT COVER. `docker stop`, systemd's stop and a bare `kill`
# all send SIGTERM, whose CPython default terminates the process outright -- no
# exception, no unwinding, no `finally`. Converting it to a SystemExit is what
# puts the restore back on the path.

_TERM_ROOT = os.path.join(_TMP, "guard-sigterm")
_TERM_READY = os.path.join(_TMP, "guard-term-ready")
_TERM_RELEASE = os.path.join(_TMP, "guard-term-release")
_TERM_ENTRY = build_guard_checkout(
    _TERM_ROOT, planting_stub(_TERM_ROOT, _TERM_READY, _TERM_RELEASE))
_TERM_TARGET = os.path.join(_TERM_ROOT, _TARGET_REL)

_termed = start_runner(_TERM_ENTRY, "term")
_reached = wait_for(_TERM_READY, _termed)
check("10j the SIGTERM arm reached a corrupt tree too (non-degeneracy)",
      (_reached, sha_of(_TERM_TARGET)), (True, _PLANTED_SHA))

os.kill(_termed.pid, signal.SIGTERM)
_termed.wait(timeout=120)
_term_out = runner_output(_termed)
check("10k SIGTERM exits 143 rather than being absorbed or ignored",
      _termed.returncode, srt.EXIT_SIGNALLED)
check("10l ...and the tree is restored IN THIS RUN's own lifetime, without "
      "waiting for a successor", sha_of(_TERM_TARGET), _PRISTINE_SHA)
check("10m ...and the copy is removed, so the next run announces no repair",
      copies_in(_TERM_ROOT), [])
check("10n ...and the shutdown is announced on stderr, which is where it has "
      "to be: Python block-buffers stdout when it is not a tty, so a line "
      "written there can materialise at interpreter exit or not at all",
      "Signal" in _term_out and "Restoring" in _term_out, True)


# --- 10n2  THE CHILD IS STOPPED, WITH A BOUND, AND ASKED FIRST -------------
#
# TWO OBVIOUS CLAIMS ABOUT `_terminate_child` WERE WRITTEN AS CHECKS HERE AND
# BOTH WERE FALSE. They are recorded because a check that cannot discriminate is
# worse than no check: it passes, so it looks like it is working.
#
#   "without it the runner restores while a live writer is still planting"
#       -- driven: a revert of `_run_one` to `subprocess.run` reported 182
#          passed, 0 failed. `subprocess.run`'s own bare `except:` already
#          calls `process.kill()`, so no orphan is produced either way.
#   "SIGTERM first lets the child's `finally` run and restore its own file"
#       -- driven: a stub whose `finally` writes a marker was sent SIGTERM
#          through this function and the marker was NOT written. CPython does
#          not convert SIGTERM into an exception; the default disposition
#          terminates the process, so a plain Python child's `finally` does not
#          run for it any more than for SIGKILL.
#
# WHAT IS LEFT IS SMALLER AND IS WHAT IS CHECKED: the child is not left running,
# the wait is BOUNDED (`subprocess.run`'s is not), and it is ASKED before it is
# shot -- which buys nothing for these five children and is the right thing for
# a runner to send. THE PRISTINE COPY IS THE MECHANISM; this is hygiene.

_GRACEFUL_ROOT = os.path.join(_TMP, "guard-graceful")
_GRACEFUL_READY = os.path.join(_TMP, "guard-graceful-ready")
_GRACEFUL_TARGET_ABS = os.path.join(_GRACEFUL_ROOT, _TARGET_REL)
_GRACEFUL_STUB = "\n".join([
    "import os, sys, time",
    f"open({_GRACEFUL_TARGET_ABS!r}, 'w').write({_PLANTED_TEXT!r})",
    f"open({_GRACEFUL_READY!r}, 'w').write(str(os.getpid()))",
    "time.sleep(120)",
])
_GRACEFUL_ENTRY = build_guard_checkout(_GRACEFUL_ROOT, _GRACEFUL_STUB)

_graceful = start_runner(_GRACEFUL_ENTRY, "graceful")
_reached = wait_for(_GRACEFUL_READY, _graceful)
_GRACEFUL_PID = pid_in(_GRACEFUL_READY)
check("10n2 the graceful arm reached a corrupt tree with a writer that would "
      "sleep for two minutes if nobody stopped it (non-degeneracy)",
      (_reached, sha_of(_GRACEFUL_TARGET_ABS)), (True, _PLANTED_SHA))
_t0 = time.time()
os.kill(_graceful.pid, signal.SIGTERM)
_graceful.wait(timeout=120)
_ELAPSED = time.time() - _t0
runner_output(_graceful)
check("10n2 ...and the runner did not wait for that sleep. The bound is the "
      "point: subprocess.run's cleanup is kill() then an UNBOUNDED wait(), so "
      "a child stuck in uninterruptible I/O holds the runner, its lock and the "
      "un-restored tree open with no deadline",
      _ELAPSED < srt.CHILD_SHUTDOWN_GRACE_SECONDS, True)
check("10n2 ...and the child is gone rather than merely quiet -- it had 118 "
      "seconds of sleep left",
      guarded(os.kill, _GRACEFUL_PID or -1, 0) is not None, True)
check("10n2 ...and the tree is pristine and no copy of ours is left",
      (sha_of(_GRACEFUL_TARGET_ABS), copies_in(_GRACEFUL_ROOT)),
      (_PRISTINE_SHA, []))
check("10n2 ...and the grace is bounded and finite, so a test that hangs "
      "after being asked to stop cannot hold the tree open until an "
      "orchestrator SIGKILLs the RUNNER -- the one shutdown the runner cannot "
      "clean up after",
      isinstance(srt.CHILD_SHUTDOWN_GRACE_SECONDS, (int, float))
      and 0 < srt.CHILD_SHUTDOWN_GRACE_SECONDS < 300, True)

# STRUCTURAL, BECAUSE NO BEHAVIOURAL CHECK ABOVE CAN SEE WHICH SIGNAL WAS SENT:
# these five children die of either one without unwinding, so the tree looks the
# same. SIGTERM-first is still what a runner should send -- it is catchable and
# SIGKILL is not -- and a `terminate()` quietly replaced by a `kill()` would
# leave every check above green.
_TC = next((n for n in ast.walk(ast.parse(_RUNNER_TEXT))
            if isinstance(n, ast.FunctionDef) and n.name == "_terminate_child"),
           None)
check("10n3 _terminate_child was found (non-degeneracy for the two below)",
      _TC is not None, True)
_TC_CALLS = [getattr(n.func, "attr", "") for n in
             ast.walk(_TC or ast.Module(body=[], type_ignores=[]))
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
check("10n3 ...and it asks with terminate() BEFORE it insists with kill()",
      (_TC_CALLS.index("terminate") if "terminate" in _TC_CALLS else -1)
      < (_TC_CALLS.index("kill") if "kill" in _TC_CALLS else -1)
      and "terminate" in _TC_CALLS and "kill" in _TC_CALLS, True)
check("10n3 ...and every wait it does is bounded -- an unbounded one is "
      "exactly what subprocess.run's cleanup does",
      all(any(k.arg == "timeout" for k in n.keywords)
          for n in ast.walk(_TC or ast.Module(body=[], type_ignores=[]))
          if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "wait"),
      True)


# --- 10o-10r  the ordinary path is unchanged and leaves nothing behind -------

_OK_ROOT = os.path.join(_TMP, "guard-clean")
_OK_READY = os.path.join(_TMP, "guard-ok-ready")
_OK_RELEASE = os.path.join(_TMP, "guard-ok-release")
_OK_ENTRY = build_guard_checkout(
    _OK_ROOT, restoring_stub(_OK_ROOT, _OK_READY, _OK_RELEASE))
_OK_TARGET = os.path.join(_OK_ROOT, _TARGET_REL)
open(_OK_RELEASE, "w").close()          # never parks; runs straight through
_ok = subprocess.run([sys.executable, _OK_ENTRY], capture_output=True,
                     text=True, env=_GUARD_ENV)
check("10o a run whose writers restore their own files exits 0 and leaves the "
      "tree exactly as it found it",
      (_ok.returncode, sha_of(_OK_TARGET)), (0, _PRISTINE_SHA))
check("10p ...and leaves no pristine copy behind", copies_in(_OK_ROOT), [])
check("10q ...and says NOTHING about repairs or about the tree, because a "
      "line printed on every clean run is a line a reader learns to skip and "
      "this one has to be read the once it appears",
      ("A PREVIOUS RUN DID NOT REACH ITS CLEANUP" in _ok.stdout,
       "THE TREE WAS NOT WHAT THIS RUN FOUND" in _ok.stdout), (False, False))

# THE OTHER HALF OF 10q: a writer that does NOT restore is announced. On the
# real suite both do and assert byte-identity, so this block appearing means one
# of them did not -- and this guard is the only thing positioned to notice.
_BAD_ROOT = os.path.join(_TMP, "guard-unrestored")
_BAD_ENTRY = build_guard_checkout(
    _BAD_ROOT, planting_stub(_BAD_ROOT, os.path.join(_TMP, "bad-ready"),
                             os.path.join(_TMP, "bad-release")))
open(os.path.join(_TMP, "bad-release"), "w").close()
_BAD_TARGET = os.path.join(_BAD_ROOT, _TARGET_REL)
_bad = subprocess.run([sys.executable, _BAD_ENTRY], capture_output=True,
                      text=True, env=_GUARD_ENV)
check("10r a writer that plants and does NOT restore is put back AND named",
      (sha_of(_BAD_TARGET), "THE TREE WAS NOT WHAT THIS RUN FOUND" in _bad.stdout,
       copies_in(_BAD_ROOT)), (_PRISTINE_SHA, True, []))


# --- 10s-10u  a copy that cannot be taken refuses, having planted nothing ----

_NOFILE_ROOT = os.path.join(_TMP, "guard-missing-target")
_NOFILE_ENTRY = build_guard_checkout(
    _NOFILE_ROOT, _NOOP_STUB.replace("import sys", "import sys, os"))
os.remove(os.path.join(_NOFILE_ROOT, _TARGET_REL))
_nofile = subprocess.run([sys.executable, _NOFILE_ENTRY], capture_output=True,
                         text=True, env=_GUARD_ENV)
check("10s a writer-owned file that is not there exits "
      "EXIT_BACKUP_UNAVAILABLE rather than running the suite with nothing to "
      "put the tree back from",
      _nofile.returncode, srt.EXIT_BACKUP_UNAVAILABLE)
check("10t ...and it is a different code from every other refusal, because "
      "the remediation differs: 3 says wait, 4 says fix the temp directory, "
      "and this says the tree cannot be made safe to plant into",
      len({srt.EXIT_LOCKED, srt.EXIT_LOCK_UNAVAILABLE,
           srt.EXIT_BACKUP_UNAVAILABLE}), 3)
check("10u ...and NOTHING WAS RUN and no half-taken copy was left",
      ("SERIAL TEST RUN" in _nofile.stdout, copies_in(_NOFILE_ROOT)),
      (False, []))


# --- 10v-10z  the pure pieces, driven as functions of their arguments -------

check("10v a copy's name carries the target and the pid, and does NOT end in "
      "`.py` -- a `config_pristine.py` beside `config.py` would join "
      "test_package_invariants.py's package walk as a second module declaring "
      "every constant in it",
      (srt.backup_path("/x/config.py", 41).endswith(".py"),
       srt.backup_path("/x/config.py", 41)),
      (False, "/x/config.py" + srt.BACKUP_MARKER + "41"))
check("10w the pid is read back out of the name, and a name that only "
      "RESEMBLES one is not claimed -- the scan uses this as its membership "
      "test, so a guess here restores a real module from somebody's notes",
      (srt.backup_owner(srt.backup_path("/x/config.py", 41)),
       srt.backup_owner("/x/config.py" + srt.BACKUP_MARKER + "notes"),
       srt.backup_owner("/x/config.py")), (41, None, None))
check("10x a non-reading is a NAMED marker rather than a digest, and "
      "`_is_real_digest` refuses BOTH sentinels -- a predicate written against "
      "'absent' alone would restore a target from a copy nothing could read",
      (srt.file_digest(os.path.join(_TMP, "no-such-file")),
       srt._is_real_digest("absent"),
       srt._is_real_digest(srt.file_digest(_TMP)),
       srt._is_real_digest(_PRISTINE_SHA)),
      ("absent", False, False, True))
check("10y an interrupted copy leaves NO third file: atomic_copy renames into "
      "place, so a copy that exists is complete and a target is never seen "
      "half-written",
      guarded(lambda: srt.atomic_copy(os.path.join(_TMP, "no-such-file"),
                                      os.path.join(_TMP, "dest"))) is not None
      and sorted(n for n in os.listdir(_TMP)
                 if n.startswith(".serial-runner-tmp-")), [])
check("10z the announcement is silent when there was nothing to repair",
      srt.repair_lines([]), [])

# --- 10z2  A COPY THAT CANNOT BE READ IS NOT RESTORED FROM ------------------
#
# NEITHER REPAIR NOR RELEASE HAD A CONTROL FOR THIS GUARD AND THE REVERT MATRIX
# IS WHAT FOUND IT: removing `_is_real_digest` from `release_pristine_backups`
# reported 186 passed, 0 failed. The guard is the difference between "put the
# file back from the copy" and "overwrite the file with a sentinel string",
# which is the one outcome worse than having no copy at all.
#
# DRIVEN AS FUNCTIONS OF THEIR ARGUMENTS, which is this suite's control shape
# for a pure decision -- the state is a mapping naming a copy that is not there,
# and building one on disk would mean racing the very cleanup under test.

_Z2 = os.path.join(_TMP, "guard-unreadable")
os.makedirs(_Z2, exist_ok=True)
_Z2_TARGET = os.path.join(_Z2, "target.py")
with open(_Z2_TARGET, "w") as _fh:
    _fh.write(_PRISTINE_TEXT)
_Z2_MISSING = os.path.join(_Z2, "no-such-copy")
_Z2_DIR = os.path.join(_Z2, "a-directory")
os.makedirs(_Z2_DIR, exist_ok=True)

for _label, _copy in (("absent", _Z2_MISSING), ("unreadable", _Z2_DIR)):
    _rec = srt.release_pristine_backups({_Z2_TARGET: _copy})
    check(f"10z2 release: a copy that is {_label} is REPORTED and NOT restored "
          f"from -- the target keeps its content instead of being overwritten "
          f"with a sentinel string",
          ([r["action"] for r in _rec], sha_of(_Z2_TARGET)),
          (["unreadable-backup"], _PRISTINE_SHA))
    check(f"10z2 release: ...and the copy is not deleted either, because it is "
          f"the only record of what the file used to be ({_label})",
          any("backup_removed" in r for r in _rec), False)

# THE REPAIR SIDE HAS THE SAME GUARD AND NEEDS THE SAME CONTROL. Its input is a
# copy found on disk, so this one is built: an empty DIRECTORY at a name the
# scan claims, which `file_digest` reads as `unreadable:` and `os.path.isfile`
# would reject -- so the scan's own membership test is exercised too.
_Z3 = os.path.join(_TMP, "guard-repair-unreadable")
_Z3_TARGET_DIR = os.path.join(_Z3, os.path.dirname(_TARGET_REL))
os.makedirs(_Z3_TARGET_DIR, exist_ok=True)
_Z3_TARGET = os.path.join(_Z3, _TARGET_REL)
with open(_Z3_TARGET, "w") as _fh:
    _fh.write(_PLANTED_TEXT)
_Z3_COPY = srt.backup_path(_Z3_TARGET, 424242)
os.makedirs(_Z3_COPY, exist_ok=True)          # a DIRECTORY where a copy goes
_z3_out = srt.repair_pristine_backups(_Z3, out=lambda _line: None)
check("10z3 repair: the scan's membership test is `isfile`, so a DIRECTORY at "
      "a copy's name is not claimed and the corrupt target is left alone "
      "rather than restored from something unreadable",
      (_z3_out, sha_of(_Z3_TARGET)), ([], _PLANTED_SHA))
os.rmdir(_Z3_COPY)
with open(_Z3_COPY, "w") as _fh:
    _fh.write(_PRISTINE_TEXT)
_z3_out = srt.repair_pristine_backups(_Z3, out=lambda _line: None)
check("10z3 ...and a REAL copy at the same name is claimed and restored from "
      "(non-degeneracy: without this, 10z3 above would be satisfied by a scan "
      "that never claims anything)",
      ([r["action"] for r in _z3_out], sha_of(_Z3_TARGET)),
      (["restored"], _PRISTINE_SHA))


# --- 10z4  THE RESTORE IS VERIFIED BEFORE THE ONLY COPY IS DELETED ----------
#
# THE REVERT MATRIX FOUND THIS ONE TOO: deleting the read-back check from
# `repair_pristine_backups` reported 192 passed, 0 failed. It cannot be driven
# by breaking the filesystem -- an unwritable target makes `atomic_copy` RAISE,
# which is the `restore-failed` branch, a different one. The state this branch
# exists for is a copy that lands and then does not read back, and the only
# honest way to produce it is to make the READING lie.
#
# `file_digest` IS REBOUND INSIDE try/finally WITH THE RESTORE ASSERTED, which
# is this suite's accepted control shape where the subject is not a function of
# its argument. It is not a plant into a file: nothing on disk is edited and the
# module's sha256 is compared at the end of this run like every other.

_Z4 = os.path.join(_TMP, "guard-unverified")
_Z4_TARGET_DIR = os.path.join(_Z4, os.path.dirname(_TARGET_REL))
os.makedirs(_Z4_TARGET_DIR, exist_ok=True)
_Z4_TARGET = os.path.join(_Z4, _TARGET_REL)
with open(_Z4_TARGET, "w") as _fh:
    _fh.write(_PLANTED_TEXT)
_Z4_COPY = srt.backup_path(_Z4_TARGET, 515151)
with open(_Z4_COPY, "w") as _fh:
    _fh.write(_PRISTINE_TEXT)

_REAL_DIGEST = srt.file_digest
_Z4_SEEN = {"n": 0}


def _lying_digest(path):
    """Honest about the copy, and wrong about the target AFTER it is written.

    The first reading of the target is what the repair compares to decide it has
    work to do; the SECOND is the read-back. Only the second lies, so the repair
    really does copy and really does then fail to confirm it.
    """
    if os.path.abspath(path) == os.path.abspath(_Z4_TARGET):
        _Z4_SEEN["n"] += 1
        if _Z4_SEEN["n"] >= 2:
            return "0" * 64
    return _REAL_DIGEST(path)


try:
    srt.file_digest = _lying_digest
    _z4_out = srt.repair_pristine_backups(_Z4, out=lambda _line: None)
finally:
    srt.file_digest = _REAL_DIGEST
check("10z4 the rebind was put back, so nothing after this line is running "
      "against a lying reader", srt.file_digest is _REAL_DIGEST, True)
check("10z4 ...and the reading really did lie, so the check below is about the "
      "branch rather than about a no-op (non-degeneracy)",
      _Z4_SEEN["n"] >= 2, True)
check("10z4 a restore that does not read back is reported as UNVERIFIED and "
      "the copy is NOT deleted -- it is the only record of what the file was, "
      "and deleting it on a restore nobody confirmed destroys the evidence "
      "along with the file",
      ([r["action"] for r in _z4_out], os.path.isfile(_Z4_COPY)),
      (["restore-unverified"], True))
check("10z4 ...and the bytes really did land, so the copy is being kept "
      "because of the READING rather than because nothing happened",
      sha_of(_Z4_TARGET), _PRISTINE_SHA)


# --- 10z5  THE TWO FAILURE BRANCHES, DRIVEN FOR REAL -----------------------
#
# Both are reachable on an ordinary machine and neither was exercised by
# anything above: a guard that refuses AFTER taking some of its copies, and a
# restore that cannot be written. They are driven by creating the condition on
# disk rather than by patching, which is this suite's preference wherever the
# condition is producible.

_Z5 = os.path.join(_TMP, "guard-branches")
for _rel, _w in srt.WRITER_OWNED_FILES:
    os.makedirs(os.path.join(_Z5, os.path.dirname(_rel)), exist_ok=True)
    with open(os.path.join(_Z5, _rel), "w") as _fh:
        _fh.write(_PRISTINE_TEXT)

# (a) HALF-TAKEN. The LAST declared target is removed, so the guard takes the
#     earlier copies and then refuses. A copy from a run that never started is
#     not evidence of anything, and the next invocation would "repair" from it
#     and announce a repair that did not happen.
_Z5_LAST = os.path.join(_Z5, srt.WRITER_OWNED_FILES[-1][0])
os.remove(_Z5_LAST)
_z5_raise = raised(lambda: srt.pristine_guard(_Z5, out=lambda _l: None).__enter__())
check("10z5 a guard that refuses part-way leaves NO half-taken copy behind -- "
      "one would be 'repaired' from by the next invocation, which would then "
      "announce a repair that never happened",
      (type(_z5_raise).__name__ if _z5_raise else None,
       srt.find_pristine_backups(_Z5)),
      ("BackupUnavailable", []))
check("10z5 ...and the refusal names the file it could not copy",
      os.path.basename(getattr(_z5_raise, "target", "")),
      os.path.basename(srt.WRITER_OWNED_FILES[-1][0]))
check("10z5 ...and there really was an EARLIER target whose copy had already "
      "been taken, so (a) is not a statement about a one-entry table "
      "(non-degeneracy)", len(srt.WRITER_OWNED_FILES) > 1, True)

# (b) RESTORE-FAILED. The copy is fine and the target's directory is not
#     writable, so `atomic_copy` raises. The copy must be KEPT: it is the only
#     record of what the file was, and deleting it on a restore that did not
#     happen destroys the evidence with the file.
with open(_Z5_LAST, "w") as _fh:
    _fh.write(_PRISTINE_TEXT)
_Z5_FIRST = os.path.join(_Z5, srt.WRITER_OWNED_FILES[0][0])
with open(_Z5_FIRST, "w") as _fh:
    _fh.write(_PLANTED_TEXT)
_Z5_COPY = srt.backup_path(_Z5_FIRST, 909090)
with open(_Z5_COPY, "w") as _fh:
    _fh.write(_PRISTINE_TEXT)
_Z5_DIR = os.path.dirname(_Z5_FIRST)
_Z5_MODE = os.stat(_Z5_DIR).st_mode
os.chmod(_Z5_DIR, 0o500)
try:
    _z5_out = srt.repair_pristine_backups(_Z5, out=lambda _l: None)
finally:
    os.chmod(_Z5_DIR, _Z5_MODE)
check("10z5 the directory mode was restored, so nothing after this line runs "
      "against a read-only tree",
      stat.S_IMODE(os.stat(_Z5_DIR).st_mode), stat.S_IMODE(_Z5_MODE))
check("10z5 a restore that cannot be WRITTEN is reported as restore-failed, "
      "the copy is KEPT and the target is left exactly as it was -- the copy "
      "is the only record of what the file used to be",
      ([r["action"] for r in _z5_out], os.path.isfile(_Z5_COPY),
       sha_of(_Z5_FIRST)),
      (["restore-failed"], True, _PLANTED_SHA))
check("10z5 ...and the failure is NAMED rather than swallowed",
      any("PermissionError" in r.get("error", "") for r in _z5_out), True)

# (c) THE SAME BRANCH WITH A WRITABLE DIRECTORY, and it is the case that
#     DISCRIMINATES. In (b) the copy survives partly for the wrong reason: the
#     directory is read-only, so a buggy `os.unlink(backup)` on the failure path
#     would fail too and be swallowed. Measured -- a revert that deletes the
#     copy on restore-failed passed (b) and every other check in this file. Here
#     the directory is writable and only the TARGET is unwritable (it is a
#     non-empty DIRECTORY, so `os.replace` onto it raises for every user there
#     is, which `chmod 000` does not: root bypasses that).
_Z6 = os.path.join(_TMP, "guard-writable-failure")
_Z6_DIR = os.path.join(_Z6, os.path.dirname(_TARGET_REL))
os.makedirs(_Z6_DIR, exist_ok=True)
_Z6_TARGET = os.path.join(_Z6, _TARGET_REL)
os.makedirs(_Z6_TARGET, exist_ok=True)
with open(os.path.join(_Z6_TARGET, "occupied"), "w") as _fh:
    _fh.write("x")
_Z6_COPY = srt.backup_path(_Z6_TARGET, 808080)
with open(_Z6_COPY, "w") as _fh:
    _fh.write(_PRISTINE_TEXT)
_z6_out = srt.repair_pristine_backups(_Z6, out=lambda _l: None)
check("10z5 (c) a restore that fails with the DIRECTORY writable still keeps "
      "the copy -- the only case in which deleting it would have succeeded, "
      "and therefore the only one that says the code does not try",
      ([r["action"] for r in _z6_out], os.path.isfile(_Z6_COPY)),
      (["restore-failed"], True))
check("10z5 (c) ...and the copy's own directory really is writable, so a "
      "delete would have worked (non-degeneracy: without this, (c) passes for "
      "(b)'s reason)",
      os.access(os.path.dirname(_Z6_COPY), os.W_OK), True)


#------------------------------------------------------------------------------


# ===========================================================================
# 11.  ISOLATION
# ===========================================================================

section("11. Isolation")

check("11a tests/run_serial_tests.py is byte-unchanged",
      hashlib.sha256(open(_RUNNER_PATH, "rb").read()).hexdigest(), _RUNNER_SHA)

# EVERY LOCK FILE THIS TEST CREATED IS REMOVED. The lock is the flock on the
# INODE and the file is deliberately never removed by the runner itself -- but
# these are keyed on temp directories that are about to stop existing, so
# leaving them behind would litter this user's lock directory with one file per
# run of this test, forever.
_ours = srt.lock_path(_CHECKOUT)
for _p in (_ours,):
    if os.path.lexists(_p):
        os.remove(_p)
check("11b the throwaway checkout's lock file is cleaned up",
      os.path.lexists(_ours), False)

shutil.rmtree(_TMP, ignore_errors=True)
check("11c the temp tree is gone", os.path.exists(_TMP), False)


#------------------------------------------------------------------------------


print(f"\n{'=' * 74}")
print(f"  {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print(f"{'=' * 74}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
