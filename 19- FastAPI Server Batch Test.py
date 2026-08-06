# Batch Evaluation: Run pipeline on all patients
################################################

"""
Run this while the server is live in another terminal. POSTs one FHIR bundle
per patient to POST /match and reports success/error counts and timings.

THIS SCRIPT COSTS REAL MONEY, and how much depends entirely on how many
patients it runs. Every POST runs the whole six-stage pipeline on the server,
including Stage 5, which is a live billed call to the matching model. Measured
from the rows this file and File 18 left in the production database on
2026-08-05: about $0.13 to $0.17 per patient. At the two patients the slice
below currently selects that is roughly $0.30; over the full corpus of ~22,000
it would be four figures. Read the slice before running it.

READ THE SLICE, LITERALLY. Line 41 below overwrites the file list with
`fhir_files[410:412]` under a comment reading "For testing purposes", so this
script runs TWO patients while its title, its "Found N patients" line and its
"Batch evaluation complete" summary all describe a full-corpus run. That is not
changed here -- it is reported as-is and left for the operator, because
widening it is a spending decision.

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
# EXEC CHAIN: 01 -> 02
# ===========================================================================
# data_fhir_path, glob, json, requests and time come from 01;
# CaffeinateSession comes from 02. Nothing here reads a config constant,
# so 03 is not loaded.
#
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

# Get all FHIR files
fhir_files = sorted(glob.glob(data_fhir_path + "*.json"))


# For testing purposes
#---------------------
fhir_files = fhir_files[410:412]


print(f"Found {len(fhir_files)} patients")
print(f"Running batch evaluation...\n")


success_count = 0
error_count = 0
start_time = time.time()


with CaffeinateSession("FastAPI Server Batch Test"):

    for idx, fhir_file in enumerate(fhir_files, 1):
        patient_start = time.time()
        
        try:
            with open(fhir_file) as f:
                bundle = json.load(f)
            
            response = requests.post(
                f"{BASE_URL}/match",
                json={"fhir_bundle": bundle},
                timeout=180
            )
            
            if response.status_code == 200:
                success_count += 1
                patient_time = time.time() - patient_start
                print(f"[{idx}/{len(fhir_files)}] Success ({patient_time:.1f}s)")
            else:
                error_count += 1
                print(f"[{idx}/{len(fhir_files)}] ERROR: HTTP {response.status_code}")
                
        except requests.exceptions.Timeout:
            error_count += 1
            print(f"[{idx}/{len(fhir_files)}] TIMEOUT (>{180}s)")
        except Exception as e:
            error_count += 1
            print(f"[{idx}/{len(fhir_files)}] ERROR: {e}")
    
    elapsed = time.time() - start_time
    print(f"\n{'='*60}")
    print(f"Batch evaluation complete:")
    print(f"  Success: {success_count}/{len(fhir_files)}")
    print(f"  Errors: {error_count}")
    print(f"  Total time: {elapsed/60:.1f} minutes")
    if len(fhir_files) > 0:
        print(f"  Avg time/patient: {elapsed/len(fhir_files):.1f}s")
    print(f"{'='*60}")


# ===========================================================================
# THE PRODUCTION DATABASE MUST NOT MOVE -- the verdict
# ===========================================================================
#
# Read again and compare. A change means the server this run talked to was
# writing to the production database, which is the defect described at the top
# of this file: real test traffic accumulating in the table every later
# analysis, query and drift baseline is computed over.
#
# OUTSIDE the CaffeinateSession above, deliberately. A SystemExit raised inside
# the `with` would still run __exit__ and release the sleep assertion, so
# correctness is not the reason -- readability is: the guard has nothing to do
# with keeping the machine awake, and running it after the block keeps the
# failure message the last thing on the terminal rather than interleaved with
# the caffeinate teardown.
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
Created on Thu Feb 12 22:11:01 2026

@author: ramyalsaffar
"""

