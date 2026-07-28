# Filter Cancer Patients Only (IN-PLACE DELETION)
##################################################

"""
Step 2: Filter synthetic patients to keep only those with cancer diagnoses
Deletes non-cancer patients directly from fhir/ directory
"""


# Configuration
#--------------

# The max number of patients with cancer
CAP = 1000

# Directory with all patients (will delete non-cancer ones in-place)
PATIENTS_DIR = data_fhir_path


#------------------------------------------------------------------------------


# Run needed files
#-----------------
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py", "03- Config.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["08- Cancer Code Registry.py"],
    caller_file=_code_dir + "05- FHIR Clean Data.py",
    caller_globals=globals(),
    chain_label="08",
)


# Call
#-----
_CANCER_REGISTRY = load_registry()
_EXCLUDE_VERIFICATION = frozenset({"refuted", "entered-in-error"})


#------------------------------------------------------------------------------


# Filter Functions
#------------------

def has_cancer_diagnosis(bundle_data):
    """
    Determine whether a patient FHIR bundle contains an active primary cancer diagnosis.

    Uses CancerCodeRegistry (SNOMED + ICD-10-CM 2024) for code-based detection —
    the same registry used downstream in File 10. This guarantees that every patient
    kept by File 05 will be recognized as a cancer patient by the matching pipeline.

    Skips conditions marked refuted or entered-in-error (verificationStatus).
    Secondary/metastatic conditions are excluded inside is_primary_cancer().
    Display-name keyword matching is intentionally avoided — SNOMED codes are
    stable and unambiguous; display strings are not.

    Args:
        bundle_data: parsed FHIR Bundle dict (from json.load)

    Returns:
        tuple: (has_cancer: bool, cancer_types: list[str])
            has_cancer   — True if at least one primary cancer condition found
            cancer_types — list of display strings for matched cancer conditions
                           (empty list if none found)
    """
    if not bundle_data or not isinstance(bundle_data, dict):
        return False, []

    cancer_types = []

    for entry in bundle_data.get('entry', []):
        resource = entry.get('resource', {})
        if not resource or resource.get('resourceType') != 'Condition':
            continue

        # Skip retracted / data-entry-error conditions
        ver_codings = resource.get('verificationStatus', {}).get('coding', [])
        verification = ver_codings[0].get('code', 'unknown').lower().strip() if ver_codings else 'unknown'
        
        if verification in _EXCLUDE_VERIFICATION:
            continue

        # Extract code and display from first coding entry
        # Synthea always puts SNOMED first in the coding array (it's Synthea's primary coding system, ICD-10-CM is a secondary translation)
        coding_list = resource.get('code', {}).get('coding', [])
        coding = coding_list[0] if coding_list else {}
        condition = {
            'code':    coding.get('code', ''),
            'display': coding.get('display', ''),
        }

        if _CANCER_REGISTRY.is_primary_cancer(condition):
            display = coding.get('display') or 'Unknown cancer'
            cancer_types.append(display)

    return len(cancer_types) > 0, cancer_types


def filter_cancer_patients_inplace():
    """
    Delete non-cancer patients AND cap at CAP patients (in-place filtering)
    
    Returns:
        dict: Statistics about filtering
    """
    
    print("="*80)
    print("STEP 2: FILTER CANCER PATIENTS (IN-PLACE DELETION)")
    print("="*80)
    print()
    
    # Check directory exists
    patients_path = Path(PATIENTS_DIR)
    if not patients_path.exists():
        print(f"ERROR: Patient directory not found: {PATIENTS_DIR}")
        return None
    
    # Get all patient files
    patient_files = list(patients_path.glob("*.json"))
    
    print(f"Patient directory: {PATIENTS_DIR}")
    print(f"Total patients: {len(patient_files)}")
    print()
    
    print("="*80)
    print("SCANNING PATIENTS FOR CANCER DIAGNOSES...")
    print("="*80)
    print()
    
    cancer_patients = []
    non_cancer_patients = []
    cancer_counts = {}
    error_patients = []
    
    # Check each patient
    for idx, patient_file in enumerate(patient_files, 1):
        if idx % 500 == 0:
            print(f"  Processed {idx}/{len(patient_files)} patients...")
        
        try:
            with open(patient_file, 'r') as f:
                bundle = json.load(f)
            
            has_cancer, cancer_types = has_cancer_diagnosis(bundle)
            
            if has_cancer:
                cancer_patients.append(patient_file)
                
                # Count cancer types
                for cancer in cancer_types:
                    cancer_counts[cancer] = cancer_counts.get(cancer, 0) + 1
            else:
                non_cancer_patients.append(patient_file)
                
        except Exception as e:
            print(f"  ERROR processing {patient_file.name}: {e}")
            error_patients.append(patient_file)
    
    print(f"  Processed {len(patient_files)}/{len(patient_files)} patients... \nDONE")
    print()
    
    # Results
    print("="*80)
    print("FILTERING RESULTS")
    print("="*80)
    print()
    print(f"Total patients scanned: {len(patient_files)}")
    print(f"Cancer patients found: {len(cancer_patients)}")
    print(f"Non-cancer patients: {len(non_cancer_patients)}")
    cancer_pct = len(cancer_patients) / len(patient_files) * 100 if patient_files else 0.0
    print(f"Cancer percentage: {cancer_pct:.1f}%")
    if error_patients:
        print(f"Files with parse errors (skipped): {len(error_patients)}")
    print()
    
    if cancer_counts:
        print("Cancer types distribution:")
        for cancer_type, count in sorted(cancer_counts.items(), 
                                         key=lambda x: x[1], 
                                         reverse=True)[:15]:
            print(f"  • {cancer_type}: {count}")
        
        if len(cancer_counts) > 15:
            print(f"  ... and {len(cancer_counts) - 15} more types")
    print()
    
    # STEP 1: Delete non-cancer patients (if any)
    non_cancer_deleted = 0
    
    if non_cancer_patients:
        print("="*80)
        print(f"STEP 1: DELETE {len(non_cancer_patients)} NON-CANCER PATIENTS")
        print("="*80)
        print()
        
        for idx, patient_file in enumerate(non_cancer_patients, 1):
            os.remove(patient_file)
            
            if idx % 500 == 0:
                print(f"  Deleted {idx}/{len(non_cancer_patients)} non-cancer patients...")
        
        print(f"  Deleted {len(non_cancer_patients)}/{len(non_cancer_patients)} non-cancer patients... \nDONE")
        print()
        
        non_cancer_deleted = len(non_cancer_patients)
    else:
        print("="*80)
        print("STEP 1: NO NON-CANCER PATIENTS TO DELETE")
        print("="*80)
        print()
    
    # STEP 2: Cap at CAP patients (if needed)
    # Sort for deterministic input ordering before seeded random.sample
    remaining_files = sorted(list(patients_path.glob("*.json")))
    extra_deleted = 0
    
    if len(remaining_files) > CAP:
        print("="*80)
        print(f"STEP 2: CAP DATASET AT {CAP} PATIENTS")
        print("="*80)
        print()
        print(f"Current cancer patients: {len(remaining_files)}")
        print(f"Target: {CAP} patients")
        print(f"Need to remove: {len(remaining_files) - CAP} patients")
        print()
        
        # Reproducible random sampling
        RANDOM_SEED = 42
        random.seed(RANDOM_SEED)
        
        # Randomly select CAP to KEEP
        patients_to_keep = random.sample(remaining_files, CAP)
        patients_to_keep_set = set(patients_to_keep)
        
        # Delete the rest
        patients_to_remove = [f for f in remaining_files if f not in patients_to_keep_set]
        
        print(f"Randomly selecting {CAP} patients to keep (seed={RANDOM_SEED})...")
        print(f"Deleting {len(patients_to_remove)} extra cancer patients...")
        print()
        
        for idx, patient_file in enumerate(patients_to_remove, 1):
            os.remove(patient_file)
            
            if idx % 100 == 0:
                print(f"  Deleted {idx}/{len(patients_to_remove)} extra patients...")
        
        print(f"  Deleted {len(patients_to_remove)}/{len(patients_to_remove)} extra patients... \n\nDONE")
        print()
        
        extra_deleted = len(patients_to_remove)
    else:
        print("="*80)
        print(f"STEP 2: ALREADY AT OR BELOW {CAP} PATIENTS")
        print("="*80)
        print()
    
    # FINAL STATS
    final_files = list(patients_path.glob("*.json"))
    
    print("="*80)
    print("FILTERING COMPLETE!")
    print("="*80)
    print()
    print(f"✓ Non-cancer patients deleted: {non_cancer_deleted}")
    print(f"✓ Extra cancer patients deleted (for cap): {extra_deleted}")
    print(f"✓ Final dataset: {len(final_files)} cancer patients")
    print(f"✓ Directory: {patients_path}")
    print()
    
    stats = {
        'total_scanned': len(patient_files),
        'cancer_patients_found': len(cancer_patients),
        'final_cancer_patients': len(final_files),
        'non_cancer_deleted': non_cancer_deleted,
        'extra_deleted': extra_deleted,
        'percentage': len(cancer_patients)/len(patient_files)*100 if patient_files else 0,
        'cancer_types': cancer_counts,
        'directory': str(patients_path),
        'parse_errors': len(error_patients),
    }
    
    return stats


#------------------------------------------------------------------------------


if __name__ == "__main__":
    
    print()
    print("╔═══════════════════════════════════════════════════════════════════════╗")
    print(f"║                  {Project_Name}: FILTER CANCER PATIENTS              ║")
    print("╚═══════════════════════════════════════════════════════════════════════╝")
    print()
    
    # Filter patients (in-place deletion)
    stats = filter_cancer_patients_inplace()
    
    if stats:
        print()
        print("SUCCESS! Cancer patient filtering complete.")
        print()
        print(f"Summary:")
        print(f"  - Total scanned: {stats['total_scanned']}")
        print(f"  - Cancer patients found: {stats['cancer_patients_found']}")
        print(f"  - Non-cancer deleted: {stats['non_cancer_deleted']}")
        print(f"  - Extra deleted (cap): {stats['extra_deleted']}")
        print(f"  - Final dataset: {stats['final_cancer_patients']} patients")
        print()
        print("NEXT STEP: Run FHIR Parser to parse the cancer patients")
        print()
    else:
        print()
        print("Filtering failed.")
        print()

#------------------------------------------------------------------------------


"""
Created on Wed Feb 11 11:22:14 2026

@author: ramyalsaffar
"""