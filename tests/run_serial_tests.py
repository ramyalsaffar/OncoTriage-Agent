# Serial Test Runner
####################

"""Runs the source-mutating tests IN ORDER, one at a time.

WHY THIS FILE EXISTS
--------------------
Some tests rewrite files in the repository and restore them. While that window
is open, anything that READS what they wrote sees a doctored tree, and the
result is a real-looking failure with no defect behind it -- the worst kind to
debug. Until pass 20c-3b that fact lived only as a warning paragraph in
CLAUDE.md. A warning is not a mechanism: it is followed by whoever read it, and
by nobody else -- including a CI job, a `for f in tests/*.py` loop, or a person
running two terminals.

THE MATRIX IS DERIVED FROM THE CODE, NOT DECLARED (pass 20d-2)
--------------------------------------------------------------
Every candidate was walked for the calls that can write a repository file
(`open(..., "w"/"a")`, `os.replace/remove/rename`, `shutil.copy*/move/rmtree`,
`Path.write_*`) and for the calls that read one, with each path expression
resolved through the file's own module-level assignments. `str.replace` was
separated from `os.replace`, which a name-only match conflates and which made
the first version of that derivation report writes that do not exist.

WHAT IT FOUND. There are exactly TWO writers in the whole suite:

  test_registries_cancer_code_claims_audit_control.py
      -> oncotriage/registries/cancer_code_registry.py   (open "w", restored
         by shutil.copy2 from a backup taken at start)
      -> oncotriage/registries/__pycache__               (rmtree, deliberate;
         see its bytecode note)

  test_config_snapshot_date_rot.py
      -> oncotriage/config.py                            (open "w", restored
         by shutil.copy2 from a backup taken at start)

EVERY OTHER TEST IS READ-ONLY WITH RESPECT TO THE REPOSITORY, including the two
that were previously described as "outside the matrix because they edit no
file". That description was true and it was only half the rule: a file that
writes nothing cannot CORRUPT anyone, but it can still BE corrupted. Membership
follows from the intersection, in either direction:

  audit x audit_control      the audit extracts the inline comment beside every
                             code in cancer_code_registry.py as the claim under
                             audit. The control plants defects into that exact
                             text. Run together, the audit audits planted
                             defects and reports them as real ones. (This pair
                             is also why the control runs the audit itself as a
                             subprocess -- that is cooperation, not collision.)

  package_invariants x both  it copytree()s the whole package in five separate
                             checks -- which brings BOTH written files along --
                             and copytree()s the dashboard in a sixth. A copy
                             taken mid-edit carries the edit; check 4 then
                             rewrites the snapshot date in its own copy and
                             fails if the copy did not carry the assignment.

  degraded_dependencies      NEW IN PASS 20d-2, and the derivation is what found
    x audit_control          it. This file was excluded on the "edits no file"
                             rule. But it asserts
                             `sorted(_p) == ["C34.10", "C50.911", "C97"]` on the
                             ICD-10 seed and exercises SNOMED 254837009 -- and
                             the control plants defects into BOTH of those exact
                             regions (case 4 is `C97 -> C99`, case 12 is
                             `254837009 -> 396275006`). Run together it fails on
                             a registry somebody else doctored.

  storage_query_layer        STAYS OUT, and this was checked rather than carried
                             forward. It reads queries.py, agent/retrieval.py,
                             agent/terminal.py, the cost tab and File 16 -- none
                             of them written by either writer -- and the only
                             config values it imports are RRF_POOL_SIZE and
                             TOP_K_CANDIDATES, which the snapshot-date rewrite
                             does not touch (it matches
                             `DATA_SNAPSHOT_DATE = "..."` alone). It writes only
                             into a fresh temp directory and reads history
                             through `git show`, which touches no working-tree
                             file.

                             It does IMPORT the package, so it shares with every
                             other test the ordinary hazard of importing
                             config.py or cancer_code_registry.py inside a
                             restore window. That is a property of importing at
                             all rather than a collision this matrix is for; it
                             is why the writers are serialized rather than why
                             every reader would have to be.

WHY THIS ORDER. It is not arbitrary.
  1. the audit runs FIRST, against a pristine registry, so its 197 claims are
     the baseline the control then tries to break;
  2. the control plants and restores, fourteen times;
  3. degraded_dependencies runs immediately after that restore, where an
     incomplete restore shows up as its failure rather than as a mystery later;
  4. the snapshot-date test patches and restores config.py;
  5. package_invariants runs LAST, over a tree every earlier file has put back,
     so a failure there means it found something rather than that it caught a
     neighbour mid-edit.

WHAT THIS DOES NOT RUN. The other tests under tests/ -- the eleven component
tests from pass 20d-1, storage_query_layer, the four from pass 20f-1 and
test_dashboard_reproducibility_tab -- write nothing in the repository and are
safe in parallel and in any order. Adding them would make a fast, safe suite
wait behind a slow, serial one. Files 18 and 19 are also not here: they need a
live server and they cost money.

  dashboard_reproducibility_tab  STAYS OUT, derived the same way as
                             storage_query_layer above. It writes only inside a
                             temporary directory -- the seeded scratch database,
                             the pickled frame and every planted COPY of the
                             module -- and the only repository file it READS is
                             oncotriage/dashboard/tabs/reproducibility.py, which
                             neither writer writes. It installs a scratch path
                             into paths._RESOLVED and restores it. Like every
                             other reader it imports the package, which is the
                             ordinary hazard of importing config.py or
                             cancer_code_registry.py inside a restore window,
                             not a collision this matrix is for.

NEVER EDIT THE REPOSITORY WHILE THIS IS RUNNING. Two of the five below restore
from a copy taken at their own start, so an edit made to
cancer_code_registry.py or config.py mid-run is silently reverted when they
finish. That is not hypothetical: pass 20d-1 lost an edit to oncotriage/config.py
exactly this way and found it only by re-grepping afterwards.

AND TWO OF THESE RUNS AT ONCE IS THE SAME DEFECT WITH NO HUMAN IN IT (pass 20f-3)
---------------------------------------------------------------------------------
Until pass 20f-3 this file had 239 lines, no lock and no pid file. Its entire
reason for existing is that two members rewrite source IN PLACE and restore it
from a backup taken at their own start -- and NOTHING STOPPED TWO COPIES OF IT
FROM RUNNING AT ONCE. Interleave two runs of
test_registries_cancer_code_claims_audit_control.py and the second one's backup
is a copy of the first one's PLANTED tree; whichever finishes last restores
that, and cancer_code_registry.py is left holding a deliberate defect with both
runs reporting 16/16 passed and exit 0. The paragraph above is the mechanism;
the only difference is that here there is no operator to have ignored a warning.

Two terminals is the obvious way in, and it is not the likely one. A CI job that
runs `make serial-tests` on push, a `watch`, a re-run started because the first
looked stuck, a second checkout of the same working tree -- none of those
involves anybody deciding to overlap.

SO THE GUARD IS STRUCTURAL AND NOT A CHECK. `flock(LOCK_EX | LOCK_NB)` on a file
outside the repository, held for the whole run, released by the KERNEL when this
process exits -- including on SIGKILL, a panic, or a laptop lid. That property is
what rules out the alternative shape: a pid file written and deleted by this
program leaves a stale lock behind every time it dies badly, and the "is that pid
still alive" repair re-introduces a check-then-act race of its own.

    The lock file is in a PER-USER SUBDIRECTORY of the system temp directory,
    named after a hash of the REALPATH of the code directory, so two different
    checkouts do not block each other -- they rewrite different files -- and two
    runs against the same tree do, INCLUDING when they name it through different
    symlinks. It carries the holder's pid, host, user and start time (UTC, with
    the marker) so the refusal names something an operator can act on. It is
    never removed: an empty lock file is 0 bytes and removing it is what would
    open the race.

    `--list` runs nothing and takes no lock.

FOUR HARDENINGS PORTED FROM `oncotriage/batch/runner.py`'s RUN LOCK
--------------------------------------------------------------------
That lock and this one were the same shape, and only that one was hardened. The
mechanism is identical -- a name in a world-writable directory, derived from a
path anybody can guess -- so the defects were identical too, and this file's
blast radius is arguably the worse of the two: what it guards is two processes
rewriting `oncotriage/` in place.

    1. `realpath`, NOT `abspath`, AS THE KEY. `abspath` does not resolve
       symlinks, so one checkout reached through two names hashed to two
       digests, took two lock files, and BOTH RAN -- the exact overlap this
       lock exists to prevent, through the one route it could not see.
    2. A 0700 UID-KEYED LOCK DIRECTORY, plus `O_NOFOLLOW` and 0600 on the file.
       The lock file's name is a SHA-256 of a path, so before this another user
       could pre-create it as a symlink to any file this user can write and the
       first run to start would `O_CREAT` through it and `ftruncate` the target
       to zero.
    3. A UTC RECORD with an explicit `Z`. The holder's start time is read by
       somebody deciding whether that run is stuck, often from a CI log written
       in another region; a bare local time is wrong by the writer's offset with
       nothing in the string saying so.
    4. A TYPED REFUSAL, NOT AN `OSError`. "The lock could not be OPENED" is
       `LockUnavailable`, a `RuntimeError`, converted at the acquisition site --
       because `_run_all()` runs INSIDE the `with`, so an `except OSError`
       around it would swallow every `OSError` the five subprocess launches can
       raise and report it as a lock failure.

    THEY ARE COPIED, NOT IMPORTED, and that is this file's own recorded design:
    it imports NOTHING from the project so that it still reports a missing test
    file rather than dying on an ImportError when the package is what is broken.
    The cost is that the two can drift, and that is paid for by
    `tests/test_serial_runner_lock.py`, which asserts the four properties HERE
    rather than assuming they came across.

EVERY EXIT CODE IS REPORTED, and the run does not stop at the first failure --
each of the five leaves its own tree in the state it found it, so a failure in
one does not make the next meaningless. The process exits non-zero if any of
them did.

Run from terminal:
    python tests/run_serial_tests.py          # all five, in order
    python tests/run_serial_tests.py --list   # print the order and exit
    make serial-tests                         # the same thing through the Makefile

Exit codes:
    0 -- every test exited 0
    1 -- at least one test exited non-zero
    2 -- a test file is missing
    3 -- another run of this file already holds the lock  (pass 20f-3)
    4 -- the lock could not be OPENED at all: not "wait", but "fix the temp
         directory". Nothing has been run and nothing has been restored.
"""

import argparse
import contextlib
import errno
import getpass
import hashlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import time

# fcntl IS REQUIRED, AND ITS ABSENCE IS A REFUSAL RATHER THAN A DEGRADATION.
# It is POSIX-only, so this fails on Windows -- where every documented command in
# this project already fails, since the numbered filenames contain spaces and
# `make` is assumed. Running the suite UNLOCKED because the locking primitive
# was missing would be precisely the failure the lock exists to prevent, and it
# would be silent. Import it at module scope so the failure is at load, not
# three tests into a run.
import fcntl


# PASS 20d-2: this file lives in tests/ and the tests it runs are named relative
# to the REPOSITORY ROOT, which is its parent. It runs them with cwd set there
# too, because each resolves the package from its own location and prints paths
# relative to it.
#
# It does not derive the root from `oncotriage.__file__`: this is a process
# launcher, it imports nothing from the project, and keeping it that way means
# `python tests/run_serial_tests.py` still reports a missing test file rather
# than dying on an ImportError when the package is what is broken.
_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# The order is load-bearing -- see the module docstring.
SERIAL_TESTS = (
    ("tests/test_registries_cancer_code_claims_audit.py",
     "audits the registry's inline claims; must see a PRISTINE source"),
    ("tests/test_registries_cancer_code_claims_audit_control.py",
     "plants defects into the registry IN PLACE and hashes the restore"),
    ("tests/test_degraded_dependencies.py",
     "asserts on the ICD-10 seed and a SNOMED code the control above plants into"),
    ("tests/test_config_snapshot_date_rot.py",
     "rewrites DATA_SNAPSHOT_DATE in oncotriage/config.py IN PLACE"),
    ("tests/test_package_invariants.py",
     "copytree()s the package five times; must copy a RESTORED tree"),
)


# ===========================================================================
# THE RUN LOCK  (pass 20f-3)
# ===========================================================================

EXIT_LOCKED = 3

EXIT_LOCK_UNAVAILABLE = 4
"""The lock could not be OPENED -- a different instruction from EXIT_LOCKED.

`3` means "another serial run holds it": wait for it, or kill it, and the state
is benign and self-clearing. `4` means the lock file could not be opened at all,
so this run cannot establish that it is the only one -- and running the suite
without that guarantee is precisely how one run's restore writes back another
run's PLANTED tree. Waiting fixes nothing; the temp directory does. They are
separate codes because a caller that treats them alike will retry the one that
never succeeds.

NOTHING HAS BEEN RUN when either fires: the lock is taken before the first
subprocess.
"""


# THE FOUR HARDENINGS BELOW ARE PORTED FROM
# `oncotriage/batch/runner.py`'s RUN LOCK, WHICH THIS ONE WAS MODELLED ON AND
# THEN DID NOT FOLLOW. That module was hardened against symlink substitution,
# a split key, a local-time record and an untyped refusal; this file kept the
# original shape. The defects are the same defects because the mechanism is the
# same mechanism -- a name in a world-writable directory, derived from a path
# anybody can guess -- and this file's blast radius is arguably worse, because
# what it guards is two processes rewriting `oncotriage/` in place.
#
# THEY ARE COPIED RATHER THAN IMPORTED, AND THAT IS THIS FILE'S OWN RECORDED
# DESIGN RATHER THAN AN OVERSIGHT. See the module docstring: this is a process
# launcher that imports NOTHING from the project, so that
# `python tests/run_serial_tests.py` still reports a missing test file rather
# than dying on an ImportError when the package is what is broken. An
# `from oncotriage.batch.runner import exclusive_run_lock` here would make the
# suite that diagnoses a broken package unrunnable exactly when the package is
# broken -- and would import the batch runner, and with it the graph, into the
# launcher. The cost of the copy is that the two can drift; that is paid for by
# `tests/test_serial_runner_lock.py`, which asserts the four properties HERE
# rather than assuming they came across.

LOCK_DIRECTORY_MODE = 0o700
"""Owner-only, on the directory the lock files live in. See `lock_directory`."""

LOCK_FILE_MODE = 0o600
"""Owner-only, on the lock file itself, AT CREATION.

A mode argument to `os.open` applies only when the file is created, so this does
not repair a lock file that already exists with wider permissions. It does not
need to: the file lives inside a 0700 directory, which is what actually excludes
another user, and the record inside it is a pid, a host and a username -- not a
secret.
"""


def lock_directory():
    """Where this user's lock files live. PURE -- it creates nothing.

    `ensure_lock_directory()` is the one that creates. The split is the
    `output_dir()` / `ensure_output_dir()` lesson: a caller who only wants to
    PRINT the path -- a diagnostic, a test -- must not bring a directory into
    existence by asking.

    A PER-USER SUBDIRECTORY RATHER THAN THE BARE TEMP DIRECTORY, and the reason
    is a real substitution rather than tidiness. `tempfile.gettempdir()` is
    world-writable and sticky, and this lock file's name is a SHA-256 of the
    CODE DIRECTORY -- derivable by anybody who can guess where the checkout is.
    Before this, another user on the same host could pre-create
    `{tmp}/oncotriage-serial-tests-<digest>.lock` as a SYMLINK to any file this
    user can write, and the first serial run to start would `O_CREAT` through it
    and then `ftruncate` the target to zero. The sticky bit does not help: it
    stops one user deleting another's file, not creating a new one at a name
    nobody has claimed. A 0700 directory means the name cannot be claimed by
    anyone else at all, and `O_NOFOLLOW` in `exclusive_run_lock` closes the
    residual case of a link inside this user's own directory.

    NAMED BY THE UID, NOT BY THE LOGIN NAME. `getpass.getuser()` consults
    LOGNAME, USER, LNAME and USERNAME BEFORE the password database -- all four
    settable by the process asking -- so a login-name directory would split one
    real user's lock namespace in two the moment those differed between two
    invocations (a CI job with a bare environment beside an interactive shell is
    the ordinary way that happens). Two namespaces means two locks for one
    checkout, which is exactly the silent overlap this lock exists to prevent.
    The uid is also the identity `ensure_lock_directory` compares ownership
    against, so the name and the check are one fact. The login name is still
    RECORDED in the lock file, which is where an operator reads it.

    THE DIRECTORY IS SHARED WITH THE OTHER TWO RUN LOCKS AND THAT IS
    DELIBERATE. `oncotriage/batch/runner.py` and `oncotriage/ablation/study.py`
    each derive the same `{tmp}/oncotriage-{uid}` path, and all three keep their
    own copy of this function -- the shared-module consolidation is a recorded
    deferral, not an oversight. One directory per user is the right shape (its
    ownership and mode are one fact to verify, not three), and what separates
    the three locks is the FILE PREFIX: `oncotriage-serial-tests-`,
    `oncotriage-batch-run-` and `oncotriage-ablation-run-`. A serial run and a
    batch run must not refuse each other -- they guard different things -- so
    the prefixes are load-bearing and must stay distinct.
    """
    return os.path.join(tempfile.gettempdir(), f"oncotriage-{os.getuid()}")


def ensure_lock_directory():
    """Create the lock directory if absent, verify it, and return it.

    RAISES `OSError`. The only caller is `exclusive_run_lock`, which converts it
    to `LockUnavailable` so `main()` can print a diagnosis instead of a
    traceback.

    `exist_ok=True` DOES NOT CHMOD AN EXISTING DIRECTORY, so creating it 0700 is
    only half the guarantee -- a directory already sitting there with wider
    permissions, or owned by somebody else, would be used exactly as if this
    function had made it. The three checks are the other half, and each names a
    distinct failure:

    * `lstat`, never `stat`: the thing at this path being a SYMLINK is one of
      the states this function exists to refuse, and `stat` would follow it and
      report on the target.
    * owned by this uid: another user's directory, however permissive, is not
      ours to write locks into.
    * not group- or other-writable: a 0777 directory pre-created by anybody
      re-opens the substitution the per-user directory closes.

    It REFUSES rather than repairing. `chmod`-ing somebody else's directory is
    not this program's business.
    """
    root = lock_directory()
    os.makedirs(root, mode=LOCK_DIRECTORY_MODE, exist_ok=True)
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(errno.ENOTDIR,
                      "the serial-test lock directory is not a directory", root)
    if info.st_uid != os.getuid():
        raise OSError(errno.EPERM,
                      f"the serial-test lock directory is owned by uid "
                      f"{info.st_uid}, not by this process (uid {os.getuid()})",
                      root)
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OSError(errno.EPERM,
                      f"the serial-test lock directory is writable by group or "
                      f"other (mode {stat.S_IMODE(info.st_mode):04o})", root)
    return root


def lock_path(code_dir=_CODE_DIR):
    """Where the run lock for `code_dir` lives.

    Outside the repository -- a test suite that writes a file into the tree it
    is about to hash is the defect this file is for -- and keyed on the code
    directory rather than fixed, so two checkouts are independent and two runs
    against one checkout are not. The hash is truncated for a readable name; a
    collision between two directories on one machine costs a spurious refusal
    with the holder's own path printed, not a corrupted tree.

    THE KEY IS `realpath` AND NOT `abspath`, AND THE DIFFERENCE IS TWO LOCKS FOR
    ONE CHECKOUT. `abspath` normalizes `.`, `..` and the working directory and
    STOPS THERE -- it does not resolve symlinks -- so two invocations naming one
    checkout through different links hash to two different digests, take two
    different lock files, and BOTH RUN. That is the exact overlap this lock
    exists to prevent, reached by the one route the lock could not see. Not
    hypothetical here: a CI job that checks out to a symlinked workspace beside
    a developer running `make serial-tests` in the real path is two names for
    one tree, and on macOS `/var` is itself a link to `/private/var`.

    It keeps both properties the old form had: it is deterministic (`realpath`
    is a pure function of the filesystem at the moment of the call), and a
    trailing separator makes no difference, because `realpath` normalizes it
    away exactly as `abspath` did.
    """
    digest = hashlib.sha256(
        os.path.realpath(code_dir).encode("utf-8")).hexdigest()
    return os.path.join(lock_directory(),
                        f"oncotriage-serial-tests-{digest[:16]}.lock")


class AlreadyRunning(RuntimeError):
    """Raised when another process holds the run lock. Carries its record.

    A `RuntimeError` subclass and deliberately NOT an `OSError`: a stray
    `except OSError` around a path check must not be able to eat a refusal.
    """

    def __init__(self, path, holder):
        self.path = path
        self.holder = holder
        super().__init__(f"{path} is held by {holder}")


class LockUnavailable(RuntimeError):
    """The lock could not be ATTEMPTED. Carries the path and the errno.

    A DIFFERENT FINDING FROM `AlreadyRunning` AND NOT A SUBCLASS OF IT. That one
    means another serial run holds the lock, which is benign and self-clearing;
    this means the lock file could not be opened at all -- a read-only temp
    directory, a full filesystem, a SYMLINK where the lock file should be, a
    directory owned by somebody else -- and no amount of waiting fixes it.

    A `RuntimeError` AND NOT AN `OSError`, WHICH IS THE WHOLE POINT OF THE
    CLASS. The obvious form of this is `except OSError` around the `with` block
    in `main()` -- and `_run_all()` runs INSIDE that block, so the clause would
    swallow every `OSError` the five subprocess launches can raise (an
    unreadable test file, a full disk, a broken pipe) and report it as "the lock
    could not be taken", with the run's real diagnosis discarded. So the
    conversion happens at the ACQUISITION site, where the only `OSError`
    reachable is the lock's own.

    IT IS NOT `EXIT_LOCKED`. "Another run holds it" and "the lock could not be
    opened" are different instructions to a human -- wait, versus fix your temp
    directory -- so they exit differently. See `EXIT_LOCK_UNAVAILABLE`.
    """

    def __init__(self, path, cause):
        self.path = path
        self.cause = cause
        self.errno = getattr(cause, "errno", None)
        self.strerror = getattr(cause, "strerror", None) or str(cause)
        self.filename = getattr(cause, "filename", None)
        super().__init__(f"{path}: {type(cause).__name__}: {cause}")


@contextlib.contextmanager
def exclusive_run_lock(path=None):
    """Hold an exclusive, non-blocking flock for the duration of the block.

    Yields the lock file's path. Raises AlreadyRunning immediately -- never
    waits -- if another process holds it, because a second run that queued
    behind the first would still run, just later, and an operator who started
    it by accident would rather be told.

    THE LOCK IS RELEASED BY THE KERNEL when this process exits, however it
    exits. Nothing in here deletes the file, and that is deliberate: the lock is
    the flock on the inode, not the file's existence, so removing it on the way
    out would let a second process create a NEW inode and lock that instead
    while a third still held the old one.
    """
    derived = path is None
    if derived:
        path = lock_path()
    try:
        # ONLY WHEN WE DERIVED THE PATH. A caller who named the lock file
        # directly owns its directory -- creating one under a path this function
        # was handed would be a side effect nobody asked for, and the in-process
        # tests name files inside directories they made themselves.
        if derived:
            ensure_lock_directory()
        # O_NOFOLLOW IS THE HALF OF THE SYMLINK FIX THAT DOES NOT DEPEND ON THE
        # DIRECTORY. The 0700 directory is what stops another user claiming the
        # name; this is what stops the open following a link that is there
        # anyway -- a stale one from before this change, one left by a restore,
        # or one this user made themselves. Without it, O_CREAT on an existing
        # symlink opens the TARGET and the `ftruncate` below zeroes it. It costs
        # nothing on the ordinary path: a regular file is not a symlink.
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                     LOCK_FILE_MODE)
    except OSError as exc:
        raise LockUnavailable(path, exc) from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.lseek(fd, 0, os.SEEK_SET)
            raw = os.read(fd, 4096).decode("utf-8", "replace").strip()
            try:
                holder = json.loads(raw) if raw else {}
            except ValueError:
                holder = {"record": raw}
            raise AlreadyRunning(path, holder) from None
        # Only now, holding the lock, is it safe to overwrite the record.
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, json.dumps({
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "user": getpass.getuser(),
            # UTC WITH AN EXPLICIT MARKER. This string is read by an operator
            # deciding whether the holder is stuck, and quite possibly from a
            # different machine or a CI log in another region. A bare local time
            # is wrong by the writer's UTC offset with nothing in the string
            # saying so -- the difference between "started four minutes ago" and
            # "started nine hours ago". The `Z` is only honest because of
            # `gmtime`; `strftime` with a `Z` over `localtime` is the exact
            # defect the structured logger had to fix once already.
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            # `realpath`, MATCHING THE KEY. The lock is keyed on the resolved
            # path, so a record naming the unresolved one would show an operator
            # a different string from the one the refused run derived its digest
            # from -- two names for the one thing the refusal is about.
            "code_dir": os.path.realpath(_CODE_DIR),
        }).encode("utf-8"))
        os.fsync(fd)
        yield path
    finally:
        os.close(fd)          # releases the flock


def already_running_lines(exc):
    """The refusal, as the lines `main()` prints. One text, one caller.

    A FUNCTION RATHER THAN A BLOCK IN `main()` so it can be driven by a test
    without starting two processes -- which is what makes it possible to assert
    that the holder's identity actually reaches the operator, rather than
    asserting that a code path exists.
    """
    lines = ["[Serial] REFUSING TO RUN: another serial run holds the lock.",
             f"         lock file: {exc.path}"]
    for key in ("pid", "host", "user", "started", "code_dir", "record"):
        if key in exc.holder:
            lines.append(f"         {key:9s} {exc.holder[key]}")
    lines.extend([
        "",
        "         Two of these tests rewrite files in oncotriage/ and restore "
        "them",
        "         from a backup taken at their own start. Overlap them and the "
        "later",
        "         restore writes back the earlier run's PLANTED tree, with both "
        "runs",
        "         reporting success. Wait for the other run, or kill it.",
    ])
    return lines


def lock_unavailable_lines(exc):
    """The diagnosis, as the lines `main()` prints. One text, one caller.

    IT NAMES THE ERRNO SYMBOLICALLY AS WELL AS NUMERICALLY. `13` is a number an
    operator has to look up; `EACCES` is the thing they already know, and the
    two together survive being pasted into a search or an issue.
    """
    code = getattr(exc, "errno", None)
    named = errno.errorcode.get(code, "?") if code is not None else "?"
    lines = [
        "[Serial] REFUSING TO RUN: the run lock could not be taken.",
        f"         lock file: {exc.path}",
        f"         error:     errno {code} ({named}): {exc.strerror}",
    ]
    if getattr(exc, "filename", None) and exc.filename != exc.path:
        lines.append(f"         at:        {exc.filename}")
    lines.extend([
        "",
        "         This is NOT 'another run holds the lock' -- that is a "
        "different",
        "         refusal with a different exit code. The lock file could not "
        "be opened",
        "         at all, so this run cannot establish that it is the only "
        "one, and",
        "         two of these tests rewrite oncotriage/ in place and restore "
        "from a",
        "         backup taken at their own start. Overlapping them leaves a "
        "planted",
        "         tree behind with both runs reporting success.",
        "",
        "         Usual causes, in the order they are worth checking:",
        f"             - {lock_directory()} is not writable, or is owned by "
        f"another user",
        "             - the temp filesystem is full or mounted read-only",
        "             - something has left a SYMLINK where the lock file goes "
        "(ELOOP);",
        "               the lock is opened O_NOFOLLOW and will not write "
        "through one",
        "",
        "         NOTHING HAS BEEN RUN AND NOTHING HAS BEEN RESTORED.",
    ])
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the source-mutating tests serially, in order.")
    parser.add_argument("--list", action="store_true",
                        help="print the order and why, then exit")
    args = parser.parse_args(argv)

    if args.list:
        print("Serial order (two of these mutate the source tree and restore it;\n       the rest read what those two write, so none may overlap):")
        for i, (name, why) in enumerate(SERIAL_TESTS, start=1):
            print(f"  {i}. {name}\n       {why}")
        return 0

    missing = [n for n, _ in SERIAL_TESTS
               if not os.path.isfile(os.path.join(_CODE_DIR, n))]
    if missing:
        print("[Serial] MISSING test file(s):")
        for name in missing:
            print(f"  - {name}")
        return 2

    # THE LOCK IS TAKEN BEFORE THE FIRST TEST AND HELD PAST THE LAST. It wraps
    # the whole run rather than each subprocess, because the hazard is not two
    # tests overlapping -- it is one run's RESTORE landing inside another run's
    # backup window, which spans the gaps between tests too.
    try:
        with exclusive_run_lock():
            return _run_all()
    except AlreadyRunning as exc:
        for line in already_running_lines(exc):
            print(line)
        return EXIT_LOCKED
    except LockUnavailable as exc:
        for line in lock_unavailable_lines(exc):
            print(line)
        return EXIT_LOCK_UNAVAILABLE


def _run_all():
    """The run itself. Called with the lock held; see main()."""
    print("=" * 78)
    print(f"SERIAL TEST RUN — {len(SERIAL_TESTS)} tests, one at a time")
    print("=" * 78)
    print(f"Code directory: {_CODE_DIR}")
    print()

    outcomes = []
    run_start = time.time()

    for i, (name, why) in enumerate(SERIAL_TESTS, start=1):
        print()
        print("-" * 78)
        print(f"[{i}/{len(SERIAL_TESTS)}] {name}")
        print(f"        {why}")
        print("-" * 78)
        start = time.time()
        # cwd is the code directory because every one of these resolves its own
        # _code_dir from __file__ and reads the package relative to it; running
        # from elsewhere works, but keeping cwd here matches how they are
        # documented to be run and keeps any relative artifact in one place.
        completed = subprocess.run([sys.executable, name], cwd=_CODE_DIR)
        elapsed = time.time() - start
        outcomes.append((name, completed.returncode, elapsed))
        verdict = "PASS" if completed.returncode == 0 else f"FAIL (exit {completed.returncode})"
        print(f"[{i}/{len(SERIAL_TESTS)}] {name}: {verdict} in {elapsed:.1f}s")

    print()
    print("=" * 78)
    print("SERIAL TEST SUMMARY")
    print("=" * 78)
    for name, rc, elapsed in outcomes:
        print(f"  {'PASS' if rc == 0 else 'FAIL':<5} exit={rc:<3} {elapsed:7.1f}s  {name}")
    failed = [n for n, rc, _ in outcomes if rc != 0]
    print()
    print(f"Total wall time: {time.time() - run_start:.1f}s")
    if failed:
        print(f"FAILED: {len(failed)} of {len(outcomes)}")
        for name in failed:
            print(f"  - {name}")
        return 1
    print(f"All {len(outcomes)} serial tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Aug  5 2026

@author: ramyalsaffar
"""
