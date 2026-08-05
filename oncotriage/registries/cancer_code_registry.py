# Cancer Code Registry
######################


#------------------------------------------------------------------------------


"""
cancer_code_registry.py (layman term explanation)
=================================================
Identifies the primary cancer diagnosis from a patient's full condition list.

The problem this solves: FHIR patient records list conditions in encounter order,
not clinical significance. Obesity or hypertension can appear before the actual
cancer. This module finds the real primary cancer reliably.

How it works — three detection layers in priority order:
  1. SNOMED CT exact match  : 52 curated codes covering all Synthea cancer modules
                              + mCODE STU4 root codes
  2. ICD-10-CM exact match  : Complete 2024 release, loaded at startup from
                              the icd10-cm package. Handles both
                              dot-formatted (C34.10) and dot-free (C3410)
                              codes in O(1) lookup.
  3. Display term fallback  : Only fires when code is missing/unknown.
                              Matches on definitional morphology terms
                              (carcinoma, lymphoma, leukemia, etc.).
                              Rejects secondary/metastatic display terms and
                              non-invasive ones (benign, in-situ, uncertain
                              behaviour).

Secondary cancers (metastases) and non-invasive disease (ICD-10 D00-D49:
in-situ, benign, uncertain/unspecified behaviour) are excluded at every layer
before matching — see the per-block decision record in
_build_icd10_cancer_sets(). Diagnoses marked refuted or entered-in-error are
filtered out upstream in the LangGraph agent before this module is called.

When multiple cancer conditions exist, a tiebreaker sorts by:
  confirmed > unconfirmed → active > remission → most recent onset date

Standard: mCODE STU4 (HL7) + ICD-10-CM 2024 (CMS)
Dependency: pip install icd10-cm
Works with: Synthea FHIR bundles (SNOMED) + real EHR data (ICD-10)
"""


#------------------------------------------------------------------------------
#
# ITEM 20c, PASS 2a: this is "08- Cancer Code Registry.py", moved.
#
# The logic below is byte-for-byte what File 08 held, sliced statement by
# statement so that every comment travelled with the thing it documents. The
# only edits are this note, the import block below, and one line of the second
# docstring: it documented the usage as `from cancer_code_registry import ...`
# back when nothing in this project could be imported at all, and now names the
# real package path.
#
# WHAT THIS MODULE NEEDS FROM OUTSIDE, measured with ast rather than grep:
# SYSTEM_KEY_ABSENT and SYSTEM_KEY_UNRECOGNIZED, and nothing else. Everything
# else it touches is the standard library or typing. That is why File 08 was
# the first of the three to move.
#
# `import icd10` STAYS INSIDE _build_icd10_cancer_sets(). It is a third-party
# import in a function body, which the package's no-deferred-import rule does
# not cover and must not: the rule exists so that oncotriage-to-oncotriage
# edges are visible in an import block, and this one is neither. Hoisting it
# would make `import oncotriage.registries.cancer_code_registry` load the full
# ICD-10-CM release, which is exactly the import-time work this package refuses
# to do.
#
# "08- Cancer Code Registry.py" survives as an explicit re-export shim; Files
# 05, 06, 11, 13, 30, 32, 33, 42 and 43 reach these names through it.
#
#------------------------------------------------------------------------------


import logging
import threading
from collections import defaultdict
from typing import Dict, FrozenSet, List, Optional, Set, Tuple

from oncotriage.constants import SYSTEM_KEY_ABSENT, SYSTEM_KEY_UNRECOGNIZED



"""
cancer_code_registry.py
=======================
Production-grade clinical terminology registry.

Contains two registries:

  1. CancerCodeRegistry
     Identifies the primary cancer diagnosis from a patient's full condition
     list. FHIR bundles list conditions in encounter order, not clinical
     significance. This registry finds the real primary cancer reliably.

     Three-layer detection:
       Layer 1 — SNOMED CT exact match   (52 codes, Synthea + mCODE roots)
       Layer 2 — ICD-10-CM exact match   (full 2024 release, O(1))
       Layer 3 — Display term fallback   (only when code is absent/unknown)

     Secondary cancers (metastases) and non-invasive disease (in-situ,
     benign, uncertain behaviour) are excluded at every layer.
     When multiple cancer conditions exist, tiebreaker sorts by:
       confirmed > unconfirmed → active > remission → most recent onset date

     Standard: mCODE STU4 (HL7) + ICD-10-CM 2024 (CMS)
     Works with: Synthea FHIR (SNOMED) + real EHR data (ICD-10)
     Dependency: pip install icd10-cm

  2. OncologyLabRegistry
     Filters patient observations down to the labs and vitals that oncology
     trials actually gate on (organ function, blood counts, tumor markers).

     Uses LOINC codes — the HL7-mandated standard for lab observations used
     by both Synthea and real EHRs. LOINC codes are permanent international
     identifiers; display names are institution-specific and unreliable.

     For each relevant lab, keeps only the most recent value — trial
     eligibility criteria check current organ function, not history.
     When multiple LOINC codes map to the same lab concept (e.g. creatinine
     in serum vs blood), only the most recent reading across all variants is
     kept, preventing duplicate rows in the GPT-4o prompt.

     Standard: LOINC (Regenstrief Institute)
     Works with: Synthea FHIR (LOINC) + real EHR data (LOINC)

Usage:
    from oncotriage.registries.cancer_code_registry import (
        load_registry, load_lab_registry)

    cancer_registry = load_registry()       # CancerCodeRegistry singleton
    lab_registry    = load_lab_registry()   # OncologyLabRegistry singleton
"""

logger = logging.getLogger(__name__)


# ===========================================================================
# SECTION 1 — CANCER CODE REGISTRY
# ===========================================================================

# ---------------------------------------------------------------------------
# SNOMED CT curated primary cancer codes
# ---------------------------------------------------------------------------
# mCODE defines primary cancer as descendants of SNOMED 363346000
# (Malignant neoplastic disease), EXCLUDING descendants of 128462008
# (Secondary malignant neoplastic disease).
#
# Full SNOMED hierarchy traversal requires a live terminology server with a
# UMLS license. Without it, we use a curated set covering all Synthea cancer
# module codes plus mCODE root codes and real-EHR SNOMED codes.
# The ICD-10-CM layer covers real EHR data exhaustively (1,609 codes).
#
# EVERY CODE BELOW HAS BEEN VERIFIED, and the display after each "#" is the
# SNOMED CT FULLY SPECIFIED NAME, copied from the source, not paraphrased.
#
# Two independent sources, because either alone misses a class of defect:
#
#   UMLS Metathesaurus 2025AB, MRCONSO.RRF, SAB=SNOMEDCT_US -- 532,287 distinct
#   codes including retired concepts (SUPPRESS=O). This answers "is this a real
#   SNOMED concept and what does it actually mean". It is the file already on
#   disk for File 09's SNOMED->CUI->MeSH crosswalk, so the check is repeatable
#   without a terminology server.
#
#   The Synthea JAR's own module JSONs. This answers "can this corpus ever emit
#   the code", which UMLS cannot, and it is what caught the defect below that a
#   name check alone would have missed.
#
# WHAT THE AUDIT FOUND, and why the verification standard is now this strict:
#
#   408512008 was listed as "Small cell carcinoma of lung, limited stage". It is
#   "Body mass index 40+ - severely obese (finding)", and Synthea's
#   wellness_encounters module emits it as a Condition. Every severely obese
#   patient in the corpus was therefore classified as having a primary lung
#   cancer. In the 2026-08-03 regeneration this put 48 non-cancer patients into
#   a 1,000-patient cancer cohort (4.8%) -- they retrieved oncology trials and
#   were scored against them. A transcription error in a comment, invisible to
#   every test, for as long as the corpus contained no obese patients (the old
#   "*cancer*" module filter excluded the modules that emit it).
#
#   408513003 was listed as "Small cell carcinoma of lung, extensive stage". It
#   is "Main spoken language Brawa (finding)".
#
#   22 further codes are absent from SNOMEDCT_US entirely -- not retired,
#   ABSENT -- so they are not SNOMED identifiers at all. They matched nothing
#   and could only ever have been dead weight, but they are removed rather than
#   left, because a set that contains 22 invented identifiers cannot be audited
#   by inspection. Example: "Diffuse non-Hodgkin's lymphoma" was listed as
#   20312006; SNOMED's code for it is 109962001.
#
#   Six codes Synthea emits on EVERY stage 2/3/4 lung cancer patient were
#   missing, so a stage IV NSCLC condition classified as 'unclassified' -> not
#   cancer. Those patients only stayed in the cohort because Synthea's lung
#   module also records 254637007 / 254632001 alongside the stage code. That is
#   luck, not design, and it is fixed here.
#
# To re-run this audit: for each code, grep MRCONSO for
# SAB=SNOMEDCT_US and TTY=FN, and grep the JAR's modules/ for the code string.
# A code whose FN disagrees with its comment is a defect, not a wording choice.

_SNOMED_PRIMARY: FrozenSet[str] = frozenset({
    # ── Lung — CONFIRMED, emitted as Conditions by Synthea lung_cancer.json /
    #    veteran_lung_cancer.json. The stage 2/3/4 codes were absent before this
    #    audit; without them a stage IV lung cancer is not a cancer.
    "254637007",       # Non-small cell lung cancer (disorder)
    "424132000",       # Non-small cell carcinoma of lung, TNM stage 1 (disorder)
    "425048006",       # Non-small cell carcinoma of lung, TNM stage 2 (disorder)
    "422968005",       # Non-small cell carcinoma of lung, TNM stage 3 (disorder)
    "423121009",       # Non-small cell carcinoma of lung, TNM stage 4 (disorder)
    "254632001",       # Small cell carcinoma of lung (disorder)
    "67811000119102",  # Primary small cell malignant neoplasm of lung, TNM stage 1 (disorder)
    "67821000119109",  # Primary small cell malignant neoplasm of lung, TNM stage 2 (disorder)
    "67831000119107",  # Primary small cell malignant neoplasm of lung, TNM stage 3 (disorder)
    "67841000119103",  # Primary small cell malignant neoplasm of lung, TNM stage 4 (disorder)
    # NOT FOUND in Synthea, kept as REAL-EHR codes (valid SNOMED FN verified):
    "363358000",       # Malignant neoplasm of lung (disorder)

    # ── Breast — 254837009 CONFIRMED in Synthea breast_cancer.json.
    "254837009",       # Malignant neoplasm of breast (disorder)
    # NOT FOUND in Synthea, kept as REAL-EHR codes:
    "408643008",       # Infiltrating duct carcinoma of breast (disorder)
    "372064008",       # Malignant neoplasm of female breast (disorder)
                       #   RETIRED concept (UMLS TTY=OAF, no active FN). Kept
                       #   deliberately: legacy real-EHR records still carry it,
                       #   and admitting a retired code costs nothing here.

    # ── Colorectal — CONFIRMED in Synthea colorectal_cancer.json.
    "363406005",       # Malignant neoplasm of colon (disorder)
    "109838007",       # Overlapping malignant neoplasm of colon (disorder)
    "93761005",        # Primary malignant neoplasm of colon (disorder)
                       #   NOTE: colon-specific. It was commented "Primary
                       #   malignant neoplasm" and grouped under "mCODE root
                       #   codes", which read as a site-agnostic root. It is not.
    # NOT FOUND in Synthea, kept as REAL-EHR codes:
    "363414004",       # Malignant neoplasm of rectosigmoid junction (disorder)
                       #   (was commented "rectum" — wrong site, right axis)
    "363415003",       # Malignant neoplasm of biliary tract (disorder)
                       #   (was commented "rectosigmoid junction" — wrong site)

    # ── Prostate — 126906006 CONFIRMED in Synthea veteran_prostate_cancer.json.
    "126906006",       # Neoplasm of prostate (disorder)
    # NOT FOUND in Synthea, kept as a REAL-EHR code:
    "399068003",       # Malignant neoplasm of prostate (disorder)

    # ── Haematologic — 91861009 CONFIRMED in acute_myeloid_leukemia.json,
    #    109989006 CONFIRMED in trigger_bone_marrow_transplant.json (a
    #    non-oncology-named module that nonetheless diagnoses a malignancy).
    "91861009",        # Acute myeloid leukemia (disorder)
    "109989006",       # Multiple myeloma (disorder)
    # NOT FOUND in Synthea, kept as REAL-EHR codes:
    "92814006",        # Chronic lymphoid leukemia, disease (disorder)
    "92818009",        # Chronic myeloid leukemia (disorder)
    "118600007",       # Malignant lymphoma (disorder)

    # ── Other solid tumours. NONE are emitted by Synthea — this JAR only
    #    produces lung, breast, colorectal, prostate, AML and myeloma. All are
    #    kept as REAL-EHR codes, each verified against SNOMEDCT_US FN.
    "363418001",       # Malignant neoplasm of pancreas (disorder)
    "372003004",       # Primary malignant neoplasm of pancreas (disorder)
    "363443007",       # Malignant neoplasm of ovary (disorder)
    "363478007",       # Malignant neoplasm of thyroid gland (disorder)
    "372244006",       # Malignant melanoma (disorder)
    "393563007",       # Glioblastoma multiforme (disorder)
                       #   (was commented "Malignant neoplasm of brain" — the
                       #   code is a primary brain malignancy either way)
    "126952004",       # Neoplasm of brain (disorder)

    # ── mCODE root codes. NOT FOUND in Synthea by construction: Synthea codes
    #    the specific disease, never the root. Kept because mCODE-conformant
    #    real records use them and because the ICD-10 layer has no equivalent.
    "363346000",       # Malignant neoplastic disease (disorder)  ← mCODE root
})

# SNOMED secondary/metastatic — excluded from primary selection.
# Per mCODE: exclude all descendants of 128462008.
#
# Same verification standard as _SNOMED_PRIMARY: displays are SNOMEDCT_US fully
# specified names. Three of these carried the wrong SITE in their comment
# (liver/lung/brain against bone/brain/bronchus); the site was wrong but the
# axis was right, so all three stay and only the comments changed. One entry,
# 315006, was not a SNOMED code at all and is removed.
_SNOMED_SECONDARY: FrozenSet[str] = frozenset({
    "128462008",   # Metastatic malignant neoplasm (disorder)  ← mCODE exclusion root
    # CONFIRMED in Synthea:
    "94260004",    # Metastatic malignant neoplasm to colon (disorder)
                   #   colorectal_cancer.json, stage IV
    "94503003",    # Metastatic malignant neoplasm to prostate (disorder)
                   #   veteran_prostate_cancer.json. ADDED by this audit: Synthea
                   #   emits it and it was in neither set, so it fell through to
                   #   'unclassified' instead of being counted as a rejection.
    # NOT FOUND in Synthea, kept as REAL-EHR codes:
    "94222008",    # Metastatic malignant neoplasm to bone (disorder)
    "94225005",    # Metastatic malignant neoplasm to brain (disorder)
    "94229004",    # Metastatic malignant neoplasm to bronchus of left upper lobe (disorder)
})

# Display-term fallback — fires ONLY when code is missing/unknown.
# Morphological terms present in every coding system's cancer labels.
_CANCER_DISPLAY_TERMS: Tuple[str, ...] = (
    "carcinoma", "adenocarcinoma", "cancer", "malignant", "malignancy",
    "neoplasm", "lymphoma", "leukemia", "leukaemia", "melanoma",
    "sarcoma", "myeloma", "glioma", "glioblastoma", "mesothelioma",
    "blastoma",
)

# Display terms that mean SECONDARY (metastatic) disease. Rejected before
# _CANCER_DISPLAY_TERMS is consulted — "metastatic carcinoma" contains
# "carcinoma" and would otherwise read as a primary.
_SECONDARY_DISPLAY_TERMS: Tuple[str, ...] = (
    "metastatic", "metastasis", "metastases", "secondary",
)

# Display terms that mean NON-INVASIVE disease — benign, in-situ
# (pre-invasive) or of uncertain/borderline behaviour.
#
# Rejected before _CANCER_DISPLAY_TERMS for the same structural reason as
# the secondary terms: "benign neoplasm of colon" contains "neoplasm" and
# "carcinoma in situ of breast" contains "carcinoma", so both used to be
# classified as primary cancer by the fallback layer. Neither is an
# invasive malignancy, and interventional oncology trials overwhelmingly
# require invasive disease, so admitting them produces a patient whose
# "primary cancer" cannot enrol on anything the pipeline retrieves for it.
#
# This is the display-side twin of the D00-D49 ICD-10 decision recorded in
# _build_icd10_cancer_sets(); the two layers must agree or the same disease
# would classify differently depending on whether it arrived coded.
_NON_INVASIVE_DISPLAY_TERMS: Tuple[str, ...] = (
    "benign", "in situ", "in-situ", "insitu",
    "noninvasive", "non-invasive", "non invasive",
    "uncertain behavior", "uncertain behaviour",
    "unspecified behavior", "unspecified behaviour",
    "borderline malignancy", "low malignant potential",
    "premalignant", "pre-malignant",
)

# Which system_key values may be looked up in which code sets.
#
# These are facts about the system_key vocabulary, not tunables, so they live
# here as named constants. The two non-system values themselves come from
# '01- Imports.py' (SYSTEM_KEY_ABSENT / SYSTEM_KEY_UNRECOGNIZED) rather than
# being spelled again here: File 07 produces them, this file branches on them,
# and one literal in two files is two literals the day one of them is renamed.
#
# SYSTEM_KEY_ABSENT (Coding.system absent) is in BOTH sets, deliberately. It is not
# laxity: is_primary_cancer()'s own backward-compatible path manufactures
# {"system_key": "unknown", "code": <bare code>} when a caller hands it a
# condition with no "codings" key at all, and File 06 and File 13 both rely on
# that path. Refusing to look "unknown" up would silently stop classifying
# every caller that passes a bare code.
#
# SYSTEM_KEY_UNRECOGNIZED (Coding.system present but not a URI File 07 knows)
# is in NEITHER. The system field is a positive statement that the code belongs
# to some other vocabulary, so matching it against SNOMED or ICD-10 is comparing
# digits, not concepts. That comparison is how MEDCIN 315006 --
# "antiphospholipid antibody syndrome with hemorrhagic disorder" -- sat in
# _SNOMED_SECONDARY as "Secondary malignant neoplasm of bone" without anything
# failing.
#
# The recognised-but-irrelevant keys (loinc, rxnorm, cpt, hcpcs) are in neither
# set either, and are counted separately from the unrecognised ones: a LOINC
# code reaching a cancer lookup is a routing mistake, a proprietary code
# reaching one is ordinary real-EHR input.
_SNOMED_CONSULT_KEYS: FrozenSet[str] = frozenset({"snomed", SYSTEM_KEY_ABSENT})
_ICD10_CONSULT_KEYS:  FrozenSet[str] = frozenset({"icd10cm", "icd10", SYSTEM_KEY_ABSENT})


# Which classification path decided a condition. Nothing is silently
# recovered or silently dropped: every terminal decision in
# is_primary_cancer() increments exactly one counter, readable after a run
# via get_cancer_classification_stats().
#
# Not lock-protected. Increments are advisory instrumentation, and the
# registry itself stays immutable and thread-safe; a lost increment under
# concurrent FastAPI requests costs a count, never a classification.
_CANCER_CLASSIFICATION_COUNTS: Dict[str, int] = {
    "snomed_primary":                 0,  # layer 1 hit
    "icd10_primary":                  0,  # layer 2 hit
    "display_fallback":               0,  # layer 3 hit (no code recognized)
    "rejected_secondary_code":        0,  # SNOMED/ICD-10 metastasis code
    "rejected_non_invasive_code":     0,  # ICD-10 D00-D49
    "rejected_secondary_display":     0,  # layer 3 — metastatic wording
    "rejected_non_invasive_display":  0,  # layer 3 — benign/in-situ wording
    "unclassified":                   0,  # coded, but no layer matched

    # --- system-awareness instrumentation -------------------------------
    # Counted PER CONDITION, at the moment a code match decides the verdict,
    # when the deciding coding carried no system at all (system_key
    # "unknown"). Such a match is a digits-only match: nothing asserted which
    # vocabulary the code came from. A corpus where this is large is a corpus
    # whose classifications rest on the permissive path.
    "decided_on_unknown_system":      0,

    # Counted PER CODING, once, for a coding that was consulted against NO set
    # because its system is present and unrecognised (SYSTEM_KEY_UNRECOGNIZED).
    # Before the
    # system_key gate these codings WERE looked up in the SNOMED and ICD-10
    # sets, purely on their digits.
    "skipped_unmapped_coding":        0,

    # Counted PER CODING, once, for a coding whose system IS recognised but is
    # not a cancer-code system (loinc, rxnorm, cpt, hcpcs). Kept separate from
    # skipped_unmapped_coding because this one means a non-Condition code
    # reached a Condition classifier -- a routing bug, not ordinary input.
    "skipped_other_system_coding":    0,
}


def get_cancer_classification_stats() -> Dict[str, int]:
    """Copy of the is_primary_cancer() decision counters."""
    return dict(_CANCER_CLASSIFICATION_COUNTS)


def reset_cancer_classification_stats() -> None:
    """Zero the classification counters (per-run reporting, tests)."""
    for key in _CANCER_CLASSIFICATION_COUNTS:
        _CANCER_CLASSIFICATION_COUNTS[key] = 0

# verificationStatus values that mean the diagnosis is retracted or erroneous.
_EXCLUDE_VERIFICATION: FrozenSet[str] = frozenset({"refuted", "entered-in-error"})

# clinical_status priority for tiebreaking (lower = higher priority).
_CLINICAL_STATUS_PRIORITY: Dict[str, int] = {
    "active": 0, "recurrence": 1, "relapse": 2, "remission": 3,
    "inactive": 4, "resolved": 5, "unknown": 6,
}

# ---------------------------------------------------------------------------
# ICD-10-CM hand-curated inputs
# ---------------------------------------------------------------------------
# Five category prefixes and four block boundaries. They are the ONLY ICD-10
# facts in this file a human typed -- everything else is derived from the
# icd10-cm release at import time by _build_icd10_cancer_sets(). They therefore
# carry exactly the gap _SNOMED_PRIMARY carried before it was audited: a
# category assigned to the wrong set, or a boundary off by one, is a comment
# nothing checks.
#
# '42- Cancer Code Registry Audit Test.py' now checks all nine, so the CATEGORY
# LINES BELOW ARE PARSED BY THAT TEST and their format is fixed:
#
#       #   <CATEGORY> = <official title> -> <SET>
#
# with SET one of PRIMARY / SECONDARY / NON_INVASIVE. The title is compared
# against the installed icd10-cm release and against UMLS, and the SET against
# the constant the category actually appears in. Edit the title or the set and
# the test fails; add a category without a line and the test fails.
#
# Alpha-suffix categories. int(c[1:3]) cannot parse these, so the block-range
# logic in _build_icd10_cancer_sets() never sees them and each has to be
# assigned by hand:
#
#   C4A = Merkel cell carcinoma -> PRIMARY
#   C7A = Malignant neuroendocrine tumors -> PRIMARY
#   C7B = Secondary neuroendocrine tumors -> SECONDARY
#   D3A = Benign neuroendocrine tumors -> NON_INVASIVE
#
# C7A was commented "Malignant carcinoid tumors" until this audit. That is the
# ICD-9 wording; ICD-10-CM titles the category "Malignant neuroendocrine
# tumors". The set assignment was right and the classification never changed,
# but the comment was describing a different revision of the standard.
#
# D3A takes the same NON_INVASIVE decision as the rest of D00-D49. Before it was
# listed it fell through as unparsed and was dropped entirely.
_ICD10_ALPHA_PRIMARY: FrozenSet[str] = frozenset({"C4A", "C7A"})
_ICD10_ALPHA_SECONDARY: FrozenSet[str] = frozenset({"C7B"})
_ICD10_ALPHA_NON_INVASIVE: FrozenSet[str] = frozenset({"D3A"})

# Codes ICD-10-CM defines that the icd10-cm package's table omits:
#
#   C97 = Malignant neoplasms of independent (primary) multiple sites -> PRIMARY
#
# C97 is absent from icd10.codes entirely -- the installed release's C
# categories stop at C96 -- so widening the block range cannot admit it and it
# is seeded. Seeding is logged in _build_icd10_cancer_sets(); if a later package
# release adds C97 the seed becomes a no-op and the log says so.
#
# It is a PRIMARY code, not a secondary one: a patient with several independent
# primary tumours, not a metastasis. Note for anyone re-verifying it -- C97 is
# also absent from UMLS under SAB=ICD10CM (that subset stops at C96 too), and is
# confirmed instead under SAB=ICD10, the WHO edition. File 42 records which
# source confirmed each category for exactly this reason.
_ICD10_SEED_PRIMARY: FrozenSet[str] = frozenset({"C97"})

# ICD-10-CM chapter 2 block boundaries (CMS FY2024). External-standard facts,
# so they stay here as named constants rather than moving to config. All four
# are asserted against the installed release by File 42.
_ICD10_C_BLOCK_MAX          = 97   # C00-C97 is the whole malignant range
_ICD10_C_SECONDARY_LO       = 77   # C77-C79 secondary / metastatic sites
_ICD10_C_SECONDARY_HI       = 79
_ICD10_D_NEOPLASM_BLOCK_MAX = 49   # D00-D49 is the rest of chapter 2;
                                   # D50+ is chapter 3 (blood/immune)


def _build_icd10_cancer_sets() -> Tuple[Set[str], Set[str], Set[str]]:
    """
    Build ICD-10-CM primary, secondary and non-invasive code sets from the
    full icd10-cm 2024 release (95,622 codes, no external API).

    Every chapter-2 block is assigned to exactly one of the three sets, and
    anything that lands in none of them is counted and logged. No block is
    dropped by falling off the end of a range test.

    PRIMARY — invasive malignancy (per mCODE STU4):
        C00-C76   malignant neoplasms of primary / ill-defined sites
        C4A       Merkel cell carcinoma            (alpha-suffix)
        C7A       malignant carcinoid tumors       (alpha-suffix)
        C80-C96   malignant neoplasms without site / hematologic
        C97       malignant neoplasms of independent multiple primary sites.
                  DECISION: INCLUDED. Previously dropped in silence by a
                  `0 <= num <= 96` bound — C97 codes a patient with several
                  independent PRIMARY tumours, not a metastasis, so dropping
                  it made exactly the multi-primary patients invisible to
                  the ICD-10 layer. The block range now reaches C97, and
                  because the icd10-cm package's table omits the code
                  outright it is additionally seeded from
                  _ICD10_SEED_PRIMARY.

    SECONDARY — metastatic, hard-excluded from primary selection:
        C77-C79   secondary malignant neoplasms of lymph nodes / distant sites
        C7B       secondary neuroendocrine tumors  (alpha-suffix)

    NON-INVASIVE — not primary cancer, hard-excluded. Recorded explicitly
    because two of these blocks used to be in the PRIMARY set:
        D00-D09   carcinoma in situ.
                  DECISION: EXCLUDED (was: primary). In-situ disease is
                  pre-invasive; interventional oncology trials overwhelmingly
                  require invasive disease, so treating DCIS/CIS as the
                  patient's primary cancer matches them against trials they
                  cannot enrol on.
        D10-D36   benign neoplasms.
                  DECISION: EXCLUDED (was: in no set at all — the same
                  outcome, but unrecorded). Now explicit and counted.
        D37-D48   neoplasms of uncertain behavior.
                  DECISION: EXCLUDED (was: primary). "Uncertain behavior"
                  means invasion has not been established; asserting an
                  invasive malignancy on that basis is a guess.
        D3A       benign neuroendocrine tumors     (alpha-suffix).
                  DECISION: EXCLUDED with the rest of D10-D36.
        D49       neoplasms of unspecified behavior.
                  DECISION: EXCLUDED, same reasoning as D37-D48.

    Returns:
        (primary, secondary, non_invasive) — raw, dot-formatted code strings.
        All three are empty when the icd10-cm package is missing.
    """
    try:
        import icd10
    except ImportError:
        logger.error(
            "icd10-cm package not installed. Run: pip install icd10-cm\n"
            "ICD-10 layer will be empty; only SNOMED and display fallback active."
        )
        return set(), set(), set()

    primary: Set[str] = set()
    secondary: Set[str] = set()
    non_invasive: Set[str] = set()

    # Codes that reach neither set. Counted, never silently discarded.
    skipped: Dict[str, int] = {
        "c_block_unparsed":   0,   # C-code whose block digits are not numeric
        "c_block_out_of_range": 0, # C-code above C97 (none exist in FY2024)
        "d_block_unparsed":   0,   # D-code whose block digits are not numeric
        "d_outside_chapter2": 0,   # D50+ — blood/immune, not a neoplasm
    }

    for code_str in icd10.codes:
        c = code_str.upper().replace(".", "")

        if c and c[0] == "C" and len(c) >= 3:
            prefix3 = c[:3]
            if prefix3 in _ICD10_ALPHA_PRIMARY:
                primary.add(code_str)
                continue
            if prefix3 in _ICD10_ALPHA_SECONDARY:
                secondary.add(code_str)
                continue

            try:
                num = int(c[1:3])
            except ValueError:
                skipped["c_block_unparsed"] += 1
                logger.debug(f"ICD-10 C-code with non-numeric block, skipped: {code_str!r}")
                continue

            if _ICD10_C_SECONDARY_LO <= num <= _ICD10_C_SECONDARY_HI:
                secondary.add(code_str)
            elif 0 <= num <= _ICD10_C_BLOCK_MAX:
                primary.add(code_str)
            else:
                skipped["c_block_out_of_range"] += 1
                logger.debug(
                    f"ICD-10 C-code above C{_ICD10_C_BLOCK_MAX}, skipped: {code_str!r}"
                )

        elif c and c[0] == "D" and len(c) >= 3:
            if c[:3] in _ICD10_ALPHA_NON_INVASIVE:
                non_invasive.add(code_str)
                continue

            try:
                num = int(c[1:3])
            except ValueError:
                skipped["d_block_unparsed"] += 1
                logger.debug(f"ICD-10 D-code with non-numeric block, skipped: {code_str!r}")
                continue

            if 0 <= num <= _ICD10_D_NEOPLASM_BLOCK_MAX:
                # D00-D49: in-situ, benign, uncertain and unspecified
                # behavior. See the block decisions in the docstring.
                non_invasive.add(code_str)
            else:
                skipped["d_outside_chapter2"] += 1

    # Seed codes the package's table omits. Logged either way so a future
    # package release that adds them is visible rather than silently absorbed.
    seeded = {s for s in _ICD10_SEED_PRIMARY
              if s.upper().replace(".", "") not in
              {p.upper().replace(".", "") for p in primary}}
    if seeded:
        primary |= seeded
        logger.info(
            f"ICD-10-CM: seeded {sorted(seeded)} into the primary set — absent "
            f"from the installed icd10-cm table, valid in ICD-10-CM 2024."
        )
    else:
        logger.debug(
            f"ICD-10-CM: seed set {sorted(_ICD10_SEED_PRIMARY)} already present "
            f"in the package table; no seeding needed."
        )

    logger.info(
        f"ICD-10-CM loaded: {len(primary)} primary, {len(secondary)} secondary, "
        f"{len(non_invasive)} non-invasive (in-situ/benign/uncertain) codes."
    )

    unexpected = {k: v for k, v in skipped.items() if v and k != "d_outside_chapter2"}
    if unexpected:
        logger.warning(f"ICD-10-CM codes assigned to no set: {unexpected}")
    logger.debug(f"ICD-10-CM non-neoplasm D-codes ignored: {skipped['d_outside_chapter2']}")

    return primary, secondary, non_invasive


class CancerCodeRegistry:
    """
    Immutable cancer code registry. Built once at startup. Thread-safe.

    Identifies the primary cancer diagnosis from a FHIR condition list using
    a three-layer detection system: SNOMED → ICD-10-CM → display fallback.
    Secondary/metastatic codes are excluded at every layer.
    """

    def __init__(self):
        (icd10_primary_raw,
         icd10_secondary_raw,
         icd10_non_invasive_raw) = _build_icd10_cancer_sets()

        self.snomed_primary: FrozenSet[str] = _SNOMED_PRIMARY
        self.snomed_secondary: FrozenSet[str] = _SNOMED_SECONDARY
        self.display_terms: Tuple[str, ...] = _CANCER_DISPLAY_TERMS
        self.secondary_display_terms: Tuple[str, ...] = _SECONDARY_DISPLAY_TERMS
        self.non_invasive_display_terms: Tuple[str, ...] = _NON_INVASIVE_DISPLAY_TERMS
        self.exclude_verification: FrozenSet[str] = _EXCLUDE_VERIFICATION
        self.clinical_status_priority: Dict[str, int] = _CLINICAL_STATUS_PRIORITY

        # Pre-normalized O(1) lookup sets.
        # Both "C34.10" and "C3410" normalize to "C3410" → same bucket.
        self._icd10_primary_norm: FrozenSet[str] = frozenset(
            c.upper().replace(".", "") for c in icd10_primary_raw
        )
        self._icd10_secondary_norm: FrozenSet[str] = frozenset(
            c.upper().replace(".", "") for c in icd10_secondary_raw
        )
        self._icd10_non_invasive_norm: FrozenSet[str] = frozenset(
            c.upper().replace(".", "") for c in icd10_non_invasive_raw
        )

        logger.info(
            f"CancerCodeRegistry ready: {len(self.snomed_primary)} SNOMED + "
            f"{len(self._icd10_primary_norm)} ICD-10-CM primary codes indexed "
            f"({len(self._icd10_non_invasive_norm)} non-invasive codes rejected)."
        )


    def is_primary_cancer(self, condition: Dict) -> bool:
        """
        Return True if this parsed FHIR condition represents a primary cancer.

        Multi-coding aware: checks ALL codings from the FHIR condition, not just
        the best-selected code. A condition is primary cancer if ANY coding matches
        a primary cancer set and NONE of its codings match a secondary set.

        SYSTEM-AWARE. Every code lookup is gated on the coding's system_key, so
        a code is only ever compared against the code system it came from:

            system_key            SNOMED sets    ICD-10 sets
            ------------------    -----------    -----------
            "snomed"              consulted      no
            "icd10cm" / "icd10"   no             consulted
            ABSENT                consulted      consulted    (system absent)
            UNRECOGNIZED          no             no           (system present,
                                                                not recognised)
            loinc/rxnorm/cpt/...  no             no

        Layers 1 and 2 used to compare c_code against both sets without ever
        reading system_key, so any code that happened to share digits with a
        SNOMED concept id matched it. That is a digits-only match across
        unrelated vocabularies, and it is how MEDCIN 315006 lived in the SNOMED
        secondary set undetected. "unknown" stays permissive because this
        method's own backward-compatible path (below) manufactures it for a
        bare code with no system, and File 06 and File 13 both depend on that.

        Detection layers (applied per coding, subject to the gate above):
          Layer 1 -- SNOMED exact match   : Synthea + SNOMED-coded real EHRs
          Layer 2 -- ICD-10-CM match      : real EHRs (handles with/without dots)

        Display fallback (Layer 3): fires only when ALL codings are absent,
        unknown, or unrecognized. Uses morphology terms in display text.

        has_recognized_code is set by ANY non-empty code, INCLUDING one the
        system gate skipped. A condition carrying a proprietary code is not an
        uncoded condition, so it must not fall through to the display-term
        fallback; keeping the flag system-blind keeps Layer 3 as conservative
        as it was.

        Hard exclusions, applied to ALL codings before any primary match:
          - secondary/metastatic codes (a metastasis, not a primary)
          - non-invasive codes: ICD-10 D00-D49, i.e. in-situ, benign,
            uncertain and unspecified behavior. See _build_icd10_cancer_sets()
            for the per-block decision record.
        Either one rejects the condition regardless of its other codings.

        Layer 3 applies the same two exclusions in wording form
        (_SECONDARY_DISPLAY_TERMS, _NON_INVASIVE_DISPLAY_TERMS) before it
        consults the morphology terms, so an uncoded "benign neoplasm of
        colon" or "carcinoma in situ of breast" is rejected for the same
        reason its coded twin is.

        Every return path increments a counter in
        _CANCER_CLASSIFICATION_COUNTS — see get_cancer_classification_stats().

        Backward compatible: if "codings" key is absent (older parsed data),
        falls back to single "code" field with original behavior.
        """
        codings = condition.get("codings", [])
        display = (condition.get("display") or "").lower().strip()

        # If no multi-coding data, fall back to single code field (backward compat)
        if not codings:
            code = (condition.get("code") or "").strip()
            codings = [{"system_key": SYSTEM_KEY_ABSENT, "code": code, "display": display}]

        # Pass 0: resolve each coding's system ONCE, and count the codings the
        # system gate will refuse to look up.
        #
        # Done here rather than inside Pass 1 and Pass 2 for two reasons: the
        # two passes would otherwise resolve and count the same coding twice,
        # and Pass 1 can return early, which would leave the skip counters
        # dependent on which verdict was reached. Every coding is accounted for
        # before any decision is made.
        prepared = []
        for c in codings:
            c_code = (c.get("code") or "").strip()
            c_key = (c.get("system_key") or SYSTEM_KEY_ABSENT).strip().lower()
            consult_snomed = c_key in _SNOMED_CONSULT_KEYS
            consult_icd10 = c_key in _ICD10_CONSULT_KEYS

            if c_code and not (consult_snomed or consult_icd10):
                if c_key == SYSTEM_KEY_UNRECOGNIZED:
                    _CANCER_CLASSIFICATION_COUNTS["skipped_unmapped_coding"] += 1
                else:
                    _CANCER_CLASSIFICATION_COUNTS["skipped_other_system_coding"] += 1
                logger.debug(
                    f"Code {c_code!r} not consulted: system_key {c_key!r} is "
                    f"neither a SNOMED nor an ICD-10 system"
                )

            prepared.append({
                "code":           c_code,
                "norm":           c_code.upper().replace(".", ""),
                "key":            c_key,
                "consult_snomed": consult_snomed,
                "consult_icd10":  consult_icd10,
            })

        def _note_unknown(entry):
            """Record that this verdict rested on a coding with no system."""
            if entry["key"] == SYSTEM_KEY_ABSENT:
                _CANCER_CLASSIFICATION_COUNTS["decided_on_unknown_system"] += 1

        # Pass 1: Hard exclude if ANY coding is secondary/metastatic or
        # non-invasive. A single such code decides the condition, even if
        # another coding maps to a primary site.
        for c in prepared:
            if c["consult_snomed"] and c["code"] in self.snomed_secondary:
                _CANCER_CLASSIFICATION_COUNTS["rejected_secondary_code"] += 1
                _note_unknown(c)
                logger.debug(f"Not primary — secondary/metastatic code {c['code']!r}")
                return False
            if c["consult_icd10"] and c["norm"] in self._icd10_secondary_norm:
                _CANCER_CLASSIFICATION_COUNTS["rejected_secondary_code"] += 1
                _note_unknown(c)
                logger.debug(f"Not primary — secondary/metastatic code {c['code']!r}")
                return False
            if c["consult_icd10"] and c["norm"] in self._icd10_non_invasive_norm:
                _CANCER_CLASSIFICATION_COUNTS["rejected_non_invasive_code"] += 1
                _note_unknown(c)
                logger.debug(
                    f"Not primary — non-invasive (in-situ/benign/uncertain) "
                    f"code {c['code']!r}"
                )
                return False

        # Pass 2: Check if ANY coding matches a primary cancer set.
        has_recognized_code = False
        for c in prepared:
            c_code = c["code"]

            if not c_code or c_code.lower() in ("unknown", "none"):
                continue

            # Set BEFORE the system gate, on purpose. A coding skipped for
            # system mismatch is still a coded condition, and letting it reach
            # the display-term fallback would make Layer 3 fire on input it was
            # never meant to see.
            has_recognized_code = True

            # Layer 1: SNOMED exact match
            if c["consult_snomed"] and c_code in self.snomed_primary:
                _CANCER_CLASSIFICATION_COUNTS["snomed_primary"] += 1
                _note_unknown(c)
                return True

            # Layer 2: ICD-10-CM normalized match
            if c["consult_icd10"] and c["norm"] in self._icd10_primary_norm:
                _CANCER_CLASSIFICATION_COUNTS["icd10_primary"] += 1
                _note_unknown(c)
                return True

        # Pass 3: Display fallback -- only when no coding was recognized
        if not has_recognized_code:
            if any(sec in display for sec in self.secondary_display_terms):
                _CANCER_CLASSIFICATION_COUNTS["rejected_secondary_display"] += 1
                logger.debug(f"Not primary — secondary wording in display {display!r}")
                return False
            if any(nb in display for nb in self.non_invasive_display_terms):
                _CANCER_CLASSIFICATION_COUNTS["rejected_non_invasive_display"] += 1
                logger.debug(
                    f"Not primary — non-invasive wording in display {display!r}"
                )
                return False
            if any(term in display for term in self.display_terms):
                _CANCER_CLASSIFICATION_COUNTS["display_fallback"] += 1
                logger.debug(f"Primary via display fallback (no usable code): {display!r}")
                return True

        _CANCER_CLASSIFICATION_COUNTS["unclassified"] += 1
        return False


    def sort_key(self, condition: Dict) -> Tuple:
        """
        Tiebreaker sort key. Sort ascending — lowest tuple wins.

          1. verificationStatus: confirmed=0, other=1
          2. clinical_status:    active=0 ... unknown=6
          3. onset_date:         most recent first; missing last

        Usage:
            primary = sorted(cancer_conditions, key=registry.sort_key)[0]
        """
        ver = (condition.get("verification_status") or "unknown").lower()
        clin = (condition.get("clinical_status") or "unknown").lower()
        onset = condition.get("onset_date") or ""

        return (
            0 if ver == "confirmed" else 1,
            self.clinical_status_priority.get(clin, 6),
            self._invert_date(onset),
        )

    @staticmethod
    def _invert_date(onset: str) -> str:
        """
        Invert date string so ascending sort yields most-recent-first.
        Missing or unparseable dates sort last ('9999-99-99').
        """
        if not onset or onset == "unknown":
            return "9999-99-99"
        try:
            parts = onset[:10].split("-")
            y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
            return f"{9999 - y:04d}-{99 - m:02d}-{99 - d:02d}"
        except (ValueError, IndexError):
            return "9999-99-99"


# ===========================================================================
# SECTION 2 — ONCOLOGY LAB REGISTRY
# ===========================================================================

# ---------------------------------------------------------------------------
# LOINC codes for oncology-trial-relevant observations
# ---------------------------------------------------------------------------
# LOINC (Logical Observation Identifiers Names and Codes) is the HL7-mandated
# international standard for lab and vital observations. Both Synthea and real
# EHRs use LOINC codes for observations. LOINC codes are permanent — once
# assigned, a code never changes meaning (Regenstrief Institute guarantee).
#
# This set covers every lab category that oncology trials gate on:
#   Hematologic  : ANC, WBC, platelets, hemoglobin, lymphocytes
#   Renal        : creatinine, GFR
#   Hepatic      : bilirubin, AST, ALT, alkaline phosphatase
#   Cardiac      : LVEF, QTc
#   Tumor markers: PSA, CA-125, CEA, AFP, CA 19-9, LDH
#   Other        : ECOG, INR, albumin
#
# Source: LOINC database (loinc.org), verified against Synthea value sets
# and common real-EHR oncology lab panels.
#
# Several labs have multiple LOINC codes for the same clinical concept
# (e.g. creatinine in serum vs blood, MDRD vs CKD-EPI eGFR). All variants
# are included to maximize coverage. Deduplication by canonical name in
# filter_relevant_observations() ensures only one row per lab reaches GPT-4o.

# Maps LOINC code -> canonical display name used in the GPT-4o prompt.
# The display name overrides the institution-specific EHR display name,
# giving GPT-4o consistent terminology regardless of data source.
_ONCOLOGY_LOINC: Dict[str, str] = {
    # ── Hematologic
    "26499-4":  "Neutrophils (ANC)",    # Absolute Neutrophil Count — primary LOINC
    "751-8":    "Neutrophils (ANC)",    # ANC — alternate LOINC (some systems use this)
    "6690-2":   "WBC",
    "777-3":    "Platelets",
    "718-7":    "Hemoglobin",
    "731-0":    "Lymphocytes",
    # ── Renal
    "2160-0":   "Creatinine",           # Creatinine in Serum or Plasma — primary LOINC
    "38483-4":  "Creatinine",           # Creatinine in Blood — alternate LOINC
    "33914-3":  "GFR",                  # eGFR — MDRD equation
    "62238-1":  "GFR",                  # eGFR — CKD-EPI equation (newer standard)
    # ── Hepatic
    "1975-2":   "Bilirubin (total)",
    "14629-0":  "Bilirubin (direct)",
    "1920-8":   "AST",
    "1742-6":   "ALT",
    "6768-6":   "Alkaline Phosphatase",
    # ── Cardiac
    "18041-2":  "LVEF",                 # Left Ventricular Ejection Fraction by US
    "77021-7":  "QTc interval",
    # ── Tumor markers
    "2857-1":   "PSA",                  # Prostate-Specific Antigen
    "10334-1":  "CA-125",              # Ovarian cancer marker
    "85319-2":  "CEA",                  # Colorectal cancer marker
    "1834-1":   "AFP",                  # Liver/testicular cancer marker
    "24108-3":  "CA 19-9",             # Pancreatic cancer marker
    "2532-0":   "LDH",                  # Lymphoma/melanoma marker
    # ── Performance / coagulation / nutrition
    "89243-0":  "ECOG Performance Status",
    "6301-6":   "INR",                  # INR — Platelet poor plasma
    "34714-6":  "INR",                  # INR — whole blood (alternate LOINC)
    "1751-7":   "Albumin",
}

# Pre-built frozenset for O(1) membership test.
_ONCOLOGY_LOINC_CODES: FrozenSet[str] = frozenset(_ONCOLOGY_LOINC.keys())

# Canonical display → position index for clinical category sort order.
# Built once at module load. Controls output order in GPT-4o prompt:
# hematologic first, tumor markers last (matching clinical report convention).
_CANONICAL_ORDER: Dict[str, int] = {}
_seen_canonical: set = set()

for _idx, (_code, _name) in enumerate(_ONCOLOGY_LOINC.items()):
    if _name not in _seen_canonical:
        _CANONICAL_ORDER[_name] = len(_seen_canonical)
        _seen_canonical.add(_name)

for _var in ('_idx', '_code', '_name', '_seen_canonical'):
    globals().pop(_var, None)


class OncologyLabRegistry:
    """
    Immutable LOINC-based filter for oncology-relevant lab observations.
    Built once at startup. Thread-safe.

    Filters a patient's full observation list (potentially hundreds of routine
    vitals and labs) down to the subset that oncology trials actually gate on.

    Two-stage deduplication:
      Stage 1 — Per LOINC code: keep most recent observation per code.
      Stage 2 — Per canonical name: when multiple LOINC codes map to the same
                lab concept (e.g. creatinine in serum vs blood), keep only the
                most recent across all variants. Prevents duplicate rows in the
                GPT-4o prompt.

    Result: at most one row per canonical lab name, most recent value.
    """

    def __init__(self):
        self.loinc_codes: FrozenSet[str] = _ONCOLOGY_LOINC_CODES
        self.loinc_display: Dict[str, str] = _ONCOLOGY_LOINC
        self.canonical_order: Dict[str, int] = _CANONICAL_ORDER
        logger.info(
            f"OncologyLabRegistry ready: {len(self.loinc_codes)} LOINC codes "
            f"({len(self.canonical_order)} unique lab concepts) across 7 clinical categories."
        )

    @staticmethod
    def _date_sort_key(date_str: Optional[str]) -> str:
        """
        Sort key for ISO 8601 date strings. Descending sort = most recent first.
        Handles full datetime strings (2023-11-01T10:30:00+00:00) by taking
        only the YYYY-MM-DD prefix. Missing or unknown dates sort last.
        """
        if not date_str or date_str == "unknown":
            return "0000-00-00"
        return date_str[:10]   # YYYY-MM-DD prefix — lexicographic sort is correct for ISO dates

    def filter_relevant_observations(self, observations: List[Dict]) -> List[Dict]:
        """
        Filter and deduplicate observations to oncology-relevant labs.

        Stage 1: Filter to LOINC codes in the oncology set; skip any observation
                 with no value (useless for threshold comparison).
        Stage 2: Per LOINC code — keep the most recent observation only.
        Stage 3: Per canonical name — when alternate LOINC codes exist for the
                 same lab concept, keep only the most recent across all variants.
        Stage 4: Sort by clinical category order (hematologic first, tumor markers last).

        Args:
            observations: list of dicts from _parse_observation() in 07-_FHIR_Parser.py
                          Keys: code (str), display (str), value (int|float|str|None),
                                unit (str|None), date (str|None)

        Returns:
            List of observation dicts, each with an added 'canonical_display' key.
            At most one entry per canonical lab name. Empty list if no relevant
            observations found.
        """
        if not observations:
            return []

        # Stage 1+2: filter relevant, group by LOINC code, keep most recent per code
        grouped: Dict[str, List[Dict]] = defaultdict(list)
        for obs in observations:
            code = (obs.get("code") or "").strip()
            if code in self.loinc_codes and obs.get("value") is not None:
                grouped[code].append(obs)

        if not grouped:
            return []

        per_loinc: List[Dict] = []
        for code, obs_list in grouped.items():
            most_recent = sorted(
                obs_list,
                key=lambda x: self._date_sort_key(x.get("date")),
                reverse=True
            )[0]
            enriched = dict(most_recent)
            enriched["canonical_display"] = self.loinc_display[code]
            per_loinc.append(enriched)

        # Stage 3: dedup per canonical name — keep most recent across all LOINC variants
        # Example: patient has both 2160-0 and 38483-4 (both = "Creatinine").
        # Without this step, GPT-4o would see two "Creatinine" rows.
        by_canonical: Dict[str, List[Dict]] = defaultdict(list)
        for obs in per_loinc:
            by_canonical[obs["canonical_display"]].append(obs)

        result: List[Dict] = []
        for obs_list in by_canonical.values():
            best = sorted(
                obs_list,
                key=lambda x: self._date_sort_key(x.get("date")),
                reverse=True
            )[0]
            result.append(best)

        # Stage 4: sort by clinical category order
        result.sort(
            key=lambda x: self.canonical_order.get(x.get("canonical_display", ""), 999)
        )

        return result


    def filter_relevant_genomic_variants(
            self,
            cancer_genomic_variants: List[Dict],
        ) -> List[Dict]:
        """
        Filter and deduplicate mCODE genomic variant Observations.

        Deduplication: per gene symbol, keep most recent observation only.
        Multiple sequencing runs on the same gene are common after treatment;
        most recent result is the clinically relevant one.

        Filters out Absent/Negative results — these are noise for trial
        matching. Trials gate on the PRESENCE of a biomarker, not its absence.
        Absent results are still handled by Rule 6 (not_evaluable default).

        Args:
            cancer_genomic_variants: list of dicts from _parse_mcode_genomic_variant()

        Returns:
            Deduplicated list, one entry per gene symbol, most recent,
            presence-only. Empty list if no relevant variants found.
        """
        if not cancer_genomic_variants:
            return []

        _ABSENT_VALUES = frozenset({
            "absent", "negative", "not detected", "not found",
            "wild type", "wild-type", "no mutation detected",
        })

        # Filter to present/positive results only
        present = []
        for v in cancer_genomic_variants:
            result = (v.get('result_value') or v.get('interpretation') or '').lower().strip()
            if result and result in _ABSENT_VALUES:
                continue
            present.append(v)

        if not present:
            return []

        # Deduplicate by gene symbol — keep most recent per gene
        by_gene: Dict[str, List[Dict]] = defaultdict(list)
        for v in present:
            gene = (v.get('gene_symbol') or 'unknown').strip()
            by_gene[gene].append(v)

        result_list: List[Dict] = []
        for _, variants in by_gene.items():
            most_recent = sorted(
                variants,
                key=lambda x: self._date_sort_key(x.get('date')),
                reverse=True
            )[0]
            result_list.append(most_recent)

        return result_list


    def filter_relevant_procedures(self, procedures: List[Dict]) -> List[Dict]:
        """
        Deduplicate procedures by display name, keeping the most recent
        occurrence of each procedure type.

        All procedures are clinically relevant for oncology trials (prior
        chemotherapy, radiation, surgery are standard eligibility gates).
        There is no LOINC filter — all procedure types are kept.

        Args:
            procedures: list of dicts from _parse_procedure() in 07-_FHIR_Parser.py
                        Keys: code (str), display (str), date (str|None), status (str)

        Returns:
            List of unique procedure dicts (one per display name), most recent
            date per type. Sorted most-recent-first. Procedures with missing or
            'unknown' display are excluded. Empty list if no valid procedures.
        """
        if not procedures:
            return []

        # Group by normalized display name
        grouped: Dict[str, List[Dict]] = defaultdict(list)
        for proc in procedures:
            display = (proc.get("display") or "").strip()
            if display and display.lower() != "unknown":
                grouped[display.lower()].append(proc)

        if not grouped:
            return []

        # Keep most recent per procedure type
        result: List[Dict] = []
        for _, proc_list in grouped.items():
            most_recent = sorted(
                proc_list,
                key=lambda x: self._date_sort_key(x.get("date")),
                reverse=True
            )[0]
            result.append(most_recent)

        # Sort most-recent-first
        result.sort(
            key=lambda x: self._date_sort_key(x.get("date")),
            reverse=True
        )
        return result


# ===========================================================================
# SECTION 3 — SINGLETONS
# ===========================================================================

_REGISTRY: Optional[CancerCodeRegistry] = None
_LAB_REGISTRY: Optional[OncologyLabRegistry] = None
_REGISTRY_LOCK = threading.Lock()

def load_registry() -> CancerCodeRegistry:
    """
    Return the singleton CancerCodeRegistry, building it on first call.
    Thread-safe: registry is immutable after construction.
    """
    global _REGISTRY
    with _REGISTRY_LOCK:
        if _REGISTRY is None:
            _REGISTRY = CancerCodeRegistry()
    return _REGISTRY


def load_lab_registry() -> OncologyLabRegistry:
    """
    Return the singleton OncologyLabRegistry, building it on first call.
    Thread-safe: registry is immutable after construction.
    """
    global _LAB_REGISTRY
    with _REGISTRY_LOCK:
        if _LAB_REGISTRY is None:
            _LAB_REGISTRY = OncologyLabRegistry()
    return _LAB_REGISTRY


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Feb 19 13:01:00 2026

@author: ramyalsaffar
"""
