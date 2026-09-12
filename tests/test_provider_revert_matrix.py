# Provider Revert Matrix: each safeguard's absence is detected BY ITS OWN TEST
##############################################################################

"""Remove a safeguard in a COPY of the tree, and require the real test to fail.

WHAT THIS IS FOR, AND WHY THE EXISTING CONTROLS DO NOT ALREADY COVER IT
----------------------------------------------------------------------
``tests/test_provider_scope_lock.py`` carries in-file controls built on
``ast`` copies: it strips a call from a parsed tree and asserts that its own
scan then reports the call missing. Those are correct and they answer a
NARROWER question than this file does -- they prove THE SCAN can see an
absence, which is a statement about the walk. They cannot prove that THE TEST
FILE GOES RED, because nothing runs the file against the stripped tree.

The difference is the one that matters when a safeguard is deleted for real. A
scan that works and a check that fails are different facts, and a file can hold
the first while the second quietly stops being true -- an expectation widened,
a control that no longer discriminates, a check whose subject moved. So each
row here plants the defect ON DISK in a copy, RUNS the real guarding test file
against that copy as a subprocess, and requires a NON-ZERO EXIT.

**AND THE CLEAN CONTROL IS THE LOAD-BEARING ROW.** An unplanted copy is run
first and every guarding file must PASS in it. Without that, a plant "detected"
because the copy itself is broken -- a missing module, a bad path, an editable
install winning over ``PYTHONPATH`` -- looks exactly like a safeguard working.
This project has shipped that false green twice.

THE COPY IS WHAT IS BROKEN, NEVER THE TREE. ``copytree`` into a temp
directory, ``PYTHONPATH`` pointed at it, a ``sitecustomize`` that strips the
editable install's ``MetaPathFinder`` (which otherwise BEATS ``PYTHONPATH``),
and a realpath preflight inside the child asserting that the copy is what
imported. Every plant is ``ast.parse``d before it runs and its occurrence count
is asserted, so a plant that matched nothing is a named PLANT-FAILED rather
than a working safeguard reported as broken, and a plant that does not parse is
a recorded failure rather than an abort.

WHAT IS COPIED, AND WHAT IS DELIBERATELY NOT. ``oncotriage/``, ``tests/`` and
the four entry points -- which is everything the guarding files read.
``09- Testing/ragas-venv`` is **1.7 GB and inside this directory**, so a
``copytree`` of the repository root would copy it eight times over; the copy is
assembled from a named list instead.

WHAT IT COSTS TO RUN
--------------------
No network, no keys, NO SPEND -- no provider library is imported and every
child is a test file this suite already runs offline. No live Qdrant, no model
load, no corpus, no database, no git history. Each child is handed its own
``ONCOTRIAGE_LOCK_DIR`` so its real flocks cannot collide with a concurrent
bucket-A run, and its own ``ONCOTRIAGE_MAIN_PATH`` is left alone. It writes
only inside a ``tempfile.mkdtemp`` it removes and asserts gone, and it EXECS
NOTHING.

**THE COLLISION-MATRIX CLASSIFICATION, SETTLED EXPLICITLY RATHER THAN
ASSUMED**, on the precedent `tests/test_storage_query_layer.py` set ("STAYS
OUT, checked rather than carried forward"). This file WRITES nothing in the
repository -- every plant goes into a copy, and the six originals are
sha256-compared at the end. But the collision matrix has two halves, and the
second is the one that applies here: *a file that writes nothing cannot corrupt
anyone, but it can still BE corrupted.*

**IT READS BOTH WRITER-OWNED FILES.** `_COPY_DIRS` includes `oncotriage/`, so
every copy carries `oncotriage/config.py` and
`oncotriage/registries/cancer_code_registry.py` -- the two files
`tests/run_serial_tests.py:WRITER_OWNED_FILES` names. That is the SAME property
that puts `tests/test_package_invariants.py` in the serial suite, whose own
entry reads "copytree()s the package five times; must copy a RESTORED tree".

**IT IS NEVERTHELESS BUCKET A, AND THE ARGUMENT IS THE RUNNERS RATHER THAN THE
FILE.** The two writers only ever run inside `tests/run_serial_tests.py`, which
holds an exclusive lock for its whole run and is invoked separately from
`ci_test_buckets.py --run A`. Bucket A and the serial suite therefore never
execute concurrently under any documented runner, so there is no window in
which this file can copy a planted tree. What would make that false is someone
running `make serial-tests` and `--run A` at the same time -- which the serial
runner's own lock does not prevent, because it guards the serial suite against
ITSELF rather than against bucket A.

**SO THE RESIDUAL IS NAMED RATHER THAN DENIED:** run those two concurrently and
this file may copy `config.py` mid-plant, and its clean control would then fail
for a reason that is not a defect. The cheap fix if that ever becomes a
supported arrangement is to move this file into `SERIAL_TESTS` beside
`test_package_invariants.py`, for the identical reason.

Run from terminal:
    python tests/test_provider_revert_matrix.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
import os
import sys

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
import shutil
import subprocess
import tempfile


#------------------------------------------------------------------------------


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


_CODE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TMP = tempfile.mkdtemp(prefix="revertmatrix_")

# WHAT THE COPY HOLDS. Everything the guarding files read, and nothing else --
# `09- Testing/ragas-venv` is 1.7 GB and lives in this directory.
_COPY_DIRS = ("oncotriage", "tests")
_COPY_FILES = ("25- Batch Runner.py", "26- Ablation Study.py",
               "rater_run.py", "ragas_run.py")

# THE COPY DELIBERATELY WRITES NO `sitecustomize.py`, AND THAT IS A CONTAINMENT
# FIX RATHER THAN A SIMPLIFICATION.
#
# Python imports exactly ONE `sitecustomize` -- the first one on `sys.path`. An
# earlier form of this file wrote its own into the copy to strip the editable
# install's MetaPathFinder, which SHADOWED whatever `sitecustomize` was already
# on `PYTHONPATH`. When that other one is a network tripwire armed for the run,
# every child launched here runs with containment DISARMED.
#
# MEASURED, NOT PREDICTED: reconciling the covered-process log against the spawn
# log over a full bucket-A run showed EIGHT children of this file -- one per
# guarded run -- that never loaded the hook, for exactly that reason.
#
# THE STRIP MOVED INTO `_PREFLIGHT` INSTEAD, where it runs in the same
# interpreter above the first `import oncotriage` and therefore does the same
# job. Chaining the two `sitecustomize` files was the other candidate and is
# WORSE here: loading a second one means `exec()` or a by-location module load,
# and `tests/test_package_invariants.py` section 1c forbids both outright --
# re-parsing string literals as Python, so writing it inside this string would
# be caught. Not needing a second one at all is the stronger answer.


def _watched(rel):
    return os.path.join(_CODE_DIR, rel)


_WATCHED_FILES = ("oncotriage/evaluation/rater.py",
                  "oncotriage/provider_resilience.py",
                  "tests/test_provider_scope_lock.py",
                  "tests/test_rater_verify_before_retry.py",
                  "tests/test_rater_management_pacing.py",
                  "25- Batch Runner.py")
_BEFORE = {rel: hashlib.sha256(open(_watched(rel), "rb").read()).hexdigest()
           for rel in _WATCHED_FILES}


def _make_copy(name, edits):
    """A copy of the tree with `edits` applied. Returns (root, problems).

    `edits` is a list of (relative path, old, new, expected occurrence count).

    **EVERY FILESYSTEM CALL IS GUARDED, AND THE REASON IS THIS PROJECT'S MOST
    REPEATED DEFECT.** An ``OSError`` out of ``copytree``, ``copy2``,
    ``makedirs``, a read or a write here -- a full disk, a permission, a
    vanished source -- would abort the module while it owes eight results,
    printing one traceback where a summary belongs. That is the shape this
    repository has shipped roughly eighteen times, and a file whose whole
    subject is "a safeguard's absence is detected" must not have it. Every
    failure is returned as a PROBLEM string instead, which the caller reports as
    a named failing check.
    """
    root = os.path.join(_TMP, name)
    problems = []
    try:
        if os.path.isdir(root):
            shutil.rmtree(root)
        os.makedirs(root)
        for d in _COPY_DIRS:
            shutil.copytree(os.path.join(_CODE_DIR, d), os.path.join(root, d),
                            ignore=shutil.ignore_patterns("__pycache__",
                                                          "*.pyc"))
        for f in _COPY_FILES:
            shutil.copy2(os.path.join(_CODE_DIR, f), os.path.join(root, f))
    except Exception as exc:                                  # noqa: BLE001
        return root, [f"{name}: the copy could not be built: "
                      f"{type(exc).__name__}: {exc}"]

    for rel, old, new, expected in edits:
        target = os.path.join(root, rel)
        try:
            text = open(target, encoding="utf-8").read()
        except Exception as exc:                              # noqa: BLE001
            problems.append(f"{rel}: could not be read in the copy: "
                            f"{type(exc).__name__}: {exc}")
            continue
        found = text.count(old)
        if found != expected:
            problems.append(f"{rel}: plant matched {found} times, "
                            f"expected {expected}")
            continue
        text = text.replace(old, new)
        try:
            ast.parse(text)
        except SyntaxError as exc:
            problems.append(f"{rel}: the plant does not parse: {exc}")
            continue
        try:
            open(target, "w", encoding="utf-8").write(text)
        except Exception as exc:                              # noqa: BLE001
            problems.append(f"{rel}: the plant could not be written: "
                            f"{type(exc).__name__}: {exc}")
    return root, problems


_PREFLIGHT = '''import os, sys
# STRIP THE EDITABLE INSTALL'S FINDER -- HERE, not in a `sitecustomize.py` the
# copy owns. setuptools installs a MetaPathFinder that takes precedence over
# `sys.path` entirely, so `PYTHONPATH` alone does not win; this project has
# shipped that false green twice. Doing it here works because this block is
# PREPENDED to the guarded test and runs in the same interpreter, above the
# first `import oncotriage` -- and it leaves the run's own `sitecustomize`
# (a network tripwire, when one is armed) the only one on the path, which is
# what keeps every child of this file covered.
sys.meta_path = [f for f in sys.meta_path
                 if "__editable__" not in type(f).__module__
                 and "__editable__" not in getattr(f, "__name__", "")]
sys.path.insert(0, {root!r})
import oncotriage
_here = os.path.realpath(os.path.dirname(os.path.dirname(
    os.path.abspath(oncotriage.__file__))))
_want = os.path.realpath({root!r})
if _here != _want:
    print("PREFLIGHT-FAILED", _here, "!=", _want)
    raise SystemExit(3)
'''


def _run_guard(root, rel_test, label):
    """Run one guarding test file against `root`. Returns (exit, tail)."""
    # THE PREFLIGHT RUNS IN THE SAME INTERPRETER AS THE TEST, prepended to it,
    # so "the copy is what imported" is established for the process that then
    # makes the assertions rather than for a separate one that proves nothing.
    #
    # **THE SCRIPT IS WRITTEN INSIDE `tests/`, AND THAT IS LOAD-BEARING.**
    # `tests/test_provider_scope_lock.py` derives its repository root as
    # `dirname(dirname(abspath(__file__)))`, so a driver at the copy's TOP
    # level would make that resolve to the temp PARENT -- and every path it
    # then reads (`25- Batch Runner.py`, `tests/`) would miss. Written here the
    # derivation lands on the copy, which is the whole point of the copy.
    script = os.path.join(root, "tests", f"_guard_{label}.py")
    body = open(os.path.join(root, rel_test), encoding="utf-8").read()
    with open(script, "w", encoding="utf-8") as fh:
        fh.write(_PREFLIGHT.format(root=root) + "\n" + body)
    env = dict(os.environ)
    # THE COPY FIRST, AND WHATEVER WAS ALREADY THERE KEPT BEHIND IT. Replacing
    # `PYTHONPATH` outright is what disarmed the tripwire for this file's eight
    # children: the guard directory carrying `sitecustomize.py` was simply
    # dropped. The copy still wins every import because it is FIRST; the rest of
    # the path survives, so a containment hook armed for the run is inherited.
    _inherited = os.environ.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (root + os.pathsep + _inherited) if _inherited else root
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # ITS OWN LOCK DIRECTORY. These files take REAL flocks keyed on real
    # allowance names; without this they would collide with a concurrent
    # bucket-A run on this machine, which is a failure produced by the suite's
    # parallelism rather than by the plant.
    env["ONCOTRIAGE_LOCK_DIR"] = os.path.join(root, "_locks")
    os.makedirs(env["ONCOTRIAGE_LOCK_DIR"], exist_ok=True)
    try:
        done = subprocess.run([sys.executable, script], env=env, cwd=root,
                              capture_output=True, text=True, timeout=1800)
    except Exception as exc:                                  # noqa: BLE001
        return (-1, f"<RAISED {type(exc).__name__}: {exc}>")
    out = (done.stdout + done.stderr)
    return (done.returncode, out[-400:])


# ===========================================================================
# SECTION 1 — THE CLEAN CONTROL
# ===========================================================================

section("1. CLEAN CONTROL: an unplanted copy passes every guarding file")

_GUARDS = ("tests/test_provider_scope_lock.py",
           "tests/test_rater_verify_before_retry.py",
           "tests/test_rater_management_pacing.py")

_clean_root, _clean_problems = _make_copy("clean", [])
check("the clean copy was built with no plant problems", _clean_problems, [])

_clean_results = {}
for _g in _GUARDS:
    _code, _tail = _run_guard(_clean_root, _g, os.path.basename(_g)[:-3])
    _clean_results[_g] = _code
    if _code != 0:
        print(f"        [clean tail] {_g}\n{_tail}")

check("*** every guarding file PASSES in an unplanted copy. Without this row a "
      "plant 'detected' because the COPY is broken -- a bad path, a missing "
      "module, an editable install beating PYTHONPATH -- is indistinguishable "
      "from a safeguard working ***",
      _clean_results, {g: 0 for g in _GUARDS})
check("PREFLIGHT: no guarding run reported importing the real tree instead of "
      "the copy",
      sorted(g for g, c in _clean_results.items() if c == 3), [])


# ===========================================================================
# SECTION 2 — THE MATRIX
# ===========================================================================

section("2. Each safeguard's absence makes its own test file FAIL")

# EACH ROW: (name, subject, edits, guarding test file).
#
# THE EDITS ARE CHOSEN TO BE THE SMALLEST HONEST REMOVAL of the safeguard --
# not a syntax break, which any file would notice, and not a rewrite, which
# would be testing the rewrite.
_MATRIX = [
    (
        "scope_lock_wiring",
        "the batch runner stops taking the ALLOWANCE lock, so two campaigns "
        "on one host each pace the whole configured rate",
        # ONE EDIT, AND IT REMOVES THE SAFEGUARD RATHER THAN RENAMING IT. An
        # earlier draft aliased `exclusive_scope_lock as _no_scope_lock` and
        # called that -- which is the SAME function, so nothing was removed and
        # the plant would have been a rename wearing a removal's costume. The
        # entry point is only ever `ast.parse`d by the guarding file, never
        # imported or run, so an undefined name here is read and never called.
        [("25- Batch Runner.py",
          "exclusive_scope_lock(matching_quota_scope()) as _scope_lock:",
          "_scope_lock_removed_by_revert_matrix() as _scope_lock:", 1)],
        "tests/test_provider_scope_lock.py",
    ),
    (
        "isolate_locks_site",
        "one of the four harnesses stops handing its children a private lock "
        "directory, so they collide with whichever other file is running",
        [("tests/test_runner_stop_switch.py",
          "    _harness.isolate_locks(env, _LOCK_DIR)",
          "    pass  # isolate_locks removed by the revert matrix", 1)],
        "tests/test_provider_scope_lock.py",
    ),
    (
        "reconcile_unknown_is_absent",
        "an unlistable provider reads as 'the batch did not land', which is "
        "acted on by SUBMITTING AGAIN -- the duplicate the whole mechanism "
        "exists to prevent",
        [("oncotriage/evaluation/rater.py",
          "        return RECONCILE_UNKNOWN, None",
          "        return RECONCILE_ABSENT, None", 1)],
        "tests/test_rater_verify_before_retry.py",
    ),
    (
        "create_retried_by_policy",
        "batches.create goes under the policy's own budget, so a throttle "
        "re-issues it and commits a second batch of billed requests",
        [("oncotriage/evaluation/rater.py",
          '"batches.create", max_attempts=1)',
          '"batches.create", max_attempts=None)', 2)],
        "tests/test_rater_management_pacing.py",
    ),
    (
        "pre_send_refusal_not_reraised",
        "a pacer refusal falls through to reconcile_uncertain_submission, "
        "asking the provider about a request that was never made",
        [("oncotriage/evaluation/rater.py",
          "            if provider_resilience.is_pre_send_refusal(exc):\n"
          "                raise",
          "            if False:\n                raise", 1)],
        "tests/test_rater_management_pacing.py",
    ),
]

_caught = {}
_plant_problems = {}
for _name, _why, _edits, _guard in _MATRIX:
    _root, _probs = _make_copy(_name, _edits)
    _plant_problems[_name] = _probs
    if _probs:
        _caught[_name] = "<PLANT-FAILED>"
        continue
    _code, _tail = _run_guard(_root, _guard, _name)
    _caught[_name] = ("caught" if _code not in (0, 3)
                      else "PREFLIGHT-FAILED" if _code == 3 else "MISSED")
    print(f"        [{_name}] {_guard} exit={_code} -> {_caught[_name]}")
    if _caught[_name] == "MISSED":
        print(f"        [tail]\n{_tail}")

check("every plant was applied cleanly -- a plant that matched nothing is a "
      "PLANT-FAILED rather than a working safeguard reported as broken",
      {k: v for k, v in _plant_problems.items() if v}, {})

check("*** EVERY safeguard's absence is DETECTED by its own guarding test "
      "file, measured by running it against a copy with the safeguard removed "
      "rather than by reading the check ***",
      _caught, {name: "caught" for name, _w, _e, _g in _MATRIX})


# ===========================================================================
# SECTION 3 — WHAT THIS FILE LEAVES BEHIND
# ===========================================================================

section("3. What this file leaves behind")

_after = {rel: hashlib.sha256(open(_watched(rel), "rb").read()).hexdigest()
          for rel in _WATCHED_FILES}
check("*** every file this matrix plants into is BYTE-IDENTICAL afterwards: "
      "the copy is what was broken, never the tree ***", _after, _BEFORE)
check("NON-DEGENERACY: the watched set is more than one file and their digests "
      "are not all equal, so the row above is not one file compared with "
      "itself", (len(_BEFORE) >= 5, len(set(_BEFORE.values())) == len(_BEFORE)),
      (True, True))

shutil.rmtree(_TMP, ignore_errors=True)
check("the temp tree is removed", os.path.exists(_TMP), False)


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
Created on Fri Sep 12 2026

@author: ramyalsaffar
"""
