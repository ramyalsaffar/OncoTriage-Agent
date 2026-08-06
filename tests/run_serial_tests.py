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
tests from pass 20d-1 and storage_query_layer -- write nothing in the repository
and are safe in parallel and in any order. Adding them would make a fast, safe
suite wait behind a slow, serial one. Files 18 and 19 are also not here: they
need a live server and they cost money.

NEVER EDIT THE REPOSITORY WHILE THIS IS RUNNING. Two of the five below restore
from a copy taken at their own start, so an edit made to
cancer_code_registry.py or config.py mid-run is silently reverted when they
finish. That is not hypothetical: pass 20d-1 lost an edit to oncotriage/config.py
exactly this way and found it only by re-grepping afterwards.

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
"""

import argparse
import os
import subprocess
import sys
import time


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
