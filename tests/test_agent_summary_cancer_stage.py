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
    7. THE CONTROLS, seven of them, each planted into an IN-MEMORY COPY of
       ``oncotriage/agent/patient.py`` -- never the file on disk, which is
       hashed before and after. A plant that fails to apply is a RECORDED
       failure, not a traceback that hides every check below it.

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

from oncotriage.agent import patient as _patient_module
from oncotriage.agent.filtering import node_rule_based_filter
from oncotriage.agent.patient import _STAGE_SOURCE_PHRASES, _create_patient_summary
from oncotriage.agent.prompts import render_system_prompt
from oncotriage.extraction.stage import (
    STAGE_NUMERALS,
    STAGE_SOURCE_CONDITION_DISPLAY,
    STAGE_SOURCE_M_CATEGORY,
    STAGE_SOURCE_METASTATIC_KEYWORD,
    STAGE_SOURCE_STAGE_GROUP,
    STAGE_SOURCES,
    extract_patient_stage,
    extract_patient_stage_with_source,
)


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
_SHA_AT_START = hashlib.sha256(
    open(_PATIENT_SRC, encoding="utf-8").read().encode()).hexdigest()


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


def _control(label, subs, probe, expected):
    """Run a negative control. A BAD PLANT IS A RECORDED FAILURE, not a crash."""
    _CONTROL_SEQ[0] += 1
    try:
        module = _plant(_PATIENT_SRC, f"ctl_{_CONTROL_SEQ[0]}", subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    try:
        actual = probe(module)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        actual = f"raised {type(exc).__name__}: {exc}"
    check(label, actual, expected)


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

_zero_ordinal, _zero_source = extract_patient_stage_with_source(
    _STAGE_ZERO["conditions"],
    cancer_stage_observations=_STAGE_ZERO["cancer_stage_observations"])
# NON-DEGENERACY FIRST: if the extractor stopped resolving this patient to 0
# the render check below would be asserting about the absence branch while
# reading as a passing truthiness test.
check("the fixture's extractor answer is literally 0", _zero_ordinal, 0)
check("...and 0 is falsy, which is what makes this a control at all",
      bool(_zero_ordinal), False)
check("the summary renders Stage 0 rather than reporting absence",
      stage_line(_create_patient_summary(_STAGE_ZERO)),
      f"Cancer Stage: {STAGE_NUMERALS[0]} "
      f"({_STAGE_SOURCE_PHRASES[STAGE_SOURCE_STAGE_GROUP]})")
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
      extract_patient_stage_with_source(_NO_STAGE["conditions"]), (None, None))
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
    _ordinal, _answered = extract_patient_stage_with_source(
        _p["conditions"],
        cancer_stage_observations=_p["cancer_stage_observations"],
        cancer_metastasis_observations=_p["cancer_metastasis_observations"])
    check(f"[{_source}] is the tier that answers for its fixture",
          _answered, _source)
    check(f"[{_source}] resolves the ordinal the fixture was built for",
          _ordinal, _expected_ordinals[_source])
    check(f"[{_source}] renders the numeral and the provenance",
          stage_line(_create_patient_summary(_p)),
          f"Cancer Stage: {STAGE_NUMERALS[_ordinal]} "
          f"({_STAGE_SOURCE_PHRASES[_source]})")

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
# 7. NEGATIVE CONTROLS
# ===========================================================================

print("\n" + "=" * 70)
print("7. every assertion above is shown to FAIL when the section is broken")
print("=" * 70)
print("Each plant goes into an in-memory COPY of oncotriage/agent/patient.py;")
print("the file on disk is hashed before and after and asserted byte-identical.")

_IS_NOT_NONE = "    if stage_ordinal is not None:"
_ABSENCE_BRANCH = ('        summary += "\\n\\nCancer Stage: not recorded in '
                   'this record\\n"')
_EXTRACTOR_CALL = (
    "    stage_ordinal, stage_source = extract_patient_stage_with_source(\n"
    "        conditions,\n"
    "        cancer_stage_observations=patient_data.get('cancer_stage_observations') or [],\n"
    "        cancer_metastasis_observations=patient_data.get('cancer_metastasis_observations') or [],\n"
    "    )")

# 1. THE TRUTHINESS TRAP -- the defect section 2 exists to catch, and the one
#    that shipped once already for ECOG.
_control("CONTROL: `if stage_ordinal:` reports stage 0 as not recorded",
         [(_IS_NOT_NONE, "    if stage_ordinal:")],
         lambda m: stage_line(m._create_patient_summary(_STAGE_ZERO)),
         _ABSENCE_LINE)
_control("CONTROL: ...and that plant is a REAL regression rather than a broken "
         "plant, because a stage-III patient still renders under it",
         [(_IS_NOT_NONE, "    if stage_ordinal:")],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_STAGE_GROUP])),
         "Cancer Stage: Stage III (from a recorded stage group observation)")

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
           "    stage_ordinal, stage_source = extract_patient_stage_with_source(\n"
           "        conditions)")],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_STAGE_GROUP])),
         _ABSENCE_LINE)
_control("CONTROL: ...and the same plant breaks the M-category patient too, "
         "which is the tier Stage 4 had to be told about explicitly",
         [(_EXTRACTOR_CALL,
           "    stage_ordinal, stage_source = extract_patient_stage_with_source(\n"
           "        conditions)")],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_M_CATEGORY])),
         _ABSENCE_LINE)

# 4. THE PROVENANCE DROPPED -- the ordinal is right and the evidence class is
#    gone, so "Stage IV from a clinician's assignment" and "Stage IV because a
#    diagnosis name contains the word metastatic" become the same sentence.
_control("CONTROL: rendering without the source phrase loses the evidence class",
         [('f"({_STAGE_SOURCE_PHRASES[stage_source]})\\n")',
           'f"(source withheld)\\n")')],
         lambda m: stage_line(m._create_patient_summary(
             _TIER_PATIENTS[STAGE_SOURCE_METASTATIC_KEYWORD])),
         "Cancer Stage: Stage IV (source withheld)")

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

_sha_at_end = hashlib.sha256(
    open(_PATIENT_SRC, encoding="utf-8").read().encode()).hexdigest()
check("oncotriage/agent/patient.py is unchanged on disk",
      _sha_at_end, _SHA_AT_START)
# NON-DEGENERACY: the two hashes must be of a file with content, and the
# comparison must not be of one expression with itself.
check("...and that comparison is of a real file rather than an empty read",
      len(open(_PATIENT_SRC, encoding="utf-8").read()) > 1000, True)


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
