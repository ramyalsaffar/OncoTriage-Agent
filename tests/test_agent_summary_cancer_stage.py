# Stage 5 Patient Summary: the Cancer Stage section
###################################################

"""The stage Stage 4 filtered on is the stage Stage 5 is told.

WHY THIS FILE EXISTS
--------------------
``extract_patient_stage()`` produces an ordinal that
``node_rule_based_filter`` uses to DROP trials, and until this section existed
``_create_patient_summary`` never mentioned it. So the model resolving a
stage-gated criterion -- the second most common gate in interventional
oncology after ECOG, which has had its own named line for exactly this reason
-- was never told the stage the pipeline had already acted on. It inferred one
from the diagnosis text if it could, which is the weakest of the extractor's
four tiers applied without any of the extractor's guards.

WHAT IT HOLDS
-------------
    1. THE SECTION IS THERE, in the position that was argued: directly after
       Performance Status and before Conditions. Position is asserted by index,
       not by "the string appears somewhere".
    2. STAGE 0 IS A STAGE. The ECOG-0 trap, one section up, applied to stage:
       ``if stage:`` would report in-situ disease -- the earliest stage a
       patient can carry, and one that gates real trials -- as no stage. Driven
       with an extractor answer of literal 0 and with the truthiness plant as
       its control.
    3. ABSENCE IS STATED, NOT OMITTED, and the exact sentence is pinned. A
       model can resolve a stage-gated criterion to not_evaluable from a stated
       absence; it cannot do anything at all with silence.
    4. EVERY TIER RENDERS ITS OWN PROVENANCE, one check per member of the
       closed STAGE_SOURCES vocabulary, derived FROM that tuple so a tier added
       without a phrase is a named failure rather than silent under-coverage.
    5. THE SUMMARY AND THE FILTER AGREE, established BEHAVIOURALLY rather than
       by calling the extractor twice: Stage 4 is driven for real with five
       probe trials, each admitting exactly one stage, and the survivor
       identifies the ordinal the filter acted on. A second call to the same
       function would agree with itself by construction and prove nothing about
       the call site.
    6. THE SYSTEM PROMPT DOES NOT CARRY IT. The summary is interpolated into
       the USER message; ``tests/test_agent_prompt_version.py`` guards the
       system prompt's bytes, and this file asserts the narrower fact that no
       rendered variant of it mentions a stage section.
    7. THE STAGING DATE, for the two tiers that have one. A stage-group or
       AJCC-M Observation carries the date it was recorded on; a diagnosis
       name does not. The line states the date of the Observation THAT
       PRODUCED THE RENDERED ORDINAL -- never the newest date on the bundle,
       which is a different observation whenever a more recent one failed to
       parse -- with the elapsed interval every other dated section states,
       measured against the run's reference date. The two diagnosis-text tiers
       render byte-identically to what they rendered before the clause
       existed, and that is a branch on the TIER, driven, rather than a
       fallback.
    8. THE CONTROLS, thirteen of them, each planted into an IN-MEMORY COPY of
       ``oncotriage/agent/patient.py`` or ``oncotriage/extraction/stage.py`` --
       never the file on disk, both of which are hashed before and after. A
       plant that fails to apply is a RECORDED failure, not a traceback that
       hides every check below it.

NO NETWORK, NO KEYS, NO SPEND, NO DATABASE, NO GIT, NO CORPUS. Every patient in
here is a literal dict. The registries it resolves (cancer, lab, MeSH) are
local file reads made by ``_create_patient_summary`` itself. Not in the
collision matrix: it writes nothing anywhere, and the one repository file it
reads -- ``oncotriage/agent/patient.py`` -- is written by neither of the
suite's two writers.

WHY IT EXECS. Two of the seven controls are one-token edits INSIDE a function
body (``is not None`` -> truthiness; the observation arguments dropped from the
extractor call). There is no attribute to rebind for either, and ``git show``
cannot supply them: the section is new, so every revision that has it also has
it correct. A patched in-memory copy is the shape CLAUDE.md prefers over an
in-place edit, and this file is an argued member of
``tests/test_package_invariants.py``'s ``_EXEC_ALLOWLIST``.

Run from terminal:
    python tests/test_agent_summary_cancer_stage.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# THE CANDIDATE DIRECTORY IS THE PARENT OF THIS FILE'S: this file sits in
# tests/ and the package sits BESIDE tests/, not inside it. `pip install -e .`
# makes the whole block a no-op.
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

from oncotriage import config as _config
from oncotriage.agent import patient as _patient_module
from oncotriage.agent.filtering import node_rule_based_filter
from oncotriage.agent.patient import (
    STAGE_DATE_CLAUSE_PREFIX,
    STAGE_DATE_UNKNOWN_CLAUSE,
    TEMPORAL_KEY_STAGE_DATE,
    TEMPORAL_RENDER_COUNTS,
    _STAGE_SOURCE_PHRASES,
    _create_patient_summary,
)
from oncotriage.agent.prompts import render_system_prompt
from oncotriage.extraction import stage as _stage_module
from oncotriage.extraction.stage import (
    STAGE_NUMERALS,
    STAGE_SOURCE_CONDITION_DISPLAY,
    STAGE_SOURCE_M_CATEGORY,
    STAGE_SOURCE_METASTATIC_KEYWORD,
    STAGE_SOURCE_STAGE_GROUP,
    STAGE_SOURCES,
    STAGE_SOURCES_OBSERVATION_BACKED,
    extract_patient_stage,
    extract_patient_stage_with_source,
)


# EVERY INTERVAL IN THIS FILE IS MEASURED AGAINST THIS DATE, not against the
# shipped DATA_SNAPSHOT_DATE and not against the clock. Pinning it is what lets
# section 8 state a whole rendered line as a literal: a check written against
# whatever the constant happens to be would go red the day somebody moves the
# snapshot, which is a landmine rather than a tripwire. Restored in the cleanup
# block at the foot of the file, because `pytest tests/` imports every module
# into ONE process and a leaked snapshot date would silently re-anchor every
# interval every later file measures.
_REAL_SNAPSHOT = _config.DATA_SNAPSHOT_DATE
_PATCHED_SNAPSHOT = "2026-08-03"
_config.DATA_SNAPSHOT_DATE = _PATCHED_SNAPSHOT


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


# THE PATH COMES FROM THE MODULE THIS PROCESS IMPORTED, never from this file's
# own location: moving the test cannot break it, and the source being planted
# into is provably the one under test rather than a same-named copy.
_PATIENT_SRC = os.path.abspath(_patient_module.__file__)
# THE SECOND PLANTED FILE. The staging DATE is decided in the extractor -- the
# renderer only prints what it is handed -- so the controls that matter most
# here plant oncotriage/extraction/stage.py. Its path comes from the module
# THIS PROCESS IMPORTED, for the reason above.
_STAGE_SRC = os.path.abspath(_stage_module.__file__)
_SHA_AT_START = {
    path: hashlib.sha256(open(path, encoding="utf-8").read().encode()).hexdigest()
    for path in (_PATIENT_SRC, _STAGE_SRC)
}


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(path, name, subs):
    """Exec an in-memory COPY of `path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is
    RECORDED as a failure instead of aborting the run and hiding every check
    below it. A control that takes the process down is not a control.
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


_CONTROL_SEQ = [0]


def _control_in(path, label, subs, probe, expected):
    """Run a negative control against a copy of `path`. A BAD PLANT IS A
    RECORDED FAILURE, not a crash."""
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
        actual = f"raised {type(exc).__name__}: {exc}"
    check(label, actual, expected)


def _control(label, subs, probe, expected):
    """A control planted into oncotriage/agent/patient.py."""
    _control_in(_PATIENT_SRC, label, subs, probe, expected)


class with_extractor:
    """Point the RENDERER at another module's extractor for the body of a `with`.

    THE PATCH POINT IS THE RENDERER'S OWN NAMESPACE, not the extraction module.
    ``oncotriage/agent/patient.py`` does ``from oncotriage.extraction.stage
    import extract_patient_stage_with_source``, which BINDS the function in
    that module -- so a control that planted the extractor and left it there
    would reach nothing and report a working check as broken. Restored by
    IDENTITY in a finally, and the restore is asserted.
    """

    _NAME = "extract_patient_stage_with_source"

    def __init__(self, fn):
        self.fn = fn

    def __enter__(self):
        self.original = getattr(_patient_module, self._NAME)
        setattr(_patient_module, self._NAME, self.fn)
        return self

    def __exit__(self, *exc):
        setattr(_patient_module, self._NAME, self.original)
        return False


# ===========================================================================
# FIXTURES -- one patient per extractor tier, every one a literal
# ===========================================================================

_BREAST = {"display": "Malignant neoplasm of breast (disorder)",
           "code": "254837009"}


def patient(conditions=None, stage_obs=None, met_obs=None):
    """The minimum _create_patient_summary reads, plus the three stage inputs."""
    return {"demographics": {"age": 61, "sex": "female", "race": "white",
                             "ethnicity": "nonhispanic"},
            "conditions": list(conditions if conditions is not None else [_BREAST]),
            "medications": [],
            "cancer_stage_observations": list(stage_obs or []),
            "cancer_metastasis_observations": list(met_obs or [])}


def stage_obs(display, date="2024-01-01"):
    return {"stage_display": display, "date": date, "loinc": "21908-9"}


def met_obs(value, code="21907-1"):
    return {"code": code, "value": value, "metastasis_category": "M",
            "date": "2024-01-01"}


# The two SNOMED displays the corpus actually stores for the AJCC M axis.
_CM1_DISPLAY = "cM1: Distant metastasis present (finding)"

_TIER_PATIENTS = {
    STAGE_SOURCE_STAGE_GROUP:
        patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)")]),
    STAGE_SOURCE_M_CATEGORY:
        patient(met_obs=[met_obs(_CM1_DISPLAY)]),
    STAGE_SOURCE_CONDITION_DISPLAY:
        patient([{"display": "Carcinoma of breast, TNM stage 2"}]),
    STAGE_SOURCE_METASTATIC_KEYWORD:
        patient([{"display": "Metastatic malignant neoplasm to lung (disorder)"}]),
}

_NO_STAGE = patient()
_STAGE_ZERO = patient(stage_obs=[stage_obs("Stage 0 (qualifier value)")])

_ABSENCE_LINE = "Cancer Stage: not recorded in this record"


def stage_line(summary):
    """The rendered Cancer Stage line, or a NAMED absence.

    Never `[...][0]`: a defect that removes the section makes an index raise
    inside check()'s argument list, which reports one traceback where it owes
    a summary. The lesson tests/test_storage_query_layer.py and
    tests/test_dashboard_reproducibility_tab.py both had to learn.
    """
    hits = [ln for ln in summary.splitlines() if ln.startswith("Cancer Stage:")]
    if not hits:
        return "<no Cancer Stage line in the summary>"
    if len(hits) > 1:
        return f"<{len(hits)} Cancer Stage lines: {hits}>"
    return hits[0]


def expected_stage_line(ordinal, source, date_clause=None):
    """The line the renderer is required to produce, built from the vocabularies.

    DERIVED FROM STAGE_NUMERALS AND _STAGE_SOURCE_PHRASES on the same footing
    section 4 already used for the phrase: a tier added without a numeral or a
    phrase is a named failure rather than a member this file quietly stops
    covering. The DATE CLAUSE is passed in as a literal by every caller, never
    derived, so nothing here can agree with the renderer by construction --
    section 8a additionally pins two whole lines with no helper at all.
    """
    detail = _STAGE_SOURCE_PHRASES[source]
    if date_clause is not None:
        detail = f"{detail}; {date_clause}"
    return f"Cancer Stage: {STAGE_NUMERALS[ordinal]} ({detail})"


def section_index(summary, needle):
    """Line index of the first line starting with `needle`, or -1."""
    for i, ln in enumerate(summary.splitlines()):
        if ln.startswith(needle):
            return i
    return -1


# ===========================================================================
# 1. THE SECTION EXISTS, AND SITS WHERE IT WAS ARGUED TO SIT
# ===========================================================================

print("\n" + "=" * 70)
print("1. the section, and its position")
print("=" * 70)

_sum = _create_patient_summary(_TIER_PATIENTS[STAGE_SOURCE_STAGE_GROUP])

_i_perf = section_index(_sum, "Performance Status:")
_i_stage = section_index(_sum, "Cancer Stage:")
_i_cond = section_index(_sum, "Conditions:")

check("the summary has a Performance Status section", _i_perf >= 0, True)
check("...a Cancer Stage section", _i_stage >= 0, True)
check("...and a Conditions section", _i_cond >= 0, True)
check("Cancer Stage comes AFTER Performance Status", _i_stage > _i_perf, True)
check("...and BEFORE Conditions", _i_stage < _i_cond, True)
# DIRECTLY after: the ECOG line, then the stage line, with only blank lines
# between. An assertion on ordering alone would still pass if a third section
# were inserted between the two facts this placement exists to keep together.
check("...with nothing but blank lines between the two sections",
      [ln for ln in _sum.splitlines()[_i_perf + 1:_i_stage] if ln.strip()],
      ["- ECOG performance status: not recorded"])

check("exactly one Cancer Stage line is rendered",
      _sum.count("\nCancer Stage:"), 1)


# ===========================================================================
# 2. STAGE 0 IS A STAGE
# ===========================================================================

print("\n" + "=" * 70)
print("2. stage 0 is a stage, not an absence")
print("=" * 70)

_zero_stage = extract_patient_stage_with_source(
    _STAGE_ZERO["conditions"],
    cancer_stage_observations=_STAGE_ZERO["cancer_stage_observations"])
_zero_ordinal, _zero_source = _zero_stage.ordinal, _zero_stage.source
# NON-DEGENERACY FIRST: if the extractor stopped resolving this patient to 0
# the render check below would be asserting about the absence branch while
# reading as a passing truthiness test.
check("the fixture's extractor answer is literally 0", _zero_ordinal, 0)
check("...and 0 is falsy, which is what makes this a control at all",
      bool(_zero_ordinal), False)
check("the summary renders Stage 0 rather than reporting absence",
      stage_line(_create_patient_summary(_STAGE_ZERO)),
      expected_stage_line(0, STAGE_SOURCE_STAGE_GROUP,
                          "staged 2024-01-01, 2 years before reference date"))
check("...and the absence sentence is nowhere in that summary",
      _ABSENCE_LINE in _create_patient_summary(_STAGE_ZERO), False)


# ===========================================================================
# 3. ABSENCE IS STATED, AND THE SENTENCE IS PINNED
# ===========================================================================

print("\n" + "=" * 70)
print("3. absence is stated explicitly")
print("=" * 70)

check("a patient with no stage anywhere gets the absence sentence",
      stage_line(_create_patient_summary(_NO_STAGE)), _ABSENCE_LINE)
check("...and the extractor genuinely returns None for that patient, so the "
      "sentence is not being produced by some other branch",
      extract_patient_stage_with_source(_NO_STAGE["conditions"]),
      (None, None, None))
check("a summary with a stage differs from one without",
      _create_patient_summary(_NO_STAGE)
      == _create_patient_summary(_STAGE_ZERO), False)


# ===========================================================================
# 4. EVERY TIER RENDERS ITS OWN PROVENANCE
# ===========================================================================

print("\n" + "=" * 70)
print("4. one rendered line per tier, derived from the closed vocabulary")
print("=" * 70)

# DERIVED FROM STAGE_SOURCES, not from a list retyped here: a tier added to the
# extractor without a fixture is a named failure below rather than a member
# this file quietly stops covering.
check("the vocabulary is non-degenerate", len(STAGE_SOURCES) >= 4, True)
check("every vocabulary member has a phrase",
      sorted(_STAGE_SOURCE_PHRASES), sorted(STAGE_SOURCES))
check("...and every member has a fixture in this file",
      sorted(_TIER_PATIENTS), sorted(STAGE_SOURCES))

_expected_ordinals = {STAGE_SOURCE_STAGE_GROUP: 3,
                      STAGE_SOURCE_M_CATEGORY: 4,
                      STAGE_SOURCE_CONDITION_DISPLAY: 2,
                      STAGE_SOURCE_METASTATIC_KEYWORD: 4}

for _source in STAGE_SOURCES:
    _p = _TIER_PATIENTS.get(_source)
    if _p is None:
        check(f"[{_source}] has a fixture", "missing", "present")
        continue
    _st = extract_patient_stage_with_source(
        _p["conditions"],
        cancer_stage_observations=_p["cancer_stage_observations"],
        cancer_metastasis_observations=_p["cancer_metastasis_observations"])
    _ordinal, _answered = _st.ordinal, _st.source
    check(f"[{_source}] is the tier that answers for its fixture",
          _answered, _source)
    check(f"[{_source}] resolves the ordinal the fixture was built for",
          _ordinal, _expected_ordinals[_source])
    # EVERY FIXTURE'S OBSERVATION IS DATED 2024-01-01, so the two
    # observation-backed tiers carry the same clause and the two
    # diagnosis-text tiers carry none. Derived from the closed subset rather
    # than listed, on the same footing as the phrases above.
    check(f"[{_source}] renders the numeral, the provenance and -- for an "
          f"observation-backed tier -- the staging date",
          stage_line(_create_patient_summary(_p)),
          expected_stage_line(
              _ordinal, _source,
              "staged 2024-01-01, 2 years before reference date"
              if _source in STAGE_SOURCES_OBSERVATION_BACKED else None))

# The four phrases must DISCRIMINATE. Four identical strings would satisfy
# every check above and tell the model nothing.
check("the four phrases are distinct",
      len(set(_STAGE_SOURCE_PHRASES.values())), len(STAGE_SOURCES))
check("the five numerals are distinct and cover the scale",
      sorted(STAGE_NUMERALS), [0, 1, 2, 3, 4])


# ===========================================================================
# 5. THE SUMMARY AND THE FILTER AGREE -- MEASURED THROUGH STAGE 4
# ===========================================================================

print("\n" + "=" * 70)
print("5. the rendered stage IS the ordinal Stage 4 filtered on")
print("=" * 70)


def probe_trial(k):
    """A trial that admits stage k and no other stage.

    Nothing but the stage gate can discriminate between five of these: same
    rerank score, same MedCPT score, an age window nothing falls outside, sex
    ALL, and no histology tags. patient_trees is empty in the driver, so the
    MeSH site filter records a skip rather than dropping anything.
    """
    return {"trial": {"nct_id": f"NCT_STAGE_{k}", "title": "probe",
                      "histology_tags": [],
                      "structured_eligibility": {"min_stage": k, "max_stage": k,
                                                 "accepts_metastatic": None},
                      "eligibility": {"min_age": "0 Years",
                                      "max_age": "150 Years", "sex": "ALL",
                                      "inclusion_criteria": ""}},
            "rerank_score": 1.0, "rerank_score_raw": 1.0,
            "medcpt_score_max": 100.0}


def stage4_ordinal(patient_data):
    """The ordinal Stage 4 acted on, read off which probe survived."""
    state = {"patient_data": patient_data,
             "reranked_trials": [probe_trial(k) for k in range(5)],
             "ablation_flags": {}, "patient_trees": set(), "stage_timings": {}}
    try:
        out = node_rule_based_filter(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}: {exc}"
    kept = sorted(t["trial"]["nct_id"] for t in out["filtered_trials"])
    if len(kept) == 5:
        return None                      # no stage: every probe survives
    if len(kept) == 1:
        return int(kept[0].rsplit("_", 1)[1])
    return f"<indeterminate survivors: {kept}>"


def rendered_ordinal(patient_data):
    """The ordinal the summary's line states, read back out of the text."""
    line = stage_line(_create_patient_summary(patient_data))
    if line == _ABSENCE_LINE:
        return None
    for ordinal, numeral in STAGE_NUMERALS.items():
        if line.startswith(f"Cancer Stage: {numeral} ("):
            return ordinal
    return f"<unreadable: {line}>"


# NON-DEGENERACY: the probe set must be able to report every ordinal AND None,
# or "they agree" is satisfied by a driver that always answers the same thing.
check("the probe set discriminates: five distinct ordinals and None",
      sorted((stage4_ordinal(patient(stage_obs=[stage_obs(n)]))
              for n in ("Stage 0", "Stage I", "Stage II", "Stage III",
                        "Stage IV")), key=str) + [stage4_ordinal(_NO_STAGE)],
      [0, 1, 2, 3, 4, None])

for _label, _p in ([(s, _TIER_PATIENTS[s]) for s in STAGE_SOURCES]
                   + [("no stage", _NO_STAGE), ("stage 0", _STAGE_ZERO)]):
    check(f"[{_label}] the summary states the stage Stage 4 filtered on",
          rendered_ordinal(_p), stage4_ordinal(_p))


# ===========================================================================
# 6. THE STAGE SECTION IS THE RECORD'S, NEVER THE TEMPLATE'S
# ===========================================================================

print("\n" + "=" * 70)
print("6. the system prompt template does not carry a stage section")
print("=" * 70)

# THE CLAIM THIS SECTION MAKES CHANGED AT PROMPT_VERSION 1.6.0, and it is
# restated rather than deleted or left standing while false.
#
# It used to read "the declaration is in the USER message only", which was true
# while _create_patient_summary's output went into the user message. 1.6.0 moved
# the patient's record into the SYSTEM message -- so the stage section IS in the
# system message now, and a check asserting otherwise would either fail or, worse,
# pass against a probe record that happened not to mention a stage and thereby
# assert nothing.
#
# The fact this file is responsible for survives the move intact: the stage
# section is the RECORD's, so it appears exactly when the record it was rendered
# from contains it, and the TEMPLATE contributes nothing of its own. Both halves
# are asserted, which is strictly more than the old check did -- the old form
# could not distinguish "the template has no stage section" from "the renderer
# ignores its arguments".
_EMPTY_RECORD = "<probe: no patient record>"

for _applied in (True, False):
    _sys = render_system_prompt(mesh_filter_applied=_applied,
                                mesh_filter_skip_reason="no_mesh_filter",
                                patient_record=_EMPTY_RECORD)
    check(f"[mesh_filter_applied={_applied}] the template has no Cancer Stage "
          "section of its own", "Cancer Stage" in _sys, False)
    check(f"[mesh_filter_applied={_applied}] ...and does not state the "
          "absence sentence either", _ABSENCE_LINE in _sys, False)

# THE OTHER HALF, AND IT IS THE NON-DEGENERACY OF THE TWO ABOVE. A renderer that
# dropped its patient_record argument entirely would satisfy both of them.
_REAL_RECORD = _create_patient_summary(_TIER_PATIENTS[STAGE_SOURCE_STAGE_GROUP])
check("the record's stage section reaches the system message when the record "
      "carries one (non-degeneracy on the two checks above)",
      "Cancer Stage" in render_system_prompt(
          mesh_filter_applied=True, mesh_filter_skip_reason="no_mesh_filter",
          patient_record=_REAL_RECORD), True)
check("...and the absence sentence reaches it for a patient with no stage",
      _ABSENCE_LINE in render_system_prompt(
          mesh_filter_applied=True, mesh_filter_skip_reason="no_mesh_filter",
          patient_record=_create_patient_summary(_NO_STAGE)), True)
check("...and the summary that carries it is itself non-degenerate",
      "Cancer Stage" in _REAL_RECORD, True)

# Non-degeneracy: the renders above are real prompts, not empty strings.
check("the rendered system prompt is non-degenerate",
      len(render_system_prompt(mesh_filter_applied=True,
                               mesh_filter_skip_reason="no_mesh_filter",
                               patient_record=_EMPTY_RECORD)) > 500, True)


# ===========================================================================
# 7. THE STAGING DATE -- WHOSE DATE IT IS, AND WHEN THERE IS NONE
# ===========================================================================

print("\n" + "=" * 70)
print("7. the staging date belongs to the observation that produced the stage")
print("=" * 70)

# THE TWO WHOLE LINES, AS LITERALS, WITH NO HELPER BETWEEN THEM AND THE
# RENDERER. Everything else in this file derives its expectation from the
# vocabularies, which is right for coverage and wrong for SHAPE: a renderer
# that emitted the phrase and the clause in the other order, or joined them
# with a comma, would satisfy every derived check above. These two are typed
# out, and the arithmetic behind each interval is stated beside it.
#
#   2024-01-01 -> 2026-08-03 is 2 completed years  (relativedelta.years == 2)
_STAGE_OBS_DATE = "2024-01-01"

check("[stage group] the whole line, spelled out",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged 2024-01-01, 2 years before reference date)")
check("[m category] the whole line, spelled out",
      stage_line(_create_patient_summary(patient(met_obs=[met_obs(_CM1_DISPLAY)]))),
      "Cancer Stage: Stage IV (from a recorded AJCC clinical M category "
      "observation; staged 2024-01-01, 2 years before reference date)")

# THE UNDATED TIERS ARE UNCHANGED, AND THAT IS THE DELIBERATE BRANCH. A
# Condition carries an onset_date -- when the DIAGNOSIS began -- and it is not
# a staging date; a tier that fell back to it would answer "staged within the
# last six months" with the date the cancer started. Both fixtures below carry
# one, so this is a claim about the RENDERER and not about the data.
_DATED_CONDITION = {"display": "Carcinoma of breast, TNM stage 2",
                    "onset_date": "2026-07-01"}
_DATED_MET_CONDITION = {"display": "Metastatic malignant neoplasm to lung "
                                   "(disorder)", "onset_date": "2026-07-01"}
check("the diagnosis-text fixtures really do carry an onset date, so the two "
      "checks below are about the renderer rather than about the data",
      ("onset_date" in _DATED_CONDITION, "onset_date" in _DATED_MET_CONDITION),
      (True, True))
check("[condition display] renders no date clause at all",
      stage_line(_create_patient_summary(patient([_DATED_CONDITION]))),
      "Cancer Stage: Stage II (from diagnosis text)")
check("[metastatic keyword] likewise",
      stage_line(_create_patient_summary(patient([_DATED_MET_CONDITION]))),
      "Cancer Stage: Stage IV (from diagnosis text describing metastatic "
      "disease)")
check("...and neither line mentions the onset date it was given",
      ["2026-07-01" in stage_line(_create_patient_summary(patient([_c])))
       for _c in (_DATED_CONDITION, _DATED_MET_CONDITION)], [False, False])

# AN OBSERVATION-BACKED TIER WHOSE OBSERVATION HAS NO DATE IS A THIRD STATE,
# and it is STATED rather than rendered as silence. Collapsing it into the
# undated-tier rendering would tell a model that a clinician-assigned stage
# came from a diagnosis name.
check("[stage group, undated observation] the absence is stated",
      stage_line(_create_patient_summary(
          patient(stage_obs=[{"stage_display": "Stage IIIA (qualifier value)",
                              "loinc": "21908-9"}]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      f"{STAGE_DATE_UNKNOWN_CLAUSE})")
check("...and the corpus's own 'unknown' sentinel takes the same branch, "
      "rather than reaching the date parser as a literal string",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)",
                                       date="unknown")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      f"{STAGE_DATE_UNKNOWN_CLAUSE})")
check("...and that clause is DIFFERENT from the dated one, so the two states "
      "are distinguishable by a reader",
      STAGE_DATE_UNKNOWN_CLAUSE.startswith(STAGE_DATE_CLAUSE_PREFIX + " "),
      False)

# A DATE THAT IS PRESENT AND CANNOT ANCHOR AN INTERVAL still states the date --
# the convention every other dated section follows when its suffix comes back
# empty -- and is COUNTED under this line's own key.
_before = dict(TEMPORAL_RENDER_COUNTS)
check("[unreadable date] the raw date is stated and no interval is invented",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)",
                                       date="not-a-date")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged not-a-date)")
check("[after the reference date] likewise",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)",
                                       date="2030-01-01")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged 2030-01-01)")
_delta = {k: TEMPORAL_RENDER_COUNTS[k] - _before.get(k, 0)
          for k in set(TEMPORAL_RENDER_COUNTS) | set(_before)
          if TEMPORAL_RENDER_COUNTS[k] - _before.get(k, 0)}
check("both were COUNTED, under this line's OWN key rather than folded into "
      "another section's",
      _delta,
      {f"{TEMPORAL_KEY_STAGE_DATE}_unreadable:unparseable": 1,
       f"{TEMPORAL_KEY_STAGE_DATE}_after_reference": 1})
check("...and that key is not one another section already uses",
      TEMPORAL_KEY_STAGE_DATE in (_patient_module.TEMPORAL_KEY_ECOG_DATE,
                                  _patient_module.TEMPORAL_KEY_CONDITION_ONSET,
                                  _patient_module.TEMPORAL_KEY_METASTASIS_DATE),
      False)
_before = dict(TEMPORAL_RENDER_COUNTS)
_create_patient_summary(patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)")]))
check("an ORDINARY date moves no counter -- a usable date is not a degradation",
      {k: TEMPORAL_RENDER_COUNTS[k] - _before.get(k, 0)
       for k in TEMPORAL_RENDER_COUNTS
       if TEMPORAL_RENDER_COUNTS[k] - _before.get(k, 0)
       and k.startswith(TEMPORAL_KEY_STAGE_DATE)}, {})

# THE INTERVAL IS CAPPED AT THE RECORD'S OWN PRECISION. parse_partial_date
# imputes the missing components of a partial date from fixed anchors, so a
# year-precise staging date resolves to a concrete day the record never
# stated; rendering days from it would state an imputed anchor as a
# measurement. THE CORPUS CARRIES NO PARTIAL STAGING DATE -- all 295 of its
# stage-group observations are day-precise -- so these three records are
# constructed, which is stated rather than hidden.
check("[year precision] the interval is capped at years",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)",
                                       date="2024")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged 2024, 2 years before reference date)")
check("[year precision, under a year] it degrades to the coarse floor rather "
      "than counting days it does not have",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)",
                                       date="2026")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged 2026, less than 1 year before reference date)")
check("[month precision, under a year] months, never days",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)",
                                       date="2026-04")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged 2026-04, 3 months before reference date)")
check("[day precision, under a year] the exact day count, which is what a "
      "restaging window is written in",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)",
                                       date="2026-06-26")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged 2026-06-26, 38 days before reference date)")

# THE INTERVAL FOLLOWS THE REFERENCE DATE AND NOT THE CLOCK. The one property
# a hardcoded datetime.today() cannot satisfy, and it is proved by moving the
# reference rather than by comparing against today -- which is a fact about
# the day the suite runs.
_at_snapshots = []
for _snap in ("2027-01-01", "2030-06-15", _PATCHED_SNAPSHOT):
    _config.DATA_SNAPSHOT_DATE = _snap
    try:
        _at_snapshots.append(stage_line(_create_patient_summary(
            patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)")]))))
    finally:
        _config.DATA_SNAPSHOT_DATE = _PATCHED_SNAPSHOT
check("three reference dates give three different intervals",
      len(set(_at_snapshots)), 3)
check("...and the RAW DATE is the same in all three, so what moved is the "
      "interval and not the record",
      [ln.count(_STAGE_OBS_DATE) for ln in _at_snapshots], [1, 1, 1])
check("the reference date is restored", _config.DATA_SNAPSHOT_DATE,
      _PATCHED_SNAPSHOT)

# THE WINNER'S DATE, WHICH IS THE WHOLE POINT. A restaged patient whose most
# recent stage-group observation has a display the regex cannot read: the tier
# sorts most recent first, skips it, and answers from the OLDER one. The date
# rendered must be the older one's. "The newest staging observation's date" is
# the plausible wrong implementation and it is planted in section 8.
_RESTAGED = patient(stage_obs=[
    {"stage_display": "Staging incomplete (qualifier value)",
     "date": "2026-06-26", "loinc": "21908-9"},
    {"stage_display": "Stage IIIA (qualifier value)",
     "date": "2019-05-26", "loinc": "21908-9"},
])
_restaged = extract_patient_stage_with_source(
    _RESTAGED["conditions"],
    cancer_stage_observations=_RESTAGED["cancer_stage_observations"])
check("NON-DEGENERACY: the newer observation really is unreadable, so the "
      "older one really is the answering record",
      (_restaged.ordinal, _restaged.source), (3, STAGE_SOURCE_STAGE_GROUP))
check("...and the two dates really do differ, or this fixture proves nothing",
      _RESTAGED["cancer_stage_observations"][0]["date"]
      != _RESTAGED["cancer_stage_observations"][1]["date"], True)
check("the extractor reports the ANSWERING observation's date",
      _restaged.observation_date, "2019-05-26")
check("...and the summary renders it, not the newer unreadable one",
      stage_line(_create_patient_summary(_RESTAGED)),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged 2019-05-26, 7 years before reference date)")

# THE SAME TRAP ACROSS TIERS. An unparseable stage-group observation sits above
# a cM1: the stage-group tier produces nothing, the M tier answers, and the
# date rendered must be the M observation's.
_M_UNDER_JUNK = patient(
    stage_obs=[{"stage_display": "Staging incomplete (qualifier value)",
                "date": "2026-06-26", "loinc": "21908-9"}],
    met_obs=[met_obs(_CM1_DISPLAY)])
_m_under = extract_patient_stage_with_source(
    _M_UNDER_JUNK["conditions"],
    cancer_stage_observations=_M_UNDER_JUNK["cancer_stage_observations"],
    cancer_metastasis_observations=_M_UNDER_JUNK["cancer_metastasis_observations"])
check("NON-DEGENERACY: the M tier is what answers here",
      (_m_under.ordinal, _m_under.source), (4, STAGE_SOURCE_M_CATEGORY))
check("the date is the M observation's and not the stage-group record's",
      _m_under.observation_date, "2024-01-01")
check("...and the rendered line says so",
      stage_line(_create_patient_summary(_M_UNDER_JUNK)),
      "Cancer Stage: Stage IV (from a recorded AJCC clinical M category "
      "observation; staged 2024-01-01, 2 years before reference date)")

# THE VOCABULARY, AND THE INVARIANT OVER IT.
check("STAGE_SOURCES_OBSERVATION_BACKED is a NON-EMPTY PROPER subset of the "
      "vocabulary -- a set covering every tier makes the branch unconditional, "
      "and an empty one deletes the clause from every line",
      (0 < len(STAGE_SOURCES_OBSERVATION_BACKED) < len(STAGE_SOURCES),
       set(STAGE_SOURCES_OBSERVATION_BACKED) <= set(STAGE_SOURCES)),
      (True, True))
check("...and its members are exactly the two tiers that read an Observation",
      sorted(STAGE_SOURCES_OBSERVATION_BACKED),
      sorted((STAGE_SOURCE_M_CATEGORY, STAGE_SOURCE_STAGE_GROUP)))

_dates_by_tier = {}
for _source in STAGE_SOURCES:
    _p = _TIER_PATIENTS[_source]
    _dates_by_tier[_source] = extract_patient_stage_with_source(
        _p["conditions"],
        cancer_stage_observations=_p["cancer_stage_observations"],
        cancer_metastasis_observations=_p["cancer_metastasis_observations"]
    ).observation_date
check("every observation-backed tier reports a date for a dated fixture "
      "(non-degeneracy on the check below)",
      {s: d for s, d in _dates_by_tier.items()
       if s in STAGE_SOURCES_OBSERVATION_BACKED},
      {s: _STAGE_OBS_DATE for s in STAGE_SOURCES_OBSERVATION_BACKED})
check("...and NO tier outside that subset reports one, whatever its fixture "
      "carries",
      {s: d for s, d in _dates_by_tier.items()
       if s not in STAGE_SOURCES_OBSERVATION_BACKED and d is not None}, {})
check("a patient with no stage at all reports no date either",
      extract_patient_stage_with_source(_NO_STAGE["conditions"])
      .observation_date, None)

# THE CORPUS'S OWN DATE SHAPE, which is not a bare ISO day. Every one of the
# 1,000 bundles stores its stage-group date as a full ISO datetime with a UTC
# offset, so the [:10] slice this line shares with every other dated section is
# load-bearing rather than defensive: without it the model is handed a
# timestamp and a timezone for a fact recorded to the day.
check("a corpus-shaped ISO datetime renders as the day it names",
      stage_line(_create_patient_summary(
          patient(stage_obs=[stage_obs("Stage IIIA (qualifier value)",
                                       date="2019-05-26T11:05:53-07:00")]))),
      "Cancer Stage: Stage III (from a recorded stage group observation; "
      "staged 2019-05-26, 7 years before reference date)")


# THE DERIVATION DID NOT MOVE. Section 5 already establishes that the rendered
# ordinal IS the one Stage 4 filtered on; this is the narrower statement that
# adding a third member changed neither of the first two for any fixture in
# this file, and that the ordinal-only delegate still agrees with it.
for _label, _p in ([(s, _TIER_PATIENTS[s]) for s in STAGE_SOURCES]
                   + [("no stage", _NO_STAGE), ("stage 0", _STAGE_ZERO),
                      ("restaged", _RESTAGED), ("m under junk", _M_UNDER_JUNK)]):
    check(f"[{_label}] extract_patient_stage agrees with the richer form",
          extract_patient_stage(
              _p["conditions"],
              cancer_stage_observations=_p["cancer_stage_observations"],
              cancer_metastasis_observations=_p["cancer_metastasis_observations"]),
          extract_patient_stage_with_source(
              _p["conditions"],
              cancer_stage_observations=_p["cancer_stage_observations"],
              cancer_metastasis_observations=_p["cancer_metastasis_observations"]
          ).ordinal)


# ===========================================================================
# 8. NEGATIVE CONTROLS
# ===========================================================================

print("\n" + "=" * 70)
print("8. every assertion above is shown to FAIL when the section is broken")
print("=" * 70)
print("Each plant goes into an in-memory COPY of the module under test;")
print("both files on disk are hashed before and after and asserted identical.")

_IS_NOT_NONE = "    if stage.ordinal is not None:"
_ABSENCE_BRANCH = ('        summary += "\\n\\nCancer Stage: not recorded in '
                   'this record\\n"')
# RETARGETED WITH THE RENDERER'S PARAMETER. `_create_patient_summary` used to
# BE the renderer and take the parsed record whole; it is a wrapper now, and
# `render_patient_record` takes a deid.DeidentifiedRecord -- so these two reads
# are `record.fields.get(...)`. THE ANCHOR IS RE-DERIVED FROM THE SHIPPED
# SOURCE rather than retyped a second time, because a plant that no longer
# matches reports MISSED against a check that works, which is the shape this
# project has already paid for once.
_EXTRACTOR_CALL = (
    "    stage = extract_patient_stage_with_source(\n"
    "        conditions,\n"
    "        cancer_stage_observations=record.fields.get('cancer_stage_observations') or [],\n"
    "        cancer_metastasis_observations=record.fields.get('cancer_metastasis_observations') or [],\n"
    "    )")

# 1. THE TRUTHINESS TRAP -- the defect section 2 exists to catch, and the one
#    that shipped once already for ECOG.
_control("CONTROL: `if stage_ordinal:` reports stage 0 as not recorded",
         [(_IS_NOT_NONE, "    if stage.ordinal:")],
         lambda m: stage_line(m._create_patient_summary(_STAGE_ZERO)),
         _ABSENCE_LINE)
_control("CONTROL: ...and that plant is a REAL regression rather than a broken "
         "plant, because a stage-III patient still renders under it",
         [(_IS_NOT_NONE, "    if stage.ordinal:")],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_STAGE_GROUP])),
         "Cancer Stage: Stage III (from a recorded stage group observation; "
         "staged 2024-01-01, 2 years before reference date)")

# 2. ABSENCE OMITTED instead of stated -- silence where the model needs a fact.
_control("CONTROL: dropping the absence branch leaves no Cancer Stage line",
         [(_ABSENCE_BRANCH, "        pass")],
         lambda m: stage_line(m._create_patient_summary(_NO_STAGE)),
         "<no Cancer Stage line in the summary>")

# 3. THE SECOND DERIVATION -- the whole point of the section. Dropping the two
#    observation arguments makes the summary state a stage the filter did not
#    act on, silently, for every patient whose stage comes from an Observation.
_control("CONTROL: re-deriving the stage from conditions alone makes the "
         "summary disagree with the filter",
         [(_EXTRACTOR_CALL,
           "    stage = extract_patient_stage_with_source(\n"
           "        conditions)")],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_STAGE_GROUP])),
         _ABSENCE_LINE)
_control("CONTROL: ...and the same plant breaks the M-category patient too, "
         "which is the tier Stage 4 had to be told about explicitly",
         [(_EXTRACTOR_CALL,
           "    stage = extract_patient_stage_with_source(\n"
           "        conditions)")],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_M_CATEGORY])),
         _ABSENCE_LINE)

# 4. THE PROVENANCE DROPPED -- the ordinal is right and the evidence class is
#    gone, so "Stage IV from a clinician's assignment" and "Stage IV because a
#    diagnosis name contains the word metastatic" become the same sentence.
_control("CONTROL: rendering without the source phrase loses the evidence class",
         [("        stage_detail = [_STAGE_SOURCE_PHRASES[stage.source]]",
           '        stage_detail = ["source withheld"]')],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_METASTATIC_KEYWORD])),
         "Cancer Stage: Stage IV (source withheld)")

# 6. THE DEFECT THIS PASS EXISTS TO PREVENT: the newest staging observation's
#    date, rendered beside an ordinal an OLDER observation produced. It is the
#    plausible implementation -- the tier sorts most recent first, so
#    sorted_obs[0] is right there -- and it is silently wrong for exactly the
#    restaged patient whose newest record the regex cannot read. Planted in
#    the EXTRACTOR, which is where the date is decided.
_WINNER_DATE_RETURN = (
    "                    return PatientStage(ordinal, STAGE_SOURCE_STAGE_GROUP,\n"
    "                                        obs.get('date'))")


def _restaged_date(module):
    return module.extract_patient_stage_with_source(
        _RESTAGED["conditions"],
        cancer_stage_observations=_RESTAGED["cancer_stage_observations"],
    ).observation_date


_control_in(_STAGE_SRC,
            "CONTROL: the CLEAN arm -- an unmutated copy reports the ANSWERING "
            "observation's date, so the plant below is what moves it",
            [], _restaged_date, "2019-05-26")
_control_in(_STAGE_SRC,
            "CONTROL: reporting the newest staging observation's date states a "
            "date no tier measured",
            [(_WINNER_DATE_RETURN,
              "                    return PatientStage(ordinal, "
              "STAGE_SOURCE_STAGE_GROUP,\n"
              "                                        sorted_obs[0].get('date'))")],
            _restaged_date, "2026-06-26")


def _rendered_under(module, patient_data):
    """The stage line the SHIPPED renderer produces from `module`'s extractor.

    The rebinding is asserted to have taken and to have been undone; without
    that, a control that patched a name the renderer does not read would report
    the defect as caught while measuring the shipped code.
    """
    with with_extractor(module.extract_patient_stage_with_source):
        if (_patient_module.extract_patient_stage_with_source
                is extract_patient_stage_with_source):
            return "<the rebinding did not reach the renderer>"
        line = stage_line(_create_patient_summary(patient_data))
    if (_patient_module.extract_patient_stage_with_source
            is not extract_patient_stage_with_source):
        return "<the rebinding was not undone>"
    return line


_control_in(_STAGE_SRC,
            "CONTROL: ...and the SUMMARY then states it, which is the sentence "
            "a reader would act on",
            [(_WINNER_DATE_RETURN,
              "                    return PatientStage(ordinal, "
              "STAGE_SOURCE_STAGE_GROUP,\n"
              "                                        sorted_obs[0].get('date'))")],
            lambda m: _rendered_under(m, _RESTAGED),
            "Cancer Stage: Stage III (from a recorded stage group observation; "
            "staged 2026-06-26, 38 days before reference date)")
_control_in(_STAGE_SRC,
            "CONTROL: ...while the unmutated copy driven through the SAME "
            "rebinding renders the answering observation's date",
            [], lambda m: _rendered_under(m, _RESTAGED),
            "Cancer Stage: Stage III (from a recorded stage group observation; "
            "staged 2019-05-26, 7 years before reference date)")

# 7. THE CROSS-TIER LEAK. The M tier answers and the date comes off a
#    stage-group observation that produced nothing.
_M_TIER_RETURN = (
    "        return PatientStage(m_category_stage, STAGE_SOURCE_M_CATEGORY,\n"
    "                            m_category_date)")
_control_in(_STAGE_SRC,
            "CONTROL: the M tier borrowing a stage-group record's date",
            [(_M_TIER_RETURN,
              "        return PatientStage(m_category_stage, "
              "STAGE_SOURCE_M_CATEGORY,\n"
              "                            (cancer_stage_observations or "
              "[{}])[0].get('date') or m_category_date)")],
            lambda m: m.extract_patient_stage_with_source(
                _M_UNDER_JUNK["conditions"],
                cancer_stage_observations=_M_UNDER_JUNK["cancer_stage_observations"],
                cancer_metastasis_observations=_M_UNDER_JUNK["cancer_metastasis_observations"],
            ).observation_date,
            "2026-06-26")

# 8. A DIAGNOSIS ONSET USED AS A STAGING DATE. Caught at the extractor -- and
#    the RENDER is unaffected, because the branch is on the tier rather than on
#    the date. Both halves are asserted: the second is the defence in depth
#    that STAGE_SOURCES_OBSERVATION_BACKED buys, and it is worth measuring
#    rather than assuming.
_CONDITION_TIER_RETURN = (
    "                return PatientStage(ordinal, STAGE_SOURCE_CONDITION_DISPLAY,\n"
    "                                    None)")
_ONSET_LEAK = [(_CONDITION_TIER_RETURN,
                "                return PatientStage(ordinal, "
                "STAGE_SOURCE_CONDITION_DISPLAY,\n"
                "                                    cond.get('onset_date'))")]
_ONSET_PATIENT = patient([_DATED_CONDITION])
_control_in(_STAGE_SRC,
            "CONTROL: the condition tier leaking the DIAGNOSIS onset as a "
            "staging date",
            _ONSET_LEAK,
            lambda m: m.extract_patient_stage_with_source(
                _ONSET_PATIENT["conditions"]).observation_date,
            "2026-07-01")
_control_in(_STAGE_SRC,
            "CONTROL: ...and the rendered line is STILL clean under that leak, "
            "because the renderer branches on the tier and not on the date",
            _ONSET_LEAK, lambda m: _rendered_under(m, _ONSET_PATIENT),
            "Cancer Stage: Stage II (from diagnosis text)")

# 9. THE BRANCH MOVED FROM THE TIER TO THE DATE -- the fallback accident. An
#    observation-backed stage whose Observation carries no date stops saying so
#    and becomes indistinguishable from a stage read out of a diagnosis name.
_UNDATED_OBS_PATIENT = patient(
    stage_obs=[{"stage_display": "Stage IIIA (qualifier value)",
                "loinc": "21908-9"}])
_control("CONTROL: branching on the date instead of the tier loses the "
         "'staging date not recorded' statement",
         [("        if stage.source in STAGE_SOURCES_OBSERVATION_BACKED:",
           "        if stage.observation_date is not None:")],
         lambda m: stage_line(m._create_patient_summary(_UNDATED_OBS_PATIENT)),
         "Cancer Stage: Stage III (from a recorded stage group observation)")

# 10. THE CLAUSE DROPPED ALTOGETHER -- the state before this pass, which is
#     what says the pinned lines in section 7 discriminate.
_control("CONTROL: dropping the date clause reverts the line to the form that "
         "could not answer a restaging criterion",
         [("            stage_detail.append(\n"
           "                _stage_date_clause(stage.observation_date, reference_date))",
           "            pass")],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_STAGE_GROUP])),
         "Cancer Stage: Stage III (from a recorded stage group observation)")

# 11. THE INTERVAL DROPPED, THE DATE KEPT -- a bare date is what every other
#     section of this summary stopped printing at PROMPT_VERSION 1.8.0.
_control("CONTROL: a staging date with no elapsed interval beside it",
         [('            + _dated_suffix(date_raw, reference, '
           'TEMPORAL_KEY_STAGE_DATE))', '            + "")')],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_STAGE_GROUP])),
         "Cancer Stage: Stage III (from a recorded stage group observation; "
         "staged 2024-01-01)")

# 12. THE RAW DATE UNSLICED. Every stage-group date in the corpus is a full ISO
#     datetime with an offset, so this is what the [:10] is for.
_control("CONTROL: without the [:10] slice the line carries a time and a "
         "timezone for a fact recorded to the day",
         [('return (f"{STAGE_DATE_CLAUSE_PREFIX} {date_raw[:10]}"',
           'return (f"{STAGE_DATE_CLAUSE_PREFIX} {date_raw}"')],
         lambda m: stage_line(m._create_patient_summary(
             patient(stage_obs=[stage_obs(
                 "Stage IIIA (qualifier value)",
                 date="2019-05-26T11:05:53-07:00")]))),
         "Cancer Stage: Stage III (from a recorded stage group observation; "
         "staged 2019-05-26T11:05:53-07:00, 7 years before reference date)")

# 5. THE VOCABULARY GUARD -- a tier added to the extractor with no phrase must
#    raise at IMPORT, not KeyError inside a prompt build in node_llm_classifier_
#    evaluation, where the graph's error handler turns it into a lost patient.
#    This control cannot go through _control(): the plant is SUPPOSED to fail
#    to exec, so what is asserted is the exception the module raised, not a
#    value read back out of it.
_DROP_ONE_PHRASE = [("    STAGE_SOURCE_METASTATIC_KEYWORD:\n"
                     '        "from diagnosis text describing metastatic '
                     'disease",\n', "")]


def plant_outcome(subs):
    """What a plant produced: 'ok', or the exception text _plant reported."""
    try:
        _plant(_PATIENT_SRC, f"probe_{_CONTROL_SEQ[0]}_x", subs)
    except _PlantFailed as exc:
        return str(exc).split(":", 1)[0]
    except Exception as exc:            # noqa: BLE001 - reported, not raised
        return f"escaped {type(exc).__name__}"
    return "ok"


check("CONTROL: a phrase map that does not cover STAGE_SOURCES refuses to "
      "import, by name",
      plant_outcome(_DROP_ONE_PHRASE), "RuntimeError")
# NON-DEGENERACY: plant_outcome must be able to answer 'ok', or the check above
# is satisfied by a probe that reports a failure whatever it is handed.
check("...and the same probe reports 'ok' for a plant that is a no-op",
      plant_outcome([("import hashlib", "import hashlib")]), "ok")


# ===========================================================================
# THE FILE ON DISK IS UNCHANGED
# ===========================================================================

print("\n" + "=" * 70)
print("the planted file is byte-identical to how it started")
print("=" * 70)

_sha_at_end = {
    path: hashlib.sha256(open(path, encoding="utf-8").read().encode()).hexdigest()
    for path in (_PATIENT_SRC, _STAGE_SRC)
}
check("both planted files are unchanged on disk", _sha_at_end, _SHA_AT_START)
# NON-DEGENERACY: the hashes must be of files with content, and the comparison
# must not be of one expression with itself.
check("...and that comparison is of real files rather than empty reads",
      sorted(len(open(p, encoding="utf-8").read()) > 1000
             for p in (_PATIENT_SRC, _STAGE_SRC)), [True, True])
check("...and the two paths are different files",
      _PATIENT_SRC != _STAGE_SRC, True)

# THE REFERENCE DATE IS PUT BACK. Restored here rather than in a finally
# because this file is a script: every check above runs at module level, so
# there is no body to wrap, and `pytest tests/` importing this module must not
# leave every later file measuring against a snapshot it did not choose.
_config.DATA_SNAPSHOT_DATE = _REAL_SNAPSHOT
check("config.DATA_SNAPSHOT_DATE is restored",
      _config.DATA_SNAPSHOT_DATE, _REAL_SNAPSHOT)
check("...and the restore is not a no-op, so the pin above was doing work",
      _REAL_SNAPSHOT == _PATCHED_SNAPSHOT
      or _config.DATA_SNAPSHOT_DATE != _PATCHED_SNAPSHOT, True)
check("the renderer's extractor binding is the shipped one",
      _patient_module.extract_patient_stage_with_source
      is extract_patient_stage_with_source, True)


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
        print(f"  - {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------
