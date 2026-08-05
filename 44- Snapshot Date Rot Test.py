# Snapshot Date Rot Test
########################

"""
Proves that Files 38 and 39 survive a change to DATA_SNAPSHOT_DATE.

WHY THIS EXISTS
---------------
DATA_SNAPSHOT_DATE (03- Config.py) is the date patient ages and ECOG reference
windows are computed against, and it MOVES: it is updated to the generation
date every time the corpus is regenerated. Twice now, a test has pinned a value
derived from it and gone red on data that was entirely correct:

    File 38  _DEMOGRAPHIC_CASES hardcoded ages of 60, 59, 59, 60, and a
             birthDate literal of "2030-01-01" chosen to sit after the
             reference. Correct at DATA_SNAPSHOT_DATE = 2026-03-11; four
             assertions failed the moment item 18b moved it to 2026-08-03.

    File 39  the _after fixture was dated "2026-05-14", chosen to sit after the
             reference. The same move turned it into a BEFORE observation and
             eight assertions failed.

Both were fixed by deriving from get_age_reference_date() instead of pinning.
Nothing stopped them being reintroduced, and a suite that goes red on every
legitimate regeneration is a suite people learn to ignore -- which is worse
than no suite, because it still reports a number.

This file is the guard. It sets DATA_SNAPSHOT_DATE to several values, one past
2030, runs both suites at each, and fails if any run is non-zero.

WHAT IT DOES NOT PROVE
----------------------
Only that these two suites tolerate the dates listed in _SNAPSHOT_DATES. Every
date here is at or after the corpus generation date, because that is the only
direction DATA_SNAPSHOT_DATE moves in practice -- File 39 still carries fixture
observations dated 2019-2024 that are "before the reference" by assumption, and
a snapshot date earlier than those would legitimately fail.

SAFETY
------
File 03 is edited in place, so:
  - it is copied aside first and restored in a finally block, so an exception
    cannot leave it edited;
  - the restore is verified by sha256 after every date and again at the end;
  - a failed restore aborts immediately rather than editing further.

A BASELINE RUN COMES FIRST. If File 38 or 39 cannot run at all -- a syntax
error, a missing scratch corpus, a broken chain -- every date would report a
non-zero exit and the failure would look like date rot rather than a broken
suite. Both must pass unmodified before any date is set.

NOTE: File 39 parses the scratch ECOG corpus, so this test inherits that
dependency. It takes a few minutes.

Run from terminal (or F5 in Spyder):
    python "44- Snapshot Date Rot Test.py"

Exit codes:
    0 -- both suites passed at every date, File 03 restored byte-for-byte
    1 -- a run failed, the baseline failed, or File 03 could not be restored
"""


# Run needed file
#----------------
# 01 and 02 only. This file does not use File 03's values -- it rewrites File 03
# as TEXT and runs Files 38 and 39 in subprocesses, so each picks up the patched
# constant from disk. Chaining 03 here would bind DATA_SNAPSHOT_DATE into THIS
# process, where it has no effect on the subprocesses and would only mislead.
# Item 20a: this file sits in the code directory, so __file__ locates it with
# no hardcoded path. __file__ is bound when the file is run as a script (every
# documented entry point for it) and when Spyder runfile()s it. In a bare
# interactive paste it is not bound, and the working directory is the only
# remaining candidate -- taken, but announced, never silently.
import os as _os_boot
if "__file__" in globals():
    _code_dir = _os_boot.path.dirname(_os_boot.path.abspath(__file__)) + _os_boot.sep
else:
    _code_dir = _os_boot.getcwd() + _os_boot.sep
    print(f"[Bootstrap] __file__ unbound; using the working directory as the code directory: {_code_dir}")
del _os_boot

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())


#------------------------------------------------------------------------------


# ===========================================================================
# CONFIGURATION
# ===========================================================================

_FILE_03 = _code_dir + "03- Config.py"

_SUITES = [
    "38- Birth Date and Demographics Parser Test.py",
    "39- ECOG Performance Status Surfacing Test.py",
]

# The dates under test. Each must be at or after the corpus generation date --
# see "WHAT IT DOES NOT PROVE" above.
#
#   2026-03-11  the pre-18b value. Every literal the two suites used to carry
#               was written to be correct at exactly this date, so a
#               reintroduced literal is most likely to pass here and fail
#               elsewhere. Keeping it makes that asymmetry visible.
#   2027-12-31  a year boundary, and a date where "has the birthday happened
#               yet this year" flips relative to the mid-year anchors
#               parse_partial_date() imputes.
#   2031-07-04  past 2030, which is what the old "2030-01-01" after-reference
#               literal in File 38 silently depended on being in the future.
_SNAPSHOT_DATES = ["2026-03-11", "2027-12-31", "2031-07-04"]

# The assignment this test rewrites. Matched as a regex so the surrounding
# comment block -- which is long, and explains why the date is what it is --
# is left untouched.
_DATE_ASSIGNMENT = r'DATA_SNAPSHOT_DATE = "[\d-]{8,10}"'


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================
# Same shape as Files 33, 42 and 43: record every outcome, never abort on the
# first failure, exit non-zero at the end.

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")


def fail(label: str, detail: str) -> None:
    """Record an outright failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_suite(filename: str):
    """
    Run one suite as a subprocess.

    A subprocess, not an exec, because the suite must read DATA_SNAPSHOT_DATE
    from the patched File 03 on disk. Running it in this process would bind
    whatever this process already loaded.

    Returns:
        tuple: (returncode, passed, failed, failing_labels)
    """
    proc = subprocess.run(
        [sys.executable, filename],
        capture_output=True, text=True, cwd=_code_dir,
    )
    out = proc.stdout + proc.stderr
    passed = re.search(r"^Passed: (\d+)", out, re.M)
    failed = re.search(r"^Failed: (\d+)", out, re.M)
    labels = [line.strip()[len("FAIL "):].strip()
              for line in out.splitlines() if line.strip().startswith("FAIL ")]
    return (proc.returncode,
            int(passed.group(1)) if passed else None,
            int(failed.group(1)) if failed else None,
            labels)


#------------------------------------------------------------------------------


# ===========================================================================
# THE TEST
# ===========================================================================

print()
print("=" * 78)
print("SNAPSHOT DATE ROT TEST — Files 38 and 39 must pass at every date")
print("=" * 78)

_PRISTINE_SHA = _sha256(_FILE_03)
_BACKUP = os.path.join(tempfile.mkdtemp(prefix="oncotriage_rot_"), "03_pristine.py")
shutil.copy2(_FILE_03, _BACKUP)

_COMMITTED_DATE = re.search(_DATE_ASSIGNMENT, open(_BACKUP, encoding="utf-8").read())
print(f"  File 03:            {os.path.basename(_FILE_03)}")
print(f"  sha256 before:      {_PRISTINE_SHA}")
print(f"  committed value:    {_COMMITTED_DATE.group(0) if _COMMITTED_DATE else 'NOT FOUND'}")
print(f"  dates under test:   {', '.join(_SNAPSHOT_DATES)}")
print(f"  suites:             {', '.join(s[:2] for s in _SUITES)}")
print()

_ROWS = []

try:
    # The assignment has to be findable before anything is rewritten.
    check("File 03 carries a DATA_SNAPSHOT_DATE assignment this test can rewrite",
          _COMMITTED_DATE is not None, True)

    if _COMMITTED_DATE is None:
        fail("the date assignment is locatable",
             f"no match for {_DATE_ASSIGNMENT!r} in File 03; the constant has "
             f"been renamed or reformatted and this test is no longer able to "
             f"set it")
    else:
        # -- Baseline. Without it, a suite that cannot run at all would report
        #    a non-zero exit at every date and read as date rot.
        print("  Baseline: running both suites unmodified...", flush=True)
        _baseline_ok = True
        for _suite in _SUITES:
            _rc, _p, _f, _labels = _run_suite(_suite)
            check(f"baseline: {_suite[:2]} passes unmodified", _rc, 0)
            _baseline_ok &= (_rc == 0)
            print(f"    {_suite[:2]}  exit={_rc}  passed={_p}  failed={_f}", flush=True)
            if _rc != 0:
                fail(f"baseline for {_suite[:2]} is usable",
                     f"exited {_rc} with File 03 UNMODIFIED, so a non-zero exit "
                     f"at any date proves nothing. First failures: {_labels[:3]}")

        if not _baseline_ok:
            print("  BASELINE FAILED — the dates below cannot be interpreted.")
        else:
            print("  Baseline OK.")
            print()

            for _date in _SNAPSHOT_DATES:
                _source = open(_BACKUP, encoding="utf-8").read()
                _patched, _n = re.subn(_DATE_ASSIGNMENT,
                                       f'DATA_SNAPSHOT_DATE = "{_date}"',
                                       _source, count=1)
                if _n != 1:
                    fail(f"DATA_SNAPSHOT_DATE set to {_date}",
                         f"expected exactly 1 substitution, made {_n}")
                    continue
                with open(_FILE_03, "w", encoding="utf-8") as _fh:
                    _fh.write(_patched)

                for _suite in _SUITES:
                    _rc, _p, _f, _labels = _run_suite(_suite)
                    check(f"{_suite[:2]} passes at DATA_SNAPSHOT_DATE = {_date}", _rc, 0)
                    _ROWS.append((_date, _suite[:2], _rc, _p, _f, _labels))
                    print(f"  {_date}  {_suite[:2]}  exit={_rc}  "
                          f"passed={_p}  failed={_f}", flush=True)

                # Restore and verify BEFORE the next date, so a failed restore
                # can never compound into a second edit.
                shutil.copy2(_BACKUP, _FILE_03)
                _restored = _sha256(_FILE_03)
                if _restored != _PRISTINE_SHA:
                    fail("File 03 restored after each date",
                         f"after {_date} the restore produced {_restored}, "
                         f"expected {_PRISTINE_SHA}. ABORTING before editing more.")
                    break

finally:
    # Unconditional: an exception anywhere above must not leave File 03 edited.
    shutil.copy2(_BACKUP, _FILE_03)
    _FINAL_SHA = _sha256(_FILE_03)
    _FINAL_DATE = re.search(_DATE_ASSIGNMENT, open(_FILE_03, encoding="utf-8").read())
    shutil.rmtree(os.path.dirname(_BACKUP), ignore_errors=True)


# ===========================================================================
# REPORT
# ===========================================================================

if _ROWS:
    print()
    print("-" * 78)
    print("Per-run detail")
    print("-" * 78)
    print(f"  {'snapshot date':14s} {'suite':6s} {'exit':5s} {'passed':7s} {'failed':7s}")
    for _date, _suite, _rc, _p, _f, _labels in _ROWS:
        print(f"  {_date:14s} {_suite:6s} {_rc:<5d} {str(_p):7s} {str(_f):7s}")
        for _l in _labels[:5]:
            print(f"        FAIL: {_l}")

check("File 03 is byte-identical to how it started", _FINAL_SHA, _PRISTINE_SHA)

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Dates tested:  {len(_SNAPSHOT_DATES)}  ({', '.join(_SNAPSHOT_DATES)})")
print(f"Runs:          {len(_ROWS)}")
print(f"Runs exit 0:   {sum(1 for r in _ROWS if r[2] == 0)}")
print(f"File 03 sha256 before: {_PRISTINE_SHA}")
print(f"File 03 sha256 after:  {_FINAL_SHA}")
print(f"Restored byte-identical: {_FINAL_SHA == _PRISTINE_SHA}")
print(f"DATA_SNAPSHOT_DATE now: {_FINAL_DATE.group(0) if _FINAL_DATE else 'NOT FOUND'}")
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

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
Created on Mon Aug  3 23:55:00 2026

@author: ramyalsaffar
"""
