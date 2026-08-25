# Operator Controls
###################

"""The run lock and the operator stop switch. ONE OWNER FOR BOTH.

WHY THIS MODULE EXISTS
----------------------
``oncotriage/batch/runner.py``, ``oncotriage/ablation/study.py`` and
``tests/run_serial_tests.py`` each grew a run lock, and the three were the same
mechanism written out three times: an ``flock(LOCK_EX | LOCK_NB)`` on a file in
the system temp directory, named after a hash of a path, held for the process's
life and released by the KERNEL however it exits. Two of the three also grew a
stop switch, and those were the same again -- a latching, thread-safe poll over
a sentinel file, with a bounded note reader and a three-member clear
vocabulary.

Three copies of a mechanism is three places to fix a defect. That is not a
prediction: the hardening pass applied FOUR security fixes -- ``realpath``
rather than ``abspath`` as the key, a 0700 uid-keyed directory with
``O_NOFOLLOW`` and 0600 on the file, a UTC record with an explicit marker, and a
typed refusal rather than a bare ``OSError`` -- and it had to apply each of them
three times, one file at a time, with nothing that would have failed if it had
stopped after two. The pass before it had applied the same fixes to ONE of the
three and left the other two, which is exactly how the divergence arose.

WHAT IS SHARED AND WHAT IS NOT
------------------------------
Shared, and therefore here: the lock directory and its verification, the lock
file's open/flock/record/release sequence, the two refusal classes' payloads,
the mechanical half of both refusal texts (the record loop, the symbolic errno),
the stop sentinel's bounded note reader, the clear vocabulary, the queued-work
sweep, and the latching poll.

NOT shared, and therefore parameters or subclasses, each with its argument at
the site that keeps it:

* the lock file's PREFIX -- ``oncotriage-batch-run-``,
  ``oncotriage-ablation-run-`` and ``oncotriage-serial-tests-``. Load-bearing:
  a batch run and an ablation study guard different things and must not refuse
  each other, so the prefixes must stay distinct.
* the KEY the lock is derived from -- a checkpoint directory, a study's
  checkpoint file, a checkout. Each program owns its own.
* the exception CLASSES. Distinct per program; see ``AlreadyRunning`` below.
* ``EXIT_LOCK_UNAVAILABLE`` -- deliberately NOT here. Its value is derived from
  the entry point's OWN exit vocabulary (1 beside the batch runner's other
  refusals, 1 beside the study's, 4 beside the serial runner's 0/1/2/3), so it
  is a fact about a program rather than about this mechanism. ``EXIT_LOCKED``
  IS here, because all three agree on 3 and agree on why.
* the refusal PROSE. Each program's consequence is different -- a cohort billed
  twice, a configuration's sample split across two ``ablation_runs`` rows, a
  restore writing back a planted tree -- and the remediation command differs
  with it.

WHY ``tests/run_serial_tests.py`` STILL KEEPS ITS COPY
-----------------------------------------------------
That file's no-project-imports rule was read before this module was written, and
it has two clauses. The first -- an import of the batch runner would drag the
graph into a process launcher -- is dissolved by this module, which imports
nothing from the project at all. The SECOND is not: the rule exists so that
``python tests/run_serial_tests.py`` still reports a missing test file rather
than dying on an ImportError WHEN THE PACKAGE IS WHAT IS BROKEN, and
``import oncotriage.control`` executes ``oncotriage/__init__.py`` and needs the
package on ``sys.path``. The launcher deliberately has no bootstrap block. The
by-location escape is closed too: ``tests/test_package_invariants.py`` section
1c forbids loading a module by location UNCONDITIONALLY, with no allowlist, and
has already caught one test file doing exactly that.

So the serial runner keeps its copy, and the copy stops being unpinned:
``tests/test_serial_runner_lock.py`` section 9 compares it against THIS
module's source by AST, with the divergences declared and everything else
required to be identical.

THE ONE THING THIS MODULE MAY NOT DO
------------------------------------
Import from ``oncotriage``. Not ``config``, not ``paths``, not
``observability``. Two reasons: the layering above (a module that could be
copied into a launcher must be copyable), and the fact that both consumers
import it at MODULE SCOPE, so any project import here lands in the import graph
of every batch run and every study. ``console`` output is therefore injected --
every function that prints takes an ``out`` callable -- which is also what makes
those texts drivable by a test without capturing stdout.

``fcntl`` IS REQUIRED AND ITS ABSENCE IS A REFUSAL RATHER THAN A DEGRADATION.
It is POSIX-only, so this module fails to import on Windows -- where every
documented command in this project already fails, since the numbered filenames
contain spaces and ``make`` is assumed. Running a 22,000-patient billed campaign
UNLOCKED because the locking primitive was missing would be precisely the
failure the lock exists to prevent, and it would be silent. At module scope the
failure is at import, not four hours in -- and because both consumers import
this module at module scope, they inherit that guarantee rather than restating
it.
"""

import contextlib
import errno
import fcntl
import getpass
import hashlib
import json
import os
import socket
import stat
import tempfile
import threading
import time
from pathlib import Path


# ===========================================================================
# EXIT CODES
# ===========================================================================

EXIT_LOCKED = 3
"""The exit code when another run of the same program holds the lock.

3 in all three programs, and they agree on why: it does not collide with
anything any of them already returns (0/1/2 are verdicts and refusals, 130 is
Ctrl-C, 143 is SIGTERM), so a supervisor can tell "another copy is already
running" -- benign, self-clearing, worth retrying -- from every other outcome
without parsing output.

ITS SIBLING ``EXIT_LOCK_UNAVAILABLE`` IS DELIBERATELY NOT HERE. That one means
"the lock file could not be opened at all", which waiting does not fix, and its
VALUE is chosen from the entry point's own vocabulary rather than from this
mechanism: 1 in the batch runner and the ablation study, where every other
refusal returns 1, and 4 in the serial runner, whose 1 is already "a test
failed". One number cannot carry that, and pretending it can is how a shared
constant becomes wrong for one of its consumers.
"""


# ===========================================================================
# THE RUN LOCK
# ===========================================================================
#
# THE MECHANISM: flock(LOCK_EX | LOCK_NB) on a file in the system temp
# directory, held for the process's life, released BY THE KERNEL however the
# process exits -- including on SIGKILL, a panic, or a laptop lid.
#
# WHY NOT A PID FILE. A pid file written and deleted by the program leaves a
# stale lock behind every time it dies badly, and the "is that pid still alive"
# repair re-introduces a check-then-act race of its own. The kernel's release is
# unconditional and needs no repair.
#
# WHY THE LOCK FILE IS NEVER UNLINKED. The lock is the flock on the INODE, not
# the file's existence. Removing it on the way out would let a second process
# create a NEW inode and lock that instead while a third still held the old one
# -- two runs, both holding "the" lock. An empty lock file is 0 bytes.

LOCK_DIRECTORY_MODE = 0o700
"""Owner-only, on the directory the lock files live in. See ``lock_directory``."""

LOCK_FILE_MODE = 0o600
"""Owner-only, on the lock file itself, at CREATION.

A mode argument to ``os.open`` applies only when the file is created, so this
does not repair a lock file that already exists with wider permissions. It does
not need to: the file lives inside a 0700 directory, which is what actually
excludes another user, and the record inside it is a pid, a host and a
username -- not a secret. What the narrow mode buys is that a lock file
inherited from a previous release, or copied about by a backup tool, does not
become the one world-writable thing in the tree.
"""


def lock_directory() -> str:
    """Where this user's lock files live. PURE -- it creates nothing.

    ``ensure_lock_directory()`` is the one that creates, and the split is the
    ``output_dir()`` / ``ensure_output_dir()`` lesson this project already
    recorded once: a caller who only wants to PRINT the path -- ``--help``, a
    diagnostic, a test -- must not bring a directory into existence by asking.

    A PER-USER SUBDIRECTORY RATHER THAN THE BARE TEMP DIRECTORY, and the reason
    is a real attack rather than tidiness. ``tempfile.gettempdir()`` is
    world-writable and sticky, and a lock file's name is a SHA-256 of a path --
    derivable by anybody who can guess the deployment's checkpoint directory or
    the checkout's location. Before this, a different user on the same host
    could pre-create ``{tmp}/oncotriage-<prefix>-<digest>.lock`` as a SYMLINK to
    any file this user can write, and the first run to start would ``O_CREAT``
    through it and then ``ftruncate`` the target to zero. The sticky bit does
    not help: it stops one user deleting another's file, not creating a new one
    at a name nobody has claimed yet. A 0700 directory means the name cannot be
    claimed by anyone else in the first place, and ``O_NOFOLLOW`` in
    ``hold_exclusive_lock`` closes the residual case where this user's own
    directory somehow holds a symlink.

    THE DIRECTORY IS NAMED BY THE UID AND NOT BY THE LOGIN NAME, which is a
    deliberate departure from the obvious form. ``getpass.getuser()`` consults
    ``LOGNAME``, ``USER``, ``LNAME`` and ``USERNAME`` BEFORE the password
    database -- all four settable by the very process asking -- so a login-name
    directory would split one real user's lock namespace in two the moment
    those variables differed between two invocations (a cron entry with a bare
    environment beside an interactive shell is the ordinary way that happens).
    Two namespaces means two locks for one key, which is exactly the silent
    double bill this lock exists to prevent. The uid is the identity the
    ownership check below compares against, so the name and the check are one
    fact. The login name is still recorded IN the lock file, which is where an
    operator reads it.

    ONE DIRECTORY FOR EVERY PROGRAM, AND WHAT SEPARATES THEM IS THE FILE
    PREFIX. One directory per user is the right shape -- its ownership and its
    mode are one fact to verify rather than three -- and a batch run, an
    ablation study and a serial test run must not refuse each other, because
    they guard different things. ``lock_file_path``'s prefix argument is what
    keeps them apart and it is load-bearing.
    """
    return os.path.join(tempfile.gettempdir(), f"oncotriage-{os.getuid()}")


def ensure_lock_directory() -> str:
    """Create the lock directory if absent, verify it, and return it.

    RAISES ``OSError``. Every caller is inside ``hold_exclusive_lock``, which
    converts it to the program's ``LockUnavailable`` so an entry point can print
    a diagnosis instead of a traceback.

    ``exist_ok=True`` DOES NOT CHMOD AN EXISTING DIRECTORY, so creating it 0700
    is only half the guarantee -- a directory already sitting there with wider
    permissions, or owned by somebody else, would be used exactly as if this
    function had made it. The three checks below are the other half, and each
    names a distinct failure:

    * ``lstat``, never ``stat``: the thing at this path being a SYMLINK is one
      of the states this whole function exists to refuse, and ``stat`` would
      follow it and report on the target.
    * owned by this uid: another user's directory, however permissive, is not
      ours to write locks into.
    * not group- or other-writable: a 0777 directory pre-created by anybody
      re-opens the symlink substitution the per-user directory closes.

    It refuses rather than repairing. ``chmod``-ing somebody else's directory
    is not this program's business, and silently narrowing a directory that
    another tool deliberately shared would be a surprise landing in a batch
    run's first second.
    """
    root = lock_directory()
    os.makedirs(root, mode=LOCK_DIRECTORY_MODE, exist_ok=True)
    info = os.lstat(root)
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(errno.ENOTDIR,
                      "the run-lock directory is not a directory", root)
    if info.st_uid != os.getuid():
        raise OSError(errno.EPERM,
                      f"the run-lock directory is owned by uid {info.st_uid}, "
                      f"not by this process (uid {os.getuid()})", root)
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OSError(errno.EPERM,
                      f"the run-lock directory is writable by group or other "
                      f"(mode {stat.S_IMODE(info.st_mode):04o})", root)
    return root


def lock_file_path(prefix: str, key) -> str:
    """The lock file for ``key``, under ``prefix``, in this user's lock directory.

    OUTSIDE WHATEVER DIRECTORY ``key`` NAMES, and that is two decisions rather
    than one. A state directory's other files are a run's resumable state and an
    operator reads a listing of it to answer "what is here"; a lock file that is
    neither state nor a control would be noise in exactly the place noise is
    expensive. And a state directory may be a network share -- the stop switch's
    whole point is that a shared filesystem is how an operator reaches a run on
    another host -- where flock is advisory at best and lies at worst. The
    system temp directory is local by construction.

    THE KEY IS ``realpath`` AND NOT ``abspath``, AND THE DIFFERENCE IS A SECOND
    LOCK FOR ONE DIRECTORY. ``abspath`` normalizes ``.``, ``..`` and the working
    directory and STOPS THERE -- it does not resolve symlinks -- so two
    invocations naming one key through different links hashed to two different
    digests, took two different lock files, and BOTH RAN. Not hypothetical in
    any of the ways this project is deployed: Docker bind mounts,
    ``ONCOTRIAGE_MAIN_PATH`` pointed at a symlinked deployment, a CI job checked
    out to a symlinked workspace, and macOS, where ``tempfile.gettempdir()`` is
    ``/var/folders/...`` and ``/var`` is itself a link to ``/private/var``.
    ``realpath`` collapses every one of those to the same string, which is the
    only thing that makes "one key, one lock" true.

    It keeps the two properties the old form had and the tests pin: it is
    deterministic (``realpath`` is a pure function of the filesystem at the
    moment of the call), and a trailing separator makes no difference, because
    ``realpath`` normalizes it away exactly as ``abspath`` did. It resolves a
    path that does not exist yet without raising -- the unresolvable tail is
    returned as given -- which matters because a state directory may not have
    been created when the first run starts.

    The digest is truncated for a readable name. A collision between two keys on
    one machine costs a spurious refusal WITH THE HOLDER'S OWN PATH PRINTED,
    which is diagnosable in one line; the untruncated alternative buys nothing an
    operator can use.
    """
    digest = hashlib.sha256(
        os.path.realpath(str(key)).encode("utf-8")).hexdigest()
    return os.path.join(lock_directory(), f"{prefix}{digest[:16]}.lock")


class AlreadyRunning(RuntimeError):
    """Another process holds a run lock. Carries its record.

    A ``RuntimeError`` subclass and deliberately not an ``OSError``: a stray
    ``except OSError`` around a path check must not be able to eat a refusal.

    EACH PROGRAM SUBCLASSES THIS RATHER THAN RAISING IT, and the argument for
    that changed shape when this module was written -- so it is restated here
    rather than carried across unread.

    WHAT EXPIRED: the batch runner's and the study's classes each argued that a
    shared class would mean importing the OTHER program's module, putting the
    whole batch runner -- its checkpoint, its ledger, its graph -- into every
    study's import graph. That is no longer true of anything: this module
    imports nothing from the project, so the shared base costs no edge at all.

    WHAT SURVIVES, AND IS WHY THEY ARE STILL DISTINCT: the refusals are raised
    by different programs, name different consequences and are remediated with
    different commands, and a caller holding more than one lock -- an
    orchestrator, or a test that drives both, which this suite already has --
    must be able to tell from the TYPE which refusal it caught rather than by
    parsing a path out of the message. A shared base gives that caller the
    option of catching either, which is strictly more than the copies allowed;
    the subclasses are what keep the finer answer available.
    """

    def __init__(self, path, holder):
        self.path = path
        self.holder = holder
        super().__init__(f"{path} is held by {holder}")


class LockUnavailable(RuntimeError):
    """The lock could not be ATTEMPTED. Carries the path and the errno.

    A DIFFERENT FINDING FROM ``AlreadyRunning`` AND NOT A SUBCLASS OF IT. That
    one means another copy of the program holds the lock, which is benign and
    self-clearing; this means the lock file could not be opened at all -- a
    read-only temp directory, a full filesystem, a symlink where the lock file
    should be, a directory owned by somebody else -- and no amount of waiting
    fixes it. They exit differently for that reason.

    A ``RuntimeError`` AND NOT AN ``OSError``, WHICH IS THE WHOLE POINT OF THE
    CLASS. The obvious form of this fix is ``except OSError`` in the entry
    point's guard -- and the program's ``main()`` runs INSIDE that guard's
    ``with`` block, so the clause would swallow every ``OSError`` the pipeline
    can raise in hours of running (an unwritable checkpoint, a full disk
    mid-campaign, a socket error) and report it as "the lock could not be
    taken", with the run's real diagnosis discarded. So the conversion happens
    at the ACQUISITION site, where the only ``OSError`` reachable is the lock's
    own.

    Subclassed per program, on ``AlreadyRunning``'s footing above.
    """

    def __init__(self, path, cause):
        self.path = path
        self.cause = cause
        self.errno = getattr(cause, "errno", None)
        self.strerror = getattr(cause, "strerror", None) or str(cause)
        self.filename = getattr(cause, "filename", None)
        super().__init__(f"{path}: {type(cause).__name__}: {cause}")


@contextlib.contextmanager
def hold_exclusive_lock(path, *, already_running, lock_unavailable,
                        record_key=None, record_value=None,
                        ensure_directory=True):
    """Hold an exclusive, non-blocking flock on ``path`` for the block's duration.

    Yields ``path``. Raises ``already_running`` IMMEDIATELY -- never waits --
    because a second run that queued behind the first would still run, just
    later, against a cohort the first has by then finished, and an operator who
    started it by accident would rather be told than have it happen four hours
    from now.

    Args:
        path: the lock file. Derived by the caller through ``lock_file_path``,
            or named directly by a test.
        already_running: the program's ``AlreadyRunning`` subclass. Required and
            not defaulted: a default would let a caller that forgot it raise a
            refusal its own entry point does not catch.
        lock_unavailable: the program's ``LockUnavailable`` subclass. Same.
        record_key: the extra field the holder record carries beside pid, host,
            user and started -- ``checkpoint_dir``, ``checkpoint``, ``code_dir``.
            ``None`` writes no extra field.
        record_value: its value. THE CALLER RESOLVES IT, once, before this is
            called. The batch runner's first version read its checkpoint
            directory a SECOND time when writing the record, so a caller that
            passed an explicit ``path`` got a holder record naming a directory it
            had nothing to do with -- worse than no record, because an operator
            acts on it.
        ensure_directory: create and verify the lock directory first. False when
            the caller named the lock file directly, because a caller who did
            that owns its directory and creating one under a path this function
            was handed would be a side effect nobody asked for.

    THE RECORD IS WRITTEN ONLY AFTER THE LOCK IS HELD, so a refused run cannot
    overwrite the holder's own identity with its own on the way to being told
    no.
    """
    try:
        if ensure_directory:
            ensure_lock_directory()
        # O_NOFOLLOW IS THE HALF OF THE SYMLINK FIX THAT DOES NOT DEPEND ON THE
        # DIRECTORY. The 0700 directory is what stops another user claiming the
        # name; this is what stops the open following a link that is there
        # anyway -- a stale one from before this change, one left by a restore,
        # or one this user made themselves. Without it, O_CREAT on an existing
        # symlink opens the TARGET and the ftruncate below zeroes it. It costs
        # nothing on the ordinary path: a regular file is not a symlink.
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW,
                     LOCK_FILE_MODE)
    except OSError as exc:
        raise lock_unavailable(path, exc) from exc
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
            raise already_running(path, holder) from None
        # Only now, holding the lock, is it safe to overwrite the record.
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        record = {
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "user": getpass.getuser(),
            # UTC WITH AN EXPLICIT MARKER, on oncotriage/observability.py's
            # precedent and for its reason: this string is read by an operator
            # deciding whether the holder is stuck, and quite possibly on a
            # different machine from the one that wrote it. A bare local time
            # is wrong by the writer's UTC offset with nothing in the string
            # saying so -- which on a container built in one region and run in
            # another is the difference between "started four minutes ago" and
            # "started nine hours ago". `Z` is only honest because of gmtime;
            # `strftime` with a `Z` over `localtime` is the exact defect the
            # structured logger had to fix once already.
            "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if record_key is not None:
            record[record_key] = record_value
        os.write(fd, json.dumps(record).encode("utf-8"))
        os.fsync(fd)
        yield path
    finally:
        os.close(fd)          # releases the flock


def already_running_lines(exc, *, header, record_keys, key_width,
                          body) -> list:
    """A lock refusal, as the lines an entry point prints. One shape, one caller
    each.

    A FUNCTION RATHER THAN A BLOCK IN THE GUARD so it can be driven by a test
    without starting two processes, and so an entry point's ``__main__`` block
    stays what this project's rule says it is.

    Args:
        header: the program's own first line.
        record_keys: which fields of the holder record to print, in order. It
            differs between programs only because the record's extra field does
            -- ``checkpoint_dir`` against ``checkpoint``.
        key_width: the column the values line up at. A per-program number
            because the longest key differs.
        body: the program's own paragraphs -- what overlapping runs actually
            cost, and how to stop the holder. NOT shared: a cohort billed twice,
            a configuration's sample split across two rows and a restore writing
            back a planted tree are three different consequences with three
            different remediations, and one text covering all of them would be
            vague about each.
    """
    lines = [header, f"        lock file: {exc.path}"]
    for key in record_keys:
        if key in exc.holder:
            lines.append(f"        {key:{key_width}s} {exc.holder[key]}")
    lines.extend(body)
    return lines


def lock_unavailable_lines(exc, *, header, consequence) -> list:
    """An unopenable-lock diagnosis, as the lines an entry point prints.

    A FUNCTION RATHER THAN A BLOCK IN THE GUARD, on ``already_running_lines``'
    footing: it can be driven by a test without arranging an unopenable path in
    a subprocess.

    IT NAMES THE ERRNO SYMBOLICALLY AS WELL AS NUMERICALLY. ``13`` is a number
    an operator has to look up; ``EACCES`` is the thing they already know, and
    the two together survive being pasted into a search or an issue.

    Args:
        header: the program's own first line.
        consequence: the program's own middle paragraph -- what running without
            the guarantee would cost. Everything around it (the errno rendering,
            the ``at:`` line for a differing filename, the causes list and the
            nothing-was-billed line) is the same question in every program and
            is here.
    """
    code = getattr(exc, "errno", None)
    named = errno.errorcode.get(code, "?") if code is not None else "?"
    lines = [
        header,
        f"        lock file: {exc.path}",
        f"        error:     errno {code} ({named}): {exc.strerror}",
    ]
    if getattr(exc, "filename", None) and exc.filename != exc.path:
        lines.append(f"        at:        {exc.filename}")
    lines.append("")
    lines.extend(consequence)
    lines.extend([
        "",
        "        Usual causes, in the order they are worth checking:",
        f"            - {lock_directory()} is not writable, or is owned by "
        f"another user",
        "            - the temp filesystem is full or mounted read-only",
        "            - something has left a SYMLINK where the lock file goes "
        "(ELOOP);",
        "              the lock is opened O_NOFOLLOW and will not write "
        "through one",
        "",
        "        NOTHING HAS BEEN RUN AND NOTHING HAS BEEN BILLED.",
    ])
    return lines


# ===========================================================================
# THE OPERATOR STOP SWITCH
# ===========================================================================
#
# WHAT IT IS FOR, AND WHY IT IS NOT A SIGNAL. A batch run is hours long and
# costs one live Stage 5 call per patient; an ablation study is seven
# configurations of the same. "Stop this cleanly, I will resume" is an ordinary
# operational request, and the two ways to make it before this existed were both
# wrong:
#
#   * Ctrl-C -- which needs a terminal the process is attached to, so it is
#     unavailable to anything running under nohup, screen, systemd, a
#     container or a cron entry; and
#   * SIGTERM -- which IS available to all of those and is deliberately an
#     ABRUPT stop: it is what an orchestrator sends when it is about to SIGKILL,
#     so it records the run KILLED, abandons in-flight billed requests mid-read,
#     and returns 143.
#
# Neither expresses "finish what you started, write it all down, and stop before
# the next unit". That is what this is: a file, so any user who can write to the
# state directory can ask for it, from any machine that shares the volume, with
# no pid to find and no signal to route.
#
# WHY A FILE AND NOT A DATABASE ROW OR A SOCKET. A row would put a poll on the
# hot write path and require the switch to be reachable through whatever
# ONCOTRIAGE_INFERENCES_DB currently resolves to -- so an operator would have to
# know which database this run is writing to in order to stop it. A socket is a
# port to allocate, a firewall to argue with and a second failure mode. A file
# in a directory the program already owns and already writes to needs nothing
# that is not already true.

STOP_MESSAGE_MAX_CHARS = 1000
"""How much of the sentinel's text is kept.

A CAP AND NOT A TRUNCATION BUG: the file is operator-written, so it can be
anything -- an accidental `cat` of a log into it, a stray binary. The note is a
courtesy for the run record and is not worth an unbounded read into a structured
log field. What is kept is the first N characters and the fact that it was cut
is stated in the same line.

IT IS NOT IN ``oncotriage/config.py``, AND THAT IS AN EXCEPTION TO THIS
PROJECT'S "ALL TUNABLES LIVE IN CONFIG" RULE, ARGUED HERE RATHER THAN TAKEN.
That rule exists so an operator who wants to change the pipeline's behaviour has
one file to look in, and it comes with a matching promise -- every constant in
that file has a reader and therefore an effect. This is not a knob of that kind:
it bounds an allocation on a SHUTDOWN PATH, an operator changing it changes
nothing about what the run does or costs, and moving it would make
``oncotriage/config.py`` an import of this module's -- which is the one thing
this module may not have, because it must import nothing from the project. The
same argument covers ``STOP_MESSAGE_TAIL_PROBE_CHARS`` and ``EXIT_LOCKED``: a
bound, a probe width and an exit code are properties of a mechanism, not
settings of a pipeline.
"""

STOP_MESSAGE_TAIL_PROBE_CHARS = 4096
"""How far past the cap the reader looks to answer "was anything LOST".

IT EXISTS BECAUSE THE OBVIOUS FIX TO THE TRUNCATION GUARD TRADES A FALSE
POSITIVE FOR A FALSE NEGATIVE. The read is bounded at CAP + 1 characters, so
``len(raw) > CAP`` was the only evidence available -- and it called a note of
exactly the cap followed by a NEWLINE truncated, which is what every editor and
every ``echo`` writes. Testing the STRIPPED length instead fixes that case and
opens the opposite one: a file whose character at the cap boundary happens to be
whitespace, with real content after it, strips to CAP characters and would be
reported WHOLE while everything past the boundary was dropped -- silently, in
the closing block, which is the only place the note is ever read.

SO THE READER LOOKS PAST THE BOUNDARY, AND ONLY WHEN IT HAS TO. The probe runs
exclusively when the first read came back capped, it continues the SAME handle
rather than re-opening, and it is itself bounded: the total this shutdown path
can ever allocate is CAP + 1 + this + 1 characters, about 5 KB, against the
megabytes an unbounded ``read_text()`` pulls in when somebody redirects a log
into the sentinel by accident.

THE RESIDUAL IS CONSERVATIVE AND IS STATED: a file with MORE than this many
whitespace characters after the note, and content after that, is reported
truncated when arguably nothing was lost. That is the safe direction -- it
over-reports a cut rather than hiding one -- and it is the direction the old
guard erred in for EVERY note rather than for a file nobody writes.

Not in ``oncotriage/config.py``; see ``STOP_MESSAGE_MAX_CHARS``.
"""


STOP_CLEAR_REMOVED = "removed"
STOP_CLEAR_ABSENT = "absent"
STOP_CLEAR_FAILED = "failed"

STOP_CLEAR_OUTCOMES = (STOP_CLEAR_REMOVED, STOP_CLEAR_ABSENT, STOP_CLEAR_FAILED)
"""What ``clear_stop_switch`` can answer. Closed, and a caller may branch on it
exhaustively.

IT USED TO BE A ``bool`` AND THAT CONFLATED TWO OPPOSITE FINDINGS. ``False``
meant "there was no sentinel to clear", and the entry point printed exactly
that -- so the moment the ``unlink`` could fail rather than return, ``False``
would have meant "there was none" AND "there is one and I could not remove it"
with one line of output covering both. The second is the dangerous one: the
preflight is deliberately SKIPPED when ``--clear-stop`` is given (it is the
gesture the refusal names), so a failed clear that reported "nothing to clear"
would start the run with the sentinel still in place, trip it at the first
completed unit, and stop again -- after billing that unit -- for a request the
operator had just withdrawn.

Three members rather than two because the remediations differ: nothing to do,
nothing to do, and `chmod`/`sudo rm`. That is ``FP_OUTCOMES``' and
``WARMUP_SOURCES``' argument, one mechanism over.
"""


def read_stop_message(path, faults) -> str:
    """The operator's note, capped, or None. NEVER RAISES.

    An unreadable sentinel is still a sentinel: the switch has already tripped
    by the time this is called, and refusing to stop because a note could not be
    decoded would be the worst available outcome. The failure is counted under
    ``message:`` -- a phase key distinct from ``poll:`` precisely so an operator
    can tell "the run may have missed a stop" from "the run stopped and lost the
    note".

    Args:
        path: the sentinel.
        faults: the PROGRAM'S OWN faults ``Counter``. Passed rather than owned
            here, and that is the same argument the two counters already carry
            in their own modules: they describe different files, so one number
            covering both would report a batch fault and a study fault as one
            finding -- and it would put a counter in a module that
            ``oncotriage/degradation.py`` cannot see without an import edge this
            module may not have.
    """
    try:
        # A BOUNDED READ, NOT A READ-THEN-TRUNCATE. `path.read_text()` would
        # pull the WHOLE file into memory before the cap below could apply --
        # so an operator who redirected a log into this file by accident, or
        # pointed the state directory at something unexpected, would have the
        # shutdown path allocate the lot. One extra character is read so "was it
        # longer than the cap" is answerable without a second stat.
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            raw = handle.read(STOP_MESSAGE_MAX_CHARS + 1)
            # THE TAIL PROBE, ON THE SAME HANDLE AND ONLY WHEN THE FIRST READ
            # CAME BACK CAPPED. It is what makes "nothing was lost" a
            # measurement rather than an assumption; see
            # STOP_MESSAGE_TAIL_PROBE_CHARS for why the stripped-length test
            # alone would trade one false report for its opposite.
            tail = (handle.read(STOP_MESSAGE_TAIL_PROBE_CHARS + 1)
                    if len(raw) > STOP_MESSAGE_MAX_CHARS else "")
    except Exception as exc:                                    # noqa: BLE001
        faults[f"message:{type(exc).__name__}"] += 1
        return None
    text = raw.strip()
    # WAS ANYTHING BEYOND WHAT WE ARE RETURNING? Two ways yes: the probe saw a
    # non-whitespace character past the boundary, or the probe ITSELF came back
    # capped, which means there is more we could not look at. The second is
    # deliberately read as "truncated": an unknown remainder must not be
    # reported as an intact note.
    more_follows = bool(tail.strip()) or len(tail) > STOP_MESSAGE_TAIL_PROBE_CHARS
    if not text and not more_follows:
        # AN EMPTY FILE IS FULLY VALID AND IS THE EXPECTED CASE. `touch` is the
        # documented gesture; None here means "no note", not "no stop". A file
        # of nothing but whitespace lands here too, correctly.
        #
        # `and not more_follows` IS NOT DEFENSIVENESS: a file whose first
        # CAP + 1 characters are all whitespace and which then carries a real
        # note would otherwise be reported as having none, which is the same
        # silent loss the probe exists to prevent, at the other end of the
        # file. It falls through to the truncation branch instead, which
        # returns the marker alone -- honest about there being a note and about
        # not being able to show it.
        return None
    # THE TEST IS ON THE STRIPPED TEXT, NOT ON THE RAW READ, and the two
    # disagree on the ordinary case. The read above takes CAP + 1 characters so
    # "was there more" is answerable without a second stat -- but a note written
    # by an editor, by `echo`, or by any of the shell forms an operator actually
    # uses ends in a newline, so a note of exactly CAP characters arrived as
    # CAP + 1 RAW and was reported truncated while nothing had been lost: the
    # operator's note was returned one character short with "... [truncated at
    # 1000 characters]" welded onto it, in the run's closing block, saying a
    # message was cut that was not. Trailing whitespace is not content; the
    # length that decides is the length of what is actually being returned.
    #
    # AND IT CANNOT UNDER-REPORT EITHER, WHICH THE STRIPPED TEST ALONE COULD
    # NOT PROMISE. `len(text) > CAP` covers the case where every read character
    # survived the strip; `more_follows` covers the case it opens -- whitespace
    # sitting exactly at the boundary with content after it, which strips to CAP
    # and would otherwise be handed back as a whole note.
    if len(text) > STOP_MESSAGE_MAX_CHARS or more_follows:
        return (text[:STOP_MESSAGE_MAX_CHARS]
                + f"... [truncated at {STOP_MESSAGE_MAX_CHARS} characters]")
    return text


def clear_stop_switch(resolve, describe, faults, *, unit, out,
                      remediation) -> str:
    """Delete the sentinel. Returns a ``STOP_CLEAR_*`` member. Used by
    ``--clear-stop``.

    A SEPARATE GESTURE FROM WHATEVER DISCARDS THE CHECKPOINT, and never folded
    into it, because the two clear opposite things: that one discards a run's
    RESULTS and re-bills the cohort, and this discards a CONTROL FILE and costs
    nothing. An operator who wants to resume after a stop wants exactly this and
    must not be within one flag of the other.

    IT NEVER RAISES, AND BEFORE THIS IT RAISED UNCAUGHT. ``path.unlink()`` on a
    state directory the run can read and cannot write -- a directory owned by
    another user, a read-only mount, a volume remounted `ro` -- raises
    ``PermissionError``, and nothing between there and the interpreter caught
    it. The operator's diagnosis was a traceback ending in ``Errno 13`` over a
    path they then had to read out of the stack, printed INSTEAD of the run they
    asked for. Every other entry point into this switch already refuses to do
    that: the poll, the note reader and the stale-sentinel preflight each catch,
    count and carry on.

    ``Exception`` AND NOT ``OSError``, and the difference is a real case rather
    than defensiveness. ``PermissionError`` IS an ``OSError`` subclass, so the
    obvious pair is one clause -- but ``resolve`` reads a lazily-globbed project
    path, which raises a plain ``RuntimeError`` when it matches nothing or
    matches several. An ``OSError``-only clause would leave ``--clear-stop`` as
    the one gesture that tracebacks on an unresolvable path, which is the shape
    ``describe`` exists to prevent one function up.

    THE COUNTER PHASE IS ``clear:`` AND IT IS ITS OWN, not folded into
    ``poll:``. ``poll:`` means "the run may have kept going through a stop
    request"; this means "an operator asked to resume and the sentinel is still
    there". Opposite directions, different fixes.

    Args:
        resolve: returns the sentinel's ``Path``. May raise; that is the point.
        describe: returns the sentinel's path as a string, or a description when
            resolving it would raise. The failure here may BE the resolution, in
            which case ``path`` was never bound, so the message re-describes
            rather than referencing.
        faults: the program's own faults ``Counter``.
        unit: what the run would bill before stopping again -- "patient",
            "pair". It is the word that makes the warning concrete.
        out: where the lines go. Injected because this module may not import
            ``oncotriage.observability``, and because it is what makes the text
            drivable by a test.
        remediation: the program's own last line -- what a permission error
            usually means for ITS state directory.
    """
    try:
        path = resolve()
        if not path.exists():
            return STOP_CLEAR_ABSENT
        path.unlink()
    except Exception as exc:                                    # noqa: BLE001
        faults[f"clear:{type(exc).__name__}"] += 1
        # THE PATH IS RE-DESCRIBED RATHER THAN REFERENCED, because the failure
        # may be the resolution itself, in which case `path` was never bound.
        out(f"[STOP] COULD NOT CLEAR the stop sentinel: "
            f"{type(exc).__name__}: {exc}")
        out(f"[STOP]   sentinel: {describe()}")
        out(f"[STOP]   The run would trip on it at its first completed "
            f"{unit} and stop again -- after billing that {unit} -- "
            f"for a request you have just withdrawn.")
        out("[STOP]   Remove it by hand and start again:")
        out(f"[STOP]       rm {describe()}")
        out(remediation)
        return STOP_CLEAR_FAILED
    out(f"[STOP] Cleared {path}")
    return STOP_CLEAR_REMOVED


def cancel_queued(futures) -> int:
    """Cancel every future that has not started. Returns how many were cancelled.

    ``Future.cancel()`` RETURNS FALSE FOR A RUNNING FUTURE and leaves it alone,
    which is exactly the contract this needs: in-flight units are already paid
    for and their rows are worth having, so they finish. A cancelled future
    never calls the pipeline, so it costs nothing -- which is what makes "no
    further unit is started" a statement about MONEY and not only about
    scheduling.

    A SNAPSHOT OF THE LIST IS ITERATED, because this is called from a
    done-callback on a worker thread while the submit loop on the main thread
    may still be appending. A list being appended to is safe to iterate in
    CPython, but "safe" there means "will not raise", not "will see every
    element" -- and the submit loop stops on its own the moment the switch
    trips, so anything it has not appended is never submitted at all.

    A CANCELLED FUTURE STILL FIRES ITS DONE-CALLBACK, which is what advances the
    progress bar and what routes it into the caller's cancellation branch --
    counted as cancelled rather than as an error.

    IT CANNOT REQUEST A STAGE 5 SHUTDOWN AND THAT IS NOW STRUCTURAL RATHER THAN
    PINNED. The sentinel promises in-flight units run to completion, and
    truncating them would break that AND cost more -- their paid round is
    discarded and the resume re-bills the whole unit. This module imports
    nothing from the project, so there is no ``request_stage5_shutdown`` in
    scope to call by a later edit that looks tidy.
    """
    return sum(1 for future in list(futures) if future.cancel())


class StopSwitch:
    """Has an operator asked this run to stop? Latching, thread-safe, one object.

    LATCHING IS THE WHOLE SEMANTICS. Once the sentinel has been seen, this
    object answers True for the rest of the process whatever happens to the file
    afterwards. Two reasons, and the second is the operational one:

      1. the answer is acted on by CANCELLING QUEUED WORK, which is not
         reversible -- so a switch that could un-trip would leave a run that had
         thrown away half its cohort and then decided to carry on; and
      2. deleting the sentinel is exactly what an operator does to make the NEXT
         run start (the stale-switch refusal is why), and they should be able to
         do it while this run is still finishing its in-flight units rather than
         having to wait for the process to exit.

    IT IS POLLED, NOT WATCHED. One existence check per completed unit, against a
    unit that takes tens of seconds -- and it is skipped entirely once tripped,
    so the steady-state cost is one stat call per unit and the tripped-state
    cost is nothing. A filesystem watcher would need a thread, a
    platform-specific backend and a story for network filesystems, to detect the
    same event a few tens of seconds sooner than the thing that decides when the
    next unit starts anyway: the completion of the current one.

    THERE IS ONE INSTANCE PER PROGRAM AND ``main()`` RESETS IT. Module-level
    mutable state that survives into the next run describes the wrong run --
    ``clear_write_ledger`` and ``run_fingerprint.clear_cache`` are the
    precedents -- for the identical reason: a second ``main()`` in one process
    (a test, an embedder looping) must not inherit the first run's stop.

    THE TWO SUBCLASSES DIFFER IN ONE THING: HOW THE SENTINEL'S PATH IS FOUND.
    The batch runner resolves it per poll from a module-level owner, because it
    is a fixed name in a fixed directory. The ablation study BINDS it at
    ``arm()``, because its location depends on ``--db`` and the poll runs on
    MAX_WORKERS done-callbacks -- ``main()`` has already resolved it for the
    banner, so binding it there means the path an operator was TOLD to write and
    the path the study watches are one reading rather than two. Everything else
    -- the lock, the latch, the fault phases, the announcement's shape -- is
    here.

    Args:
        faults: the program's own faults ``Counter``, keyed
            ``{phase}:{ExceptionType}``.
        unit: what the run will not start another of -- "patient", "(config,
            patient) pair". It appears in the announcement and nowhere else.
        subject: how the announcement names the thing being stopped -- "the
            run", "this study". Parameters rather than one wording because the
            two blocks shipped with different wording and this pass is a MOVE:
            re-wording either would be a change to operator-facing output that
            nothing asked for, and it would make the byte-comparison this pass
            is accepted on meaningless.
        noticed_prefix: what sits between "Noticed" and ``where``. The batch
            runner passes "during the " and names a PASS ("the run", "the
            resample pass"); the study passes "" and names a MOMENT ("between
            configurations"). Same reason as ``subject``.
        banner_width: the width of the rule the announcement is boxed in. A
            per-program number only because the two blocks were written to
            different widths and matching them would change output nothing
            reads for a reason nobody asked for.

    WHERE THE OUTPUT GOES IS A METHOD, NOT A PARAMETER. ``_emit`` and ``_warn``
    are overridden per program and delegate to that module's own ``console.out``
    and logger AT CALL TIME. This module may not import
    ``oncotriage.observability`` -- it imports nothing from the project -- and a
    captured callable would also silently defeat any caller that rebinds
    ``console.out``. See ``_emit``.
    """

    def __init__(self, faults, *, unit, subject, noticed_prefix, banner_width,
                 default_where="run"):
        self._lock = threading.Lock()
        self._faults = faults
        self._unit = unit
        self._subject = subject
        self._noticed_prefix = noticed_prefix
        self._banner_width = banner_width
        self._default_where = default_where
        self.requested = False
        self.message = None
        self.detected_in = None
        self.path = None
        self._armed_path = None

    # -- the one thing subclasses supply ------------------------------------

    def _resolve_path(self):
        """The sentinel to test, or None for "this switch is not armed".

        RAISING IS ALLOWED AND IS HANDLED BY ``poll``: the batch runner's
        override reads a lazily-globbed project path, which raises on a machine
        that does not have the sibling data tree.
        """
        return self._armed_path

    def _emit(self, line=""):
        """Write one console line. Overridden per program.

        A METHOD AND NOT A CALLABLE CAPTURED IN ``__init__``, AND THE DIFFERENCE
        IS LATE BINDING -- which is a real property this pass nearly lost. Both
        subclasses were written as ``console.out(...)`` inside the poll, so the
        module attribute was looked up AT CALL TIME; a constructor parameter
        binds it once, at import, and a caller that later rebinds
        ``console.out`` -- a test capturing the announcement, a future harness
        -- would find the switch still writing to the object it captured.
        Measured, not reasoned about: the first version of this class took
        ``out=console.out`` and a probe that patched ``console.out`` on the
        module captured NOTHING while the announcement went to the real stream.

        The default raises rather than printing: a subclass that forgets this is
        a switch that trips silently, and silence is exactly what this
        announcement exists to prevent.
        """
        raise NotImplementedError

    def _warn(self, message, **fields):
        """Emit the structured record. Overridden per program.

        A method for ``_emit``'s reason: the logger is a module-level object in
        the subclass's own module and is looked up at call time.
        """
        raise NotImplementedError

    # -- shared ------------------------------------------------------------

    def reset(self) -> None:
        """Forget any stop seen by an earlier run in this process."""
        with self._lock:
            self.requested = False
            self.message = None
            self.detected_in = None
            self.path = None
            self._armed_path = None

    def arm(self, path) -> None:
        """Bind the sentinel this run watches. Called once, from ``main()``."""
        with self._lock:
            self._armed_path = None if path is None else Path(path)

    def poll(self, where=None) -> bool:
        """Is a stop requested? Reads the disk at most once per process.

        Args:
            where: which pass noticed, recorded for the console line and the
                structured record. Free text, and it never reaches a durable
                store -- the callers pass literals.

        Returns:
            True once the sentinel has been seen, forever after.

        A POLL THAT RAISES DOES NOT TRIP THE SWITCH, and that direction is
        chosen rather than defaulted. ``Path.exists`` already answers False for
        every ordinary "not there" case, so a raise here is something else
        entirely -- an unreadable directory, a filesystem gone -- and treating
        that as a stop request would silently cancel a paid campaign because a
        mount hiccuped. It is counted and the run continues; if the condition
        persists the counter says so on the run's own report.

        AN UNRESOLVABLE OR UNARMED PATH NEVER TRIPS, and that is not a silent
        skip: ``main()`` resolves or arms it before the first billed call and
        the entry point's preflight has already asked the same question, so
        None here means a caller that is not ``main()`` -- a test driving one
        function -- for which "no operator has asked this to stop" is the true
        answer.
        """
        where = self._default_where if where is None else where
        with self._lock:
            if self.requested:
                return True
            try:
                path = self._resolve_path()
                present = False if path is None else path.exists()
            except Exception as exc:                            # noqa: BLE001
                self._faults[f"poll:{type(exc).__name__}"] += 1
                return False
            if not present:
                return False
            self.requested = True
            self.detected_in = where
            self.path = str(path)
            self.message = read_stop_message(path, self._faults)

        # OUTSIDE THE LOCK, because the console writer and the logger both take
        # locks of their own and this is called from MAX_WORKERS done-callbacks
        # at once. Holding a lock across a write to a bar-aware writer is how a
        # shutdown path deadlocks.
        rule = "=" * self._banner_width
        self._emit()
        self._emit(rule)
        self._emit(f"[STOP] Stop requested by {self.path}")
        if self.message:
            self._emit(f"[STOP] Note from the operator: {self.message}")
        self._emit(f"[STOP] Noticed {self._noticed_prefix}{where}. No further "
                   f"{self._unit} will be STARTED; those already running will "
                   f"finish and be written, the checkpoint is current, and "
                   f"{self._subject} will be recorded STOPPED.")
        self._emit(rule)
        self._warn("an operator stop was requested",
                   event="stop_switch_tripped", status="stopped",
                   mode=where, reason=self.message or "<no note>")
        return True


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 24 2026

@author: ramyalsaffar
"""
