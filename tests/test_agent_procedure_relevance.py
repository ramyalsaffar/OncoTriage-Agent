# Stage 5 Procedure Rendering Relevance Test
###########################################

"""``_classify_procedure_relevance`` decides which procedures reach the Stage 5
summary, and a wrong DROP is invisible: no line, no count, no placeholder.

WHY THE FILTER EXISTS
---------------------
Measured over 100 patients drawn from the 1,000-bundle Synthea corpus with
``random.Random(42).sample(sorted(glob(...)), 100)``, the Procedures section was
40.2% to 62.9% of the summary's characters (median 50.8%) and a median of 100
lines per patient -- dental cleanings, depression questionnaires, referrals and
discharge paperwork. After the filter: median 28.1% and 39 lines, and the whole
record is 33% smaller by the pipeline's own token estimator.

WHY THIS FILE IS STRICTER THAN THE MEDICATION AND CONDITION EQUIVALENTS
-----------------------------------------------------------------------
A "background" condition is still SUMMARIZED into the prompt. A background
procedure is not rendered at all. So the asymmetry the condition classifier
documents is load-bearing here rather than merely prudent, and section 1e tests
it directly: a display matching BOTH a keep keyword and a blacklist keyword must
be KEPT, because the keep layers run first and the default is keep.

WHAT IS ASKED, AND WHY EACH COULD FAIL SILENTLY
-----------------------------------------------
    1. CLASSIFICATION. Every keep family keeps, every blacklist family drops, an
       unknown procedure keeps, and the two protective overrides that exist only
       to beat the blacklist (NYHA class, ICU admission) do beat it.
    2. RENDERING. A mixed patient renders the kept lines and NOTHING about the
       dropped ones -- asserted in both directions, and against the specific
       words a summary line, a count or a placeholder would have to contain.
    3. THE EMPTY ARM. A patient whose every procedure is dropped renders exactly
       what a patient with no procedures renders. That target is read out of the
       shipped renderer first (section 3a) rather than retyped, so the test
       cannot pin a string the code does not produce.
    4. NOTHING ELSE MOVED. Every other section of the summary is byte-identical
       with the filter active and with it neutralised, for the same patient.
    5. THE COUNTERS. Kept and dropped are counted as deltas around one render,
       and no key carries a procedure name or any clinical text.

NEGATIVE CONTROLS ARE INPUTS, NOT PLANTS. Every control here is a different
ARGUMENT to the shipped function or a different patient handed to the shipped
renderer, which is the natural control for a classifier of its own input. The
one exception is the "before" arm of section 4, which rebinds
``_classify_procedure_relevance`` to a keep-everything stand-in inside a
try/finally -- that IS the pre-change renderer for this section, and it needs
neither git history nor an exec, so this file runs in a tree without ``.git``.

NO NETWORK, NO KEYS, NO SPEND, NO CORPUS, NO GIT HISTORY, and not in the
collision matrix: it writes nothing anywhere. Every patient in it is a literal
dict. The MeSH filter is overridden to None -- a documented reachable state --
so no data file is read; the cancer and lab registries are the real ones, which
read no files either.

Run from terminal:
    python tests/test_agent_procedure_relevance.py

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

from oncotriage.agent import deps
from oncotriage.agent import patient as patient_mod
from oncotriage.agent.patient import (
    PROCEDURE_RENDER_COUNTS,
    PROCEDURE_RENDER_DROPPED,
    PROCEDURE_RENDER_KEPT,
    _classify_procedure_relevance,
    _create_patient_summary,
)


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


def classify(display, code=""):
    """The shipped classifier, on one synthetic procedure record."""
    return drive(_classify_procedure_relevance, {"display": display, "code": code,
                                                 "date": "2024-01-01",
                                                 "status": "completed"})


def section(text, heading, next_heading):
    """One named block of a rendered summary, or a named absence."""
    try:
        start = text.index(heading)
        return text[start:text.index(next_heading, start)]
    except (ValueError, AttributeError):
        return f"<section {heading!r} not found>"


def procedures_of(text):
    return section(text, "\nProcedures:\n", "\nRelevant Lab Values")


#------------------------------------------------------------------------------


# ===========================================================================
# THE PATIENTS AND THE DEPENDENCY SEAM
# ===========================================================================
#
# The MeSH filter is overridden to None, which the renderer already handles and
# which is what keeps this file free of the sibling data tree. The cancer and
# lab registries are the REAL ones on purpose: the lab registry is what
# deduplicates procedures by display and picks the most recent date per type,
# and this filter runs on its output, so a stand-in there would test the filter
# against a shape production never produces.

deps.set_override(deps.MESH_FILTER, None)


def _procedure(display, code=""):
    return {"display": display, "code": code, "date": "2024-03-01",
            "status": "completed"}


def _patient(patient_id, procedures):
    return {
        "patient_id": patient_id,
        "demographics": {"age": 63, "sex": "female", "birth_date": "1962-04-03",
                         "race": "White",
                         "ethnicity": "Not Hispanic or Latino"},
        "conditions": [{"code": "254837009",
                        "system": "http://snomed.info/sct",
                        "display": "Malignant neoplasm of breast",
                        "clinical_status": "active",
                        "verification_status": "confirmed",
                        "onset": "2020-01-01"}],
        "observations": [{"code": "718-7", "display": "Hemoglobin",
                          "value": 11.2, "unit": "g/dL",
                          "date": "2024-02-01"}],
        "medications": [{"display": "Anastrozole 1 MG Oral Tablet",
                         "status": "active", "date": "2023-01-01"}],
        "procedures": list(procedures),
        "allergies": [{"display": "Penicillin", "category": "medication",
                       "criticality": "high"}],
        "cancer_stage_observations": [], "cancer_metastasis_observations": [],
        "cancer_genomic_variants": [],
        "ecog_performance_status": {"value": 1, "date": "2024-01-01",
                                    "value_shape": "valueInteger",
                                    "observation_count": 1,
                                    "selection_path": "most_recent"},
    }


# Real corpus displays, spelled exactly as the Synthea bundles spell them.
KEEP_CASES = [
    ("oncology diagnostic",   "biopsy of breast (procedure)", "122548005"),
    ("oncology diagnostic",   "sentinel lymph node biopsy (procedure)", "396487001"),
    ("oncology diagnostic",   "colonoscopy (procedure)", "73761001"),
    ("oncology diagnostic",   "mammography (procedure)", "71651007"),
    ("oncology therapeutic",  "chemotherapy (procedure)", "367336001"),
    ("oncology therapeutic",  "external beam radiation therapy procedure (procedure)", "33195004"),
    ("oncology therapeutic",  "interstitial brachytherapy (procedure)", "113120007"),
    ("marrow transplant",     "autologous bone marrow transplant (procedure)", "58776007"),
    ("organ transplant",      "transplant of kidney (procedure)", "70536003"),
    ("transfusion",           "transfusion of packed red blood cells (procedure)", "71493000"),
    ("transfusion",           "platelet transfusion (procedure)", "12719002"),
    ("major surgery",         "partial resection of colon (procedure)", "43075005"),
    ("major surgery",         "coronary artery bypass grafting (procedure)", "232717009"),
    ("major surgery",         "median sternotomy (procedure)", "359672006"),
    ("line placement",        "insertion of catheter into artery (procedure)", "392247006"),
    ("line placement",        "pulmonary catheterization with swan-ganz catheter (procedure)", "65677008"),
    ("tumour-site imaging",   "computed tomography of chest, abdomen and pelvis (procedure)", "418023006"),
    ("tumour-site imaging",   "magnetic resonance imaging of breast (procedure)", "241615005"),
    ("tumour-site imaging",   "ultrasonography of bilateral breasts (procedure)", "1571000087109"),
]

DROP_CASES = [
    ("dental",         "dental care (regime/therapy)", "225362009"),
    ("dental",         "removal of supragingival plaque and calculus from all teeth using dental instrument (procedure)", "1260009003"),
    ("dental",         "examination of gingivae (procedure)", "274788003"),
    ("dental",         "simple extraction of tooth (procedure)", "173291009"),
    ("dental",         "gingivectomy or gingivoplasty, per tooth (procedure)", "64544008"),
    ("immunisation",   "administration of vaccine product containing only bordetella pertussis and clostridium tetani and corynebacterium diphtheriae antigens (procedure)", "399014008"),
    ("immunisation",   "passive immunization (procedure)", "51116004"),
    ("cast / splint",  "bone immobilization (procedure)", "274474001"),
    ("routine screening", "depression screening (procedure)", "171207006"),
    ("routine screening", "screening for domestic abuse (procedure)", "866148006"),
    ("routine screening", "assessment using morse fall scale (procedure)", "762993000"),
    ("routine screening", "assessment of anxiety (procedure)", "710841007"),
    ("background care",   "medication reconciliation (procedure)", "430193006"),
    ("background care",   "patient discharge (procedure)", "58000006"),
    ("background care",   "referral to cardiology service (procedure)", "183519002"),
    ("background care",   "history and physical examination (procedure)", "63332003"),
    ("background care",   "taking patient vital signs (procedure)", "61746007"),
    ("background care",   "urine specimen collection (procedure)", "57617002"),
]


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 1 -- classification
# ===========================================================================

print("=" * 70)
print("SECTION 1 -- keep families keep, blacklist families drop, unknown keeps")
print("=" * 70)

print("\n  1a. every keep family is kept")
for _family, _display, _code in KEEP_CASES:
    check(f"KEEP [{_family}] {_display[:52]}", classify(_display, _code), "relevant")

print("\n  1b. every blacklist family is dropped")
for _family, _display, _code in DROP_CASES:
    check(f"DROP [{_family}] {_display[:52]}", classify(_display, _code), "background")

print("\n  1c. an unmatched procedure is KEPT -- unknown means keep")
#
# THE SAFETY RULE, AND THE ONE ASSERTION THAT MAKES THE OTHERS SAFE. These are
# real corpus displays that no layer names, plus two invented ones standing in
# for a procedure vocabulary this corpus does not contain.
for _display in (
    "hospice care (regime/therapy)",
    "renal dialysis (procedure)",
    "suture open wound (procedure)",
    "echocardiography (procedure)",
    "intubation (procedure)",
    "human immunodeficiency virus antigen test (procedure)",
    "hepatitis b surface antigen measurement (procedure)",
    "insertion of intrauterine contraceptive device (procedure)",
    "cardiac ablation by an approach nobody has enumerated yet",
    "hyperthermic intraperitoneal perfusion",
):
    check(f"UNKNOWN -> keep: {_display[:56]}", classify(_display), "relevant")

print("\n  1d. the code layer decides before the text does")
#
# A KEEP CODE ON A DISPLAY THE BLACKLIST WOULD OTHERWISE CATCH. Real EHR data
# carries local display strings against standard codes, so the code has to be
# able to save a procedure whose text says something else.
check("a keep SNOMED code beats a blacklist word in the display",
      classify("referral note: chemo port dental clearance", "367336001"),
      "relevant")
check("...and the same display with no code is dropped (so 1d is not vacuous)",
      classify("referral note: chemo port dental clearance", ""), "background")
check("an unknown code does not by itself keep anything",
      classify("dental care (regime/therapy)", "999999999"), "background")

print("\n  1e. THE ASYMMETRY -- keep layers run first, so keep wins a tie")
check("a display matching a keep word AND a blacklist word is KEPT",
      classify("biopsy of tooth (procedure)"), "relevant")
check("the same display without the keep word drops (so the tie is real)",
      classify("extraction of tooth (procedure)"), "background")
check("a cancer word protects a routine-screening phrase",
      classify("lung cancer screening with low dose computed tomography"),
      "relevant")
check("the identical phrase without the cancer word drops",
      classify("screening with low dose thermometry"), "background")

print("\n  1f. the two protective overrides that exist only to beat the blacklist")
check("NYHA class is kept though 'assessment using' is blacklisted",
      classify("assessment using new york heart association classification (procedure)",
               "762998009"), "relevant")
check("...and a sibling 'assessment using' instrument still drops",
      classify("assessment using health assessment questionnaire (procedure)",
               "445988008"), "background")
check("ICU admission is kept though 'admission to' is blacklisted",
      classify("admission to intensive care unit (procedure)", "305351004"),
      "relevant")
check("...and a sibling admission still drops",
      classify("admission to ward (procedure)", "305342007"), "background")

print("\n  1g. degenerate input never raises and never drops")
check("no display at all -> keep", classify(""), "relevant")
check("display None -> keep", drive(_classify_procedure_relevance,
                                    {"display": None, "code": None}), "relevant")
check("empty dict -> keep", drive(_classify_procedure_relevance, {}), "relevant")

print("\n  1h. the vocabulary carries no accidental substring keeps")
#
# THE TWO DEFECTS THE CORPUS ENUMERATION CAUGHT, pinned so they cannot come
# back: "port" matched "dental consultation and REPORT" in 100 of 100 patients,
# and "surgical procedure" matched "DENTAL SURGICAL PROCEDURE" in 96 of 100.
check("'dental consultation and report' drops (the 'port' substring is gone)",
      classify("dental consultation and report (procedure)", "34043003"),
      "background")
check("'dental surgical procedure' drops (the broad keep phrase is gone)",
      classify("dental surgical procedure (procedure)", "81733005"), "background")


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 2 -- rendering: only the kept lines, and no trace of the rest
# ===========================================================================

print()
print("=" * 70)
print("SECTION 2 -- a mixed patient renders the kept procedures and nothing else")
print("=" * 70)

_MIXED = _patient("procedure-mixed", [
    _procedure("Biopsy of breast", "122548005"),
    _procedure("Chemotherapy", "367336001"),
    _procedure("Transfusion of packed red blood cells", "71493000"),
    _procedure("Dental care", "225362009"),
    _procedure("Depression screening", "171207006"),
    _procedure("Medication reconciliation", "430193006"),
    _procedure("Referral to cardiology service", "183519002"),
    _procedure("Hospice care", "385763009"),          # unknown -> kept
])

_mixed_text = drive(_create_patient_summary, _MIXED)
_mixed_procs = procedures_of(_mixed_text)

print("\n  2a. every kept procedure is rendered")
for _name in ("Biopsy of breast", "Chemotherapy",
              "Transfusion of packed red blood cells", "Hospice care"):
    check(f"rendered: {_name}", _name in _mixed_procs, True)

print("\n  2b. no dropped procedure appears anywhere in the WHOLE summary")
#
# The whole summary, not just the section: a dropped procedure leaking into
# another block would be the same failure in a different place.
for _name in ("Dental care", "Depression screening", "Medication reconciliation",
              "Referral to cardiology service"):
    check(f"absent from the section: {_name}", _name in _mixed_procs, False)
    check(f"absent from the whole summary: {_name}", _name in _mixed_text, False)

print("\n  2c. the section says nothing ABOUT the dropping")
check("the line count is exactly the kept count",
      _mixed_procs.count("\n- "), 4)
for _word in ("dropped", "omitted", "withheld", "excluded", "filtered",
              "not shown", "others", "and 4 more", "irrelevant", "background",
              "suppressed", "hidden", "truncated"):
    check(f"no placeholder or count wording: {_word!r}",
          _word in _mixed_procs.lower(), False)

print("\n  2d. NON-DEGENERACY -- the same patient with the filter neutralised")
#
# Without this, 2b passes for a renderer that emitted no Procedures section at
# all, or for a patient whose procedures never reached the renderer.


def render_unfiltered(patient):
    """The pre-change renderer for this section: keep everything.

    A rebind of the module attribute the renderer resolves, inside a
    try/finally. No exec, no git blob -- so this file does not abort in a tree
    without ``.git``, which three files in this suite still do.
    """
    real = patient_mod._classify_procedure_relevance
    patient_mod._classify_procedure_relevance = lambda proc: "relevant"
    try:
        return _create_patient_summary(patient)
    finally:
        patient_mod._classify_procedure_relevance = real


_mixed_unfiltered = drive(render_unfiltered, _MIXED)
_mixed_unfiltered_procs = procedures_of(_mixed_unfiltered)
check("unfiltered, all 8 procedures render",
      _mixed_unfiltered_procs.count("\n- "), 8)
for _name in ("Dental care", "Depression screening", "Medication reconciliation",
              "Referral to cardiology service"):
    check(f"unfiltered, {_name} IS rendered", _name in _mixed_unfiltered_procs, True)
check("so the filter is what removed them, and 2b can fail",
      len(_mixed_procs) < len(_mixed_unfiltered_procs), True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 3 -- a patient whose every procedure is dropped
# ===========================================================================

print()
print("=" * 70)
print("SECTION 3 -- all-dropped renders exactly what no-procedures renders")
print("=" * 70)

print("\n  3a. what the shipped renderer produces for an empty procedure list")
#
# READ OUT OF THE CODE, NOT RETYPED. The target of 3b is whatever this is; a
# literal here would pin a string the renderer might not produce and the test
# would then be about this file rather than about the code.
_EMPTY = _patient("procedure-none", [])
_empty_text = drive(_create_patient_summary, _EMPTY)
_empty_procs = procedures_of(_empty_text)
print(f"      (empty procedure section is {_empty_procs!r})")
check("the empty section was found at all (non-degenerate)",
      _empty_procs.startswith("\nProcedures:\n"), True)
check("it renders exactly one line", _empty_procs.count("\n- "), 1)

print("\n  3b. an all-dropped patient renders the identical section")
_ALL_DROPPED = _patient("procedure-all-dropped", [
    _procedure("Dental care", "225362009"),
    _procedure("Depression screening", "171207006"),
    _procedure("Medication reconciliation", "430193006"),
    _procedure("Patient discharge", "58000006"),
    _procedure("Taking patient vital signs", "61746007"),
])
_all_dropped_text = drive(_create_patient_summary, _ALL_DROPPED)
_all_dropped_procs = procedures_of(_all_dropped_text)
check("byte-identical to the no-procedures section",
      _all_dropped_procs, _empty_procs)
check("and it carries none of the dropped names",
      any(n in _all_dropped_text for n in
          ("Dental care", "Depression screening", "Patient discharge")), False)

print("\n  3c. NON-DEGENERACY -- one kept procedure changes that section")
_ONE_KEPT = _patient("procedure-one-kept", [
    _procedure("Dental care", "225362009"),
    _procedure("Chemotherapy", "367336001"),
])
_one_kept_procs = procedures_of(drive(_create_patient_summary, _ONE_KEPT))
check("a kept procedure makes the section differ from the empty one",
      _one_kept_procs == _empty_procs, False)
check("and it is the kept one that is in it",
      "Chemotherapy" in _one_kept_procs, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 4 -- every other section is byte-identical
# ===========================================================================

print()
print("=" * 70)
print("SECTION 4 -- the change touches the Procedures section and nothing else")
print("=" * 70)


def without_procedures(text):
    """The whole summary minus its Procedures block."""
    block = procedures_of(text)
    return text.replace(block, "\n<PROCEDURES REMOVED>\n", 1)


_before = without_procedures(_mixed_unfiltered)
_after = without_procedures(_mixed_text)

check("everything outside the Procedures block is byte-identical",
      _before, _after)
check("the comparison is over a real summary, not an empty string "
      "(non-degenerate)", len(_after) > 400, True)
check("...and the Procedures blocks genuinely differ (non-degenerate)",
      _mixed_procs == _mixed_unfiltered_procs, False)

print("\n  4b. NON-DEGENERACY -- the comparison detects a real difference")
#
# Without this, "byte-identical" is satisfied by a comparison that would report
# two different patients as equal.
# The decoy differs in a RENDERED field outside the Procedures block -- an
# extra allergy -- because the first version of this control differed only in
# patient_id and the procedure list, neither of which the summary renders
# outside that block, so it compared EQUAL and reported that the comparison
# could not fail. A control that does not discriminate is worse than none.
_DECOY = _patient("procedure-decoy", [_procedure("Chemotherapy", "367336001")])
_DECOY["allergies"] = _DECOY["allergies"] + [
    {"display": "Latex", "category": "environment", "criticality": "low"}]
_decoy = without_procedures(drive(_create_patient_summary, _DECOY))
check("a DIFFERENT patient does not compare equal",
      _decoy == _after, False)
check("...and it differs OUTSIDE the procedures block (non-degenerate)",
      "Latex" in _decoy, True)

print("\n  4c. the sections a reader would check by name are all present")
# The headings as the renderer spells them, read off the shipped summary rather
# than guessed: the first version of this list asked for "Demographics", which
# this renderer does not emit.
for _heading in ("Performance Status:", "Conditions:", "Medications:",
                 "Allergies:", "Procedures:", "Relevant Lab Values (most recent):",
                 "Metastasis & Nodal Status:", "Genomic & Molecular Biomarkers:"):
    check(f"heading present: {_heading}", _heading in _mixed_text, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 5 -- the counters
# ===========================================================================

print()
print("=" * 70)
print("SECTION 5 -- kept and dropped are counted, and carry no clinical text")
print("=" * 70)

print("\n  5a. one render moves both counters by the right amounts")
#
# DELTAS AROUND ONE RENDER, never absolute values: the counter is a
# process-lifetime tally and sections 2 to 4 have already rendered.
_kept_before = PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT]
_dropped_before = PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_DROPPED]
drive(_create_patient_summary, _MIXED)
_kept_delta = PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT] - _kept_before
_dropped_delta = (PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_DROPPED]
                  - _dropped_before)
check("kept moved by the number of rendered procedures", _kept_delta, 4)
check("dropped moved by the number withheld", _dropped_delta, 4)
check("and the two sum to what the renderer was handed", _kept_delta + _dropped_delta, 8)

print("\n  5b. a render with nothing to drop moves only the kept counter")
_kept_before = PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT]
_dropped_before = PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_DROPPED]
drive(_create_patient_summary, _patient("procedure-clean", [
    _procedure("Chemotherapy", "367336001"),
    _procedure("Biopsy of breast", "122548005"),
]))
check("kept +2", PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT] - _kept_before, 2)
check("dropped +0",
      PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_DROPPED] - _dropped_before, 0)

print("\n  5c. a patient with no procedures moves neither")
_kept_before = PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT]
_dropped_before = PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_DROPPED]
drive(_create_patient_summary, _EMPTY)
check("kept +0", PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT] - _kept_before, 0)
check("dropped +0",
      PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_DROPPED] - _dropped_before, 0)

print("\n  5d. the counter carries counts only -- never a name, never a date")
_keys = sorted(PROCEDURE_RENDER_COUNTS.keys())
check("the key set is exactly the two documented keys",
      _keys, sorted({PROCEDURE_RENDER_KEPT, PROCEDURE_RENDER_DROPPED}))
check("the counters are non-degenerate by now (something was counted)",
      PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT] > 0
      and PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_DROPPED] > 0, True)
check("every value is an int", sorted({type(v).__name__
                                       for v in PROCEDURE_RENDER_COUNTS.values()}),
      ["int"])
# The names this file rendered and withheld, none of which may be in a key.
for _name in ("dental", "depression", "biopsy", "chemotherapy", "2024",
              "procedure-mixed"):
    check(f"no key contains {_name!r}",
          any(_name in k.lower() for k in _keys), False)

print("\n  5e. NON-DEGENERACY -- the delta method can observe a change")
check("the kept counter is strictly larger than at 5a's start",
      PROCEDURE_RENDER_COUNTS[PROCEDURE_RENDER_KEPT] > 4, True)


#------------------------------------------------------------------------------


# ===========================================================================
# SECTION 6 -- the classifier and the renderer agree
# ===========================================================================

print()
print("=" * 70)
print("SECTION 6 -- what the classifier says is what the renderer does")
print("=" * 70)
#
# SECTIONS 1 AND 2 COULD BOTH PASS WITH THE RENDERER WIRED TO A DIFFERENT
# CLASSIFIER, or to none at all on some path. This closes that by driving both
# over the same list and comparing the two answers.

_ALL_CASES = ([(d, c) for _f, d, c in KEEP_CASES]
              + [(d, c) for _f, d, c in DROP_CASES])
_expected_kept = [d for d, c in _ALL_CASES if classify(d, c) == "relevant"]
_rendered = procedures_of(drive(_create_patient_summary, _patient(
    "procedure-agreement", [_procedure(d, c) for d, c in _ALL_CASES])))

check("the classifier keeps the whole keep list and nothing else",
      len(_expected_kept), len(KEEP_CASES))
check("the renderer rendered exactly that many lines",
      _rendered.count("\n- "), len(_expected_kept))
check("and every one of them by name",
      sorted(d for d in _expected_kept if d not in _rendered), [])
check("no dropped display is in the rendered text",
      sorted(d for d, c in _ALL_CASES
             if classify(d, c) == "background" and d in _rendered), [])


#------------------------------------------------------------------------------


# ===========================================================================
# CLEANUP
# ===========================================================================

deps.clear_override(deps.MESH_FILTER)
check("the MeSH override this file installed was cleared",
      deps.peek(deps.MESH_FILTER) is deps.UNSET
      or not deps.is_resolved(deps.MESH_FILTER), True)


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
