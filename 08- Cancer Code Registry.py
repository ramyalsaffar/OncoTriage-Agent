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


# THREE NAMES THIS SHIM USED TO RE-EXPORT AND NO LONGER DOES (pass 20c-2b)
#-------------------------------------------------------------------------
#
# Pass 2a re-exported _REGISTRY, _LAB_REGISTRY and _var, on the rule that no
# name File 08 defined before the move may disappear. All three are gone now,
# deliberately, and the reasoning is here rather than in a commit message
# because "the inventory shrank" is exactly the kind of change that has to be
# argued to be readable.
#
# _REGISTRY and _LAB_REGISTRY are the module's private singleton SLOTS. Before
# pass 2a they lived in the shared namespace, and load_registry()'s
# `global _REGISTRY` wrote back into that namespace, so a later reader saw the
# built registry. After the move the write lands on the MODULE, and the shim's
# binding was a SNAPSHOT taken at shim load: None, permanently, whatever
# load_registry() went on to build. A name that looks like an accessor and is
# guaranteed to be None is worse than no name at all — it is a trap that reads
# as "no registry has been built yet". Nothing consumes either one: grep across
# every file in the repository finds exactly one other mention, File 13 line 65,
# and it is an ASSIGNMENT (`_LAB_REGISTRY = load_lab_registry()`) that shadows
# the name rather than reading it.
#
# USE load_registry() / load_lab_registry(). They are the accessors, they are
# thread-safe, and they see the module's real slot.
#
# _var no longer exists at all. It was a LEAKED LOOP VARIABLE — File 08's
# canonical-order cleanup loop deleted the four temporaries it named and left
# the loop variable itself bound to the string '_seen_canonical'. Pass 2a
# carried the leak into the package verbatim because it was not allowed to
# change File 08's logic; pass 2b adds '_var' to that tuple, so the module
# binds it and then removes it and there is nothing left here to re-export.
#
# tests/test_package_invariants.py holds the pre-2a runtime inventory and an explicit
# list of these three names as the ONLY permitted deletions from it, and checks
# that each one is genuinely absent from the shim's namespace — so the exception
# is exercised rather than merely declared, and a fourth name going missing
# still fails.


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 19 13:01:00 2026

@author: ramyalsaffar
"""
