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
# them, and 'tests/test_registries_cancer_codes_and_stage_extraction.py' chains
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


# The not-for-clinical-use framing
#---------------------------------
#
# THIS WORDING IS NEW AND THAT IS A FINDING, NOT A PREFERENCE. The MCP pass was
# told to "find the existing wording and reuse it verbatim". There is none. The
# whole tree was searched -- every ``.py``, ``.md``, ``.toml`` and ``.yml`` --
# for "not for clinical", "clinical use", "research use", "medical advice",
# "not a substitute", "disclaimer", "investigational", "educational" and
# "prototype", and the only hits are Synthea's ``-cs clinician seed`` flag, a
# comment in ``oncotriage/config.py`` about what a clinician has to read, and
# this project's own use of the word "demonstration" to mean a negative
# control. NOTHING in the repository tells a consumer of a match result what it
# is not. ``GET /pipeline/info`` describes the architecture, ``POST /match``
# returns verdicts and a match score, and neither carries a caveat of any kind.
#
# So the string is authored here rather than in the MCP server, for the same
# reason ``CROSS_ENCODER_MODEL`` sits in ``config`` rather than beside its
# loader: THERE ARE ALREADY THREE SURFACES THAT SHOULD CARRY IT -- the MCP
# tools, the FastAPI responses and the Streamlit dashboard -- and a second copy
# is a second copy however carefully it is typed. This module is the leaf of the
# import graph, so all three can reach it without pulling in a path resolution
# or a client.
#
# ONLY THE MCP SERVER READS IT TODAY. The API and the dashboard are NOT changed
# by the pass that added this; widening a response shape is a contract change
# and belongs to a pass that measures it. That is recorded as the top-ranked
# follow-up rather than done quietly here.
#
# WHY THE MCP SURFACE IS THE ONE THAT COULD NOT WAIT: an HTTP endpoint is
# reached by a program somebody wrote on purpose, and a dashboard is read by a
# person who navigated to it. An MCP tool is selected by a model, from a
# description, inside somebody else's conversation -- so the framing has to
# travel WITH the payload, on every call, because the caller who most needs to
# read it never saw the README.
#
# TWO STRINGS, and the split is load-bearing rather than cosmetic. The long one
# is what a RESULT carries: it is read after the fact, next to the numbers it
# qualifies, so it can afford to say what the score is and is not. The short one
# is what a TOOL DESCRIPTION carries: that text is spent from a model's context
# window on every listing whether the tool is called or not, and a caveat long
# enough to be skimmed past is a caveat that does not arrive. Both say the same
# thing; neither is a summary of the other's meaning.

NOT_FOR_CLINICAL_USE = (
    "NOT FOR CLINICAL USE. This is a research and engineering demonstration "
    "built on synthetic Synthea patient records. Its output is a retrieval and "
    "ranking suggestion produced by an automated pipeline that includes a "
    "large language model; it is not medical advice, not a clinical "
    "determination of trial eligibility, and not a substitute for review by a "
    "qualified clinician or the trial's own screening process. Eligibility "
    "verdicts and match scores are unvalidated, may be wrong in either "
    "direction, and must be independently confirmed against the trial record "
    "at ClinicalTrials.gov before any use involving a real patient."
)

NOT_FOR_CLINICAL_USE_SHORT = (
    "NOT FOR CLINICAL USE — research demonstration on synthetic data; "
    "unvalidated automated output, not medical advice."
)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
