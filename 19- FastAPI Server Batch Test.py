# Batch Evaluation: Run pipeline on all patients
################################################


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

