# Cohort Selector Diff
######################

"""
Cohort Selector Diff — READ ONLY

Runs both cohort selectors over the patient bundles currently on disk and
records where they disagree:

  LEGACY  — has_cancer_diagnosis() as File 05 shipped it: read
            Condition.code.coding[0] by array position, build a
            {code, display} dict with no "codings" key, and hand that to
            CancerCodeRegistry.is_primary_cancer(). Because "codings" is
            absent, is_primary_cancer() takes its backward-compatible
            single-code path: system_key "unknown", one code examined, and
            none of the multi-coding exclusion logic runs.

  CURRENT — has_cancer_diagnosis() as File 05 now defines it: coding
            selection through File 07's _select_best_coding("condition"),
            with the full annotated coding list passed under "codings" so
            is_primary_cancer() checks every coding.

The CURRENT selector is not re-implemented here. File 05 is loaded through
the exec chain and its function object is called directly, so this script
measures the code that will actually build the cohort.

This script NEVER deletes, moves or rewrites a patient bundle. It opens
bundles read-only and writes two report files under the FHIR Exploration
results directory.

One-sided by construction: the directory holds the survivors of a previous
LEGACY run, so every file present was LEGACY-positive. The measurable
disagreement is therefore "LEGACY kept it, CURRENT would not". Patients the
LEGACY selector deleted are gone and cannot be re-tested; whether CURRENT
would have kept any of them is answerable only by regenerating from Synthea.
That limit is restated in the report.

Run from terminal (or F5 in Spyder):
    python "34- Cohort Selector Diff.py"

Exit codes:
    0 -- diff completed (agreement or disagreement; both are results)
    1 -- no bundles found, or the report could not be written
"""


# Run needed files
#-----------------
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

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py", "03- Config.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

# 05 chains 07 and 08 itself — do not list them again here.
exec_chain(
    ["05- FHIR Clean Data.py"],
    caller_file=_code_dir + "34- Cohort Selector Diff.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 03 → 05 (→ 07 → 08)",
)


#------------------------------------------------------------------------------


# Configuration
#--------------

# Where the two reports are written (text summary + machine-readable detail)
_REPORT_TXT  = os.path.join(result_fhir_explore_path, "cohort_selector_diff.txt")
_REPORT_JSON = os.path.join(result_fhir_explore_path, "cohort_selector_diff.json")

# Max disagreeing patients written out in full detail
_MAX_DETAIL_ROWS = 200


#------------------------------------------------------------------------------


# ===========================================================================
# LEGACY SELECTOR — verbatim reconstruction of the pre-fix File 05 logic
# ===========================================================================

# The pre-fix File 05 defined its own copy of this set at module level, which
# under the exec chain overwrote File 08's. Reconstructed locally so the legacy
# path is measured exactly as it behaved, independent of File 08's current set.
_LEGACY_EXCLUDE_VERIFICATION = frozenset({"refuted", "entered-in-error"})


def has_cancer_diagnosis_legacy(bundle_data):
    """
    Pre-fix cohort selector. Reads coding_list[0] by position and omits the
    "codings" key, which routes is_primary_cancer() to its single-code
    backward-compatible path.

    Returns:
        tuple: (has_cancer: bool, cancer_types: list[str])
    """
    if not bundle_data or not isinstance(bundle_data, dict):
        return False, []

    cancer_types = []

    for entry in bundle_data.get('entry', []):
        resource = entry.get('resource', {})
        if not resource or resource.get('resourceType') != 'Condition':
            continue

        ver_codings = resource.get('verificationStatus', {}).get('coding', [])
        verification = ver_codings[0].get('code', 'unknown').lower().strip() if ver_codings else 'unknown'

        if verification in _LEGACY_EXCLUDE_VERIFICATION:
            continue

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


#------------------------------------------------------------------------------


# ===========================================================================
# CODING SHAPE PROBE — does the positional assumption hold in this data?
# ===========================================================================

def probe_coding_shapes(bundle_data, shape_counts):
    """
    Count the coding-array shapes the legacy positional read depends on.

    The legacy comment claimed "Synthea always puts SNOMED first". These
    counters say whether that held for the bundles actually on disk, and how
    much of the multi-coding logic the legacy path was skipping.

    Mutates shape_counts in place; returns None.
    """
    for entry in bundle_data.get('entry', []):
        resource = entry.get('resource', {})
        if not resource or resource.get('resourceType') != 'Condition':
            continue

        coding_list = resource.get('code', {}).get('coding', [])
        shape_counts['conditions_total'] += 1

        if not coding_list:
            shape_counts['conditions_no_coding'] += 1
            continue

        if len(coding_list) > 1:
            shape_counts['conditions_multi_coding'] += 1

        best_coding, all_codings = _select_best_coding(coding_list, "condition")
        first_key = all_codings[0]['system_key']

        shape_counts['first_system_' + first_key] = \
            shape_counts.get('first_system_' + first_key, 0) + 1

        if best_coding['code'] != all_codings[0]['code']:
            # _select_best_coding chose a coding other than position 0 —
            # exactly the case the legacy positional read gets wrong.
            shape_counts['best_not_first'] += 1


#------------------------------------------------------------------------------


# ===========================================================================
# CONDITION-LEVEL DIFF
# ===========================================================================

def condition_level_diff(bundle_data):
    """
    Per-condition verdicts under both selectors.

    Patient-level agreement can hide condition-level disagreement: a bundle
    with one condition the selectors disagree on and another they both call
    cancer still lands in the cohort either way, but the disagreement is real
    and will surface on a bundle that lacks the second condition.

    Returns:
        list[dict]: one row per condition where the two verdicts differ.
    """
    rows = []

    for entry in bundle_data.get('entry', []):
        resource = entry.get('resource', {})
        if not resource or resource.get('resourceType') != 'Condition':
            continue

        ver_codings = resource.get('verificationStatus', {}).get('coding', [])
        verification = ver_codings[0].get('code', 'unknown').lower().strip() if ver_codings else 'unknown'
        if verification in _LEGACY_EXCLUDE_VERIFICATION:
            continue

        coding_list = resource.get('code', {}).get('coding', [])

        # Legacy view
        legacy_coding = coding_list[0] if coding_list else {}
        legacy_verdict = _CANCER_REGISTRY.is_primary_cancer({
            'code':    legacy_coding.get('code', ''),
            'display': legacy_coding.get('display', ''),
        })

        # Current view
        best_coding, all_codings = _select_best_coding(coding_list, "condition")
        current_verdict = _CANCER_REGISTRY.is_primary_cancer({
            'code':    best_coding['code'],
            'display': best_coding['display'],
            'codings': all_codings,
        })

        if legacy_verdict != current_verdict:
            rows.append({
                'display':         best_coding['display'],
                'legacy_code':     legacy_coding.get('code', ''),
                'current_code':    best_coding['code'],
                'codings':         all_codings,
                'legacy_verdict':  legacy_verdict,
                'current_verdict': current_verdict,
            })

    return rows


#------------------------------------------------------------------------------


# ===========================================================================
# RUN THE DIFF
# ===========================================================================

def run_diff():
    """
    Run both selectors over every bundle in data_fhir_path.

    Returns:
        dict: the full diff record, or None if no bundles were found.
    """
    patients_path = Path(data_fhir_path)

    if not patients_path.exists():
        print(f"ERROR: Patient directory not found: {data_fhir_path}")
        return None

    patient_files = sorted(patients_path.glob("*.json"))

    if not patient_files:
        print(f"ERROR: No patient bundles found in: {data_fhir_path}")
        return None

    print("="*80)
    print("COHORT SELECTOR DIFF (READ ONLY)")
    print("="*80)
    print()
    print(f"Directory: {patients_path}")
    print(f"Bundles:   {len(patient_files)}")
    print()

    agree_cancer     = []
    agree_non_cancer = []
    legacy_only      = []   # LEGACY says cancer, CURRENT does not — cohort shrinks
    current_only     = []   # CURRENT says cancer, LEGACY does not — cohort grows
    read_errors      = []

    shape_counts = {
        'conditions_total':        0,
        'conditions_no_coding':    0,
        'conditions_multi_coding': 0,
        'best_not_first':          0,
    }

    condition_disagreements = []

    # Registry counters are process-wide; zero them so the totals below
    # describe this diff run only.
    reset_cancer_classification_stats()

    for idx, patient_file in enumerate(patient_files, 1):

        if idx % 200 == 0:
            print(f"  Compared {idx}/{len(patient_files)} bundles...")

        try:
            with open(patient_file, 'r') as fh:
                bundle = json.load(fh)
        except (OSError, json.JSONDecodeError) as e:
            read_errors.append({'file': patient_file.name,
                                'error': f"{type(e).__name__}: {e}"})
            print(f"  ERROR reading {patient_file.name}: {type(e).__name__}: {e}")
            continue

        probe_coding_shapes(bundle, shape_counts)

        legacy_has, legacy_types   = has_cancer_diagnosis_legacy(bundle)
        current_has, current_types = has_cancer_diagnosis(bundle)

        cond_rows = condition_level_diff(bundle)
        if cond_rows:
            condition_disagreements.append({
                'file':       patient_file.name,
                'conditions': cond_rows,
            })

        if legacy_has and current_has:
            agree_cancer.append(patient_file.name)
        elif not legacy_has and not current_has:
            agree_non_cancer.append(patient_file.name)
        elif legacy_has and not current_has:
            legacy_only.append({
                'file':          patient_file.name,
                'legacy_types':  legacy_types,
                'current_types': current_types,
                'conditions':    cond_rows,
            })
        else:
            current_only.append({
                'file':          patient_file.name,
                'legacy_types':  legacy_types,
                'current_types': current_types,
                'conditions':    cond_rows,
            })

    print(f"  Compared {len(patient_files)}/{len(patient_files)} bundles... \nDONE")
    print()

    compared = len(patient_files) - len(read_errors)
    disagreements = len(legacy_only) + len(current_only)

    return {
        'generated_utc':            datetime.now(timezone.utc).isoformat(),
        'directory':                str(patients_path),
        'bundles_found':            len(patient_files),
        'bundles_compared':         compared,
        'read_errors':              read_errors,
        'agree_cancer':             len(agree_cancer),
        'agree_non_cancer':         len(agree_non_cancer),
        'agree_non_cancer_files':   agree_non_cancer,
        'legacy_only':              legacy_only,
        'current_only':             current_only,
        'patient_disagreements':    disagreements,
        'agreement_rate':           (compared - disagreements) / compared if compared else 0.0,
        'condition_disagreements':  condition_disagreements,
        'coding_shapes':            shape_counts,
        'classification_counts':    get_cancer_classification_stats(),
    }


#------------------------------------------------------------------------------


# ===========================================================================
# REPORT
# ===========================================================================

def _format_report(diff):
    """Build the human-readable report text from the diff record."""
    L = []
    add = L.append

    add("=" * 78)
    add("COHORT SELECTOR DIFF — LEGACY vs CURRENT")
    add("=" * 78)
    add("")
    add(f"Generated (UTC): {diff['generated_utc']}")
    add(f"Directory:       {diff['directory']}")
    add(f"Bundles found:   {diff['bundles_found']}")
    add(f"Bundles compared:{diff['bundles_compared']}")
    add("")
    add("LEGACY  = Condition.code.coding[0] by position, no 'codings' key")
    add("          (is_primary_cancer takes its single-code fallback path)")
    add("CURRENT = _select_best_coding('condition') + full 'codings' list")
    add("          (is_primary_cancer runs its multi-coding logic)")
    add("")

    add("-" * 78)
    add("PATIENT-LEVEL AGREEMENT")
    add("-" * 78)
    add(f"  Both say cancer:                  {diff['agree_cancer']}")
    add(f"  Both say non-cancer:              {diff['agree_non_cancer']}")
    add(f"  LEGACY cancer, CURRENT non-cancer:{len(diff['legacy_only']):>4}   (cohort would shrink)")
    add(f"  CURRENT cancer, LEGACY non-cancer:{len(diff['current_only']):>4}   (cohort would grow)")
    add(f"  Disagreements:                    {diff['patient_disagreements']}")
    add(f"  Agreement rate:                   {diff['agreement_rate']*100:.2f}%")
    add("")

    add("-" * 78)
    add("CODING-ARRAY SHAPES (does the positional assumption hold here?)")
    add("-" * 78)
    shapes = diff['coding_shapes']
    add(f"  Conditions examined:              {shapes['conditions_total']}")
    add(f"  With no coding array:             {shapes['conditions_no_coding']}")
    add(f"  With more than one coding:        {shapes['conditions_multi_coding']}")
    add(f"  Where coding[0] is NOT the best:  {shapes['best_not_first']}")
    for key in sorted(k for k in shapes if k.startswith('first_system_')):
        add(f"  coding[0] system = {key[len('first_system_'):]:<12}   {shapes[key]}")
    add("")
    if shapes['conditions_multi_coding'] == 0:
        add("  Every condition in this corpus carries exactly one coding, so the")
        add("  positional read and the system-preference read select the same")
        add("  coding here. The legacy path still differed in what it PASSED:")
        add("  without the 'codings' key, is_primary_cancer skipped its")
        add("  multi-coding exclusion pass entirely. Agreement on this corpus is")
        add("  a property of Synthea output, not evidence that the positional")
        add("  read is safe on data with more than one coding per condition.")
    else:
        add("  Multi-coding conditions are present: the positional read and the")
        add("  system-preference read can select different codings.")
    add("")

    add("-" * 78)
    add("CONDITION-LEVEL DISAGREEMENTS")
    add("-" * 78)
    n_cond = len(diff['condition_disagreements'])
    add(f"  Bundles with at least one condition scored differently: {n_cond}")
    add("")
    for row in diff['condition_disagreements'][:_MAX_DETAIL_ROWS]:
        add(f"  {row['file']}")
        for c in row['conditions']:
            add(f"      display        : {c['display']}")
            add(f"      coding[0].code : {c['legacy_code']}")
            add(f"      best.code      : {c['current_code']}")
            add(f"      codings        : {c['codings']}")
            add(f"      LEGACY -> {c['legacy_verdict']}   CURRENT -> {c['current_verdict']}")
            add("")
    if n_cond > _MAX_DETAIL_ROWS:
        add(f"  ... and {n_cond - _MAX_DETAIL_ROWS} more (full list in the JSON report)")
        add("")

    add("-" * 78)
    add("PATIENTS THE CURRENT SELECTOR WOULD DROP")
    add("-" * 78)
    if not diff['legacy_only']:
        add("  None. Every bundle currently on disk is still a cancer patient")
        add("  under the current selector.")
    else:
        for row in diff['legacy_only'][:_MAX_DETAIL_ROWS]:
            add(f"  {row['file']}")
            add(f"      LEGACY cancer types : {row['legacy_types']}")
            for c in row['conditions']:
                add(f"      condition           : {c['display']}  codings={c['codings']}")
                add(f"                            LEGACY -> {c['legacy_verdict']}   "
                    f"CURRENT -> {c['current_verdict']}")
        if len(diff['legacy_only']) > _MAX_DETAIL_ROWS:
            add(f"  ... and {len(diff['legacy_only']) - _MAX_DETAIL_ROWS} more "
                f"(full list in the JSON report)")
    add("")

    add("-" * 78)
    add("PATIENTS THE CURRENT SELECTOR WOULD ADD")
    add("-" * 78)
    if not diff['current_only']:
        add("  None among the bundles on disk. See the coverage limit below —")
        add("  this direction is largely untestable against a directory that has")
        add("  already been filtered by the legacy selector.")
    else:
        for row in diff['current_only'][:_MAX_DETAIL_ROWS]:
            add(f"  {row['file']}")
            add(f"      CURRENT cancer types: {row['current_types']}")
            for c in row['conditions']:
                add(f"      condition           : {c['display']}  codings={c['codings']}")
                add(f"                            LEGACY -> {c['legacy_verdict']}   "
                    f"CURRENT -> {c['current_verdict']}")
    add("")

    add("-" * 78)
    add("REGISTRY CLASSIFICATION COUNTERS (both selectors, this run)")
    add("-" * 78)
    for key, val in sorted(diff['classification_counts'].items()):
        add(f"  {key:<32} {val}")
    add("")

    if diff['read_errors']:
        add("-" * 78)
        add("BUNDLES THAT COULD NOT BE READ")
        add("-" * 78)
        for row in diff['read_errors']:
            add(f"  {row['file']}: {row['error']}")
        add("")

    add("-" * 78)
    add("COVERAGE LIMIT")
    add("-" * 78)
    add("  This directory holds the survivors of an earlier LEGACY run, so every")
    add("  bundle in it was LEGACY-positive. The diff can therefore only measure")
    add("  'LEGACY kept it, CURRENT drops it'. Patients the LEGACY selector")
    add("  deleted are not on disk and cannot be re-scored; whether CURRENT")
    add("  would have kept any of them is answerable only by regenerating the")
    add("  full Synthea population (File 04) and running both selectors over it")
    add("  before any deletion.")
    add("")
    add("=" * 78)

    return "\n".join(L)


def write_reports(diff):
    """
    Write the text and JSON reports. Returns True on success.

    A write failure is reported and returns False; the caller exits non-zero.
    Nothing else in this script touches the filesystem.
    """
    try:
        with open(_REPORT_TXT, "w") as fh:
            fh.write(_format_report(diff))
        with open(_REPORT_JSON, "w") as fh:
            json.dump(diff, fh, indent=2)
        return True
    except OSError as e:
        print(f"ERROR writing report: {type(e).__name__}: {e}")
        return False


#------------------------------------------------------------------------------


if __name__ == "__main__":

    print()
    print("╔═══════════════════════════════════════════════════════════════════════╗")
    print(f"║              {Project_Name}: COHORT SELECTOR DIFF (READ ONLY)         ║")
    print("╚═══════════════════════════════════════════════════════════════════════╝")
    print()

    _diff = run_diff()

    if _diff is None:
        sys.exit(1)

    print(_format_report(_diff))

    if not write_reports(_diff):
        sys.exit(1)

    print(f"Report written: {_REPORT_TXT}")
    print(f"Detail written: {_REPORT_JSON}")
    print()

    sys.exit(0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  2 12:00:00 2026

@author: ramyalsaffar
"""
