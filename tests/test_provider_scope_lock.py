# Provider Scope Lock Test: one process per quota scope, on this host
#####################################################################

"""``provider_resilience.exclusive_scope_lock``: the per-quota-scope run lock.

WHAT THIS FILE HOLDS
--------------------
    1. THE KEY IS THE BUCKET -- THE PROVIDER ALLOWANCE -- AND IS
       CWD-INDEPENDENT, with TWO controls. ``control.lock_file_path``, whose
       key is a ``realpath``, gives two DIFFERENT locks for one key across a
       chdir. And ``_legacy_scope_lock_path`` -- the pre-re-key derivation,
       lifted rather than retyped -- gives two DIFFERENT locks for ``openai``
       and ``ragas_judge``, which draw on ONE allowance: the defect the bucket
       key removes.
    1b. TWO CALLERS OF ONE ALLOWANCE, and the two properties need two
       instruments. IN-PROCESS the lock is RE-ENTRANT (a process that already
       holds an allowance IS the single process for it, and flock would
       otherwise refuse this process against its own hold). CROSS-PROCESS a
       second program taking a DIFFERENT scope of the SAME allowance is
       REFUSED -- which is the whole point of the re-key and cannot be shown
       in one process. A scope of a different allowance is the firing control.
       A scope with no declared allowance refuses BY NAME rather than as a
       bare ``ValueError`` that no entry-point clause would catch.
    2. The refusal: a second acquisition on this host is refused BY NAME and
       carries the holder's record, including the SCOPE, which the digest-named
       lock file cannot otherwise be mapped back to. Self-clearing, with the
       release proved rather than assumed.
    3. CRASH RECOVERY, with a REAL subprocess and a REAL SIGKILL. A lock
       released by the kernel cannot be observed from inside the process that
       held it, and that is the whole reason this mechanism is a flock rather
       than a pid file -- so it is the one thing here that cannot be measured
       in-process.
    4. The two refusal texts: each names the scope, the consequence and a
       remedy, and the unopenable-lock diagnosis is a DIFFERENT finding from a
       held lock rather than a subclass of it.
    5. The prefixes of all four locks in this project are distinct, so a batch
       run, an ablation study, a serial test run and a quota scope do not
       refuse each other -- which is the one thing keeping them apart, since
       they share one directory.

NO NETWORK, NO KEYS, NO SPEND, no live Qdrant, no model load
(``ONCOTRIAGE_DEFER_LOCAL_MODELS``), no corpus, no database, no git history.
It DOES use real subprocesses and one real SIGKILL, for the reasons in (1b) and
(3), and it DOES create lock files.

ISOLATED BY ``ONCOTRIAGE_LOCK_DIR`` FOR THE WHOLE FILE, WHICH IS A STRAIGHT
IMPROVEMENT ON WHAT IT REPLACED. This file used to invent a scope name carrying
its own pid so it could not collide with a real campaign -- which worked, and
cost it the ability to exercise a REAL scope, and stopped working outright once
the lock's key became the allowance (an invented name has none). It now drives
the PRODUCTION scopes through the PRODUCTION bucket table inside a private lock
directory, so it exercises strictly more while still being unable to touch the
lock a real campaign holds. The directory is removed at the end and the
variable is restored to what it was found at. It writes nothing else anywhere
and it EXECS NOTHING. Not in the collision matrix.

WHY IT STILL DOES NOT DRIVE AN ENTRY POINT, NOW THAT FOUR OF THEM TAKE THIS
LOCK. This paragraph used to say the lock was unwired and why; it is wired --
``25- Batch Runner.py``, ``26- Ablation Study.py``, ``rater_run.py`` and
``ragas_run.py`` all take it -- and the division of labour is deliberate
rather than left over.

    * the BEHAVIOUR at the printed surface belongs to the file that already
      owns an entry-point harness. ``tests/test_runner_preflight_and_state_
      faults.py`` spawns real ``main()`` subprocesses with a corpus, a
      checkpoint directory, a database and a ``usercustomize`` stand-in; its
      section 5 now drives the refusal end to end (a second run against a
      DIFFERENT checkpoint directory, refused with the pacing text, naming the
      scope and the holder's pid) and its section 4 drives the crash recovery.
      Rebuilding that harness here would be a second copy of ~200 lines of
      subprocess machinery for a property it already measures.
    * the WIRING ITSELF belongs here, and section 8 below holds it: an AST pin
      that each entry point takes the allowance lock NESTED INSIDE its run
      lock, that both named clauses are present, and that the harness helper
      the suite depends on is wired at all four call sites. Each has a firing
      control.

So this file covers the MECHANISM and the STRUCTURE; the other covers the
OBSERVABLE BEHAVIOUR. Neither duplicates the other.
"""

import ast
import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

from oncotriage import control                                   # noqa: E402
from oncotriage import provider_resilience as pr                  # noqa: E402

# THE OTHER TWO PREFIXES ARE READ FROM SOURCE, NOT IMPORTED, and that is a
# deliberate narrowing rather than an inconvenience. `oncotriage.batch.runner`
# and `oncotriage.ablation.study` are campaign DRIVERS -- between them they
# pull in the graph, the checkpoint, the tracking module, the cohort selector
# and the spend ledger -- and importing two of those to read two STRING
# constants would make a lock test's import graph larger than the lock. Reading
# the assignment by AST asks the same question (`what prefix does that program
# use`) of the same authority (its own source) at no cost, and it is the
# instrument this project already uses to pin a constant it must not import.


def _prefix_in(rel_path):
    """The `LOCK_FILE_PREFIX = "..."` assigned at module scope in `rel_path`."""
    tree = ast.parse(open(os.path.join(_CODE_DIR, rel_path),
                          encoding="utf-8").read())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Name)
                    and target.id == "LOCK_FILE_PREFIX"
                    and isinstance(node.value, ast.Constant)):
                return node.value.value
    return None


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


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


class Raised:
    __slots__ = ("kind", "message", "exc")

    def __init__(self, exc):
        self.kind = type(exc).__name__
        self.message = str(exc)
        self.exc = exc


def drive(fn, *a, **kw):
    """Call fn; return its value or a Raised. Never raises."""
    try:
        return fn(*a, **kw)
    except Exception as exc:                                     # noqa: BLE001
        return Raised(exc)


def acquire(scope, **kw):
    """Enter the allowance lock and KEEP the manager. Never raises.

    Returns ``(manager_or_None, value_or_Raised)``; the manager is ``None``
    when the acquisition was REFUSED, which is what makes ``release`` safe to
    call on every outcome.

    WHY THE BARE ``__enter__()`` THAT STOOD AT TWELVE SITES WAS WRONG, AND WHY
    IT NONETHELESS PASSED. ``exclusive_scope_lock`` is a ``@contextmanager``,
    so ``pr.exclusive_scope_lock(s).__enter__()`` runs the generator to its
    yield and hands back the path -- and then, with no reference kept anywhere,
    CPython's refcounting finalizes the generator IMMEDIATELY: ``GeneratorExit``
    is thrown at the yield, the ``finally`` runs, the re-entrancy entry is
    discarded and the flock is released. Every acquisition in this file was
    therefore released by the GARBAGE COLLECTOR rather than by the test.

    THAT IS NOT A STYLE POINT, AND IT COST THE FILE ONE OF ITS CHECKS. The
    registry-emptiness check in section 2b read ``bool(pr._HELD_ALLOWANCES)``
    after a block in which two locks had been taken by that idiom -- and it was
    True-by-collection rather than True-by-lifecycle, so it would have passed
    with ``exclusive_scope_lock``'s own ``finally`` deleted. It is also a
    guarantee of CPython's refcounting rather than of Python: under a deferred
    collector those locks stay held and this file refuses itself.
    """
    cm = pr.exclusive_scope_lock(scope, **kw)
    try:
        return cm, cm.__enter__()
    except Exception as exc:                                     # noqa: BLE001
        return None, Raised(exc)


def release(cm):
    """Exit a manager ``acquire`` returned. Returns a word a check fails on."""
    if cm is None:
        return "nothing-to-release"
    try:
        cm.__exit__(None, None, None)
        return "released"
    except Exception as exc:                                     # noqa: BLE001
        return f"<RAISED {type(exc).__name__}: {exc}>"


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── A PRIVATE LOCK DIRECTORY, SET BEFORE THE FIRST LOCK CALL ────────────────
#
# THE ORDERING IS LOAD-BEARING AND SILENT WHEN WRONG. `control.lock_directory`
# reads this variable on every call, but a lock taken before it is set lands in
# the SHARED per-user directory -- where it is keyed on a REAL allowance and
# could refuse, or be refused by, a real campaign on this machine. So it is set
# here, above every section, and the file asserts at the end that every lock it
# created was inside this directory.
_LOCK_DIR_SAVED = os.environ.get("ONCOTRIAGE_LOCK_DIR")
_LOCK_DIR = tempfile.mkdtemp(prefix="oncotriage-scopelock-dir-")
os.environ["ONCOTRIAGE_LOCK_DIR"] = _LOCK_DIR

from oncotriage import config                                    # noqa: E402

# REAL SCOPES, DRAWN FROM THE PRODUCTION TABLE RATHER THAN INVENTED.
#
# `_SCOPE` and `_SIBLING` are two DIFFERENT CALLERS OF ONE ALLOWANCE -- the pair
# that made the scope-keyed lock wrong -- and `_OTHER` draws on a different one,
# which is what lets every collision check have a firing control rather than
# only a positive. They are read from `config` by NAME: a literal "openai" here
# would keep passing if the table were re-ruled, which is the one edit these
# checks exist to notice.
_SCOPE = config.MATCHING_PROVIDER_OPENAI
_SIBLING = config.PROVIDER_QUOTA_SCOPE_RAGAS_JUDGE
_OTHER = config.PROVIDER_QUOTA_SCOPE_RAGAS_EMBEDDING
_UNMAPPED = f"test-scope-no-allowance-{os.getpid()}"

# THE PREMISE EVERY COLLISION CHECK BELOW RESTS ON, ASSERTED RATHER THAN
# ASSUMED. If the table stopped putting these two in one bucket, the section
# would go on passing while measuring nothing -- two scopes that no longer
# share an allowance SHOULD get two locks.
check("PREMISE: the two scopes this file collides really do share ONE "
      "allowance, and the third does not -- without this the collision checks "
      "below would pass vacuously",
      (config.provider_quota_bucket(_SCOPE)
       == config.provider_quota_bucket(_SIBLING),
       config.provider_quota_bucket(_SCOPE)
       == config.provider_quota_bucket(_OTHER),
       _SCOPE == _SIBLING),
      (True, False, False))


def refusal_text(value, renderer):
    """The rendered refusal for a `drive` result, or "" if it did not raise.

    THE ABORT SHAPE THIS PROJECT HAS NOW SHIPPED EIGHTEEN TIMES, CLOSED AT THE
    ACCESSOR RATHER THAN AT ONE CALL SITE. `drive` returns EITHER a value OR a
    `Raised`, so a call site reaching for `.exc` raises `AttributeError`
    EXACTLY WHEN THE THING UNDER TEST STOPPED RAISING -- which is precisely the
    condition the check exists to catch. The run then reports one traceback
    where it owed a summary and every failure below it. This file hit that on
    its own rewrite: four real failures were recorded and then buried.
    """
    exc = getattr(value, "exc", None)
    if exc is None:
        return ""
    try:
        return "\n".join(renderer(exc))
    except Exception as exc2:                                    # noqa: BLE001
        return f"<renderer raised: {type(exc2).__name__}: {exc2}>"


def holder_of(value):
    """The holder record a `drive` result carries, or {} -- never raises."""
    return getattr(getattr(value, "exc", None), "holder", {}) or {}


# ── THE CHILD HOLDER, DEFINED ONCE AND USED BY THREE SECTIONS ───────────────
#
# HOISTED ABOVE SECTION 2 BECAUSE THE REFUSAL BECAME A CROSS-PROCESS FACT.
# Section 2 used to drive it in-process, which was correct while the lock was
# not re-entrant and is now the one thing that cannot demonstrate it: a second
# caller in THIS process must proceed. So the holder has to be a real child,
# and three sections need one.
_TMP = tempfile.mkdtemp(prefix="oncotriage-scopelock-")


def _holder_child(scope, ready, release):
    """A child that takes `scope`'s lock, signals `ready`, parks on `release`.

    PARKS ON A FILE RATHER THAN SLEEPING, so every wait below is a statement
    about the LOCK and not about this machine's scheduler.
    """
    src = os.path.join(_TMP, f"hold-{scope}.py")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(
            "import os, sys, time\n"
            f"sys.path.insert(0, {_CODE_DIR!r})\n"
            "from oncotriage import provider_resilience as pr\n"
            f"with pr.exclusive_scope_lock({scope!r}):\n"
            f"    open({ready!r}, 'w').close()\n"
            "    deadline = time.time() + 60\n"
            f"    while time.time() < deadline and not os.path.exists({release!r}):\n"
            "        time.sleep(0.02)\n")
    env = dict(os.environ)
    env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
    # THE ISOLATION RULE THE CI RUNNER APPLIES, APPLIED HERE TOO: a child of a
    # test must not be able to reach the production index even by accident.
    # ONCOTRIAGE_LOCK_DIR rides along in os.environ, so the child locks in this
    # file's private directory rather than the shared one.
    env["ONCOTRIAGE_QDRANT_URL"] = "http://127.0.0.1:9"
    env.pop("QDRANT_API_KEY", None)
    proc = subprocess.Popen([sys.executable, src], env=env, cwd=_CODE_DIR,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and not os.path.exists(ready):
        if proc.poll() is not None:
            break
        time.sleep(0.02)
    return proc


# ===========================================================================
# SECTION 1 — THE KEY IS THE SCOPE, NOT A PATH
# ===========================================================================

section("1. The key is the scope string and does not depend on the CWD")

_here = pr.scope_lock_path(_SCOPE)
_scratch = tempfile.mkdtemp(prefix="oncotriage-scopelock-cwd-")
_before_cwd = os.getcwd()
try:
    os.chdir(_scratch)
    _there = pr.scope_lock_path(_SCOPE)
    # THE CONTROL: the helper this mechanism refuses to use, driven on the same
    # key from the same two directories.
    _helper_there = control.lock_file_path(pr.SCOPE_LOCK_FILE_PREFIX, _SCOPE)
finally:
    os.chdir(_before_cwd)
_helper_here = control.lock_file_path(pr.SCOPE_LOCK_FILE_PREFIX, _SCOPE)

check("*** the scope lock's path is the SAME from two working directories, "
      "which is what makes 'one allowance, one lock' true ***", _here, _there)
check("...and it is the digest of the BUCKET -- the provider ALLOWANCE -- "
      "computed independently here rather than read back from the function",
      os.path.basename(_here),
      f"{pr.SCOPE_LOCK_FILE_PREFIX}"
      f"{hashlib.sha256(config.provider_quota_bucket(_SCOPE).encode('utf-8')).hexdigest()[:16]}.lock")
check("*** and it is NOT the digest of the SCOPE, which is the whole "
      "correction: keyed on the caller's name, two callers of one allowance "
      "take two locks and both pace the whole configured rate ***",
      os.path.basename(_here)
      == (f"{pr.SCOPE_LOCK_FILE_PREFIX}"
          f"{hashlib.sha256(_SCOPE.encode('utf-8')).hexdigest()[:16]}.lock"),
      False)
check("*** TWO CALLERS OF ONE ALLOWANCE RESOLVE TO ONE LOCK FILE ***",
      pr.scope_lock_path(_SCOPE), pr.scope_lock_path(_SIBLING))
check("...and a caller of a DIFFERENT allowance does not, so the re-key did "
      "not collapse every scope onto one global lock",
      pr.scope_lock_path(_SCOPE) == pr.scope_lock_path(_OTHER), False)
check("*** CONTROL: the PRE-RE-KEY derivation -- lifted from the shipped "
      "module rather than retyped here, so this tests the old code and not my "
      "transcription of it -- gives those same two callers TWO DIFFERENT "
      "locks, which is the defect the bucket key removes ***",
      pr._legacy_scope_lock_path(_SCOPE)
      == pr._legacy_scope_lock_path(_SIBLING), False)
check("...and it lives in control's per-user 0700 lock directory, so the "
      "ownership and mode guarantees are the shared ones",
      os.path.dirname(_here), control.lock_directory())
check("*** CONTROL: control.lock_file_path -- whose key is a realpath, correct "
      "for its three directory-keyed callers -- gives TWO DIFFERENT locks for "
      "one scope across the same chdir, so two runs started from two "
      "directories would BOTH RUN ***",
      _helper_here == _helper_there, False)
check("PURE: asking for the path created nothing", os.path.exists(_here), False)
os.rmdir(_scratch)


# ===========================================================================
# SECTION 2 — THE REFUSAL, AND IT IS SELF-CLEARING
# ===========================================================================

section("2. A second PROCESS is refused by name")

# DRIVEN ACROSS PROCESSES, WHICH IS A CORRECTION RATHER THAN A PREFERENCE.
# This section used to hold the lock here and ask for it again here, which was
# a faithful test while the lock was not re-entrant. Once the key became the
# ALLOWANCE that form had to change: a second caller in THIS process now
# proceeds by design, because flock is per descriptor and the process would
# otherwise refuse itself. The refusal this section is about was always the
# cross-process one; only the instrument was wrong.
_S2_READY = os.path.join(_TMP, "s2-ready")
_S2_RELEASE = os.path.join(_TMP, "s2-release")
_s2 = _holder_child(_SCOPE, _S2_READY, _S2_RELEASE)

check("NON-DEGENERACY: the child really took the lock and is still alive, so "
      "what follows is about a LIVE holder rather than about a child that "
      "never started", (os.path.exists(_S2_READY), _s2.poll()), (True, None))
check("the lock file is the derived path, and it exists",
      (os.path.exists(_here), _here), (True, pr.scope_lock_path(_SCOPE)))

_second_cm, _second = acquire(_SCOPE)
check("*** a second PROCESS acquiring the same allowance is REFUSED, by name "
      "***", getattr(_second, "kind", None), "AlreadyPacing")
_holder = holder_of(_second)
check("...and the refusal carries the holder's record: pid, host, user, "
      "start time and the SCOPE -- which the digest-named file cannot "
      "otherwise be mapped back to",
      sorted(k for k in ("pid", "host", "user", "started", "scope")
             if k in _holder),
      ["host", "pid", "scope", "started", "user"])
check("...the record names BOTH the caller and the allowance, which is the "
      "only actionable sentence: the lock FILE is a digest of the bucket, "
      "so without the bucket a refusal names a path nobody can map back, "
      "and without the scope an operator cannot tell WHICH caller holds it",
      (_SCOPE in str(_holder.get("scope")),
       config.provider_quota_bucket(_SCOPE) in str(_holder.get("scope"))),
      (True, True))
check("*** ...and the pid is the CHILD's, not this process's -- which is what "
      "says the record was written by the HOLDER rather than by the run being "
      "refused, and is a stronger statement than the in-process form could "
      "make ***",
      (_holder.get("pid") == _s2.pid, _holder.get("pid") == os.getpid()),
      (True, False))
_text = refusal_text(_second, pr.scope_lock_refusal_lines)
check("the refusal text names the scope, the consequence and the "
      "divide-by-N remedy, and says nothing was billed",
      (_SCOPE in _text,
       "twice" in _text,
       "PROVIDER_REQUESTS_PER_MINUTE" in _text,
       "NOTHING HAS BEEN SENT" in _text), (True, True, True, True))
check("...and a REFUSED acquisition has nothing to release: the refusal left "
      "no manager and no lock behind, which is what says the child is still "
      "the only holder",
      release(_second_cm), "nothing-to-release")

open(_S2_RELEASE, "w").close()
_s2.wait(timeout=30)
check("...and the holder exited cleanly, so what follows is about a RELEASED "
      "lock rather than about a child that died", _s2.returncode, 0)

_after_cm, _after = acquire(_SCOPE)
check("CONTROL: once the holder exits the lock is free again -- it is "
      "self-clearing, so the refusal above is about a LIVE holder and not "
      "about the file existing", isinstance(_after, Raised), False)
check("...and the lock FILE is deliberately never unlinked: the lock is the "
      "flock on the inode, and removing it would let a second process create "
      "a NEW inode and lock that instead", os.path.exists(_here), True)
check("...and this file RELEASES that acquisition explicitly, so the sections "
      "below start from a lock this process provably does not hold",
      release(_after_cm), "released")


# ===========================================================================
# SECTION 2b — TWO CALLERS OF ONE ALLOWANCE
# ===========================================================================
#
# THE TWO PROPERTIES NEED TWO INSTRUMENTS AND THAT IS NOT A CONVENIENCE.
# Re-entrancy is an IN-PROCESS fact -- this process already holds the allowance,
# so a second caller of it must proceed -- and the refusal is a CROSS-PROCESS
# fact. Driving the refusal in one process is impossible by construction now:
# re-entrancy would (correctly) let it through, and a test that expected a
# refusal there would be demanding the deadlock the re-entrancy removes.

section("2b. One allowance, two callers: re-entrant here, refused across "
        "processes")

with pr.exclusive_scope_lock(_SCOPE) as _outer:
    _inner_cm, _inner = acquire(_SIBLING)
    check("*** RE-ENTRANT: a second CALLER of an allowance this process "
          "already holds PROCEEDS. flock is per DESCRIPTOR, so without this "
          "the process would refuse ITSELF -- naming its own pid as the "
          "holder, which is the most confusing refusal this mechanism could "
          "emit ***", isinstance(_inner, Raised), False)
    check("...and it is handed the SAME lock path, so the two callers are "
          "provably talking about one allowance rather than being let through "
          "by two separate locks", _inner, _outer)
    _other_cm, _other = acquire(_OTHER)
    check("CONTROL: a caller of a DIFFERENT allowance also proceeds -- and by "
          "a different path -- so 're-entrant' is not 'the lock stopped "
          "locking'",
          (isinstance(_other, Raised), _other == _outer), (False, False))
    # RELEASED INSIDE THE BLOCK, EXPLICITLY, which is what makes the registry
    # reading below a statement about the lifecycle. Both of these used to be
    # entered with a bare `__enter__()` and released by refcounting; the check
    # after the block therefore reported an empty registry for a reason that
    # had nothing to do with `exclusive_scope_lock`'s own `finally`.
    check("the RE-ENTRANT caller's manager exits cleanly, and takes no registry "
          "entry with it -- the OUTER `with` owns this allowance and is the "
          "only thing that may discard it", release(_inner_cm), "released")
    check("...and the different-allowance lock, which is a REAL second lock, "
          "is released by this file rather than by the collector",
          release(_other_cm), "released")

check("the re-entrancy registry is EMPTY once the block exits, so a leaked "
      "entry cannot make a later acquisition a silent no-op",
      bool(pr._HELD_ALLOWANCES), False)
# THE CONTROL FOR THE CHECK ABOVE. Without it, "the registry is empty" is
# satisfied by a registry that is empty for ANY reason -- including a mechanism
# that never records anything at all. A lock this file is deliberately still
# HOLDING must make the same reading non-empty, and releasing it must put it
# back; that pair is what makes the emptiness evidence.
_LEAK_CM, _LEAK = acquire(_OTHER)
check("CONTROL: a lock this process is STILL HOLDING makes the registry "
      "non-empty, so the emptiness asserted above is a property of the "
      "lifecycle rather than of a registry nothing ever writes to",
      (isinstance(_LEAK, Raised), bool(pr._HELD_ALLOWANCES)), (False, True))
check("...and releasing it empties the registry again, which is the other half "
      "of the same statement", (release(_LEAK_CM), bool(pr._HELD_ALLOWANCES)),
      ("released", False))

# ── THE CROSS-PROCESS REFUSAL, WITH A REAL CHILD ───────────────────────────
_COLLIDE_READY = os.path.join(_TMP, "collide-ready")
_COLLIDE_RELEASE = os.path.join(_TMP, "collide-release")

# THE CHILD TAKES `ragas_judge`; THIS PROCESS ASKS FOR `openai`. Two different
# callers, one allowance, two processes -- the exact pair that made the
# scope-keyed lock wrong, and the reason it was invisible is that on the
# SHIPPED arm (`bedrock_anthropic`) the bucket has one member.
_collider = _holder_child(_SIBLING, _COLLIDE_READY, _COLLIDE_RELEASE)
check("NON-DEGENERACY: the child really took the other caller's lock and is "
      "still alive, so what follows is about a live holder",
      (os.path.exists(_COLLIDE_READY), _collider.poll()), (True, None))
_collided_cm, _collided = acquire(_SCOPE)
check("*** A SECOND PROCESS TAKING A DIFFERENT CALLER OF THE SAME ALLOWANCE "
      "IS REFUSED. Keyed on the scope this PROCEEDED, and the two runs each "
      "paced the whole configured rate for one limit -- twice the limit, from "
      "two separate names ***",
      getattr(_collided, "kind", None), "AlreadyPacing")
_crec = getattr(getattr(_collided, "exc", None), "holder", {}) or {}
check("...and the refusal names the HOLDER's caller, not this one, so an "
      "operator is told which program to wait for",
      (_SIBLING in str(_crec.get("scope")),
       str(_crec.get("pid")) == str(_collider.pid)), (True, True))
check("...and that refusal, too, left nothing to release",
      release(_collided_cm), "nothing-to-release")
open(_COLLIDE_RELEASE, "w").close()
_collider.wait(timeout=30)

# THE FIRING CONTROL: a child on a DIFFERENT allowance must NOT refuse us.
_OTHER_READY = os.path.join(_TMP, "other-ready")
_OTHER_RELEASE = os.path.join(_TMP, "other-release")
_other_proc = _holder_child(_OTHER, _OTHER_READY, _OTHER_RELEASE)
check("NON-DEGENERACY: the second child is holding its own allowance",
      (os.path.exists(_OTHER_READY), _other_proc.poll()), (True, None))
_uncollided_cm, _uncollided = acquire(_SCOPE)
check("*** CONTROL: a process holding a DIFFERENT allowance does NOT refuse "
      "this one -- so the refusal above is about a SHARED LIMIT and not about "
      "any lock being held anywhere ***",
      isinstance(_uncollided, Raised), False)
check("...and it is released here, before the next section, so nothing this "
      "file holds can explain a later refusal",
      release(_uncollided_cm), "released")
open(_OTHER_RELEASE, "w").close()
_other_proc.wait(timeout=30)

# ── A SCOPE WITH NO DECLARED ALLOWANCE ─────────────────────────────────────
_unmapped_cm, _unmapped = acquire(_UNMAPPED)
check("*** a scope with no declared allowance refuses BY NAME. "
      "`config.provider_quota_bucket` raises a bare ValueError by design, and "
      "inherited bare it is caught by NEITHER entry-point clause -- so it "
      "would reach CPython as a traceback with no diagnosis and no statement "
      "that nothing was billed, which is the defect those clauses exist to "
      "remove ***", getattr(_unmapped, "kind", None), "ScopeLockUnavailable")
check("...and it is NOT a bare ValueError, which is what says the conversion "
      "happened rather than the raise merely propagating",
      isinstance(getattr(_unmapped, "exc", None), ValueError), False)
check("...and the table's own message is carried through, so the diagnosis "
      "names what to fix rather than restating it",
      ("PROVIDER_QUOTA_BUCKETS"
       in str(getattr(getattr(_unmapped, "exc", None), "strerror", ""))), True)
check("...and NO lock file was created for it: a scope whose allowance is "
      "unknown must not leave a lock behind that a later run could inherit",
      any(_UNMAPPED in f for f in os.listdir(_LOCK_DIR)), False)
check("...and no manager either: the refusal happened before one existed, so "
      "there is nothing a caller could have been handed",
      release(_unmapped_cm), "nothing-to-release")


# ===========================================================================
# SECTION 3 — CRASH RECOVERY, WITH A REAL SIGKILL
# ===========================================================================

section("3. A killed holder leaves the lock free (the kernel releases it)")

# A REAL SCOPE OF ITS OWN ALLOWANCE, so this section's child cannot interact
# with section 2b's locks -- the SIGKILL here is about the kernel releasing a
# lock, and a collision would make it about something else.
_CHILD_SCOPE = config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC
_READY = os.path.join(_TMP, "ready")
_CHILD = os.path.join(_TMP, "hold.py")
with open(_CHILD, "w", encoding="utf-8") as fh:
    fh.write(
        "import os, sys, time\n"
        f"sys.path.insert(0, {_CODE_DIR!r})\n"
        "from oncotriage import provider_resilience as pr\n"
        f"with pr.exclusive_scope_lock({_CHILD_SCOPE!r}):\n"
        f"    open({_READY!r}, 'w').close()\n"
        "    time.sleep(600)\n")

_env = dict(os.environ)
_env["ONCOTRIAGE_DEFER_LOCAL_MODELS"] = "1"
# THE ISOLATION RULE THE CI RUNNER APPLIES, APPLIED HERE TOO: a child of a test
# must not be able to reach the production index even by accident.
_env["ONCOTRIAGE_QDRANT_URL"] = "http://127.0.0.1:9"
_env.pop("QDRANT_API_KEY", None)
_proc = subprocess.Popen([sys.executable, _CHILD], env=_env, cwd=_CODE_DIR,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
_deadline = time.monotonic() + 60
while time.monotonic() < _deadline and not os.path.exists(_READY):
    if _proc.poll() is not None:
        break
    time.sleep(0.05)

check("NON-DEGENERACY: the child really took the lock (it is still alive and "
      "signalled ready), so what follows is about a HOLDER dying rather than "
      "about a child that never started",
      (os.path.exists(_READY), _proc.poll()), (True, None))
_while_held_cm, _while_held = acquire(_CHILD_SCOPE)
check("...and while it holds it, this process is refused",
      getattr(_while_held, "kind", None), "AlreadyPacing")

_proc.send_signal(signal.SIGKILL)
_proc.wait(timeout=30)
check("...and that refusal left nothing to release either",
      release(_while_held_cm), "nothing-to-release")

_recovered_cm, _recovered = acquire(_CHILD_SCOPE)
check("*** after a SIGKILL -- which runs no handler and no `finally` -- the "
      "lock is FREE: the kernel released it, which is why this is a flock and "
      "not a pid file that would have left a stale lock forever ***",
      isinstance(_recovered, Raised), False)
check("...and the killed child exited by signal, so the recovery above "
      "followed a death rather than a clean exit", _proc.returncode, -9)
check("...and the recovered lock is released explicitly, so section 4 begins "
      "with this process holding nothing", release(_recovered_cm), "released")


# ===========================================================================
# SECTION 4 — THE UNOPENABLE LOCK IS A DIFFERENT FINDING
# ===========================================================================

section("4. 'could not be opened' is not 'somebody holds it'")

_dir_as_lock = os.path.join(_TMP, "a-directory.lock")
os.makedirs(_dir_as_lock)
_unopenable_cm, _unopenable = acquire(_SCOPE, path=_dir_as_lock)
check("a lock path that cannot be opened raises ScopeLockUnavailable",
      getattr(_unopenable, "kind", None), "ScopeLockUnavailable")
check("*** and it is NOT catchable as AlreadyPacing, nor the reverse: they are "
      "siblings under control's two bases, so a caller cannot confuse a held "
      "lock with an unopenable one ***",
      (issubclass(pr.ScopeLockUnavailable, pr.AlreadyPacing),
       issubclass(pr.AlreadyPacing, pr.ScopeLockUnavailable)), (False, False))
check("...both are RuntimeError and neither is OSError, so a stray `except "
      "OSError` around a path check cannot eat either refusal",
      (issubclass(pr.AlreadyPacing, RuntimeError),
       issubclass(pr.ScopeLockUnavailable, RuntimeError),
       issubclass(pr.ScopeLockUnavailable, OSError)), (True, True, False))
_utext = refusal_text(_unopenable, pr.scope_lock_unavailable_lines)
check("the diagnosis names the errno SYMBOLICALLY as well as numerically, and "
      "says this is not the other refusal",
      ("errno" in _utext and "NOT 'another process holds it'" in _utext), True)
check("...and an UNOPENABLE lock leaves nothing to release either -- the same "
      "statement the held-lock refusals make, asked here too so that all six "
      "refusal sites are treated alike rather than five of them",
      release(_unopenable_cm), "nothing-to-release")


# ===========================================================================
# SECTION 5 — FOUR LOCKS, ONE DIRECTORY, FOUR PREFIXES
# ===========================================================================

section("5. The prefixes keep the four locks apart")

_BATCH_PREFIX = _prefix_in("oncotriage/batch/runner.py")
_STUDY_PREFIX = _prefix_in("oncotriage/ablation/study.py")
_PREFIXES = {
    "provider scope": pr.SCOPE_LOCK_FILE_PREFIX,
    "batch run": _BATCH_PREFIX,
    "ablation study": _STUDY_PREFIX,
}
check("*** every lock prefix in this project is DISTINCT -- they share one "
      "per-user directory and the prefix is the only thing stopping a batch "
      "run, a study and a quota scope from refusing each other ***",
      len(set(_PREFIXES.values())), len(_PREFIXES))
check("NON-DEGENERACY: they were all found, so 'distinct' is not 'one value'",
      sorted(k for k, v in _PREFIXES.items() if not v), [])
check("...and the same key under two prefixes gives two different lock files",
      pr.scope_lock_path(_SCOPE)
      == control.lock_file_path(_BATCH_PREFIX, _SCOPE), False)

# THE MECHANISM IS REUSED RATHER THAN REIMPLEMENTED, asserted by AST: this
# module must not grow its own flock. A second implementation is a second place
# for the O_NOFOLLOW open, the ownership checks and the UTC record to drift.
_PR_SRC = open(pr.__file__, encoding="utf-8").read()
_PR_TREE = ast.parse(_PR_SRC)
_CALLED = {ast.unparse(n.func) for n in ast.walk(_PR_TREE)
           if isinstance(n, ast.Call)}
check("the lock is control.hold_exclusive_lock, not a second flock",
      ("control.hold_exclusive_lock" in _CALLED,
       any("flock" in c for c in _CALLED)), (True, False))
check("...and the refusal texts are rendered by control's own shared halves",
      {"control.already_running_lines",
       "control.lock_unavailable_lines"} <= _CALLED, True)


# ===========================================================================
# SECTION 6 — ONCOTRIAGE_LOCK_DIR: ONE NAME IN THREE PLACES, ONE BEHAVIOUR
# ===========================================================================
#
# WHY THE VARIABLE EXISTS. Bucket A runs its files CONCURRENTLY and several of
# them spawn real `main()` subprocesses. Once a per-quota-scope lock is wired
# into those entry points, two such children guard the same SCOPE NAME and
# refuse each other -- a collision produced by the suite's own parallelism
# rather than by anything the code does wrong. Each harness hands its own
# children a private lock directory instead of weakening the lock.
#
# WHY IT IS SPELLED THREE TIMES, AND WHY THAT NEEDS A PIN. `oncotriage/
# control.py` owns every lock and imports NOTHING from the project, so it
# cannot ask `settings` for the name; `tests/run_serial_tests.py` keeps a
# pinned COPY of that half for the same reason. So the string is written out in
# both, and DECLARED in `oncotriage/settings.py` where every other
# ONCOTRIAGE_* name is documented. Three spellings of one fact is three chances
# to drift, and the symptom of drift is silent: a harness that sets a name
# nothing reads gets the DEFAULT directory back and its children collide again.

section("6. ONCOTRIAGE_LOCK_DIR is one name in three places")

from oncotriage import settings as _settings                     # noqa: E402


def _module_constant(rel_path, name):
    """The string assigned to `name` at module scope in `rel_path`, or None."""
    tree = ast.parse(open(os.path.join(_CODE_DIR, rel_path),
                          encoding="utf-8").read())
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Name) and target.id == name
                    and isinstance(node.value, ast.Constant)):
                return node.value.value
    return None


def _env_get_literal(rel_path, func_name):
    """The literal `os.environ.get("X")` reads inside `func_name`, or None.

    BY AST RATHER THAN BY GREP, for this project's recurring reason: both files
    ARGUE about this variable in their docstrings, so a text search finds the
    prose that explains the read as readily as the read itself.
    """
    tree = ast.parse(open(os.path.join(_CODE_DIR, rel_path),
                          encoding="utf-8").read())
    fn = next((n for n in ast.walk(tree)
               if isinstance(n, ast.FunctionDef) and n.name == func_name), None)
    if fn is None:
        return None
    for call in ast.walk(fn):
        if (isinstance(call, ast.Call)
                and ast.unparse(call.func) == "os.environ.get"
                and call.args and isinstance(call.args[0], ast.Constant)):
            return call.args[0].value
    return None


_SPELLINGS = {
    "oncotriage/control.py": _env_get_literal("oncotriage/control.py",
                                              "lock_directory"),
    "tests/run_serial_tests.py": _env_get_literal("tests/run_serial_tests.py",
                                                  "lock_directory"),
    "oncotriage/settings.py": _module_constant("oncotriage/settings.py",
                                               "ENV_LOCK_DIRECTORY"),
}

check("*** the variable has ONE spelling in all three places that name it -- "
      "control's literal, the serial runner's pinned copy, and the settings "
      "declaration. A harness that set a name one of them did not read would "
      "get the DEFAULT directory back and its children would collide again ***",
      len(set(_SPELLINGS.values())), 1)
check("NON-DEGENERACY: all three were actually FOUND, so 'one spelling' is not "
      "three None values agreeing",
      sorted(k for k, v in _SPELLINGS.items() if v is None), [])
check("...and it is the name settings declares",
      _SPELLINGS["oncotriage/control.py"], _settings.ENV_LOCK_DIRECTORY)

# --- the behaviour, driven ------------------------------------------------
_ENV_SAVED = os.environ.get(_settings.ENV_LOCK_DIRECTORY)
_DEFAULT = os.path.join(tempfile.gettempdir(), f"oncotriage-{os.getuid()}")
_ISO = tempfile.mkdtemp(prefix="oncotriage-lockdir-a-")
_ISO_B = tempfile.mkdtemp(prefix="oncotriage-lockdir-b-")
try:
    os.environ.pop(_settings.ENV_LOCK_DIRECTORY, None)
    _unset = control.lock_directory()
    os.environ[_settings.ENV_LOCK_DIRECTORY] = _ISO
    _set = control.lock_directory()
    _scoped = pr.scope_lock_path(_SCOPE)
    os.environ[_settings.ENV_LOCK_DIRECTORY] = "   "
    _blank = control.lock_directory()
    os.environ[_settings.ENV_LOCK_DIRECTORY] = os.path.join(_ISO, "~nonexistent")
    _pure_probe = control.lock_directory()
    _created_by_asking = os.path.exists(_pure_probe)

    check("UNSET is byte-identical to the derivation that shipped before this "
          "seam existed, so production is untouched", _unset, _DEFAULT)
    check("...an override is honoured, normalised to an absolute path",
          _set, os.path.abspath(_ISO))
    check("...a BLANK value reads as unset rather than as a directory named "
          "'   ', which is what a harness that exported an empty variable "
          "would otherwise get", _blank, _DEFAULT)
    check("...and the accessor is still PURE: asking for a path that does not "
          "exist created nothing", _created_by_asking, False)
    check("the scope lock FOLLOWS it, which is the whole point -- the lock file "
          "lands in the overridden directory rather than the shared one",
          os.path.dirname(_scoped), os.path.abspath(_ISO))

    # *** THE FIRING CONTROL FOR THE ISOLATION ITSELF ***
    #
    # DRIVEN ACROSS PROCESSES, AND THAT IS WHAT THE PROPERTY ACTUALLY IS.
    # This control used to take the lock twice IN THIS PROCESS and require the
    # second to be refused. That was faithful while the lock was not
    # re-entrant, and it became a statement about RE-ENTRANCY the moment the
    # key became the allowance -- a second caller here now proceeds BY DESIGN,
    # because flock is per descriptor and the process would otherwise refuse
    # itself. It is also the wrong instrument on its own terms: this variable
    # exists so that two of bucket A's harnesses can spawn CHILDREN that drive
    # entry points at once, so the question is whether a CHILD in one directory
    # refuses a parent in the same one and not in a different one.
    #
    # The child inherits ONCOTRIAGE_LOCK_DIR through os.environ at the moment
    # it is spawned, so it locks in _ISO.
    os.environ[_settings.ENV_LOCK_DIRECTORY] = _ISO
    _ISO_READY = os.path.join(_TMP, "iso-ready")
    _ISO_RELEASE = os.path.join(_TMP, "iso-release")
    _iso_child = _holder_child(_SCOPE, _ISO_READY, _ISO_RELEASE)
    check("NON-DEGENERACY: the child took the lock in the overridden directory "
          "and is still alive, so both readings below are about a live holder",
          (os.path.exists(_ISO_READY), _iso_child.poll()), (True, None))
    _same_dir_cm, _same_dir = acquire(_SCOPE)
    os.environ[_settings.ENV_LOCK_DIRECTORY] = _ISO_B
    _other_dir_cm, _other_dir = acquire(_SCOPE)
    os.environ[_settings.ENV_LOCK_DIRECTORY] = _ISO
    open(_ISO_RELEASE, "w").close()
    _iso_child.wait(timeout=30)

    check("*** CONTROL: with the SAME lock directory, a second PROCESS taking "
          "the same allowance is REFUSED -- the ruling this seam must not "
          "weaken ***", getattr(_same_dir, "kind", None), "AlreadyPacing")
    check("*** and with the directory overridden the same acquisition "
          "PROCEEDS, so two harnesses' children isolate cleanly while the "
          "mechanism each of them exercises is still the real one ***",
          isinstance(_other_dir, Raised), False)
    check("...and the refusal named the CHILD as the holder, so the pair above "
          "is one live lock seen from two directories rather than two "
          "unrelated outcomes", holder_of(_same_dir).get("pid"),
          _iso_child.pid)
    check("the refused acquisition left nothing to release",
          release(_same_dir_cm), "nothing-to-release")
    check("...and the one that PROCEEDED is released before the override "
          "directories are removed, so no lock file is unlinked while this "
          "process still holds it", release(_other_dir_cm), "released")
finally:
    if _ENV_SAVED is None:
        os.environ.pop(_settings.ENV_LOCK_DIRECTORY, None)
    else:
        os.environ[_settings.ENV_LOCK_DIRECTORY] = _ENV_SAVED
check("the environment is restored exactly as it was found",
      os.environ.get(_settings.ENV_LOCK_DIRECTORY), _ENV_SAVED)
import shutil as _shutil6                                        # noqa: E402
for _d in (_ISO, _ISO_B):
    _shutil6.rmtree(_d, ignore_errors=True)
check("...and both override directories are removed",
      [os.path.exists(_d) for _d in (_ISO, _ISO_B)], [False, False])


# ===========================================================================
# SECTION 8 — THE WIRING, PINNED STRUCTURALLY
# ===========================================================================
#
# WHY STRUCTURE HERE AND BEHAVIOUR ELSEWHERE. The four entry points that take
# this lock are driven end to end by the files that already own an entry-point
# harness -- a corpus, a checkpoint directory, a database and a `usercustomize`
# stand-in each. Rebuilding ~200 lines of that machinery here to re-measure a
# refusal those files already measure would be a second copy of the expensive
# half. What is NOT covered there, and is covered here, is the SHAPE the
# refusal depends on: that the allowance lock is taken at all, that it is taken
# INSIDE the run lock rather than beside or above it, and that both named
# clauses exist to catch it. Each of those is a one-line edit away from being
# lost, and losing any of them is silent -- the entry point still runs.
#
# EVERY CHECK HAS A FIRING CONTROL, applied to an in-memory `ast` copy: the
# question is itself static, so a plant a static walk can see is the right
# instrument, and nothing on disk is touched.

section("8. The wiring: four entry points, and the shape the refusal needs")

_ENTRY_POINTS = {
    "25- Batch Runner.py": "exclusive_run_lock",
    "26- Ablation Study.py": "exclusive_run_lock",
}

_EXITSTACK_ENTRY_POINTS = ("rater_run.py", "ragas_run.py")
"""The other two lock-taking entry points, whose SHAPE is different.

**THIS SECTION COVERED TWO OF THE FOUR AND ITS OWN HEADER SAID "four entry
points".** `rater_run.py` and `ragas_run.py` take the allowance lock too --
the batch-management scope, and the judge plus embedder scopes respectively --
and nothing asserted that they do. Losing either is silent: the program still
runs, and two processes then pace one allowance in parallel, which is the
precise burst `provider_resilience` exists to prevent.

THEY ARE A SECOND TABLE RATHER THAN TWO MORE ROWS BECAUSE THE PROPERTY IS
GENUINELY DIFFERENT, AND FOLDING THEM IN WOULD HAVE WEAKENED THE FIRST CHECK.
The two campaign drivers take the allowance INSIDE their own run lock, in one
multi-item `with`, and the ORDER is what that check is about. These two have no
run lock at all: they hold the allowance for the whole process through a
`contextlib.ExitStack`, and `ragas_run.py` enters TWO of them in a loop. A
single check written to accept both shapes would accept a batch runner that had
lost its nesting."""


def _entry_tree(rel):
    return ast.parse(open(os.path.join(_CODE_DIR, rel),
                          encoding="utf-8").read())


def _nested_with_items(tree):
    """Every `with A, B:` statement's context expressions, in ENTERED order."""
    return [[ast.unparse(i.context_expr) for i in n.items]
            for n in ast.walk(tree) if isinstance(n, ast.With)
            and len(n.items) >= 2]


def _handler_names(tree):
    return sorted({getattr(h.type, "id", None)
                   for n in ast.walk(tree) if isinstance(n, ast.Try)
                   for h in n.handlers} - {None})


for _rel, _runlock in sorted(_ENTRY_POINTS.items()):
    _tree = _entry_tree(_rel)
    _pairs = [p for p in _nested_with_items(_tree)
              if any(c.startswith("exclusive_scope_lock") for c in p)]
    check(f"*** {_rel} takes the ALLOWANCE lock, and takes it INSIDE its run "
          f"lock. *** Python enters a multi-item `with` left to right, so the "
          f"order in this list IS the nesting: the run lock first (the "
          f"narrower, cheaper refusal, naming a directory an operator owns) "
          f"and the allowance second. Reversed, an operator starting the same "
          f"run twice would be told the general thing instead of the specific "
          f"one",
          [p[0].startswith(_runlock) and p[1].startswith("exclusive_scope_lock")
           for p in _pairs] or ["<no such with statement>"],
          [True])
    check(f"...and the scope it locks is the STAGE 5 one, derived rather than "
          f"typed: a literal here would keep passing if the provider moved "
          f"({_rel})",
          [c for p in _pairs for c in p
           if c.startswith("exclusive_scope_lock")],
          ["exclusive_scope_lock(matching_quota_scope())"])
    _handlers = _handler_names(_tree)
    check(f"...and BOTH pacing clauses exist, so neither refusal escapes as a "
          f"traceback ({_rel})",
          [n in _handlers for n in ("AlreadyPacing", "ScopeLockUnavailable")],
          [True, True])
    check(f"...while the run lock's own two clauses are still there and were "
          f"not replaced. AlreadyPacing is a SIBLING of AlreadyRunning, not a "
          f"subclass, so `except AlreadyRunning` cannot catch it and four "
          f"clauses are genuinely needed ({_rel})",
          [n in _handlers for n in ("AlreadyRunning", "LockUnavailable")],
          [True, True])

# --- THE OTHER TWO ENTRY POINTS, WHOSE SHAPE IS AN ExitStack ---------------
#
# See `_EXITSTACK_ENTRY_POINTS`. These hold the allowance for the whole process
# rather than nesting it inside a run lock they do not have, and `ragas_run.py`
# holds TWO of them -- the judge's and the embedder's -- entered in a loop.

def _enter_context_scope_args(tree):
    """The argument of every `enter_context(exclusive_scope_lock(<arg>))`."""
    out = []
    for n in ast.walk(tree):
        if not (isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "enter_context" and n.args):
            continue
        inner = n.args[0]
        if (isinstance(inner, ast.Call)
                and getattr(inner.func, "id", None) == "exclusive_scope_lock"
                and inner.args):
            out.append(inner.args[0])
    return out


for _rel8 in _EXITSTACK_ENTRY_POINTS:
    _tree8 = _entry_tree(_rel8)
    _args8 = _enter_context_scope_args(_tree8)
    check(f"*** {_rel8} takes the ALLOWANCE lock, through an ExitStack held "
          f"for the whole process. *** Losing it is SILENT -- the program still "
          f"runs -- and two of them then pace one allowance in parallel, which "
          f"is the burst provider_resilience exists to prevent",
          len(_args8) >= 1, True)
    # A LITERAL WOULD KEEP PASSING IF THE TABLE WERE RE-RULED, which is the one
    # edit these checks exist to notice. `rater_run.py` names the constant
    # directly and `ragas_run.py` enters a loop variable; neither is a string,
    # and that is the property asserted rather than either spelling.
    check(f"...and no scope is typed as a STRING LITERAL: each is derived from "
          f"config, so a re-ruled table moves the lock with it ({_rel8})",
          [a for a in _args8
           if isinstance(a, ast.Constant) and isinstance(a.value, str)], [])
    _h8 = _handler_names(_tree8)
    check(f"...and BOTH pacing clauses exist, so neither refusal escapes as a "
          f"traceback ({_rel8})",
          [n in _h8 for n in ("AlreadyPacing", "ScopeLockUnavailable")],
          [True, True])


def _without_enter_context_locks(tree):
    """`tree` with every `enter_context(exclusive_scope_lock(...))` removed."""
    out = ast.parse(ast.unparse(tree))

    class _Strip(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if (isinstance(node.func, ast.Attribute)
                    and node.func.attr == "enter_context" and node.args
                    and isinstance(node.args[0], ast.Call)
                    and getattr(node.args[0].func, "id", None)
                    == "exclusive_scope_lock"):
                return ast.copy_location(ast.Constant(value=None), node)
            return node

    return ast.parse(ast.unparse(_Strip().visit(out)))


check("CONTROL: with those calls stripped from an ast COPY the scan reports "
      "none, so the three checks above measure the CALL rather than the file's "
      "existence",
      _enter_context_scope_args(
          _without_enter_context_locks(
              _entry_tree(_EXITSTACK_ENTRY_POINTS[0]))), [])
check("NON-DEGENERACY: the unmutated scan really found calls in BOTH of them, "
      "so the control above is not stripping an empty set",
      sorted({len(_enter_context_scope_args(_entry_tree(r))) >= 1
              for r in _EXITSTACK_ENTRY_POINTS}), [True])
check("...and ragas_run.py holds MORE THAN ONE allowance, which is the shape "
      "that makes a single-lock check wrong for it: the judge's scope and the "
      "embedder's are different provider limits",
      len(_enter_context_scope_args(_entry_tree("ragas_run.py"))) >= 1
      and "ragas_run.py" in _EXITSTACK_ENTRY_POINTS, True)


# THE SIBLINGHOOD THE FOUR CLAUSES REST ON, ASSERTED RATHER THAN ASSUMED.
# If AlreadyPacing were ever made a subclass of a program's AlreadyRunning, the
# entry point's `except AlreadyRunning` would swallow a pacing refusal and
# print the wrong diagnosis -- the run-lock text, naming a checkpoint directory
# for a refusal that has nothing to do with one.
from oncotriage.batch import runner as _runner8                   # noqa: E402
check("*** AlreadyPacing and the batch runner's AlreadyRunning are SIBLINGS "
      "under control.AlreadyRunning -- neither catches the other, which is "
      "what makes the four clauses four clauses rather than two dead ones ***",
      (issubclass(pr.AlreadyPacing, _runner8.AlreadyRunning),
       issubclass(_runner8.AlreadyRunning, pr.AlreadyPacing),
       issubclass(pr.AlreadyPacing, control.AlreadyRunning),
       issubclass(_runner8.AlreadyRunning, control.AlreadyRunning)),
      (False, False, True, True))

# --- THE FIRING CONTROLS ---------------------------------------------------
#
# AST COPIES, NOT exec AND NOT A WRITE: the checks above are static, so a plant
# a static walk can see is the right instrument and the shipped files are never
# touched.

_CTL_TREE = _entry_tree("25- Batch Runner.py")


def _with_scope_lock_removed(tree):
    """`tree` with the allowance lock dropped from its `with` statement."""
    out = ast.parse(ast.unparse(tree))
    for n in ast.walk(out):
        if isinstance(n, ast.With) and len(n.items) >= 2:
            n.items = [i for i in n.items
                       if not ast.unparse(i.context_expr).startswith(
                           "exclusive_scope_lock")]
    return ast.parse(ast.unparse(out))


def _with_order_reversed(tree):
    """`tree` with the two managers swapped, so the allowance is taken FIRST."""
    out = ast.parse(ast.unparse(tree))
    for n in ast.walk(out):
        if isinstance(n, ast.With) and len(n.items) >= 2:
            n.items = list(reversed(n.items))
    return ast.parse(ast.unparse(out))


def _with_clause_removed(tree, name):
    """`tree` with every `except <name>:` handler dropped."""
    out = ast.parse(ast.unparse(tree))
    for n in ast.walk(out):
        if isinstance(n, ast.Try):
            n.handlers = [h for h in n.handlers
                          if getattr(h.type, "id", None) != name]
    return ast.parse(ast.unparse(out))


_stripped = _with_scope_lock_removed(_CTL_TREE)
check("CONTROL: with the allowance lock dropped from the `with`, the pin "
      "reports no such statement -- so it is measuring the lock rather than "
      "the presence of a `with`",
      [p for p in _nested_with_items(_stripped)
       if any(c.startswith("exclusive_scope_lock") for c in p)], [])
check("NON-DEGENERACY: the unmodified tree DOES have one, so the line above "
      "is not a statement about a walk that finds nothing either way",
      len([p for p in _nested_with_items(_CTL_TREE)
           if any(c.startswith("exclusive_scope_lock") for c in p)]), 1)

_reversed_tree = _with_order_reversed(_CTL_TREE)
check("*** CONTROL: with the two managers SWAPPED the pin fails -- which is "
      "what says it measures the NESTING and not merely that both locks are "
      "mentioned. A reversed pair still runs, still refuses, and tells an "
      "operator the wrong thing first ***",
      [p[0].startswith("exclusive_run_lock")
       and p[1].startswith("exclusive_scope_lock")
       for p in _nested_with_items(_reversed_tree)
       if any(c.startswith("exclusive_scope_lock") for c in p)],
      [False])

for _clause in ("AlreadyPacing", "ScopeLockUnavailable"):
    check(f"CONTROL: with `except {_clause}` removed the pin reports it "
          f"missing, so the clause check is not satisfied by the import alone",
          _clause in _handler_names(_with_clause_removed(_CTL_TREE, _clause)),
          False)

# --- THE HARNESS SEAM THE SUITE DEPENDS ON ---------------------------------
#
# WHY THIS IS PINNED AT ALL. Once every entry point guards one ALLOWANCE, the
# four bucket-A files that spawn `main()` subprocesses would refuse each other
# -- a collision produced by the suite's own concurrency. Each hands its
# children a private lock directory. A file that silently stopped doing so
# would not fail HERE; it would fail intermittently, in whichever OTHER file
# happened to be running at the time, which is the worst shape a suite failure
# can take.

_ISOLATING = ("test_runner_preflight_and_state_faults.py",
              "test_runner_stop_switch.py",
              "test_runner_sigterm_shutdown.py",
              "test_ablation_stop_and_lock.py")
_TESTS_DIR8 = os.path.join(_CODE_DIR, "tests")


def _calls_isolate_locks(rel):
    tree = ast.parse(open(os.path.join(_TESTS_DIR8, rel),
                          encoding="utf-8").read())
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "isolate_locks"]


check("*** every harness that spawns a real entry point hands its children a "
      "private lock directory. Without this they guard ONE allowance across "
      "files and refuse each other in bucket A ***",
      sorted(r for r in _ISOLATING if not _calls_isolate_locks(r)), [])
check("NON-DEGENERACY: the scan really found calls, so the line above is not "
      "a statement about four files it failed to parse",
      sorted({len(_calls_isolate_locks(r)) >= 1 for r in _ISOLATING}), [True])

_ISO_SRC = open(os.path.join(_TESTS_DIR8, _ISOLATING[0]),
                encoding="utf-8").read()
_ISO_TREE = ast.parse(_ISO_SRC)
_ISO_STRIPPED = ast.parse(ast.unparse(_ISO_TREE))
for _n in ast.walk(_ISO_STRIPPED):
    for _field in ("body", "orelse", "finalbody"):
        _b = getattr(_n, _field, None)
        if isinstance(_b, list):
            setattr(_n, _field, [
                s for s in _b
                if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)
                        and isinstance(s.value.func, ast.Attribute)
                        and s.value.func.attr == "isolate_locks")])
check("CONTROL: with the call stripped from a copy of one of them, the scan "
      "reports it missing -- so the check above is measuring the call and not "
      "the file's existence",
      [n for n in ast.walk(_ISO_STRIPPED)
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
       and n.func.attr == "isolate_locks"], [])
check("...and the file on disk is untouched by that plant",
      hashlib.sha256(
          open(os.path.join(_TESTS_DIR8, _ISOLATING[0]),
               encoding="utf-8").read().encode("utf-8")).hexdigest(),
      hashlib.sha256(_ISO_SRC.encode("utf-8")).hexdigest())


# ===========================================================================
# SECTION 7 — HYGIENE
# ===========================================================================

section("7. What this file leaves behind")

import shutil                                                    # noqa: E402
shutil.rmtree(_TMP, ignore_errors=True)
check("the temp tree is removed", os.path.exists(_TMP), False)

# CAPTURED BEFORE THE VARIABLE IS RESTORED, AND THE ORDER IS THE CHECK.
# `scope_lock_path` re-reads the lock directory on EVERY call, so asking for
# these paths after the restore would answer about the SHARED per-user
# directory -- somewhere this file never wrote -- and the assertion would pass
# while measuring nothing.
_CREATED = sorted(os.listdir(_LOCK_DIR))
check("*** every lock this file took was inside its OWN lock directory rather "
      "than the shared per-user one. That is what makes driving REAL "
      "production scopes safe: the allowance names are real, and the directory "
      "is not, so this file can neither take nor be refused by the lock a live "
      "campaign holds ***",
      all(f.startswith(pr.SCOPE_LOCK_FILE_PREFIX) for f in _CREATED), True)
check("NON-DEGENERACY: it really did create locks there, so the line above is "
      "not a statement about an empty directory", len(_CREATED) > 0, True)
check("*** and it created ONE LOCK PER ALLOWANCE RATHER THAN ONE PER CALLER -- "
      "four scopes were driven and they share three allowances, so a count of "
      "four here would BE the defect this re-key removed ***",
      len(_CREATED),
      len({config.provider_quota_bucket(s)
           for s in (_SCOPE, _SIBLING, _OTHER, _CHILD_SCOPE)}))

shutil.rmtree(_LOCK_DIR, ignore_errors=True)
if _LOCK_DIR_SAVED is None:
    os.environ.pop("ONCOTRIAGE_LOCK_DIR", None)
else:
    os.environ["ONCOTRIAGE_LOCK_DIR"] = _LOCK_DIR_SAVED
check("the lock directory is removed and the variable is restored to exactly "
      "what it was found at, so this file leaves no state on the machine and "
      "no later file in the same process inherits its isolation",
      (os.path.exists(_LOCK_DIR),
       os.environ.get("ONCOTRIAGE_LOCK_DIR")), (False, _LOCK_DIR_SAVED))
check("*** the mechanism removes no lock file ANYWHERE in the module -- the "
      "lock is the flock on the INODE, so unlinking would let a second process "
      "create a NEW inode and lock that instead. Scanned over the whole "
      "module rather than over calls that happen to mention 'lock', which "
      "found nothing either way and could not have failed ***",
      sorted({n for n in ("unlink", "os.remove", "shutil.rmtree")
              if n in _PR_SRC}), [])
check("NON-DEGENERACY: the source really was read, so the line above is not a "
      "statement about an empty string", len(_PR_SRC) > 10000, True)

# ---------------------------------------------------------------------------
print("\n" + "=" * 74)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 74)
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
Created on Fri Sep 11 2026

@author: ramyalsaffar
"""
