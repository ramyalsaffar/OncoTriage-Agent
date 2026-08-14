# Stage 5 Composed Assessment
#############################

"""The stored assessment is a rendering of the criteria arrays, not prose.

WHY THIS FILE EXISTS
--------------------
Section 5 of the system prompt orders the model to write ``assessment`` FIRST,
as its reasoning, and that draft used to be stored verbatim as the trial's
assessment. Audited assessments contradicted their own criteria arrays: they
called a field "not documented" while the arrays quoted a value for it from the
record, they named numeric thresholds that appear nowhere in the trial's
criteria text, and one emitted both mandated openings at once. The arrays were
right in every case; the stored prose was not, and the stored prose is what a
reader sees.

Reordering the emission is NOT available -- strict Structured Outputs emits a
trial object's keys alphabetically whatever the schema says, and ``assessment``
sorts before both criteria arrays (measured; see
``oncotriage/agent/response_schema.py``). So the model contract is unchanged and
the STORED text is composed in code, at PROMPT_VERSION 1.5.0, by
``oncotriage/agent/evaluation.py:compose_assessment``.

WHAT IT HOLDS
-------------
    1. THE RENDERER, as a pure function, over synthetic verdicts: all three
       trial verdicts, empty arrays, an eligible trial with no undocumented
       rows, and quoting fidelity (every criterion and patient_value it cites
       appears in the output verbatim).
    2. TWO INVARIANTS, written as CHECKERS rather than as assertions, so the
       same code that passes over every live composition can be shown to FIRE
       on a hand-built violation:
         - every criterion named in a composed "Not documented in the patient
           record:" clause maps to a row whose status is "not_evaluable" AND
           whose patient_value is exactly "Not in patient record";
         - every composed "Known disqualifier:" cites at least one row whose
           status is "not_met" or "violated".
    3. NUMBER PROVENANCE. Every numeric token in a composed assessment must
       appear as a numeric token in that trial's own criterion / patient_value
       strings. The helper is exercised on every composition in this file and
       has its own firing negative control.
    4. PLACEMENT, THROUGH THE REAL NODE. The composition has to run LAST: the
       label normalizer, the verdict logic, the absent-data validator and the
       reconciliation can all still move what it reads. Section 5 drives
       ``node_llm_classifier_evaluation`` with a stubbed client over five
       trials that exercise each of those paths.
    5. THE PROMPT'S OWN EXAMPLES. The JSON template in Section 5 of the system
       prompt is an example of a conforming response, so it must satisfy the
       rules the prompt states -- including the two added at 1.5.0. Checked
       against the RENDERED prompt, so the examples cannot drift out of
       agreement with the constraints above them.

THERE IS NO ``exec`` AND NO ``git show`` ANYWHERE IN HERE. Every control is a
different INPUT to a pure function, which is the natural control for a pure
function of its argument, so no source is patched and no revision is read. It
writes nothing anywhere and is not in the collision matrix.

NO NETWORK, NO KEYS, NO SPEND, NO DATABASE, NO CORPUS, NO GIT, NO FIXTURE. The
one model call in section 5 is served by a stub installed through
``oncotriage/agent/deps.py``.

Run from terminal:
    python tests/test_agent_composed_assessment.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
# The package bootstrap every file in tests/ carries. The candidate directory
# is the PARENT of this file's, because the package sits beside tests/ rather
# than inside it. `pip install -e .` makes the whole block a no-op.
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

import contextlib
import io
import json
import re
import time

from oncotriage.agent import deps
from oncotriage.agent import evaluation as _evaluation_module
from oncotriage.agent.evaluation import (
    ASSESSMENT_CASES,
    ASSESSMENT_COMPOSED_ELIGIBLE,
    ASSESSMENT_COMPOSED_NOT_ELIGIBLE,
    ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR,
    ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
    ASSESSMENT_COMPOSITION_ANOMALIES,
    ASSESSMENT_ELIGIBLE_OPENING,
    ASSESSMENT_KEPT_NOT_EVALUABLE,
    ASSESSMENT_KEPT_NO_DISQUALIFIER,
    ASSESSMENT_KEPT_UNKNOWN_VERDICT,
    ASSESSMENT_NOT_ELIGIBLE_OPENING,
    ASSESSMENT_NOT_EVALUABLE_OPENING,
    ASSESSMENT_UNDOCUMENTED_OPENING,
    ASSESSMENT_UNDOCUMENTED_PATIENT_VALUE,
    ASSESSMENT_REMAP_NO_SURVIVOR_TEXT,
    ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
    UNEVALUABLE_REJECTION_UNSUPPORTED,
    UNEVALUABLE_REMAP_NO_SURVIVOR,
    assessment_composition_case,
    compose_assessment,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.prompts import PROMPT_VERSION, render_system_prompt
from oncotriage.agent.state import (
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_ELIGIBLE,
    TRIAL_VERDICT_NOT_EVALUABLE,
)


#------------------------------------------------------------------------------


_T_START = time.time()


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


def compose(verdict):
    """compose_assessment, with a raise converted into a value.

    NEVER A BARE CALL. A defect that makes the renderer raise is exactly what
    these checks exist to catch, and a bare call lets the exception escape
    through check()'s argument list and take the whole file down -- one
    traceback where it owes a summary. This project has shipped that shape in
    five test files already.
    """
    try:
        return compose_assessment(verdict)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"<raised {type(exc).__name__}: {exc}>"


def case_of(verdict):
    """assessment_composition_case, same treatment."""
    try:
        return assessment_composition_case(verdict)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"<raised {type(exc).__name__}>"


def pos(text, needle):
    """Where `needle` starts in `text`, or -1. NEVER ``str.index``.

    THIS FILE SHIPPED THE INDEX FORM AND A REVERT HARNESS FOUND IT. With the
    composition removed in a copy of the package, `_ne.index("Adequate renal
    function")` raised ValueError inside check()'s argument list and the run
    died at section 1, reporting one traceback where it owed 82 results -- the
    sixth time this project has produced that shape. `find` returns -1, which
    makes an ordering comparison FAIL and be reported, and every call site
    below asserts both operands are >= 0 so an ordering check cannot pass by
    both being absent.
    """
    return text.find(needle)


print("=" * 78)
print("STAGE 5 COMPOSED ASSESSMENT")
print("=" * 78)
print(f"PROMPT_VERSION: {PROMPT_VERSION}")


# ===========================================================================
# FIXTURES: verdict dicts, built by hand
# ===========================================================================

def row(criterion, patient_value, status):
    return {"criterion": criterion, "patient_value": patient_value,
            "status": status}


UNDOCUMENTED = ASSESSMENT_UNDOCUMENTED_PATIENT_VALUE


def verdict(eligible, inclusion=(), exclusion=(), assessment="the draft"):
    return {"nct_id": "NCT00000001", "eligible": eligible,
            "assessment": assessment,
            "inclusion_criteria": list(inclusion),
            "exclusion_criteria": list(exclusion)}


# One rejection carrying a disqualifier in each arm, a documented pass and an
# undocumented row -- so a renderer that quoted everything, or that quoted only
# the first arm, is distinguishable from one that quotes the disqualifiers.
V_NOT_ELIGIBLE = verdict(
    TRIAL_VERDICT_NOT_ELIGIBLE,
    inclusion=[row("Adequate renal function (creatinine <= 1.5 x ULN)",
                   "Creatinine: 3.4 mg/dL", "not_met"),
               row("Age 18-75", "62", "met"),
               row("ECOG 0-1", UNDOCUMENTED, "not_evaluable")],
    exclusion=[row("Active hepatitis B", "HBsAg positive: reactive",
                   "violated"),
               row("Pregnancy", "Not applicable -- male", "not_violated")],
    assessment="Known disqualifier: the model's own wording.")

V_ELIGIBLE_WITH_UNDOCUMENTED = verdict(
    TRIAL_VERDICT_ELIGIBLE,
    inclusion=[row("Age 18-75", "62", "met"),
               row("ECOG 0-1", UNDOCUMENTED, "not_evaluable")],
    exclusion=[row("Active autoimmune disease", UNDOCUMENTED,
                   "not_evaluable")],
    assessment="No known disqualifiers. Age confirmed.")

V_ELIGIBLE_NO_UNDOCUMENTED = verdict(
    TRIAL_VERDICT_ELIGIBLE,
    inclusion=[row("Age 18-75", "62", "met")],
    exclusion=[row("Pregnancy", "Not applicable -- male", "not_violated")],
    assessment="No known disqualifiers.")

# not_evaluable: EMPTY ARRAYS BY CONTRACT (Section 1 of the prompt).
V_NOT_EVALUABLE = verdict(
    TRIAL_VERDICT_NOT_EVALUABLE,
    assessment="Not evaluable: the trial's criteria text is empty.")

# The two cases the pipeline's normalizer makes unreachable, kept here because
# compose_assessment is public and pure and must be total over what it is
# handed.
V_REJECTION_WITHOUT_EVIDENCE = verdict(
    TRIAL_VERDICT_NOT_ELIGIBLE,
    inclusion=[row("Age 18-75", "62", "met")],
    assessment="Known disqualifier: nothing in the arrays says so.")

# THE CORRECTED REJECTION: not_evaluable, FULL arrays, none of them
# disqualifying, and the marker Stage 5 writes at the correction site. It is
# the one not_evaluable shape whose draft is a rejection, which is why it is
# composed rather than kept. The draft below is deliberately the contradictory
# text the correction exists to stop storing.
V_CORRECTED_REJECTION = verdict(
    TRIAL_VERDICT_NOT_EVALUABLE,
    inclusion=[row("Age 18-75", "62", "met")],
    exclusion=[row("Pregnancy", "Not applicable -- male", "not_violated")],
    assessment="Known disqualifier: a 1963 tubal ligation.")
V_CORRECTED_REJECTION["not_evaluable_reason"] = UNEVALUABLE_REJECTION_UNSUPPORTED

# THE OTHER CORRECTED REJECTION: the one whose disqualifying labels were out of
# their arm's vocabulary and did not survive normalisation. Same shape --
# not_evaluable, full arrays, a rejection for a draft -- and a DIFFERENT marker,
# because a remapped row may have been a real disqualifier the model spelled
# wrong, so the sentence composed for it must be weaker. The inclusion row below
# is what such a row looks like after _normalize_arm has resolved it.
V_REMAP_NO_SURVIVOR = verdict(
    TRIAL_VERDICT_NOT_EVALUABLE,
    inclusion=[row("Age 18-75", "62", "met"),
               row("Prior therapy", "none recorded", "not_evaluable")],
    assessment="Known disqualifier: a label nobody can read.")
V_REMAP_NO_SURVIVOR["not_evaluable_reason"] = UNEVALUABLE_REMAP_NO_SURVIVOR

V_UNKNOWN_VERDICT = verdict(
    "probably eligible",
    inclusion=[row("Age 18-75", "62", "met")],
    assessment="the model's own wording")

ALL_VERDICTS = [V_NOT_ELIGIBLE, V_ELIGIBLE_WITH_UNDOCUMENTED,
                V_ELIGIBLE_NO_UNDOCUMENTED, V_NOT_EVALUABLE,
                V_CORRECTED_REJECTION, V_REMAP_NO_SURVIVOR,
                V_REJECTION_WITHOUT_EVIDENCE, V_UNKNOWN_VERDICT]


# ===========================================================================
# SECTION 1 -- the renderer, as a pure function
# ===========================================================================

print("\n" + "=" * 78)
print("SECTION 1 -- compose_assessment over synthetic verdicts")
print("=" * 78)

check("the case vocabulary is closed and its seven members are distinct",
      (len(ASSESSMENT_CASES), len(set(ASSESSMENT_CASES))), (7, 7))
check("...and every member is one this file drives",
      sorted(ASSESSMENT_CASES),
      sorted({ASSESSMENT_COMPOSED_ELIGIBLE, ASSESSMENT_COMPOSED_NOT_ELIGIBLE,
              ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
              ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR,
              ASSESSMENT_KEPT_NOT_EVALUABLE, ASSESSMENT_KEPT_NO_DISQUALIFIER,
              ASSESSMENT_KEPT_UNKNOWN_VERDICT}))
check("...and ASSESSMENT_COMPOSED_CASES is the composed subset of it, so the "
      "`kept = total - composed` arithmetic cannot miss a new member",
      (sorted(_evaluation_module.ASSESSMENT_COMPOSED_CASES),
       set(_evaluation_module.ASSESSMENT_COMPOSED_CASES) <= set(ASSESSMENT_CASES)),
      (sorted({ASSESSMENT_COMPOSED_ELIGIBLE, ASSESSMENT_COMPOSED_NOT_ELIGIBLE,
               ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
               ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR}), True))

check("1a  a rejection composes the not_eligible opening",
      case_of(V_NOT_ELIGIBLE), ASSESSMENT_COMPOSED_NOT_ELIGIBLE)
_ne = compose(V_NOT_ELIGIBLE)
check("1a  ...and the text begins with it",
      _ne.startswith(ASSESSMENT_NOT_ELIGIBLE_OPENING + " "), True)
check("1a  ...naming both arms' disqualifiers, in inclusion-then-exclusion "
      "order (both operands asserted present, so the ordering cannot pass by "
      "both being absent)",
      (pos(_ne, "Adequate renal function") >= 0,
       pos(_ne, "Active hepatitis B") >= 0,
       pos(_ne, "Adequate renal function") < pos(_ne, "Active hepatitis B"),
       'Inclusion criterion "Adequate renal function' in _ne,
       'Exclusion criterion "Active hepatitis B"' in _ne),
      (True, True, True, True, True))
check("1a  ...and quoting each one's patient_value",
      ('patient record: "Creatinine: 3.4 mg/dL"' in _ne,
       'patient record: "HBsAg positive: reactive"' in _ne), (True, True))
check("1a  ...and saying which status each row carried",
      ('" not met;' in _ne, '" violated;' in _ne), (True, True))
check("1a  ...and NOT quoting the rows that disqualify nobody",
      ("Age 18-75" in _ne, "ECOG 0-1" in _ne, "Pregnancy" in _ne),
      (False, False, False))

check("1b  an eligible trial composes the eligible opening",
      case_of(V_ELIGIBLE_WITH_UNDOCUMENTED), ASSESSMENT_COMPOSED_ELIGIBLE)
_el = compose(V_ELIGIBLE_WITH_UNDOCUMENTED)
check("1b  ...and the text begins with it",
      _el.startswith(ASSESSMENT_ELIGIBLE_OPENING), True)
check("1b  ...carrying the undocumented clause",
      ASSESSMENT_UNDOCUMENTED_OPENING in _el, True)
check("1b  ...naming exactly the two undocumented criteria, in array order",
      (pos(_el, "ECOG 0-1") >= 0,
       pos(_el, "Active autoimmune disease") >= 0,
       pos(_el, "ECOG 0-1") < pos(_el, "Active autoimmune disease"),
       "Age 18-75" in _el),
      (True, True, True, False))

check("1c  an eligible trial with nothing undocumented composes the opening "
      "ALONE -- no empty clause",
      compose(V_ELIGIBLE_NO_UNDOCUMENTED), ASSESSMENT_ELIGIBLE_OPENING)
check("1c  ...and that is exactly the opening, with no trailing space",
      compose(V_ELIGIBLE_NO_UNDOCUMENTED) == ASSESSMENT_ELIGIBLE_OPENING
      == "No known disqualifiers.", True)

check("1d  a not_evaluable trial keeps the model's own text UNCHANGED",
      compose(V_NOT_EVALUABLE), V_NOT_EVALUABLE["assessment"])
check("1d  ...and reports the kept case",
      case_of(V_NOT_EVALUABLE), ASSESSMENT_KEPT_NOT_EVALUABLE)
check("1d  ...non-degeneracy: that draft is the one the prompt mandates and "
      "is not what either composed branch would produce",
      (V_NOT_EVALUABLE["assessment"].startswith(
          ASSESSMENT_NOT_EVALUABLE_OPENING),
       V_NOT_EVALUABLE["assessment"].startswith(ASSESSMENT_ELIGIBLE_OPENING)),
      (True, False))

check("1d' a CORRECTED rejection composes fixed text instead of keeping its "
      "draft", compose(V_CORRECTED_REJECTION),
      ASSESSMENT_UNSUPPORTED_REJECTION_TEXT)
check("1d' ...and reports the composed case, not a kept one and not an anomaly",
      (case_of(V_CORRECTED_REJECTION),
       case_of(V_CORRECTED_REJECTION)
       in _evaluation_module.ASSESSMENT_COMPOSED_CASES,
       case_of(V_CORRECTED_REJECTION)
       in _evaluation_module._ASSESSMENT_ANOMALY_CASES),
      (ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION, True, False))
check("1d' ...opening with the mandated not-evaluable opening",
      ASSESSMENT_UNSUPPORTED_REJECTION_TEXT.startswith(
          ASSESSMENT_NOT_EVALUABLE_OPENING + " "), True)
check("1d' ...saying what the model did and what the node did",
      ("rejected this trial" in ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
       "no disqualifying criterion" in ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
       "corrected to not evaluable" in ASSESSMENT_UNSUPPORTED_REJECTION_TEXT),
      (True, True, True))
check("1d' ...and compose_assessment is still PURE: the draft on the verdict "
      "it was handed is untouched",
      V_CORRECTED_REJECTION["assessment"],
      "Known disqualifier: a 1963 tubal ligation.")
check("1d' non-degeneracy: the draft it REPLACED is the contradiction this "
      "case exists for, and is not what is stored",
      (V_CORRECTED_REJECTION["assessment"].startswith(
          ASSESSMENT_NOT_ELIGIBLE_OPENING),
       compose(V_CORRECTED_REJECTION) == V_CORRECTED_REJECTION["assessment"]),
      (True, False))

# THE MARKER IS WHAT SELECTS THE CASE, and only on a not_evaluable verdict.
# Both halves are asserted, because a predicate reading only the marker would
# compose this text over a rejection and one reading only the verdict would
# compose it over every non-evaluation in the pipeline.
_marked_eligible = verdict(TRIAL_VERDICT_ELIGIBLE,
                           inclusion=[row("Age 18-75", "62", "met")])
_marked_eligible["not_evaluable_reason"] = UNEVALUABLE_REJECTION_UNSUPPORTED
check("1d' the marker alone does NOT compose it: the verdict must also be "
      "not_evaluable", case_of(_marked_eligible), ASSESSMENT_COMPOSED_ELIGIBLE)
_other_reason = verdict(TRIAL_VERDICT_NOT_EVALUABLE, assessment="Not evaluable: x")
_other_reason["not_evaluable_reason"] = "omitted_from_model_response"
check("1d' a not_evaluable carrying a DIFFERENT reason keeps its draft",
      (case_of(_other_reason), compose(_other_reason)),
      (ASSESSMENT_KEPT_NOT_EVALUABLE, "Not evaluable: x"))
check("1d' ...and one carrying no reason key at all keeps its draft",
      case_of(V_NOT_EVALUABLE), ASSESSMENT_KEPT_NOT_EVALUABLE)

# THE SECOND CORRECTED REJECTION. Same construction as 1d', different marker,
# different sentence -- and the difference is the point rather than a detail:
# this population's disqualifying evidence may have been written in a label the
# arm's vocabulary does not contain, so the text may not say the model cited no
# disqualifying criterion. That sentence belongs to 1d' alone.
check("1d\" a remap-corrected rejection composes its OWN fixed text",
      compose(V_REMAP_NO_SURVIVOR), ASSESSMENT_REMAP_NO_SURVIVOR_TEXT)
check("1d\" ...reporting its own composed case, not the sibling's and not an "
      "anomaly",
      (case_of(V_REMAP_NO_SURVIVOR),
       case_of(V_REMAP_NO_SURVIVOR)
       in _evaluation_module.ASSESSMENT_COMPOSED_CASES,
       case_of(V_REMAP_NO_SURVIVOR)
       in _evaluation_module._ASSESSMENT_ANOMALY_CASES),
      (ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR, True, False))
check("1d\" ...opening with the mandated not-evaluable opening",
      (ASSESSMENT_REMAP_NO_SURVIVOR_TEXT.startswith(
          ASSESSMENT_NOT_EVALUABLE_OPENING + " "),
       ASSESSMENT_REMAP_NO_SURVIVOR_TEXT.startswith(
           ASSESSMENT_NOT_ELIGIBLE_OPENING)),
      (True, False))
check("1d\" ...and it is WEAKER than the sibling's: it says what the model and "
      "the node did, and does NOT say the model cited no disqualifier",
      ("rejected this trial" in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT,
       "corrected to not evaluable" in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT,
       "no disqualifying criterion" in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT),
      (True, True, False))
check("1d\" non-degeneracy: the sibling text DOES make that claim, so the "
      "check above discriminates rather than passing on empty text",
      "no disqualifying criterion" in ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
      True)
check("1d\" the two markers select different texts on the SAME verdict shape",
      compose(dict(V_REMAP_NO_SURVIVOR,
                   not_evaluable_reason=UNEVALUABLE_REJECTION_UNSUPPORTED))
      == compose(V_REMAP_NO_SURVIVOR), False)
check("1d\" ...and the marker alone does not compose it: the verdict must "
      "also be not_evaluable",
      case_of(dict(V_REMAP_NO_SURVIVOR, eligible=TRIAL_VERDICT_ELIGIBLE)),
      ASSESSMENT_COMPOSED_ELIGIBLE)
check("1d\" compose_assessment is still PURE: the draft it was handed is "
      "untouched", V_REMAP_NO_SURVIVOR["assessment"],
      "Known disqualifier: a label nobody can read.")

# EMPTY ARRAYS, on every verdict. The not_evaluable case is the contract; the
# other two are the degenerate inputs a renderer must not crash on.
check("1e  empty arrays: not_evaluable keeps its draft",
      compose(verdict(TRIAL_VERDICT_NOT_EVALUABLE, assessment="Not evaluable: x")),
      "Not evaluable: x")
check("1e  empty arrays: eligible composes the bare opening",
      compose(verdict(TRIAL_VERDICT_ELIGIBLE)), ASSESSMENT_ELIGIBLE_OPENING)
check("1e  empty arrays: a rejection has nothing to cite, so it keeps the "
      "draft rather than composing an opening with no evidence after it",
      (case_of(verdict(TRIAL_VERDICT_NOT_ELIGIBLE, assessment="d")),
       compose(verdict(TRIAL_VERDICT_NOT_ELIGIBLE, assessment="d"))),
      (ASSESSMENT_KEPT_NO_DISQUALIFIER, "d"))
check("1e  MISSING arrays entirely (the shape _unevaluable_entry builds) do "
      "not raise",
      compose({"eligible": TRIAL_VERDICT_NOT_EVALUABLE,
               "assessment": "Not assessed.", "criteria": []}),
      "Not assessed.")

check("1f  an unrecognised verdict keeps the draft and is named as such",
      (case_of(V_UNKNOWN_VERDICT), compose(V_UNKNOWN_VERDICT)),
      (ASSESSMENT_KEPT_UNKNOWN_VERDICT, V_UNKNOWN_VERDICT["assessment"]))

check("1g  a rejection with no disqualifying row keeps the draft",
      (case_of(V_REJECTION_WITHOUT_EVIDENCE),
       compose(V_REJECTION_WITHOUT_EVIDENCE)),
      (ASSESSMENT_KEPT_NO_DISQUALIFIER,
       V_REJECTION_WITHOUT_EVIDENCE["assessment"]))

# THE ONLY SOURCE OF A "not documented" CLAIM. A row that is not_evaluable for
# any other reason, or that carries an absent-data SYNONYM rather than the
# canonical phrase, must not enter the clause: the absent-data validator's
# twenty-synonym predicate exists to catch a bad disqualification, and using it
# here would turn free-written model text into a positive claim about the record.
_SYNONYM_ROWS = [row("Prior platinum therapy", "not documented",
                     "not_evaluable"),
                 row("Hepatitis serology", "unknown", "not_evaluable"),
                 row("Brain metastases", "", "not_evaluable"),
                 row("HER2 status", "Not available", "not_evaluable")]
_syn = compose(verdict(TRIAL_VERDICT_ELIGIBLE, inclusion=_SYNONYM_ROWS))
check("1h  an absent-data SYNONYM does not license the undocumented clause",
      (ASSESSMENT_UNDOCUMENTED_OPENING in _syn, _syn),
      (False, ASSESSMENT_ELIGIBLE_OPENING))
check("1h  ...whereas the canonical phrase does (the other half of the "
      "control)",
      ASSESSMENT_UNDOCUMENTED_OPENING in compose(
          verdict(TRIAL_VERDICT_ELIGIBLE,
                  inclusion=[row("HER2 status", UNDOCUMENTED,
                                 "not_evaluable")])),
      True)
check("1h  ...and the canonical phrase under a NON-not_evaluable status does "
      "not either: a row whose status contradicts its own value is not "
      "evidence of anything",
      ASSESSMENT_UNDOCUMENTED_OPENING in compose(
          verdict(TRIAL_VERDICT_ELIGIBLE,
                  inclusion=[row("HER2 status", UNDOCUMENTED, "met")])),
      False)
check("1h  ...whitespace and case around the canonical phrase are tolerated, "
      "because they are transcription rather than vocabulary",
      ASSESSMENT_UNDOCUMENTED_OPENING in compose(
          verdict(TRIAL_VERDICT_ELIGIBLE,
                  inclusion=[row("HER2 status", "  not in PATIENT record ",
                                 "not_evaluable")])),
      True)

check("1i  the renderer is pure: composing twice returns the same string and "
      "leaves the verdict dict unchanged",
      (compose(V_NOT_ELIGIBLE) == compose(V_NOT_ELIGIBLE),
       V_NOT_ELIGIBLE["assessment"],
       len(V_NOT_ELIGIBLE["inclusion_criteria"])),
      (True, "Known disqualifier: the model's own wording.", 3))


# ===========================================================================
# SECTION 2 -- quoting fidelity
# ===========================================================================
#
# Every criterion and patient_value a composed assessment cites must appear in
# it VERBATIM. Trimming is the only transformation the renderer applies; a
# renderer that rephrased, truncated or re-punctuated would be making the whole
# claim of this mechanism about words it had changed.

print("\n" + "=" * 78)
print("SECTION 2 -- quoting fidelity")
print("=" * 78)

# Text chosen to break a naive renderer: internal quotes, a semicolon, a
# non-ASCII comparator, a trailing period, and surrounding whitespace.
_AWKWARD = row('  Adequate renal function; "creatinine ≤ 1.5 x ULN".  ',
               '  Creatinine: 3.4 mg/dL (2026-01-02); repeat 3.2.  ',
               "not_met")
_awk = compose(verdict(TRIAL_VERDICT_NOT_ELIGIBLE, inclusion=[_AWKWARD]))

check("2a  the criterion appears verbatim, trimmed",
      _AWKWARD["criterion"].strip() in _awk, True)
check("2a  the patient_value appears verbatim, trimmed",
      _AWKWARD["patient_value"].strip() in _awk, True)
check("2a  non-degeneracy: the awkward text is genuinely awkward",
      ('"' in _AWKWARD["criterion"], ";" in _AWKWARD["criterion"],
       "≤" in _AWKWARD["criterion"],
       _AWKWARD["criterion"] != _AWKWARD["criterion"].strip()),
      (True, True, True, True))

_und = compose(V_ELIGIBLE_WITH_UNDOCUMENTED)
check("2b  every undocumented criterion appears verbatim in the clause",
      [c["criterion"] for c in
       V_ELIGIBLE_WITH_UNDOCUMENTED["inclusion_criteria"]
       + V_ELIGIBLE_WITH_UNDOCUMENTED["exclusion_criteria"]
       if c["patient_value"] == UNDOCUMENTED and c["criterion"] not in _und],
      [])
check("2b  ...and the clause quotes the criterion, never the patient_value: "
      "the phrase is the licence, not the content",
      UNDOCUMENTED in _und, False)


# ===========================================================================
# SECTION 3 -- the two invariants, as checkers that can fire
# ===========================================================================

print("\n" + "=" * 78)
print("SECTION 3 -- the invariants, over every composition and over a "
      "violation")
print("=" * 78)


def all_rows(v):
    """Every criterion row of a verdict, both arms, defensively."""
    out = []
    for key in ("inclusion_criteria", "exclusion_criteria"):
        rows = v.get(key)
        if isinstance(rows, list):
            out += [r for r in rows if isinstance(r, dict)]
    return out


def undocumented_clause_criteria(text):
    """The criterion strings a composed undocumented clause names.

    Parses the rendering rather than the verdict, which is the point: the
    invariant is about what the STORED TEXT claims. The clause is
    `<opening> "a"; "b"; "c".` -- split on the quote-semicolon-quote seam, so a
    criterion containing a bare semicolon or a bare quote does not split. A
    criterion containing the exact sequence `"; "` would; that is a stated
    limit of the parser, not of the renderer.
    """
    marker = ASSESSMENT_UNDOCUMENTED_OPENING + ' "'
    if marker not in text:
        return []
    tail = text[text.index(marker) + len(marker):]
    if tail.endswith('".'):
        tail = tail[:-2]
    return tail.split('"; "')


def undocumented_violations(text, v, composed=None):
    """Criteria the text calls undocumented that the arrays do not support.

    GATED ON THE COMPOSITION CASE, and that is a real limit rather than a
    convenience. The three KEPT cases store the model's own draft, which this
    mechanism does not write and cannot vouch for; asserting this invariant
    over one would be asserting that the model never wrote a bad sentence,
    which is the thing that is false and the reason the composition exists.
    `composed` is overridable so a control can drive the body directly.
    """
    if composed is None:
        composed = case_of(v) == ASSESSMENT_COMPOSED_ELIGIBLE
    if not composed:
        return []
    supported = {r.get("criterion", "").strip()
                 for r in all_rows(v)
                 if r.get("status") == "not_evaluable"
                 and str(r.get("patient_value", "")).strip().casefold()
                 == UNDOCUMENTED.casefold()}
    return [c for c in undocumented_clause_criteria(text) if c not in supported]


def disqualifier_violation(text, v, composed=None):
    """A message if a COMPOSED rejection claims what the arrays do not carry.

    Same gating and the same reason as above: a kept draft may legitimately
    open "Known disqualifier:" with nothing in the arrays behind it -- that is
    exactly the shape the node counts into ASSESSMENT_COMPOSITION_ANOMALIES and
    logs at WARNING, rather than one this checker should read as a rendering
    fault.
    """
    if composed is None:
        composed = case_of(v) == ASSESSMENT_COMPOSED_NOT_ELIGIBLE
    if not composed:
        return None
    if not text.startswith(ASSESSMENT_NOT_ELIGIBLE_OPENING):
        return f"composed text does not open with the mandated opening: {text[:40]!r}"
    cited = [r for r in all_rows(v)
             if r.get("status") in ("not_met", "violated")]
    if not cited:
        return "composed a disqualification with no not_met/violated row"
    for r in cited:
        if r.get("criterion", "").strip() not in text:
            return f"row {r.get('criterion')!r} is not quoted in the text"
    return None


_COMPOSED = [(v, compose(v)) for v in ALL_VERDICTS]
_COMPOSED_ONLY = [(v, t) for v, t in _COMPOSED
                  if case_of(v) in _evaluation_module.ASSESSMENT_COMPOSED_CASES]

check("3a  non-degeneracy: the battery really does compose text for every "
      "composed case, and keeps a draft for the other three",
      sorted({case_of(v) for v in ALL_VERDICTS}), sorted(set(ASSESSMENT_CASES)))
check("3a  ...and five of the eight are composed, so the invariants below are "
      "not passing over an empty list",
      len(_COMPOSED_ONLY), 5)

check("3b  no composed assessment names an unsupported undocumented criterion",
      [(v["eligible"], undocumented_violations(t, v))
       for v, t in _COMPOSED if undocumented_violations(t, v)], [])
check("3b  non-degeneracy: the checker did read a clause with criteria in it",
      len(undocumented_clause_criteria(compose(V_ELIGIBLE_WITH_UNDOCUMENTED))),
      2)

# THE CHECKER MUST FIRE. A hand-built text that names a criterion the arrays do
# not support -- exactly the audited defect: prose calling a field "not
# documented" while the row quotes a value for it.
_FABRICATED_UNDOCUMENTED = (
    f'{ASSESSMENT_ELIGIBLE_OPENING} {ASSESSMENT_UNDOCUMENTED_OPENING} '
    f'"ECOG 0-1"; "Creatinine".')
check("3b  CONTROL: the checker fires on a clause naming a criterion the "
      "arrays document",
      undocumented_violations(
          _FABRICATED_UNDOCUMENTED,
          verdict(TRIAL_VERDICT_ELIGIBLE,
                  inclusion=[row("ECOG 0-1", UNDOCUMENTED, "not_evaluable"),
                             row("Creatinine", "3.4 mg/dL", "not_met")])),
      ["Creatinine"])

check("3c  every composed 'Known disqualifier:' cites at least one "
      "not_met/violated row, and quotes each",
      [(v["eligible"], disqualifier_violation(t, v))
       for v, t in _COMPOSED if disqualifier_violation(t, v)], [])
check("3c  non-degeneracy: one COMPOSED text really does open that way",
      sum(1 for _v, t in _COMPOSED_ONLY
          if t.startswith(ASSESSMENT_NOT_ELIGIBLE_OPENING)), 1)
check("3c  CONTROL: the checker fires on a composed rejection with no "
      "disqualifying row behind it",
      disqualifier_violation(
          f"{ASSESSMENT_NOT_ELIGIBLE_OPENING} creatinine is high.",
          verdict(TRIAL_VERDICT_NOT_ELIGIBLE,
                  inclusion=[row("Creatinine", "3.4", "met")]),
          composed=True),
      "composed a disqualification with no not_met/violated row")
check("3c  CONTROL: ...and on a composed rejection that fails to quote a row "
      "the arrays carry -- a renderer that dropped a disqualifier",
      disqualifier_violation(
          f"{ASSESSMENT_NOT_ELIGIBLE_OPENING} creatinine is high.",
          verdict(TRIAL_VERDICT_NOT_ELIGIBLE,
                  inclusion=[row("Creatinine", "3.4 mg/dL", "not_met")])),
      "row 'Creatinine' is not quoted in the text")
check("3c  ...and a KEPT draft that opens that way is deliberately outside "
      "this invariant, because the node records it as an anomaly instead",
      (disqualifier_violation(compose(V_REJECTION_WITHOUT_EVIDENCE),
                              V_REJECTION_WITHOUT_EVIDENCE),
       compose(V_REJECTION_WITHOUT_EVIDENCE).startswith(
           ASSESSMENT_NOT_ELIGIBLE_OPENING),
       case_of(V_REJECTION_WITHOUT_EVIDENCE)),
      (None, True, ASSESSMENT_KEPT_NO_DISQUALIFIER))

# THE DEFECT THAT SHIPPED: both mandated openings in one assessment. A composed
# text cannot contain both, because the branch that writes one cannot write the
# other -- asserted rather than argued.
check("3d  no composed assessment carries both mandated openings",
      [t for _v, t in _COMPOSED_ONLY
       if ASSESSMENT_ELIGIBLE_OPENING in t
       and ASSESSMENT_NOT_ELIGIBLE_OPENING in t], [])
check("3d  non-degeneracy: each opening occurs on its own among the composed "
      "texts",
      (sum(1 for _v, t in _COMPOSED_ONLY
           if t.startswith(ASSESSMENT_ELIGIBLE_OPENING)),
       sum(1 for _v, t in _COMPOSED_ONLY
           if t.startswith(ASSESSMENT_NOT_ELIGIBLE_OPENING))),
      (2, 1))


# ===========================================================================
# SECTION 4 -- number provenance
# ===========================================================================
#
# The audited defect: an assessment naming a numeric threshold that appears
# nowhere in the trial's criteria text. A composed assessment cannot do it,
# because its only inputs are the rows -- and the scaffolding words the renderer
# adds carry no digits. This section is what says so mechanically.

print("\n" + "=" * 78)
print("SECTION 4 -- every number in a composed assessment came from a row")
print("=" * 78)

# A numeric TOKEN, not a digit: "1.5" must be provenanced as "1.5" and must not
# be satisfied by a stray "5" somewhere in the corpus. Dates and ranges tokenize
# into their parts, which is the conservative direction -- it can only make the
# checker demand more, never less.
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def numeric_tokens(text):
    return _NUMBER_RE.findall(text or "")


def unprovenanced_numbers(text, v):
    """Numeric tokens in `text` that no criterion or patient_value carries."""
    corpus = " ".join(f'{r.get("criterion", "")} {r.get("patient_value", "")}'
                      for r in all_rows(v))
    allowed = set(numeric_tokens(corpus))
    return sorted({t for t in numeric_tokens(text) if t not in allowed})


check("4a  no composed assessment carries a number its rows do not",
      [(v["eligible"], unprovenanced_numbers(t, v))
       for v, t in _COMPOSED
       if case_of(v) in _evaluation_module.ASSESSMENT_COMPOSED_CASES
       and unprovenanced_numbers(t, v)], [])
check("4a  non-degeneracy: the compositions checked DO contain numbers, so "
      "the check above is not passing over empty input",
      sorted(numeric_tokens(compose(V_NOT_ELIGIBLE))),
      sorted(["1.5", "3.4"]))

check("4b  the renderer's own scaffolding contributes no digits",
      numeric_tokens(ASSESSMENT_ELIGIBLE_OPENING
                     + ASSESSMENT_NOT_ELIGIBLE_OPENING
                     + ASSESSMENT_UNSUPPORTED_REJECTION_TEXT
                     + ASSESSMENT_REMAP_NO_SURVIVOR_TEXT
                     + ASSESSMENT_UNDOCUMENTED_OPENING
                     + 'Inclusion criterion "" not met; patient record: "".'
                     + 'Exclusion criterion "" violated; patient record: "".'),
      [])

# THE FIRING CONTROL. The audited assessment's shape: a threshold the arrays
# never carried, in text that otherwise looks composed.
_INVENTED = ('Known disqualifier: Inclusion criterion "Adequate renal '
             'function" not met; patient record: "Creatinine: 3.4 mg/dL" '
             '(threshold 1.5 x ULN).')
check("4c  CONTROL: an invented threshold is reported",
      unprovenanced_numbers(
          _INVENTED,
          verdict(TRIAL_VERDICT_NOT_ELIGIBLE,
                  inclusion=[row("Adequate renal function",
                                 "Creatinine: 3.4 mg/dL", "not_met")])),
      ["1.5"])
check("4c  ...and the number that IS in a row is not reported alongside it",
      "3.4" in unprovenanced_numbers(
          _INVENTED,
          verdict(TRIAL_VERDICT_NOT_ELIGIBLE,
                  inclusion=[row("Adequate renal function",
                                 "Creatinine: 3.4 mg/dL", "not_met")])),
      False)
check("4c  ...and a token is not satisfied by a substring of another number: "
      "'1.5' is not provenanced by a corpus containing only '11.55'",
      unprovenanced_numbers("value 1.5",
                            verdict(TRIAL_VERDICT_ELIGIBLE,
                                    inclusion=[row("x", "11.55", "met")])),
      ["1.5"])


# ===========================================================================
# SECTION 5 -- placement, through the real node
# ===========================================================================
#
# compose_assessment being right is half of it. The other half is WHERE it runs:
# every pass in node_llm_classifier_evaluation can still move what it reads --
# the label normalizer rewrites a criterion status, Step 3 rewrites the verdict,
# the absent-data validator rewrites both AND can flip a rejection to eligible,
# and the reconciliation appends entries that were never in the response. A
# composition that ran earlier would render a state the node then changed.

print("\n" + "=" * 78)
print("SECTION 5 -- through node_llm_classifier_evaluation, with a stub")
print("=" * 78)

PATIENT = {
    "patient_id": "composed-assessment-patient",
    "demographics": {"age": 62, "sex": "male", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254637007",
                    "display": "Non-small cell lung cancer",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(nct_id):
    return {
        "nct_id": nct_id, "title": "A Study of Something", "phase": "Phase 2",
        "conditions": ["Lung Neoplasms"], "mesh_terms": ["Lung Neoplasms"],
        "eligibility": {"inclusion_criteria": "Adults with NSCLC",
                        "exclusion_criteria": "Pregnancy",
                        "min_age": 18, "max_age": 99, "sex": "ALL"},
    }


class _StubMessage:
    def __init__(self, content):
        self.content = content


class _StubChoice:
    def __init__(self, content):
        self.message = _StubMessage(content)
        self.finish_reason = "stop"


class _StubUsage:
    prompt_tokens = 1000
    completion_tokens = 200


class _StubResponse:
    def __init__(self, content):
        self.choices = [_StubChoice(content)]
        self.usage = _StubUsage()
        # None means "the response carried no model field", which the node
        # handles explicitly and which keeps MatchingModelMismatchError out of
        # a test that is not about it.
        self.model = None


class StubOpenAI:
    """Serves one chosen JSON payload. No network, no key, no spend."""

    def __init__(self, payload):
        self._payload = json.dumps(payload)
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        self.calls += 1
        return _StubResponse(self._payload)


def run_stage5(payload, nct_ids):
    """Drive Stage 5 with a stubbed model. Returns (result, stderr_text)."""
    state = {
        "patient_data": PATIENT,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "llm_classifier_retries": 0,
        "mesh_filter_applied": True,
        "mesh_filter_skip_reason": "applied",
        "stage_timings": {},
    }
    saved = deps.set_overrides({"openai_client": StubOpenAI(payload)})
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            result = node_llm_classifier_evaluation(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        result = {"evaluations": [], "raised": f"{type(exc).__name__}: {exc}"}
    finally:
        deps.restore_overrides(saved)
    return result, err.getvalue()


def entry_for(result, nct_id):
    """One evaluation by id, or a named absence. NEVER a bare index."""
    for e in result.get("evaluations", []):
        if e.get("nct_id") == nct_id:
            return e
    return {"<absent>": nct_id}


def log_records(stderr_text, event=None):
    out = []
    for line in stderr_text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if event is None or record.get("event") == event:
            out.append(record)
    return out


def logfield(records, key):
    if not records:
        return "<no such record>"
    return records[0].get(key, "<no such field>")


_IDS = ("NCT00000001", "NCT00000002", "NCT00000003", "NCT00000004",
        "NCT00000005")

_ANOMALIES_BEFORE = dict(ASSESSMENT_COMPOSITION_ANOMALIES)

_PAYLOAD = {"evaluations": [
    # 1: eligible, with one documented row and one undocumented row.
    {"nct_id": _IDS[0], "match_score": 0.0,
     "assessment": "No known disqualifiers. ECOG not documented. "
                   "Creatinine within 1.5 x ULN.",
     "inclusion_criteria": [
         {"criterion": "Age 18-75", "patient_value": "62", "status": "met"},
         {"criterion": "ECOG 0-1", "patient_value": UNDOCUMENTED,
          "status": "not_evaluable"}],
     "exclusion_criteria": [],
     "eligible": "eligible"},
    # 2: a genuine rejection.
    {"nct_id": _IDS[1], "match_score": 0.0,
     "assessment": "Known disqualifier: creatinine is 3.4 which exceeds the "
                   "usual 1.5 x ULN limit.",
     "inclusion_criteria": [
         {"criterion": "Adequate renal function",
          "patient_value": "Creatinine: 3.4 mg/dL", "status": "not_met"}],
     "exclusion_criteria": [],
     "eligible": "not_eligible"},
    # 3: the absent-data validator's case. The model rejected on a criterion
    #    whose patient_value says the data is absent, so the validator remaps
    #    the status and flips the trial to eligible. The patient_value is a
    #    SYNONYM, not the canonical phrase, so the composed text must be the
    #    bare opening -- no undocumented clause, and no trace of the model's
    #    "Known disqualifier:".
    {"nct_id": _IDS[2], "match_score": 0.0,
     "assessment": "Known disqualifier: no ECOG on file, assumed 3.",
     "inclusion_criteria": [
         {"criterion": "ECOG 0-1", "patient_value": "not documented",
          "status": "not_met"}],
     "exclusion_criteria": [],
     "eligible": "not_eligible"},
    # 4: not_evaluable, empty arrays by contract.
    {"nct_id": _IDS[3], "match_score": 0.0,
     "assessment": "Not evaluable: the criteria text is empty.",
     "inclusion_criteria": [], "exclusion_criteria": [],
     "eligible": "not_evaluable"},
    # _IDS[4] is deliberately ABSENT: the reconciliation constructs its entry.
]}

_RES, _ERR = run_stage5(_PAYLOAD, _IDS)

check("5a  the node returned an entry for every trial sent",
      sorted(e.get("nct_id") for e in _RES.get("evaluations", [])),
      sorted(_IDS))

_e1 = entry_for(_RES, _IDS[0])
check("5a  eligible: the stored assessment is the composed one",
      _e1.get("assessment"),
      f'{ASSESSMENT_ELIGIBLE_OPENING} {ASSESSMENT_UNDOCUMENTED_OPENING} '
      f'"ECOG 0-1".')
check("5a  ...and the model's draft is kept beside it, unedited",
      _e1.get("assessment_draft"),
      "No known disqualifiers. ECOG not documented. "
      "Creatinine within 1.5 x ULN.")
check("5a  ...so the invented threshold the draft carried is NOT in the "
      "stored text",
      ("1.5" in _e1.get("assessment_draft", ""),
       unprovenanced_numbers(_e1.get("assessment", ""), _e1)),
      (True, []))

_e2 = entry_for(_RES, _IDS[1])
check("5b  not_eligible: the stored assessment cites the row",
      _e2.get("assessment"),
      'Known disqualifier: Inclusion criterion "Adequate renal function" not '
      'met; patient record: "Creatinine: 3.4 mg/dL".')
check("5b  ...and the draft's invented '1.5' is gone from the stored text "
      "while the row's 3.4 survives",
      (unprovenanced_numbers(_e2.get("assessment", ""), _e2),
       "3.4" in _e2.get("assessment", ""),
       "1.5" in _e2.get("assessment_draft", "")),
      ([], True, True))

_e3 = entry_for(_RES, _IDS[2])
check("5c  the absent-data validator flipped the trial to eligible",
      _e3.get("eligible"), TRIAL_VERDICT_ELIGIBLE)
check("5c  ...and the composition ran AFTER it: the stored text is the "
      "eligible opening, with no clause (the value was a synonym, not the "
      "canonical phrase)",
      _e3.get("assessment"), ASSESSMENT_ELIGIBLE_OPENING)
check("5c  ...carrying no trace of the model's rejection wording, and none of "
      "the validator's old bracketed annotation",
      (ASSESSMENT_NOT_ELIGIBLE_OPENING in _e3.get("assessment", ""),
       "Validator corrected" in _e3.get("assessment", "")),
      (False, False))
check("5c  ...while the draft still holds what the model actually wrote",
      _e3.get("assessment_draft"),
      "Known disqualifier: no ECOG on file, assumed 3.")
check("5c  ...and the correction is still reported in the structured log",
      logfield(log_records(_ERR, "absent_data_correction"), "count"), 1)

_e4 = entry_for(_RES, _IDS[3])
check("5d  not_evaluable: the model's own text is stored unchanged",
      _e4.get("assessment"), "Not evaluable: the criteria text is empty.")
check("5d  ...and it is its own draft",
      _e4.get("assessment_draft"), _e4.get("assessment"))

_e5 = entry_for(_RES, _IDS[4])
check("5e  a trial the model omitted is reconstructed by the reconciliation",
      _e5.get("eligible"), TRIAL_VERDICT_NOT_EVALUABLE)
check("5e  ...keeps the constructed text as its assessment",
      "no entry for this trial" in _e5.get("assessment", ""), True)
check("5e  ...and carries assessment_draft too, so no consumer has to test "
      "for the key's presence",
      ("assessment_draft" in _e5, _e5.get("assessment_draft"),
       sorted(("assessment_draft" in e) for e in _RES["evaluations"])),
      (True, _e5.get("assessment"), [True] * len(_IDS)))

check("5f  one composition event, counting the two composed and the three "
      "kept",
      (logfield(log_records(_ERR, "assessment_composition"), "count"),
       logfield(log_records(_ERR, "assessment_composition"), "kept"),
       logfield(log_records(_ERR, "assessment_composition"), "total")),
      (3, 2, 5))
check("5f  ...and it names the cases that fired",
      logfield(log_records(_ERR, "assessment_composition"), "reason"),
      sorted([ASSESSMENT_COMPOSED_ELIGIBLE, ASSESSMENT_COMPOSED_NOT_ELIGIBLE,
              ASSESSMENT_KEPT_NOT_EVALUABLE]))
check("5f  no anomaly was recorded, and none was logged: the normalizer makes "
      "both anomaly cases unreachable through the node",
      (dict(ASSESSMENT_COMPOSITION_ANOMALIES) == _ANOMALIES_BEFORE,
       log_records(_ERR, "assessment_composition_anomaly")),
      (True, []))

check("5g  every stored assessment in the run satisfies both invariants",
      [(e.get("nct_id"), undocumented_violations(e.get("assessment", ""), e),
        disqualifier_violation(e.get("assessment", ""), e))
       for e in _RES["evaluations"]
       if undocumented_violations(e.get("assessment", ""), e)
       or disqualifier_violation(e.get("assessment", ""), e)], [])
check("5g  ...and carries no number its own rows do not",
      [(e.get("nct_id"), unprovenanced_numbers(e.get("assessment", ""), e))
       for e in _RES["evaluations"]
       if e.get("eligible") != TRIAL_VERDICT_NOT_EVALUABLE
       and unprovenanced_numbers(e.get("assessment", ""), e)], [])

# 5h -- PLACEMENT, STRUCTURALLY, BECAUSE BEHAVIOUR CANNOT SEE ALL OF IT.
#
# Two of the placement properties have no behavioural witness on today's code
# and a revert harness is what established that, rather than reading:
#
#   * the draft snapshot being taken EARLY is unobservable while nothing
#     between the parse and the composition rewrites `assessment` -- the one
#     thing that did (the absent-data validator's bracketed annotation) was
#     deleted by this same change. Dropping the early snapshot leaves the
#     composition pass's own setdefault producing an identical result, so every
#     behavioural check still passed. It is kept as defence against the next
#     validator that patches the field, and this is what makes keeping it
#     testable.
#   * the composition running LAST is observable only through a pass that
#     actually moves it, and only for the mutations that happen to be exercised.
#
# So the source order is asserted directly. Every marker is located in the
# module's OWN file, found through its __file__ rather than through this test's
# directory, and every position is asserted present before any of them are
# compared -- an ordering over four -1s would otherwise hold trivially.
_EVAL_SRC = open(os.path.abspath(_evaluation_module.__file__),
                 encoding="utf-8").read()

_MARKERS = {
    "snapshot": 'eval_result["assessment_draft"] = eval_result.get("assessment", "")',
    "validator": "# ── Absent-data validator",
    "reconciliation": "evaluations.extend(unevaluable)",
    "composition": '_e["assessment"] = compose_assessment(_e)',
}
_AT = {name: pos(_EVAL_SRC, needle) for name, needle in _MARKERS.items()}

check("5h  every placement marker is present in evaluation.py exactly once",
      {name: (_AT[name] >= 0, _EVAL_SRC.count(needle))
       for name, needle in _MARKERS.items()},
      {name: (True, 1) for name in _MARKERS})
check("5h  ...and they occur in the only order that is correct: the draft is "
      "snapshotted before the validator can rewrite the field, and the "
      "composition runs after the validator AND after the reconciliation has "
      "appended every constructed entry",
      _AT["snapshot"] < _AT["validator"] < _AT["reconciliation"]
      < _AT["composition"]
      if min(_AT.values()) >= 0 else f"a marker is missing: {_AT}",
      True)


# ===========================================================================
# SECTION 6 -- the prompt's own examples obey the prompt's own rules
# ===========================================================================
#
# The JSON template in Section 5 is an EXAMPLE of a conforming response. An
# example that violates a constraint stated twenty lines above it teaches the
# model the violation, and it is the one part of the prompt a reader is most
# likely to copy. Read out of the RENDERED prompt rather than retyped here, so
# the two cannot drift.

print("\n" + "=" * 78)
print("SECTION 6 -- the JSON template's example assessments")
print("=" * 78)


def extract_json_template(prompt_text):
    """The JSON object under 'JSON template:', by brace balance."""
    marker = "JSON template:"
    if marker not in prompt_text:
        return None
    tail = prompt_text[prompt_text.index(marker) + len(marker):]
    start = tail.index("{")
    depth, in_string, escape = 0, False, False
    for i, ch in enumerate(tail[start:], start):
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(tail[start:i + 1])
    return None


# A declared stand-in for the patient record, which PROMPT_VERSION 1.6.0
# moved into the system message. It exists only so the template renders;
# nothing below reads it, and every span this file inspects (Section 5, the
# JSON template, the output contract) is outside the PATIENT RECORD block.
_PROBE_RECORD = "<probe: no patient record>"

_PROMPT = render_system_prompt(mesh_filter_applied=True,
                               mesh_filter_skip_reason="applied",
                               patient_record=_PROBE_RECORD)
try:
    _TEMPLATE = extract_json_template(_PROMPT)
except Exception as _exc:               # noqa: BLE001 - a raise IS an outcome
    _TEMPLATE = f"<raised {type(_exc).__name__}: {_exc}>"

check("6a  the JSON template parses out of the rendered prompt",
      isinstance(_TEMPLATE, dict), True)
_EXAMPLES = (_TEMPLATE.get("evaluations", [])
             if isinstance(_TEMPLATE, dict) else [])
check("6a  non-degeneracy: it holds more than one example trial",
      len(_EXAMPLES) > 1, True)

check("6b  every example's numbers appear in its own criteria rows -- which "
      "is C7 applied to the prompt's own examples",
      [(e.get("nct_id"), unprovenanced_numbers(e.get("assessment", ""), e))
       for e in _EXAMPLES
       if unprovenanced_numbers(e.get("assessment", ""), e)], [])
check("6b  non-degeneracy: at least one example assessment contains a number, "
      "so the check above is not passing over prose",
      any(numeric_tokens(e.get("assessment", "")) for e in _EXAMPLES), True)

check("6c  every example's assessment opens with the opening its verdict "
      "mandates",
      [(e.get("nct_id"), e.get("eligible"), e.get("assessment", "")[:30])
       for e in _EXAMPLES
       if not e.get("assessment", "").startswith(
           {"eligible": ASSESSMENT_ELIGIBLE_OPENING,
            "not_eligible": ASSESSMENT_NOT_ELIGIBLE_OPENING,
            "not_evaluable": ASSESSMENT_NOT_EVALUABLE_OPENING}.get(
                e.get("eligible"), "\0"))], [])
check("6c  ...and none of them carries both mandated openings",
      [e.get("nct_id") for e in _EXAMPLES
       if ASSESSMENT_ELIGIBLE_OPENING in e.get("assessment", "")
       and ASSESSMENT_NOT_ELIGIBLE_OPENING in e.get("assessment", "")], [])

# 6d -- THE AUDITED DEFECT, APPLIED TO THE PROMPT'S OWN EXAMPLES: prose calling
# a field "not documented" while the row beside it quotes a value from the
# record. The eligible example says "ECOG and autoimmune status not documented",
# and both of those rows do carry the canonical patient_value.
#
# THE WEAK FORM OF THIS CHECK -- "the example has at least one canonical row" --
# WAS WRITTEN FIRST AND A REVERT HARNESS SHOWED IT DOES NOT DISCRIMINATE:
# changing the ECOG row to `"0"/met` while the assessment still calls it not
# documented left the autoimmune row canonical, so the check passed on an
# example carrying exactly the defect. This form asks the question per ROW.
#
# It matches by keyword rather than by concept, which is a stated limit: a
# criterion is named in the sentence if one of its own words of four or more
# letters appears there. That is enough to catch the shape and it cannot catch
# a paraphrase -- which is the general problem the composition exists to remove
# and which no check over free prose can solve.

_PROSE_STOPWORDS = frozenset({
    "with", "this", "that", "other", "than", "from", "have", "been", "must",
    "will", "were", "your", "their", "which", "when", "where", "after",
    "before", "within", "prior", "status", "patient", "record", "documented",
    "criterion", "criteria", "trial", "study", "known", "disqualifier",
})


def criterion_keywords(text):
    return {w.casefold() for w in re.findall(r"[A-Za-z]{4,}", text or "")
            if w.casefold() not in _PROSE_STOPWORDS}


def undocumented_prose_violations(assessment, rows):
    """Rows a free-written assessment calls undocumented that are documented.

    The subject is the SENTENCE containing "not documented"; a row is named in
    it if any of its keywords appears there.
    """
    sentences = [s for s in (assessment or "").split(". ")
                 if "not documented" in s.casefold()]
    if not sentences:
        return []
    subject = " ".join(sentences).casefold()
    out = []
    for r in rows:
        canonical = (str(r.get("patient_value", "")).strip().casefold()
                     == UNDOCUMENTED.casefold())
        named = bool(criterion_keywords(r.get("criterion", ""))
                     & criterion_keywords(subject))
        if named and not canonical:
            out.append(r.get("criterion"))
    return out


check("6d  no example calls a row 'not documented' whose patient_value is a "
      "value from the record",
      [(e.get("nct_id"),
        undocumented_prose_violations(e.get("assessment", ""), all_rows(e)))
       for e in _EXAMPLES
       if undocumented_prose_violations(e.get("assessment", ""),
                                        all_rows(e))], [])
check("6d  non-degeneracy: one example does make a 'not documented' claim, "
      "and the checker really did match rows to it",
      (sum(1 for e in _EXAMPLES
           if "not documented" in e.get("assessment", "").casefold()),
       sorted(c for e in _EXAMPLES for c in
              [r.get("criterion") for r in all_rows(e)
               if criterion_keywords(r.get("criterion", ""))
               & criterion_keywords(" ".join(
                   s for s in e.get("assessment", "").split(". ")
                   if "not documented" in s.casefold()))])),
      (1, ["Active autoimmune disease", "ECOG 0-1"]))
check("6d  CONTROL: the checker fires when a named row carries a value from "
      "the record instead of the canonical phrase",
      undocumented_prose_violations(
          "No known disqualifiers. ECOG and autoimmune status not documented.",
          [row("ECOG 0-1", "0", "met"),
           row("Active autoimmune disease", UNDOCUMENTED, "not_evaluable")]),
      ["ECOG 0-1"])

check("6e  the two constraints added at 1.5.0 are in the rendered prompt, and "
      "the composition is described in Section 5",
      ("C7 -- NO INVENTED NUMBERS" in _PROMPT,
       "C8 -- EVIDENCE BOUNDARY" in _PROMPT,
       "composes the stored assessment mechanically" in _PROMPT,
       f'"{UNDOCUMENTED}"' in _PROMPT),
      (True, True, True, True))
check("6e  ...and the three mandated openings this module writes are the "
      "three the prompt states",
      (ASSESSMENT_ELIGIBLE_OPENING in _PROMPT,
       ASSESSMENT_NOT_ELIGIBLE_OPENING in _PROMPT,
       ASSESSMENT_NOT_EVALUABLE_OPENING in _PROMPT),
      (True, True, True))


# ===========================================================================
# SUMMARY
# ===========================================================================

print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
print(f"Runtime: {time.time() - _T_START:.2f}s")

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
Created on Tue Aug 11 09:00:00 2026

@author: ramyalsaffar
"""
