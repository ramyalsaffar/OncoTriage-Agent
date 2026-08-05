# Test OncoMatch Agent FastAPI Server
######################################

"""
Run this while the server is live in another terminal.
Tests all 4 endpoints.
"""

# ===========================================================================
# EXEC CHAIN: 01
# ===========================================================================
# Four free names -- data_fhir_path, glob, json and requests -- all from
# 01- Imports.py. This script talks to a live server over HTTP and uses
# no project function, so 02 and 03 are not loaded.
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

with open(_code_dir + "01- Imports.py") as _fh:
    exec(_fh.read(), globals())


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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Wed Feb 11 20:35:02 2026

@author: ramyalsaffar
"""
