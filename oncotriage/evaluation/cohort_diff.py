"""Cohort selector diff -- LEGACY vs CURRENT. READ ONLY.

Moved out of ``34- Cohort Selector Diff Read Only.py`` by item 20c, pass 3d.
``34- Cohort Selector Diff Read Only.py`` survives as a THIN ENTRY POINT and keeps no
re-export shim: all 15 of its top-level names were grepped against every
``.py``, ``.md``, ``.toml`` and ``.yml`` in the tree and the only hits are the
exec-bootstrap locals every numbered file shares (``_code_dir``, ``_bootstrap``,
``_fh``, ``_os_boot``).

THIS FILE IS DELIBERATELY BUILT THE WAY IT LOOKS WRONG, and the conversion
preserved both halves of that:

  * ``has_cancer_diagnosis`` is NOT re-implemented here. It is imported from
    ``oncotriage.fhir.clean`` -- the live function object the cohort filter will
    actually call -- so this diff measures the code that will build the cohort
    rather than a copy of it that can drift. Under the exec chain that was
    File 05's function read out of the shared namespace; it is the same object.

  * ``_LEGACY_EXCLUDE_VERIFICATION`` STAYS LOCAL and is NOT consolidated with
    the registry's ``exclude_verification``. The pre-fix File 05 defined its own
    copy of that set at module level, which under the exec chain OVERWROTE File
    08's, so the legacy path has to be measured against the set it actually
    used. Reaching for the registry's set here would silently make the LEGACY
    arm agree with the CURRENT arm on any bundle where the two sets differ, and
    the whole file exists to find where the two arms disagree.

WHAT CHANGED, and nothing else did
----------------------------------
1. ``_REPORT_TXT`` / ``_REPORT_JSON`` were module-level ``os.path.join`` calls
   over ``result_fhir_explore_path``, which ``oncotriage/paths.py`` resolves by
   glob on first READ -- so importing this module would have globbed the sibling
   data tree. They are ``report_txt()`` / ``report_json()`` now, resolved on
   first call and cached under a lock. Neither creates the directory: File 34
   did not create it either.

2. ``_CANCER_REGISTRY`` came out of the shared exec namespace, where File 05's
   shim binds it as ``clean.cancer_registry()``. Both sites call that accessor.
   Deliberately NOT ``oncotriage.agent.deps.get_cancer_registry()``: this file
   measures the COHORT FILTER, and ``clean.cancer_registry()`` is the accessor
   that filter uses -- routing it through the agent's seam would let a stub
   installed for an agent test change what this diff reports about a deletion
   pass. Same argument, same direction, as ``clean.cancer_registry()``'s own
   docstring.

3. ``_select_best_coding`` is imported from ``oncotriage.fhir.parser``,
   ``get_cancer_classification_stats`` / ``reset_cancer_classification_stats``
   from ``oncotriage.registries.cancer_code_registry``, ``data_fhir_path``
   through ``oncotriage.paths``, and ``Project_Name`` from ``oncotriage.config``.

4. File 34's ``__main__`` body became ``main()``, returning an exit code instead
   of calling ``sys.exit`` inline, so the entry point owns process exit. The
   three exit paths and their codes are unchanged.

Nothing else moved. Both selectors, the coding-shape probe, the condition-level
diff, the report text and the JSON writer are the line slice of File 34 between
its bootstrap and its ``__main__`` guard, unmodified.
"""

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path

from oncotriage import paths
from oncotriage.config import Project_Name
from oncotriage.fhir.clean import cancer_registry, has_cancer_diagnosis
from oncotriage.fhir.parser import _select_best_coding
from oncotriage.registries.cancer_code_registry import (
    get_cancer_classification_stats,
    reset_cancer_classification_stats,
)


#------------------------------------------------------------------------------


# ===========================================================================
# LAZY PATHS
# ===========================================================================
#
# File 34 built both report paths at module level over result_fhir_explore_path.
# That is a lazy path: reading it globs the sibling results tree, so importing
# this module would have raised on any machine without one.
#
# Locked for consistency with oncotriage/fhir/clean.py, whose accessors this
# module already calls. Nothing here is multi-threaded.

_RESOLVED = {}
_RESOLVE_LOCK = threading.RLock()


def report_txt() -> str:
    """The human-readable report path. Resolved on first call; creates nothing."""
    with _RESOLVE_LOCK:
        if "report_txt" not in _RESOLVED:
            _RESOLVED["report_txt"] = os.path.join(
                paths.result_fhir_explore_path, "cohort_selector_diff.txt")
        return _RESOLVED["report_txt"]


def report_json() -> str:
    """The machine-readable report path. Resolved on first call; creates nothing."""
    with _RESOLVE_LOCK:
        if "report_json" not in _RESOLVED:
            _RESOLVED["report_json"] = os.path.join(
                paths.result_fhir_explore_path, "cohort_selector_diff.json")
        return _RESOLVED["report_json"]


# Configuration
#--------------

# Where the two reports are written (text summary + machine-readable detail)
# File 34 built its two report paths here, over the lazy
# result_fhir_explore_path. They are the two accessors above.

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

        if cancer_registry().is_primary_cancer(condition):
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
        legacy_verdict = cancer_registry().is_primary_cancer({
            'code':    legacy_coding.get('code', ''),
            'display': legacy_coding.get('display', ''),
        })

        # Current view
        best_coding, all_codings = _select_best_coding(coding_list, "condition")
        current_verdict = cancer_registry().is_primary_cancer({
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
    Run both selectors over every bundle in paths.data_fhir_path.

    Returns:
        dict: the full diff record, or None if no bundles were found.
    """
    patients_path = Path(paths.data_fhir_path)

    if not patients_path.exists():
        print(f"ERROR: Patient directory not found: {paths.data_fhir_path}")
        return None

    patient_files = sorted(patients_path.glob("*.json"))

    if not patient_files:
        print(f"ERROR: No patient bundles found in: {paths.data_fhir_path}")
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
        with open(report_txt(), "w") as fh:
            fh.write(_format_report(diff))
        with open(report_json(), "w") as fh:
            json.dump(diff, fh, indent=2)
        return True
    except OSError as e:
        print(f"ERROR writing report: {type(e).__name__}: {e}")
        return False


#------------------------------------------------------------------------------


def main() -> int:
    """Run the diff and write both reports. Returns the process exit code.

    File 34's ``__main__`` block verbatim, with each ``sys.exit(n)`` turned into
    a ``return n`` so the entry point owns process exit and a caller can run the
    diff without ending its own process. The three codes are unchanged: 1 when
    no bundle was found, 1 when a report could not be written, 0 otherwise.
    """
    print()
    print("╔═══════════════════════════════════════════════════════════════════════╗")
    print(f"║              {Project_Name}: COHORT SELECTOR DIFF (READ ONLY)         ║")
    print("╚═══════════════════════════════════════════════════════════════════════╝")
    print()

    _diff = run_diff()

    if _diff is None:
        return 1

    print(_format_report(_diff))

    if not write_reports(_diff):
        return 1

    print(f"Report written: {report_txt()}")
    print(f"Detail written: {report_json()}")
    print()

    return 0


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug  6 2026

@author: ramyalsaffar
"""
