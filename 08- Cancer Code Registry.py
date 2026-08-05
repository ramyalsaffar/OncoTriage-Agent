# Cancer Code Registry
######################
#
# ITEM 20c, PASS 2a: THIS FILE IS A SHIM.
#
# Every line of it moved to oncotriage/registries/cancer_code_registry.py,
# logic byte-for-byte unchanged. This file survives because Files 05, 06, 11,
# 13, 30, 32, 33, 42 and 43 exec-chain it and read these names out of the
# shared namespace with no import statement of their own.
#
# The name list below is not hand-written. It is the RUNTIME surface of File 08
# as it stood before this pass: the file was exec'd into a throwaway namespace
# and every resulting binding recorded. An ast walk would have been wrong twice
# over — twenty of its names are ANNOTATED assignments (_SNOMED_PRIMARY is one), which a
# `grep "NAME ="` misses entirely, and _seen_canonical is assigned at module
# level but then DELETED by the globals().pop() cleanup loop, so it is not part
# of the surface at all and must not be re-exported.
#
# Explicit, by name, never a star import. A shim whose surface is "whatever the
# module happens to expose" stops being a contract, and this one is the
# contract nine files depend on.


#------------------------------------------------------------------------------


from oncotriage.registries.cancer_code_registry import (
    CancerCodeRegistry,
    OncologyLabRegistry,
    _CANCER_CLASSIFICATION_COUNTS,
    _CANCER_DISPLAY_TERMS,
    _CANONICAL_ORDER,
    _CLINICAL_STATUS_PRIORITY,
    _EXCLUDE_VERIFICATION,
    _ICD10_ALPHA_NON_INVASIVE,
    _ICD10_ALPHA_PRIMARY,
    _ICD10_ALPHA_SECONDARY,
    _ICD10_CONSULT_KEYS,
    _ICD10_C_BLOCK_MAX,
    _ICD10_C_SECONDARY_HI,
    _ICD10_C_SECONDARY_LO,
    _ICD10_D_NEOPLASM_BLOCK_MAX,
    _ICD10_SEED_PRIMARY,
    _NON_INVASIVE_DISPLAY_TERMS,
    _ONCOLOGY_LOINC,
    _ONCOLOGY_LOINC_CODES,
    _REGISTRY_LOCK,
    _SECONDARY_DISPLAY_TERMS,
    _SNOMED_CONSULT_KEYS,
    _SNOMED_PRIMARY,
    _SNOMED_SECONDARY,
    _build_icd10_cancer_sets,
    get_cancer_classification_stats,
    load_lab_registry,
    load_registry,
    logger,
    reset_cancer_classification_stats,
)


#------------------------------------------------------------------------------


# THREE NAMES THAT NEED A WARNING, re-exported anyway
#---------------------------------------------------
#
# _REGISTRY and _LAB_REGISTRY are the module's private singleton SLOTS. Before
# this pass they lived in the shared namespace, and load_registry()'s
# `global _REGISTRY` wrote back into that namespace, so a later reader saw the
# built registry. Now the write lands on the module and this binding is a
# SNAPSHOT taken at shim load — it is None and it stays None.
#
# That difference is safe today and was checked rather than assumed: grep across
# every file in the repository finds exactly one other mention, File 13 line 65,
# and it is an ASSIGNMENT (`_LAB_REGISTRY = load_lab_registry()`) that shadows
# this name rather than reading it. Nothing reads either slot. They are
# re-exported because the inventory contract for this pass is that no name File
# 08 defined disappears, not because anything wants them.
#
# USE load_registry() / load_lab_registry(). They are the accessors, they are
# thread-safe, and they see the module's real slot.
#
# _var is a LEAKED LOOP VARIABLE. File 08 ends its canonical-order build with
#
#     for _var in ('_idx', '_code', '_name', '_seen_canonical'):
#         globals().pop(_var, None)
#
# which deletes the four temporaries it names and leaves _var itself bound to
# the string '_seen_canonical'. That leak is in the module now, verbatim,
# because this pass changes no logic. It is re-exported for the same reason as
# the two above. Adding '_var' to that tuple is a one-line fix and belongs in a
# pass that is allowed to touch File 08's logic.

from oncotriage.registries.cancer_code_registry import (
    _REGISTRY,
    _LAB_REGISTRY,
    _var,
)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 19 13:01:00 2026

@author: ramyalsaffar
"""
