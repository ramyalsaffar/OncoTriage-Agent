# Cancer Code and Stage Extraction Test
#######################################

"""
Cancer Code and Stage Extraction Test

Unit tests for two fixes that widen or narrow the candidate pool silently:

  File 08 — 08- Cancer Code Registry.py
    1. Benign, in-situ and uncertain-behaviour disease is no longer
       classified as primary cancer, on either the coded path (ICD-10
       D00-D49) or the display-term fallback ("benign neoplasm of colon").
    2. C97 (independent multiple primary sites) reaches the primary set.
       The old `0 <= num <= 96` bound dropped it, and the icd10-cm package
       omits the code outright, so it is seeded — see _ICD10_SEED_PRIMARY.
    3. Every classification decision lands in a counter.

  File 10 — 10- Structured Eligibility Extractor.py
    4. _is_negated() picks the NEAREST preceding negation cue, not the
       leftmost one in the look-back window.
    5. A collected stage span covering the whole scale is reported as
       unresolved rather than as a permissive range.
    6. The exclusion block is read for an upper stage bound, guarded
       against non-oncology staging systems (CKD, GVHD, Child-Pugh) and
       against sentences that merely enumerate every stage.

No network, no LLM, no Qdrant. Pure function tests.

Run from terminal (or F5 in Spyder):
    python "33- Cancer Code and Stage Extraction Test.py"

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
_code_dir = "/Users/ramyalsaffar/Ramy/C.V..V/07- LLM Projects/03- Clinical Trial Patient Match/03- Code/"

for _bootstrap in ("01- Imports.py", "02- Utility Functions.py"):
    with open(_code_dir + _bootstrap) as _fh:
        exec(_fh.read(), globals())

exec_chain(
    ["08- Cancer Code Registry.py", "10- Structured Eligibility Extractor.py"],
    caller_file=_code_dir + "33- Cancer Code and Stage Extraction Test.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 08 → 10",
)


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label: str, actual, expected) -> None:
    """Assert equality, record the outcome, never abort the run."""
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected: {expected}\n          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


_REGISTRY_UNDER_TEST = load_registry()


def condition(display: str, code: str = None) -> dict:
    """
    Minimal parsed FHIR condition, shaped like _parse_condition() output.

    Omitting `code` produces a condition with NO coding at all — the only
    input that reaches the display-term fallback (layer 3).
    """
    cond = {"display": display}
    if code is not None:
        cond["codings"] = [{"system_key": "icd10", "code": code, "display": display}]
    return cond


def is_primary(display: str, code: str = None) -> bool:
    """Shorthand for the function under test."""
    return _REGISTRY_UNDER_TEST.is_primary_cancer(condition(display, code))


def span(title: str, inclusion: str = "", exclusion: str = "") -> tuple:
    """(min_stage, max_stage) that enrich_structured_eligibility() writes."""
    trial = {
        "nct_id": "NCT00000001",
        "title": title,
        "eligibility": {
            "inclusion_criteria": inclusion,
            "exclusion_criteria": exclusion,
        },
    }
    enrich_structured_eligibility(trial)
    se = trial["structured_eligibility"]
    return (se["min_stage"], se["max_stage"])


# ===========================================================================
# TEST 1: BENIGN / IN-SITU / UNCERTAIN BEHAVIOUR — DISPLAY FALLBACK
# ===========================================================================
# These conditions carry NO code, so has_recognized_code is False and layer 3
# fires. Every display below contains a term from _CANCER_DISPLAY_TERMS
# ("neoplasm", "carcinoma", "tumor"), which is exactly why the old fallback
# accepted them. Synthea never reaches this path — every Synthea condition is
# SNOMED-coded — but real EHR input and the API endpoint both do.

print("=" * 70)
print("Test 1: Benign / in-situ / uncertain behaviour — display fallback")
print("=" * 70)

check("benign neoplasm of colon",            is_primary("Benign neoplasm of colon"),            False)
check("benign neoplasm of thyroid gland",    is_primary("Benign neoplasm of thyroid gland"),    False)
check("carcinoma in situ of breast",         is_primary("Carcinoma in situ of breast"),         False)
check("in-situ (hyphenated) wording",        is_primary("Adenocarcinoma in-situ of cervix"),    False)
check("uncertain behaviour neoplasm",        is_primary("Neoplasm of uncertain behavior of ovary"), False)
check("unspecified behaviour neoplasm",      is_primary("Neoplasm of unspecified behaviour of skin"), False)
check("borderline malignancy",               is_primary("Borderline malignancy of ovary"),      False)
check("low malignant potential",             is_primary("Serous tumor of low malignant potential"), False)
check("non-invasive carcinoma",              is_primary("Non-invasive papillary carcinoma"),    False)

# Secondary rejection must still work, and must still beat the morphology terms.
check("metastatic carcinoma still rejected", is_primary("Metastatic carcinoma of liver"),       False)
check("secondary neoplasm still rejected",   is_primary("Secondary malignant neoplasm of bone"), False)

# Regressions — real invasive primaries must still pass the fallback.
check("adenocarcinoma of lung kept",         is_primary("Adenocarcinoma of lung"),              True)
check("malignant melanoma of skin kept",     is_primary("Malignant melanoma of skin"),          True)
check("invasive ductal carcinoma kept",      is_primary("Infiltrating duct carcinoma of breast"), True)


# ===========================================================================
# TEST 2: BENIGN / IN-SITU / UNCERTAIN BEHAVIOUR — CODED PATH
# ===========================================================================
# ICD-10-CM D00-D49 is hard-excluded regardless of how the display reads.

print()
print("=" * 70)
print("Test 2: Benign / in-situ / uncertain behaviour — ICD-10 D00-D49")
print("=" * 70)

check("D05.11 DCIS",                is_primary("Intraductal carcinoma in situ of right breast", "D05.11"), False)
check("D09.3 CIS of thyroid",       is_primary("Carcinoma in situ of thyroid", "D09.3"),        False)
check("D12.6 benign colon",         is_primary("Benign neoplasm of colon", "D12.6"),            False)
check("D34 benign thyroid",         is_primary("Benign neoplasm of thyroid gland", "D34"),      False)
check("D3A.00 benign neuroendocrine", is_primary("Benign carcinoid tumor", "D3A.00"),           False)
check("D48.5 uncertain behavior",   is_primary("Neoplasm of uncertain behavior of skin", "D48.5"), False)

# The three sets must be disjoint on the blocks the decision record names.
_primary_norm = _REGISTRY_UNDER_TEST._icd10_primary_norm
check("no D0x code in primary set", any(c.startswith("D0") for c in _primary_norm), False)
check("no D3x code in primary set", any(c.startswith("D3") for c in _primary_norm), False)
check("no D4x code in primary set", any(c.startswith("D4") for c in _primary_norm), False)

# Regressions — invasive C-codes and metastatic C-codes unchanged.
check("C34.10 lung primary kept",   is_primary("Malignant neoplasm of upper lobe", "C34.10"),   True)
check("C3410 dot-free form kept",   is_primary("Malignant neoplasm of upper lobe", "C3410"),    True)
check("C50.911 breast primary kept", is_primary("Malignant neoplasm of breast", "C50.911"),     True)
check("C78.00 secondary rejected",  is_primary("Secondary malignant neoplasm of lung", "C78.00"), False)
check("C7B.00 secondary NE rejected", is_primary("Secondary carcinoid tumor", "C7B.00"),        False)


# ===========================================================================
# TEST 3: C97 — INDEPENDENT MULTIPLE PRIMARY SITES
# ===========================================================================
# C97 was dropped by the old `0 <= num <= 96` bound. It is a PRIMARY code —
# a patient with several independent primaries, not a metastasis.

print()
print("=" * 70)
print("Test 3: C97 reaches the primary set")
print("=" * 70)

check("C97 in the primary lookup set", "C97" in _primary_norm,                                  True)
check("C97 classifies as primary",
      is_primary("Malignant neoplasms of independent (primary) multiple sites", "C97"),         True)
check("C97 is not in the secondary set",
      "C97" in _REGISTRY_UNDER_TEST._icd10_secondary_norm,                                      False)
check("C97 is not in the non-invasive set",
      "C97" in _REGISTRY_UNDER_TEST._icd10_non_invasive_norm,                                   False)


# ===========================================================================
# TEST 4: CLASSIFICATION COUNTERS
# ===========================================================================
# Every terminal decision is recorded. Nothing recovers silently.

print()
print("=" * 70)
print("Test 4: Classification decisions are counted")
print("=" * 70)

reset_cancer_classification_stats()
check("reset zeroes the counters", any(get_cancer_classification_stats().values()),             False)

is_primary("Malignant neoplasm of upper lobe", "C34.10")
check("icd10_primary counted",     get_cancer_classification_stats()["icd10_primary"],          1)

is_primary("Benign neoplasm of colon", "D12.6")
check("rejected_non_invasive_code counted",
      get_cancer_classification_stats()["rejected_non_invasive_code"],                          1)

is_primary("Secondary malignant neoplasm of lung", "C78.00")
check("rejected_secondary_code counted",
      get_cancer_classification_stats()["rejected_secondary_code"],                             1)

is_primary("Benign neoplasm of thyroid gland")
check("rejected_non_invasive_display counted",
      get_cancer_classification_stats()["rejected_non_invasive_display"],                       1)

is_primary("Adenocarcinoma of lung")
check("display_fallback counted",  get_cancer_classification_stats()["display_fallback"],       1)


# ===========================================================================
# TEST 5: NEAREST PRECEDING NEGATION
# ===========================================================================
# _is_negated() used re.search(), which returns the LEFTMOST cue in the
# look-back window — the one most likely to have a clause boundary after it.
# Layer 2 then discarded it and reported "not negated" even when a nearer cue
# governed the match. Choosing the furthest candidate under-detects negation.

print()
print("=" * 70)
print("Test 5: Negation uses the nearest preceding cue")
print("=" * 70)

_NEAREST = "Patients without prior therapy. Cohort B; excluding stage IV disease"
check("nearest cue wins over a boundaried far cue",
      _is_negated(_NEAREST, _NEAREST.index("stage IV")),                                        True)

_TWO_CUES = "Subjects with no prior chemotherapy in patients; must not have stage III disease"
check("nearest cue wins when both are in the window",
      _is_negated(_TWO_CUES, _TWO_CUES.index("stage III")),                                     True)

# Regressions — the clause-boundary layer must still block a lone far cue,
# and an affirmative mention must still read as affirmative.
_BOUNDARY = "Trial With or Without MK-2870 in Participants With Resectable Stage II Cancer"
check("clause boundary still blocks a lone cue",
      _is_negated(_BOUNDARY, _BOUNDARY.index("Stage II")),                                      False)

_PLAIN = "Patients with histologically confirmed stage III disease"
check("no cue in window -> not negated",
      _is_negated(_PLAIN, _PLAIN.index("stage III")),                                           False)


# ===========================================================================
# TEST 6: FULL-RANGE SPANS ARE UNRESOLVED
# ===========================================================================
# _extract_stage_from_text() takes the global min/max of every non-negated
# mention in a block. One stray "Stage I" beside a genuine "Stage IV" widens
# the span to I-IV, which admits everything while claiming to be resolved.

print()
print("=" * 70)
print("Test 6: A full-range span is unresolved, not permissive")
print("=" * 70)

check("stray stage I + stage IV -> unresolved",
      _extract_stage_from_text(
          "Prior adjuvant therapy for stage I disease allowed; enrolling stage IV patients"),   None)
check("explicit stage I to IV -> unresolved",
      _extract_stage_from_text("Stage I to IV disease"),                                        None)
check("explicit stage 0 to IV -> unresolved",
      _extract_stage_from_text("stage 0 to IV"),                                                None)

# Regressions — every span that genuinely constrains must survive intact.
check("stage II-III kept",     _extract_stage_from_text("Stage II to III breast cancer"),       (2, 3))
check("stage I-III kept",      _extract_stage_from_text("Stages I-III colorectal cancer"),      (1, 3))
check("single stage IV kept",  _extract_stage_from_text("Stage IV colorectal cancer"),          (4, 4))
check("metastatic keyword kept", _extract_stage_from_text("Metastatic breast cancer"),          (4, 4))
check("locally advanced kept", _extract_stage_from_text("Locally advanced disease"),            (3, 4))
check("no stage signal",       _extract_stage_from_text("A study of pembrolizumab"),            None)

# A full-range span must NOT fall through to the metastatic keyword — that
# would turn an explicit stage I-IV trial into a stage IV one and hide it
# from the stage I-III patients it named.
check("full-range span does not fall through to 'metastatic'",
      _extract_stage_from_text("Stage I to IV metastatic or non-metastatic disease"),           None)


# ===========================================================================
# TEST 7: EXCLUSION BLOCK SUPPLIES AN UPPER BOUND
# ===========================================================================
# "Stage IV will be excluded" is a cap the inclusion block never states.
# Only a contiguous suffix of the scale yields a bound, and only downward.

print()
print("=" * 70)
print("Test 7: Exclusion criteria are read for an upper bound")
print("=" * 70)

check("exclusion IV on an unresolved trial",
      span("A Study of Drug X", "Histologically confirmed carcinoma",
           "Stage IV disease will be excluded"),                                                (None, 3))
check("exclusion IV tightens a resolved span",
      span("A Study of Drug X", "Stage II or III disease",
           "Patients with stage IV disease"),                                                   (2, 3))
check("exclusion III and IV -> bound 2",
      span("A Study of Drug X", "Stage I to IV disease",
           "Stage III or IV will be excluded"),                                                 (None, 2))
check("exclusion not reaching stage IV is ignored",
      span("A Study of Drug X", "Stage III-IV disease",
           "Prior stage II malignancy"),                                                        (3, 4))
check("exclusion never loosens an existing bound",
      span("A Study of Drug X", "Stage I-II disease",
           "Stage IV disease excluded"),                                                        (1, 2))
check("inclusion floor above the exclusion cap -> unresolved",
      span("A Study of Drug X", "Stage IV disease", "Stage IV disease excluded"),               (None, None))
check("negated exclusion mention contributes nothing",
      span("A Study of Drug X", "Stage II disease",
           "Second primary malignancy except for stage IV skin cancer"),                        (2, 2))
check("empty exclusion block is a no-op",
      span("A Study of Drug X", "Stage II to III disease", ""),                                 (2, 3))

# Title-first ordering is unchanged: the title still wins over inclusion.
check("title still beats inclusion",
      span("A Study in Stage II Breast Cancer", "Stage IV disease", ""),                        (2, 2))


# ===========================================================================
# TEST 8: EXCLUSION GUARDS
# ===========================================================================
# "Stage 4" in criteria text is as often chronic kidney disease or GVHD as it
# is AJCC stage IV. A false upper bound hides a trial from the stage IV
# patients it wants — the same false-ineligible direction the whole module
# exists to prevent.

print()
print("=" * 70)
print("Test 8: Non-oncology staging systems and swept scales are refused")
print("=" * 70)

check("CKD staging ignored",
      span("A Study of Drug X", "Locally advanced rectal cancer",
           "Acute renal insufficiency or stage II to IV chronic renal insufficiency"),          (3, 4))
check("CKD abbreviation ignored",
      span("A Study of Drug X", "Locally advanced rectal cancer",
           "Renal impairment: CKD >= Stage 4"),                                                 (3, 4))
check("GVHD staging ignored",
      span("A Study of Drug X", "Histologically confirmed carcinoma",
           "Any history of Stage 4 skin GVHD or Stage 3 gut/liver GVHD"),                       (None, None))
check("NYHA staging ignored",
      span("A Study of Drug X", "Stage II breast cancer",
           "NYHA stage III or IV congestive heart failure"),                                    (2, 2))
check("a sentence enumerating every stage yields no bound",
      span("A Study of Drug X", "Newly diagnosed B-LLy",
           "For Murphy stage III/IV patients, or stage I/II patients with steroid pretreatment"),
                                                                                                (None, None))

# The guard is disease-specific, never a bare organ word: a cancer of the
# kidney or liver must still read as a cancer stage.
check("stage IV renal cell carcinoma still read",
      span("Stage IV renal cell carcinoma", "", ""),                                            (4, 4))
check("stage III liver cancer still read",
      span("Stage III liver cancer", "", ""),                                                   (3, 3))


# ===========================================================================
# TEST 9: STAGE EXTRACTION COUNTERS
# ===========================================================================

print()
print("=" * 70)
print("Test 9: Stage extraction decisions are counted")
print("=" * 70)

reset_stage_extraction_stats()
check("reset zeroes the counters", any(get_stage_extraction_stats().values()),                  False)

_extract_stage_from_text("Stage I to IV disease")
check("full_range_unresolved counted",
      get_stage_extraction_stats()["full_range_unresolved"],                                    1)

span("A Study of Drug X", "Histologically confirmed carcinoma", "Stage IV disease excluded")
check("exclusion_upper_bound counted",
      get_stage_extraction_stats()["exclusion_upper_bound"],                                    1)

_extract_stage_upper_bound_from_exclusion("Stage 4 chronic kidney disease")
check("non_oncology_stage_skipped counted",
      get_stage_extraction_stats()["non_oncology_stage_skipped"],                               1)

_extract_stage_upper_bound_from_exclusion("Prior stage II malignancy")
check("exclusion_not_suffix counted",
      get_stage_extraction_stats()["exclusion_not_suffix"],                                     1)

_extract_stage_upper_bound_from_exclusion(
    "For Murphy stage III/IV patients, or stage I/II patients with steroid pretreatment")
check("exclusion_scale_swept counted",
      get_stage_extraction_stats()["exclusion_scale_swept"],                                    1)


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug  2 12:00:00 2026

@author: ramyalsaffar
"""
