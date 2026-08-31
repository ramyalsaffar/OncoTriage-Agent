# ECOG Pre-Diagnosis Suppression Test
####################################

"""
An ECOG performance status recorded BEFORE the primary cancer diagnosis is
refused, and the refusal is visible everywhere the selection vocabulary is read.

THE RULING. An ECOG measured before the primary cancer was diagnosed describes
a person who did not yet have the disease. Rendering it as "this patient's
performance status" is a false statement to the model, and it is false in the
flattering direction -- a pre-diagnosis reading is systematically better than
the post-diagnosis one, so it makes an unwell patient look eligible. Measured on
the 1,000-patient corpus: 23 patients, gaps of up to 28 years, one of them an
ECOG 1 recorded in 1997 offered as the performance status of a colon cancer
diagnosed in 2025.

WHAT WAS REJECTED ALONGSIDE IT, so nobody widens this into it later: a general
STALENESS floor. Measured and refused -- it demoted 96% of the scored corpus and
recovered nothing. An old POST-diagnosis score still describes the right person
with the right disease and is kept. There is no age-based cutoff anywhere in
this file or in the code it covers, and section 2 pins that a decades-old
post-diagnosis reading survives.

Covers:
    1. `_ecog_predates_primary_diagnosis` as a PURE function of its two
       arguments -- strictly-before, same-day kept, no anchor, coarse anchor,
       coarse observation -- with the ECOG_ANCHOR_COUNTS key each case returns.
    2. `_select_ecog_performance_status` end to end: the winner is refused, the
       partition counters are unchanged by the refusal, `observations_found`
       still counts what was on the bundle, and the anchor actually applied is
       carried on the record.
    3. `parse_fhir_bundle` end to end on literal bundles, including the one
       thing no unit test can see: that the anchor is the condition list AFTER
       filtering and deduplication.
    4. THE VOCABULARY IS CLOSED AND SHARED. oncotriage.constants owns it; the
       parser, the dashboard breakdown and the drift metric all agree with it.
       This is where the defect that motivated moving it is pinned: the
       dashboard used to key its explanation table on
       'most_recent_on_or_before_reference' -- no trailing `_date` -- so the
       most common path in the pipeline rendered as "unrecognised path", on
       every dashboard, and nothing failed.
    5. THE RENDER says what happened and names the diagnosis anchor rather than
       the snapshot reference date, which had nothing to do with the refusal.
    6. THE DRIFT METRIC counts a suppressed row as unavailable -- by derivation
       rather than by enumeration, which is what let it survive with no edit.
    7. THE CALL SITE is pinned by AST: `parse_fhir_bundle` supplies the anchor,
       and it supplies it from the ONE derivation
       (`registries.primary_cancer.primary_cancer_onset_date`) rather than from
       a second one of its own.
    8. THE PLANT. The predicate is inverted and the end-to-end checks are
       required to flip -- with the control that the SHIPPED predicate gives the
       clean answer on the identical inputs, without which a probe that always
       disagreed would report the plant as caught while measuring nothing.

No network, no keys, NO SPEND, no live Qdrant, no model load, no corpus, no
database, no git history, no live server. It writes NOTHING anywhere, not even a
temp directory, and it EXECS NOTHING and loads no module by location -- the one
plant is an attribute rebind inside try/finally with the restore asserted BY
IDENTITY, which is the natural control for a module-global lookup and needs no
_EXEC_ALLOWLIST entry. NOT in the collision matrix: the three repository files
it READS (oncotriage/fhir/parser.py, oncotriage/agent/patient.py,
oncotriage/dashboard/tabs/performance.py) are written by neither of the suite's
two writers, and are sha256-compared at the end.

IT DOES BUILD THE CANCER REGISTRY, which is `import icd10` on first
construction. That is not incidental: it is the dependency this change adds to
`parse_fhir_bundle`, and a test of the change that avoided it would be avoiding
the thing under test.

Run from terminal (or F5 in Spyder):
    python tests/test_ecog_pre_diagnosis_suppression.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""

import ast
import hashlib
import os
import sys
import textwrap
from typing import Dict, List

try:
    import oncotriage                                          # noqa: F401
except ImportError:                                            # pragma: no cover
    for _candidate in (os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       os.getcwd()):
        if os.path.isfile(os.path.join(_candidate, "oncotriage", "__init__.py")):
            sys.path.insert(0, _candidate)
            print(f"[bootstrap] added {_candidate} to sys.path")
            break
    import oncotriage                                          # noqa: F401

import pandas as pd

from oncotriage.agent import patient as _agent_patient
from oncotriage.agent.patient import _create_patient_summary
from oncotriage.constants import (
    ECOG_SELECTION_ALL_AFTER_REFERENCE,
    ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS,
    ECOG_SELECTION_MOST_RECENT,
    ECOG_SELECTION_NONE_RECORDED,
    ECOG_SELECTION_UNDATED_AMBIGUOUS,
    ECOG_SELECTION_UNDATED_SINGLE,
    ECOG_SELECTION_USABLE,
    ECOG_SELECTION_VALUES,
)
from oncotriage.fhir import parser as _parser
from oncotriage.fhir.parser import (
    ECOG_ANCHOR_COUNTS,
    ECOG_SELECTION_COUNTS,
    _ecog_predates_primary_diagnosis,
    _select_ecog_performance_status,
    parse_fhir_bundle,
)
from oncotriage.monitoring.drift import ecog_unavailable_rate
from oncotriage.registries import primary_cancer as _primary_cancer
from oncotriage.utils import PARTIAL_DATE_DEGRADATIONS, parse_partial_date


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES: List[str] = []


def check(label: str, actual, expected) -> None:
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


def fail(label: str, detail: str) -> None:
    """An outright failure that is not an equality comparison."""
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


def drive(fn, *args, **kwargs):
    """Call `fn` and turn a raise into a VALUE `check` can fail on.

    Every plant in section 8 changes what production code does, and a plant that
    made it raise would otherwise escape through `check()`'s argument list and
    take the summary with it -- the abort shape this project has shipped enough
    times to have a name for. `("RAISED", type, message)` fails every comparison
    below and names what happened.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                     # noqa: BLE001 - reported
        return ("RAISED", type(exc).__name__, str(exc)[:200])


def at(mapping, key, default="<absent>"):
    """`mapping[key]` that reports an absence instead of raising one.

    A defect that DELETES a key is exactly the defect several checks below are
    written for, and a bare subscript would abort on it.
    """
    if not isinstance(mapping, dict):
        return f"<not-a-dict: {type(mapping).__name__}>"
    return mapping.get(key, default)


_PKG_DIR = os.path.dirname(os.path.abspath(oncotriage.__file__))
_PARSER_SRC = os.path.abspath(_parser.__file__)
_PATIENT_SRC = os.path.abspath(_agent_patient.__file__)
_PERF_SRC = os.path.join(_PKG_DIR, "dashboard", "tabs", "performance.py")


def _sha256_of(path: str) -> str:
    return hashlib.sha256(open(path, encoding="utf-8").read().encode()).hexdigest()


# Taken before anything below runs, so the comparison at the end is against a
# real baseline rather than against itself -- the tautology an earlier pass in
# this project shipped by hashing one file twice in one expression.
_SHA_BEFORE = {p: _sha256_of(p) for p in (_PARSER_SRC, _PATIENT_SRC, _PERF_SRC)}


# ===========================================================================
# FIXTURES -- literal, so nothing here depends on a corpus
# ===========================================================================

_DX = "2019-05-26T13:15:53-07:00"          # primary breast cancer onset
_BEFORE_DX = "2013-03-14T11:48:37-07:00"   # six years earlier
_SAME_DAY = "2019-05-26T08:00:00-07:00"    # the screening reading
_AFTER_DX = "2020-03-14T11:48:37-07:00"
_ANCIENT_AFTER_DX = "2019-06-01T09:00:00-07:00"


def obs(date, value=2) -> Dict:
    """One parsed ECOG observation, in the shape _parse_ecog_observation emits."""
    return {"value": value, "value_shape": "valueInteger", "unit": None,
            "date": date, "loinc": "89247-1"}


def bundle(condition_onset=_DX, ecog_dates=(_BEFORE_DX,), ecog_value=2,
           extra_conditions=(), patient_id="pt-1") -> Dict:
    """A decoded FHIR bundle carrying one breast cancer and N ECOG readings.

    SNOMED 254837009 is used because the registry recognises it through its
    SNOMED layer, so this fixture does not depend on the ICD-10-CM release
    version -- only on the release being importable, which is the dependency
    under test.
    """
    entries = [{"resource": {"resourceType": "Patient", "id": patient_id,
                             "gender": "female", "birthDate": "1960-01-01"}}]
    if condition_onset is not None:
        entries.append({"resource": {
            "resourceType": "Condition",
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "verificationStatus": {"coding": [{"code": "confirmed"}]},
            "onsetDateTime": condition_onset,
            "code": {"coding": [{"system": "http://snomed.info/sct",
                                 "code": "254837009",
                                 "display": "Malignant neoplasm of breast (disorder)"}]},
        }})
    for extra in extra_conditions:
        entries.append({"resource": extra})
    for d in ecog_dates:
        res = {"resourceType": "Observation", "status": "final",
               "valueInteger": ecog_value,
               "code": {"coding": [{"system": "http://loinc.org",
                                    "code": "89247-1",
                                    "display": "ECOG Performance Status score"}]}}
        if d is not None:
            res["effectiveDateTime"] = d
        entries.append({"resource": res})
    return {"resourceType": "Bundle", "type": "collection", "entry": entries}


# ===========================================================================
# 1. THE PREDICATE, AS A PURE FUNCTION OF ITS TWO ARGUMENTS
# ===========================================================================

print("\n" + "=" * 70)
print("1. _ecog_predates_primary_diagnosis")
print("=" * 70)

def parsed(date_str):
    """The (date, precision) pair the caller's partition already computed.

    The predicate takes THAT PAIR rather than the observation, because
    parse_partial_date() increments PARTIAL_DATE_DEGRADATIONS on an
    out-of-range component and the partition has already called it on this
    exact field -- see check 1k, which is the measurement.
    """
    return parse_partial_date(date_str)


def predicate(date_str, anchor):
    d, prec = parsed(date_str)
    return drive(_ecog_predates_primary_diagnosis, d, prec, anchor)


check("1a  an observation before the diagnosis is suppressed",
      predicate(_BEFORE_DX, _DX), (True, "compared"))

check("1b  an observation after the diagnosis is kept",
      predicate(_AFTER_DX, _DX), (False, "compared"))

# STRICTLY BEFORE. A reading taken on the day of diagnosis is the baseline
# performance status a trial screens against; refusing it would delete the most
# clinically relevant reading a record can carry.
check("1c  an observation on the diagnosis DAY is kept (strictly-before)",
      predicate(_SAME_DAY, _DX), (False, "compared"))

# THE FAIL-SAFE DIRECTION, all four ways the anchor can be unusable. Every one
# of them KEEPS the observation, and every one is counted under its own key --
# a guard that declines to run must not be indistinguishable from a guard that
# ran and found nothing.
for _label, _anchor in (("None", None), ("empty string", ""),
                        ("unparseable", "not-a-date")):
    _expect = "no_primary_onset" if _anchor in (None, "") else "onset_precision:unparseable"
    check(f"1d  a {_label} anchor keeps the observation and is counted",
          predicate(_BEFORE_DX, _anchor), (False, _expect))

check("1e  a YEAR-precision anchor keeps the observation (mid-range anchoring "
      "could otherwise suppress a post-diagnosis reading)",
      predicate(_BEFORE_DX, "2019"), (False, "onset_precision:year"))

check("1f  a MONTH-precision anchor does the same",
      predicate(_BEFORE_DX, "2019-05"), (False, "onset_precision:month"))

check("1g  an UNDATED observation cannot be compared and is kept",
      predicate(None, _DX), (False, "observation_precision:missing"))

check("1h  a YEAR-precision observation date does the same",
      predicate("2013", _DX), (False, "observation_precision:year"))

# NON-DEGENERACY. Everything above turns on the predicate answering False for
# reasons OTHER than "it always answers False".
check("1i  non-degeneracy: the predicate can answer True at all",
      _ecog_predates_primary_diagnosis(*parsed(_BEFORE_DX), _DX)[0], True)

# THE COUNTER KEY IS RETURNED, NOT COUNTED, which is what keeps this a pure
# function and lets every case above be a different INPUT rather than a patched
# module. If it started counting, calling it twice would double-count.
_before_counts = dict(ECOG_ANCHOR_COUNTS)
_ecog_predates_primary_diagnosis(*parsed(_BEFORE_DX), _DX)
check("1j  the predicate itself increments no counter",
      dict(ECOG_ANCHOR_COUNTS), _before_counts)

# A DATA-QUALITY SIGNAL IS COUNTED ONCE, and this was a defect in the first
# version of this change rather than a hypothetical. The predicate took the
# OBSERVATION and re-parsed its date, while the caller's partition had already
# parsed the same field -- and parse_partial_date() increments
# PARTIAL_DATE_DEGRADATIONS on an out-of-range component, so one bad ECOG date
# was recorded TWICE. Measured, not reasoned about: "2019-02-30" scored
# out_of_range:day = 1 through a bare parse and = 2 through the selection
# function. The fix is that the predicate takes the pair the partition already
# computed, and this is the check that keeps it that way.
_BAD_DATE = "2019-02-30T00:00:00-08:00"
PARTIAL_DATE_DEGRADATIONS.clear()
parse_partial_date(_BAD_DATE)
_bare = dict(PARTIAL_DATE_DEGRADATIONS)
PARTIAL_DATE_DEGRADATIONS.clear()
drive(_select_ecog_performance_status, [obs(_BAD_DATE)], _DX)
_through = dict(PARTIAL_DATE_DEGRADATIONS)
check("1k  an out-of-range ECOG date is counted ONCE through the selection "
      "function, not twice",
      _through, _bare)
check("1k-i  non-degeneracy: that date really is a degradation, so 1k is not "
      "comparing two empty dicts",
      _bare, {"out_of_range:day": 1})
PARTIAL_DATE_DEGRADATIONS.clear()


# ===========================================================================
# 2. THE SELECTION FUNCTION
# ===========================================================================

print("\n" + "=" * 70)
print("2. _select_ecog_performance_status")
print("=" * 70)

ECOG_ANCHOR_COUNTS.clear()
ECOG_SELECTION_COUNTS.clear()

_sup = drive(_select_ecog_performance_status, [obs(_BEFORE_DX)], _DX)
check("2a  a pre-diagnosis winner produces no value", at(_sup, "value"), None)
check("2b  ...and lands in the present-but-unusable family under its own name",
      at(_sup, "selection"), ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS)
check("2c  ...and is NOT 'none_recorded' -- the observation existed",
      at(_sup, "selection") == ECOG_SELECTION_NONE_RECORDED, False)
check("2d  observations_found still counts what was on the bundle",
      at(_sup, "observations_found"), 1)
check("2e  the reference-date partition is untouched by the refusal",
      (at(_sup, "observations_on_or_before_reference"),
       at(_sup, "observations_after_reference"),
       at(_sup, "observations_undated")), (1, 0, 0))
check("2f  the partition still sums to observations_found",
      at(_sup, "observations_on_or_before_reference")
      + at(_sup, "observations_after_reference")
      + at(_sup, "observations_undated"), at(_sup, "observations_found"))
check("2g  the anchor actually applied is carried on the record",
      at(_sup, "primary_diagnosis_date"), _DX)
check("2h  no date, shape or unit is published for a refused observation",
      (at(_sup, "date"), at(_sup, "value_shape"), at(_sup, "unit")),
      (None, None, None))

_kept = drive(_select_ecog_performance_status, [obs(_AFTER_DX)], _DX)
check("2i  a post-diagnosis winner is published",
      (at(_kept, "value"), at(_kept, "selection")),
      (2, ECOG_SELECTION_MOST_RECENT))
check("2j  ...and carries the anchor it was measured against",
      at(_kept, "primary_diagnosis_date"), _DX)

# THE REJECTED RULING, PINNED. A decades-old POST-diagnosis reading is kept.
# This is the check that fails if anyone widens the guard into a staleness
# floor, which was measured and refused.
_old_post = drive(_select_ecog_performance_status,
                  [obs("1999-01-04T09:00:00-08:00")], "1998-01-04T09:00:00-08:00")
check("2k  a 27-year-old POST-diagnosis reading is KEPT (no staleness floor)",
      (at(_old_post, "value"), at(_old_post, "selection")),
      (2, ECOG_SELECTION_MOST_RECENT))

_no_anchor = drive(_select_ecog_performance_status, [obs(_BEFORE_DX)])
check("2l  with NO anchor the observation is kept (fail-safe direction)",
      (at(_no_anchor, "value"), at(_no_anchor, "selection")),
      (2, ECOG_SELECTION_MOST_RECENT))
check("2m  ...and the record says no anchor was applied",
      at(_no_anchor, "primary_diagnosis_date"), None)

# ONLY THE WINNER IS TESTED, AND THAT IS EXACT RATHER THAN APPROXIMATE: the
# winner is the MOST RECENT of the eligible pool, so a pre-diagnosis winner
# means every eligible observation is pre-diagnosis. Both directions driven.
_mixed_kept = drive(_select_ecog_performance_status,
                    [obs(_BEFORE_DX, 3), obs(_ANCIENT_AFTER_DX, 1)], _DX)
check("2n  an older pre-diagnosis reading does not suppress a newer "
      "post-diagnosis one",
      (at(_mixed_kept, "value"), at(_mixed_kept, "selection")),
      (1, ECOG_SELECTION_MOST_RECENT))
_all_pre = drive(_select_ecog_performance_status,
                 [obs(_BEFORE_DX, 3), obs("2010-01-01T00:00:00-08:00", 1)], _DX)
check("2o  when every eligible reading is pre-diagnosis, all of them go",
      (at(_all_pre, "value"), at(_all_pre, "selection"),
       at(_all_pre, "observations_found")),
      (None, ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS, 2))

# THE OTHER UNUSABLE PATHS ARE UNCHANGED, and they must not consume an anchor
# outcome: no comparison happened, so counting one would make the counter's
# total stop meaning "patients that reached the check".
_amb = drive(_select_ecog_performance_status, [obs(None, 1), obs(None, 2)], _DX)
check("2p  undated_ambiguous is unchanged", at(_amb, "selection"),
      ECOG_SELECTION_UNDATED_AMBIGUOUS)
_after_ref = drive(_select_ecog_performance_status, [obs("2099-01-01")], _DX)
check("2q  all_after_reference_date is unchanged", at(_after_ref, "selection"),
      ECOG_SELECTION_ALL_AFTER_REFERENCE)
_none = drive(_select_ecog_performance_status, [], _DX)
check("2r  none_recorded is unchanged", at(_none, "selection"),
      ECOG_SELECTION_NONE_RECORDED)
check("2s  ...and none_recorded carries the anchor field like every other path",
      "primary_diagnosis_date" in (_none if isinstance(_none, dict) else {}), True)

_undated_single = drive(_select_ecog_performance_status, [obs(None, 1)], _DX)
check("2t  undated_single still publishes its score", at(_undated_single, "value"), 1)

# THE COUNTER. Totals are exactly the calls that reached the check: 2a, 2i, 2k,
# 2l, 2n, 2o and 2t -- seven -- and NOT the three paths with no winner.
check("2u  ECOG_ANCHOR_COUNTS totals only the patients that reached the check",
      sum(ECOG_ANCHOR_COUNTS.values()), 7)
check("2v  ...and separates the comparisons from the refusals",
      dict(sorted(ECOG_ANCHOR_COUNTS.items())),
      {"compared": 5, "no_primary_onset": 1, "observation_precision:missing": 1})
check("2w  the selection census saw the new member",
      ECOG_SELECTION_COUNTS[ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS], 2)


# ===========================================================================
# 3. parse_fhir_bundle END TO END
# ===========================================================================

print("\n" + "=" * 70)
print("3. parse_fhir_bundle")
print("=" * 70)

_pre = drive(parse_fhir_bundle, bundle(ecog_dates=(_BEFORE_DX,)))
_pre_ecog = at(_pre, "ecog_performance_status", {})
check("3a  a pre-diagnosis bundle produces no ECOG value",
      at(_pre_ecog, "value"), None)
check("3b  ...under the new selection value",
      at(_pre_ecog, "selection"), ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS)
check("3c  ...with the anchor resolved from the bundle's own conditions",
      at(_pre_ecog, "primary_diagnosis_date"), _DX)

_post = drive(parse_fhir_bundle, bundle(ecog_dates=(_AFTER_DX,)))
check("3d  a post-diagnosis bundle is unaffected",
      (at(at(_post, "ecog_performance_status", {}), "value"),
       at(at(_post, "ecog_performance_status", {}), "selection")),
      (2, ECOG_SELECTION_MOST_RECENT))

# NO CANCER CONDITION AT ALL. `_resolve_primary_cancer_condition` falls back to
# the first valid condition, so the anchor is that condition's onset -- which is
# the SAME fallback `inferences.primary_condition` has always taken. Pinned so
# the two cannot diverge silently.
_non_cancer = {"resourceType": "Condition",
               "clinicalStatus": {"coding": [{"code": "active"}]},
               "verificationStatus": {"coding": [{"code": "confirmed"}]},
               "onsetDateTime": "2005-01-01T00:00:00-08:00",
               "code": {"coding": [{"system": "http://snomed.info/sct",
                                    "code": "44054006",
                                    "display": "Diabetes mellitus type 2 (disorder)"}]}}
_no_cancer = drive(parse_fhir_bundle,
                   bundle(condition_onset=None, ecog_dates=(_BEFORE_DX,),
                          extra_conditions=(_non_cancer,)))
check("3e  a bundle with no cancer falls back to the first valid condition, "
      "exactly as _resolve_primary_cancer does",
      at(at(_no_cancer, "ecog_performance_status", {}), "primary_diagnosis_date"),
      "2005-01-01T00:00:00-08:00")

_no_conditions = drive(parse_fhir_bundle,
                       bundle(condition_onset=None, ecog_dates=(_BEFORE_DX,)))
check("3f  a bundle with no conditions has no anchor and keeps its score",
      (at(at(_no_conditions, "ecog_performance_status", {}), "value"),
       at(at(_no_conditions, "ecog_performance_status", {}), "primary_diagnosis_date")),
      (2, None))

# A REFUTED CANCER MUST NOT ANCHOR ANYTHING. This is the case only the
# end-to-end drive can see: the anchor is resolved from the FILTERED condition
# list, so a refuted diagnosis is gone before it is asked for. Resolved from the
# raw resource sweep it would suppress an ECOG against a diagnosis no other part
# of the pipeline believes in.
_refuted = {"resourceType": "Condition",
            "clinicalStatus": {"coding": [{"code": "active"}]},
            "verificationStatus": {"coding": [{"code": "refuted"}]},
            "onsetDateTime": "2025-01-01T00:00:00-08:00",
            "code": {"coding": [{"system": "http://snomed.info/sct",
                                 "code": "363406005",
                                 "display": "Malignant tumor of colon (disorder)"}]}}
_with_refuted = drive(parse_fhir_bundle,
                      bundle(condition_onset="2010-01-01T00:00:00-08:00",
                             ecog_dates=("2012-01-01T00:00:00-08:00",),
                             extra_conditions=(_refuted,)))
_wr = at(_with_refuted, "ecog_performance_status", {})
check("3g  a REFUTED diagnosis does not anchor the suppression",
      (at(_wr, "value"), at(_wr, "primary_diagnosis_date")),
      (2, "2010-01-01T00:00:00-08:00"))

# NON-DEGENERACY for 3g: the refuted condition really would have suppressed it,
# so 3g is a statement about the filter rather than about an inert fixture.
check("3g-i  non-degeneracy: that refuted onset WOULD have suppressed it",
      _ecog_predates_primary_diagnosis(*parsed("2012-01-01T00:00:00-08:00"),
                                       "2025-01-01T00:00:00-08:00")[0], True)

check("3h  the ECOG field is still present on every patient",
      all(isinstance(at(p, "ecog_performance_status"), dict)
          for p in (_pre, _post, _no_cancer, _no_conditions)), True)


# ===========================================================================
# 4. THE VOCABULARY IS CLOSED, OWNED ONCE, AND AGREED ON BY ITS CONSUMERS
# ===========================================================================

print("\n" + "=" * 70)
print("4. the selection vocabulary")
print("=" * 70)

check("4a  the closed set has six members and no duplicates",
      (len(ECOG_SELECTION_VALUES), len(set(ECOG_SELECTION_VALUES))), (6, 6))
check("4b  the new member is in it",
      ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS in ECOG_SELECTION_VALUES, True)
check("4c  the usable pair is a subset of the closed set, and is exactly two",
      (set(ECOG_SELECTION_USABLE) <= set(ECOG_SELECTION_VALUES),
       len(ECOG_SELECTION_USABLE)), (True, 2))
check("4d  the suppressed path is NOT usable",
      ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS in ECOG_SELECTION_USABLE, False)

# THE PRODUCER WRITES NOTHING OUTSIDE THE SET. Every selection this file has
# driven, plus every literal assignment left in the parser.
_parser_tree = ast.parse(open(_PARSER_SRC, encoding="utf-8").read())
_sel_fn = next((n for n in ast.walk(_parser_tree)
                if isinstance(n, ast.FunctionDef)
                and n.name == "_select_ecog_performance_status"), None)
if _sel_fn is None:
    fail("4e  _select_ecog_performance_status not found in the parser",
         "the AST walk found no such function -- this section proves nothing")
else:
    _assigned = [n.value for n in ast.walk(_sel_fn)
                 if isinstance(n, (ast.Assign, ast.AnnAssign))]
    _string_literals = [v.value for v in _assigned
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)]
    check("4e  the selection function assigns no bare selection STRING -- every "
          "path names a constant",
          [s for s in _string_literals if s in ECOG_SELECTION_VALUES], [])
    _names = sorted({n.id for n in ast.walk(_sel_fn) if isinstance(n, ast.Name)}
                    & set(dir(_parser)))
    check("4e-i  non-degeneracy: the walk reached the function body at all",
          len([n for n in ast.walk(_sel_fn) if isinstance(n, ast.Assign)]) > 5, True)

# THE DASHBOARD AGREES. This is where the drift that motivated moving the
# vocabulary is pinned: the table used to be keyed on retyped strings and one of
# them had lost a trailing `_date`.
_perf_src = open(_PERF_SRC, encoding="utf-8").read()
_perf_tree = ast.parse(_perf_src)
_meaning = next((n for n in ast.walk(_perf_tree)
                 if isinstance(n, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == "_PATH_MEANING"
                         for t in n.targets)), None)
if _meaning is None:
    fail("4f  _PATH_MEANING not found in the dashboard performance tab",
         "the AST walk found no such assignment -- this section proves nothing")
else:
    _keys = _meaning.value.keys
    check("4f  the dashboard explains all six paths",
          len(_keys), len(ECOG_SELECTION_VALUES))
    check("4g  ...and every key is a NAME imported from constants, never a "
          "retyped string (a constant cannot drift from itself)",
          [k for k in _keys if not isinstance(k, ast.Name)], [])
    _key_names = {k.id for k in _keys if isinstance(k, ast.Name)}
    check("4h  ...and the names it uses are exactly the vocabulary's",
          sorted(_key_names),
          sorted(n for n in dir(oncotriage.constants)
                 if n.startswith("ECOG_SELECTION_")
                 and getattr(oncotriage.constants, n) in ECOG_SELECTION_VALUES
                 and isinstance(getattr(oncotriage.constants, n), str)))

# SCANNED AS AST STRING CONSTANTS, NOT AS TEXT, and the first version of this
# check was written as a text scan and FAILED -- on the COMMENT above
# _PATH_MEANING that explains the drift it is checking for. A file that argues
# about its own settings cannot be grepped for them; this project has now met
# that three times. Comments are invisible to ast, so the argument survives and
# only a live literal can fail it.
_perf_strings = {n.value for n in ast.walk(_perf_tree)
                 if isinstance(n, ast.Constant) and isinstance(n.value, str)}
check("4i  no live string literal in the dashboard tab spells a selection path, "
      "truncated or otherwise",
      sorted(s for s in _perf_strings
             if s.startswith("most_recent_on_or_before")
             or s in ECOG_SELECTION_VALUES), [])
check("4i-i  non-degeneracy: the walk collected the file's string literals",
      len(_perf_strings) > 50, True)


# ===========================================================================
# 5. THE RENDER
# ===========================================================================

print("\n" + "=" * 70)
print("5. the Stage 5 patient record")
print("=" * 70)


def _perf_section(summary: str) -> str:
    if not isinstance(summary, str) or "Performance Status:\n" not in summary:
        return f"<no Performance Status section: {str(summary)[:120]}>"
    return summary.split("Performance Status:\n", 1)[1].split("\n\n", 1)[0]


_sup_summary = drive(_create_patient_summary, _pre)
_sup_line = _perf_section(_sup_summary)
check("5a  a suppressed patient does not render a grade",
      "- ECOG performance status: not available" in _sup_line, True)
check("5b  ...and is NOT reported as 'not recorded'",
      "not recorded" in _sup_line, False)
check("5c  ...and states how many observations were on file",
      "1 observation(s) on file, none usable" in _sup_line, True)
check("5d  ...and names the path",
      ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS in _sup_line, True)
check("5e  ...and names the DIAGNOSIS anchor rather than the snapshot",
      "predates the primary cancer diagnosis dated 2019-05-26" in _sup_line, True)
check("5f  ...and does not point the reader at the reference date, which "
      "refused nothing here",
      "reference date" in _sup_line, False)

# THE OTHER UNUSABLE PATHS STILL NAME THE REFERENCE DATE. Branching the tail
# must not have taken it away from the path it belongs to.
_after_patient = drive(parse_fhir_bundle, bundle(ecog_dates=("2099-01-01",)))
_after_line = _perf_section(drive(_create_patient_summary, _after_patient))
check("5g  an after-reference patient still names the reference date",
      "reference date" in _after_line, True)
check("5h  ...and does not claim a diagnosis anchor refused it",
      "predates the primary cancer diagnosis" in _after_line, False)

_scored_line = _perf_section(drive(_create_patient_summary, _post))
check("5i  a scored patient's line is unchanged in shape",
      _scored_line.startswith("- ECOG performance status: 2 ("), True)

_none_patient = drive(parse_fhir_bundle, bundle(ecog_dates=()))
check("5j  a patient with nothing on file still says 'not recorded'",
      "- ECOG performance status: not recorded"
      in _perf_section(drive(_create_patient_summary, _none_patient)), True)

# THE RENDERER DOES NOT RE-DERIVE THE ANCHOR. A second derivation could state a
# diagnosis date the suppression was not measured against.
_patient_tree = ast.parse(open(_PATIENT_SRC, encoding="utf-8").read())
_render_fn = next((n for n in ast.walk(_patient_tree)
                   if isinstance(n, ast.FunctionDef)
                   and n.name == "render_patient_record"), None)
if _render_fn is None:
    fail("5k  render_patient_record not found", "the AST walk found no such function")
else:
    _called = {n.func.id for n in ast.walk(_render_fn)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    _called |= {n.func.attr for n in ast.walk(_render_fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    check("5k  the renderer calls no primary-cancer resolver of its own",
          sorted(c for c in _called if "primary_cancer" in c), [])
    check("5k-i  non-degeneracy: the walk found the calls it was looking through",
          len(_called) > 10, True)


# ===========================================================================
# 6. THE DRIFT METRIC
# ===========================================================================

print("\n" + "=" * 70)
print("6. ecog_unavailable_rate")
print("=" * 70)


def rows(*specs) -> pd.DataFrame:
    out = []
    for selection, value, n in specs:
        out.extend([{"ecog_selection": selection, "ecog_value": value}] * n)
    return pd.DataFrame(out, columns=["ecog_selection", "ecog_value"])


_all_sup = drive(ecog_unavailable_rate,
                 rows((ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS, None, 40)))
check("6a  a corpus of suppressed rows is 100% unavailable",
      at(_all_sup, "metric_value"), 1.0)
check("6b  ...and raises the alert", at(_all_sup, "alert"), 1)

_half = drive(ecog_unavailable_rate,
              rows((ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS, None, 20),
                   (ECOG_SELECTION_MOST_RECENT, 2, 20)))
check("6c  the new path counts in the numerator alongside the scored rows",
      at(_half, "metric_value"), 0.5)

_with_none = drive(ecog_unavailable_rate,
                   rows((ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS, None, 10),
                        (ECOG_SELECTION_NONE_RECORDED, None, 10),
                        (ECOG_SELECTION_MOST_RECENT, 1, 20)))
check("6d  none_recorded still leaves the numerator and stays in the "
      "denominator",
      at(_with_none, "metric_value"), 0.25)

# NON-DEGENERACY. The three readings above are only meaningful because the
# metric can report 0.0 on a corpus with nothing wrong with it.
check("6e  non-degeneracy: a fully scored corpus is 0.0",
      at(drive(ecog_unavailable_rate, rows((ECOG_SELECTION_MOST_RECENT, 1, 40))),
         "metric_value"), 0.0)


# ===========================================================================
# 7. THE CALL SITE, PINNED
# ===========================================================================

print("\n" + "=" * 70)
print("7. parse_fhir_bundle supplies the anchor, from the one derivation")
print("=" * 70)

_bundle_fn = next((n for n in ast.walk(_parser_tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "parse_fhir_bundle"),
                  None)
if _bundle_fn is None:
    fail("7a  parse_fhir_bundle not found in the parser",
         "the AST walk found no such function -- this section proves nothing")
else:
    _calls = [n for n in ast.walk(_bundle_fn)
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "_select_ecog_performance_status"]
    check("7a  it calls the selection function exactly once", len(_calls), 1)
    if _calls:
        _args = _calls[0].args
        check("7b  ...with TWO positional arguments, so the anchor is not left "
              "to the default (which means 'suppress nothing')", len(_args), 2)
        _anchor_arg = _args[1] if len(_args) == 2 else None
        check("7c  ...and the second is primary_cancer_onset_date(...), the same "
              "derivation _resolve_primary_cancer projects",
              isinstance(_anchor_arg, ast.Call)
              and isinstance(_anchor_arg.func, ast.Name)
              and _anchor_arg.func.id == "primary_cancer_onset_date", True)

# ONE DERIVATION, TWO PROJECTIONS. If these two ever answered about different
# conditions, the row's primary_condition and the ECOG's anchor would describe
# different diagnoses and nothing would fail.
_cond_list = [{"display": "Malignant neoplasm of breast (disorder)",
               "code": "254837009", "system_key": "snomed",
               "codings": [{"system_key": "snomed", "code": "254837009",
                            "display": "Malignant neoplasm of breast (disorder)"}],
               "onset_date": _DX, "clinical_status": "active",
               "verification_status": "confirmed"},
              {"display": "Diabetes mellitus type 2 (disorder)",
               "code": "44054006", "system_key": "snomed",
               "codings": [{"system_key": "snomed", "code": "44054006",
                            "display": "Diabetes mellitus type 2 (disorder)"}],
               "onset_date": "2005-01-01", "clinical_status": "active",
               "verification_status": "confirmed"}]
_won = drive(_primary_cancer._resolve_primary_cancer_condition, _cond_list)
check("7d  the display projection is the winning condition's display",
      drive(_primary_cancer._resolve_primary_cancer, _cond_list),
      at(_won, "display"))
check("7e  the onset projection is the winning condition's onset",
      drive(_primary_cancer.primary_cancer_onset_date, _cond_list),
      at(_won, "onset_date"))
check("7f  non-degeneracy: the two projections came from the CANCER, not from "
      "the first condition in the list",
      at(_won, "display"), "Malignant neoplasm of breast (disorder)")
check("7g  an 'unknown' onset is normalised to None, never carried as a string "
      "that sorts above every ISO date",
      drive(_primary_cancer.primary_cancer_onset_date,
            [dict(_cond_list[0], onset_date="unknown")]), None)


# ===========================================================================
# 8. THE PLANT -- an inverted predicate, and the control beside it
# ===========================================================================

print("\n" + "=" * 70)
print("8. planted inversion")
print("=" * 70)

# THE SEAM IS A MODULE-GLOBAL LOOKUP, which is why an attribute rebind reaches
# the caller: _select_ecog_performance_status resolves
# _ecog_predates_primary_diagnosis in the parser's own module dict at CALL time.
# Nothing is exec'd, nothing is written and no module is loaded by location.
_ORIGINAL = _parser._ecog_predates_primary_diagnosis


def _inverted(observation_date, observation_precision, primary_diagnosis_date):
    """The predicate with its comparison the wrong way round.

    Everything else -- the fail-safe returns, the counter keys, the signature --
    is the shipped behaviour, so the ONLY thing this plant changes is the
    direction of the comparison. A plant that changed more than one thing would
    not say which of them the checks below caught.
    """
    suppress, outcome = _ORIGINAL(observation_date, observation_precision,
                                  primary_diagnosis_date)
    if outcome != "compared":
        return suppress, outcome
    return (not suppress), outcome


def _under_plant(fn, *args, **kwargs):
    _parser._ecog_predates_primary_diagnosis = _inverted
    try:
        return drive(fn, *args, **kwargs)
    finally:
        _parser._ecog_predates_primary_diagnosis = _ORIGINAL


# THE CONTROL COMES FIRST. Without it, a probe that disagreed with everything
# would report the plant as caught while measuring nothing at all.
_ctl_pre = drive(_select_ecog_performance_status, [obs(_BEFORE_DX)], _DX)
_ctl_post = drive(_select_ecog_performance_status, [obs(_AFTER_DX)], _DX)
check("8a  CONTROL: the shipped predicate suppresses the pre-diagnosis reading",
      at(_ctl_pre, "selection"), ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS)
check("8b  CONTROL: ...and keeps the post-diagnosis one",
      at(_ctl_post, "selection"), ECOG_SELECTION_MOST_RECENT)

_planted_pre = _under_plant(_select_ecog_performance_status, [obs(_BEFORE_DX)], _DX)
_planted_post = _under_plant(_select_ecog_performance_status, [obs(_AFTER_DX)], _DX)

check("8c  PLANT: the inverted predicate publishes the pre-diagnosis reading, "
      "which the selection check catches",
      at(_planted_pre, "selection") == ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS,
      False)
check("8d  PLANT: ...and it suppresses the post-diagnosis one, which the "
      "kept-reading check catches",
      at(_planted_post, "selection"), ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS)
check("8e  PLANT: the published value flips too, so the check on the VALUE "
      "catches it independently of the check on the selection",
      (at(_planted_pre, "value"), at(_planted_post, "value")), (2, None))

# END TO END, through the real parser and the real renderer, because a plant
# caught only at the unit boundary says nothing about what reaches the model.
_planted_patient = _under_plant(parse_fhir_bundle, bundle(ecog_dates=(_BEFORE_DX,)))
_planted_line = _perf_section(drive(_create_patient_summary, _planted_patient))
check("8f  PLANT: the rendered record states the pre-diagnosis grade, which "
      "check 5a catches",
      "- ECOG performance status: not available" in _planted_line, False)
check("8g  PLANT: ...specifically, it prints the grade the ruling forbids",
      _planted_line.startswith("- ECOG performance status: 2 ("), True)

# THE SEAM ITSELF, asserted rather than assumed: the plant reached the caller.
# Without this, 8c-8g would be equally satisfied by a rebind that reached
# nothing and by a guard that had been deleted.
check("8h  the rebind really reached the caller (seam non-degeneracy)",
      at(_planted_pre, "selection") != at(_ctl_pre, "selection"), True)

# AND THE RESTORE, BY IDENTITY. A restore compared by equality would be
# satisfied by any callable of the same name.
check("8i  the predicate is restored BY IDENTITY",
      _parser._ecog_predates_primary_diagnosis is _ORIGINAL, True)
check("8j  ...and the shipped behaviour is back",
      at(drive(_select_ecog_performance_status, [obs(_BEFORE_DX)], _DX), "selection"),
      ECOG_SELECTION_ALL_BEFORE_PRIMARY_DIAGNOSIS)


# ===========================================================================
# 9. NOTHING IN THE REPOSITORY WAS WRITTEN
# ===========================================================================

print("\n" + "=" * 70)
print("9. the files this test reads are unchanged")
print("=" * 70)

for _path, _sha in sorted(_SHA_BEFORE.items()):
    check(f"9  {os.path.relpath(_path, os.path.dirname(_PKG_DIR))} is byte-identical",
          _sha256_of(_path), _sha)


# ===========================================================================
# SUMMARY
# ===========================================================================

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")

if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(textwrap.indent(f"  - {_f}", ""))

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 31 2026

@author: ramyalsaffar
"""
