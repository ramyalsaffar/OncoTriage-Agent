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

# Seed for the reproducible down-sample to CAP patients
RANDOM_SEED = 42

# Directory with all patients (will delete non-cancer ones in-place)
PATIENTS_DIR = data_fhir_path


#------------------------------------------------------------------------------


# Run needed files
#-----------------
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py", "03- Config.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# File 07 is chained for _select_best_coding(): the cohort filter must read a
# condition's codings exactly the way the pipeline's parser does, or the set of
# patients on disk stops agreeing with the set of patients the pipeline calls
# cancer patients.
exec_chain(
    ["07- FHIR Parser.py", "08- Cancer Code Registry.py"],
    caller_file=_code_dir + "05- FHIR Clean Data.py",
    caller_globals=globals(),
    chain_label="07 → 08",
)


# Call
#-----
_CANCER_REGISTRY = load_registry()

# _EXCLUDE_VERIFICATION is NOT redefined here. File 08 owns it
# (08- Cancer Code Registry.py, module level) and the registry exposes it as
# .exclude_verification. A second frozenset with the same values today is a
# second frozenset with different values the day one of them is edited, and
# under the exec() chain the later definition silently wins for every file
# loaded after this one. Read it off the registry instead.


#------------------------------------------------------------------------------


# Deletion Manifest
#------------------
#
# Every unlink in this file is manifest-backed. The rule is: nothing is
# deleted that was not written to the manifest first, and every outcome
# (deleted / already absent / failed, with the error text) is written back.
# The deletion is in-place and irreversible, so a half-finished run must still
# be able to answer "which 4,300 files did it remove before it died".

_MANIFEST_PATH = os.path.join(checkpoint_path, COHORT_MANIFEST_FILENAME)

# Outcome counters for the whole run. No except: branch below leaves without
# incrementing one of these.
_DELETION_COUNTS = {
    "deleted":        0,
    "already_absent": 0,
    "failed":         0,
    "manifest_write_failed": 0,
}


def _write_manifest(manifest):
    """
    Persist the manifest atomically (temp file + os.replace + fsync).

    Returns True on success. A manifest write that fails is recorded in
    _DELETION_COUNTS and raised: losing the record is not a recoverable
    condition, because the next thing this file does is delete data that only
    the manifest describes.
    """
    tmp_path = _MANIFEST_PATH + ".tmp"
    try:
        with open(tmp_path, "w") as fh:
            json.dump(manifest, fh, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, _MANIFEST_PATH)
        return True
    except OSError as e:
        _DELETION_COUNTS["manifest_write_failed"] += 1
        print(f"  FATAL: cannot write deletion manifest {_MANIFEST_PATH}: {e}")
        raise


def _delete_manifested(manifest, phase_key, files, reason, progress_every=500):
    """
    Delete `files`, recording every outcome in the manifest.

    Args:
        manifest:       the live manifest dict (mutated and rewritten here)
        phase_key:      manifest["phases"] key for this batch, e.g. "non_cancer"
        files:          list of Path objects to remove
        reason:         why these files are being removed (goes in the manifest)
        progress_every: console progress interval

    Returns:
        dict: this phase's record — planned/deleted/already_absent/failed.

    The plan is written and fsync'd before the first unlink, so a crash at any
    point leaves the full target list on disk plus every outcome recorded up to
    the last flush. Individual failures do not abort the loop: aborting halfway
    would leave the remaining targets untouched AND unexplained, whereas
    finishing the sweep leaves a complete account. The caller decides what a
    non-zero failure count means.
    """
    phase = {
        "reason":         reason,
        "status":         "in_progress",
        "planned_count":  len(files),
        "planned":        [f.name for f in files],
        "deleted":        [],
        "already_absent": [],
        "failed":         [],
    }
    manifest["phases"][phase_key] = phase
    manifest["status"] = "in_progress"
    _write_manifest(manifest)

    for idx, patient_file in enumerate(files, 1):

        try:
            patient_file.unlink()
            phase["deleted"].append(patient_file.name)
            _DELETION_COUNTS["deleted"] += 1

        except FileNotFoundError:
            # Already gone — a re-run over a partially filtered directory, or a
            # concurrent writer. Not an error, but it is not a deletion either.
            phase["already_absent"].append(patient_file.name)
            _DELETION_COUNTS["already_absent"] += 1
            print(f"  WARNING: already absent, not deleted: {patient_file.name}")

        except OSError as e:
            phase["failed"].append({
                "file":  patient_file.name,
                "error": f"{type(e).__name__}: {e}",
            })
            _DELETION_COUNTS["failed"] += 1
            print(f"  ERROR deleting {patient_file.name}: {type(e).__name__}: {e}")

        if idx % COHORT_MANIFEST_FLUSH_EVERY == 0:
            _write_manifest(manifest)

        if idx % progress_every == 0:
            print(f"  Deleted {idx}/{len(files)} files...")

    phase["status"] = "complete" if not phase["failed"] else "partial"
    _write_manifest(manifest)

    return phase


#------------------------------------------------------------------------------


# Filter Functions
#------------------

def has_cancer_diagnosis(bundle_data):
    """
    Determine whether a patient FHIR bundle contains an active primary cancer diagnosis.

    Uses CancerCodeRegistry (SNOMED + ICD-10-CM 2024) for code-based detection —
    the same registry used downstream in File 10. This guarantees that every patient
    kept by File 05 will be recognized as a cancer patient by the matching pipeline.

    Coding selection goes through File 07's _select_best_coding(), the same
    function parse_fhir_bundle() uses, and the full annotated coding list is
    handed to is_primary_cancer() under the "codings" key. This replaced a
    coding_list[0] read that trusted Synthea to emit SNOMED first:

      - Positional reads break on any bundle whose Condition.code.coding array
        is ordered differently — which is the normal case in real EHR data and
        the reason _select_best_coding() exists at all.
      - Passing a single {code, display} dict pushed is_primary_cancer() onto
        its backward-compatible path, where the code carries system_key
        "unknown" and only ONE coding is examined. The multi-coding logic —
        exclude if ANY coding is secondary/metastatic or non-invasive, admit if
        ANY coding matches a primary set — never ran. A condition coded
        C78.00 (secondary lung) in position 1 and a primary site code in
        position 0 was admitted; the pipeline, reading all codings, rejects it.

    The cohort filter and the pipeline now classify a condition through the
    same code path, so a patient kept here cannot be dropped downstream for
    not being a cancer patient.

    Skips conditions marked refuted or entered-in-error (verificationStatus),
    using File 08's set via the registry rather than a local copy.
    Secondary/metastatic conditions are excluded inside is_primary_cancer().
    Display-name keyword matching is intentionally avoided unless no coding is
    usable — SNOMED codes are stable and unambiguous; display strings are not.

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

        if verification in _CANCER_REGISTRY.exclude_verification:
            continue

        # Resolve the coding array the same way the pipeline's parser does:
        # system-preference selection for the representative code, and every
        # coding annotated with its system_key for the multi-coding checks in
        # is_primary_cancer(). No positional assumption about coding order.
        coding_list = resource.get('code', {}).get('coding', [])
        best_coding, all_codings = _select_best_coding(coding_list, "condition")
        condition = {
            'code':    best_coding['code'],
            'display': best_coding['display'],
            'codings': all_codings,
        }

        if _CANCER_REGISTRY.is_primary_cancer(condition):
            display = best_coding['display']
            if not display or display == 'unknown':
                display = 'Unknown cancer'
            cancer_types.append(display)

    return len(cancer_types) > 0, cancer_types


def filter_cancer_patients_inplace():
    """
    Delete non-cancer patients AND cap at CAP patients (in-place filtering)

    Both deletion phases go through _delete_manifested(), so the target list is
    on disk before anything is unlinked and every outcome is written back. A
    phase that hits IO errors finishes its sweep, marks itself "partial", and
    the run returns a non-zero deletion_failures count instead of exiting as if
    it had succeeded.

    Files that fail to parse are counted, listed in the manifest, left on disk,
    and excluded from the cohort. They are NOT part of the cap sampling pool:
    a bundle that could not be read has not been shown to contain a cancer
    diagnosis, and letting one occupy a slot in a 1,000-patient cancer cohort
    is the same silent-recovery defect this project exists to remove. Their
    presence in the directory is reported, since the directory then holds more
    .json files than the cohort has patients.

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
    
    # Open the deletion manifest before anything is unlinked.
    manifest = {
        'created_utc':          datetime.now(timezone.utc).isoformat(),
        'directory':            str(patients_path),
        'cap':                  CAP,
        'random_seed':          RANDOM_SEED,
        'selector':             '_select_best_coding("condition") + '
                                'CancerCodeRegistry.is_primary_cancer(codings=...)',
        'scanned':              len(patient_files),
        'cancer_found':         len(cancer_patients),
        'non_cancer_found':     len(non_cancer_patients),
        'unparseable_retained': sorted(f.name for f in error_patients),
        'status':               'planned',
        'phases':               {},
    }
    _write_manifest(manifest)
    print(f"Deletion manifest: {_MANIFEST_PATH}")
    print()

    # STEP 1: Delete non-cancer patients (if any)
    non_cancer_deleted = 0

    if non_cancer_patients:
        print("="*80)
        print(f"STEP 1: DELETE {len(non_cancer_patients)} NON-CANCER PATIENTS")
        print("="*80)
        print()

        phase = _delete_manifested(
            manifest,
            phase_key='non_cancer',
            files=non_cancer_patients,
            reason='no primary cancer condition found by has_cancer_diagnosis()',
            progress_every=500,
        )
        non_cancer_deleted = len(phase['deleted'])

        print(f"  Deleted {non_cancer_deleted}/{len(non_cancer_patients)} non-cancer patients... \nDONE")
        if phase['failed'] or phase['already_absent']:
            print(f"  NOT deleted: {len(phase['failed'])} failed, "
                  f"{len(phase['already_absent'])} already absent (see manifest)")
        print()
    else:
        print("="*80)
        print("STEP 1: NO NON-CANCER PATIENTS TO DELETE")
        print("="*80)
        print()

    # STEP 2: Cap at CAP patients (if needed)
    #
    # The sampling pool is the confirmed-cancer list, not a re-glob of the
    # directory: a re-glob also picks up the bundles that failed to parse and
    # can hand one of them a slot in the cohort. Sorted for deterministic input
    # ordering before the seeded random.sample.
    remaining_files = sorted(cancer_patients)
    extra_deleted = 0

    if error_patients:
        print(f"NOTE: {len(error_patients)} unparseable bundle(s) left on disk and "
              f"excluded from the cap pool (listed in the manifest).")
        print()

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
        random.seed(RANDOM_SEED)

        # Randomly select CAP to KEEP
        patients_to_keep = random.sample(remaining_files, CAP)
        patients_to_keep_set = set(patients_to_keep)

        # Delete the rest
        patients_to_remove = [f for f in remaining_files if f not in patients_to_keep_set]

        print(f"Randomly selecting {CAP} patients to keep (seed={RANDOM_SEED})...")
        print(f"Deleting {len(patients_to_remove)} extra cancer patients...")
        print()

        phase = _delete_manifested(
            manifest,
            phase_key='over_cap',
            files=patients_to_remove,
            reason=f'cancer patient beyond CAP={CAP} (seed={RANDOM_SEED})',
            progress_every=100,
        )
        extra_deleted = len(phase['deleted'])

        print(f"  Deleted {extra_deleted}/{len(patients_to_remove)} extra patients... \n\nDONE")
        if phase['failed'] or phase['already_absent']:
            print(f"  NOT deleted: {len(phase['failed'])} failed, "
                  f"{len(phase['already_absent'])} already absent (see manifest)")
        print()
    else:
        print("="*80)
        print(f"STEP 2: ALREADY AT OR BELOW {CAP} PATIENTS")
        print("="*80)
        print()

    # FINAL STATS
    final_files = list(patients_path.glob("*.json"))
    deletion_failures = _DELETION_COUNTS["failed"]

    manifest['status'] = 'complete' if not deletion_failures else 'partial'
    manifest['finished_utc'] = datetime.now(timezone.utc).isoformat()
    manifest['counts'] = dict(_DELETION_COUNTS)
    manifest['files_remaining'] = len(final_files)
    _write_manifest(manifest)

    print("="*80)
    print("FILTERING COMPLETE!" if not deletion_failures else "FILTERING FINISHED WITH FAILURES")
    print("="*80)
    print()
    print(f"✓ Non-cancer patients deleted: {non_cancer_deleted}")
    print(f"✓ Extra cancer patients deleted (for cap): {extra_deleted}")
    print(f"✓ Files remaining in directory: {len(final_files)}")
    print(f"✓ Directory: {patients_path}")
    print(f"✓ Manifest: {_MANIFEST_PATH} (status: {manifest['status']})")
    if deletion_failures:
        print(f"✗ Deletions that FAILED: {deletion_failures} — the directory is "
              f"partially filtered. Per-file errors are in the manifest.")
    if _DELETION_COUNTS["already_absent"]:
        print(f"! Targets already absent: {_DELETION_COUNTS['already_absent']}")
    if error_patients:
        print(f"! Unparseable bundles left on disk (not in cohort): {len(error_patients)}")
    print()

    stats = {
        'total_scanned': len(patient_files),
        'cancer_patients_found': len(cancer_patients),
        'final_cancer_patients': len(final_files) - len(error_patients),
        'files_remaining': len(final_files),
        'non_cancer_deleted': non_cancer_deleted,
        'extra_deleted': extra_deleted,
        'percentage': len(cancer_patients)/len(patient_files)*100 if patient_files else 0,
        'cancer_types': cancer_counts,
        'directory': str(patients_path),
        'parse_errors': len(error_patients),
        'deletion_failures': deletion_failures,
        'deletions_already_absent': _DELETION_COUNTS['already_absent'],
        'manifest_path': _MANIFEST_PATH,
        'manifest_status': manifest['status'],
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
    
    if stats and not stats['deletion_failures']:
        print()
        print("SUCCESS! Cancer patient filtering complete.")
        print()
        print(f"Summary:")
        print(f"  - Total scanned: {stats['total_scanned']}")
        print(f"  - Cancer patients found: {stats['cancer_patients_found']}")
        print(f"  - Non-cancer deleted: {stats['non_cancer_deleted']}")
        print(f"  - Extra deleted (cap): {stats['extra_deleted']}")
        print(f"  - Parse errors (left on disk): {stats['parse_errors']}")
        print(f"  - Final dataset: {stats['final_cancer_patients']} patients")
        print(f"  - Manifest: {stats['manifest_path']}")
        print()
        print("NEXT STEP: Run FHIR Parser to parse the cancer patients")
        print()
    elif stats:
        # Deletions failed: the directory is partially filtered. Exit non-zero
        # so a caller (or Airflow) does not treat this cohort as final.
        print()
        print("FILTERING INCOMPLETE — some deletions failed.")
        print(f"  - Deletions failed: {stats['deletion_failures']}")
        print(f"  - Files remaining: {stats['files_remaining']}")
        print(f"  - Manifest ({stats['manifest_status']}): {stats['manifest_path']}")
        print()
        print("The manifest lists every targeted file and the error for each "
              "failure. Re-run after resolving them; already-deleted files are "
              "reported as already absent.")
        print()
        sys.exit(1)
    else:
        print()
        print("Filtering failed.")
        print()
        sys.exit(1)

#------------------------------------------------------------------------------


"""
Created on Wed Feb 11 11:22:14 2026

@author: ramyalsaffar
"""