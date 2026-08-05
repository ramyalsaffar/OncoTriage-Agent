# Batch Evaluation: Run pipeline on all patients
################################################

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


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 12 22:11:01 2026

@author: ramyalsaffar
"""

