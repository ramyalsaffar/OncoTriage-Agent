# Batch Evaluation: Run pipeline on all patients
################################################

"""
Run this while the server is live in another terminal. POSTs one FHIR bundle
per patient to POST /match and reports success/error counts and timings.

NOTHING HAPPENS WHEN THIS FILE IS IMPORTED (pass 20f). Every executable
statement -- the production row-count guard, the temporary control database it
builds, the file selection, the batch loop and the closing verdict -- is inside
main(), behind `if __name__ == "__main__"`. Only the constants and the three
module-level helpers are bound at import, and none of them does anything.

    WHY THAT MATTERS HERE MORE THAN ANYWHERE ELSE IN THE PROJECT. Loading this
    file used to BE running it, and running it POSTs real bundles to a live
    server. Pass 20e's own entry-point probe loaded this file and File 18 in
    order to inspect them, and three live POSTs reached a running API
    container as a result; nothing was billed only because that container was
    missing its MeSH lookup files and died at Stage 1. Any tool that walks the
    tree and imports what it finds does the same thing: a load probe, a
    coverage run, a linter that imports to resolve names, an editor that
    executes a file on open. The guard is what stops that, and it is why the
    lazy path names (data_fhir_path, inferences_path) are imported INSIDE
    main() rather than at module scope -- `from X import name` is an attribute
    read, and on oncotriage.paths an attribute read resolves the sibling data
    tree. Same reasoning as Files 07, 09, 13 and 20.

THIS SCRIPT COSTS REAL MONEY WHEN IT IS RUN, and how much depends entirely on
how many patients it runs. Every POST runs the whole six-stage pipeline on the
server, including Stage 5, which is a live billed call to the matching model.
Measured from the rows this file and File 18 left in the production database on
2026-08-05: about $0.13 to $0.17 per patient. At the two patients the slice
below currently selects that is roughly $0.30; over the full corpus of ~22,000
it would be four figures. Read the slice before running it.

READ THE SLICE, LITERALLY. main() overwrites the file list with
`fhir_files[410:412]` under a comment reading "For testing purposes", so this
script runs TWO patients while its title, its "Found N patients" line and its
"Batch evaluation complete" summary all describe a full-corpus run. That is not
changed here -- it is reported as-is and left for the operator, because
widening it is a spending decision.

THE EXIT CODE IS A BEHAVIOUR CHANGE (pass 20g), AND IT IS STATED AS ONE.
------------------------------------------------------------------------
Until this pass the exit code was set by the production row-count verdict and
by nothing else. The batch loop counted errors, printed them in the summary --
`Errors: 2` -- and NOTHING READ THAT COUNT. So a run in which every POST came
back HTTP 500, or timed out, or could not connect at all, printed its errors and
then exited 0. Measured, not supposed: pass 20f ran this file against a server
returning 500 to both POSTs and the process exited 0.

    Two failed POSTs and two successful POSTs were the same exit code, so
    anything reading it -- a CI step, `make`, a shell `&&`, the harness item 20d
    will build -- saw a total failure as a pass.

So: a non-zero error_count now returns 1. That CHANGES THIS FILE'S CONTRACT with
any caller reading its exit code. There is no such caller in the repository
today (grepped: this file is named only in prose), which is what makes the
change cheap to make now and expensive to postpone -- the harness item 20d
builds would inherit the old contract.

AND AN EMPTY SELECTION IS ALSO A FAILURE, which is the same defect one step
earlier. `fhir_files[410:412]` on a corpus of fewer than 411 bundles is the
empty list; the loop then runs zero times, error_count stays 0, "Success: 0/0"
prints, and the old code exited 0. A run that POSTed nothing is not a run that
passed. It is recorded as a failure naming the glob pattern searched, the number
of bundles that matched it and the slice that emptied it -- the three facts
needed to tell "no corpus" from "corpus too small for this slice".

    NOT A WIDENING OF THE SLICE. The slice is untouched; what changed is that
    the file now says so out loud when the slice selects nothing.

THE ROW-COUNT VERDICT IS STILL LAST AND STILL OVERRIDES. The batch summary is
printed BEFORE the guard, for the reason File 18 records: the guard returns the
moment it finds the count moved, so a summary below it would be dropped by
exactly the runs that had two things wrong instead of one. The guard's own
verdict is still the last thing on the terminal and still returns 1 on its own
finding, whatever the batch did.

THE POST TIMEOUT IS A STAGE 5 BOUND. POST_TIMEOUT_SECONDS is 180, which is the
value this file has always passed to this endpoint; pass 20f only gave it a name
so the number is written once instead of three times, and gave File 18 the same
constant. One POST runs all six stages on the server and the fifth is a live
model call the server retries up to MAX_GPT4O_RETRIES (3) times, so the ceiling
has to cover a full pipeline run plus those retries rather than a single round
trip.

AND IT WRITES TO WHATEVER DATABASE THE SERVER IS POINTED AT. That is not this
script's decision to make: "17- FastAPI Server.py" calls log_inference with no
path, so it resolves to the production inferences.db, and the server is a
SEPARATE PROCESS started by the operator. So start the server like this:

    ONCOTRIAGE_INFERENCES_DB=/tmp/oncotriage-test.db python "17- FastAPI Server.py"

and the rows this run produces land there instead. If you do not, this script
detects it: it reads the production inference row count before and after the
run and fails, naming ONCOTRIAGE_INFERENCES_DB, if the count moved. See the
guard immediately below the bootstrap.
"""

# ===========================================================================
# IMPORTS (pass 20e — this file no longer exec's anything)
# ===========================================================================
# IT USED TO EXEC "01- Imports.py" AND "02- Utility Functions.py" INTO ITS OWN
# GLOBALS, and with "18- FastAPI Server Test.py" it was one of the last two
# files in the repository that did. Those two exec'd files are deleted by this
# pass; nothing else needed them.
#
# The free-name set was re-derived with symtable rather than taken from the
# comment that used to sit here, and the comment was INCOMPLETE: besides
# data_fhir_path, glob, json, requests, time and CaffeinateSession it also used
# os, shutil, sqlite3, tempfile, datetime, timezone and inferences_path, all of
# which File 01 happened to bind. Every one is imported explicitly now.
#
# WHAT IS LOST, stated rather than discovered: this process no longer imports
# torch, transformers, streamlit, langgraph and eighty more libraries in order
# to POST HTTP requests, and it no longer resolves the whole sibling data tree
# at import. `data_fhir_path` and `inferences_path` resolve on first READ
# instead (oncotriage/paths.py is lazy since pass 20c-2b) -- same resolver,
# same value, later.
import glob
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timezone

import requests


# Make the oncotriage package importable
#---------------------------------------
# The same six-line block every other entry point carries. `pip install -e .`
# makes it a no-op; without it the code directory is added to sys.path and the
# fact is printed rather than left silent. This replaces the sys.path work
# "01- Imports.py" used to do on this file's behalf.
try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals()
         else None, "__file__"),
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

from oncotriage.utils import CaffeinateSession  # noqa: E402

# NOTE: `from oncotriage.paths import data_fhir_path, inferences_path` is NOT
# here. It is the first statement of main(). oncotriage.paths has a PEP 562
# module __getattr__ and every path name resolves on first READ, so a
# from-import at module scope would glob the whole sibling data tree the moment
# anything imported this file -- which is one of the things the __main__ guard
# exists to prevent. oncotriage.utils has no such resolver: importing
# CaffeinateSession binds a class and touches nothing, so it stays here with the
# other imports.


#------------------------------------------------------------------------------


BASE_URL = "http://localhost:8000"

# See the docstring: 180 is a Stage 5 bound, it is the value this file has
# always used, and File 18 now carries the same constant for the same endpoint.
POST_TIMEOUT_SECONDS = 180


# ===========================================================================
# THE PRODUCTION DATABASE MUST NOT MOVE (pass 20c-3i)
# ===========================================================================
#
# WHAT WENT WRONG, MEASURED RATHER THAN SUPPOSED. "17- FastAPI Server.py" calls
# log_inference(result, patient_data) with NO db_path, so it resolves to
# oncotriage.paths.inferences_path -- the production database. This file and
# "18- FastAPI Server Test.py" POST real bundles to that live server, so every
# run of either has been writing real inference rows and their trial_matches
# children into the real inferences.db. Six such rows are in it, dated
# 2026-08-05, three runs of two patients each; they are the reason
# "16- Database Query.py" started dying at a different query, which is the only
# way this surfaced at all.
#
# WHY THIS FILE CANNOT FIX IT DIRECTLY. The writer is in another PROCESS,
# started by the operator, with its own environment. Nothing this script does to
# its own namespace, its own imports or its own arguments reaches it. The only
# channel that does is the server's environment, which is why pass 20c-3i added
# ONCOTRIAGE_INFERENCES_DB (oncotriage/settings.py) and had
# resolve_inference_db_path and resolve_drift_db_path both honour it.
#
# SO THIS FILE DETECTS INSTEAD OF PREVENTING. Same shape as
# "tests/test_monitoring_ecog_availability_drift.py"'s _production_drift_rows(): count
# before, count after, fail if it moved. Detection is weaker than prevention and
# it is what is available from here.
#
# DUPLICATED VERBATIM FROM FILE 18 ON PURPOSE. These two scripts have no shared
# module and are not given one here -- item 20d converts them, and a
# self-contained harness belongs in that pass rather than in a helper module
# invented now and unwound then.
#
# READ-ONLY CONNECTION. mode=ro means the guard cannot itself create the
# database it is asserting about: a plain sqlite3.connect() on an absent path
# CREATES an empty file, so a machine with no inferences.db would have this
# check bring one into existence, count 0, count 0 again and report success.
#
# THE PRODUCTION PATH IS AN ARGUMENT, NOT A MODULE GLOBAL (pass 20f). It used to
# be `_PRODUCTION_DB = inferences_path` at module scope, which both resolved a
# lazy path at import and made _production_schema() read a global. main()
# resolves it once and hands it down, so these three helpers are pure functions
# of what they are given and the module binds nothing that had to be resolved.


def _inference_rows(db_path):
    """Rows in db_path's inferences table, or None if it cannot be read.

    None is a real answer and the caller must treat it as one: an unreadable
    database makes the before/after comparison vacuous (None == None passes),
    which is why rows_before is asserted non-degenerate in main().
    """
    try:
        _c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return _c.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
        finally:
            _c.close()
    except sqlite3.Error:
        return None


def _production_schema(db_path):
    """The CREATE TABLE text for db_path's `inferences` table, or None."""
    try:
        _c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            _row = _c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='inferences'").fetchone()
            return _row[0] if _row else None
        finally:
            _c.close()
    except sqlite3.Error:
        return None


def _rowcount_control_fires(production_db):
    """True once the row counter has been SHOWN to notice an inserted row.

    NEGATIVE CONTROL: the comparison must be able to come out False.

    An "unchanged" verdict from a counter that always returns the same number is
    not evidence of anything. So before trusting it, run it against a database
    where a row IS inserted and require it to say so.

    The control database carries the PRODUCTION schema, read out of the real
    sqlite_master rather than retyped, so the counter is exercised against the
    same table definition it will meet for real. It is not a byte copy of the
    86 MB production file: copying that on every run would cost seconds and disk
    for no additional coverage -- the count query reads the table, and the table
    is what is reproduced here. What a byte copy would add is confidence that the
    COUNT is correct on a populated table, so the control seeds two rows first
    and checks the count reads 2 before the third is added.

    THE TEMPORARY DIRECTORY IS REMOVED IN A `finally` (pass 20f). It used to be
    removed by two separate calls on two separate paths, and anything raising
    between the mkdtemp and the second of them leaked it. The mkdtemp also now
    happens AFTER the schema read rather than before it, so the unreadable-schema
    path creates nothing at all instead of creating a directory and deleting it.
    """
    schema = _production_schema(production_db)

    if schema is None:
        print("\n✗ FAIL: the production inferences table could not be read, so "
              "the row-count guard below cannot be shown to work.")
        print(f"  Database: {production_db}")
        return False

    control_dir = tempfile.mkdtemp(prefix="oncotriage-rowguard-")
    try:
        control_db = os.path.join(control_dir, "control.db")

        _cc = sqlite3.connect(control_db)
        _cc.execute(schema)
        for _i in range(2):
            _cc.execute(
                "INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
                (f"control-{_i}", datetime.now(timezone.utc).isoformat()))
        _cc.commit()
        _cc.close()

        control_before = _inference_rows(control_db)

        _cc = sqlite3.connect(control_db)
        _cc.execute(
            "INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
            ("control-inserted", datetime.now(timezone.utc).isoformat()))
        _cc.commit()
        _cc.close()

        control_after = _inference_rows(control_db)
    finally:
        shutil.rmtree(control_dir, ignore_errors=True)

    if not (control_before == 2 and control_after == 3
            and control_before != control_after):
        print("\n✗ FAIL: the row-count guard does not discriminate. Against a "
              "database built from the production schema with one row inserted "
              f"it reported before={control_before!r} after={control_after!r}; "
              "it must report 2 then 3. The 'production database unchanged' "
              "verdict at the end of this run would be meaningless, so the run "
              "is stopped here rather than printing it.")
        return False

    print(f"[Guard] Row-count control fired: {control_before} -> "
          f"{control_after} on a database carrying the production inferences "
          f"schema.")
    return True


#------------------------------------------------------------------------------


def main():
    """Run the batch evaluation inside the production-database guard.

    Returns the process exit code: 0 only when the production inference row
    count is unchanged AND every selected patient came back HTTP 200 AND the
    selection was non-empty. See the docstring: the last two conjuncts are new
    in pass 20g and they are a behaviour change.

    ORDER IS LOAD-BEARING and it is the order the file has always had. The
    row-count control fires, then the BEFORE count is read, then the first POST
    goes out; the AFTER count is read once the batch loop has finished and the
    verdict is the last thing printed. Reading the count after the first POST
    would measure nothing.
    """
    # Lazy paths, resolved here rather than at module scope -- see the note
    # above the constants.
    from oncotriage.paths import data_fhir_path, inferences_path

    production_db = inferences_path

    if not _rowcount_control_fires(production_db):
        return 1

    rows_before = _inference_rows(production_db)

    if rows_before is None:
        print(f"\n✗ FAIL: cannot read the production inferences table at "
              f"{production_db}, so 'unchanged' would compare None against None "
              f"and pass whatever this run does.")
        return 1

    print(f"[Guard] Production inference rows before this run: {rows_before}")
    print(f"[Guard] Production database: {production_db}")
    print("[Guard] If the server was NOT started with ONCOTRIAGE_INFERENCES_DB "
          "set, this run will write into that file and this script will fail at "
          "the end.")

    # Get all FHIR files
    #-------------------
    # THE PATTERN IS BOUND TO A NAME because the empty-selection failure below
    # reports it. A message reading "no patients selected" without saying where
    # it looked sends the reader to grep this file for the glob; the two facts
    # belong together.
    fhir_pattern = data_fhir_path + "*.json"
    fhir_files = sorted(glob.glob(fhir_pattern))
    corpus_count = len(fhir_files)

    # For testing purposes
    #---------------------
    # TWO PATIENTS, NOT THE CORPUS. Left exactly as it was: widening this is a
    # spending decision and it is the operator's, not this pass's. See the
    # docstring.
    _SLICE = slice(410, 412)
    fhir_files = fhir_files[_SLICE]

    print(f"Found {len(fhir_files)} patients")
    print(f"Running batch evaluation...\n")

    # EVERY FAILED PATIENT, NAMED (pass 20g). error_count still drives the
    # printed summary line this file has always had; `failures` is what the exit
    # code reads, and it carries the bundle name and the reason so the summary
    # is a diagnosis rather than a number. The two cannot disagree: every
    # increment of one appends to the other, and main() asserts nothing about
    # them separately.
    failures = []

    # A SELECTION OF NOTHING IS A FAILURE, NOT A CLEAN RUN. Recorded rather than
    # returned on, so the row-count guard below still runs and still has the
    # last word -- an early return here would skip the one check that reports
    # whether the PREVIOUS run polluted the production database.
    if not fhir_files:
        print(f"✗ No patients selected, so nothing was POSTed and nothing was "
              f"tested.")
        print(f"    Searched:       {fhir_pattern}")
        print(f"    Bundles found:  {corpus_count}")
        print(f"    Slice applied:  [{_SLICE.start}:{_SLICE.stop}]")
        print(f"  A corpus of {corpus_count} bundles cannot fill a slice that "
              f"starts at {_SLICE.start}. Either the FHIR directory above is "
              f"empty or wrong, or the corpus is smaller than the slice this "
              f"file hard-codes.")
        failures.append(
            f"selection was empty: {corpus_count} bundle(s) matched "
            f"{fhir_pattern}, slice [{_SLICE.start}:{_SLICE.stop}] selected 0")

    success_count = 0
    error_count = 0
    start_time = time.time()

    with CaffeinateSession("FastAPI Server Batch Test"):

        for idx, fhir_file in enumerate(fhir_files, 1):
            patient_start = time.time()
            bundle_name = os.path.basename(fhir_file)

            try:
                with open(fhir_file) as f:
                    bundle = json.load(f)

                response = requests.post(
                    f"{BASE_URL}/match",
                    json={"fhir_bundle": bundle},
                    timeout=POST_TIMEOUT_SECONDS
                )

                if response.status_code == 200:
                    success_count += 1
                    patient_time = time.time() - patient_start
                    print(f"[{idx}/{len(fhir_files)}] Success ({patient_time:.1f}s)")
                else:
                    error_count += 1
                    print(f"[{idx}/{len(fhir_files)}] ERROR: HTTP {response.status_code}")
                    # The server's own body is the diagnosis, exactly as
                    # File 18's _json_or_report() prints it. Truncated because
                    # an HTML error page from a proxy is a real shape.
                    print(f"    Response body: {response.text[:500]}")
                    failures.append(
                        f"[{idx}/{len(fhir_files)}] {bundle_name}: "
                        f"HTTP {response.status_code}")

            except requests.exceptions.Timeout:
                error_count += 1
                print(f"[{idx}/{len(fhir_files)}] TIMEOUT (>{POST_TIMEOUT_SECONDS}s)")
                failures.append(
                    f"[{idx}/{len(fhir_files)}] {bundle_name}: timeout after "
                    f"{POST_TIMEOUT_SECONDS}s")
            except Exception as e:
                error_count += 1
                print(f"[{idx}/{len(fhir_files)}] ERROR: {e}")
                failures.append(
                    f"[{idx}/{len(fhir_files)}] {bundle_name}: "
                    f"{type(e).__name__}: {e}")

        elapsed = time.time() - start_time
        print(f"\n{'='*60}")
        print(f"Batch evaluation complete:")
        print(f"  Success: {success_count}/{len(fhir_files)}")
        print(f"  Errors: {error_count}")
        print(f"  Total time: {elapsed/60:.1f} minutes")
        if len(fhir_files) > 0:
            print(f"  Avg time/patient: {elapsed/len(fhir_files):.1f}s")
        print(f"{'='*60}")

    # =======================================================================
    # THE BATCH'S OWN VERDICT -- printed BEFORE the database guard (pass 20g)
    # =======================================================================
    #
    # ORDER, FOR THE REASON FILE 18 RECORDS. The guard below returns 1 the
    # moment it finds the row count moved, so a summary printed after it would
    # be dropped by exactly the runs that had two things wrong instead of one.
    # Printing it first costs the guard nothing: the guard's verdict is still
    # the last thing on the terminal, which is what a reader looks for.

    if failures:
        print("=" * 60)
        print(f"✗ {len(failures)} check(s) failed:")
        for _f in failures:
            print(f"    {_f}")
        print("  Each is diagnosed above: a non-200 prints the server's own "
              "response")
        print("  body, an empty selection prints the pattern it searched.")
        print("=" * 60)
        print("\n")

    # =======================================================================
    # THE PRODUCTION DATABASE MUST NOT MOVE -- the verdict
    # =======================================================================
    #
    # Read again and compare. A change means the server this run talked to was
    # writing to the production database, which is the defect described at the
    # top of this file: real test traffic accumulating in the table every later
    # analysis, query and drift baseline is computed over.
    #
    # OUTSIDE the CaffeinateSession above, deliberately. A SystemExit raised
    # inside the `with` would still run __exit__ and release the sleep
    # assertion, so correctness is not the reason -- readability is: the guard
    # has nothing to do with keeping the machine awake, and running it after the
    # block keeps the failure message the last thing on the terminal rather than
    # interleaved with the caffeinate teardown.
    #
    # The exit code is non-zero on a change. That is what makes this a check
    # rather than a note: this file is run by hand today, and item 20d will run
    # it from a harness that reads exit codes.

    rows_after = _inference_rows(production_db)

    print("=" * 60)
    print("Production database guard")
    print("=" * 60)
    print(f"  Database: {production_db}")
    print(f"  Rows before: {rows_before}")
    print(f"  Rows after:  {rows_after}")

    if rows_after is None:
        print("\n✗ FAIL: the production inferences table became unreadable "
              "during this run. That is not 'unchanged'; it is unknown.")
        return 1

    if rows_after != rows_before:
        print(f"\n✗ FAIL: this run wrote {rows_after - rows_before} row(s) into "
              f"the PRODUCTION inference database.")
        print("\n  The server was not redirected. Restart it with:")
        print("      ONCOTRIAGE_INFERENCES_DB=/tmp/oncotriage-test.db \\")
        print('          python "17- FastAPI Server.py"')
        print("\n  ONCOTRIAGE_INFERENCES_DB is read by "
              "oncotriage/settings.py:resolve_inferences_db() and honoured by "
              "both resolve_inference_db_path (storage/database_logger.py) and "
              "resolve_drift_db_path (monitoring/drift.py).")
        print("\n  The rows are NOT removed by this script. Deciding what to do "
              "with them is the operator's call; File 16's cost and model "
              "breakdowns are where to look at them first.")
        return 1

    print("\n✓ The production inference database was not written to by this "
          "run.")
    print("=" * 60)
    print("\n")

    # Non-zero when a POST failed or when nothing was selected. Before pass 20g
    # this was a bare `return 0`, so error_count was printed and never read; see
    # the docstring for why that is a behaviour change rather than a bug fix.
    # The summary itself was printed above, before the guard.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 22:11:01 2026

@author: ramyalsaffar
"""
