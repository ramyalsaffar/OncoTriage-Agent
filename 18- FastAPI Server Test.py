# Test OncoMatch Agent FastAPI Server
######################################

"""
Run this while the server is live in another terminal.
Tests all 4 endpoints.

THIS SCRIPT COSTS REAL MONEY. Two of the four tests POST a real FHIR bundle to
POST /match and POST /match/file, and the server runs the whole six-stage
pipeline for each -- including Stage 5, which is a live billed call to the
matching model. Measured against the six rows this file and File 19 left in the
production database on 2026-08-05: about $0.13 to $0.17 per patient, so roughly
$0.30 for one full run of this file. Nothing here is stubbed and nothing is
replayed; "fixture_replay.py" is the file that costs nothing.

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
import glob
import json
import os
import shutil
import sqlite3
import sys
import tempfile
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

from oncotriage.paths import data_fhir_path, inferences_path  # noqa: E402


#------------------------------------------------------------------------------


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

_PRODUCTION_DB = inferences_path


def _inference_rows(db_path):
    """Rows in db_path's inferences table, or None if it cannot be read.

    None is a real answer and the caller must treat it as one: an unreadable
    database makes the before/after comparison vacuous (None == None passes),
    which is why _ROWS_BEFORE is asserted non-degenerate below.
    """
    try:
        _c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return _c.execute("SELECT COUNT(*) FROM inferences").fetchone()[0]
        finally:
            _c.close()
    except sqlite3.Error:
        return None


def _production_schema():
    """The production CREATE TABLE text for `inferences`, or None."""
    try:
        _c = sqlite3.connect(f"file:{_PRODUCTION_DB}?mode=ro", uri=True)
        try:
            _row = _c.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' "
                "AND name='inferences'").fetchone()
            return _row[0] if _row else None
        finally:
            _c.close()
    except sqlite3.Error:
        return None


# --- NEGATIVE CONTROL: the comparison must be able to come out False --------
#
# An "unchanged" verdict from a counter that always returns the same number is
# not evidence of anything. So before trusting it, run it against a database
# where a row IS inserted and require it to say so.
#
# The control database carries the PRODUCTION schema, read out of the real
# sqlite_master rather than retyped, so the counter is exercised against the
# same table definition it will meet for real. It is not a byte copy of the
# 86 MB production file: copying that on every run would cost seconds and disk
# for no additional coverage -- the count query reads the table, and the table
# is what is reproduced here. What a byte copy would add is confidence that the
# COUNT is correct on a populated table, so the control seeds two rows first and
# checks the count reads 2 before the third is added.
_control_dir = tempfile.mkdtemp(prefix="oncotriage-rowguard-")
_control_db = os.path.join(_control_dir, "control.db")
_schema = _production_schema()

if _schema is None:
    print("\n✗ FAIL: the production inferences table could not be read, so the "
          "row-count guard below cannot be shown to work.")
    print(f"  Database: {_PRODUCTION_DB}")
    shutil.rmtree(_control_dir, ignore_errors=True)
    raise SystemExit(1)

_cc = sqlite3.connect(_control_db)
_cc.execute(_schema)
for _i in range(2):
    _cc.execute("INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
                (f"control-{_i}", datetime.now(timezone.utc).isoformat()))
_cc.commit()
_cc.close()

_control_before = _inference_rows(_control_db)

_cc = sqlite3.connect(_control_db)
_cc.execute("INSERT INTO inferences (patient_id, timestamp) VALUES (?, ?)",
            ("control-inserted", datetime.now(timezone.utc).isoformat()))
_cc.commit()
_cc.close()

_control_after = _inference_rows(_control_db)
shutil.rmtree(_control_dir, ignore_errors=True)

if not (_control_before == 2 and _control_after == 3
        and _control_before != _control_after):
    print("\n✗ FAIL: the row-count guard does not discriminate. Against a "
          "database built from the production schema with one row inserted it "
          f"reported before={_control_before!r} after={_control_after!r}; it "
          "must report 2 then 3. The 'production database unchanged' verdict "
          "at the end of this run would be meaningless, so the run is stopped "
          "here rather than printing it.")
    raise SystemExit(1)

print(f"[Guard] Row-count control fired: {_control_before} -> {_control_after} "
      f"on a database carrying the production inferences schema.")

_ROWS_BEFORE = _inference_rows(_PRODUCTION_DB)

if _ROWS_BEFORE is None:
    print(f"\n✗ FAIL: cannot read the production inferences table at "
          f"{_PRODUCTION_DB}, so 'unchanged' would compare None against None "
          f"and pass whatever this run does.")
    raise SystemExit(1)

print(f"[Guard] Production inference rows before this run: {_ROWS_BEFORE}")
print(f"[Guard] Production database: {_PRODUCTION_DB}")
print("[Guard] If the server was NOT started with ONCOTRIAGE_INFERENCES_DB "
      "set, this run will write into that file and this script will fail at "
      "the end.")


#------------------------------------------------------------------------------



BASE_URL = "http://localhost:8000"


# ------------------------------------------------------------------
# Test 1: Health Check
# ------------------------------------------------------------------

print("=" * 60)
print("Test 1: GET /health")
print("=" * 60)

r = requests.get(f"{BASE_URL}/health")
print(json.dumps(r.json(), indent=2))


# ------------------------------------------------------------------
# Test 2: Pipeline Info
# ------------------------------------------------------------------

print("\n" + "=" * 60)
print("Test 2: GET /pipeline/info")
print("=" * 60)

r = requests.get(f"{BASE_URL}/pipeline/info")
print(json.dumps(r.json(), indent=2))


# ------------------------------------------------------------------
# Test 3: Match via JSON body (POST /match)
# ------------------------------------------------------------------

print("\n" + "=" * 60)
print("Test 3: POST /match (JSON body)")
print("=" * 60)

# Grab first FHIR bundle from the data directory
fhir_files = sorted(glob.glob(data_fhir_path + "*.json"))

if fhir_files:
    with open(fhir_files[0]) as f:
        bundle = json.load(f)

    print(f"Using: {fhir_files[0].split('/')[-1]}")

    r = requests.post(
        f"{BASE_URL}/match",
        json={"fhir_bundle": bundle}
    )

    result = r.json()
    print(f"\nStatus: {r.status_code}")
    print(f"Processing time: {result['processing_time_seconds']}s")

    print(f"\nStatus: {r.status_code}")

    if r.status_code != 200:
        print("ERROR RESPONSE:")
        print(r.text)
    else:
        result = r.json()
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
            print(f"     Explanation: {m.get('explanation', 'N/A')[:200]}")
    else:
        print("\nNo matches found.")
        print("\nFull result for inspection:")
        print(json.dumps(res, indent=2))
else:
    print("No FHIR files found.")


# ------------------------------------------------------------------
# Test 4: Match via file upload (POST /match/file)
# ------------------------------------------------------------------

print("\n" + "=" * 60)
print("Test 4: POST /match/file (file upload)")
print("=" * 60)

if len(fhir_files) > 1:
    filepath = fhir_files[1]
    print(f"Using: {filepath.split('/')[-1]}")

    with open(filepath, 'rb') as f:
        r = requests.post(
            f"{BASE_URL}/match/file",
            files={"file": (filepath.split('/')[-1], f, "application/json")}
        )

    result = r.json()
    print(f"\nStatus: {r.status_code}")
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
            print(f"     Explanation: {m.get('explanation', 'N/A')[:200]}")
    else:
        print("No matches — printing full result:")
        print(json.dumps(res, indent=2))
else:
    print("Need at least 2 FHIR files to test both endpoints.")


print("\n" + "=" * 60)
print("All tests complete.")
print("=" * 60)
print("\n")


# ===========================================================================
# THE PRODUCTION DATABASE MUST NOT MOVE -- the verdict
# ===========================================================================
#
# Read again and compare. A change means the server this run talked to was
# writing to the production database, which is the defect described at the top
# of this file: real test traffic accumulating in the table every later
# analysis, query and drift baseline is computed over.
#
# The exit code is non-zero on a change. That is what makes this a check rather
# than a note: this file is run by hand today, and item 20d will run it from a
# harness that reads exit codes.

_ROWS_AFTER = _inference_rows(_PRODUCTION_DB)

print("=" * 60)
print("Production database guard")
print("=" * 60)
print(f"  Database: {_PRODUCTION_DB}")
print(f"  Rows before: {_ROWS_BEFORE}")
print(f"  Rows after:  {_ROWS_AFTER}")

if _ROWS_AFTER is None:
    print("\n✗ FAIL: the production inferences table became unreadable during "
          "this run. That is not 'unchanged'; it is unknown.")
    raise SystemExit(1)

if _ROWS_AFTER != _ROWS_BEFORE:
    print(f"\n✗ FAIL: this run wrote {_ROWS_AFTER - _ROWS_BEFORE} row(s) into "
          f"the PRODUCTION inference database.")
    print("\n  The server was not redirected. Restart it with:")
    print("      ONCOTRIAGE_INFERENCES_DB=/tmp/oncotriage-test.db \\")
    print('          python "17- FastAPI Server.py"')
    print("\n  ONCOTRIAGE_INFERENCES_DB is read by "
          "oncotriage/settings.py:resolve_inferences_db() and honoured by both "
          "resolve_inference_db_path (storage/database_logger.py) and "
          "resolve_drift_db_path (monitoring/drift.py).")
    print("\n  The rows are NOT removed by this script. Deciding what to do "
          "with them is the operator's call; File 16's cost and model "
          "breakdowns are where to look at them first.")
    raise SystemExit(1)

print("\n✓ The production inference database was not written to by this run.")
print("=" * 60)
print("\n")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 20:35:02 2026

@author: ramyalsaffar
"""
