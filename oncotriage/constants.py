"""Coding-system sentinels shared across the pipeline.

This module imports NOTHING — not from the project, not from third-party
packages, not from the standard library. That is deliberate: it is the leaf of
the package's import graph, so any module may import it without pulling in a
path resolution, a credential read or a client.

Moved out of ``01- Imports.py`` by item 20c. The reasoning below is the
original, and the "producer and consumers sit in different exec_chain chains"
argument is now doubly true: they sit in different MODULES too.
"""


# Coding system keys
#-------------------
#
# The two system_key values that are NOT a named code system. They live here,
# not in the file that produces them, because producer and consumers sit in
# different exec_chain chains and cannot see each other's module constants:
# '07- FHIR Parser.py' writes them, '08- Cancer Code Registry.py' branches on
# them, and '33- Cancer Code and Stage Extraction Test.py' chains
# 01 -> 02 -> 08 -> 10 with no File 07 in it at all. File 01 is the only file
# every bootstrap loads first, so it is the only place all three can share one
# spelling -- and File 01 now gets them from here. Everything else about coding
# systems -- the URI table (_SYSTEM_URI_TO_KEY) and the per-resource preference
# order -- stays in File 07, because those are facts about FHIR parsing rather
# than a vocabulary other files must agree with.
#
# The distinction between the two is load-bearing:
#
#   SYSTEM_KEY_ABSENT ("unknown")
#       Coding.system is absent or empty. FHIR permits this, and this codebase
#       MANUFACTURES it: File 08's no-codings fallback and File 13 both build
#       {"system_key": "unknown", "code": ...} from a bare code with no system.
#       Nothing is asserted about which vocabulary the code came from, so
#       lookups treat it PERMISSIVELY and try every set.
#
#   SYSTEM_KEY_UNRECOGNIZED ("unmapped")
#       Coding.system is present but is not a URI File 07 knows: a proprietary
#       or local system, a registry URI, MEDCIN, an EHR's internal dictionary.
#       That is a POSITIVE STATEMENT that the code belongs to some other
#       vocabulary, so looking it up in SNOMED or ICD-10 compares digits, not
#       concepts, and must not happen.
#
# These were one value until they were split. Collapsing them is how MEDCIN
# 315006 came to sit in File 08's SNOMED secondary set, labelled "Secondary
# malignant neoplasm of bone", matching on its digits alone.
#
# "unknown" keeps its spelling because consumers predating the split compare
# against that literal.
SYSTEM_KEY_ABSENT = "unknown"
SYSTEM_KEY_UNRECOGNIZED = "unmapped"


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
