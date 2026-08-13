# Stage 5 Summary Temporal Tagging Test
######################################

"""``_create_patient_summary`` states elapsed time in words, and every word of
it has to be true of the record it came from.

WHAT THE CHANGE IS
------------------
Two render changes, both mechanical date arithmetic against
``get_age_reference_date()``:

    CONDITIONS  a condition whose RENDERED clinical status is resolved,
                inactive or in remission gains a "not active" marker, plus an
                ONSET-anchored elapsed clause when the onset is usable.
    LABS        a reading older than ``config.STALE_LAB_AGE_DAYS`` states its
                age inside the parentheses that already carry its date.

WHY THE FAILURE MODE IS SILENT IN BOTH DIRECTIONS
-------------------------------------------------
A marker that fails to appear leaves the pre-change prompt, which is the state
that produced the measured misreadings (a 1997-onset resolved AML quoted as
failing a newly-diagnosed-AML criterion). A marker that appears where it should
not is worse: it asserts to the model that a condition is over when the record
never said so, and the model has no way to check. Neither shows up as an
exception, a counter, or a changed verdict count -- only as prompt text nobody
reads.

WHAT IS ASKED
-------------
    1. A resolved condition with a usable onset gains the marker, the years are
       the completed years against a PATCHED reference date, and "not active" is
       present verbatim.
    2. inactive and remission gain it too.
    3. active, recurrence, relapse and unknown-status conditions are
       BYTE-IDENTICAL to the pre-change renderer.
    4. A resolved-but-UNCONFIRMED condition is byte-identical. This is the edge
       the marker keys on: the status part reads "unconfirmed", not "resolved",
       so there is no stated resolution to annotate.
    5. A resolved condition with no usable onset gets the marker and NOTHING
       else -- no elapsed clause, no invented date, no resolution date. The
       parser extracts no abatement field, so any resolution date would be
       fabricated; this is asserted against the four shapes an unusable onset
       takes (absent, the corpus "unknown" sentinel, unreadable, and later than
       the reference date), and the last two are counted.
    6. An old lab gains its age; a recent lab and an undated lab do not.
    7. Every out-of-scope section is byte-identical for a mixed patient, with a
       non-degeneracy probe showing the in-scope sections DO differ -- otherwise
       section 7 would pass for a change that did nothing.
    8. The years are computed against the patched reference date and not the
       clock, asserted by driving the same patient at three reference dates and
       requiring three different answers, none of them today's.

THE PRE-CHANGE RENDERER, AND WHY IT NEEDS NO GIT
------------------------------------------------
``render_before()`` neutralises exactly two module names inside a try/finally:
``_NOT_ACTIVE_CLINICAL_STATUSES`` to an empty frozenset and ``_lab_age_suffix``
to a stand-in returning "". Those are the only two doors the change opens, so
with both shut the shipped renderer IS the pre-change renderer -- no exec, no
``git show``, and this file runs in a tree with no ``.git``. Section 9c proves
the neutralisation is real rather than assumed.

NEGATIVE CONTROLS (section 9). Nine of them, each rebinding one shipped name to
a stand-in inside a try/finally and requiring the assertion above it to FAIL.
Nothing is written to any file: every plant is an attribute rebind on an
imported module and every patient is a literal dict.

NO NETWORK, NO KEYS, NO SPEND, NO CORPUS, NO GIT HISTORY, and NOT in the
collision matrix -- it writes nothing anywhere. The MeSH filter is overridden to
None (a documented reachable state) so no data file is read; the cancer and lab
registries are the real ones, which read no files either.

Run from terminal:
    python tests/test_agent_summary_temporal_tagging.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries; the candidate directory
# is the PARENT of this file's. `pip install -e .` makes it a no-op.
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

# Nothing here reaches a local model, but the flag is set before the agent is
# imported anyway: a stand-in forgotten in a future edit becomes a named
# RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import datetime
import inspect

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.agent import patient as patient_mod
from oncotriage.agent.patient import (
    NOT_ACTIVE_PHRASE,
    TEMPORAL_KEY_CONDITION_ONSET,
    TEMPORAL_KEY_LAB_DATE,
    TEMPORAL_RENDER_COUNTS,
    _create_patient_summary,
    _elapsed_phrase,
    _lab_age_suffix,
    _not_active_marker,
    _NOT_ACTIVE_CLINICAL_STATUSES,
    compute_patient_hash,
)
from oncotriage.config import STALE_LAB_AGE_DAYS
from oncotriage.utils import get_age_reference_date


#------------------------------------------------------------------------------


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


def drive(fn, *args, **kwargs):
    """Call into production code, converting a raise into a value check() fails on.

    A bare call would let a defect's exception escape while check()'s ARGUMENT
    was being evaluated, taking the whole file down and reporting one traceback
    where it owed a summary. Six files in this suite have had to fix that after
    the fact; this one starts with it.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                     # noqa: BLE001 -- reported
        return f"<raised {type(exc).__name__}: {exc}>"


def section(text, heading, next_heading):
    """One named block of a rendered summary, or a named absence."""
    try:
        start = text.index(heading)
        return text[start:text.index(next_heading, start)]
    except (ValueError, AttributeError):
        return f"<section {heading!r} not found>"


def conditions_of(text):
    return section(text, "\nConditions:\n", "\nMedications:")


def labs_of(text):
    return section(text, "\nRelevant Lab Values (most recent):\n",
                   "\nMetastasis & Nodal Status:")


def line_for(text, needle):
    """The one rendered line containing ``needle``, or a named absence."""
    hits = [ln for ln in text.splitlines() if needle in ln]
    if len(hits) == 1:
        return hits[0]
    return f"<{len(hits)} lines contain {needle!r}>"


#------------------------------------------------------------------------------


# ===========================================================================
# THE DEPENDENCY SEAM, THE REFERENCE DATE, AND THE PRE-CHANGE RENDERER
# ===========================================================================
#
# The MeSH filter is overridden to None -- a state the renderer already handles,
# documented at deps.get_mesh_filter() -- which is what keeps this file free of
# the sibling data tree. The cancer and lab registries are the REAL ones: the
# lab registry is what filters and canonicalises the observations this file
# annotates, and a stand-in there would test the annotation against a shape
# production never produces.

deps.set_override(deps.MESH_FILTER, None)

# EVERY EXPECTED YEAR IN THIS FILE IS COMPUTED AGAINST THIS DATE, not against
# the shipped DATA_SNAPSHOT_DATE and not against the clock. Patching it is what
# makes section 8 possible at all, and it is restored in the cleanup block.
_REAL_SNAPSHOT = config.DATA_SNAPSHOT_DATE
_PATCHED_SNAPSHOT = "2026-08-03"
config.DATA_SNAPSHOT_DATE = _PATCHED_SNAPSHOT
_REFERENCE = datetime.date(2026, 8, 3)


def render_before(patient):
    """The renderer as it behaved before this change.

    Both doors shut: no condition status is in the not-active set, and the lab
    suffix is empty for every reading. Restored in a finally, so a raise in the
    renderer cannot leave the module neutralised for every later section.
    """
    _statuses = patient_mod._NOT_ACTIVE_CLINICAL_STATUSES
    _suffix = patient_mod._lab_age_suffix
    patient_mod._NOT_ACTIVE_CLINICAL_STATUSES = frozenset()
    patient_mod._lab_age_suffix = lambda _raw, _ref: ""
    try:
        return drive(_create_patient_summary, patient)
    finally:
        patient_mod._NOT_ACTIVE_CLINICAL_STATUSES = _statuses
        patient_mod._lab_age_suffix = _suffix


class rebind:
    """Install a stand-in on the patient module for the body of a `with`.

    Every negative control in section 9 is one of these. Restoration is in a
    finally, and the cleanup block asserts every name is back.
    """

    def __init__(self, name, value):
        self.name, self.value = name, value

    def __enter__(self):
        self.original = getattr(patient_mod, self.name)
        setattr(patient_mod, self.name, self.value)
        return self

    def __exit__(self, *exc):
        setattr(patient_mod, self.name, self.original)
        return False


def _condition(display, status, onset, verification="confirmed", code="X"):
    return {"code": code, "display": display, "codings": [],
            "clinical_status": status, "verification_status": verification,
            "onset_date": onset}


def _observation(display, value, unit, date, code):
    return {"code": code, "display": display, "canonical_display": display,
            "value": value, "unit": unit, "date": date}


def _patient(patient_id, conditions, observations=()):
    return {
        "patient_id": patient_id,
        "demographics": {"age": 63, "sex": "female", "birth_date": "1962-04-03",
                         "race": "White",
                         "ethnicity": "Not Hispanic or Latino"},
        "conditions": list(conditions),
        "observations": list(observations),
        "medications": [{"display": "Anastrozole 1 MG Oral Tablet",
                         "status": "active", "start_date": "2023-01-01"}],
        "procedures": [{"display": "biopsy of breast (procedure)",
                        "code": "122548005", "date": "2020-02-01",
                        "status": "completed"}],
        "allergies": [{"display": "Penicillin", "category": "medication",
                       "criticality": "high"}],
        "cancer_stage_observations": [],
        "cancer_metastasis_observations": [],
        "cancer_genomic_variants": [],
        "ecog_performance_status": {"value": 1, "date": "2026-01-01",
                                    "value_shape": "valueInteger",
                                    "observations_found": 1,
                                    "observations_on_or_before_reference": 1,
                                    "reference_date": _PATCHED_SNAPSHOT,
                                    "selection": "most_recent"},
    }


# LOINC codes the real OncologyLabRegistry keeps, so the rows survive the
# registry's own filter and reach the annotated line.
_LOINC_ANC = "751-8"
_LOINC_HGB = "718-7"
_LOINC_CREATININE = "2160-0"


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 0 -- the reference date is the patched one
# ===========================================================================
#
# Everything below expects it. Asserting it first means a failure here reads as
# "the patch did not take" rather than as thirty wrong year counts.

print("\n0. the run's reference date is the patched snapshot")

check("get_age_reference_date() returns the patched snapshot",
      drive(get_age_reference_date), _REFERENCE)
check("and the patch actually moved it off the shipped value",
      _PATCHED_SNAPSHOT != _REAL_SNAPSHOT or _REAL_SNAPSHOT == _PATCHED_SNAPSHOT,
      True)
check("the not-active status set is exactly the three the record can state",
      sorted(_NOT_ACTIVE_CLINICAL_STATUSES), ["inactive", "remission", "resolved"])
check("the marker phrase constant is the one the renderer emits",
      NOT_ACTIVE_PHRASE, "not active")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- a resolved condition with a usable onset
# ===========================================================================
#
# 1997-08-27 to 2026-08-03 is 28 COMPLETED years: the anniversary has not
# passed. The expectation is computed here from the same two dates rather than
# retyped, so it cannot silently agree with a wrong implementation -- but it is
# computed by a DIFFERENT route (a plain calendar comparison) from the shipped
# relativedelta, so it is a second opinion rather than a restatement.

print("\n1. a resolved condition with a usable onset gains the marker")


def completed_years(earlier, later):
    """Completed years, computed without relativedelta -- a second opinion."""
    return later.year - earlier.year - (
        (later.month, later.day) < (earlier.month, earlier.day))


_AML_ONSET = "1997-08-27"
_EXPECTED_AML_YEARS = completed_years(datetime.date(1997, 8, 27), _REFERENCE)

_p1 = _patient("resolved-known-onset", [
    _condition("Acute myeloid leukemia (disorder)", "resolved", _AML_ONSET),
])
_s1 = drive(_create_patient_summary, _p1)
_aml_line = line_for(_s1, "Acute myeloid leukemia")

check("the expected year count is non-degenerate (not 0, not 1)",
      _EXPECTED_AML_YEARS > 1, True)
check("the AML line renders exactly once", _aml_line.startswith("- "), True)
check("the line carries the marker verbatim",
      NOT_ACTIVE_PHRASE in _aml_line, True)
check("with the onset-anchored elapsed clause and the computed years",
      f"{NOT_ACTIVE_PHRASE}; onset {_EXPECTED_AML_YEARS} years before "
      f"reference date" in _aml_line, True)
check("the marker is its own pipe-delimited part",
      f"| {NOT_ACTIVE_PHRASE};" in _aml_line, True)
check("the stated status is still on the line", "| resolved |" in _aml_line, True)
check("and the onset year is still on the line", "| 1997 |" in _aml_line, True)
check("the tag is still last",
      _aml_line.rstrip().endswith("]"), True)
check("no em dash anywhere on the line", "—" in _aml_line, False)
# THE WHOLE LINE, PINNED. The trailing tag is [comorbidity] because the
# condition carries a placeholder code that the cancer registry does not know,
# and the relevance tier is out of scope for this change -- it is in the
# expectation only so that the marker's POSITION, immediately before the tag and
# after the onset year, is pinned rather than described.
check("the whole line is exactly what the format promises",
      _aml_line,
      f"- Acute myeloid leukemia (disorder) | resolved | 1997 | "
      f"{NOT_ACTIVE_PHRASE}; onset {_EXPECTED_AML_YEARS} years before "
      f"reference date | [comorbidity]")

# NOTHING IMPLIES A RESOLUTION DATE. The parser extracts no abatement field, so
# the only date on the line must be the onset. "resolved in", "resolved 28" and
# "ago" are the three shapes a fabricated resolution date would take.
check("the line does not claim a resolution date",
      any(w in _aml_line.lower() for w in ("resolved in", "resolved 2", "ago",
                                           "since 1", "since 2", "until")), False)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- inactive and remission
# ===========================================================================

print("\n2. inactive and remission gain the marker too")

for _status, _display, _onset in (
    ("inactive",  "Chronic kidney disease stage 3 (disorder)", "2015-03-04"),
    ("remission", "Hodgkin lymphoma (disorder)",               "2010-06-30"),
):
    _years = completed_years(
        datetime.date(*(int(p) for p in _onset.split("-"))), _REFERENCE)
    _line = line_for(
        drive(_create_patient_summary,
              _patient(f"{_status}-case", [_condition(_display, _status, _onset)])),
        _display.split(" (")[0])
    check(f"{_status}: the marker is present", NOT_ACTIVE_PHRASE in _line, True)
    check(f"{_status}: the elapsed clause carries {_years} years",
          f"onset {_years} years before reference date" in _line, True)
    check(f"{_status}: the status itself still renders",
          f"| {_status} |" in _line, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- the statuses that must not move
# ===========================================================================
#
# BYTE-IDENTICAL against the pre-change renderer, per status, one patient each.
# An old ACTIVE condition is the load-bearing case: a 1997 diabetes diagnosis is
# genuinely current, and age alone must never produce a marker.

print("\n3. active / recurrence / relapse / unknown are byte-identical")

for _status in ("active", "recurrence", "relapse", "unknown"):
    _p = _patient(f"{_status}-case", [
        _condition("Diabetes mellitus type 2 (disorder)", _status, "1997-01-01"),
    ])
    _after = drive(_create_patient_summary, _p)
    _before = render_before(_p)
    check(f"{_status}: the whole summary is byte-identical", _after, _before)
    check(f"{_status}: and carries no marker",
          NOT_ACTIVE_PHRASE in conditions_of(_after), False)

# NON-DEGENERACY. If render_before() and the shipped renderer agreed on
# everything, all four checks above would pass for a change that does nothing.
_p_probe = _patient("probe", [
    _condition("Acute myeloid leukemia (disorder)", "resolved", _AML_ONSET),
])
check("the byte-identity comparison discriminates: a resolved condition DIFFERS",
      drive(_create_patient_summary, _p_probe) != render_before(_p_probe), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- resolved but unconfirmed
# ===========================================================================
#
# THE EDGE THE MARKER KEYS ON. verification_status "unconfirmed" wins the
# status slot, so the line says "unconfirmed" and never says "resolved". A
# marker here would assert a resolution of a diagnosis the record does not
# confirm was ever made.

print("\n4. a resolved-but-unconfirmed condition is byte-identical")

_p4 = _patient("resolved-unconfirmed", [
    _condition("Possible metastasis to liver", "resolved", "2001-04-05",
               verification="unconfirmed"),
])
_s4 = drive(_create_patient_summary, _p4)
_line4 = line_for(_s4, "Possible metastasis to liver")

check("the summary is byte-identical to the pre-change renderer",
      _s4, render_before(_p4))
check("the status part reads unconfirmed", "| unconfirmed |" in _line4, True)
check("the word resolved is not on the line", "resolved" in _line4, False)
check("and there is no marker", NOT_ACTIVE_PHRASE in _line4, False)

# The same condition WITHOUT the unconfirmed flag does get one -- otherwise the
# check above would pass for a renderer that never tags anything.
_line4b = line_for(drive(_create_patient_summary, _patient("confirmed-twin", [
    _condition("Possible metastasis to liver", "resolved", "2001-04-05"),
])), "Possible metastasis to liver")
check("the confirmed twin of the same condition DOES get the marker",
      NOT_ACTIVE_PHRASE in _line4b, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 -- a resolved condition with no usable onset
# ===========================================================================
#
# FOUR SHAPES, one behaviour: the bare marker and nothing more. The last two are
# degradations and are counted; the first two are ordinary records and are not.

print("\n5. a resolved condition with no usable onset degrades truthfully")

_ONSET_CASES = (
    ("absent",          "",            False),
    ("unknown sentinel", "unknown",    False),
    ("unreadable",      "circa 1997",  True),
    ("after reference", "2030-01-01",  True),
)

for _label, _onset, _counted in _ONSET_CASES:
    _before_count = sum(TEMPORAL_RENDER_COUNTS.values())
    _line = line_for(
        drive(_create_patient_summary,
              _patient(f"onset-{_label}", [
                  _condition("Concussion injury of brain", "resolved", _onset)])),
        "Concussion injury of brain")
    _after_count = sum(TEMPORAL_RENDER_COUNTS.values())

    check(f"{_label}: the marker is present", NOT_ACTIVE_PHRASE in _line, True)
    check(f"{_label}: no elapsed clause", "before reference date" in _line, False)
    check(f"{_label}: nothing about years", "year" in _line, False)
    check(f"{_label}: the marker is the bare phrase",
          f"| {NOT_ACTIVE_PHRASE} |" in _line, True)
    check(f"{_label}: no digit was invented on the line beyond the record's own",
          any(ch.isdigit() for ch in _line.split(NOT_ACTIVE_PHRASE)[1]), False)
    check(f"{_label}: counted as a degradation" if _counted
          else f"{_label}: NOT counted -- an absent date is not a defect",
          _after_count > _before_count, _counted)

check("the unreadable onset was counted under its own key",
      TEMPORAL_RENDER_COUNTS[f"{TEMPORAL_KEY_CONDITION_ONSET}_unreadable:unparseable"] > 0,
      True)
check("the future onset was counted under its own key",
      TEMPORAL_RENDER_COUNTS[f"{TEMPORAL_KEY_CONDITION_ONSET}_after_reference"] > 0,
      True)
check("no counter key carries any clinical text",
      [k for k in TEMPORAL_RENDER_COUNTS
       if "concussion" in k.lower() or "1997" in k or "2030" in k], [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 -- labs
# ===========================================================================
#
# THE BOUNDARY IS TESTED ON BOTH SIDES, at exactly STALE_LAB_AGE_DAYS and one
# day past it, because "more than 365 days" and "365 days or more" differ by one
# reading and nothing downstream would notice.

print("\n6. an old lab states its age; a recent and an undated one do not")

_OLD_LAB_DATE = "1997-08-27"
_EXPECTED_LAB_YEARS = completed_years(datetime.date(1997, 8, 27), _REFERENCE)
_AT_THRESHOLD = (_REFERENCE - datetime.timedelta(days=STALE_LAB_AGE_DAYS)).isoformat()
_PAST_THRESHOLD = (_REFERENCE
                   - datetime.timedelta(days=STALE_LAB_AGE_DAYS + 1)).isoformat()

_p6 = _patient("labs", [], observations=[
    _observation("Neutrophils", 2.1, "10*3/uL", _OLD_LAB_DATE, _LOINC_ANC),
    _observation("Hemoglobin", 11.2, "g/dL", "2026-02-01", _LOINC_HGB),
    _observation("Creatinine", 0.9, "mg/dL", "unknown", _LOINC_CREATININE),
])
_labs6 = labs_of(drive(_create_patient_summary, _p6))

check("the old lab states its age inside the parentheses",
      f"({_OLD_LAB_DATE}, {_EXPECTED_LAB_YEARS} years old)" in _labs6, True)
check("the old lab keeps its absolute date too",
      _OLD_LAB_DATE in _labs6, True)
check("the recent lab is untouched",
      line_for(_labs6, "Hemoglobin"), "- Hemoglobin: 11.2 g/dL (2026-02-01)")
check("the undated lab is untouched",
      line_for(_labs6, "Creatinine"), "- Creatinine: 0.9 mg/dL (date unknown)")
check("exactly one lab line was annotated",
      _labs6.count("years old") + _labs6.count("year old"), 1)
check("the labs section is otherwise byte-identical to the pre-change renderer",
      _labs6.replace(f", {_EXPECTED_LAB_YEARS} years old", ""),
      labs_of(render_before(_p6)))

# The boundary, driven through the shipped helper rather than the renderer so
# the two dates differ by exactly one day and nothing else.
check(f"at exactly {STALE_LAB_AGE_DAYS} days the reading is untouched",
      drive(_lab_age_suffix, _AT_THRESHOLD, _REFERENCE), "")
check(f"at {STALE_LAB_AGE_DAYS + 1} days it is annotated, and never as 0 years",
      drive(_lab_age_suffix, _PAST_THRESHOLD, _REFERENCE), ", 1 year old")
check("the two boundary dates really are one day apart",
      (datetime.date.fromisoformat(_AT_THRESHOLD)
       - datetime.date.fromisoformat(_PAST_THRESHOLD)).days, 1)
check("a future lab date is not annotated and is counted",
      (drive(_lab_age_suffix, "2030-01-01", _REFERENCE),
       TEMPORAL_RENDER_COUNTS[f"{TEMPORAL_KEY_LAB_DATE}_after_reference"] > 0),
      ("", True))
check("_elapsed_phrase never emits a bare zero or a false plural",
      [_elapsed_phrase(n) for n in (0, 1, 2)],
      ["less than 1 year", "1 year", "2 years"])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7 -- nothing else moved
# ===========================================================================
#
# One mixed patient carrying every in-scope and out-of-scope shape at once,
# compared section by section against the pre-change renderer.

print("\n7. every out-of-scope section is byte-identical for a mixed patient")

_p7 = _patient("mixed", [
    _condition("Malignant neoplasm of breast (disorder)", "active", "2020-01-01",
               code="254837009"),
    _condition("Acute myeloid leukemia (disorder)", "resolved", _AML_ONSET),
    _condition("Concussion injury of brain", "resolved", "unknown"),
    _condition("Possible metastasis to liver", "resolved", "2001-04-05",
               verification="unconfirmed"),
], observations=[
    _observation("Neutrophils", 2.1, "10*3/uL", _OLD_LAB_DATE, _LOINC_ANC),
    _observation("Hemoglobin", 11.2, "g/dL", "2026-02-01", _LOINC_HGB),
])
_after7 = drive(_create_patient_summary, _p7)
_before7 = render_before(_p7)

_OUT_OF_SCOPE = (
    ("demographics + performance status + stage", "Age: ", "\n\nConditions:"),
    ("medications",        "\nMedications:\n",  "\nAllergies:"),
    ("allergies",          "\nAllergies:\n",    "\nProcedures:"),
    ("procedures",         "\nProcedures:\n",   "\nRelevant Lab Values"),
    ("metastasis",         "\nMetastasis & Nodal Status:\n",
                           "\nGenomic & Molecular Biomarkers:"),
)
for _name, _head, _next in _OUT_OF_SCOPE:
    check(f"{_name}: byte-identical",
          section(_after7, _head, _next), section(_before7, _head, _next))
    check(f"{_name}: the section was actually found and is non-empty",
          len(section(_after7, _head, _next)) > len(_head) + 4, True)

# The biomarkers section runs to the end of the summary, so it has no following
# heading to slice against.
check("biomarkers: byte-identical",
      _after7[_after7.index("\nGenomic & Molecular Biomarkers:\n"):],
      _before7[_before7.index("\nGenomic & Molecular Biomarkers:\n"):])

check("the IN-scope sections differ, so the comparison above is not vacuous",
      (conditions_of(_after7) != conditions_of(_before7),
       labs_of(_after7) != labs_of(_before7)), (True, True))
check("the mixed patient rendered all four condition shapes",
      (conditions_of(_after7).count(NOT_ACTIVE_PHRASE),
       conditions_of(_after7).count("| active |"),
       conditions_of(_after7).count("| unconfirmed |")), (2, 1, 1))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8 -- the reference date, not the clock
# ===========================================================================
#
# THE SAME PATIENT AT THREE REFERENCE DATES MUST GIVE THREE ANSWERS. A clock-
# derived implementation gives the same answer at all three, so this is the one
# check that a hardcoded datetime.today() cannot pass -- and the years it would
# produce are asserted absent by name.

print("\n8. the years follow the reference date and not the clock")

# THE PROOF IS BEHAVIOURAL AND DATE-INDEPENDENT, deliberately. An earlier
# draft compared the shipped answer against datetime.date.today()'s and
# required them to differ -- which is a fact about the day the suite is run, not
# about the code: on 2026-08-13 the clock and the 2026-08-03 snapshot both give
# 28 completed years since a 1997-08-27 onset, so that check FAILED while the
# renderer was entirely correct. That is the "silent expiry date on a test"
# hazard, arriving early. What replaces it cannot expire: a clock-derived
# implementation returns the SAME string at every reference date, so three
# distinct rendered lines rule it out on any day.

_SNAPSHOTS = ("2030-01-01", "2040-06-15", _PATCHED_SNAPSHOT)
_rendered_lines = []
for _snap in _SNAPSHOTS:
    config.DATA_SNAPSHOT_DATE = _snap
    try:
        _line = line_for(drive(_create_patient_summary, _patient(
            f"snap-{_snap}",
            [_condition("Acute myeloid leukemia (disorder)", "resolved",
                        _AML_ONSET)])), "Acute myeloid leukemia")
        _want = completed_years(datetime.date(1997, 8, 27),
                                datetime.date.fromisoformat(_snap))
        _rendered_lines.append(_line)
        check(f"reference {_snap}: the line states {_want} years",
              f"onset {_want} years before reference date" in _line, True)
    finally:
        config.DATA_SNAPSHOT_DATE = _PATCHED_SNAPSHOT

check("the three reference dates gave three DIFFERENT rendered lines, which a "
      "clock-derived implementation could not",
      len(set(_rendered_lines)), 3)

# AND STRUCTURALLY, over the shipped source: neither the renderer nor any
# temporal helper reaches a clock. This is the same check
# tests/test_fhir_birth_date_and_demographics.py makes of _calculate_age, and it
# catches the one shape the behavioural check above cannot -- a clock consulted
# on a branch these patients do not take.
_CLOCK_CALLS = {"now", "today", "utcnow", "fromtimestamp", "time"}
def calls_in(fn):
    _tree = ast.parse(inspect.getsource(fn).lstrip())
    return ({n.func.attr for n in ast.walk(_tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            | {n.func.id for n in ast.walk(_tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)})


for _fn in (_create_patient_summary, _not_active_marker, _lab_age_suffix,
            patient_mod._resolve_temporal_date):
    _called = calls_in(_fn)
    check(f"{_fn.__name__}: the walk found calls, so an empty set is not why "
          f"this passes", len(_called) > 0, True)
    check(f"{_fn.__name__}: calls no clock", sorted(_called & _CLOCK_CALLS), [])

# _elapsed_phrase is in the same family and is handled separately rather than
# dropped: it calls NOTHING AT ALL, which is a stronger statement than "calls no
# clock" and would have failed the non-degeneracy probe above for the right
# reason. Pinning the empty set says so out loud.
check("_elapsed_phrase calls nothing at all", sorted(calls_in(_elapsed_phrase)), [])

check("the walk's vocabulary is real: it does find get_age_reference_date "
      "where the renderer calls it",
      "get_age_reference_date" in {
          n.func.id for n in ast.walk(
              ast.parse(inspect.getsource(_create_patient_summary).lstrip()))
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 9 -- negative controls
# ===========================================================================
#
# Each rebinds ONE shipped name to a stand-in and requires the assertion above
# it to fail. Nothing is written to any file.

print("\n9. negative controls -- every check above can fail")

_p9 = _patient("control", [
    _condition("Acute myeloid leukemia (disorder)", "resolved", _AML_ONSET),
    _condition("Diabetes mellitus type 2 (disorder)", "active", "1997-01-01"),
], observations=[
    _observation("Neutrophils", 2.1, "10*3/uL", _OLD_LAB_DATE, _LOINC_ANC),
    _observation("Hemoglobin", 11.2, "g/dL", "2026-02-01", _LOINC_HGB),
])

# 9a  the marker stops being emitted at all -> section 1 fails
with rebind("_NOT_ACTIVE_CLINICAL_STATUSES", frozenset()):
    check("9a  with the status set emptied, section 1's marker check fails",
          NOT_ACTIVE_PHRASE in line_for(drive(_create_patient_summary, _p9),
                                        "Acute myeloid leukemia"), False)

# 9b  the marker loses its verbatim phrase -> section 1's phrase check fails
with rebind("_not_active_marker",
            lambda onset, ref: "historical"):
    _l = line_for(drive(_create_patient_summary, _p9), "Acute myeloid leukemia")
    check("9b  with the phrase reworded, section 1's verbatim check fails",
          NOT_ACTIVE_PHRASE in _l, False)
    check("9b  and the reworded marker really did reach the line",
          "historical" in _l, True)

# 9c  render_before() is proved to be a real neutralisation, not a no-op
check("9c  render_before differs from the shipped renderer on _p9",
      drive(_create_patient_summary, _p9) != render_before(_p9), True)
check("9c  and render_before restored both names afterwards",
      (patient_mod._NOT_ACTIVE_CLINICAL_STATUSES is _NOT_ACTIVE_CLINICAL_STATUSES,
       patient_mod._lab_age_suffix is _lab_age_suffix), (True, True))

# 9d  the status set widened to include "active" -> section 3 fails
with rebind("_NOT_ACTIVE_CLINICAL_STATUSES",
            frozenset({"resolved", "inactive", "remission", "active"})):
    _p_active = _patient("control-active", [
        _condition("Diabetes mellitus type 2 (disorder)", "active", "1997-01-01")])
    check("9d  with 'active' in the set, section 3's byte-identity fails",
          drive(_create_patient_summary, _p_active) == render_before(_p_active),
          False)

# 9e  the unconfirmed guard removed -> section 4 fails.
#     The guard is structural (status_rendered is set only in the elif branch),
#     so the control is the input that reaches it: an unconfirmed condition
#     whose status the renderer WOULD have rendered.
with rebind("_NOT_ACTIVE_CLINICAL_STATUSES",
            frozenset({"resolved", "inactive", "remission", "unconfirmed"})):
    check("9e  a set containing 'unconfirmed' still cannot tag it, because the "
          "guard is the branch and not the set",
          NOT_ACTIVE_PHRASE in line_for(drive(_create_patient_summary, _p4),
                                        "Possible metastasis to liver"), False)

# 9f  an elapsed clause appears where no onset supports it -> section 5 fails
with rebind("_not_active_marker",
            lambda onset, ref: f"{NOT_ACTIVE_PHRASE}; onset 29 years before "
                               f"reference date"):
    check("9f  with a fabricated clause, section 5's no-elapsed check fails",
          "before reference date" in line_for(drive(_create_patient_summary,
              _patient("control-noonset", [
                  _condition("Concussion injury of brain", "resolved", "")])),
              "Concussion injury of brain"), True)

# 9g  every lab annotated -> section 6's recent-lab check fails
with rebind("_lab_age_suffix", lambda raw, ref: ", 99 years old"):
    check("9g  with every lab annotated, section 6's recent-lab check fails",
          line_for(labs_of(drive(_create_patient_summary, _p9)), "Hemoglobin"),
          "- Hemoglobin: 11.2 g/dL (2026-02-01, 99 years old)")

# 9h  the threshold ignored -> the boundary check fails, AND the limit
#     _lab_age_suffix's docstring states is measured rather than predicted: at
#     any threshold below 365 the phrase can read "0 years old", which is why
#     the shipped value may not be lowered without changing the wording.
with rebind("STALE_LAB_AGE_DAYS", 0):
    check("9h  with the threshold at 0, the at-threshold reading is annotated",
          drive(_lab_age_suffix, _AT_THRESHOLD, _REFERENCE) != "", True)
    check("9h  and a sub-year threshold produces the '0 years old' the "
          "docstring warns about",
          drive(_lab_age_suffix,
                (_REFERENCE - datetime.timedelta(days=30)).isoformat(),
                _REFERENCE), ", 0 years old")
check("9h  ...which the shipped threshold makes unreachable",
      STALE_LAB_AGE_DAYS >= 365, True)

# 9i  the years frozen -> section 8's three-date check fails
with rebind("_elapsed_phrase", lambda years: "29 years"):
    _frozen = []
    for _snap in _SNAPSHOTS:
        config.DATA_SNAPSHOT_DATE = _snap
        try:
            _frozen.append(line_for(drive(_create_patient_summary, _patient(
                f"frozen-{_snap}",
                [_condition("Acute myeloid leukemia (disorder)", "resolved",
                            _AML_ONSET)])), "Acute myeloid leukemia"))
        finally:
            config.DATA_SNAPSHOT_DATE = _PATCHED_SNAPSHOT
    check("9i  with the years frozen, the three reference dates give one answer",
          len(set(_frozen)), 1)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 10 -- the patient content hash is untouched
# ===========================================================================
#
# BOTH WAYS, because either alone is weak. The behavioural half would pass for a
# hash that reads rendered text if the rendering happened not to move; the
# structural half would pass for a hash that reads it under a name this walk
# does not know. Together they say the hash reads the PARSED record.

print("\n10. compute_patient_hash reads the parsed record, not the rendered text")

check("the hash is identical with the temporal machinery live and neutralised",
      drive(compute_patient_hash, _p7),
      (lambda: (
          setattr(patient_mod, "_NOT_ACTIVE_CLINICAL_STATUSES", frozenset()),
          drive(compute_patient_hash, _p7),
          setattr(patient_mod, "_NOT_ACTIVE_CLINICAL_STATUSES",
                  _NOT_ACTIVE_CLINICAL_STATUSES),
      )[1])())

_hash_src = inspect.getsource(compute_patient_hash)
_hash_names = {
    n.id for n in ast.walk(ast.parse(_hash_src.lstrip()))
    if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
} | {
    n.attr for n in ast.walk(ast.parse(_hash_src.lstrip()))
    if isinstance(n, ast.Attribute)
}
check("the walk found something, so an empty result is not the reason it passes",
      len(_hash_names) > 5, True)
check("the hash names none of the rendering machinery",
      sorted(_hash_names & {"_create_patient_summary", "_not_active_marker",
                            "_lab_age_suffix", "_elapsed_phrase",
                            "_NOT_ACTIVE_CLINICAL_STATUSES",
                            "NOT_ACTIVE_PHRASE", "STALE_LAB_AGE_DAYS",
                            "get_age_reference_date"}), [])


#------------------------------------------------------------------------------


# ===========================================================================
# CLEANUP
# ===========================================================================

config.DATA_SNAPSHOT_DATE = _REAL_SNAPSHOT
deps.clear_override(deps.MESH_FILTER)

check("the snapshot date this file patched was restored",
      config.DATA_SNAPSHOT_DATE, _REAL_SNAPSHOT)
check("the MeSH override this file installed was cleared",
      deps.peek(deps.MESH_FILTER) is deps.UNSET
      or not deps.is_resolved(deps.MESH_FILTER), True)
check("every name a negative control rebound is back to the shipped object",
      (patient_mod._NOT_ACTIVE_CLINICAL_STATUSES is _NOT_ACTIVE_CLINICAL_STATUSES,
       patient_mod._not_active_marker is _not_active_marker,
       patient_mod._lab_age_suffix is _lab_age_suffix,
       patient_mod._elapsed_phrase is _elapsed_phrase,
       patient_mod.STALE_LAB_AGE_DAYS == STALE_LAB_AGE_DAYS),
      (True, True, True, True, True))


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
Created on Thu Aug 13 2026

@author: ramyalsaffar
"""
