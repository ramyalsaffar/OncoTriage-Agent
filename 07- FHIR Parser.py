# FHIR Parser for Patients Data
###############################

"""
FHIR Patient Parser
Extracts structured patient data from Synthea FHIR bundles
"""

# ITEM 20c, PASS 2b: THIS FILE IS A SHIM.
#
# Every definition moved to oncotriage/fhir/parser.py, logic byte-for-byte
# unchanged. This file survives because Files 05, 17, 25, 26, 38, 39, 40 and 45
# exec-chain it and read these names out of the shared namespace with no import
# statement of their own.
#
# The name list below is not hand-written. It is the RUNTIME surface of File 07
# as it stood before this pass: the file was exec'd into a throwaway namespace,
# with only the free names it reads pre-seeded, and every resulting binding
# recorded. An ast walk would have been wrong -- eleven of these are ANNOTATED
# assignments (_SYSTEM_URI_TO_KEY, _ECOG_LOINC_CODE, _MCODE_STAGE_LOINCS and
# eight more), which a `grep "NAME ="` misses entirely and which ast.Assign does
# not see either, because they are ast.AnnAssign.
#
# 45 names, and none of them is a temporary: File 07 has no cleanup loop, unlike
# File 08.
#
# Explicit, by name, never a star import. A shim whose surface is "whatever the
# module happens to expose" stops being a contract, and this one is the contract
# eight files depend on.
#
# WHAT THIS FILE NO LONGER CARRIES: the module's own imports. File 07 used to
# take json, re, Path, Counter, datetime, relativedelta and eleven typing names
# out of the shared exec namespace -- File 01's block -- and the two coding
# sentinels and three utility functions out of Files 01 and 02. The package
# module imports all of them itself, so the parser now works with no exec chain
# at all. The chain still binds those names for everybody else; nothing here
# depends on it.


#------------------------------------------------------------------------------


from oncotriage.fhir.parser import (
    BIRTH_DATE_PRECISION_COUNTS,
    DEMOGRAPHIC_SOURCE_COUNTS,
    ECOG_SELECTION_COUNTS,
    ECOG_VALUE_SHAPE_COUNTS,
    _ACTIVE_ALLERGY_STATUSES,
    _ACTIVE_MED_STATUSES,
    _COMPONENT_GENE_STUDIED,
    _COMPONENT_GENOMIC_SOURCE,
    _COMPONENT_HGVS_CDNA,
    _COMPONENT_HGVS_PROTEIN,
    _CONDITION_STATUS_PRIORITY,
    _ECOG_LOINC_CODE,
    _ECOG_LOINC_INTERPRETATION_CODE,
    _ECOG_LOINC_PANEL_CODE,
    _ECOG_MAX_GRADE,
    _ECOG_MIN_GRADE,
    _EXCLUDE_ALLERGY_VERIFICATION,
    _EXCLUDE_OBS_STATUSES,
    _EXCLUDE_PROC_STATUSES,
    _HISTORICAL_MED_STATUSES,
    _MCODE_GENOMIC_VARIANT_LOINC,
    _MCODE_STAGE_LOINCS,
    _METASTASIS_LOINCS,
    _SYSTEM_PREFERENCE,
    _SYSTEM_URI_TO_KEY,
    _US_CORE_DETAILED,
    _US_CORE_OMB_CATEGORY,
    _US_CORE_TEXT,
    _calculate_age,
    _condition_sort_key,
    _parse_allergy,
    _parse_condition,
    _parse_demographics,
    _parse_ecog_observation,
    _parse_mcode_genomic_variant,
    _parse_mcode_stage_observation,
    _parse_medication,
    _parse_medication_statement,
    _parse_observation,
    _parse_procedure,
    _read_us_core_category,
    _select_best_coding,
    _select_ecog_performance_status,
    load_all_patients,
    parse_fhir_bundle,
)


#------------------------------------------------------------------------------


# THE FOUR COUNTERS ARE SHARED OBJECTS, NOT COPIES
#-------------------------------------------------
# BIRTH_DATE_PRECISION_COUNTS, DEMOGRAPHIC_SOURCE_COUNTS, ECOG_VALUE_SHAPE_COUNTS
# and ECOG_SELECTION_COUNTS are Counter INSTANCES, so the names above and the
# module's own names point at the same four objects. load_all_patients() clears
# and fills them through the module's names; a reader going through this shim
# sees every one of those mutations.
#
# That is the behaviour File 39 depends on -- it reads the counters after
# calling load_all_patients() -- and it is only true because they are mutable.
# A shim re-exporting an int or a str would be re-exporting a snapshot, which is
# the trap _REGISTRY was in File 08's shim before pass 2b removed it. Nothing
# here is in that category: the other 41 names are frozensets, dicts, strings,
# ints and functions that are never rebound.


#------------------------------------------------------------------------------


# Call the parser
#----------------
# STILL RUNNABLE, and now runnable in one more way than before.
#
# Under exec_chain this block does not fire at all: exec_chain sets
# __name__ = "_exec_chain_" while exec'ing, which is what lets every numbered
# file be both a script and a library.
#
# Run directly (python "07- FHIR Parser.py") it fires, and it now WORKS.
# Before this pass it could not: the definitions above it needed Dict, Counter,
# relativedelta and a dozen more names that only File 01 binds, so a direct run
# died at the first annotated assignment. The definitions are in the package now
# and import their own dependencies.
#
# data_fhir_path is the one name this block reads from outside itself, and it is
# the ONLY place in all 1,491 lines of the original File 07 that named a path.
# It is resolved from the shared exec namespace when there is one -- a Spyder
# session that has already run File 01 -- and from oncotriage.paths otherwise,
# and WHICH of the two was used is printed, because a fallback whose path is not
# logged is the defect this project exists to remove.
if __name__ == '__main__':

    _fhir_dir = globals().get("data_fhir_path")
    if _fhir_dir is None:
        from oncotriage.paths import data_fhir_path as _fhir_dir
        print(f"[07] data_fhir_path resolved from oncotriage.paths: {_fhir_dir}")
    else:
        print(f"[07] data_fhir_path taken from the shared namespace: {_fhir_dir}")

    # Load all patients
    all_patients = load_all_patients(_fhir_dir)
    print(f"\nTotal patients loaded: {len(all_patients)}\n")

    # TEMPORARY: Use only first 100 for testing
    # all_patients = all_patients[:100]
    # print(f"Using subset for testing: {len(all_patients)} patients\n")


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Feb  9 19:42:33 2026

@author: ramyalsaffar
"""
