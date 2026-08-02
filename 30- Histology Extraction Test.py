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
    6. Self-contradiction — a trial with a mutually exclusive pair raises
       HistologyTagConflictError, carrying the trial so the index-time
       handler can recover it
    6b. Softening — the raise is recoverable: the pair is dropped and the
       trial is indexed unfiltered on the histology axis (File 11 handler)
    7. Patient side — a contradictory pair is dropped, not raised

No network, no LLM, no Qdrant. Pure function tests.

Run from terminal (or F5 in Spyder):
    python "30- Histology Extraction Test.py"

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


def check_raises(label: str, exc_type, fn, *args, **kwargs) -> None:
    """Assert that fn(*args) raises exc_type."""
    try:
        result = fn(*args, **kwargs)
    except exc_type:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    except Exception as e:                                    # noqa: BLE001
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected {exc_type.__name__}, got {type(e).__name__}: {e}")
        print(f"  FAIL  {label} — expected {exc_type.__name__}, got {type(e).__name__}: {e}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append(f"{label}\n          expected {exc_type.__name__}, returned {result}")
        print(f"  FAIL  {label} — expected {exc_type.__name__}, returned normally")


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
# TEST 6: SELF-CONTRADICTORY TRIAL TAG SET RAISES
# ===========================================================================
# A trial cannot REQUIRE both squamous and adenocarcinoma. Such a set would
# conflict with both patient populations, so enrich_histology_tags refuses to
# produce it — the refusal is never silent. Test 6b covers the recovery.

print()
print("=" * 70)
print("Test 6: Self-contradictory trial tag set raises")
print("=" * 70)

check_raises("squamous + adenocarcinoma in title raises",
             HistologyTagConflictError,
             enrich_histology_tags,
             make_trial("Adenocarcinoma and Squamous Cell Carcinoma of the Lung"))

# nsclc/sclc can only contradict ACROSS fields: within one text the extractor
# uses an if/elif, so "non-small cell" always wins over "small cell".
check("within one text, nsclc precedence prevents an nsclc+sclc pair",
      tags("Small Cell and Non-Small Cell Lung Cancer Cohort"), {"nsclc"})

check_raises("nsclc (inclusion) + sclc (title) raises",
             HistologyTagConflictError,
             enrich_histology_tags,
             make_trial("Chemotherapy for Small Cell Lung Cancer",
                        "Inclusion Criteria: confirmed non-small cell lung cancer"))

check_raises("contradiction split across title and inclusion raises",
             HistologyTagConflictError,
             enrich_histology_tags,
             make_trial("Squamous Cell Carcinoma of the Lung",
                        "Inclusion Criteria: confirmed adenocarcinoma of the lung"))

check("HistologyTagConflictError is a ValueError",
      issubclass(HistologyTagConflictError, ValueError), True)

# The contradiction is NOT raised once negation removes one side —
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
# TEST 6b: THE RAISE IS RECOVERABLE — soften, do not refuse
# ===========================================================================
# A trial tagged {squamous, adenocarcinoma} PERMITS either histology, it does
# not require both. Refusing it would hide it from every patient, including
# patients with no histology tag — the same false-ineligible direction this
# filter exists to prevent. File 11's handler calls soften_histology_conflict.

print()
print("=" * 70)
print("Test 6b: Contradictory trial is softened and indexed, not refused")
print("=" * 70)

# --- The error carries what the handler needs to recover ---
_both = make_trial("Adenocarcinoma or Squamous Cell Carcinoma of the Esophagus",
                   nct="NCT99999999")
try:
    enrich_histology_tags(_both)
    _err = None
except HistologyTagConflictError as _e:
    _err = _e

check("raise still fires", _err is not None, True)
check("error carries the trial", _err.trial is _both, True)
check("error carries the full tag set", _err.tags, {"adenocarcinoma", "squamous"})
check("error carries the offending pair", _err.pair, ("adenocarcinoma", "squamous"))
check("no histology_tags written by the refused call",
      "histology_tags" in _both, False)

# --- Softening indexes the trial with the pair dropped ---
reset_histology_extraction_stats()
_recovered = soften_histology_conflict(_err, log=lambda _m: None)

check("softened trial is the same dict (indexable)", _recovered is _both, True)
check("contradictory pair dropped → unfiltered on histology axis",
      _recovered["histology_tags"], [])
check("softening increments contradiction_softened",
      get_histology_extraction_stats()["contradiction_softened"], 1)

# --- And the softened trial is now reachable by BOTH populations ---
check("squamous patient can still see the softened trial",
      is_histology_mismatch({"squamous"}, _recovered), False)
check("adenocarcinoma patient can still see the softened trial",
      is_histology_mismatch({"adenocarcinoma"}, _recovered), False)
check("untagged patient can still see the softened trial",
      is_histology_mismatch(set(), _recovered), False)

# --- Softening drops ONLY the exclusive pair; other tags keep filtering ---
_mixed = make_trial("Non-Small Cell Lung Cancer: Adenocarcinoma or "
                    "Squamous Cell Carcinoma", nct="NCT99999998")
try:
    enrich_histology_tags(_mixed)
except HistologyTagConflictError as _e2:
    soften_histology_conflict(_e2, log=lambda _m: None)

check("only the exclusive pair is dropped, other tags survive",
      _mixed["histology_tags"], ["nsclc"])
check("surviving nsclc tag still filters out an sclc patient",
      is_histology_mismatch({"sclc"}, _mixed), True)
check("squamous patient still sees the softened NSCLC trial",
      is_histology_mismatch({"squamous"}, _mixed), False)

# --- Unrecoverable error re-raises rather than inventing a trial ---
check_raises("error with no trial attached re-raises",
             HistologyTagConflictError,
             soften_histology_conflict,
             HistologyTagConflictError("no trial attached"))


# ===========================================================================
# TEST 7: PATIENT SIDE — contradiction dropped, not raised
# ===========================================================================
# A patient CAN have two primaries. Raising at query time would break the
# request; dropping the pair leaves that axis unfiltered (conservative).

print()
print("=" * 70)
print("Test 7: Patient side — contradictory pair dropped, not raised")
print("=" * 70)

check("two primaries → contradictory pair dropped",
      extract_patient_histology([
          {"display": "Squamous cell carcinoma of cervix"},
          {"display": "Adenocarcinoma of lung"},
      ]), set())

check("single primary unaffected",
      extract_patient_histology([
          {"display": "Non-small cell carcinoma of lung, TNM stage 1"},
      ]), {"nsclc"})

check("dropped pair leaves other tags intact",
      extract_patient_histology([
          {"display": "Squamous cell carcinoma of lung"},
          {"display": "Adenocarcinoma of lung"},
          {"display": "Neuroendocrine carcinoma of pancreas"},
      ]), {"neuroendocrine"})

check("empty conditions → empty set",
      extract_patient_histology([]), set())

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
