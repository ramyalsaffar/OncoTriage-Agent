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


def condition(display: str, code: str = None, system: str = "icd10cm") -> dict:
    """
    Minimal parsed FHIR condition, shaped like _parse_condition() output.

    Omitting `code` produces a condition with NO coding at all — the only
    input that reaches the display-term fallback (layer 3).

    `system` is the File 07 system_key the coding is labelled with, and it is
    now load-bearing: is_primary_cancer() consults the SNOMED sets only for
    "snomed"/"unknown" and the ICD-10 sets only for "icd10cm"/"icd10"/
    "unknown". This helper used to hardcode "icd10" for EVERY code including
    SNOMED ones, which was harmless only for as long as the lookups ignored
    system_key. It defaults to "icd10cm" because every code passed to it in
    Tests 2-4 is an ICD-10-CM code; SNOMED codes must go through
    snomed_condition() or pass system="snomed" explicitly.
    """
    cond = {"display": display}
    if code is not None:
        cond["codings"] = [{"system_key": system, "code": code, "display": display}]
    return cond


def is_primary(display: str, code: str = None, system: str = "icd10cm") -> bool:
    """Shorthand for the function under test."""
    return _REGISTRY_UNDER_TEST.is_primary_cancer(condition(display, code, system))


def snomed_condition(code: str, display: str) -> dict:
    """
    Condition carrying a SNOMED coding, the shape every Synthea bundle produces.
    """
    return {
        "code": code,
        "display": display,
        "codings": [{"system_key": "snomed", "code": code, "display": display}],
    }


def is_primary_snomed(code: str, display: str) -> bool:
    """is_primary_cancer() over a SNOMED-coded condition."""
    return _REGISTRY_UNDER_TEST.is_primary_cancer(snomed_condition(code, display))


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
# TEST 10: SNOMED CODES SYNTHEA ACTUALLY EMITS
# ===========================================================================
# The regression this test exists for: _SNOMED_PRIMARY listed 408512008 as
# "Small cell carcinoma of lung, limited stage". 408512008 is "Body mass index
# 40+ - severely obese (finding)", and Synthea's wellness_encounters module
# emits it as a Condition, so every severely obese patient classified as having
# primary lung cancer. It put 48 non-cancer patients into a 1,000-patient
# cancer cohort and nothing failed.
#
# Nothing in the codebase could have caught it: the code was in the set, the
# set was consulted, the lookup succeeded. Only the COMMENT was wrong, and a
# comment is not executable. What makes it catchable is asserting on the
# (code, display) PAIR that Synthea emits -- if the code means something else,
# the pair is the only place the disagreement shows up.
#
# Every code below was verified twice: against UMLS 2025AB MRCONSO
# (SAB=SNOMEDCT_US, TTY=FN) for meaning, and against the module JSONs inside
# the Synthea JAR for "does this corpus emit it". The displays are the SNOMED
# fully specified names, which is also what Synthea writes into the bundle.

print()
print("=" * 70)
print("Test 10: SNOMED codes Synthea actually emits")
print("=" * 70)

# -- The defect itself. 408512008 must never again read as a cancer.
check("408512008 BMI 40+ is NOT cancer",
      is_primary_snomed("408512008", "Body mass index 40+ - severely obese (finding)"), False)
check("408512008 absent from _SNOMED_PRIMARY",
      "408512008" in _SNOMED_PRIMARY,                                                   False)
# Same defect class, found by the same audit: 408513003 was listed as
# "Small cell carcinoma of lung, extensive stage" and is a spoken-language code.
check("408513003 spoken language is NOT cancer",
      is_primary_snomed("408513003", "Main spoken language Brawa (finding)"),           False)
check("408513003 absent from _SNOMED_PRIMARY",
      "408513003" in _SNOMED_PRIMARY,                                                   False)

# -- Every primary-cancer Condition code the Synthea JAR can emit.
#    lung_cancer.json / veteran_lung_cancer.json
check("254637007 NSCLC",
      is_primary_snomed("254637007", "Non-small cell lung cancer (disorder)"),          True)
check("424132000 NSCLC TNM stage 1",
      is_primary_snomed("424132000", "Non-small cell carcinoma of lung, TNM stage 1 (disorder)"), True)
check("425048006 NSCLC TNM stage 2",
      is_primary_snomed("425048006", "Non-small cell carcinoma of lung, TNM stage 2 (disorder)"), True)
check("422968005 NSCLC TNM stage 3",
      is_primary_snomed("422968005", "Non-small cell carcinoma of lung, TNM stage 3 (disorder)"), True)
check("423121009 NSCLC TNM stage 4",
      is_primary_snomed("423121009", "Non-small cell carcinoma of lung, TNM stage 4 (disorder)"), True)
check("254632001 SCLC",
      is_primary_snomed("254632001", "Small cell carcinoma of lung (disorder)"),        True)
check("67811000119102 SCLC TNM stage 1",
      is_primary_snomed("67811000119102", "Primary small cell malignant neoplasm of lung, TNM stage 1 (disorder)"), True)
check("67821000119109 SCLC TNM stage 2",
      is_primary_snomed("67821000119109", "Primary small cell malignant neoplasm of lung, TNM stage 2 (disorder)"), True)
check("67831000119107 SCLC TNM stage 3",
      is_primary_snomed("67831000119107", "Primary small cell malignant neoplasm of lung, TNM stage 3 (disorder)"), True)
check("67841000119103 SCLC TNM stage 4",
      is_primary_snomed("67841000119103", "Primary small cell malignant neoplasm of lung, TNM stage 4 (disorder)"), True)
#    breast_cancer.json
check("254837009 breast",
      is_primary_snomed("254837009", "Malignant neoplasm of breast (disorder)"),        True)
#    colorectal_cancer.json
check("93761005 primary colon",
      is_primary_snomed("93761005", "Primary malignant neoplasm of colon (disorder)"),  True)
check("109838007 overlapping colon",
      is_primary_snomed("109838007", "Overlapping malignant neoplasm of colon (disorder)"), True)
check("363406005 colon",
      is_primary_snomed("363406005", "Malignant neoplasm of colon (disorder)"),         True)
#    veteran_prostate_cancer.json
check("126906006 prostate",
      is_primary_snomed("126906006", "Neoplasm of prostate (disorder)"),                True)
#    acute_myeloid_leukemia.json
check("91861009 AML",
      is_primary_snomed("91861009", "Acute myeloid leukemia (disorder)"),               True)
#    trigger_bone_marrow_transplant.json -- a malignancy from a module whose
#    name does not say "cancer". Dropping the -m filter is what turned it on.
check("109989006 multiple myeloma",
      is_primary_snomed("109989006", "Multiple myeloma (disorder)"),                    True)

# -- Codes Synthea emits alongside a cancer that are NOT a primary cancer.
#    Each is a distinct rejection reason, and each must stay rejected.
check("94260004 metastatic to colon rejected",
      is_primary_snomed("94260004", "Metastatic malignant neoplasm to colon (disorder)"),    False)
check("94503003 metastatic to prostate rejected",
      is_primary_snomed("94503003", "Metastatic malignant neoplasm to prostate (disorder)"), False)
check("92691004 carcinoma in situ of prostate rejected",
      is_primary_snomed("92691004", "Carcinoma in situ of prostate (disorder)"),             False)
check("162573006 suspected lung cancer rejected",
      is_primary_snomed("162573006", "Suspected lung cancer (situation)"),                   False)
check("315268008 suspected prostate cancer rejected",
      is_primary_snomed("315268008", "Suspected prostate cancer (situation)"),               False)
check("68496003 colon polyp rejected",
      is_primary_snomed("68496003", "Polyp of colon (disorder)"),                            False)
check("713197008 recurrent rectal polyp rejected",
      is_primary_snomed("713197008", "Recurrent rectal polyp (disorder)"),                   False)

# -- Metastatic Synthea codes must be rejected BY THE CODE, not by wording.
#    Before this audit 94503003 was in neither set and fell through to
#    'unclassified'; the verdict was right for the wrong reason, and a display
#    string is not a guarantee.
reset_cancer_classification_stats()
is_primary_snomed("94260004", "Metastatic malignant neoplasm to colon (disorder)")
is_primary_snomed("94503003", "Metastatic malignant neoplasm to prostate (disorder)")
check("both metastatic codes rejected by code, not display",
      get_cancer_classification_stats()["rejected_secondary_code"],                          2)

# -- Set-level invariants. A code cannot be both, and every entry must look
#    like a SNOMED identifier (the 22 removed entries were not).
check("primary and secondary sets are disjoint",
      bool(_SNOMED_PRIMARY & _SNOMED_SECONDARY),                                             False)
check("every primary entry is all digits",
      all(c.isdigit() for c in _SNOMED_PRIMARY),                                             True)
check("every secondary entry is all digits",
      all(c.isdigit() for c in _SNOMED_SECONDARY),                                           True)
check("315006 (not a SNOMED code) gone from secondary",
      "315006" in _SNOMED_SECONDARY,                                                         False)


# ===========================================================================
# TEST 11: CODE LOOKUPS ARE GATED ON system_key
# ===========================================================================
# Layers 1 and 2 used to compare the code against the SNOMED set AND the
# ICD-10 sets without ever reading system_key, so a code matched on its digits
# alone regardless of which vocabulary it came from. That is how MEDCIN 315006
# ("antiphospholipid antibody syndrome with hemorrhagic disorder") sat in
# _SNOMED_SECONDARY labelled "Secondary malignant neoplasm of bone".
#
# The gate:
#   snomed              -> SNOMED sets only
#   icd10cm / icd10     -> ICD-10 sets only
#   unknown             -> both      (system ABSENT; the bare-code path below)
#   unmapped            -> neither   (system PRESENT but not recognised)
#   loinc/rxnorm/...    -> neither

print()
print("=" * 70)
print("Test 11: Code lookups are gated on system_key")
print("=" * 70)

# -- A SNOMED code presented under an ICD-10 system must not match.
check("SNOMED breast code under icd10cm does NOT match",
      is_primary("Malignant neoplasm of breast (disorder)", "254837009", "icd10cm"),  False)
check("SNOMED AML code under icd10cm does NOT match",
      is_primary("x", "91861009", "icd10cm"),                                         False)
check("...but the same code under snomed DOES match",
      is_primary_snomed("254837009", "Malignant neoplasm of breast (disorder)"),      True)

# -- An ICD-10 code presented under SNOMED must not match.
check("ICD-10 C34.10 under snomed does NOT match",
      is_primary("Malignant neoplasm of upper lobe", "C34.10", "snomed"),             False)
check("...but the same code under icd10cm DOES match",
      is_primary("Malignant neoplasm of upper lobe", "C34.10", "icd10cm"),            True)

# -- Exclusions are gated too, in both directions. The verdict stays False
#    either way, so the counter is what shows the gate worked: an ICD-10
#    secondary code mislabelled snomed is no longer REJECTED as secondary, it
#    is simply never looked up.
reset_cancer_classification_stats()
check("ICD-10 secondary C78.00 under snomed still returns False",
      is_primary("Secondary malignant neoplasm of lung", "C78.00", "snomed"),         False)
check("...but NOT via the secondary-code path",
      get_cancer_classification_stats()["rejected_secondary_code"],                   0)
check("...it is unclassified instead",
      get_cancer_classification_stats()["unclassified"],                              1)

reset_cancer_classification_stats()
check("SNOMED metastatic 94260004 under icd10cm still returns False",
      is_primary("x", "94260004", "icd10cm"),                                         False)
check("...but NOT via the secondary-code path",
      get_cancer_classification_stats()["rejected_secondary_code"],                   0)

reset_cancer_classification_stats()
check("...while under snomed it IS rejected as secondary",
      is_primary("x", "94260004", "snomed"),                                          False)
check("...and counted as such",
      get_cancer_classification_stats()["rejected_secondary_code"],                   1)

# -- SYSTEM_KEY_UNRECOGNIZED matches nothing at all. Both codes below are real
#    members of
#    the SNOMED / ICD-10 primary sets, and both must fail under unmapped.
check("SNOMED primary under unmapped does NOT match",
      is_primary("x", "254837009", SYSTEM_KEY_UNRECOGNIZED),                          False)
check("ICD-10 primary under unmapped does NOT match",
      is_primary("x", "C34.10", SYSTEM_KEY_UNRECOGNIZED),                             False)
check("a recognised non-cancer system (loinc) matches nothing",
      is_primary("x", "254837009", "loinc"),                                          False)

# -- "unknown" stays permissive: it is what the no-codings backward-compatible
#    path manufactures, and File 06 / File 13 depend on it.
check("bare code, no codings key -> SNOMED still consulted",
      _REGISTRY_UNDER_TEST.is_primary_cancer(
          {"code": "254837009", "display": "Malignant neoplasm of breast"}),          True)
check("bare code, no codings key -> ICD-10 still consulted",
      _REGISTRY_UNDER_TEST.is_primary_cancer(
          {"code": "C34.10", "display": "Malignant neoplasm of upper lobe"}),         True)
check("explicit system_key unknown -> SNOMED consulted",
      is_primary("x", "254837009", SYSTEM_KEY_ABSENT),                                True)

# -- A code skipped by the gate must NOT fall through to the display fallback.
#    "Adenocarcinoma of lung" passes the fallback when uncoded (Test 1), so if
#    the gate leaked into has_recognized_code this would come back True.
check("skipped coding still suppresses the display fallback",
      is_primary("Adenocarcinoma of lung", "254837009", SYSTEM_KEY_UNRECOGNIZED),     False)
check("...and the same display with no code at all still passes",
      is_primary("Adenocarcinoma of lung"),                                           True)

# -- The two new counters.
reset_cancer_classification_stats()
is_primary("x", "254837009", SYSTEM_KEY_UNRECOGNIZED)
check("skipped_unmapped_coding counted",
      get_cancer_classification_stats()["skipped_unmapped_coding"],                   1)
check("unmapped skip is NOT counted as another system",
      get_cancer_classification_stats()["skipped_other_system_coding"],               0)

reset_cancer_classification_stats()
is_primary("x", "254837009", "loinc")
check("skipped_other_system_coding counted",
      get_cancer_classification_stats()["skipped_other_system_coding"],               1)
check("loinc skip is NOT counted as unmapped",
      get_cancer_classification_stats()["skipped_unmapped_coding"],                   0)

reset_cancer_classification_stats()
_REGISTRY_UNDER_TEST.is_primary_cancer(
    {"code": "254837009", "display": "Malignant neoplasm of breast"})
check("decided_on_unknown_system counted for the bare-code path",
      get_cancer_classification_stats()["decided_on_unknown_system"],                 1)
check("...and the decision itself was a SNOMED match",
      get_cancer_classification_stats()["snomed_primary"],                            1)

reset_cancer_classification_stats()
is_primary_snomed("254837009", "Malignant neoplasm of breast (disorder)")
check("a properly systemed decision does NOT count as unknown",
      get_cancer_classification_stats()["decided_on_unknown_system"],                 0)

# -- Every coding is counted exactly once, even when Pass 1 returns early.
#    94260004 is SNOMED metastatic, so Pass 1 rejects; the unmapped sibling
#    must still have been counted, because Pass 0 runs before any verdict.
reset_cancer_classification_stats()
_REGISTRY_UNDER_TEST.is_primary_cancer({
    "code": "94260004", "display": "Metastatic malignant neoplasm to colon",
    "codings": [
        {"system_key": SYSTEM_KEY_UNRECOGNIZED, "code": "999999", "display": "local code"},
        {"system_key": "snomed",   "code": "94260004", "display": "Metastatic malignant neoplasm to colon"},
    ],
})
check("early Pass 1 return still counted the unmapped sibling",
      get_cancer_classification_stats()["skipped_unmapped_coding"],                   1)
check("...and the rejection was recorded",
      get_cancer_classification_stats()["rejected_secondary_code"],                   1)


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
