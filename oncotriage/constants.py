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


# The absent-date stand-in
#-------------------------
#
# What 'oncotriage/fhir/parser.py' writes into a record's date field when the
# source resource carries no date at all -- for ECOG, when an Observation has
# neither effectiveDateTime nor effectivePeriod.start.
#
# IT IS NOT SYSTEM_KEY_ABSENT AND MUST NOT BE MERGED WITH IT, despite the two
# spelling the same six characters. That one is a statement about Coding.system;
# this one is a statement about a date. They are equal today by coincidence, and
# a reader who folded them together would make a change to either one silently
# change the other -- the collapse the comment above records the cost of.
#
# IT HAS A CROSS-MODULE READER, which is the whole reason it is a name rather
# than a literal. 'oncotriage/storage/database_logger.py' compares the selected
# ECOG observation's date against it to decide whether inferences.ecog_date
# gets a date or NULL; see the schema comment there. Two literals that must
# agree and can drift make that comparison silently stop firing, and the column
# would then hold the string "unknown" where a reader expects a date -- which
# sorts AFTER every ISO date, so the oldest possible reading would rank as the
# newest. Same failure shape as the cross-encoder checkpoint literal.
#
# THE PARSER SPELLS THIS LITERALLY AT ITS OTHER DATE SITES (medication
# start_date, condition onset_date) and those are deliberately left alone: none
# of them has a reader that branches on the value, so none of them can drift
# into a wrong answer. Converting them is a sweep with no test able to see it,
# and it is recorded as a follow-up rather than done here.
UNKNOWN_DATE = "unknown"


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


# The AJCC clinical M category LOINC
#-----------------------------------
#
# 21907-1 "Distant metastases.clinical [Class] Cancer" — the AJCC clinical M
# category. A fact about an external standard, so a named constant rather than
# configuration; what puts it HERE rather than inline is that TWO modules must
# agree on the spelling and neither can see the other's private constant:
#
#   oncotriage/fhir/parser.py     ROUTES the Observation by this code, out of
#                                 the general `observations` pool and into
#                                 `cancer_metastasis_observations`.
#   oncotriage/extraction/stage.py SELECTS it back out of that list, by the same
#                                 code, to read cM1 as stage IV.
#
# If those two spellings ever disagree the rule does not fail — it silently
# never fires. The observation is routed somewhere the stage extractor does not
# look, `extract_patient_stage()` falls through to the condition-display tiers,
# and a patient with recorded distant metastasis is staged from their diagnosis
# text or not at all. Nothing raises and no counter moves: exactly the shape
# CROSS_ENCODER_MODEL and BM25_SPARSE_MODEL_NAME were each given one name to
# remove. `tests/test_extraction_stage_m_category.py` section 6 asserts by AST
# that the literal "21907-1" appears exactly once in the package.
#
# `extraction/stage.py` may import this module and stay honest about doing no
# work at import: this file imports nothing, opens nothing and resolves nothing.
#
# The three OTHER codes in parser.py's `_METASTASIS_LOINCS` deliberately do NOT
# get a shared name. Only one module reads each of them, so there is nothing to
# drift against — and 44667-4 in particular must NOT be treated as an M
# category by the stage rule: it is "Site of distant metastasis in Breast
# tumor", whose 290 corpus values are all "None (qualifier value)". It shares
# the M *axis* with this code and carries an entirely different vocabulary.
LOINC_AJCC_CLINICAL_M = "21907-1"


#------------------------------------------------------------------------------


# The ECOG selection vocabulary
#------------------------------
#
# Which path `oncotriage/fhir/parser.py:_select_ecog_performance_status()` took
# to produce (or to refuse to produce) a patient's performance status. It is
# written into `patient_data['ecog_performance_status']['selection']`, tallied
# in `ECOG_SELECTION_COUNTS`, stored per row in `inferences.ecog_selection`,
# rendered by `oncotriage/agent/patient.py`, broken down by the dashboard's
# performance tab, and it is what separates the three states `ecog_value IS
# NULL` collapses into.
#
# IT LIVES HERE BECAUSE ITS CONSUMERS SIT IN FOUR SUBPACKAGES THAT MAY NOT
# IMPORT EACH OTHER. `storage` may not import `fhir`; `dashboard` importing the
# FHIR parser to read four strings would be the wrong direction and would drag
# a parser into a Streamlit rerun. This module imports nothing at all, so all
# four can share one spelling. Same argument SYSTEM_KEY_ABSENT carries above,
# and the same argument `database_logger.py` writes out beside `criteria_split`
# for why that column has no CHECK constraint.
#
# AND THE DRIFT IT PREVENTS HAD ALREADY HAPPENED. Before this block existed the
# parser wrote 'most_recent_on_or_before_reference_date' while
# `oncotriage/dashboard/tabs/performance.py` keyed its explanation table on
# 'most_recent_on_or_before_reference' -- no trailing `_date`. So the single
# most common path in the whole pipeline rendered as "unrecognised path -- not
# one of the five this pipeline writes", on every dashboard, for every corpus,
# and nothing failed. `tests/test_storage_query_layer.py` seeded the same
# truncated spelling. A literal in two places with no failure when they
# disagree is the shape this project keeps removing; the fix is one owner, not
# one corrected copy.

# USABLE -- `value` carries a real grade.
ECOG_SELECTION_MOST_RECENT = "most_recent_on_or_before_reference_date"
ECOG_SELECTION_UNDATED_SINGLE = "undated_single"

# NOTHING WAS ON FILE. `observations_found` is 0. Distinct from every member
# below, which all mean "an observation existed and could not be used".
ECOG_SELECTION_NONE_RECORDED = "none_recorded"

# PRESENT BUT UNUSABLE. `value` is None and `observations_found` >= 1.
ECOG_SELECTION_ALL_AFTER_REFERENCE = "all_after_reference_date"
ECOG_SELECTION_UNDATED_AMBIGUOUS = "undated_ambiguous"

# PRESENT BUT UNUSABLE -- and unlike the two above, the observation is
# well-formed and inside the snapshot. It describes a person who did not yet
# have the disease.
#
# An ECOG measured before the primary cancer was diagnosed is a performance
# status for the patient's PRE-CANCER life. Rendering it as "this patient's
# ECOG" is a false statement to the model, and it is false in the direction
# that matters: a pre-diagnosis reading is systematically better than the
# post-diagnosis one, so it makes an unwell patient look eligible. Measured on
# the 1,000-patient corpus: 23 patients, gaps of up to 28 years, one of them an
# ECOG 1 recorded in 1997 offered as the performance status of a colon cancer
# diagnosed in 2025.
#
# THIS IS NOT A STALENESS FLOOR AND MUST NOT BE WIDENED INTO ONE. A general
# "too old to trust" cutoff was measured and REJECTED: it demoted 96% of the
# scored corpus and recovered nothing. An old POST-diagnosis score still
# describes the right person with the right disease and is kept.
ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS = "all_before_primary_diagnosis"

# THE CLOSED SET, IN THE ORDER A BREAKDOWN SHOULD READ: the two usable paths,
# then absence, then the three present-but-unusable ones. A consumer may branch
# on it exhaustively; anything outside it is a defect in the producer, not a
# sixth case to guess at.
ECOG_SELECTION_VALUES = (
    ECOG_SELECTION_MOST_RECENT,
    ECOG_SELECTION_UNDATED_SINGLE,
    ECOG_SELECTION_NONE_RECORDED,
    ECOG_SELECTION_ALL_AFTER_REFERENCE,
    ECOG_SELECTION_UNDATED_AMBIGUOUS,
    ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS,
)

# The two that mean "a grade was produced". Everything else means it was not,
# which is the partition `ecog_unavailable_rate` and the dashboard both need
# and which neither should have to re-derive from a list of negatives.
ECOG_SELECTION_USABLE = (
    ECOG_SELECTION_MOST_RECENT,
    ECOG_SELECTION_UNDATED_SINGLE,
)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Tue Aug  5 2026

@author: ramyalsaffar
"""
