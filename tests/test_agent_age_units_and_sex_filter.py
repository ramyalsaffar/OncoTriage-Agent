# Age Units / Sex Filter Test
#############################

"""
Age Units and Sex Filter Test

The Stage 4 checks in oncotriage/agent/filtering.py for two fixes that each
change WHICH TRIALS SURVIVE, and which nothing in the repository proved until
this file existed.

  1. ``_parse_age_bound`` CONVERTS THE UNIT. It used to take the digits out of
     a bound and throw the unit away, so "240 Months" -- twenty years -- was
     read as two hundred and forty years and that trial's upper bound stopped
     excluding anybody, while a min_age of "6 Months" was read as six years and
     excluded every infant the trial was written for. Nothing was recorded,
     because digits WERE found. The result is fractional years and must stay
     fractional: six months is 0.5, and rounding moves the boundary in exactly
     the direction the fix exists to correct.

  2. AN UNUSABLE PATIENT SEX NO LONGER EXCLUDES EVERY SEX-SPECIFIC TRIAL. The
     old predicate kept a trial when its sex field was ALL or equalled the
     patient's, so a patient whose sex did not parse failed that test against
     every sex-specific trial and all of them dropped. The same line called
     ``.upper()`` on the patient's sex with no guard, so a `gender` present and
     JSON-null arrived as None and RAISED rather than dropping. Not knowing a
     patient's sex is not evidence that they fail the trial's requirement, and
     the failure direction is asymmetric: keeping a trial that later proves
     ineligible costs one judged trial, dropping it hides an eligible trial
     permanently and invisibly.

WHY THIS IS A NEW FILE. Five files in tests/ touch ``node_rule_based_filter``
or ``_parse_age_bound``, and none of them is the right home:

  * ``test_degraded_dependencies.py`` covers BOTH names -- and it is one of the
    five members of ``tests/run_serial_tests.py``'s collision matrix, because it
    asserts on the ICD-10 seed and a SNOMED code that the audit control plants
    into. Its ``_parse_age_bound`` checks are about item 11a's COUNTER, not
    about units. Adding forty checks that need no serialization would put them
    behind a six-minute serial run for nothing, and would broaden a file whose
    docstring pins its subject to item 11a.
  * ``test_agent_mesh_boost_and_quality_gate.py`` is about the separation of the
    Stage 3 boost from the Stage 4 gate; it drives the node only as a vehicle.
  * ``test_agent_retrieval_observability.py`` and
    ``test_agent_ablation_flag_passthrough.py`` have their own subjects, named
    in their own titles.
  * ``test_indexer_admission_filters.py`` calls ``_parse_age_bound`` once, to
    show that the age decision the scraper used to make now happens here.

Pass 20f-1's precedent is four NEW files for four fixes, for this reason.

Covers:
    1. Age units -- years, months, weeks, days, hours, minutes; the fractional
       requirement; a bare number; an unrecognised unit; the number and the unit
       coming from ONE match
    2. The node -- an unusable bound means the age check is SKIPPED and the
       trial KEPT, which is the recovery item 11a deliberately did not change
    3. Sex -- an unusable sex stops excluding; None does not raise; a real
       mismatch still drops and is still counted apart from a survival caused
       by an unknown sex
    4. The Stage 4 returned dict gains no key (the twelve characterization
       fixtures diff it field by field)
    5. Negative controls, each planted into an in-memory COPY and each shown
       to fire
    6. THE HISTOLOGY-IMPORT TRAP -- see its own section

No network, no LLM, no Qdrant, no keys, no spend. The MeSH filter is overridden
to None through oncotriage/agent/deps.py, so no lookup file is read and no
model is built.

NOT in tests/run_serial_tests.py's collision matrix, derived rather than
assumed: this file writes nothing anywhere -- every plant goes into an
in-memory copy and the two source files it READS
(oncotriage/agent/filtering.py, oncotriage/extraction/histology.py) are written
by neither of the suite's two writers.

Run from terminal (or F5 in Spyder):
    python tests/test_agent_age_units_and_sex_filter.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# THE CANDIDATE DIRECTORY IS THE PARENT OF THIS FILE'S, because this file sits
# in tests/ and the package sits BESIDE tests/, not inside it.
# `pip install -e .` makes the whole block a no-op.
import os
import sys

try:
    import oncotriage  # noqa: F401
except ImportError:
    for _candidate, _how in (
        (os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
         if "__file__" in globals() else None, "__file__"),
        (os.getcwd(), "cwd"),
    ):
        if _candidate and os.path.isdir(os.path.join(_candidate, "oncotriage")):
            if _candidate not in sys.path:
                sys.path.insert(0, _candidate)
            print(f"[Bootstrap] oncotriage package found at {_candidate} "
                  f"(via {_how}); added to sys.path")
            break
    else:
        raise
    del _candidate, _how

import hashlib
import types

from oncotriage.agent import deps
from oncotriage.agent import filtering as _filtering_module
from oncotriage.agent.filtering import (
    AGE_PARSE_FAILURES,
    AGE_UNIT_ASSUMPTIONS,
    SEX_UNKNOWN_KEPT,
    _AGE_UNIT_YEARS,
    _COMPARABLE_PATIENT_SEXES,
    _parse_age_bound,
    node_rule_based_filter,
)
from oncotriage.extraction import histology as _histology_module
from oncotriage.extraction.histology import extract_patient_histology


# ===========================================================================
# HARNESS
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
        _FAILURES.append(f"{label}\n          expected: {expected}\n"
                         f"          actual:   {actual}")
        print(f"  FAIL  {label}")
        print(f"          expected: {expected}")
        print(f"          actual:   {actual}")


# THE PATHS COME FROM THE MODULES THIS PROCESS IMPORTED, never from this file's
# own location: moving the test cannot break them, and the source being planted
# into is provably the one under test rather than a same-named copy.
_FILTERING_SRC = os.path.abspath(_filtering_module.__file__)
_HISTOLOGY_SRC = os.path.abspath(_histology_module.__file__)
_SHA_AT_START = {
    p: hashlib.sha256(open(p, encoding="utf-8").read().encode()).hexdigest()
    for p in (_FILTERING_SRC, _HISTOLOGY_SRC)
}


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(path, name, subs):
    """Exec an in-memory COPY of `path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError, never ValueError -- so that a
    malformed plant is RECORDED as a failure instead of aborting the run and
    hiding every check below it. A control that takes the process down is not a
    control; it is an outage that happens to be red.

    The file on disk is hashed before and after and asserted byte-identical,
    because "mutates a COPY" is only true for as long as it stays true.
    """
    source = open(path, encoding="utf-8").read()
    before = hashlib.sha256(source.encode()).hexdigest()
    try:
        for old, new in subs:
            if old not in source:
                raise _PlantFailed(f"plant target absent: {old[:60]!r}...")
            source = source.replace(old, new, 1)
        module = types.ModuleType(name)
        module.__file__ = path
        exec(compile(source, path, "exec"), module.__dict__)
    except _PlantFailed:
        raise
    except Exception as exc:            # noqa: BLE001 - reported, not raised
        raise _PlantFailed(f"{type(exc).__name__}: {exc}") from None
    finally:
        after = hashlib.sha256(
            open(path, encoding="utf-8").read().encode()).hexdigest()
        if before != after:
            raise AssertionError(f"{path} was modified on disk by a plant")
    return module


def _plant_outcome(path, subs):
    """The exception TYPE NAME a plant produced, or 'no exception'.

    Catches Exception, so a _plant() that had LOST its guard reports
    'SyntaxError' here rather than killing the run. Takes a LIST, the same
    shape _control() passes, so that this probe cannot answer about its own
    argument handling instead of about the guard.
    """
    try:
        _plant(path, "probe", subs)
    except Exception as exc:            # noqa: BLE001 - the point
        return type(exc).__name__
    return "no exception"


_CONTROL_SEQ = [0]


def _control(label, path, subs, probe, expected):
    """Run a negative control. A BAD PLANT IS A RECORDED FAILURE, not a crash."""
    _CONTROL_SEQ[0] += 1
    try:
        module = _plant(path, f"ctl_{_CONTROL_SEQ[0]}", subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    try:
        actual = probe(module)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        actual = f"raised {type(exc).__name__}"
    check(label, actual, expected)


# --- the plant targets, quoted from the shipped source ---------------------

_CONVERT_LINE = "    return number * factor"
_UNKNOWN_UNIT_BLOCK = (
    '            _record_age_parse_failure(bound, raw, "UnknownAgeUnit",\n'
    '                                      unit=raw_unit)\n'
    '            return None')
_ONE_MATCH_LINES = (
    "        number_text, raw_unit = _AGE_BOUND_RE.findall(text)[0]\n"
    "        number = float(number_text)")
_SEX_NORMALISE_LINES = (
    '    _raw_patient_sex = demographics.get("sex")\n'
    '    patient_sex = ("unknown" if _raw_patient_sex is None\n'
    '                   else str(_raw_patient_sex).strip().lower())')
# THE `if` READS sex_filter_applied, NOT patient_sex_comparable, AND THE
# CONTROL IS UNCHANGED BY THAT. The filter-applied marker pass made the marker
# BE the loop's predicate rather than a second copy of it -- one name assigned
# once, read in the loop and returned as sex_filter_applied -- so this quotation
# tracks the shipped source, which is what it is for. The replacement below
# names neither variable: it substitutes the whole block for the pre-fix
# one-liner, so what this control asserts (the old predicate drops every
# sex-specific trial for an unknown sex) is the same claim it always made.
_SEX_PREDICATE_BLOCK = (
    '        if trial_sex != "ALL":\n'
    '            if sex_filter_applied:\n'
    '                if trial_sex != patient_sex.upper():\n'
    '                    sex_dropped += 1\n'
    '                    continue\n'
    '            else:\n'
    "                # NOT a drop and not a mismatch: the patient's sex never parsed,\n"
    "                # so this trial's requirement was never tested. Counted apart\n"
    '                # from sex_dropped because the two are different findings.\n'
    '                sex_unknown_kept += 1\n')

_HIST_SCLC_LINE = ('_SCLC_ABBREV_RE = re.compile(r"(?<!\\bN)\\bSCLC\\b", '
                   're.IGNORECASE)')
_HIST_NSCLC_LINE = '_NSCLC_ABBREV_RE = re.compile(r"\\bNSCLC\\b", re.IGNORECASE)'
_HIST_NO_FLAG = [
    (_HIST_SCLC_LINE, '_SCLC_ABBREV_RE = re.compile(r"(?<!\\bN)\\bSCLC\\b")'),
    (_HIST_NSCLC_LINE, '_NSCLC_ABBREV_RE = re.compile(r"\\bNSCLC\\b")'),
]


# --- state builders --------------------------------------------------------

# No MeSH lookup file is read and nothing is built: the filter is overridden to
# None, which is a REACHABLE state the node already handles and reports.
deps.set_override(deps.MESH_FILTER, None)


def trial(nct, min_age="18 Years", max_age="99 Years", sex="ALL",
          histology_tags=()):
    """One reranked-trial envelope, shaped like Stage 3's output."""
    return {"trial": {"nct_id": nct,
                      "title": "a trial",
                      "histology_tags": list(histology_tags),
                      "eligibility": {"min_age": min_age,
                                      "max_age": max_age,
                                      "sex": sex,
                                      "inclusion_criteria": ""}},
            # Identical scores so the quality gate cannot be the thing that
            # moves a count; every assertion below reads
            # candidates_after_rule_filter, which is measured BEFORE the gate.
            "rerank_score": 1.0,
            "rerank_score_raw": 1.0,
            "medcpt_score_max": 100.0}


def run(module, trials, age=50, sex="female", conditions=()):
    """Drive node_rule_based_filter, returning its dict or a RAISE MARKER.

    A REGRESSION THAT MAKES THE NODE RAISE MUST BE A RECORDED FAILURE, not a
    traceback. This is not defensiveness -- it is measured. Reverting the sex
    normalisation to the pre-fix `demographics.get("sex", "unknown").lower()`
    makes the node raise AttributeError on a null sex, which is precisely the
    defect Test 3 exists to catch; with a bare call, that raise escaped `check`
    while its argument was being evaluated, the file died with no summary, and
    the run reported one traceback where it owed a hundred and ten results. The
    marker is a string, so every `field()` comparison below fails loudly and
    names what happened instead of taking the process down.
    """
    state = {"patient_data": {"demographics": {"age": age, "sex": sex},
                              "conditions": list(conditions),
                              "cancer_stage_observations": []},
             "reranked_trials": list(trials),
             "ablation_flags": {},
             "patient_trees": set(),
             "stage_timings": {}}
    try:
        return module.node_rule_based_filter(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}: {exc}"


def field(result, name):
    """`result[name]`, or the raise marker verbatim. Never raises itself."""
    if isinstance(result, str):
        return result
    return result.get(name)


def survivors(result):
    """Sorted NCT ids of the survivors, or the raise marker verbatim."""
    if isinstance(result, str):
        return result
    return sorted(t["trial"]["nct_id"] for t in result["filtered_trials"])


def keys_of(result):
    """Sorted key set, or the raise marker verbatim."""
    if isinstance(result, str):
        return result
    return sorted(result)


# ===========================================================================
# TEST 1: AGE BOUNDS CONVERT TO FRACTIONAL YEARS
# ===========================================================================

print()
print("=" * 70)
print("Test 1: Age bounds convert to fractional years")
print("=" * 70)

AGE_PARSE_FAILURES.clear()
AGE_UNIT_ASSUMPTIONS.clear()

# --- 1a. every unit ClinicalTrials.gov registers ---------------------------
print()
print("--- 1a. every unit converts")

check("Years is the identity", _parse_age_bound("18 Years", 0, "min_age"), 18)
check("the singular Year parses too", _parse_age_bound("1 Year", 0, "min_age"), 1)
check("240 Months is twenty years, not two hundred and forty",
      _parse_age_bound("240 Months", 999, "max_age"), 20.0)
check("Weeks convert", round(_parse_age_bound("52 Weeks", 0, "min_age"), 4),
      0.9966)
check("Days convert", round(_parse_age_bound("365 Days", 0, "min_age"), 4),
      0.9993)
check("Hours convert", round(_parse_age_bound("24 Hours", 0, "min_age"), 6),
      0.002738)
# THE SMALL UNITS ARE CHECKED AS RELATIONS, NOT AS TRANSCRIBED DECIMALS. Pass
# 20f-4 lost a day to a hand-copied literal that no data reached; here the first
# attempt wrote 0.00011407 for a value that is 0.00011408, which would have been
# a failing test reporting a defect that does not exist. A relation cannot be
# mistyped into agreement with itself.
_bound = lambda s: _parse_age_bound(s, 0, "min_age")            # noqa: E731
check("60 Minutes is exactly 1 Hour",
      _bound("60 Minutes") == _bound("1 Hour"), True)
check("24 Hours is exactly 1 Day", _bound("24 Hours") == _bound("1 Day"), True)
check("7 Days is exactly 1 Week", round(_bound("7 Days"), 12),
      round(_bound("1 Week"), 12))
check("12 Months is exactly 1 Year", _bound("12 Months"), _bound("1 Year"))
check("...and a minute is smaller than an hour, so the relations above are not "
      "all comparing zero to zero",
      _bound("1 Minute") < _bound("1 Hour") < _bound("1 Day")
      < _bound("1 Week") < _bound("1 Month") < _bound("1 Year"), True)
check("the unit table names exactly the six units above",
      sorted(_AGE_UNIT_YEARS), ["day", "hour", "minute", "month", "week", "year"])
check("case does not matter to the unit", _parse_age_bound("240 MONTHS", 999,
                                                           "max_age"), 20.0)
check("one month is a twelfth of a year",
      round(_parse_age_bound("1 Month", 0, "min_age"), 10), round(1 / 12, 10))

# --- 1b. the fractional requirement ---------------------------------------
print()
print("--- 1b. six months is 0.5, and it is a float")

check("six months is 0.5", _parse_age_bound("6 Months", 0, "min_age"), 0.5)
check("...NOT 0", _parse_age_bound("6 Months", 0, "min_age") == 0, False)
check("...NOT 1", _parse_age_bound("6 Months", 0, "min_age") == 1, False)
check("...and the type is float, so nothing rounded on the way out",
      isinstance(_parse_age_bound("6 Months", 0, "min_age"), float), True)
# The only consumer is the numeric comparison in the node. Run, not read.
_lo = _parse_age_bound("6 Months", 0, "min_age")
_hi = _parse_age_bound("240 Months", 999, "max_age")
check("a float bound works in `min <= age <= max` (inside)",
      bool(_lo <= 20 <= _hi), True)
check("...above the top", bool(_lo <= 25 <= _hi), False)
check("...below the floor", bool(_lo <= 0 <= _hi), False)
check("...and an 18-year-old is inside a 6-month-to-20-year window",
      bool(_lo <= 18 <= _hi), True)

# --- 1c. a bound with no unit ---------------------------------------------
print()
print("--- 1c. a bare number is still read as years")

AGE_UNIT_ASSUMPTIONS.clear()
check("a bare number is years, which is what the pre-fix code assumed",
      _parse_age_bound("18", 0, "min_age"), 18.0)
check("...and the assumption is RECORDED rather than silent",
      AGE_UNIT_ASSUMPTIONS["min_age:no_unit:18"], 1)
check("...in its own counter, not in AGE_PARSE_FAILURES, because the bound WAS "
      "applied", dict(AGE_PARSE_FAILURES), {})
check("an empty bound still returns the caller's default, untouched",
      _parse_age_bound("", 0, "min_age"), 0)
check("...and None likewise", _parse_age_bound(None, 999, "max_age"), 999)
check("...neither of which records anything",
      (len(AGE_PARSE_FAILURES), len(AGE_UNIT_ASSUMPTIONS)), (0, 1))

# --- 1d. an unrecognised unit is recorded, never guessed ------------------
print()
print("--- 1d. an unrecognised unit records rather than being guessed at")

AGE_PARSE_FAILURES.clear()
AGE_UNIT_ASSUMPTIONS.clear()
check("an unrecognised unit is unusable", _parse_age_bound("3 Fortnights", 0,
                                                           "min_age"), None)
check("...recorded under a key that NAMES the unit, so the fix is one row in "
      "_AGE_UNIT_YEARS",
      AGE_PARSE_FAILURES["min_age:UnknownAgeUnit:fortnights:3 Fortnights"], 1)
check("...and nothing was guessed into AGE_UNIT_ASSUMPTIONS",
      dict(AGE_UNIT_ASSUMPTIONS), {})

AGE_PARSE_FAILURES.clear()
check("a digit-less bound is still None, exactly as before the fix",
      _parse_age_bound("N/A", 0, "min_age"), None)
check("...and still records under the key it has always recorded under",
      AGE_PARSE_FAILURES["min_age:IndexError:N/A"], 1)
check("a digit-less max_age records under its OWN bound",
      (_parse_age_bound("no maximum", 999, "max_age"),
       AGE_PARSE_FAILURES["max_age:IndexError:no maximum"]), (None, 1))

AGE_PARSE_FAILURES.clear()
_parse_age_bound("Q" * 500, 0, "min_age")
check("a pathological bound cannot grow a counter key without bound",
      all(len(k) < 100 for k in AGE_PARSE_FAILURES), True)
check("...and the sweep actually recorded something (non-degeneracy)",
      len(AGE_PARSE_FAILURES), 1)

# --- 1e. the number and the unit come from ONE match ----------------------
print()
print("--- 1e. the number and its unit are read together")

AGE_PARSE_FAILURES.clear()
AGE_UNIT_ASSUMPTIONS.clear()
check("an adjacent unit is used, whatever the string names later",
      _parse_age_bound("6 Months to 2 Years", 0, "min_age"), 0.5)
check("a unit belonging to a LATER number is not pulled back onto the first",
      _parse_age_bound("5, 240 Months", 0, "min_age"), 5.0)
check("a unit that is not adjacent is recovered when the string names exactly "
      "one", _parse_age_bound("18 to 65 Years", 0, "min_age"), 18.0)
check("...recorded as an inference rather than read as a unit",
      AGE_UNIT_ASSUMPTIONS["min_age:unit_not_adjacent:year:18 to 65 Years"], 1)
check("two candidate units and no adjacent one stays unusable",
      _parse_age_bound("18 to 65 Years or 6 Months", 0, "min_age"), None)
check("...naming the non-unit token that sat where the unit should have been",
      AGE_PARSE_FAILURES["min_age:UnknownAgeUnit:to:18 to 65 Years or 6 Months"],
      1)


# ===========================================================================
# TEST 2: THE NODE -- AN UNUSABLE BOUND SKIPS THE CHECK AND KEEPS THE TRIAL
# ===========================================================================
# The recovery item 11a deliberately did not change. Asserted at the NODE,
# because "returns None" is a statement about a helper and "the trial survives"
# is the statement anybody cares about.

print()
print("=" * 70)
print("Test 2: the node -- unusable bound keeps the trial, usable bound filters")
print("=" * 70)

_POOL_AGE = [
    trial("NCT00000001", min_age="18 Years", max_age="99 Years"),   # in range
    trial("NCT00000002", min_age="6 Months", max_age="240 Months"),  # 0.5-20y
    trial("NCT00000003", min_age="N/A", max_age="99 Years"),         # unusable
    trial("NCT00000004", min_age="3 Fortnights", max_age="99 Years"),  # unusable
]

AGE_PARSE_FAILURES.clear()
_r50 = run(_filtering_module, _POOL_AGE, age=50)
_kept50 = survivors(_r50)
check("a 50-year-old is dropped by the 6-month-to-20-year trial",
      "NCT00000002" in _kept50, False)
check("...and it is counted as an age drop", field(_r50, "age_dropped"), 1)
check("both unusable bounds KEEP their trial",
      [n for n in _kept50 if n in ("NCT00000003", "NCT00000004")],
      ["NCT00000003", "NCT00000004"])
check("...and the age filter is reported as not having run for them, which is "
      "NOT a drop",
      (field(_r50, "age_dropped"), len(_POOL_AGE) - len(_kept50)), (1, 1))
check("the unusable bounds both reached AGE_PARSE_FAILURES",
      sum(AGE_PARSE_FAILURES.values()), 2)

_r10 = run(_filtering_module, _POOL_AGE, age=10)
_kept10 = survivors(_r10)
check("a 10-year-old IS inside the 6-month-to-20-year window, which the "
      "pre-fix reading of 6..240 years excluded", "NCT00000002" in _kept10, True)
check("...and is excluded by the 18-to-99-YEARS trial",
      "NCT00000001" in _kept10, False)
check("...so the two age-bearing trials disagree, which is what makes this "
      "sample discriminating", field(_r10, "age_dropped"), 1)

_r_noage = run(_filtering_module, _POOL_AGE, age=None)
check("a patient with NO age drops nothing on age",
      field(_r_noage, "age_dropped"), 0)
check("...and keeps every trial in the pool",
      field(_r_noage, "candidates_after_rule_filter"), len(_POOL_AGE))


# ===========================================================================
# TEST 3: AN UNUSABLE PATIENT SEX STOPS EXCLUDING
# ===========================================================================

print()
print("=" * 70)
print("Test 3: an unusable patient sex no longer excludes sex-specific trials")
print("=" * 70)

_POOL_SEX = [trial("NCT00000010", sex="ALL"),
             trial("NCT00000011", sex="MALE"),
             trial("NCT00000012", sex="FEMALE"),
             trial("NCT00000013", sex="MALE"),
             trial("NCT00000014", sex="FEMALE")]

print()
print("--- 3a. a known sex is unchanged")
SEX_UNKNOWN_KEPT.clear()
_rf = run(_filtering_module, _POOL_SEX, sex="female")
check("a female patient drops the two MALE trials", field(_rf, "sex_dropped"), 2)
check("...and keeps three", field(_rf, "candidates_after_rule_filter"), 3)
check("...and records NO unknown-sex survival", dict(SEX_UNKNOWN_KEPT), {})
_rm = run(_filtering_module, _POOL_SEX, sex="male")
check("symmetrically for male", field(_rm, "sex_dropped"), 2)
check("case and whitespace are normalised, so this is still a known sex",
      field(run(_filtering_module, _POOL_SEX, sex="  Female  "),
            "sex_dropped"), 2)

print()
print("--- 3b. a sex the trial vocabulary cannot express")
# Read from oncotriage/fhir/parser.py: `sex = patient_resource.get('gender',
# 'unknown')`. An ABSENT gender element gives "unknown"; a gender present and
# JSON-null gives None, because a .get default does not apply to a key that
# exists; present-and-empty gives "". FHIR also registers "other". None DOES
# reach this filter -- there is no sentinel, which is why the rule is "can the
# trial vocabulary express it" and not "is it equal to some magic string".
check("the comparable set is exactly the two the trial vocabulary names",
      sorted(_COMPARABLE_PATIENT_SEXES), ["female", "male"])
# The node this file drives through `module.` is the same object the package
# exports by name -- so `run(_filtering_module, ...)` is testing the shipped
# node and not something a copy shadowed. Asserted rather than assumed, and it
# is also what keeps the direct import from being a declaration nothing reads.
check("the node driven above IS oncotriage.agent.filtering's exported one",
      _filtering_module.node_rule_based_filter is node_rule_based_filter, True)

for _value, _label in ((    "unknown", "the parser's absent-gender value"),
                       (         None, "a gender present and JSON-null"),
                       (           "", "a gender present and empty"),
                       (      "other", "FHIR's registered 'other'"),
                       ("Not Recorded", "free text nothing anticipated")):
    SEX_UNKNOWN_KEPT.clear()
    _r = run(_filtering_module, _POOL_SEX, sex=_value)
    check(f"{_label}: drops nothing", field(_r, "sex_dropped"), 0)
    check(f"{_label}: every sex-specific trial survives",
          field(_r, "candidates_after_rule_filter"), len(_POOL_SEX))
    check(f"{_label}: the four survivals are recorded, keyed by what arrived",
          dict(SEX_UNKNOWN_KEPT),
          {"unknown" if _value is None else str(_value).strip().lower(): 4})

# Stated as its own check because a RAISE and a DROP are different regressions
# with the same symptom for a caller: no trials. run() converts the raise into
# a marker so this one fails rather than aborting the file.
check("a null sex does not RAISE -- the old unguarded .upper() did",
      str(run(_filtering_module, _POOL_SEX, sex=None)).startswith("raised"),
      False)
check("...and it does not drop either",
      field(run(_filtering_module, _POOL_SEX, sex=None), "sex_dropped"), 0)
check("a null or empty TRIAL sex is read as ALL rather than raising",
      field(run(_filtering_module,
                [trial("NCT00000020", sex=None), trial("NCT00000021", sex=""),
                 trial("NCT00000022", sex="MALE")],
                sex="female"), "sex_dropped"), 1)

print()
print("--- 3c. the two records do not describe each other")
SEX_UNKNOWN_KEPT.clear()
_r_known = run(_filtering_module, _POOL_SEX, sex="female")
_known_dropped = field(_r_known, "sex_dropped")
_known_unknown = sum(SEX_UNKNOWN_KEPT.values())
SEX_UNKNOWN_KEPT.clear()
_r_unknown = run(_filtering_module, _POOL_SEX, sex="unknown")
_unknown_dropped = field(_r_unknown, "sex_dropped")
check("a real mismatch is a drop and no unknown-sex survival",
      (_known_dropped, _known_unknown), (2, 0))
check("an unknown sex is a survival and no drop",
      (_unknown_dropped, sum(SEX_UNKNOWN_KEPT.values())), (0, 4))
check("...so one number could not have told them apart, which is why there "
      "are two", _known_dropped != _unknown_dropped, True)


# ===========================================================================
# TEST 4: THE RETURNED DICT GAINED NO RECOVERY-RECORD KEY
# ===========================================================================
# THIS TEST IS ABOUT WHAT MAY NOT BE A KEY, not about the key count. AGE_PARSE_
# FAILURES, AGE_UNIT_ASSUMPTIONS and SEX_UNKNOWN_KEPT are RECOVERY RECORDS --
# "the filter could not decide, so it kept and recorded why" -- and item 11a's
# rule sends those to module-level counters. The three checks under the pinned
# set are that rule, and they are what actually enforces it: they scan the
# returned key set for an unknown-sex or age-unit name rather than trusting the
# literal above.
#
# THE PINNED SET STILL MOVES WHEN A KEY IS ADDED, and it moved here: the
# filter-applied marker pass added eight (stage/histology/age/sex x
# applied/skip_reason). Those are not recovery records -- they are a FILTER'S
# OWN ACCOUNTING, the same admission the three quality_dropped_* keys were made
# on, and each one is the loop-invariant condition the filter itself branches
# on. Re-measured rather than inherited: oncotriage/fixtures/capture.py builds
# its stage4 block by naming keys one at a time, so none of the eight reaches a
# fixture's deterministic prefix and none costs a recapture.

print()
print("=" * 70)
print("Test 4: Stage 4's returned dict gained no recovery-record key")
print("=" * 70)

_EXPECTED_KEYS = sorted([
    "filtered_trials", "candidates_after_rule_filter",
    "candidates_after_quality_filter", "mesh_dropped", "histology_dropped",
    "stage_dropped", "age_dropped", "sex_dropped", "quality_dropped",
    "quality_dropped_percentile", "quality_dropped_floor",
    "quality_dropped_floor_only", "quality_threshold", "mesh_filter_applied",
    "mesh_filter_skip_reason",
    "stage_filter_applied", "stage_filter_skip_reason",
    "histology_filter_applied", "histology_filter_skip_reason",
    "age_filter_applied", "age_filter_skip_reason",
    "sex_filter_applied", "sex_filter_skip_reason",
    "stage_timings",
])
_r_keys = keys_of(run(_filtering_module, _POOL_SEX, sex="unknown"))
check("the key set is exactly the pinned one", _r_keys, _EXPECTED_KEYS)
check("...and it is non-empty (non-degeneracy)", len(_EXPECTED_KEYS) > 10, True)
check("no unknown-sex key leaked into it",
      [k for k in _r_keys if "unknown" in k.lower()], [])
check("no age-unit key leaked into it",
      [k for k in _r_keys if "unit" in k.lower() or "assum" in k.lower()], [])


# ===========================================================================
# TEST 5: NEGATIVE CONTROLS
# ===========================================================================
# Every check above must be shown to be capable of failing. Each control plants
# ONE defect into an in-memory copy of the shipped source; nothing on disk is
# touched, which the hash at the end of this file asserts.

print()
print("=" * 70)
print("Test 5: negative controls, each planted and each fired")
print("=" * 70)

print()
print("--- 5a. the age unit conversion")
_control("CONTROL: without the conversion, 240 Months is 240 YEARS",
         _FILTERING_SRC, [(_CONVERT_LINE, "    return number")],
         lambda m: m._parse_age_bound("240 Months", 999, "max_age"), 240.0)
_control("CONTROL: ...and a 50-year-old is then INSIDE that trial's window, "
         "which is the whole defect",
         _FILTERING_SRC, [(_CONVERT_LINE, "    return number")],
         lambda m: field(m.node_rule_based_filter(
             {"patient_data": {"demographics": {"age": 50, "sex": "female"},
                               "conditions": [],
                               "cancer_stage_observations": []},
              "reranked_trials": [trial("NCT00000002", min_age="6 Months",
                                        max_age="240 Months")],
              "ablation_flags": {}, "patient_trees": set(),
              "stage_timings": {}}), "age_dropped"), 0)
_control("CONTROL: rounding makes six months ZERO -- the boundary moves",
         _FILTERING_SRC,
         [(_CONVERT_LINE, "    return round(number * factor)")],
         lambda m: m._parse_age_bound("6 Months", 0, "min_age"), 0)
_control("CONTROL: rounding makes seven months ONE -- it moves the other way too",
         _FILTERING_SRC,
         [(_CONVERT_LINE, "    return round(number * factor)")],
         lambda m: m._parse_age_bound("7 Months", 0, "min_age"), 1)

print()
print("--- 5b. the unrecognised unit")
_control("CONTROL: a guessed unit returns a number and records NOTHING",
         _FILTERING_SRC, [(_UNKNOWN_UNIT_BLOCK, "            return number")],
         lambda m: (m._parse_age_bound("3 Fortnights", 0, "min_age"),
                    dict(m.AGE_PARSE_FAILURES)), (3.0, {}))

print()
print("--- 5c. the number and the unit read separately")
_control("CONTROL: two independent searches pair 5 with a unit that belongs "
         "to 240",
         _FILTERING_SRC,
         [(_ONE_MATCH_LINES,
           "        number = float(re.findall(r'\\d+(?:\\.\\d+)?', text)[0])\n"
           "        _m = re.search(r'\\d+(?:\\.\\d+)?\\s*([A-Za-z]+)', text)\n"
           "        raw_unit = _m.group(1) if _m else ''")],
         # rounded: 5*(1/12) and 5/12 differ in the last bit, which is not the
         # finding -- the finding is 0.417 years where 5.0 was expected.
         lambda m: round(m._parse_age_bound("5, 240 Months", 0, "min_age"), 9),
         round(5.0 / 12.0, 9))

print()
print("--- 5d. the sex predicate")
_control("CONTROL: the old predicate drops EVERY sex-specific trial for an "
         "unknown sex",
         _FILTERING_SRC,
         [(_SEX_PREDICATE_BLOCK,
           '        if trial_sex not in ["ALL", patient_sex.upper()]:\n'
           '            sex_dropped += 1\n'
           '            continue\n')],
         lambda m: (lambda r: (field(r, "sex_dropped"),
                               field(r, "candidates_after_rule_filter")))(
             run(m, _POOL_SEX, sex="unknown")), (4, 1))
_control("CONTROL: ...while leaving a KNOWN sex alone, so the control is about "
         "the unknown case and not about the filter as a whole",
         _FILTERING_SRC,
         [(_SEX_PREDICATE_BLOCK,
           '        if trial_sex not in ["ALL", patient_sex.upper()]:\n'
           '            sex_dropped += 1\n'
           '            continue\n')],
         lambda m: field(run(m, _POOL_SEX, sex="female"), "sex_dropped"), 2)
_control("CONTROL: the old unguarded .upper() RAISES on a null sex",
         _FILTERING_SRC,
         [(_SEX_NORMALISE_LINES,
           '    patient_sex = demographics.get("sex", "unknown").lower()')],
         # run() converts the raise into a marker; the TYPE is the finding,
         # and the message after the colon is not pinned.
         lambda m: str(run(m, _POOL_SEX, sex=None)).split(":")[0],
         "raised AttributeError")

print()
print("--- 5e. the plant machinery itself")
check("a plant whose target is absent is reported as _PlantFailed",
      _plant_outcome(_FILTERING_SRC, [("not in filtering.py at all", "x")]),
      "_PlantFailed")
check("a plant that produces invalid Python is reported, not raised",
      _plant_outcome(_FILTERING_SRC, [(_CONVERT_LINE, "    return (")]),
      "_PlantFailed")
check("...and a well-formed plant produces no exception at all",
      _plant_outcome(_FILTERING_SRC, [(_CONVERT_LINE, "    return number")]),
      "no exception")


# ===========================================================================
# TEST 6: THE HISTOLOGY-IMPORT TRAP
# ===========================================================================
# THIS IS THE CONTROL THAT CAUGHT A FALSE RESULT, and it is committed here in a
# form that cannot rot.
#
# The original compared the shipped node against the PRE-FIX node read out of
# `git show HEAD:`. It reported "no difference" -- and the reason was not that
# there is none. Exec'ing the pre-fix oncotriage/agent/filtering.py runs its
# `from oncotriage.extraction.histology import ...` line, which resolves to the
# LIVE, ALREADY-FIXED module. So the "old" side ran the NEW extractor and agreed
# with itself.
#
# Two things follow, and both are built in below:
#
#   1. IT MUST NOT READ GIT. HEAD now carries the fix, so a history-based
#      control would compare the fixed module against itself and pass for
#      exactly the reason it exists to catch. It would also DIE rather than
#      report in a tree with no .git -- a git archive export, a shallow clone
#      past the commit -- and this project already has three files with that
#      shape. The defect is planted into an in-memory copy instead.
#   2. THE WIRING MUST BE PROVED, NOT ASSUMED. The trap is asserted directly
#      (a freshly exec'd filtering copy DOES resolve to the live histology
#      module), the rebinding is asserted to have taken, and the two extractors
#      are required to DISAGREE BY BEHAVIOUR before anything built on them is
#      compared.

print()
print("=" * 70)
print("Test 6: the histology-import trap")
print("=" * 70)

# Patient carries lower-case "sclc"; four trials are tagged nsclc and conflict,
# two are tagged sclc and agree, two are untagged and are filtered by nobody.
_LOWER_SCLC_PATIENT = [{"display": "sclc, extensive stage"}]
_POOL_HIST = ([trial(f"NCT0000010{i}", histology_tags=["nsclc"]) for i in range(4)]
              + [trial(f"NCT0000011{i}", histology_tags=["sclc"]) for i in range(2)]
              + [trial(f"NCT0000012{i}") for i in range(2)])

print()
print("--- 6a. the trap is real, asserted rather than described")
try:
    _naive = _plant(_FILTERING_SRC, "f_naive", [])
    check("a filtering module exec'd from its own source resolves its "
          "histology import to the LIVE module -- which is why an 'old' copy "
          "silently runs the NEW extractor",
          _naive.extract_patient_histology is extract_patient_histology, True)
except _PlantFailed as _exc:
    check(f"a filtering copy execs at all  [PLANT FAILED: {_exc}]",
          "plant-failed", True)
    _naive = None

print()
print("--- 6b. the planted extractor differs BY BEHAVIOUR")

# UNCONDITIONAL, and deliberately so. Everything below depends on the plant
# applying, and a plant that cannot apply used to skip the whole section -- so
# reverting histology.py in place produced ONE recorded failure and silently
# dropped nine checks, which reads as a smaller problem than it is. This one
# asserts the SHIPPED side and runs whatever the plant does.
_shipped_tags = sorted(extract_patient_histology(_LOWER_SCLC_PATIENT))
check("the SHIPPED extractor tags a lower-case sclc patient "
      "(non-degeneracy: it must be non-empty for the rest to mean anything)",
      _shipped_tags, ["sclc"])

try:
    _planted_hist = _plant(_HISTOLOGY_SRC, "h_noflag", _HIST_NO_FLAG)
except _PlantFailed as _exc:
    check(f"the histology plant applies  [PLANT FAILED: {_exc}]",
          "plant-failed", True)
    _planted_hist = None

if _planted_hist is not None:
    _planted_tags = sorted(
        _planted_hist.extract_patient_histology(_LOWER_SCLC_PATIENT))
    check("the PLANTED extractor tags nothing, so the plant took -- proved by "
          "what it does, not by trusting the edit", _planted_tags, [])
    check("...and therefore the two extractors DISAGREE",
          _shipped_tags != _planted_tags, True)

print()
print("--- 6c. wiring the planted extractor into a filtering copy")
_old_side = None
if _planted_hist is not None:
    try:
        _old_side = _plant(_FILTERING_SRC, "f_oldhist", [])
    except _PlantFailed as _exc:
        check(f"the filtering copy execs  [PLANT FAILED: {_exc}]",
              "plant-failed", True)

if _old_side is not None and _planted_hist is not None:
    check("before rebinding, the copy is wired to the LIVE extractor",
          _old_side.extract_patient_histology is extract_patient_histology, True)
    _old_side.extract_patient_histology = _planted_hist.extract_patient_histology
    _old_side.is_histology_mismatch = _planted_hist.is_histology_mismatch
    check("after rebinding, it is wired to the PLANTED one -- asserted, "
          "because this is the exact step whose omission produced a false "
          "'no difference'",
          _old_side.extract_patient_histology
          is _planted_hist.extract_patient_histology, True)
    check("...and it is no longer the live one",
          _old_side.extract_patient_histology is extract_patient_histology,
          False)

    print()
    print("--- 6d. the node results differ")
    _r_new = run(_filtering_module, _POOL_HIST, conditions=_LOWER_SCLC_PATIENT)
    _r_old = run(_old_side, _POOL_HIST, conditions=_LOWER_SCLC_PATIENT)
    check("the SHIPPED node drops the four conflicting trials",
          field(_r_new, "histology_dropped"), 4)
    check("...which is non-zero, so the comparison below can fail",
          (field(_r_new, "histology_dropped") or 0) > 0, True)
    check("the PLANTED node drops NOTHING -- an untagged patient is filtered "
          "by nobody", field(_r_old, "histology_dropped"), 0)
    check("...so the two disagree, which the git-based control could not see",
          field(_r_new, "histology_dropped")
          != field(_r_old, "histology_dropped"), True)
    check("and the survivor counts differ by exactly those four",
          (field(_r_old, "candidates_after_rule_filter") or 0)
          - (field(_r_new, "candidates_after_rule_filter") or 0), 4)

    print()
    print("--- 6e. the UPPER-case spelling was never affected")
    _r_upper_new = run(_filtering_module, _POOL_HIST,
                       conditions=[{"display": "SCLC, extensive stage"}])
    _r_upper_old = run(_old_side, _POOL_HIST,
                       conditions=[{"display": "SCLC, extensive stage"}])
    check("both nodes drop the same four for an UPPER-case patient, so the "
          "difference above is the CASE and not the plant at large",
          (field(_r_upper_new, "histology_dropped"),
           field(_r_upper_old, "histology_dropped")),
          (4, 4))


# ===========================================================================
# TEST 7: NOTHING ON DISK WAS TOUCHED
# ===========================================================================

print()
print("=" * 70)
print("Test 7: every plant was in memory")
print("=" * 70)

for _path, _sha in sorted(_SHA_AT_START.items()):
    check(f"{os.path.basename(_path)} is byte-identical to its state at start",
          hashlib.sha256(
              open(_path, encoding="utf-8").read().encode()).hexdigest(), _sha)
check("...and both files were actually hashed (non-degeneracy)",
      len(_SHA_AT_START), 2)


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
Created on Fri Aug  7 2026

@author: ramyalsaffar
"""
