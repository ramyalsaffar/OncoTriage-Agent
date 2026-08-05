# Histology Extraction Test
###########################

"""
Histology Extraction Test

Unit tests for the negation-aware histology tag extraction in
10- Structured Eligibility Extractor.py.

Covers:
    1. Clause-prefix negation — every phrase in _NEGATION_PREFIXES
    2. Morphological negation — "non-<histology>" (incl. unicode hyphens)
    3. Clause-suffix negation — "<histology> is excluded"
    4. Negation scoping — clause boundaries must STOP a negation
    5. Affirmative regressions — real histologies still tagged
    6. Permissive trials — a trial carrying both members of a mutually
       exclusive pair ("adenocarcinoma OR squamous") keeps BOTH tags at
       index time; it permits either histology
    6b. The matching rule — intersection first: a trial is dropped only when
       the tag sets do not overlap AND an exclusive pair spans them
    7. Patient side — two primaries keep both tags and match either type

No network, no LLM, no Qdrant. Pure function tests.

Run from terminal (or F5 in Spyder):
    python "30- Histology Extraction Test.py"

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
    ["10- Structured Eligibility Extractor.py"],
    caller_file=_code_dir + "30- Histology Extraction Test.py",
    caller_globals=globals(),
    chain_label="01 → 02 → 10",
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


def tags(text: str) -> set:
    """Shorthand for the function under test."""
    return _extract_histology_tags(text)


def make_trial(title: str, inclusion: str = "", nct: str = "NCT00000001") -> dict:
    """Minimal trial dict shaped like parse_trial_metadata() output."""
    return {
        "nct_id": nct,
        "title": title,
        "eligibility": {"inclusion_criteria": inclusion},
    }


# ===========================================================================
# TEST 1: CLAUSE-PREFIX NEGATION (_NEGATION_PREFIXES)
# ===========================================================================
# One case per phrase in _NEGATION_PREFIXES, applied to a histology term.
# Every one of these must yield NO tag for the negated histology.

print("=" * 70)
print("Test 1: Clause-prefix negation — every phrase in _NEGATION_PREFIXES")
print("=" * 70)

_PREFIX_CASES = [
    # (negation phrase under test, text, term that must NOT be tagged)
    ("no prior",        "Patients with no prior squamous cell carcinoma",          "squamous"),
    ("no history",      "Patients with no history of adenocarcinoma",              "adenocarcinoma"),
    ("no previous",     "Subjects with no previous neuroendocrine tumor",          "neuroendocrine"),
    ("without",         "Patients without squamous histology",                     "squamous"),
    ("must not have",   "Patients must not have adenocarcinoma of the lung",       "adenocarcinoma"),
    ("should not have", "Subjects should not have squamous cell carcinoma",        "squamous"),
    ("excluded",        "Excluded: adenocarcinoma of any site",                    "adenocarcinoma"),
    ("excludes",        "The protocol excludes squamous histology",                "squamous"),
    ("excluding",       "All lung tumors excluding adenocarcinoma",                "adenocarcinoma"),
    # "ruled out" reads naturally in trailing position, so it is covered on
    # both sides: here as a look-back phrase, and again in Test 3 as a suffix.
    ("ruled out",       "Histologies ruled out: adenocarcinoma of the lung",       "adenocarcinoma"),
    ("absence of",      "Documented absence of adenocarcinoma component",          "adenocarcinoma"),
    ("free of",         "Patients free of squamous differentiation",               "squamous"),
    ("other than",      "Tumors other than adenocarcinoma",                        "adenocarcinoma"),
    ("except for",      "Any histology except for squamous cell carcinoma",        "squamous"),
]

for phrase, text, term in _PREFIX_CASES:
    check(f'"{phrase}" suppresses "{term}"  |  {text!r}',
          term in tags(text), False)

# Prefix negation on the lung-type axis too, not just squamous/adeno
check('"no history" suppresses "sclc"  |  lung',
      "sclc" in tags("Patients with no history of small cell lung cancer"), False)
check('"without" suppresses "nsclc"',
      "nsclc" in tags("Cohort without non-small cell lung cancer"), False)


# ===========================================================================
# TEST 2: MORPHOLOGICAL NEGATION ("non-<histology>")
# ===========================================================================
# The bug that motivated this work: "non-squamous" contains "squamous".

print()
print("=" * 70)
print("Test 2: Morphological negation — 'non-<histology>'")
print("=" * 70)

check('"non-squamous NSCLC" → nsclc only',
      tags("Study in non-squamous NSCLC"), {"nsclc"})
check('"non squamous" (space separator)',
      "squamous" in tags("Patients with non squamous histology"), False)
check('"non‑squamous" (unicode non-breaking hyphen)',
      "squamous" in tags("Patients with non‑squamous histology"), False)
check('"non–adenocarcinoma" (en dash)',
      "adenocarcinoma" in tags("non–adenocarcinoma tumors of the lung"), False)
check('"non-neuroendocrine" suppresses neuroendocrine',
      "neuroendocrine" in tags("Patients with non-neuroendocrine tumors"), False)
check('"non-small cell" still yields nsclc (not suppressed by its own "non-")',
      tags("Non-Small Cell Lung Cancer"), {"nsclc"})


# ===========================================================================
# TEST 3: CLAUSE-SUFFIX NEGATION ("<histology> is excluded")
# ===========================================================================
# The negation cue FOLLOWS the term, so a look-back cannot see it.

print()
print("=" * 70)
print("Test 3: Clause-suffix negation — '<histology> is excluded'")
print("=" * 70)

check('"adenocarcinoma is excluded"',
      "adenocarcinoma" in tags("Adenocarcinoma is excluded"), False)
check('"squamous cell carcinoma is not eligible"',
      "squamous" in tags("Squamous cell carcinoma is not eligible"), False)
check('"small cell lung cancer patients are ineligible"',
      "sclc" in tags("Small cell lung cancer patients are ineligible"), False)
check('"neuroendocrine tumors are not permitted"',
      "neuroendocrine" in tags("Neuroendocrine tumors are not permitted"), False)
check('"adenocarcinoma must be ruled out" (trailing "ruled out")',
      "adenocarcinoma" in tags("Adenocarcinoma must be ruled out by central biopsy"), False)

# --- The exact reported failure case ---
_REPORTED = "Patients with non-squamous histology; adenocarcinoma is excluded"
check(f'REPORTED CASE → no tags  |  {_REPORTED!r}',
      tags(_REPORTED), set())


# ===========================================================================
# TEST 4: NEGATION SCOPING — boundaries must stop a negation
# ===========================================================================
# Over-firing is safe (a dropped tag only leaves the trial unfiltered), but
# a negation must not leak across a sentence into an affirmative mention.

print()
print("=" * 70)
print("Test 4: Negation scoping — clause boundaries stop a negation")
print("=" * 70)

check("period ends the negation scope",
      "adenocarcinoma" in tags("No prior chemotherapy. Adenocarcinoma of the lung required"), True)
check("semicolon ends the negation scope",
      "squamous" in tags("No prior radiotherapy; squamous cell carcinoma of the lung"), True)
check("suffix cue past a period does not negate",
      "adenocarcinoma" in tags("Adenocarcinoma of the lung. Prior therapy is excluded"), True)
check("suffix cue beyond the lookahead window does not negate",
      "adenocarcinoma" in tags("Adenocarcinoma of the lung" + " x" * 40 + " is excluded"), True)


# ===========================================================================
# TEST 5: AFFIRMATIVE REGRESSIONS — real histologies still tagged
# ===========================================================================

print()
print("=" * 70)
print("Test 5: Affirmative regressions — extraction still works")
print("=" * 70)

check("plain NSCLC title",
      tags("A Study of Pembrolizumab in Non-Small Cell Lung Cancer"), {"nsclc"})
check("NSCLC abbreviation",
      tags("Osimertinib in EGFR-mutant NSCLC"), {"nsclc"})
check("plain SCLC title",
      tags("Chemotherapy for Small Cell Lung Cancer"), {"sclc"})
check("SCLC abbreviation",
      tags("Maintenance therapy in SCLC"), {"sclc"})
check("squamous lung",
      tags("Squamous Cell Carcinoma of the Lung"), {"squamous"})
check("adenocarcinoma lung",
      tags("Adenocarcinoma of the Lung, Stage IV"), {"adenocarcinoma"})
check("squamous + nsclc co-occur (not exclusive)",
      tags("Squamous Non-Small Cell Lung Cancer"), {"nsclc", "squamous"})
check("affirmative before a negated mention survives",
      tags("Squamous NSCLC; adenocarcinoma is excluded"), {"nsclc", "squamous"})
check("tracheal",
      tags("Tracheal Carcinoma Study"), {"tracheal"})
check("neuroendocrine",
      tags("Neuroendocrine Carcinoma of the Lung"), {"neuroendocrine"})
check("no histology signal → empty",
      tags("A Study of Aspirin in Healthy Volunteers"), set())
check("empty text → empty",
      tags(""), set())
check("'small cell' without lung context → no sclc tag (conservative)",
      tags("Small cell carcinoma of the bladder"), set())


# ===========================================================================
# TEST 6: A TRIAL CARRYING AN EXCLUSIVE PAIR KEEPS BOTH TAGS
# ===========================================================================
# "Adenocarcinoma or squamous cell carcinoma of the esophagus" is ordinary
# eligibility language: the trial PERMITS either histology. Dropping the pair
# at index time would discard real information — the trial would end up saying
# nothing about histology at all. Both tags are kept and counted; test 6b
# shows why that is safe for both populations.

print()
print("=" * 70)
print("Test 6: Trial with a mutually exclusive pair keeps both tags")
print("=" * 70)

reset_histology_extraction_stats()

_permissive = make_trial("Adenocarcinoma and Squamous Cell Carcinoma of the Lung")
enrich_histology_tags(_permissive)
check("squamous + adenocarcinoma in title → both tags indexed",
      _permissive["histology_tags"], ["adenocarcinoma", "squamous"])

# nsclc/sclc can only co-occur ACROSS fields: within one text the extractor
# uses an if/elif, so "non-small cell" always wins over "small cell".
check("within one text, nsclc precedence prevents an nsclc+sclc pair",
      tags("Small Cell and Non-Small Cell Lung Cancer Cohort"), {"nsclc"})

_both_lung = make_trial("Chemotherapy for Small Cell Lung Cancer",
                        "Inclusion Criteria: confirmed non-small cell lung cancer")
enrich_histology_tags(_both_lung)
check("nsclc (inclusion) + sclc (title) → both tags indexed",
      _both_lung["histology_tags"], ["nsclc", "sclc"])

_split = make_trial("Squamous Cell Carcinoma of the Lung",
                    "Inclusion Criteria: confirmed adenocarcinoma of the lung")
enrich_histology_tags(_split)
check("pair split across title and inclusion → both tags indexed",
      _split["histology_tags"], ["adenocarcinoma", "squamous"])

check("each permissive trial counted once as exclusive_pair_kept",
      get_histology_extraction_stats()["exclusive_pair_kept"], 3)

# A trial with an exclusive pair AND an extra tag keeps all three — nothing
# is dropped, so the extra tag goes on filtering.
_mixed = make_trial("Non-Small Cell Lung Cancer: Adenocarcinoma or "
                    "Squamous Cell Carcinoma", nct="NCT99999998")
enrich_histology_tags(_mixed)
check("exclusive pair alongside another tag → all tags survive",
      _mixed["histology_tags"], ["adenocarcinoma", "nsclc", "squamous"])

# The pair does NOT appear once negation removes one side —
# this is exactly the reported string, and it must index cleanly.
_ok_trial = make_trial("Study in NSCLC", _REPORTED)
enrich_histology_tags(_ok_trial)
check("reported string indexes cleanly after negation handling",
      _ok_trial["histology_tags"], ["nsclc"])

_clean_trial = make_trial("Squamous Non-Small Cell Lung Cancer")
enrich_histology_tags(_clean_trial)
check("non-contradictory trial still enriched (sorted list payload)",
      _clean_trial["histology_tags"], ["nsclc", "squamous"])

_no_histology = make_trial("A Study of Aspirin in Healthy Volunteers")
enrich_histology_tags(_no_histology)
check("trial with no histology signal → empty list (no filtering)",
      _no_histology["histology_tags"], [])


# ===========================================================================
# TEST 6b: THE MATCHING RULE — INTERSECTION FIRST
# ===========================================================================
# Drop only when the two tag sets do NOT overlap and a mutually exclusive
# pair spans them. If the patient's own histology is among the trial's tags,
# the trial names that patient's disease and is kept, whatever else it also
# names. This is the defect the old rule had: it walked every cross pair and
# dropped on the first exclusive one, so an adenocarcinoma patient lost a
# trial that listed adenocarcinoma alongside squamous.

print()
print("=" * 70)
print("Test 6b: Matching rule — intersection first, then exclusive pair")
print("=" * 70)

_both = make_trial("Adenocarcinoma or Squamous Cell Carcinoma of the Esophagus",
                   nct="NCT99999999")
enrich_histology_tags(_both)

check("permissive trial keeps both tags",
      _both["histology_tags"], ["adenocarcinoma", "squamous"])

# --- The reported defect: the patient's histology IS in the trial's set ---
check("adenocarcinoma patient KEPT against adeno-or-squamous trial",
      is_histology_mismatch({"adenocarcinoma"}, _both), False)
check("squamous patient KEPT against adeno-or-squamous trial",
      is_histology_mismatch({"squamous"}, _both), False)
check("untagged patient KEPT against adeno-or-squamous trial",
      is_histology_mismatch(set(), _both), False)

# --- No overlap + an exclusive pair spanning the sets → still dropped ---
check("squamous patient DROPPED against adenocarcinoma-only trial",
      is_histology_mismatch({"squamous"}, {"histology_tags": ["adenocarcinoma"]}), True)
check("adenocarcinoma patient DROPPED against squamous-only trial",
      is_histology_mismatch({"adenocarcinoma"}, {"histology_tags": ["squamous"]}), True)
check("nsclc patient DROPPED against sclc-only trial",
      is_histology_mismatch({"nsclc"}, {"histology_tags": ["sclc"]}), True)

# --- No overlap, no exclusive pair → keep (conservative) ---
# squamous/tracheal is not in the table: tracheal squamous cell carcinoma is
# the commonest tracheal histology, so the two are not a contradiction.
check("squamous patient vs tracheal trial → no exclusive pair → keep",
      is_histology_mismatch({"squamous"}, {"histology_tags": ["tracheal"]}), False)

# --- A permissive trial's OTHER tags still filter ---
check("sclc patient DROPPED by the nsclc tag on the permissive trial",
      is_histology_mismatch({"sclc"}, _mixed), True)
check("squamous patient KEPT by the permissive trial's squamous tag",
      is_histology_mismatch({"squamous"}, _mixed), False)
check("adenocarcinoma patient KEPT by the permissive trial's adeno tag",
      is_histology_mismatch({"adenocarcinoma"}, _mixed), False)


# ===========================================================================
# TEST 7: PATIENT SIDE — two primaries keep both tags
# ===========================================================================
# A patient CAN have two primaries. Under the intersection rule keeping both
# tags is correct: the patient matches trials naming EITHER type, which is
# what two primaries means. Nothing is dropped on the patient side.

print()
print("=" * 70)
print("Test 7: Patient side — two primaries keep both tags")
print("=" * 70)

_two_primaries = extract_patient_histology([
    {"display": "Squamous cell carcinoma of cervix"},
    {"display": "Adenocarcinoma of lung"},
])
check("two primaries → both tags kept",
      _two_primaries, {"adenocarcinoma", "squamous"})

check("single primary unaffected",
      extract_patient_histology([
          {"display": "Non-small cell carcinoma of lung, TNM stage 1"},
      ]), {"nsclc"})

check("a third histology is kept alongside the pair",
      extract_patient_histology([
          {"display": "Squamous cell carcinoma of lung"},
          {"display": "Adenocarcinoma of lung"},
          {"display": "Neuroendocrine carcinoma of pancreas"},
      ]), {"adenocarcinoma", "neuroendocrine", "squamous"})

check("empty conditions → empty set",
      extract_patient_histology([]), set())

# --- A patient carrying both members of a pair matches trials of either type ---
check("two-primary patient matches a squamous-only trial",
      is_histology_mismatch(_two_primaries, {"histology_tags": ["squamous"]}), False)
check("two-primary patient matches an adenocarcinoma-only trial",
      is_histology_mismatch(_two_primaries, {"histology_tags": ["adenocarcinoma"]}), False)
check("two-primary patient matches the permissive adeno-or-squamous trial",
      is_histology_mismatch(_two_primaries, _both), False)
check("two-primary patient still dropped by an unrelated exclusive pair",
      is_histology_mismatch(_two_primaries | {"nsclc"},
                            {"histology_tags": ["sclc"]}), True)


# ===========================================================================
# TEST 7b: _EXCLUSIVE_PAIRS MATCHES ITS OWN DOCUMENTATION
# ===========================================================================
# The comment above _EXCLUSIVE_PAIRS lists five pairs. The set must contain
# those five and nothing else: a tag the table never names (neuroendocrine,
# tracheal) is a tag that filters nothing, and the extractor emits both.
#
# The two deliberate ABSENCES are asserted too, because they are the ones a
# future edit is most likely to "complete" by mistake:
#   - sclc/neuroendocrine — SCLC *is* a neuroendocrine carcinoma of the lung,
#     so a neuroendocrine trial is a trial for that patient's own disease.
#   - nsclc/squamous, nsclc/adenocarcinoma — NSCLC includes both subtypes.

print()
print("=" * 70)
print("Test 7b: Exclusive pair table matches the documented biology")
print("=" * 70)

check("the pair set is exactly the five documented pairs",
      {tuple(sorted(p)) for p in _EXCLUSIVE_PAIRS},
      {("nsclc", "sclc"),
       ("neuroendocrine", "nsclc"),
       ("nsclc", "tracheal"),
       ("sclc", "tracheal"),
       ("adenocarcinoma", "squamous")})

# --- nsclc ↔ neuroendocrine (NSCLC is epithelial, NE is neuroendocrine) ---
check("nsclc patient DROPPED by a neuroendocrine trial",
      is_histology_mismatch({"nsclc"}, {"histology_tags": ["neuroendocrine"]}), True)
check("neuroendocrine patient DROPPED by an NSCLC trial",
      is_histology_mismatch({"neuroendocrine"}, {"histology_tags": ["nsclc"]}), True)

# --- nsclc ↔ tracheal (different anatomical origin) ---
check("nsclc patient DROPPED by a tracheal trial",
      is_histology_mismatch({"nsclc"}, {"histology_tags": ["tracheal"]}), True)
check("tracheal patient DROPPED by an NSCLC trial",
      is_histology_mismatch({"tracheal"}, {"histology_tags": ["nsclc"]}), True)

# --- sclc ↔ tracheal (different anatomical origin) ---
check("sclc patient DROPPED by a tracheal trial",
      is_histology_mismatch({"sclc"}, {"histology_tags": ["tracheal"]}), True)
check("tracheal patient DROPPED by an SCLC trial",
      is_histology_mismatch({"tracheal"}, {"histology_tags": ["sclc"]}), True)

# --- Each added pair goes through the real extractor, not hand-built tags ---
_ne_trial = make_trial("Neuroendocrine Carcinoma of the Lung")
enrich_histology_tags(_ne_trial)
check("extracted neuroendocrine trial drops an NSCLC patient",
      is_histology_mismatch({"nsclc"}, _ne_trial), True)

_tracheal_trial = make_trial("Tracheal Carcinoma Study")
enrich_histology_tags(_tracheal_trial)
check("extracted tracheal trial drops an NSCLC patient",
      is_histology_mismatch({"nsclc"}, _tracheal_trial), True)
check("extracted tracheal trial drops an SCLC patient",
      is_histology_mismatch({"sclc"}, _tracheal_trial), True)

# --- ABSENT ON PURPOSE: sclc ↔ neuroendocrine ---
check("sclc/neuroendocrine is NOT an exclusive pair",
      frozenset({"sclc", "neuroendocrine"}) in _EXCLUSIVE_PAIRS, False)
check("SCLC patient KEEPS a neuroendocrine trial (SCLC is neuroendocrine)",
      is_histology_mismatch({"sclc"}, _ne_trial), False)
check("neuroendocrine patient KEEPS an SCLC trial",
      is_histology_mismatch({"neuroendocrine"}, {"histology_tags": ["sclc"]}), False)

# --- ABSENT ON PURPOSE: nsclc ↔ its own subtypes ---
check("nsclc/squamous is NOT an exclusive pair",
      frozenset({"nsclc", "squamous"}) in _EXCLUSIVE_PAIRS, False)
check("nsclc/adenocarcinoma is NOT an exclusive pair",
      frozenset({"nsclc", "adenocarcinoma"}) in _EXCLUSIVE_PAIRS, False)
check("squamous patient KEEPS an NSCLC-only trial",
      is_histology_mismatch({"squamous"}, {"histology_tags": ["nsclc"]}), False)
check("adenocarcinoma patient KEEPS an NSCLC-only trial",
      is_histology_mismatch({"adenocarcinoma"}, {"histology_tags": ["nsclc"]}), False)

# --- A trial naming both members of a NEW pair still permits either ---
_ne_or_nsclc = make_trial("Non-Small Cell Lung Cancer or Neuroendocrine "
                          "Carcinoma", nct="NCT99999997")
enrich_histology_tags(_ne_or_nsclc)
check("nsclc + neuroendocrine trial keeps both tags",
      _ne_or_nsclc["histology_tags"], ["neuroendocrine", "nsclc"])
check("nsclc patient KEPT by the nsclc-or-neuroendocrine trial",
      is_histology_mismatch({"nsclc"}, _ne_or_nsclc), False)
check("neuroendocrine patient KEPT by the nsclc-or-neuroendocrine trial",
      is_histology_mismatch({"neuroendocrine"}, _ne_or_nsclc), False)

# --- Downstream mismatch behaviour is unchanged ---
check("nsclc patient vs sclc trial → mismatch",
      is_histology_mismatch({"nsclc"}, {"histology_tags": ["sclc"]}), True)
check("nsclc patient vs squamous nsclc trial → no mismatch",
      is_histology_mismatch({"nsclc"}, {"histology_tags": ["nsclc", "squamous"]}), False)
check("empty patient tags → keep",
      is_histology_mismatch(set(), {"histology_tags": ["sclc"]}), False)
check("trial without histology_tags key → keep (backward compatible)",
      is_histology_mismatch({"nsclc"}, {}), False)


# ===========================================================================
# TEST 8: COUNTERS
# ===========================================================================
# No silent recovery: every suppressed mention lands in a counter.

print()
print("=" * 70)
print("Test 8: Negation counters are recorded")
print("=" * 70)

reset_histology_extraction_stats()
tags("Patients without squamous histology")
tags("Study in non-squamous NSCLC")
tags("Adenocarcinoma is excluded")
_stats = get_histology_extraction_stats()

check("clause_prefix counted",  _stats["clause_prefix"] >= 1, True)
check("morphological counted",  _stats["morphological"] >= 1, True)
check("clause_suffix counted",  _stats["clause_suffix"] >= 1, True)

# Nothing is softened any more, but permissive trials are still counted.
check("exclusive_pair_kept not incremented by extraction alone",
      _stats["exclusive_pair_kept"], 0)
enrich_histology_tags(make_trial("Adenocarcinoma or Squamous Cell Carcinoma"))
check("exclusive_pair_kept incremented by a permissive trial",
      get_histology_extraction_stats()["exclusive_pair_kept"], 1)

reset_histology_extraction_stats()
check("reset zeroes the counters",
      any(get_histology_extraction_stats().values()), False)


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
