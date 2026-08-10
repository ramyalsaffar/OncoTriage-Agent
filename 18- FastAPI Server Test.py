# Test OncoMatch Agent FastAPI Server
######################################

"""
Run this while the server is live in another terminal.
Tests all 4 endpoints.

NOTHING HAPPENS WHEN THIS FILE IS IMPORTED (pass 20f). Every executable
statement -- the production row-count guard, the temporary control database it
builds, the four tests and the closing verdict -- is inside main(), behind
`if __name__ == "__main__"`. Only the constants and the four module-level
helpers are bound at import, and none of them does anything.

    WHY THAT MATTERS HERE MORE THAN ANYWHERE ELSE IN THE PROJECT. Loading this
    file used to BE running it, and running it POSTs real bundles to a live
    server. Pass 20e's own entry-point probe loaded this file and File 19 in
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

THIS SCRIPT COSTS REAL MONEY WHEN IT IS RUN. Two of the four tests POST a real
FHIR bundle to POST /match and POST /match/file, and the server runs the whole
six-stage pipeline for each -- including Stage 5, which is a live billed call to
the matching model. Measured against the six rows this file and File 19 left in
the production database on 2026-08-05: about $0.13 to $0.17 per patient, so
roughly $0.30 for one full run of this file. Nothing here is stubbed and nothing
is replayed; "fixture_replay.py" is the file that costs nothing.

BOTH POSTS CARRY A TIMEOUT, AND THE VALUE IS NOT ARBITRARY. POST_TIMEOUT_SECONDS
is 180, the same value "19- FastAPI Server Batch Test.py" has always passed to
the same endpoint, and it is a STAGE 5 BOUND: one POST runs all six stages on
the server and the fifth is a live model call that the server itself retries up
to MAX_LLM_CLASSIFIER_RETRIES (3) times, so the ceiling has to cover a full pipeline run
plus those retries rather than a single round trip. Before pass 20f these two
POSTs had no timeout at all, so a server that accepted the connection and then
hung left this script waiting forever with no output and no verdict. The two
GETs carry the shorter GET_TIMEOUT_SECONDS: /health touches nothing and
/pipeline/info makes one Qdrant metadata call, so neither has any reason to
approach the POST bound.

A NON-200 IS REPORTED, NOT RAISED, ON EVERY ONE OF THE FOUR CALL SITES. Each
response goes through _json_or_report(), which prints the status, prints the
server's own error body on any non-200, and returns None so the caller skips
result parsing. Before pass 20f Tests 3 and 4 called r.json() and indexed
result['processing_time_seconds'] BEFORE looking at the status code, so a 500 --
which is exactly what pass 20e's accidental POSTs got back -- produced a KeyError
with no sight of the server's error message. Tests 1 and 2 had the same shape
one call earlier.

    AND A REPORTED FAILURE STILL EXITS NON-ZERO. That is deliberate and it is
    forced by the fix above: the old KeyError was a crash, and a crash is exit
    1, so turning it into a tidy printed line and exiting 0 would have made a
    500 from the server read as a clean run to anything reading exit codes.
    Every check that does not come back 200 is recorded and named in a summary
    printed just BEFORE the guard verdict -- before, because the guard returns
    the moment it finds the row count moved, so a summary below it would be
    dropped by exactly the runs that had two things wrong instead of one -- and
    main() returns 1.

A SKIPPED TEST IS NOT A PASSED TEST (pass 20g). Two of the four tests sat behind
a corpus precondition and neither precondition affected the outcome:

    Test 3   `if fhir_files:`          else printed "No FHIR files found."
    Test 4   `if len(fhir_files) > 1:` else printed "Need at least 2 FHIR files
                                       to test both endpoints."

Both messages were true and neither was read. A run against an empty FHIR
directory therefore printed two notes, POSTed nothing, exercised neither of the
two endpoints this file exists to exercise, and exited 0 -- indistinguishable,
to anything reading the exit code, from a run in which both answered correctly.
That is not hypothetical here: a container whose data volume has just been
recreated has exactly this directory, and pass 20g's own Docker rebuild produced
it.

Each branch now records a failure, which routes it into the same summary a
non-200 uses and the same `return 1`. Each message names WHAT was missing and
WHICH pattern was searched, because "no FHIR files found" alone does not
distinguish an empty directory from a wrong one from an unmounted one, and
because the two conditions differ: Test 3 needs one bundle, Test 4 needs a
second so the two endpoints do not run the same patient.

    THE ROW-COUNT VERDICT IS STILL LAST AND STILL OVERRIDES. Nothing about the
    ordering changed: the skips join `failures`, `failures` prints above the
    guard, the guard prints last and returns 1 on its own finding whatever the
    four tests did.

AND IT WRITES TO WHATEVER DATABASE THE SERVER IS POINTED AT. That is not this
script's decision to make: "17- FastAPI Server.py" calls log_inference with no
path, so it resolves to the production inferences.db, and the server is a
SEPARATE PROCESS started by the operator. So start the server like this:

    ONCOTRIAGE_INFERENCES_DB=/tmp/oncotriage-test.db python "17- FastAPI Server.py"

and the rows this run produces land there instead. If you do not, this script
detects it: it reads the production inference row count before and after the
run and fails, naming ONCOTRIAGE_INFERENCES_DB, if the count moved. See the
guard immediately below.
"""

# ===========================================================================
# IMPORTS (pass 20e — this file no longer exec's anything)
# ===========================================================================
# IT USED TO EXEC "01- Imports.py" INTO ITS OWN GLOBALS, and it was one of the
# last two files in the repository that did. Its whole reason was four free
# names -- data_fhir_path, glob, json and requests -- three of which are
# standard library or third party and one of which is a package attribute.
#
# The free-name set was re-derived with symtable before the change, not taken
# from the comment that used to sit here, and the comment was WRONG: the guard
# below also uses os, shutil, sqlite3, tempfile, datetime, timezone and
# inferences_path. Under the exec chain that was invisible, because File 01
# bound all of them. Every one is imported explicitly now.
#
# WHAT IS LOST BY NOT EXEC'ING FILE 01, stated rather than discovered: this
# process no longer imports torch, transformers, streamlit, langgraph,
# matplotlib and eighty more libraries in order to POST two HTTP requests, and
# it no longer resolves the whole sibling data tree at import. `data_fhir_path`
# and `inferences_path` resolve on first READ instead (oncotriage/paths.py is
# lazy since pass 20c-2b), which is the same value from the same resolver.
#
# `traceback` IS NEW IN PASS 20f. main() wraps the four tests so that an
# exception cannot skip the closing database verdict -- see the comment there --
# and a recorded exception that threw its traceback away would be a worse
# diagnosis than the crash it replaces.
import glob
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import traceback
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

# NOTE: `from oncotriage.paths import data_fhir_path, inferences_path` is NOT
# here. It is the first statement of main(). oncotriage.paths has a PEP 562
# module __getattr__ and every path name resolves on first READ, so a
# from-import at module scope would glob the whole sibling data tree the moment
# anything imported this file -- which is one of the things the __main__ guard
# exists to prevent. Files 07, 09, 13 and 20 do the same for the same reason.


#------------------------------------------------------------------------------


BASE_URL = "http://localhost:8000"

# See the docstring: 180 is a Stage 5 bound and it is File 19's existing value
# for the same endpoint, so the two files agree rather than each guessing. The
# GET bound is separate because neither GET runs the pipeline.
POST_TIMEOUT_SECONDS = 180
GET_TIMEOUT_SECONDS = 30


# ===========================================================================
# THE PRODUCTION DATABASE MUST NOT MOVE (pass 20c-3i)
# ===========================================================================
#
# WHAT WENT WRONG, MEASURED RATHER THAN SUPPOSED. "17- FastAPI Server.py" calls
# log_inference(result, patient_data) with NO db_path, so it resolves to
# oncotriage.paths.inferences_path -- the production database. This file and
# "19- FastAPI Server Batch Test.py" POST real bundles to that live server, so
# every run of either has been writing real inference rows and their
# trial_matches children into the real inferences.db. Six such rows are in it,
# dated 2026-08-05, three runs of two patients each; they are the reason
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
# DUPLICATED VERBATIM IN FILE 19 ON PURPOSE. These two scripts have no shared
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


# ===========================================================================
# ONE RESPONSE READER FOR ALL FOUR CALL SITES (pass 20f)
# ===========================================================================
#
# THE DEFECT THIS REMOVES. Tests 3 and 4 each called r.json() and then indexed
# result['processing_time_seconds'] BEFORE testing r.status_code. A 500 from the
# server -- which is what pass 20e's accidental POSTs got back, and what the
# Docker stack returns whenever the MeSH lookup files are missing -- therefore
# produced `KeyError: 'processing_time_seconds'` and a traceback, with the
# server's own error message never printed. Test 3 additionally printed the
# status twice and then fell through to result['patient_summary'] whatever the
# status had been, so its own status check could not stop it.
#
# Tests 1 and 2 had the identical shape one call earlier: `json.dumps(r.json())`
# with no status test at all. They are not what the task named, and they are
# fixed anyway, because "reports a non-200 instead of raising" is not a property
# of two of the four call sites.
#
# RETURNING None IS THE CONTRACT. There is no result to parse after a non-200,
# so the caller must skip its parsing rather than be handed a plausible-looking
# empty dict it would then index into.
def _json_or_report(response):
    """The decoded JSON body of a 200, or None with the failure printed.

    Prints the status once. On any non-200 it prints the server's own response
    text -- the whole point of the change: the error body is the diagnosis. On a
    200 whose body is not JSON it says so and prints the raw text, truncated,
    because an HTML error page from a proxy in front of the server is a real
    shape and dumping all of it helps nobody.
    """
    print(f"\nStatus: {response.status_code}")

    if response.status_code != 200:
        print("ERROR RESPONSE:")
        print(response.text)
        return None

    try:
        return response.json()
    except ValueError as exc:
        # requests.exceptions.JSONDecodeError subclasses ValueError, so this
        # catches both it and a bare json failure. Recorded, not swallowed:
        # the caller sees None and reports the test as failed.
        print(f"ERROR: the 200 response body did not decode as JSON ({exc}).")
        print("RAW RESPONSE (first 2000 chars):")
        print(response.text[:2000])
        return None


#------------------------------------------------------------------------------


def main():
    """Run the four endpoint tests inside the production-database guard.

    Returns the process exit code: 0 only when the production inference row
    count is unchanged AND all four tests RAN and came back 200. "ran" is new in
    pass 20g and it is the point of that pass: two of the four used to be
    skippable without affecting the outcome.

    ORDER IS LOAD-BEARING and it is the order the file has always had. The
    row-count control fires, then the BEFORE count is read, then the first
    request goes out; the AFTER count is read once the last test has finished
    and the verdict is the last thing printed. Reading the count after the first
    POST would measure nothing.
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

    # WHY THE TESTS ARE WRAPPED. The verdict below has to run even when a test
    # blows up, and pass 20f gave both POSTs a timeout, which creates exactly
    # that case: a server that accepts the request, processes it, writes its row
    # and answers after POST_TIMEOUT_SECONDS raises requests.Timeout here. An
    # unwrapped raise would take the process out before the AFTER count was ever
    # read, so the one run most likely to have written a production row would be
    # the one run that never checked. Recorded and re-reported, never swallowed:
    # the traceback is printed, the failure is named in the summary, and the
    # exit code is 1.
    failures = []

    try:
        # ------------------------------------------------------------------
        # Test 1: Health Check
        # ------------------------------------------------------------------

        print("=" * 60)
        print("Test 1: GET /health")
        print("=" * 60)

        r = requests.get(f"{BASE_URL}/health", timeout=GET_TIMEOUT_SECONDS)
        health = _json_or_report(r)
        if health is None:
            failures.append("Test 1: GET /health")
        else:
            print(json.dumps(health, indent=2))

        # ------------------------------------------------------------------
        # Test 2: Pipeline Info
        # ------------------------------------------------------------------

        print("\n" + "=" * 60)
        print("Test 2: GET /pipeline/info")
        print("=" * 60)

        r = requests.get(f"{BASE_URL}/pipeline/info",
                         timeout=GET_TIMEOUT_SECONDS)
        info = _json_or_report(r)
        if info is None:
            failures.append("Test 2: GET /pipeline/info")
        else:
            print(json.dumps(info, indent=2))

        # ------------------------------------------------------------------
        # Test 3: Match via JSON body (POST /match)
        # ------------------------------------------------------------------

        print("\n" + "=" * 60)
        print("Test 3: POST /match (JSON body)")
        print("=" * 60)

        # Grab first FHIR bundle from the data directory
        #----------------------------------------------
        # THE PATTERN IS BOUND TO A NAME because both skip messages below report
        # it. "No FHIR files found." without saying where it looked sends the
        # reader to grep this file for the glob; the two facts belong together.
        fhir_pattern = data_fhir_path + "*.json"
        fhir_files = sorted(glob.glob(fhir_pattern))

        if fhir_files:
            with open(fhir_files[0]) as f:
                bundle = json.load(f)

            print(f"Using: {fhir_files[0].split('/')[-1]}")

            r = requests.post(
                f"{BASE_URL}/match",
                json={"fhir_bundle": bundle},
                timeout=POST_TIMEOUT_SECONDS
            )

            result = _json_or_report(r)

            if result is None:
                failures.append("Test 3: POST /match")
            else:
                print(f"Processing time: {result['processing_time_seconds']}s")

                # Patient summary
                ps = result['patient_summary']
                print(f"\nPatient: {ps['patient_id']}")
                print(f"  Age: {ps['age']} | Sex: {ps['sex']}")
                print(f"  Conditions: {ps['condition_count']} | Medications: {ps['medication_count']}")

                # Pipeline summary
                res = result['result']
                print(f"\nPipeline:")
                print(f"  Retrieved:  {res.get('candidates_retrieved', 'N/A')}")
                print(f"  Re-ranked:  {res.get('candidates_reranked', 'N/A')}")
                print(f"  Filtered:   {res.get('candidates_filtered', 'N/A')}")
                print(f"  Evaluated:  {res.get('candidates_evaluated', 'N/A')}")
                print(f"  Eligible:   {len(res.get('matches', []))}")
                print(f"  Near-misses: {len(res.get('near_misses', []))}")
                print(f"  Not evaluable: {len(res.get('not_evaluable', []))}")

                # Show matches
                matches = res.get('matches', [])
                if matches:
                    print("\nTRIAL MATCHES:")
                    for i, m in enumerate(matches, 1):
                        print(f"\n  {i}. {m.get('nct_id', 'N/A')}")
                        print(f"     Title: {m.get('title', 'N/A')[:100]}")
                        print(f"     Phase: {m.get('phase', 'N/A')}")
                        print(f"     Match Score: {m.get('match_score', 'N/A')}")
                        print(f"     Eligible: {m.get('eligible', 'N/A')}")
                        print(f"     Explanation: {m.get('assessment', 'N/A')[:200]}")
                else:
                    print("\nNo matches found.")
                    print("\nFull result for inspection:")
                    print(json.dumps(res, indent=2))
        else:
            # A SKIPPED TEST IS NOT A PASSED TEST (pass 20g). This branch used
            # to print one line and fall through, so a run with no corpus at all
            # POSTed nothing, tested nothing and exited 0 -- identical, to
            # anything reading the exit code, to a run in which both endpoints
            # answered correctly. Recorded as a failure, which is what makes it
            # visible; the message names WHAT was missing and WHERE it looked,
            # because "no FHIR files found" alone does not say whether the
            # directory is empty, wrong, or unmounted.
            print("✗ SKIPPED: no FHIR bundle to send, so POST /match was never "
                  "exercised.")
            print(f"    Searched:      {fhir_pattern}")
            print(f"    Bundles found: 0")
            print("  data_fhir_path comes from oncotriage.paths and resolves "
                  "under ONCOTRIAGE_MAIN_PATH; inside a container it is the "
                  "fixed /app/data/patients/fhir/.")
            failures.append(
                f"Test 3: POST /match — SKIPPED, 0 bundles matched "
                f"{fhir_pattern}")

        # ------------------------------------------------------------------
        # Test 4: Match via file upload (POST /match/file)
        # ------------------------------------------------------------------

        print("\n" + "=" * 60)
        print("Test 4: POST /match/file (file upload)")
        print("=" * 60)

        # A SEPARATE CALL SITE, FIXED SEPARATELY. This is the upload path the
        # docstring names, and it had its own unchecked r.json(); the fix to
        # Test 3 does not reach it.
        if len(fhir_files) > 1:
            filepath = fhir_files[1]
            print(f"Using: {filepath.split('/')[-1]}")

            with open(filepath, 'rb') as f:
                r = requests.post(
                    f"{BASE_URL}/match/file",
                    files={"file": (filepath.split('/')[-1], f,
                                    "application/json")},
                    timeout=POST_TIMEOUT_SECONDS
                )

            result = _json_or_report(r)

            if result is None:
                failures.append("Test 4: POST /match/file")
            else:
                print(f"Processing time: {result['processing_time_seconds']}s")

                ps = result['patient_summary']
                print(f"\nPatient: {ps['patient_id']}")
                print(f"  Age: {ps['age']} | Sex: {ps['sex']}")

                res = result['result']
                matches = res.get('matches', [])
                print(f"\nMatches: {len(matches)}")

                if matches:
                    for i, m in enumerate(matches, 1):
                        print(f"\n  {i}. {m.get('nct_id', 'N/A')}")
                        print(f"     Title: {m.get('title', 'N/A')[:100]}")
                        print(f"     Phase: {m.get('phase', 'N/A')}")
                        print(f"     Match Score: {m.get('match_score', 'N/A')}")
                        print(f"     Eligible: {m.get('eligible', 'N/A')}")
                        print(f"     Explanation: {m.get('assessment', 'N/A')[:200]}")
                else:
                    print("No matches — printing full result:")
                    print(json.dumps(res, indent=2))
        else:
            # THE SECOND SILENT SKIP, and it is a separate condition from the
            # first: Test 3 needs one bundle, Test 4 needs a SECOND one, so a
            # corpus of exactly one bundle skipped only this test and still
            # exited 0. The count is reported because it is what distinguishes
            # the two cases -- 0 means no corpus, 1 means a corpus too small for
            # this file's "use a different bundle per endpoint" rule.
            print("✗ SKIPPED: POST /match/file was never exercised — it needs a "
                  "SECOND bundle, so that the two endpoints do not run the same "
                  "patient.")
            print(f"    Searched:      {fhir_pattern}")
            print(f"    Bundles found: {len(fhir_files)} (need 2)")
            failures.append(
                f"Test 4: POST /match/file — SKIPPED, needs 2 bundles and "
                f"{len(fhir_files)} matched {fhir_pattern}")

        print("\n" + "=" * 60)
        # "attempted", not "complete". A skipped test used to leave this line
        # reading "All tests complete." above a run in which two of the four
        # never ran; the summary below is what says which.
        print("All tests attempted.")
        print("=" * 60)
        print("\n")

    except Exception as exc:                                    # noqa: BLE001
        failures.append(f"unhandled {type(exc).__name__}: {exc}")
        print("\n" + "=" * 60)
        print(f"✗ The tests stopped on an unhandled {type(exc).__name__}. The "
              f"database verdict below still runs -- that is what this handler "
              f"is for -- and the exit code is 1.")
        print("=" * 60)
        traceback.print_exc()
        print("\n")

    # =======================================================================
    # THE TESTS' OWN VERDICT -- printed BEFORE the database guard
    # =======================================================================
    #
    # ORDER MATTERS AND THE FIRST DRAFT OF THIS PASS GOT IT WRONG. This summary
    # sat BELOW the guard, and the guard returns 1 as soon as it finds the row
    # count moved -- so a run that both failed a test and moved the count
    # returned at the guard and never printed this list at all. A real finding
    # dropped because a worse one happened beside it. Printing it first costs
    # the guard nothing: the guard's own verdict is still the last thing on the
    # terminal, which is what a reader looks for.

    if failures:
        print("=" * 60)
        print(f"✗ {len(failures)} check(s) failed:")
        for _f in failures:
            print(f"    {_f}")
        print("  Each is diagnosed above: a non-200 prints the server's own "
              "response")
        print("  body, an unhandled exception prints its traceback, a skipped "
              "test")
        print("  prints the pattern it searched and what it found there.")
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

    # Non-zero when a check failed, because the failure it reports used to be a
    # crash and a crash is exit 1; see the docstring. The summary itself was
    # printed above.
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 20:35:02 2026

@author: ramyalsaffar
"""
