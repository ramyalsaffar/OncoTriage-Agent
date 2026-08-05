# Serial Test Runner
####################

"""Runs the four source-mutating tests IN ORDER, one at a time.

WHY THIS FILE EXISTS
--------------------
Files 42, 43, 44 and 47 cannot run concurrently, and until pass 20c-3b that fact
lived only as a warning paragraph in CLAUDE.md. A warning is not a mechanism: it
is followed by whoever read it, and by nobody else -- including a CI job, a
`for f in *.py` loop, or a person running two terminals.

WHAT ACTUALLY COLLIDES, pair by pair. Every one of these is a real-looking
failure with no defect behind it, which is the worst kind to debug:

  44 x 47   "44- Snapshot Date Rot Test.py" rewrites the DATA_SNAPSHOT_DATE
            literal in oncotriage/config.py IN PLACE (hashing before and after,
            restoring byte-identically). "47- Package Split Test.py" check 4
            COPIES that file to build its own rewrite. Run together, 47 copies a
            config that 44 has patched and fails on `the copied config carries
            the snapshot-date assignment to rewrite`.

  43 x 42   "43- Cancer Code Registry Audit Negative Control.py" plants defects
            into oncotriage/registries/cancer_code_registry.py IN PLACE.
            "42- Cancer Code Registry Audit Test.py" reads that same SOURCE TEXT
            and extracts the inline comment beside every code as the claim under
            audit. Run together, 42 audits planted defects and reports them as
            real ones.

  43 x 47   47 copytree()s the whole package in three separate checks (the cycle
            negative control, the BM25 plant, the snapshot-date rewrite). A copy
            taken while 43 has a defect planted carries the defect.

  44 x 42/43  same shape: 44's window of a patched config.py overlaps anything
            that reads or copies the package.

So the safe order is: everything that mutates, one at a time, each restoring
before the next starts. That is what this file enforces.

WHY NOT JUST RUN THEM IN ANY ORDER SERIALLY. The order below is not arbitrary.
42 runs FIRST, against a pristine registry, so its audit is the baseline. 43
then plants and restores. 44 patches and restores config.py. 47 runs LAST, over
a tree that every earlier file has put back, so a 47 failure means 47 found
something rather than that it caught a neighbour mid-edit.

WHAT THIS DOES NOT DO. It does not run the other tests (30-41, 45, 46). Those
mutate nothing and are safe in parallel or in any order; adding them here would
make a fast, safe suite wait behind a slow, serial one.

EVERY EXIT CODE IS REPORTED, and the run does not stop at the first failure --
each of the four leaves its own tree in the state it found it, so a failure in
one does not make the next meaningless. The process exits non-zero if any of
them did.

Run from terminal:
    python run_serial_tests.py            # all four, in order
    python run_serial_tests.py --list     # print the order and exit
    make serial-tests                     # the same thing through the Makefile

Exit codes:
    0 -- every test exited 0
    1 -- at least one test exited non-zero
    2 -- a test file is missing from the code directory
"""

import argparse
import os
import subprocess
import sys
import time


# Item 20a: this file sits in the code directory, so __file__ locates it with no
# hardcoded path. Unlike the numbered files it is importable, so it takes the
# simple form rather than the bootstrap block.
_CODE_DIR = os.path.dirname(os.path.abspath(__file__))


# The order is load-bearing -- see the module docstring.
SERIAL_TESTS = (
    ("42- Cancer Code Registry Audit Test.py",
     "audits the registry's inline claims; must see a PRISTINE source"),
    ("43- Cancer Code Registry Audit Negative Control.py",
     "plants defects into the registry IN PLACE and hashes the restore"),
    ("44- Snapshot Date Rot Test.py",
     "rewrites DATA_SNAPSHOT_DATE in oncotriage/config.py IN PLACE"),
    ("47- Package Split Test.py",
     "copytree()s the package three times; must copy a RESTORED tree"),
)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Run the four source-mutating tests serially, in order.")
    parser.add_argument("--list", action="store_true",
                        help="print the order and why, then exit")
    args = parser.parse_args(argv)

    if args.list:
        print("Serial order (each mutates the source tree and restores it):")
        for i, (name, why) in enumerate(SERIAL_TESTS, start=1):
            print(f"  {i}. {name}\n       {why}")
        return 0

    missing = [n for n, _ in SERIAL_TESTS
               if not os.path.isfile(os.path.join(_CODE_DIR, n))]
    if missing:
        print("[Serial] MISSING test file(s) in the code directory:")
        for name in missing:
            print(f"  - {name}")
        return 2

    print("=" * 78)
    print("SERIAL TEST RUN — four source-mutating tests, one at a time")
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
