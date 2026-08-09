# Stage Extraction: the AJCC M Category
######################################

"""
AJCC Clinical M Category Test

``extract_patient_stage()`` in oncotriage/extraction/stage.py gained a tier
that reads the AJCC clinical M category Observation (LOINC 21907-1) and maps
cM1 -- and only cM1 -- to stage IV. oncotriage/fhir/parser.py had been routing
that Observation into ``cancer_metastasis_observations`` since the metastasis
item, where it reached the patient hash and the Stage 5 prompt and NOTHING
that decides a stage, and a comment beside the routing recorded the deferral.

THE ONE-DIRECTION RULE IS THE WHOLE POINT AND IT IS WHAT MOST OF THIS FILE
CHECKS. cM1 is distant metastasis and is stage IV by definition. cM0 is a
POSITIVE statement that there is NO distant metastasis and maps to nothing at
all -- a patient can be cM0 and stage IIIC. Measured over all 1,000 corpus
bundles on 2026-08-07 (295 observations, one per patient, no patient carrying
two): 290 cM0 to 5 cM1. So a rule that read cM0 as an early stage would reach
58 patients wrongly for every one it reached rightly, and it would reach them
in the damaging direction -- a stage floor low enough to drop the
advanced-disease trials they qualify for.

WHAT THE CORPUS SAYS ABOUT THE CHANGE, measured rather than predicted: ZERO
patients change stage, because all five cM1 patients ALSO carry a stage GROUP
Observation reading "Stage 4 (qualifier value)", and the tier above this one
already answered for them. A zero is only worth anything if the measurement
could have said otherwise, so section 3 withholds the stage-group list from
those same five real patients and requires the M tier to reach 4 on its own --
three of the five have NO stage anywhere else in their record.

WHY THIS IS A NEW FILE. The natural home would be
``test_registries_cancer_codes_and_stage_extraction.py``, which owns
``extract_patient_stage``. This file is separate because its subject is not the
extractor alone: the tier is inert unless the CALL SITE passes the new
argument, so sections 4 and 5 drive Stage 4's real node and read
oncotriage/fixtures/capture.py by AST, and section 6 walks the whole package
for a duplicated LOINC literal. Folding a cross-module wiring proof into a file
whose docstring pins it to registries and stage text would broaden it past what
its name claims. Same reasoning pass 20f-1 used for its four new files.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every fixture in
here is a literal dict. It is NOT in tests/run_serial_tests.py's collision
matrix: it writes nothing anywhere -- every plant goes into an in-memory copy,
hashed before and after -- and the three source files it reads are written by
neither of the suite's two writers.

    python tests/test_extraction_stage_m_category.py
"""

import ast
import hashlib
import os
import sys
import types

try:
    import oncotriage                                          # noqa: F401
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

from oncotriage.agent import deps
from oncotriage.agent.filtering import node_rule_based_filter
from oncotriage.constants import LOINC_AJCC_CLINICAL_M
from oncotriage.extraction import stage as _stage_module
from oncotriage.extraction.stage import (
    M_CATEGORY_UNREADABLE,
    _stage_from_m_category,
    extract_patient_stage,
    is_stage_mismatch,
)
from oncotriage.fixtures import capture as _capture_module


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
_STAGE_SRC = os.path.abspath(_stage_module.__file__)
_CAPTURE_SRC = os.path.abspath(_capture_module.__file__)
_PKG_DIR = os.path.dirname(os.path.abspath(oncotriage.__file__))


def _sha256_of(path):
    """sha256 of a source file, for the restore assertions."""
    return hashlib.sha256(
        open(path, encoding="utf-8").read().encode()).hexdigest()


# Taken NOW, before any plant runs. The first version of the check at the
# bottom of Test 7 hashed the file twice in one expression and compared the two
# — a tautology that passes however corrupt the file is, which is the vacuous
# assertion this project's rules exist to forbid. These are the baselines it
# compares against instead.
_STAGE_SHA_BEFORE = _sha256_of(_STAGE_SRC)
_FILTERING_PATH = os.path.join(_PKG_DIR, "agent", "filtering.py")
_FILTERING_SHA_BEFORE = _sha256_of(_FILTERING_PATH)


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(path, name, subs):
    """Exec an in-memory COPY of `path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is
    RECORDED as a failure instead of aborting the run and hiding every check
    below it. A control that takes the process down is not a control; it is an
    outage that happens to be red.

    The file on disk is hashed before and after and asserted byte-identical,
    because "mutates a COPY" is only true for as long as it stays true.
    """
    source = open(path, encoding="utf-8").read()
    before = hashlib.sha256(source.encode()).hexdigest()
    try:
        for old, new in subs:
            if old not in source:
                raise _PlantFailed(f"plant target absent: {old[:70]!r}...")
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


def _control(label, path, subs, probe, expected):
    """Plant, probe the planted module, record. A raise IS an outcome."""
    _CONTROL_SEQ[0] += 1
    try:
        module = _plant(path, f"planted_{_CONTROL_SEQ[0]}", subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    try:
        actual = probe(module)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        actual = f"raised {type(exc).__name__}"
    check(label, actual, expected)


# --- observation and trial builders ----------------------------------------
#
# Shaped exactly as oncotriage/fhir/parser.py:_parse_observation() emits them,
# with the metastasis_category field the routing adds. The two SNOMED display
# strings are the ones measured in the corpus, verbatim.

_CM0_DISPLAY = "American Joint Committee on Cancer cM0 (qualifier value)"
_CM1_DISPLAY = "American Joint Committee on Cancer cM1 (qualifier value)"


def met_obs(value, code=LOINC_AJCC_CLINICAL_M, category="M",
            date="2020-01-01T00:00:00-00:00"):
    """One entry of patient_data['cancer_metastasis_observations']."""
    return {"code": code,
            "display": "Distant metastases.clinical [Class] Cancer",
            "value": value,
            "unit": None,
            "date": date,
            "metastasis_category": category}


def stage_obs(display, date="2020-01-01T00:00:00-00:00"):
    """One entry of patient_data['cancer_stage_observations']."""
    return {"stage_display": display, "stage_code": "", "date": date,
            "loinc": "21908-9"}


def trial(nct="NCT00000001", min_stage=None, max_stage=None):
    """One reranked-trial envelope carrying stage bounds."""
    return {"trial": {"nct_id": nct,
                      "title": "a trial",
                      "histology_tags": [],
                      "structured_eligibility": {
                          "min_stage": min_stage,
                          "max_stage": max_stage,
                          "accepts_metastatic": None},
                      "eligibility": {"min_age": "18 Years",
                                      "max_age": "99 Years",
                                      "sex": "ALL",
                                      "inclusion_criteria": ""}},
            # Identical scores so the quality gate cannot move a count.
            "rerank_score": 1.0,
            "rerank_score_raw": 1.0,
            "medcpt_score_max": 100.0}


# No MeSH lookup file is read and nothing is built: the filter is overridden to
# None, a REACHABLE state the node already handles and reports.
deps.set_override(deps.MESH_FILTER, None)


def run_stage4(trials, conditions=(), stage_observations=(),
               metastasis_observations=()):
    """Drive the REAL Stage 4 node, returning its dict or a RAISE MARKER.

    A regression that makes the node raise must be a recorded failure rather
    than a traceback that hides every check below it -- the defect the
    promotion pass had to fix in its own harness.
    """
    state = {"patient_data": {
                 "demographics": {"age": 50, "sex": "female"},
                 "conditions": list(conditions),
                 "cancer_stage_observations": list(stage_observations),
                 "cancer_metastasis_observations": list(metastasis_observations)},
             "reranked_trials": list(trials),
             "ablation_flags": {},
             "patient_trees": set(),
             "stage_timings": {}}
    try:
        return node_rule_based_filter(state)
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


def _non_docstring_str_constants(tree):
    """Every string literal in `tree` that is NOT a docstring.

    Docstrings are excluded so that the prose ARGUING for a check does not fail
    it -- the same allowance test_package_invariants.py check 2f(ii) makes for
    the cross-encoder checkpoint. A `#` comment is invisible to ast already.
    """
    doc_ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef,
                                 ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if (body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)):
            doc_ids.add(id(body[0].value))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in doc_ids]


def _package_py_files():
    """Every .py under oncotriage/, to any depth."""
    out = []
    for dirpath, dirnames, filenames in os.walk(_PKG_DIR):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return sorted(out)


# ===========================================================================
# TEST 1 — THE M CATEGORY MAPS IN ONE DIRECTION ONLY
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 1: cM1 is stage IV; cM0 and cMX are not a stage at all")
print("=" * 70)

check("the corpus cM1 display resolves to 4",
      _stage_from_m_category([met_obs(_CM1_DISPLAY)]), 4)
check("THE CONTROL THAT MATTERS: the corpus cM0 display resolves to None",
      _stage_from_m_category([met_obs(_CM0_DISPLAY)]), None)
check("...and it is None rather than 0, which would be stage 0 (in situ)",
      _stage_from_m_category([met_obs(_CM0_DISPLAY)]) is None, True)

check("a bare 'cM1' resolves to 4", _stage_from_m_category([met_obs("cM1")]), 4)
check("a bare 'cM0' resolves to None",
      _stage_from_m_category([met_obs("cM0")]), None)
check("'M1' with no determination prefix resolves to 4",
      _stage_from_m_category([met_obs("M1")]), 4)
check("'M0' with no determination prefix resolves to None",
      _stage_from_m_category([met_obs("M0")]), None)

check("cMX -- 'cannot be assessed' -- resolves to None, not to a stage",
      _stage_from_m_category([met_obs("cMX")]), None)
check("...and lowercase 'cmx' likewise",
      _stage_from_m_category([met_obs("cmx")]), None)

for _sub in ("a", "b", "c", "d"):
    check(f"cM1{_sub} is distant metastasis and resolves to 4",
          _stage_from_m_category([met_obs(f"cM1{_sub}")]), 4)
    check(f"cM0{_sub} does not exist as staging evidence and resolves to None",
          _stage_from_m_category([met_obs(f"cM0{_sub}")]), None)

for _prefix in ("c", "p", "y", "r", "yc", "yp", "rc", "rp", ""):
    check(f"the AJCC determination prefix {_prefix!r} is admitted on M1",
          _stage_from_m_category([met_obs(f"{_prefix}M1")]), 4)
    check(f"...and does not turn M0 into a stage with prefix {_prefix!r}",
          _stage_from_m_category([met_obs(f"{_prefix}M0")]), None)

check("case is not load-bearing: 'cm1' resolves to 4",
      _stage_from_m_category([met_obs("cm1")]), 4)
check("...and 'cm0' still resolves to None",
      _stage_from_m_category([met_obs("cm0")]), None)

check("no observations at all resolves to None",
      _stage_from_m_category([]), None)
check("None for the whole list resolves to None",
      _stage_from_m_category(None), None)

# ANY cM1 answers, whatever sits beside it. Argued at the function: AJCC does
# not de-stage a patient who has had distant metastasis, so a later cM0 records
# a response rather than a correction.
check("cM1 anywhere in the list answers, even with a later cM0 beside it",
      _stage_from_m_category([met_obs(_CM1_DISPLAY, date="2020-01-01"),
                              met_obs(_CM0_DISPLAY, date="2024-01-01")]), 4)
check("...and in the other order",
      _stage_from_m_category([met_obs(_CM0_DISPLAY, date="2024-01-01"),
                              met_obs(_CM1_DISPLAY, date="2020-01-01")]), 4)
check("two cM0s and nothing else is still None",
      _stage_from_m_category([met_obs(_CM0_DISPLAY), met_obs(_CM0_DISPLAY)]),
      None)


# ===========================================================================
# TEST 2 — WHAT THE RULE REFUSES TO READ
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 2: only LOINC 21907-1, and unreadable values are counted")
print("=" * 70)

check("the shared LOINC constant is 21907-1", LOINC_AJCC_CLINICAL_M, "21907-1")

# 44667-4 travels in the SAME list with metastasis_category "M". Keying on that
# field instead of the code would pull it in -- and its 290 corpus values are
# all "None (qualifier value)", a site vocabulary, not an M category.
check("44667-4 is ignored even though it carries category 'M'",
      _stage_from_m_category([met_obs("cM1", code="44667-4", category="M")]),
      None)
check("...and an N-axis nodal count is ignored",
      _stage_from_m_category([met_obs("3", code="85343-2", category="N")]),
      None)
check("a 21907-1 sitting AFTER ignored codes is still read",
      _stage_from_m_category([met_obs("cM1", code="44667-4", category="M"),
                              met_obs("2", code="85344-0", category="N"),
                              met_obs(_CM1_DISPLAY)]), 4)
check("an observation with no code at all is ignored",
      _stage_from_m_category([{"value": "cM1"}]), None)
check("...and one whose code is None is ignored",
      _stage_from_m_category([met_obs("cM1", code=None)]), None)
check("surrounding whitespace on the code does not hide the observation",
      _stage_from_m_category([met_obs("cM1", code="  21907-1  ")]), 4)

M_CATEGORY_UNREADABLE.clear()
check("an unreadable value contributes no stage",
      _stage_from_m_category([met_obs("no category recorded")]), None)
check("...and IS COUNTED, keyed by the text that failed",
      dict(M_CATEGORY_UNREADABLE), {"no category recorded": 1})

M_CATEGORY_UNREADABLE.clear()
check("a None value is counted rather than silently skipped",
      _stage_from_m_category([met_obs(None)]), None)
check("...under the empty-string key", dict(M_CATEGORY_UNREADABLE), {"": 1})

M_CATEGORY_UNREADABLE.clear()
_LONG = "x" * 200
_stage_from_m_category([met_obs(_LONG)])
_key = next(iter(M_CATEGORY_UNREADABLE))
check("a pathological value cannot grow the counter key without bound",
      len(_key) <= _stage_module._M_KEY_MAX_LEN + 3, True)
check("...and the key is truncated rather than dropped", _key.endswith("..."),
      True)

M_CATEGORY_UNREADABLE.clear()
_stage_from_m_category([met_obs(_CM0_DISPLAY), met_obs(_CM1_DISPLAY)])
check("cM0 and cM1 are READ, so neither is counted as unreadable",
      dict(M_CATEGORY_UNREADABLE), {})
M_CATEGORY_UNREADABLE.clear()
_stage_from_m_category([met_obs("cMX")])
check("...and cMX is read too -- 'cannot be assessed' is an answer",
      dict(M_CATEGORY_UNREADABLE), {})

# A word ending in a letter+digit that is not an M category must not match.
M_CATEGORY_UNREADABLE.clear()
for _junk in ("Tumour size 1 cm", "pending", "N1", "T1M", "AJCC"):
    check(f"{_junk!r} yields no stage",
          _stage_from_m_category([met_obs(_junk)]), None)
check("...and all five were counted as unreadable rather than passed over",
      sum(M_CATEGORY_UNREADABLE.values()), 5)
M_CATEGORY_UNREADABLE.clear()


# ===========================================================================
# TEST 3 — TIER ORDER
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 3: the M tier sits below the stage group and above the text tiers")
print("=" * 70)

_BREAST = [{"display": "Malignant neoplasm of breast (disorder)"}]

check("with nothing but cM1, the patient is stage 4",
      extract_patient_stage(_BREAST, cancer_metastasis_observations=[
          met_obs(_CM1_DISPLAY)]), 4)
check("with nothing but cM0, the patient has no stage",
      extract_patient_stage(_BREAST, cancer_metastasis_observations=[
          met_obs(_CM0_DISPLAY)]), None)

check("a stage GROUP outranks the M tier -- IIIA wins over cM1",
      extract_patient_stage(_BREAST,
                            cancer_stage_observations=[stage_obs("Stage IIIA")],
                            cancer_metastasis_observations=[
                                met_obs(_CM1_DISPLAY)]), 3)
check("...and a stage group agreeing at IV still gives 4",
      extract_patient_stage(_BREAST,
                            cancer_stage_observations=[
                                stage_obs("Stage 4 (qualifier value)")],
                            cancer_metastasis_observations=[
                                met_obs(_CM1_DISPLAY)]), 4)
check("a stage group that resolves to NOTHING falls through to the M tier",
      extract_patient_stage(_BREAST,
                            cancer_stage_observations=[stage_obs("unknown")],
                            cancer_metastasis_observations=[
                                met_obs(_CM1_DISPLAY)]), 4)

check("the M tier outranks a condition display saying Stage II",
      extract_patient_stage(
          [{"display": "Carcinoma of breast, Stage 2"}],
          cancer_metastasis_observations=[met_obs(_CM1_DISPLAY)]), 4)
check("...and outranks the 'metastatic' keyword tier, which agrees anyway",
      extract_patient_stage(
          [{"display": "Metastatic malignant neoplasm to bone"}],
          cancer_metastasis_observations=[met_obs(_CM1_DISPLAY)]), 4)
check("cM0 does NOT suppress a condition display that states a stage",
      extract_patient_stage(
          [{"display": "Carcinoma of breast, Stage 2"}],
          cancer_metastasis_observations=[met_obs(_CM0_DISPLAY)]), 2)
check("...and does not suppress the metastatic keyword either, which is a "
      "different fact from an unstated M category",
      extract_patient_stage(
          [{"display": "Metastatic malignant neoplasm to bone"}],
          cancer_metastasis_observations=[met_obs(_CM0_DISPLAY)]), 4)

# The corpus shape, reproduced: three of the five real cM1 patients have no
# stage anywhere except the group Observation, so with the group withheld the
# M tier is the only thing that can answer.
_CORPUS_SHAPE = [{"display": "Malignant neoplasm of breast (disorder)"},
                 {"display": "Essential hypertension (disorder)"},
                 {"display": "Osteoporosis (disorder)"}]
check("the measured corpus shape: no stage without the M tier",
      extract_patient_stage(_CORPUS_SHAPE), None)
check("...and stage 4 with it -- this is what the tier recovers",
      extract_patient_stage(_CORPUS_SHAPE, cancer_metastasis_observations=[
          met_obs(_CM1_DISPLAY)]), 4)


# ===========================================================================
# TEST 4 — THE OLD CALL SHAPES STILL WORK
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 4: existing callers are unaffected")
print("=" * 70)

# oncotriage/fhir/explore.py calls with ONE positional argument and no
# observations at all, once per condition row. Confirmed by running: its stage
# analysis output is unchanged.
check("the one-argument call shape still resolves a stage from text",
      extract_patient_stage([{"display": "Carcinoma of breast, Stage 3"}]), 3)
check("...and still returns None when the text carries no stage",
      extract_patient_stage([{"display": "Malignant neoplasm of breast"}]),
      None)
check("...and still reads the metastatic keyword",
      extract_patient_stage([{"display": "Metastatic carcinoma"}]), 4)
check("the two-argument keyword shape is unchanged",
      extract_patient_stage([], cancer_stage_observations=[
          stage_obs("Stage IIIC")]), 3)
check("an empty condition list with no observations is still None",
      extract_patient_stage([]), None)

_sig = _stage_module.extract_patient_stage.__code__
check("the new parameter is third and optional",
      _sig.co_varnames[:3],
      ("conditions", "cancer_stage_observations",
       "cancer_metastasis_observations"))
check("...with a default, so no existing caller has to change",
      _stage_module.extract_patient_stage.__defaults__, (None, None))


# ===========================================================================
# TEST 5 — STAGE 4 ACTUALLY PASSES THE LIST
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 5: the pipeline call site, driven through the real node")
print("=" * 70)
print("The tier is INERT unless Stage 4 passes the argument. This section")
print("drives node_rule_based_filter itself rather than asserting on source.")

# A trial for early disease only: min_stage 1, max_stage 2. A stage-4 patient
# must be dropped from it; a patient with no stage must be kept.
_EARLY = [trial("NCT_EARLY", min_stage=1, max_stage=2)]

_no_m = run_stage4(_EARLY, conditions=_CORPUS_SHAPE)
check("without an M observation the patient has no stage, so the early-disease "
      "trial survives", survivors(_no_m), ["NCT_EARLY"])
check("...and nothing was dropped by the stage filter",
      field(_no_m, "stage_dropped"), 0)

_with_m = run_stage4(_EARLY, conditions=_CORPUS_SHAPE,
                     metastasis_observations=[met_obs(_CM1_DISPLAY)])
check("WITH cM1 the node resolves stage 4 and drops the early-disease trial",
      survivors(_with_m), [])
check("...counted in stage_dropped, so the funnel can account for it",
      field(_with_m, "stage_dropped"), 1)

_with_cm0 = run_stage4(_EARLY, conditions=_CORPUS_SHAPE,
                       metastasis_observations=[met_obs(_CM0_DISPLAY)])
check("THE CONTROL: with cM0 the node drops nothing",
      survivors(_with_cm0), ["NCT_EARLY"])
check("...and stage_dropped stays 0", field(_with_cm0, "stage_dropped"), 0)

# The other direction: a trial written for advanced disease only.
_ADVANCED = [trial("NCT_ADV", min_stage=4, max_stage=None)]
_adv_no_m = run_stage4(_ADVANCED, conditions=[
    {"display": "Carcinoma of breast, Stage 2"}])
check("a stage-2 patient is dropped from an advanced-disease trial",
      survivors(_adv_no_m), [])
_adv_with_m = run_stage4(_ADVANCED,
                         conditions=[{"display": "Carcinoma of breast, Stage 2"}],
                         metastasis_observations=[met_obs(_CM1_DISPLAY)])
check("...and cM1 outranks that text, so the trial is KEPT",
      survivors(_adv_with_m), ["NCT_ADV"])

# A missing key must not raise: the node reaches the field with .get().
_missing = {"patient_data": {"demographics": {"age": 50, "sex": "female"},
                            "conditions": [],
                            "cancer_stage_observations": []},
            "reranked_trials": list(_EARLY),
            "ablation_flags": {}, "patient_trees": set(), "stage_timings": {}}
try:
    _missing_result = node_rule_based_filter(_missing)
    _missing_outcome = sorted(t["trial"]["nct_id"]
                              for t in _missing_result["filtered_trials"])
except Exception as exc:                # noqa: BLE001 - a raise IS an outcome
    _missing_outcome = f"raised {type(exc).__name__}"
check("a patient_data with no cancer_metastasis_observations key does not raise",
      _missing_outcome, ["NCT_EARLY"])

# is_stage_mismatch itself is untouched, asserted rather than assumed.
check("is_stage_mismatch keeps a stage-4 patient for an unbounded trial",
      is_stage_mismatch(4, trial()["trial"]), False)
check("...drops a stage-4 patient from a max_stage 2 trial",
      is_stage_mismatch(4, trial(max_stage=2)["trial"]), True)
check("...and keeps every trial when the stage is None",
      is_stage_mismatch(None, trial(max_stage=2)["trial"]), False)


# ===========================================================================
# TEST 6 — THE COHORT SCAN AGREES WITH STAGE 4, AND ONE LOINC LITERAL
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 6: the fixture cohort scan, and the shared LOINC")
print("=" * 70)

# scan_cohort()'s docstring promises that a patient it labels CASE_UNKNOWN_STAGE
# is one the pipeline will also find unstaged. That is only true if it passes
# the same arguments Stage 4 does.
_capture_tree = ast.parse(open(_CAPTURE_SRC, encoding="utf-8").read())
_scan_calls = [
    node for node in ast.walk(_capture_tree)
    if isinstance(node, ast.Call)
    and ast.unparse(node.func) == "extract_patient_stage"
]
check("capture.py calls extract_patient_stage exactly once", len(_scan_calls), 1)
_scan_kwargs = sorted(kw.arg for kw in _scan_calls[0].keywords) if _scan_calls \
    else ["<no call found>"]
check("...and passes BOTH observation lists, as Stage 4 does",
      _scan_kwargs,
      ["cancer_metastasis_observations", "cancer_stage_observations"])

_filtering_tree = ast.parse(
    open(os.path.join(_PKG_DIR, "agent", "filtering.py"),
         encoding="utf-8").read())
_stage4_calls = [
    node for node in ast.walk(_filtering_tree)
    if isinstance(node, ast.Call)
    and ast.unparse(node.func) == "extract_patient_stage"
]
check("Stage 4 calls extract_patient_stage exactly once", len(_stage4_calls), 1)
check("...and the two call sites pass the SAME keyword set, so the scan cannot "
      "classify a patient the pipeline disagrees with",
      sorted(kw.arg for kw in _stage4_calls[0].keywords) if _stage4_calls
      else ["<no call found>"], _scan_kwargs)

# ONE SPELLING OF THE LOINC IN THE PACKAGE. parser.py ROUTES by it and stage.py
# SELECTS by it; two literals that drift make the rule silently never fire.
_loinc_sites = []
for _path in _package_py_files():
    _tree = ast.parse(open(_path, encoding="utf-8").read())
    for _node in _non_docstring_str_constants(_tree):
        if _node.value == "21907-1":
            _loinc_sites.append(
                (os.path.relpath(_path, _PKG_DIR).replace(os.sep, "/"),
                 _node.lineno))
check("the LOINC literal '21907-1' appears exactly once in the package",
      [s[0] for s in _loinc_sites], ["constants.py"])
check("...and that one site is LOINC_AJCC_CLINICAL_M's value",
      LOINC_AJCC_CLINICAL_M, "21907-1")

# NON-DEGENERACY: a scan that found nothing anywhere would satisfy neither
# check above by accident, but a scan that cannot SEE a literal would satisfy
# the first. Prove it can see one.
_probe_tree = ast.parse('X = "21907-1"\n')
check("the literal scan is not blind: it finds a planted assignment",
      [n.value for n in _non_docstring_str_constants(_probe_tree)],
      ["21907-1"])
_probe_doc = ast.parse('"""LOINC 21907-1 is the M category."""\n')
check("...and it does NOT report a docstring, so prose arguing for this check "
      "cannot fail it",
      [n.value for n in _non_docstring_str_constants(_probe_doc)], [])
check("parser.py imports the shared name rather than typing the code",
      "LOINC_AJCC_CLINICAL_M" in open(
          os.path.join(_PKG_DIR, "fhir", "parser.py"), encoding="utf-8").read(),
      True)


# ===========================================================================
# TEST 7 — NEGATIVE CONTROLS
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 7: every assertion above is shown to FAIL when the fix is broken")
print("=" * 70)
print("Each plant goes into an in-memory COPY of the module; the file on disk")
print("is hashed before and after and asserted byte-identical.")

# The needle is the SHIPPED text of the tier, and it moved when the extractor
# started reporting which tier answered: the return carries STAGE_SOURCE_M_
# CATEGORY beside the ordinal now. Updated rather than loosened -- an exact
# needle is what makes `_plant` able to say "plant target absent" instead of
# silently planting nothing, and both controls below reported exactly that when
# this line went stale.
_TIER_CALL = (
    "    m_category_stage = _stage_from_m_category(cancer_metastasis_observations)\n"
    "    if m_category_stage is not None:\n"
    "        return m_category_stage, STAGE_SOURCE_M_CATEGORY\n")

_MATCH_LINE = '        if match.group("category") == "1":'

# 1. The tier removed entirely -- the state before this item.
_control("CONTROL: with the tier removed, cM1 no longer reaches stage 4",
         _STAGE_SRC, [(_TIER_CALL, "")],
         lambda m: m.extract_patient_stage(
             _CORPUS_SHAPE,
             cancer_metastasis_observations=[met_obs(_CM1_DISPLAY)]), None)

# 2. THE DAMAGING DIRECTION: cM0 read as evidence of an early stage. This is
#    the plant that would reach 290 corpus patients instead of 5.
_control("CONTROL: reading cM0 as a stage makes 290 corpus patients stage 0",
         _STAGE_SRC, [(_MATCH_LINE, '        if match.group("category") == "0":')],
         lambda m: m._stage_from_m_category([met_obs(_CM0_DISPLAY)]), 4)
_control("CONTROL: ...and the shipped module must NOT do that, which is what "
         "Test 1's cM0 check asserts",
         _STAGE_SRC, [(_MATCH_LINE, '        if match.group("category") in "01":')],
         lambda m: m._stage_from_m_category([met_obs(_CM0_DISPLAY)]), 4)

# 3. The tier moved ABOVE the stage group -- a real ordering regression that
#    every Test 1 check would still pass.
_GROUP_TIER_HEAD = "    # Tier 0: mCODE TNM stage group Observations"
_control("CONTROL: hoisting the M tier above the stage group makes cM1 beat an "
         "explicit Stage IIIA assignment",
         _STAGE_SRC,
         [(_TIER_CALL, ""),
          (_GROUP_TIER_HEAD, _TIER_CALL + _GROUP_TIER_HEAD)],
         lambda m: m.extract_patient_stage(
             _BREAST,
             cancer_stage_observations=[stage_obs("Stage IIIA")],
             cancer_metastasis_observations=[met_obs(_CM1_DISPLAY)]), 4)

# 4. Keying on metastasis_category instead of the LOINC -- the mistake that
#    pulls 44667-4's site vocabulary in.
_CODE_GUARD = ('        if (obs.get("code") or "").strip() '
               '!= LOINC_AJCC_CLINICAL_M:')
_control("CONTROL: keying on category 'M' instead of the LOINC admits 44667-4",
         _STAGE_SRC,
         [(_CODE_GUARD, '        if obs.get("metastasis_category") != "M":')],
         lambda m: m._stage_from_m_category(
             [met_obs("cM1", code="44667-4", category="M")]), 4)

# 5. The unreadable counter removed -- a silent skip.
_COUNT_LINE = "            M_CATEGORY_UNREADABLE[_m_key_text(text)] += 1"
_control("CONTROL: without the counter an unreadable value is silently skipped",
         _STAGE_SRC, [(_COUNT_LINE, "            pass")],
         lambda m: (m._stage_from_m_category([met_obs("no category recorded")]),
                    dict(m.M_CATEGORY_UNREADABLE)), (None, {}))

# 6. \b instead of the lookarounds -- the regex that matches NOTHING in the
#    string this corpus actually stores, because "c" is a word character.
_RE_BLOCK = (
    '    r"(?<![A-Za-z0-9])"                 # start of a token, not mid-word\n'
    '    r"(?:yc|yp|rc|rp|[cpry])?"          # optional AJCC determination prefix\n'
    '    r"m(?P<category>[01x])"             # the axis value itself\n'
    '    r"(?P<subcategory>[a-d])?"          # M1a / M1b / M1c / M1d\n'
    '    r"(?![A-Za-z0-9])",                 # end of a token\n')
_control("CONTROL: a \\b-anchored regex cannot see the 'M1' inside 'cM1'",
         _STAGE_SRC,
         [(_RE_BLOCK, '    r"\\bM(?P<category>[01x])(?P<subcategory>[a-d])?\\b",\n')],
         lambda m: m._stage_from_m_category([met_obs(_CM1_DISPLAY)]), None)
_control("CONTROL: ...and that plant is a REAL regression rather than a broken "
         "plant, because a bare 'M1' still resolves under it",
         _STAGE_SRC,
         [(_RE_BLOCK, '    r"\\bM(?P<category>[01x])(?P<subcategory>[a-d])?\\b",\n')],
         lambda m: m._stage_from_m_category([met_obs("M1")]), 4)

# 7. The key-length cap removed.
_control("CONTROL: without the cap the counter key grows without bound",
         _STAGE_SRC,
         [("    return text if len(text) <= _M_KEY_MAX_LEN else "
           'text[:_M_KEY_MAX_LEN] + "..."', "    return text")],
         lambda m: (m._stage_from_m_category([met_obs("z" * 200)]),
                    max(len(k) for k in m.M_CATEGORY_UNREADABLE))[1], 200)

# 8. THE CALL SITE, which is what makes the tier reachable at all. Reverting it
#    leaves every check in Tests 1-4 passing and the pipeline unchanged -- so
#    this control is the one that proves Test 5 is doing work.
_STAGE4_CALL = ("        cancer_metastasis_observations=patient_data.get("
                "'cancer_metastasis_observations') or [],\n")
_FILTERING_SRC = _FILTERING_PATH
_control("CONTROL: reverting Stage 4's call site makes cM1 stop dropping the "
         "early-disease trial, with the extractor entirely correct",
         _FILTERING_SRC, [(_STAGE4_CALL, "")],
         lambda m: sorted(
             t["trial"]["nct_id"] for t in m.node_rule_based_filter(
                 {"patient_data": {
                      "demographics": {"age": 50, "sex": "female"},
                      "conditions": list(_CORPUS_SHAPE),
                      "cancer_stage_observations": [],
                      "cancer_metastasis_observations": [
                          met_obs(_CM1_DISPLAY)]},
                  "reranked_trials": [trial("NCT_EARLY", min_stage=1,
                                            max_stage=2)],
                  "ablation_flags": {}, "patient_trees": set(),
                  "stage_timings": {}})["filtered_trials"]), ["NCT_EARLY"])

# 9. THE PLANT MACHINERY ITSELF. A control that aborts is not a control.
def _plant_outcome(path, subs):
    """The exception TYPE NAME a plant produced, or 'no exception'."""
    try:
        _plant(path, "probe", subs)
    except Exception as exc:            # noqa: BLE001 - the point
        return type(exc).__name__
    return "no exception"


check("a plant whose target is absent is reported, not raised as SyntaxError",
      _plant_outcome(_STAGE_SRC, [("this text is not in the module", "x")]),
      "_PlantFailed")
check("a plant that produces invalid Python is reported the same way",
      _plant_outcome(_STAGE_SRC, [(_MATCH_LINE, "        if ((((")]),
      "_PlantFailed")
check("an unmutated plant of the shipped module compiles and behaves",
      _plant(_STAGE_SRC, "unmutated", [])._stage_from_m_category(
          [met_obs(_CM1_DISPLAY)]), 4)
check("stage.py on disk is byte-identical to what it was before any plant ran",
      _sha256_of(_STAGE_SRC), _STAGE_SHA_BEFORE)
check("...and so is filtering.py, which control 8 planted into",
      _sha256_of(_FILTERING_PATH), _FILTERING_SHA_BEFORE)
check("...and that comparison is not a tautology: a changed file is detected",
      _sha256_of(_STAGE_SRC) == _sha256_of(_CAPTURE_SRC), False)

check("the module-level counter is left empty for the next reader",
      dict(M_CATEGORY_UNREADABLE), {})


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
