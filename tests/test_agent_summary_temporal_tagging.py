# Stage 5 Summary Temporal Tagging Test
######################################

"""``_create_patient_summary`` states elapsed time beside EVERY date it renders,
and every word of it has to be true of the record it came from.

WHAT THE CHANGE IS (PROMPT_VERSION 1.8.0)
-----------------------------------------
Until 1.8.0 the renderer did date arithmetic in exactly two places -- the
not-active condition marker, and a lab reading past ``config.STALE_LAB_AGE_DAYS``
-- and every other date reached the model raw. 1.8.0 makes the rule uniform:

    WHEREVER THE RENDERER PRINTS A DATE, IT PRINTS THE ELAPSED TIME BESIDE IT.

    Performance Status  the ECOG reading date
    Conditions          every onset, whatever the clinical status. The
                        "not active" marker is unchanged IN SHAPE and still
                        appears only for resolved / inactive / remission.
    Medications         the start date and the end date, each bracketed
    Procedures          the most-recent date per type
    Lab Values          every dated reading, UNGATED
    Metastasis / Biomarkers / mCODE variants
                        each observation's date

    Demographics, Cancer Stage, Allergies and the Tier C / background summary
    lines render NO date and therefore gain NOTHING.

WHY THE FAILURE MODE IS SILENT IN BOTH DIRECTIONS
-------------------------------------------------
A phrase that fails to appear leaves the pre-change prompt, which is the state
that produced the measured misreadings -- a 1997-onset resolved AML quoted as
failing a newly-diagnosed-AML criterion, and a 1993 event judged as falling
inside a five-year window. A phrase that appears WRONG is worse: it asserts an
interval the record cannot support, and the model has no way to check. Neither
shows up as an exception, a counter, or a changed verdict count -- only as
prompt text nobody reads.

THE TWO PROPERTIES THAT ARE NOT ABOUT PRESENCE
-----------------------------------------------
GRADED (section 9a). "less than 1 year" cannot answer a four-week washout
window, and the confirmed arithmetic error sits exactly there: chemotherapy 33
days before the reference date, judged against a four-week window. So an
interval under a year is stated in months or days.

CAPPED AT THE RECORD'S PRECISION (section 9b). ``parse_partial_date`` imputes
the month and day of a "1997" onset from fixed anchors, so a day-grained phrase
built on a year-grained record would state an anchor as a measurement. A
year-precision date may state years only; a month-precision date months at most;
only a day-precision date may state days.

WHAT IS ASKED
-------------
    1-2. resolved / inactive / remission gain the marker AND the onset clause,
         in the shape the constants pin.
    3.   active / recurrence / relapse / unknown gain the ONSET CLAUSE and NOT
         the marker -- the 1.8.0 change, and the one most able to go wrong in
         the damaging direction.
    4.   a resolved-but-UNCONFIRMED condition still gets no marker, and does get
         the bare clause.
    5.   an unusable onset degrades to the part that is still true, in four
         shapes, and the two that are defects are counted.
    6.   every dated lab states its age; an undated one does not; the boundary
         at one completed year.
    7.   per section: dated renders date + phrase, undated renders unchanged,
         and every DATELESS section is byte-identical with the temporal
         machinery neutralised -- with a non-degeneracy probe showing the dated
         ones are not.
    8.   the years follow the reference date and not the clock.
    9.   granularity: graded, capped, and the boundaries of both ladders.
    10.  negative controls: one per new phrase site, each shown to FIRE.
    11.  compute_patient_hash is untouched, behaviourally and structurally.
    12.  PROMPT_VERSION is 1.8.0 and the template says the intervals are there.

THE NEUTRALISED RENDERER, AND WHY IT NEEDS NO GIT
--------------------------------------------------
``render_bare()`` shuts the THREE doors every temporal phrase comes through:
``_NOT_ACTIVE_CLINICAL_STATUSES`` to an empty frozenset, ``_lab_age_suffix`` to
a stand-in returning "", and ``_event_clause`` to a stand-in returning "" (which
is what ``_onset_clause``, ``_dated_suffix`` and the medications section's
``_dated_part`` are all built from). With all three shut the shipped renderer
emits no temporal annotation at all -- no exec, no ``git show``, and this file
runs in a tree with no ``.git``. Section 10c proves the neutralisation is real
rather than assumed.

IT IS DELIBERATELY *NOT* A "PRE-1.8.0" RENDERER, and the distinction is the
reason section 7 is built the way it is. Reconstructing the pre-1.8.0 state
would mean keeping the onset clause alive inside the marker while suppressing
the identical clause on an active condition -- two call sites, one helper, no
seam between them -- so any stand-in that appeared to do it would be a stand-in
whose fidelity nobody could check. What IS checkable is the stronger pair of
statements section 7 makes instead: a section that renders no date is
byte-identical with ALL temporal machinery off, and every section that renders
one is pinned line by line against an expectation computed by a second route.

NEGATIVE CONTROLS (section 10). Each rebinds one shipped name to a stand-in
inside a try/finally and requires the assertion above it to FAIL. Nothing is
written to any file: every plant is an attribute rebind on an imported module
and every patient is a literal dict.

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
import re

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.agent import patient as patient_mod
from oncotriage.agent.patient import (
    BEFORE_REFERENCE_PHRASE,
    ELAPSED_UNDER_DAY,
    ELAPSED_UNDER_MONTH,
    ELAPSED_UNDER_YEAR,
    NOT_ACTIVE_PHRASE,
    ONSET_CLAUSE_PREFIX,
    TEMPORAL_KEY_BIOMARKER_DATE,
    TEMPORAL_KEY_CONDITION_ONSET,
    TEMPORAL_KEY_ECOG_DATE,
    TEMPORAL_KEY_LAB_DATE,
    TEMPORAL_KEY_LAB_STALE,
    TEMPORAL_KEY_MEDICATION_END,
    TEMPORAL_KEY_MEDICATION_START,
    TEMPORAL_KEY_METASTASIS_DATE,
    TEMPORAL_KEY_PROCEDURE_DATE,
    TEMPORAL_KEY_VARIANT_DATE,
    TEMPORAL_RENDER_COUNTS,
    _create_patient_summary,
    _elapsed_phrase,
    _event_clause,
    _lab_age_suffix,
    _not_active_marker,
    _onset_clause,
    _resolve_temporal_date,
    _NOT_ACTIVE_CLINICAL_STATUSES,
    compute_patient_hash,
)
from oncotriage.agent.prompts import PROMPT_VERSION, render_system_prompt
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
# THE DEPENDENCY SEAM, THE REFERENCE DATE, AND THE NEUTRALISED RENDERER
# ===========================================================================
#
# The MeSH filter is overridden to None -- a state the renderer already handles,
# documented at deps.get_mesh_filter() -- which is what keeps this file free of
# the sibling data tree. The cancer and lab registries are the REAL ones: the
# lab registry is what filters and canonicalises the observations this file
# annotates, and a stand-in there would test the annotation against a shape
# production never produces.

deps.set_override(deps.MESH_FILTER, None)

# EVERY EXPECTED INTERVAL IN THIS FILE IS COMPUTED AGAINST THIS DATE, not against
# the shipped DATA_SNAPSHOT_DATE and not against the clock. Patching it is what
# makes section 8 possible at all, and it is restored in the cleanup block.
_REAL_SNAPSHOT = config.DATA_SNAPSHOT_DATE
_PATCHED_SNAPSHOT = "2026-08-03"
config.DATA_SNAPSHOT_DATE = _PATCHED_SNAPSHOT
_REFERENCE = datetime.date(2026, 8, 3)


def render_bare(patient):
    """The renderer with every temporal phrase suppressed.

    Three doors, and they are ALL of them: no condition status is in the
    not-active set, the lab suffix is empty for every reading, and _event_clause
    -- which _onset_clause, _dated_suffix and the medications section's
    _dated_part are each built from -- returns "" for every date. Restored in a
    finally, so a raise in the renderer cannot leave the module neutralised for
    every later section.
    """
    _statuses = patient_mod._NOT_ACTIVE_CLINICAL_STATUSES
    _suffix = patient_mod._lab_age_suffix
    _clause = patient_mod._event_clause
    patient_mod._NOT_ACTIVE_CLINICAL_STATUSES = frozenset()
    patient_mod._lab_age_suffix = lambda _raw, _ref: ""
    patient_mod._event_clause = lambda _raw, _ref, _key: ""
    try:
        return drive(_create_patient_summary, patient)
    finally:
        patient_mod._NOT_ACTIVE_CLINICAL_STATUSES = _statuses
        patient_mod._lab_age_suffix = _suffix
        patient_mod._event_clause = _clause


class rebind:
    """Install a stand-in on the patient module for the body of a `with`.

    Every negative control in section 10 is one of these. Restoration is in a
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


def _patient(patient_id, conditions, observations=(), **over):
    base = {
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
                       "criticality": "high", "onset_date": "2001-06-06"}],
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
    base.update(over)
    return base


# LOINC codes the real OncologyLabRegistry keeps, so the rows survive the
# registry's own filter and reach the annotated line.
_LOINC_ANC = "751-8"
_LOINC_HGB = "718-7"
_LOINC_CREATININE = "2160-0"


def completed_years(earlier, later):
    """Completed years, computed without relativedelta -- a second opinion.

    Every expected YEAR count below comes from here rather than from the shipped
    relativedelta, so an expectation cannot silently agree with a wrong
    implementation by sharing its arithmetic.
    """
    return later.year - earlier.year - (
        (later.month, later.day) < (earlier.month, earlier.day))


def days_before(n):
    """The ISO date exactly n days before the reference."""
    return (_REFERENCE - datetime.timedelta(days=n)).isoformat()


def event(phrase):
    """The full event clause the renderer must emit for a magnitude."""
    return f"{phrase} {BEFORE_REFERENCE_PHRASE}"


def onset(phrase):
    """The full onset clause the renderer must emit for a magnitude."""
    return f"{ONSET_CLAUSE_PREFIX} {event(phrase)}"


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 0 -- the reference date, and the phrase vocabulary
# ===========================================================================
#
# Everything below expects both. Asserting them first means a failure here reads
# as "the patch did not take" or "the wording moved" rather than as thirty wrong
# interval counts.

print("\n0. the reference date and the pinned phrase constants")

check("get_age_reference_date() returns the patched snapshot",
      drive(get_age_reference_date), _REFERENCE)
check("the not-active status set is exactly the three the record can state",
      sorted(_NOT_ACTIVE_CLINICAL_STATUSES), ["inactive", "remission", "resolved"])
check("the marker phrase constant is the one the renderer emits",
      NOT_ACTIVE_PHRASE, "not active")
check("the shared tail every interval ends with",
      BEFORE_REFERENCE_PHRASE, "before reference date")
check("what a condition's interval is anchored to, spelled on the line",
      ONSET_CLAUSE_PREFIX, "onset")
check("the three sub-unit floors, none of them a bare zero",
      [ELAPSED_UNDER_YEAR, ELAPSED_UNDER_MONTH, ELAPSED_UNDER_DAY],
      ["less than 1 year", "less than 1 month", "less than 1 day"])
check("every counter key prefix is distinct, so one field's degradations "
      "cannot be filed under another's",
      len({TEMPORAL_KEY_CONDITION_ONSET, TEMPORAL_KEY_LAB_DATE,
           TEMPORAL_KEY_PROCEDURE_DATE, TEMPORAL_KEY_MEDICATION_START,
           TEMPORAL_KEY_MEDICATION_END, TEMPORAL_KEY_ECOG_DATE,
           TEMPORAL_KEY_METASTASIS_DATE, TEMPORAL_KEY_BIOMARKER_DATE,
           TEMPORAL_KEY_VARIANT_DATE, TEMPORAL_KEY_LAB_STALE}), 10)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- a resolved condition with a usable onset
# ===========================================================================
#
# 1997-08-27 to 2026-08-03 is 28 COMPLETED years: the anniversary has not
# passed. THE MARKER'S SHAPE IS UNCHANGED BY 1.8.0 and this section is the pin
# that says so.

print("\n1. a resolved condition with a usable onset gains the marker")

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
      f"{NOT_ACTIVE_PHRASE}; {onset(f'{_EXPECTED_AML_YEARS} years')}" in _aml_line,
      True)
check("the marker is its own pipe-delimited part",
      f"| {NOT_ACTIVE_PHRASE};" in _aml_line, True)
check("the stated status is still on the line", "| resolved |" in _aml_line, True)
check("and the onset year is still on the line", "| 1997 |" in _aml_line, True)
check("the tag is still last", _aml_line.rstrip().endswith("]"), True)
# THE WHOLE LINE, PINNED. The trailing tag is [comorbidity] because the
# condition carries a placeholder code that the cancer registry does not know,
# and the relevance tier is out of scope for this change -- it is in the
# expectation only so that the marker's POSITION, immediately before the tag and
# after the onset year, is pinned rather than described.
check("the whole line is exactly what the format promises",
      _aml_line,
      f"- Acute myeloid leukemia (disorder) | resolved | 1997 | "
      f"{NOT_ACTIVE_PHRASE}; {onset(f'{_EXPECTED_AML_YEARS} years')} | [comorbidity]")

# THE INTERVAL APPEARS EXACTLY ONCE. The marker ends with the onset clause and
# the renderer's else-branch would emit the same clause again; the `if/else` is
# what prevents it, and a future edit that made the append unconditional would
# print the interval twice on one line.
check("the onset clause appears exactly once on the line",
      _aml_line.count(BEFORE_REFERENCE_PHRASE), 1)

# NOTHING IMPLIES A RESOLUTION DATE. The parser extracts no abatement field, so
# the only date on the line must be the onset.
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
          onset(f"{_years} years") in _line, True)
    check(f"{_status}: the status itself still renders",
          f"| {_status} |" in _line, True)
    check(f"{_status}: and the interval appears exactly once",
          _line.count(BEFORE_REFERENCE_PHRASE), 1)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- THE 1.8.0 CHANGE: a CURRENT condition states its onset interval
# ===========================================================================
#
# THE ONE MOST ABLE TO GO WRONG IN THE DAMAGING DIRECTION. An old ACTIVE
# condition is genuinely current -- a 1997 diabetes diagnosis is not stale -- so
# it must gain the arithmetic and MUST NOT gain any word that implies it is
# over. The clause is asserted present, the marker asserted absent, and the
# whole line pinned so a future edit cannot slip a status word in beside it.

print("\n3. active / recurrence / relapse / unknown state the onset interval "
      "and carry NO marker")

_DM_ONSET = "1997-01-01"
_EXPECTED_DM_YEARS = completed_years(datetime.date(1997, 1, 1), _REFERENCE)

for _status in ("active", "recurrence", "relapse", "unknown"):
    _p = _patient(f"{_status}-case", [
        _condition("Diabetes mellitus type 2 (disorder)", _status, _DM_ONSET),
    ])
    _line = line_for(drive(_create_patient_summary, _p), "Diabetes mellitus")

    check(f"{_status}: the onset clause is present",
          onset(f"{_EXPECTED_DM_YEARS} years") in _line, True)
    check(f"{_status}: and carries no marker", NOT_ACTIVE_PHRASE in _line, False)
    check(f"{_status}: nor any other word implying the condition is over",
          [w for w in ("resolved", "inactive", "remission", "historical",
                       "former", "past") if w in _line.lower()], [])
    check(f"{_status}: the interval appears exactly once",
          _line.count(BEFORE_REFERENCE_PHRASE), 1)

# The status word "unknown" is not rendered by the renderer (it is filtered out
# of the status slot), so its line has one fewer part; the other three carry it.
check("active: the whole line is exactly what the format promises",
      line_for(drive(_create_patient_summary, _patient("active-pin", [
          _condition("Diabetes mellitus type 2 (disorder)", "active", _DM_ONSET)])),
          "Diabetes mellitus"),
      f"- Diabetes mellitus type 2 (disorder) | active | 1997 | "
      f"{onset(f'{_EXPECTED_DM_YEARS} years')} | [comorbidity]")
check("unknown: the status word is still suppressed, and the clause still lands",
      line_for(drive(_create_patient_summary, _patient("unknown-pin", [
          _condition("Diabetes mellitus type 2 (disorder)", "unknown", _DM_ONSET)])),
          "Diabetes mellitus"),
      f"- Diabetes mellitus type 2 (disorder) | 1997 | "
      f"{onset(f'{_EXPECTED_DM_YEARS} years')} | [comorbidity]")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- resolved but unconfirmed
# ===========================================================================
#
# THE EDGE THE MARKER KEYS ON. verification_status "unconfirmed" wins the
# status slot, so the line says "unconfirmed" and never says "resolved". A
# marker here would assert a resolution of a diagnosis the record does not
# confirm was ever made. The INTERVAL is a different matter and is correct: the
# record does carry an onset date, whatever the diagnosis's confirmation state.

print("\n4. a resolved-but-unconfirmed condition gets the clause and no marker")

_p4 = _patient("resolved-unconfirmed", [
    _condition("Possible metastasis to liver", "resolved", "2001-04-05",
               verification="unconfirmed"),
])
_s4 = drive(_create_patient_summary, _p4)
_line4 = line_for(_s4, "Possible metastasis to liver")
_EXPECTED_UNCONF_YEARS = completed_years(datetime.date(2001, 4, 5), _REFERENCE)

check("the status part reads unconfirmed", "| unconfirmed |" in _line4, True)
check("the word resolved is not on the line", "resolved" in _line4, False)
check("and there is no marker", NOT_ACTIVE_PHRASE in _line4, False)
check("but the onset interval IS stated -- the onset date is real either way",
      onset(f"{_EXPECTED_UNCONF_YEARS} years") in _line4, True)

# The same condition WITHOUT the unconfirmed flag does get one -- otherwise the
# marker check above would pass for a renderer that never tags anything.
_line4b = line_for(drive(_create_patient_summary, _patient("confirmed-twin", [
    _condition("Possible metastasis to liver", "resolved", "2001-04-05"),
])), "Possible metastasis to liver")
check("the confirmed twin of the same condition DOES get the marker",
      NOT_ACTIVE_PHRASE in _line4b, True)
check("and both twins state the same interval, so the marker is the only "
      "difference the confirmation flag makes",
      (onset(f"{_EXPECTED_UNCONF_YEARS} years") in _line4b,
       _line4b.count(BEFORE_REFERENCE_PHRASE)), (True, 1))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 -- an onset that cannot anchor an interval
# ===========================================================================
#
# FOUR SHAPES, one behaviour: say the part that is still true and nothing more.
# The last two are degradations and are counted; the first two are ordinary
# records and are not. RUN FOR BOTH ARMS -- the marker arm and 1.8.0's bare-clause
# arm -- because they are different branches of the renderer and only one of
# them existed when this section was first written.

print("\n5. an unusable onset degrades truthfully, in both arms")

_ONSET_CASES = (
    ("absent",          "",            False),
    ("unknown sentinel", "unknown",    False),
    ("unreadable",      "circa 1997",  True),
    ("after reference", "2030-01-01",  True),
)

for _arm, _status, _expect_marker in (("marker", "resolved", True),
                                      ("bare",   "active",   False)):
    for _label, _onset_raw, _counted in _ONSET_CASES:
        _before_count = sum(TEMPORAL_RENDER_COUNTS.values())
        _line = line_for(
            drive(_create_patient_summary,
                  _patient(f"{_arm}-onset-{_label}", [
                      _condition("Concussion injury of brain", _status,
                                 _onset_raw)])),
            "Concussion injury of brain")
        _after_count = sum(TEMPORAL_RENDER_COUNTS.values())

        check(f"{_arm}/{_label}: marker present is {_expect_marker}",
              NOT_ACTIVE_PHRASE in _line, _expect_marker)
        check(f"{_arm}/{_label}: no elapsed clause",
              BEFORE_REFERENCE_PHRASE in _line, False)
        check(f"{_arm}/{_label}: nothing about years, months or days",
              [u for u in ("year", "month", "day") if u in _line.lower()], [])
        check(f"{_arm}/{_label}: counted as a degradation" if _counted
              else f"{_arm}/{_label}: NOT counted -- an absent date is not a defect",
              _after_count > _before_count, _counted)

check("the marker degrades to the bare phrase, as its own part",
      f"| {NOT_ACTIVE_PHRASE} |" in line_for(
          drive(_create_patient_summary, _patient("bare-marker", [
              _condition("Concussion injury of brain", "resolved", "")])),
          "Concussion injury of brain"), True)
check("the unreadable onset was counted under its own key",
      TEMPORAL_RENDER_COUNTS[
          f"{TEMPORAL_KEY_CONDITION_ONSET}_unreadable:unparseable"] > 0, True)
check("the future onset was counted under its own key",
      TEMPORAL_RENDER_COUNTS[f"{TEMPORAL_KEY_CONDITION_ONSET}_after_reference"] > 0,
      True)
check("no counter key carries any clinical text or any raw date",
      [k for k in TEMPORAL_RENDER_COUNTS
       if "concussion" in k.lower() or "1997" in k or "2030" in k], [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 -- labs, ungated
# ===========================================================================
#
# THE GATE IS GONE AND THE BOUNDARY MOVED WITH IT. Before 1.8.0 the boundary
# under test was STALE_LAB_AGE_DAYS, and it decided whether the age appeared at
# all. Now every dated reading states its age and the only boundary left is the
# one completed year at which the phrase switches from days to years -- which is
# a wording boundary, not a visibility one.

print("\n6. every dated lab states its age; an undated one does not")

_OLD_LAB_DATE = "1997-08-27"
_EXPECTED_LAB_YEARS = completed_years(datetime.date(1997, 8, 27), _REFERENCE)
_RECENT_LAB_DATE = days_before(33)

_p6 = _patient("labs", [], observations=[
    _observation("Neutrophils", 2.1, "10*3/uL", _OLD_LAB_DATE, _LOINC_ANC),
    _observation("Hemoglobin", 11.2, "g/dL", _RECENT_LAB_DATE, _LOINC_HGB),
    _observation("Creatinine", 0.9, "mg/dL", "unknown", _LOINC_CREATININE),
])
_labs6 = labs_of(drive(_create_patient_summary, _p6))

check("the old lab states its age inside the parentheses",
      f"({_OLD_LAB_DATE}, {_EXPECTED_LAB_YEARS} years old)" in _labs6, True)
check("the old lab keeps its absolute date too", _OLD_LAB_DATE in _labs6, True)
check("the RECENT lab now states its age too, in days -- the 1.8.0 change",
      line_for(_labs6, "Hemoglobin"),
      f"- Hemoglobin: 11.2 g/dL ({_RECENT_LAB_DATE}, 33 days old)")
check("the undated lab is untouched -- there is nothing to anchor to",
      line_for(_labs6, "Creatinine"), "- Creatinine: 0.9 mg/dL (date unknown)")
check("both dated labs were annotated and the undated one was not",
      (_labs6.count(" old)"), _labs6.count("date unknown)")), (2, 1))

# The one completed year, on both sides, driven through the shipped helper so
# the two dates differ by exactly one day and nothing else.
check("at 365 days the phrase is a completed year",
      drive(_lab_age_suffix, days_before(365), _REFERENCE), ", 1 year old")
check("at 364 days it is still days, and never a bare zero",
      drive(_lab_age_suffix, days_before(364), _REFERENCE), ", 364 days old")
check("a reading dated the reference date itself says less than a day",
      drive(_lab_age_suffix, _REFERENCE.isoformat(), _REFERENCE),
      f", {ELAPSED_UNDER_DAY} old")
check("a future lab date is not annotated and is counted",
      (drive(_lab_age_suffix, "2030-01-01", _REFERENCE),
       TEMPORAL_RENDER_COUNTS[f"{TEMPORAL_KEY_LAB_DATE}_after_reference"] > 0),
      ("", True))
check("an undated lab is not annotated and is NOT counted",
      drive(_lab_age_suffix, "unknown", _REFERENCE), "")

# STALE_LAB_AGE_DAYS still has a reader, and it is a census rather than a gate.
_stale_before = TEMPORAL_RENDER_COUNTS[TEMPORAL_KEY_LAB_STALE]
drive(_lab_age_suffix, days_before(STALE_LAB_AGE_DAYS + 1), _REFERENCE)
_stale_after = TEMPORAL_RENDER_COUNTS[TEMPORAL_KEY_LAB_STALE]
drive(_lab_age_suffix, days_before(STALE_LAB_AGE_DAYS), _REFERENCE)
_stale_at = TEMPORAL_RENDER_COUNTS[TEMPORAL_KEY_LAB_STALE]
check("a reading past STALE_LAB_AGE_DAYS is counted stale",
      _stale_after - _stale_before, 1)
check("a reading AT the threshold is not -- the comparison is strict",
      _stale_at - _stale_after, 0)
check("...and the count decides no rendered character: both are annotated",
      (drive(_lab_age_suffix, days_before(STALE_LAB_AGE_DAYS + 1), _REFERENCE) != "",
       drive(_lab_age_suffix, days_before(STALE_LAB_AGE_DAYS), _REFERENCE) != ""),
      (True, True))


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 7 -- every section, dated and dateless
# ===========================================================================
#
# TWO CLAIMS, AND THEY ARE DIFFERENT CLAIMS. A section that renders a date is
# pinned LINE BY LINE against an expectation built from the constants and a
# second-opinion arithmetic. A section that renders NO date is byte-identical
# with every temporal door shut -- which is stronger than "unchanged by 1.8.0",
# because it also rules out a phrase leaking in from any earlier version.

print("\n7. per section: dated lines annotated, dateless sections untouched")

_PROC_DATE = days_before(40)
_MET_DATE = "2021-03-04"
_BIO_DATE = days_before(120)
_VAR_DATE = days_before(400)
_ECOG_DATE = days_before(214)

_p7 = _patient(
    "mixed",
    [
        _condition("Malignant neoplasm of breast (disorder)", "active",
                   "2020-01-01", code="254837009"),
        _condition("Acute myeloid leukemia (disorder)", "resolved", _AML_ONSET),
        _condition("Concussion injury of brain", "resolved", "unknown"),
        _condition("Possible metastasis to liver", "resolved", "2001-04-05",
                   verification="unconfirmed"),
        # Tier C: rendered as a name in a count line, with no date at all.
        _condition("Dental caries (disorder)", "active", "2019-05-05"),
    ],
    observations=[
        _observation("Neutrophils", 2.1, "10*3/uL", _OLD_LAB_DATE, _LOINC_ANC),
        _observation("Hemoglobin", 11.2, "g/dL", _RECENT_LAB_DATE, _LOINC_HGB),
        {"code": "ZZZ", "display": "EGFR mutation analysis",
         "value": "Positive", "date": _BIO_DATE},
    ],
    medications=[{"display": "Cisplatin 50 MG Injection", "status": "completed",
                  "start_date": days_before(94), "end_date": _PROC_DATE},
                 {"display": "Acetaminophen 325 MG Oral Tablet",
                  "status": "active", "start_date": "2024-02-02"}],
    procedures=[{"display": "Chemotherapy (procedure)", "code": "367336001",
                 "date": _PROC_DATE, "status": "completed"}],
    cancer_metastasis_observations=[
        {"display": "Metastasis to liver", "value": None, "unit": "",
         "metastasis_category": "M", "date": _MET_DATE}],
    cancer_genomic_variants=[
        {"display": "EGFR p.Leu858Arg: Present | Somatic", "gene_symbol": "EGFR",
         "hgvs_protein": "p.Leu858Arg", "result_value": "Present",
         "interpretation": "Positive", "date": _VAR_DATE}],
    ecog_performance_status={"value": 1, "date": _ECOG_DATE,
                             "value_shape": "valueInteger",
                             "observations_found": 1,
                             "observations_on_or_before_reference": 1,
                             "reference_date": _PATCHED_SNAPSHOT,
                             "selection": "most_recent"},
)
_after7 = drive(_create_patient_summary, _p7)
_bare7 = render_bare(_p7)

# --- the dated sections, pinned line by line -------------------------------
_MET_YEARS = completed_years(datetime.date(2021, 3, 4), _REFERENCE)

for _label, _needle, _expected in (
    ("ECOG",
     "ECOG performance status",
     f"- ECOG performance status: 1 ({_ECOG_DATE}, {event('214 days')})"),
    ("procedure",
     "Chemotherapy (procedure)",
     f"- Chemotherapy (procedure) ({_PROC_DATE}, {event('40 days')})"),
    ("medication (both dates, each bracketed)",
     "Cisplatin",
     f"- Cisplatin 50 MG Injection | status: completed | "
     f"start: {days_before(94)} ({event('94 days')}), "
     f"end: {_PROC_DATE} ({event('40 days')})"),
    ("lab",
     "Hemoglobin",
     f"- Hemoglobin: 11.2 g/dL ({_RECENT_LAB_DATE}, 33 days old)"),
    ("metastasis",
     "Metastasis to liver",
     f"- [M] Metastasis to liver ({_MET_DATE}, {event(f'{_MET_YEARS} years')})"),
    ("biomarker",
     "EGFR mutation analysis",
     f"- EGFR mutation analysis: Positive ({_BIO_DATE}, {event('120 days')})"),
    ("mCODE variant",
     "p.Leu858Arg",
     f"- EGFR p.Leu858Arg: Present | Somatic ({_VAR_DATE}, {event('1 year')})"),
):
    check(f"{_label}: the whole line is date + interval",
          line_for(_after7, _needle), _expected)

# --- the dateless sections, byte-identical with every door shut ------------
#
# THREE OF THEM, SLICED APART RATHER THAN LUMPED TOGETHER. The pre-1.8.0 version
# of this file compared "demographics + performance status + stage" as one
# block, which was sound while Performance Status rendered no interval and is
# not now: that block would differ, and lumping it in would either fail for the
# right reason under a misleading name or, once someone "fixed" it by deleting
# the block, stop covering demographics and stage at all.
for _name, _head, _next in (
    ("allergies",    "\nAllergies:\n",    "\nProcedures:"),
    ("cancer stage", "\n\nCancer Stage:", "\n\nConditions:"),
):
    check(f"{_name}: the section was found and is non-empty",
          len(section(_after7, _head, _next)) > len(_head) + 4, True)

check("allergies: byte-identical with every temporal door shut",
      section(_after7, "\nAllergies:\n", "\nProcedures:"),
      section(_bare7, "\nAllergies:\n", "\nProcedures:"))
check("allergies: and it really does carry an onset_date the renderer never "
      "prints, so this is a claim about the RENDERER and not about the data",
      ("onset_date" in _p7["allergies"][0],
       "2001" in section(_after7, "\nAllergies:\n", "\nProcedures:")),
      (True, False))
check("demographics: byte-identical",
      _after7.split("\n\nPerformance Status:")[0],
      _bare7.split("\n\nPerformance Status:")[0])
check("cancer stage: byte-identical",
      section(_after7, "\n\nCancer Stage:", "\n\nConditions:"),
      section(_bare7, "\n\nCancer Stage:", "\n\nConditions:"))
check("the Tier C count line carries no date and no interval",
      line_for(_after7, "Other conditions"),
      "- Other conditions (1): Dental caries (disorder)")
check("the background medication count line likewise",
      line_for(_after7, "Other medications"),
      "- Other medications (1): Acetaminophen 325 MG Oral Tablet")

# NON-DEGENERACY. If render_bare() and the shipped renderer agreed everywhere,
# the byte-identity checks above would pass for a change that did nothing.
check("the comparison discriminates: every dated section DIFFERS",
      [_n for _n, _h, _x in (
          ("performance status", "Performance Status:\n", "\n\nCancer Stage"),
          ("conditions", "\nConditions:\n", "\nMedications:"),
          ("medications", "\nMedications:\n", "\nAllergies:"),
          ("procedures", "\nProcedures:\n", "\nRelevant Lab Values"),
          ("labs", "\nRelevant Lab Values (most recent):\n",
           "\nMetastasis & Nodal Status:"),
          ("metastasis", "\nMetastasis & Nodal Status:\n",
           "\nGenomic & Molecular Biomarkers:"),
       ) if section(_after7, _h, _x) == section(_bare7, _h, _x)], [])
check("...and the biomarkers section, which runs to the end of the summary",
      _after7[_after7.index("\nGenomic & Molecular Biomarkers:\n"):]
      != _bare7[_bare7.index("\nGenomic & Molecular Biomarkers:\n"):], True)
check("the mixed patient rendered all four condition shapes",
      (conditions_of(_after7).count(NOT_ACTIVE_PHRASE),
       conditions_of(_after7).count("| active |"),
       conditions_of(_after7).count("| unconfirmed |")), (2, 1, 1))

# EVERY RENDERED DATE HAS AN INTERVAL BESIDE IT. The structural form of the
# whole change, checked over one patient carrying every dated section at once:
# no line may print a resolvable date without also printing an interval.
#
# THE FIRST VERSION OF THIS SWEEP LOOKED FOR "20NN-" over a hardcoded year
# range and found 7 lines where it demanded 8 -- because a CONDITION line
# renders `onset[:4]`, a bare year with no hyphen, and the one lab it would have
# caught is dated 1997, outside the range someone had typed. A detector built
# from a guessed literal is the defect this project keeps re-finding; this one
# matches the ISO shape the renderer actually emits.
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATE_BEARING = [ln for ln in _after7.splitlines()
                 if ln.startswith("- ") and _ISO_DATE.search(ln)]
check("the sweep found every dated section's line, so an empty or short result "
      "is not why this passes",
      len(_DATE_BEARING), 8)
check("every date-bearing line also states an interval",
      [ln for ln in _DATE_BEARING
       if BEFORE_REFERENCE_PHRASE not in ln and " old)" not in ln], [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 8 -- the reference date, not the clock
# ===========================================================================
#
# THE SAME PATIENT AT THREE REFERENCE DATES MUST GIVE THREE ANSWERS. A clock-
# derived implementation gives the same answer at all three, so this is the one
# check that a hardcoded datetime.today() cannot pass. The proof is behavioural
# and date-independent on purpose: an earlier draft compared the shipped answer
# against today()'s and required them to differ, which is a fact about the day
# the suite is run rather than about the code.

print("\n8. the intervals follow the reference date and not the clock")

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
              onset(f"{_want} years") in _line, True)
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
            _onset_clause, _event_clause, _elapsed_phrase,
            _resolve_temporal_date):
    _called = calls_in(_fn)
    check(f"{_fn.__name__}: the walk found calls, so an empty set is not why "
          f"this passes", len(_called) > 0, True)
    check(f"{_fn.__name__}: calls no clock", sorted(_called & _CLOCK_CALLS), [])

check("the walk's vocabulary is real: it does find get_age_reference_date "
      "where the renderer calls it",
      "get_age_reference_date" in {
          n.func.id for n in ast.walk(
              ast.parse(inspect.getsource(_create_patient_summary).lstrip()))
          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 9 -- granularity: graded and capped
# ===========================================================================

print("\n9. the interval is graded by size and capped by the record's precision")

# --- 9a: the graded ladder at day precision --------------------------------
#
# THE MOTIVATING CASE IS THE 33-DAY ONE. "less than 1 year" cannot answer a
# four-week window; "33 days" answers it by integer comparison, with no rounding
# in either direction.

_GRADED = (
    (0,    ELAPSED_UNDER_DAY),
    (1,    "1 day"),
    (2,    "2 days"),
    (27,   "27 days"),
    (28,   "28 days"),
    (33,   "33 days"),
    (90,   "90 days"),
    (364,  "364 days"),
    (365,  "1 year"),
    (366,  "1 year"),
    (731,  "2 years"),
)
for _n, _want in _GRADED:
    check(f"9a  {_n} days before the reference renders {_want!r}",
          drive(_elapsed_phrase, _REFERENCE,
                _REFERENCE - datetime.timedelta(days=_n), "day"), _want)

check("9a  the ladder is non-degenerate: it produced days, a floor and years",
      sorted({w.split()[-1] for _n, w in _GRADED}), ["day", "days", "year", "years"])
check("9a  the 33-day case is the one the four-week window needs, and it is "
      "NOT rounded to weeks or to a month",
      [u for u in ("week", "month") if u in dict(_GRADED)[33]], [])

# --- 9b: the precision cap -------------------------------------------------
#
# THE SAME ELAPSED TIME AT THREE PRECISIONS. Only the day-precision record may
# speak in days; only day and month may speak in months; every precision may
# speak in completed years, because a year is coarser than any imputation.

_SUB_YEAR = _REFERENCE - datetime.timedelta(days=33)
check("9b  day precision states the exact days",
      drive(_elapsed_phrase, _REFERENCE, _SUB_YEAR, "day"), "33 days")
check("9b  month precision may not, and states months instead",
      drive(_elapsed_phrase, _REFERENCE, _SUB_YEAR, "month"), "1 month")
check("9b  year precision may state neither, and says so",
      drive(_elapsed_phrase, _REFERENCE, _SUB_YEAR, "year"), ELAPSED_UNDER_YEAR)
check("9b  a completed year is speakable at every precision",
      sorted({drive(_elapsed_phrase, _REFERENCE,
                    _REFERENCE - datetime.timedelta(days=800), p)
              for p in ("day", "month", "year")}), ["2 years"])
check("9b  a sub-month interval at month precision hits the month floor",
      drive(_elapsed_phrase, _REFERENCE,
            _REFERENCE - datetime.timedelta(days=3), "month"),
      ELAPSED_UNDER_MONTH)

# THE UNREACHABLE-TODAY BRANCH, DRIVEN RATHER THAN ARGUED. No precision label
# outside the three reaches _elapsed_phrase from the shipped parser, and the
# branch exists so that one added upstream tomorrow degrades to the COARSEST
# claim instead of falling through to the finest. A branch that has never
# executed is a branch nobody has checked the direction of.
check("9b  an unrecognised precision label degrades to the year floor, not to "
      "a day count",
      drive(_elapsed_phrase, _REFERENCE, _SUB_YEAR, "century"),
      ELAPSED_UNDER_YEAR)
check("9b  ...and the parser really does return only the three, so this is a "
      "guard against a future label rather than a live path",
      sorted({drive(_resolve_temporal_date, r, _REFERENCE, "probe")[1]
              for r in ("1997", "1997-08", "1997-08-27",
                        "1997-08-27T11:00:00+00:00")}),
      ["day", "month", "year"])

# ...and END TO END, through the renderer, on the three date shapes the parser
# actually returns those precisions for. This is what says the cap is plumbed
# rather than merely implemented: _resolve_temporal_date used to discard the
# precision, so a helper that respects it while the renderer never receives it
# would pass 9b and fail here.
for _raw, _precision, _want in (
    ("1997-08-27", "day",   f"{_EXPECTED_AML_YEARS} years"),
    ("1997-08",    "month", f"{_EXPECTED_AML_YEARS} years"),
    ("1997",       "year",  "29 years"),
):
    _parsed, _got_precision = drive(_resolve_temporal_date, _raw, _REFERENCE,
                                    "probe")
    check(f"9b  _resolve_temporal_date returns {_precision!r} for {_raw!r}",
          _got_precision, _precision)
    check(f"9b  ...and the rendered condition line states {_want!r}",
          onset(_want) in line_for(drive(_create_patient_summary, _patient(
              f"precision-{_precision}",
              [_condition("Acute myeloid leukemia (disorder)", "active", _raw)])),
              "Acute myeloid leukemia"), True)

# THE ONE THAT WOULD BE FABRICATION. A year-precision onset resolved to a
# concrete day by PARTIAL_DATE_ANCHOR_*; a days phrase built on it would state
# an anchor as a measurement.
_YEAR_ONLY_RECENT = str(_REFERENCE.year)
_parsed_yr, _prec_yr = drive(_resolve_temporal_date, _YEAR_ONLY_RECENT,
                             _REFERENCE, "probe")
check("9b  a year-only date inside the current year parses, so the case is real",
      (_prec_yr, _parsed_yr is not None), ("year", True))
check("9b  ...and it renders the year floor, never a day count",
      drive(_elapsed_phrase, _REFERENCE, _parsed_yr, _prec_yr), ELAPSED_UNDER_YEAR)

# --- 9c: one vocabulary ----------------------------------------------------
#
# The marker's old wording ("less than 1 year") is now the YEAR-PRECISION floor
# of the graded ladder rather than a phrase of its own, so there is one
# vocabulary and not two. Asserted by identity of the constant, and by driving
# the marker to the same string through a year-precision onset.
check("9c  the marker's sub-year wording IS the ladder's year floor",
      drive(_not_active_marker, _YEAR_ONLY_RECENT, _REFERENCE),
      f"{NOT_ACTIVE_PHRASE}; {onset(ELAPSED_UNDER_YEAR)}")
check("9c  and a day-precision recent onset reaches the marker in days, which "
      "the old single-bucket wording could not express",
      drive(_not_active_marker, days_before(33), _REFERENCE),
      f"{NOT_ACTIVE_PHRASE}; {onset('33 days')}")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 10 -- negative controls
# ===========================================================================
#
# Each rebinds ONE shipped name to a stand-in and requires the assertion above
# it to fail. Nothing is written to any file. There is one per NEW phrase site,
# plus the pre-existing three, because a control that only covers the condition
# line proves nothing about the six sections 1.8.0 added.

print("\n10. negative controls -- every check above can fail")

_p10 = _patient("control", [
    _condition("Acute myeloid leukemia (disorder)", "resolved", _AML_ONSET),
    _condition("Diabetes mellitus type 2 (disorder)", "active", _DM_ONSET),
], observations=[
    _observation("Neutrophils", 2.1, "10*3/uL", _OLD_LAB_DATE, _LOINC_ANC),
    _observation("Hemoglobin", 11.2, "g/dL", _RECENT_LAB_DATE, _LOINC_HGB),
])

# 10a  the marker stops being emitted at all -> section 1 fails
with rebind("_NOT_ACTIVE_CLINICAL_STATUSES", frozenset()):
    check("10a with the status set emptied, section 1's marker check fails",
          NOT_ACTIVE_PHRASE in line_for(drive(_create_patient_summary, _p10),
                                        "Acute myeloid leukemia"), False)

# 10b  the marker loses its verbatim phrase -> section 1's phrase check fails
with rebind("_not_active_marker", lambda onset_raw, ref: "historical"):
    _l = line_for(drive(_create_patient_summary, _p10), "Acute myeloid leukemia")
    check("10b with the phrase reworded, section 1's verbatim check fails",
          NOT_ACTIVE_PHRASE in _l, False)
    check("10b and the reworded marker really did reach the line",
          "historical" in _l, True)

# 10c  render_bare() is proved to be a real neutralisation, not a no-op
check("10c render_bare differs from the shipped renderer on _p10",
      drive(_create_patient_summary, _p10) != render_bare(_p10), True)
check("10c and it suppresses EVERY interval, which is what section 7's "
      "byte-identity arm rests on",
      (BEFORE_REFERENCE_PHRASE in render_bare(_p7), " old)" in render_bare(_p7)),
      (False, False))
check("10c and render_bare restored all three names afterwards",
      (patient_mod._NOT_ACTIVE_CLINICAL_STATUSES is _NOT_ACTIVE_CLINICAL_STATUSES,
       patient_mod._lab_age_suffix is _lab_age_suffix,
       patient_mod._event_clause is _event_clause), (True, True, True))

# 10d  THE 1.8.0 CONTROL: the else-branch removed, so a current condition loses
#      its interval -> section 3 fails. The branch is inside the renderer, so
#      the control is the helper it calls.
with rebind("_onset_clause", lambda onset_raw, ref: ""):
    check("10d with the onset clause suppressed, section 3's clause check fails",
          BEFORE_REFERENCE_PHRASE in line_for(
              drive(_create_patient_summary, _p10), "Diabetes mellitus"), False)
    check("10d ...and the marker survives, so the two arms really are separate "
          "code paths and this control is specific to the new one",
          NOT_ACTIVE_PHRASE in line_for(drive(_create_patient_summary, _p10),
                                        "Acute myeloid leukemia"), True)

# 10e  the interval appended to a current condition WITHOUT the onset anchor ->
#      section 3's "anchored" wording is what fails
with rebind("_onset_clause",
            lambda onset_raw, ref: f"29 years {BEFORE_REFERENCE_PHRASE}"):
    _l = line_for(drive(_create_patient_summary, _p10), "Diabetes mellitus")
    check("10e an unanchored interval fails the onset-prefixed expectation",
          onset(f"{_EXPECTED_DM_YEARS} years") in _l, False)
    check("10e and the unanchored form really did reach the line",
          f"29 years {BEFORE_REFERENCE_PHRASE}" in _l, True)

# 10f  a fabricated clause where no onset supports one -> section 5 fails
with rebind("_not_active_marker",
            lambda onset_raw, ref: f"{NOT_ACTIVE_PHRASE}; {onset('29 years')}"):
    check("10f with a fabricated clause, section 5's no-elapsed check fails",
          BEFORE_REFERENCE_PHRASE in line_for(drive(_create_patient_summary,
              _patient("control-noonset", [
                  _condition("Concussion injury of brain", "resolved", "")])),
              "Concussion injury of brain"), True)

# 10g  ONE CONTROL PER NEW PHRASE SITE. _dated_suffix feeds ECOG, procedures,
#      metastasis, biomarkers and variants; suppressing it must remove the
#      interval from all five and from NEITHER of the two that do not use it
#      (conditions, which go through _onset_clause, and labs, which go through
#      _lab_age_suffix). That second half is what makes this control specific.
with rebind("_dated_suffix", lambda raw, ref, key: ""):
    _s10 = drive(_create_patient_summary, _p7)
    for _label, _needle in (("ECOG", "ECOG performance status"),
                            ("procedure", "Chemotherapy (procedure)"),
                            ("metastasis", "Metastasis to liver"),
                            ("biomarker", "EGFR mutation analysis"),
                            ("mCODE variant", "p.Leu858Arg")):
        check(f"10g {_label}: with _dated_suffix suppressed, its interval is gone",
              BEFORE_REFERENCE_PHRASE in line_for(_s10, _needle), False)
    check("10g ...while conditions and labs keep theirs, so the control is "
          "specific to the five sections that use it",
          (BEFORE_REFERENCE_PHRASE
           in line_for(_s10, "Malignant neoplasm of breast"),
           " old)" in labs_of(_s10)), (True, True))

# 10h  the medications site, whose bracketing is its own decision. _event_clause
#      is the helper under it, and suppressing it must take the interval out of
#      the part after the status.
with rebind("_event_clause", lambda raw, ref, key: ""):
    check("10h with _event_clause suppressed, the medication dates lose theirs",
          BEFORE_REFERENCE_PHRASE
          in line_for(drive(_create_patient_summary, _p7), "Cisplatin"), False)
_cis = line_for(_after7, "Cisplatin")
check("10h ...and unsuppressed, each medication date carries its OWN bracketed "
      "interval, so neither can be read against the wrong date -- which is what "
      "RULE 2 makes decide the verdict for a completed therapy",
      (_cis.count(f"({event('94 days')})"), _cis.count(f"({event('40 days')})")),
      (1, 1))

# 10i  every lab annotated with a wrong constant -> section 6 fails
with rebind("_lab_age_suffix", lambda raw, ref: ", 99 years old"):
    check("10i with every lab annotated at 99 years, section 6's line pin fails",
          line_for(labs_of(drive(_create_patient_summary, _p10)), "Hemoglobin"),
          f"- Hemoglobin: 11.2 g/dL ({_RECENT_LAB_DATE}, 99 years old)")

# 10j  the precision cap ignored -> section 9b fails, and the failure is the
#      fabrication the cap exists to prevent: a year-precision record stating
#      a day count computed from an imputed anchor.
#      THE EXPECTED DAY COUNT IS DERIVED, NOT TYPED. It is the distance from the
#      anchor parse_partial_date imputes for a bare year, and the first draft of
#      this control hardcoded a number 43 days out -- which is exactly the class
#      of hand-transcription error the cap exists to keep out of the prompt.
_ANCHORED, _ = drive(_resolve_temporal_date, "1997", _REFERENCE, "probe")
_ANCHORED_DAYS = (_REFERENCE - _ANCHORED).days
with rebind("_elapsed_phrase",
            lambda ref, parsed, precision: f"{(ref - parsed).days} days"):
    check("10j with the cap ignored, a year-only onset states a day count "
          "measured from an anchor the record never stated",
          onset(f"{_ANCHORED_DAYS} days") in line_for(
              drive(_create_patient_summary, _patient(
                  "control-cap", [_condition("Acute myeloid leukemia (disorder)",
                                             "active", "1997")])),
              "Acute myeloid leukemia"), True)
check("10j ...and the anchor really is imputed: the raw date carries no day",
      (_ANCHORED.month, _ANCHORED.day) != (1, 1) or _ANCHORED_DAYS > 0, True)

# 10k  the interval frozen -> section 8's three-date check fails
with rebind("_elapsed_phrase", lambda ref, parsed, precision: "29 years"):
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
    check("10k with the interval frozen, the three reference dates give one answer",
          len(set(_frozen)), 1)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 11 -- the patient content hash is untouched
# ===========================================================================
#
# BOTH WAYS, because either alone is weak. The behavioural half would pass for a
# hash that reads rendered text if the rendering happened not to move; the
# structural half would pass for a hash that reads it under a name this walk
# does not know. Together they say the hash reads the PARSED record -- which is
# why 1.8.0 moves no stored patient_data_hash despite rewriting most of the
# prompt.

print("\n11. compute_patient_hash reads the parsed record, not the rendered text")

_HASH_LIVE = drive(compute_patient_hash, _p7)
_HASH_BARE = (lambda: (
    setattr(patient_mod, "_NOT_ACTIVE_CLINICAL_STATUSES", frozenset()),
    setattr(patient_mod, "_event_clause", lambda r, f, k: ""),
    drive(compute_patient_hash, _p7),
    setattr(patient_mod, "_NOT_ACTIVE_CLINICAL_STATUSES",
            _NOT_ACTIVE_CLINICAL_STATUSES),
    setattr(patient_mod, "_event_clause", _event_clause),
)[2])()

check("the hash is non-degenerate (16 hex characters, not an empty string)",
      isinstance(_HASH_LIVE, str) and len(_HASH_LIVE) == 16, True)
check("the hash is identical with the temporal machinery live and neutralised",
      _HASH_LIVE, _HASH_BARE)
check("...while the rendered summary is NOT, so the comparison discriminates",
      drive(_create_patient_summary, _p7) != render_bare(_p7), True)

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
                            "_event_clause", "_onset_clause", "_dated_suffix",
                            "_resolve_temporal_date",
                            "_NOT_ACTIVE_CLINICAL_STATUSES",
                            "NOT_ACTIVE_PHRASE", "BEFORE_REFERENCE_PHRASE",
                            "STALE_LAB_AGE_DAYS", "get_age_reference_date"}), [])


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 12 -- the version, and the template that describes the record
# ===========================================================================
#
# THE RECORD AND THE TEMPLATE ARE ONE CHANGE. The renderer now states intervals
# and RULE 4 now says they are stated; shipping either alone is the failure --
# a record full of intervals under a rule that says "calculate elapsed time"
# instructs the model to do the arithmetic anyway, and that instruction is what
# the adjudicated 1993-inside-a-five-year-window error was measured under.
#
# tests/test_agent_prompt_version.py owns the version-versus-digest guard. What
# is asserted HERE is the pairing, because this is the file that owns the render.

print("\n12. PROMPT_VERSION and the RULE 4 wording move together")

check("PROMPT_VERSION reads 1.9.0", PROMPT_VERSION, "1.9.0")

_RENDERED_PROMPT = drive(render_system_prompt, True, "applied",
                         "<probe: no patient record>")
for _label, _needle in (
    ("the intervals are declared present", "ELAPSED TIME IS STATED FOR YOU."),
    ("...and are to be read rather than recomputed", "Read them as given."),
    ("the anchor rule", "anchored to the date it is printed beside"),
    ("the no-resolution-date rule", "It is never a resolution date."),
    ("an absent interval means nothing",
     "Absence of an interval is not evidence of anything"),
    # 1.9.0. THE PAIRING THIS SECTION OWNS SURVIVED THE BUMP, AND SHARPENED.
    # 1.8.0 said the renderer states the interval and the rule PREFERS it; 1.9.0
    # says the rule QUOTES it into patient_value and classifies by comparing it,
    # so the record's phrase now has to survive into the model's own output. That
    # is a stronger dependency on this file's renderer than 1.8.0's was, not a
    # weaker one, which is why the needle moved rather than being dropped.
    ("the time-window clause quotes the stated interval into patient_value",
     "Quote the record's stated interval for that event verbatim in "
     "patient_value"),
    ("...and classifies by comparing it to the window",
     "classify by comparing that interval to the window"),
    ("...with a stated fallback when no interval is printed, which is the "
     "renderer's own documented case",
     "If no interval is stated, or the stated one is too coarse"),
):
    check(f"12  {_label}", _needle in _RENDERED_PROMPT, True)

check("the old unconditional imperative is gone",
      "If event end date is known: calculate elapsed time." in _RENDERED_PROMPT,
      False)
# 1.8.0's OWN BRANCH IS GONE TOO. Without this, a template that carried both
# 1.8.0's prefer-the-interval line and 1.9.0's quote-the-interval line would pass
# every check above -- two instructions for one decision, keyed on different
# facts, with the digest moved either way so no guard could tell them apart.
check("...and so is 1.8.0's prefer-the-interval branch it replaced",
      sorted(n for n in ("use the elapsed time the record states beside it",
                         "If event end date is unknown:",
                         "If event end date is known:")
             if n in _RENDERED_PROMPT), [])
check("...non-degeneracy: that scan finds those clauses when they ARE present",
      sorted(n for n in ("use the elapsed time the record states beside it",
                         "If event end date is unknown:",
                         "If event end date is known:")
             if n in _RENDERED_PROMPT
             + "\n    If event end date is known: use the elapsed time the "
               "record states beside it, or calculate it if none is stated."
             + '\n    If event end date is unknown: classification = '
               '"not_evaluable"'),
      ["If event end date is known:", "If event end date is unknown:",
       "use the elapsed time the record states beside it"])


def pos(needle):
    """Where ``needle`` starts, or a NAMED ABSENCE -- never a raise.

    ``str.index`` raises ValueError on a needle that is not there, at module
    level, outside any check(). The revert harness found that the hard way: with
    RULE 4's addition removed from a copy of prompts.py -- the exact edit the two
    checks below exist to catch -- this file died with a traceback and reported
    ZERO failures where it owed several. Seven files in this suite have now had
    to close that hole; this is the eighth, and it is closed with a helper rather
    than a try/except around each call so a future position check cannot
    reintroduce it.
    """
    i = _RENDERED_PROMPT.find(needle) if isinstance(_RENDERED_PROMPT, str) else -1
    return i if i >= 0 else f"<absent: {needle!r}>"


_P_ADDITION = pos("ELAPSED TIME IS STATED FOR YOU.")
_P_RULE4 = pos("RULE 4 -- TEMPORAL REASONING")
_P_RULE5 = pos("RULE 5 -- DIRECT CONTRADICTION CHECK")
check("all three position markers were found, so the two order checks below "
      "are comparing numbers rather than absences",
      [m for m in (_P_ADDITION, _P_RULE4, _P_RULE5) if not isinstance(m, int)],
      [])
check("the addition sits under RULE 4, where the reference date is",
      isinstance(_P_ADDITION, int) and isinstance(_P_RULE4, int)
      and _P_ADDITION > _P_RULE4, True)
check("...and above RULE 5, so it did not land in a later section",
      isinstance(_P_ADDITION, int) and isinstance(_P_RULE5, int)
      and _P_ADDITION < _P_RULE5, True)
# THE EXAMPLES IN THE PROMPT MUST BE SHAPES THE RENDERER CAN ACTUALLY EMIT.
# A worked example the record never produces teaches the model to look for
# something that is not there.
check("every example interval the prompt quotes is a shape the renderer emits",
      [q for q in ('onset 29 years before reference date',
                   '33 days before reference date', '2 years old')
       if q not in _RENDERED_PROMPT], [])
check("...and each of those shapes is reachable from the shipped helpers",
      [onset("29 years"), event("33 days"),
       drive(_lab_age_suffix, days_before(731), _REFERENCE)],
      ["onset 29 years before reference date", "33 days before reference date",
       ", 2 years old"])


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
       patient_mod._event_clause is _event_clause,
       patient_mod._onset_clause is _onset_clause,
       patient_mod.STALE_LAB_AGE_DAYS == STALE_LAB_AGE_DAYS),
      (True, True, True, True, True, True, True))


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
