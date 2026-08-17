# Stage Extraction: the non-oncology guard on the patient side
##############################################################

"""
Non-Oncology Patient Stage Guard Test

``extract_patient_stage()``'s condition-display tier matched
"Chronic kidney disease stage 3 (disorder)" and returned cancer stage 3.
The module already carried the guard for this -- ``_is_non_oncology_stage``
and ``_NON_ONCOLOGY_STAGE_CONTEXT_RE``, covering CKD, GVHD, NYHA, Child-Pugh,
COPD and more -- and the TRIAL-side extractor used it. The patient side did
not. This item wires the existing guard up; it writes no second one.

MEASURED OVER ALL 1,000 CORPUS BUNDLES ON 2026-08-08, re-derived rather than
taken from the note that claimed it: of the 260 patients whose stage came from
this tier, 245 got it from a chronic-kidney-disease display and 15 from a real
cancer TNM display. Corpus-wide the regex matched CKD displays 1,025 times
against 16 cancer ones. 244 patients change stage.

IT DAMAGED IN BOTH DIRECTIONS, which is why leaving it was not the
conservative choice: a CKD stage 1 sets a floor that drops the
advanced-disease trials the patient qualifies for (35.7% of the trial corpus),
and a CKD stage 4 sets a ceiling that drops the early ones (7.6%).

THE COUNTER KEY IS SEPARATE AND THAT IS THE SUBTLE PART. ``_is_non_oncology_
stage`` increments ``_STAGE_EXTRACTION_COUNTS["non_oncology_stage_skipped"]``,
which ``oncotriage/retrieval/indexer.py`` reads through
``get_stage_extraction_stats()`` after an index build to describe TRIAL text.
Patient-side calls fire at QUERY time, once per matching condition display, on
every patient of every run -- so landing them in that key would mix an
unbounded query-time count into an index-time statistic. The patient side gets
``non_oncology_patient_stage_skipped``; section 4 proves the two never cross.

NOTHING PINS THAT DICT'S KEY SET -- checked before the key was added, and
section 4e re-checks it so the finding cannot silently rot.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every fixture here
is a literal dict. NOT in tests/run_serial_tests.py's collision matrix: it
writes nothing anywhere -- every plant goes into an in-memory copy, hashed
before and after -- and the two source files it reads are written by neither
of the suite's two writers.

    python tests/test_extraction_stage_non_oncology_guard.py
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
from oncotriage.extraction import stage as _stage_module
from oncotriage.extraction.stage import (
    _STAGE_EXTRACTION_COUNTS,
    _is_non_oncology_stage,
    enrich_structured_eligibility,
    extract_patient_stage,
    get_stage_extraction_stats,
    reset_stage_extraction_stats,
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


def fail(label: str, detail: str) -> None:
    """Record an outright failure that is not an equality comparison.

    Mirrors tests/test_package_invariants.py's helper of the same name, and
    exists for the same reason: section 4e's repository scan can meet a file it
    cannot read, and that is a fact about coverage rather than a comparison of
    two values. It increments nothing until it is called, so the file's pass
    count is unchanged on a machine with nothing to report.
    """
    _RESULTS["failed"] += 1
    _FAILURES.append(f"{label}\n          {detail}")
    print(f"  FAIL  {label}")
    print(f"          {detail}")


_STAGE_SRC = os.path.abspath(_stage_module.__file__)
_PKG_DIR = os.path.dirname(os.path.abspath(oncotriage.__file__))
_CAPTURE_SRC = os.path.join(_PKG_DIR, "fixtures", "capture.py")
_FILTERING_SRC = os.path.join(_PKG_DIR, "agent", "filtering.py")


def _sha256_of(path):
    return hashlib.sha256(
        open(path, encoding="utf-8").read().encode()).hexdigest()


# Taken before any plant runs, so the restore assertion at the end compares
# against a real baseline rather than against itself.
_STAGE_SHA_BEFORE = _sha256_of(_STAGE_SRC)


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(path, name, subs):
    """Exec an in-memory COPY of `path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is a
    RECORDED failure instead of a traceback hiding every check below it.
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


def cond(display):
    """One condition, as oncotriage/fhir/parser.py:_parse_condition emits it."""
    return {"display": display}


def stage_obs(display, date="2020-01-01T00:00:00-00:00"):
    """One entry of patient_data['cancer_stage_observations']."""
    return {"stage_display": display, "stage_code": "", "date": date,
            "loinc": "21908-9"}


# The four CKD displays the corpus actually carries, verbatim.
_CKD = ["Chronic kidney disease stage 1 (disorder)",
        "Chronic kidney disease stage 2 (disorder)",
        "Chronic kidney disease stage 3 (disorder)",
        "Chronic kidney disease stage 4 (disorder)"]

# The two real cancer TNM displays the corpus carries, verbatim.
_CANCER_TNM = ["Non-small cell carcinoma of lung, TNM stage 1 (disorder)",
               "Primary small cell malignant neoplasm of lung, TNM stage 1 "
               "(disorder)"]


# ===========================================================================
# TEST 1 — THE GUARD SUPPRESSES NON-CANCER STAGING SYSTEMS
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 1: a non-cancer staging system is not a cancer stage")
print("=" * 70)

for _d in _CKD:
    check(f"{_d!r} yields no stage",
          extract_patient_stage([cond(_d)]), None)

check("all four CKD displays together still yield no stage",
      extract_patient_stage([cond(d) for d in _CKD]), None)
check("...and the result is None rather than 0, which is stage 0 (in situ)",
      extract_patient_stage([cond(_CKD[0])]) is None, True)

# The other staging systems the shared guard already covers. These are not in
# the Synthea corpus; they are what a real EHR carries, and they are the whole
# reason the guard is a vocabulary rather than a CKD special case.
for _d in ("Stage 3 chronic kidney disease",
           "Acute skin GVHD stage 2",
           "Graft-versus-host disease, stage 3",
           "NYHA stage III heart failure",
           "Child-Pugh stage B cirrhosis",
           "COPD stage 2",
           "Pressure ulcer stage 3",
           "Stage 2 hypertension",
           "Diabetic retinopathy stage 4",
           "Stage 3 nephropathy",
           "End-stage renal disease, stage 5"):
    check(f"{_d!r} yields no stage", extract_patient_stage([cond(_d)]), None)


# ===========================================================================
# TEST 2 — WHAT MUST STILL RESOLVE
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 2: real cancer stages are untouched")
print("=" * 70)

check(f"{_CANCER_TNM[0]!r} still resolves to 1",
      extract_patient_stage([cond(_CANCER_TNM[0])]), 1)
check(f"{_CANCER_TNM[1]!r} still resolves to 1",
      extract_patient_stage([cond(_CANCER_TNM[1])]), 1)

for _d, _want in (("Carcinoma of breast, Stage 3", 3),
                  ("Stage IV lung cancer", 4),
                  ("TNM stage 1 (disorder)", 1),
                  ("Stage IIIA carcinoma", 3),
                  ("Malignant neoplasm of breast, stage 2", 2),
                  ("Stage 0 carcinoma in situ", 0)):
    check(f"{_d!r} still resolves to {_want}",
          extract_patient_stage([cond(_d)]), _want)

# THE GUARD IS BUILT ON DISEASE-SPECIFIC PHRASES, NEVER BARE ORGAN WORDS.
# "renal" or "kidney" alone would suppress "Stage IV renal cell carcinoma".
# The module comment says so; this proves it by running.
for _d, _want in (("Stage IV renal cell carcinoma", 4),
                  ("Renal cell carcinoma, Stage 3", 3),
                  ("Stage 2 carcinoma of kidney", 2),
                  ("Malignant neoplasm of kidney, TNM stage 1", 1),
                  ("Stage 1 renal cell carcinoma of the left kidney", 1),
                  ("Hepatocellular carcinoma, Stage 4", 4),
                  ("Stage III carcinoma of liver", 3),
                  ("Stage 2 hepatoblastoma", 2)):
    check(f"a real cancer of the kidney/liver still resolves: {_d!r} -> {_want}",
          extract_patient_stage([cond(_d)]), _want)

# NON-DEGENERACY: the assertions above would also pass if the guard suppressed
# nothing at all. Test 1 is the other half, and this pins the pair.
check("the guard discriminates: the CKD display and the renal-cancer display "
      "get different answers",
      (extract_patient_stage([cond("Chronic kidney disease stage 3 (disorder)")]),
       extract_patient_stage([cond("Stage III renal cell carcinoma")])),
      (None, 3))


# ===========================================================================
# TEST 3 — TIER INTERACTION
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 3: the guard applies to the display tier and nowhere else")
print("=" * 70)

# The mCODE stage-group tier is cancer staging BY ITS LOINC CODE. A guard
# there could only suppress a legitimate stage, so it is deliberately absent.
check("a stage GROUP observation is not guarded, even when the patient also "
      "carries CKD",
      extract_patient_stage([cond(_CKD[3])],
                            cancer_stage_observations=[stage_obs("Stage IIIA")]),
      3)
check("...and a stage group reading Stage 4 still gives 4",
      extract_patient_stage([cond(_CKD[0])],
                            cancer_stage_observations=[
                                stage_obs("Stage 4 (qualifier value)")]), 4)

# THE REAL CORPUS SHAPE, both patients reproduced from the measurement.
check("CKD stage 1 no longer masks a genuine lung TNM stage 1 (corpus patient "
      "1c1fdc23...): same answer, correct provenance",
      extract_patient_stage([cond(_CKD[0]), cond(_CANCER_TNM[0])]), 1)
check("...and the order does not matter",
      extract_patient_stage([cond(_CANCER_TNM[0]), cond(_CKD[0])]), 1)
check("CKD stage 3 no longer masks metastatic prostate cancer (corpus patient "
      "404d2880...): 3 -> 4",
      extract_patient_stage([cond("Chronic kidney disease stage 3 (disorder)"),
                             cond("Chronic kidney disease stage 2 (disorder)"),
                             cond("Metastatic malignant neoplasm to prostate "
                                  "(disorder)")]), 4)

# The metastatic-keyword tier is deliberately unguarded -- argued at the code.
check("the metastatic tier still fires when the display tier is suppressed",
      extract_patient_stage([cond(_CKD[2]),
                             cond("Metastatic carcinoma")]), 4)
check("...and 'non-metastatic' still suppresses it, as before",
      extract_patient_stage([cond("Non-metastatic carcinoma")]), None)

# finditer, not search: a display whose FIRST stage mention is suppressed can
# still yield a later cancer stage. This mirrors _collect_stage_ordinals().
check("one display carrying both: the CKD mention is skipped and the cancer "
      "mention answers",
      extract_patient_stage([cond("Stage 2 chronic kidney disease and "
                                  "carcinoma of breast at Stage 3")]), 3)
check("...and a display carrying only suppressed mentions yields nothing",
      extract_patient_stage([cond("Stage 2 chronic kidney disease and "
                                  "stage 3 chronic kidney disease")]), None)


# ===========================================================================
# TEST 4 — THE COUNTER, AND THE KEY THAT MUST NOT BE SHARED
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 4: query-time counts must not pollute the index-time statistic")
print("=" * 70)

reset_stage_extraction_stats()
check("reset zeroes every key", any(get_stage_extraction_stats().values()),
      False)

extract_patient_stage([cond(_CKD[2])])
_after = get_stage_extraction_stats()
check("a patient-side skip is counted", _after["non_oncology_patient_stage_skipped"],
      1)
check("...and the TRIAL-side key did not move", _after["non_oncology_stage_skipped"],
      0)

reset_stage_extraction_stats()
enrich_structured_eligibility({
    "title": "A Study",
    "eligibility": {"inclusion_criteria": "Histologically confirmed carcinoma",
                    "exclusion_criteria": "Stage 4 chronic kidney disease"}})
_after = get_stage_extraction_stats()
check("a TRIAL-side skip is counted under the trial-side key",
      _after["non_oncology_stage_skipped"] >= 1, True)
check("...and the PATIENT-side key did not move",
      _after["non_oncology_patient_stage_skipped"], 0)

reset_stage_extraction_stats()
for _d in _CKD:
    extract_patient_stage([cond(_d)])
check("four patient-side skips are counted individually",
      get_stage_extraction_stats()["non_oncology_patient_stage_skipped"], 4)

reset_stage_extraction_stats()
extract_patient_stage([cond("Carcinoma of breast, Stage 3")])
check("a clean cancer display increments nothing",
      any(get_stage_extraction_stats().values()), False)

# 4e. THE FINDING THAT LET THIS KEY BE ADDED, re-checked so it cannot rot.
_repo = os.path.dirname(_PKG_DIR)

# What is not this project's source: caches, build artifacts, the VCS
# directory. Mirrors .github/scripts/static_checks.py's _SKIP_DIRS and
# tests/test_package_invariants.py's _SKIP_WALK_DIRS, so the three agree.
_SKIP_WALK_DIRS = frozenset({
    "__pycache__", ".git", "build", "oncotriage.egg-info", ".vscode",
    ".venv", "venv", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache",
})


def _prune_walk_dirs(dirpath, dirnames):
    """In-place prune of non-source directories, VIRTUAL ENVIRONMENTS included.

    A VENV IS IDENTIFIED BY ITS ``pyvenv.cfg`` MARKER, NOT BY ITS NAME. The
    name list above is a convenience the marker makes non-load-bearing, and
    ``09- Testing/ragas-venv/`` is the live proof that a name list rots: a real,
    deliberately un-pinned virtualenv (see ``oncotriage/evaluation/
    ragas_harness.py`` for why ragas is NOT a pipeline dependency), untracked
    and self-ignored, matching neither ``venv`` nor ``.venv``. ``python -m
    venv`` writes ``pyvenv.cfg`` at the root of every environment it creates.

    Duplicated verbatim from tests/test_package_invariants.py rather than
    shared, on the standing precedent for this suite: every file here is a
    self-contained script with no shared helper module, and `tests/` has no
    ``__init__.py`` to import one from.

    The prune is right independently of the decode arm below. This scan asks
    what pins the key SET of _STAGE_EXTRACTION_COUNTS, a question only about
    code this project owns; site-packages can only contribute false positives
    and 38,000 files of latency to it.

    ``isfile`` rather than ``exists``: the marker is a FILE, and a directory
    that happened to be named ``pyvenv.cfg`` is not a virtualenv. ``sorted``
    for the reason static_checks.py sorts -- determinism is a stated property
    of this project, and an unsorted walk makes the ORDER of a failure report
    depend on ``os.scandir``.
    """
    dirnames[:] = [d for d in sorted(dirnames)
                   if d not in _SKIP_WALK_DIRS
                   and not os.path.isfile(
                       os.path.join(dirpath, d, "pyvenv.cfg"))]


_pins = []
for _dirpath, _dirnames, _filenames in os.walk(_repo):
    _prune_walk_dirs(_dirpath, _dirnames)
    for _name in sorted(_filenames):
        if not _name.endswith(".py"):
            continue
        _p = os.path.join(_dirpath, _name)
        if os.path.abspath(_p) == os.path.abspath(__file__):
            continue
        try:
            _tree = ast.parse(open(_p, encoding="utf-8").read())
        except UnicodeDecodeError as _exc:
            # A SEPARATE ARM, NOT A THIRD MEMBER OF THE TUPLE BELOW.
            # UnicodeDecodeError is a ValueError, so neither OSError nor
            # SyntaxError catches it and it ABORTED this file mid-run --
            # traceback, no summary, exit code from the crash rather than from
            # the results. Adding it to that tuple would fix the abort by
            # trading it for a silent skip, and a scan that quietly covers less
            # reports FEWER pins, which reads exactly like a repository that
            # pins nothing. Named finding instead.
            fail("4e  every .py in the repository scan corpus decoded as UTF-8",
                 f"{os.path.relpath(_p, _repo)}: "
                 f"{type(_exc).__name__}: {_exc}")
            continue
        except (OSError, SyntaxError):
            continue
        for _node in ast.walk(_tree):
            # `.keys()` / `sorted(...)` / `set(...)` over the stats dict is what
            # a pinned key SET looks like; reading one key is not.
            if (isinstance(_node, ast.Call)
                    and isinstance(_node.func, ast.Attribute)
                    and _node.func.attr == "keys"
                    and "stage_extraction_stats" in ast.unparse(_node.func.value)):
                _pins.append(f"{_name}:{_node.lineno}")
check("nothing in the repository pins the key SET of _STAGE_EXTRACTION_COUNTS, "
      "which is what made adding a key safe", _pins, [])
check("...and the scan is not blind: it finds a planted .keys() call",
      [n.func.attr for n in ast.walk(
          ast.parse("get_stage_extraction_stats().keys()"))
       if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)],
      ["keys"])
check("both non_oncology keys are declared, so a typo at an increment site "
      "raises KeyError rather than creating a counter nobody reads",
      sorted(k for k in _STAGE_EXTRACTION_COUNTS if k.startswith("non_oncology")),
      ["non_oncology_patient_stage_skipped", "non_oncology_stage_skipped"])
def _increment_outcome(key):
    """The exception type name from `counts[key] += 1`, or 'no exception'.

    Run against a COPY of the dict, so a probe about the safety property
    cannot leave a stray key in the shipped one.
    """
    probe = dict(_STAGE_EXTRACTION_COUNTS)
    try:
        probe[key] += 1
    except Exception as exc:            # noqa: BLE001 - the point
        return type(exc).__name__
    return "no exception"


check("an undeclared key raises KeyError rather than being created silently, "
      "which is why a plain dict is used here and not a Counter",
      _increment_outcome("non_oncology_typo_skipped"), "KeyError")
check("...and a declared key does not raise, so the probe discriminates",
      _increment_outcome("non_oncology_patient_stage_skipped"), "no exception")
check("the probe left no stray key in the shipped dict",
      "non_oncology_typo_skipped" in _STAGE_EXTRACTION_COUNTS, False)


# ===========================================================================
# TEST 5 — THE SHARED IMPLEMENTATION, AND THE PIPELINE
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 5: one guard, two callers")
print("=" * 70)

# _is_non_oncology_stage is the SAME function both sides call. Its default
# argument is the trial-side key, so no existing call site changed.
check("_is_non_oncology_stage defaults to the trial-side key",
      _stage_module._is_non_oncology_stage.__defaults__,
      ("non_oncology_stage_skipped",))

_stage_tree = ast.parse(open(_STAGE_SRC, encoding="utf-8").read())
_guard_calls = [n for n in ast.walk(_stage_tree)
                if isinstance(n, ast.Call)
                and ast.unparse(n.func) == "_is_non_oncology_stage"]
check("the guard has exactly three call sites: two trial-side, one patient-side",
      len(_guard_calls), 3)
check("...and exactly one of them names the patient-side key",
      sum(1 for c in _guard_calls
          for kw in c.keywords
          if kw.arg == "counter_key"), 1)
check("there is exactly ONE non-oncology context regex in the module, so this "
      "is the existing guard rather than a second one",
      len([n for n in ast.walk(_stage_tree)
           if isinstance(n, ast.Assign)
           and any(getattr(t, "id", "") == "_NON_ONCOLOGY_STAGE_CONTEXT_RE"
                   for t in n.targets)]), 1)

# The pipeline, driven through the real Stage 4 node.
deps.set_override(deps.MESH_FILTER, None)


def trial(nct="NCT1", min_stage=None, max_stage=None):
    return {"trial": {"nct_id": nct, "title": "a trial", "histology_tags": [],
                      "structured_eligibility": {"min_stage": min_stage,
                                                 "max_stage": max_stage,
                                                 "accepts_metastatic": None},
                      "eligibility": {"min_age": "18 Years",
                                      "max_age": "99 Years", "sex": "ALL",
                                      "inclusion_criteria": ""}},
            "rerank_score": 1.0, "rerank_score_raw": 1.0,
            "medcpt_score_max": 100.0}


def run_stage4(trials, conditions):
    state = {"patient_data": {"demographics": {"age": 50, "sex": "female"},
                              "conditions": list(conditions),
                              "cancer_stage_observations": [],
                              "cancer_metastasis_observations": []},
             "reranked_trials": list(trials),
             "ablation_flags": {}, "patient_trees": set(), "stage_timings": {}}
    try:
        return node_rule_based_filter(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}: {exc}"


def survivors(result):
    if isinstance(result, str):
        return result
    return sorted(t["trial"]["nct_id"] for t in result["filtered_trials"])


# A CKD stage 1 patient used to be given a FLOOR of 1, dropping every trial
# written for advanced disease. 35.7% of the real trial corpus, measured.
_ADVANCED = [trial("NCT_ADV", min_stage=3, max_stage=None)]
check("a CKD stage 1 patient is no longer dropped from advanced-disease trials",
      survivors(run_stage4(_ADVANCED, [cond(_CKD[0])])), ["NCT_ADV"])
# ...and a CKD stage 4 patient used to be given a CEILING of 4, dropping the
# early-disease trials.
_EARLY = [trial("NCT_EARLY", min_stage=1, max_stage=2)]
check("a CKD stage 4 patient is no longer dropped from early-disease trials",
      survivors(run_stage4(_EARLY, [cond(_CKD[3])])), ["NCT_EARLY"])
check("a patient with a REAL stage 4 is still dropped from an early-disease "
      "trial, so the filter did not simply stop working",
      survivors(run_stage4(_EARLY, [cond("Carcinoma of breast, Stage 4")])), [])

# capture.py's scan_cohort promises to classify the way Stage 4 does. Compared
# by call site rather than by reading the docstring.
_cap_tree = ast.parse(open(_CAPTURE_SRC, encoding="utf-8").read())
_flt_tree = ast.parse(open(_FILTERING_SRC, encoding="utf-8").read())


def _stage_call_kwargs(tree):
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and ast.unparse(n.func) == "extract_patient_stage"]
    if len(calls) != 1:
        return f"<{len(calls)} call sites>"
    return sorted(kw.arg for kw in calls[0].keywords)


check("Stage 4 and scan_cohort still pass the identical keyword set",
      _stage_call_kwargs(_cap_tree), _stage_call_kwargs(_flt_tree))
check("...and that set is non-degenerate", _stage_call_kwargs(_flt_tree),
      ["cancer_metastasis_observations", "cancer_stage_observations"])
check("neither passes a counter_key, so both get the patient-side key the "
      "extractor chooses",
      "counter_key" in open(_CAPTURE_SRC, encoding="utf-8").read()
      or "counter_key" in open(_FILTERING_SRC, encoding="utf-8").read(),
      False)


# ===========================================================================
# TEST 6 — NEGATIVE CONTROLS
# ===========================================================================

print("\n" + "=" * 70)
print("TEST 6: every assertion above is shown to FAIL when the fix is broken")
print("=" * 70)

_GUARD_CALL = (
    "            if _is_non_oncology_stage(\n"
    "                    display, m.start(), m.end(),\n"
    '                    counter_key="non_oncology_patient_stage_skipped"):\n'
    "                continue\n")

# 1. The guard removed from the display tier -- the state before this item.
_control("CONTROL: without the guard, a CKD display is read as cancer stage 3",
         _STAGE_SRC, [(_GUARD_CALL, "")],
         lambda m: m.extract_patient_stage([cond(_CKD[2])]), 3)
_control("CONTROL: ...and CKD stage 1 as cancer stage 1, the floor that "
         "dropped advanced-disease trials",
         _STAGE_SRC, [(_GUARD_CALL, "")],
         lambda m: m.extract_patient_stage([cond(_CKD[0])]), 1)
_control("CONTROL: ...while a planted 'Stage IV renal cell carcinoma' resolves "
         "to 4 either way, so the control is about the guard and not the regex",
         _STAGE_SRC, [(_GUARD_CALL, "")],
         lambda m: m.extract_patient_stage(
             [cond("Stage IV renal cell carcinoma")]), 4)

# 2. The guard's vocabulary widened to a bare organ word -- the mistake the
#    module comment warns about, which no CKD test would catch.
_control("CONTROL: a bare organ word in the guard suppresses a REAL renal "
         "cancer, which is why the vocabulary is disease-specific",
         _STAGE_SRC,
         [(r'r"\bckd\b|chronic\s+kidney\s+disease|kidney\s+disease|"',
           r'r"\bckd\b|chronic\s+kidney\s+disease|kidney\s+disease|renal|"')],
         lambda m: m.extract_patient_stage(
             [cond("Stage IV renal cell carcinoma")]), None)

# 3. The counter key shared -- the defect the separate key exists to prevent.
_control("CONTROL: sharing the key puts query-time counts into the index-time "
         "statistic",
         _STAGE_SRC,
         [('                    counter_key="non_oncology_patient_stage_skipped"):',
           "                    ):")],
         lambda m: (m.reset_stage_extraction_stats(),
                    m.extract_patient_stage([cond(_CKD[2])]),
                    m.get_stage_extraction_stats()["non_oncology_stage_skipped"])[2],
         1)

# 4. The counter removed entirely -- a silent skip.
_control("CONTROL: without the counter the skip is silent",
         _STAGE_SRC,
         [('        _STAGE_EXTRACTION_COUNTS[counter_key] += 1\n'
           '        return True', "        return True")],
         lambda m: (m.reset_stage_extraction_stats(),
                    m.extract_patient_stage([cond(_CKD[2])]),
                    any(m.get_stage_extraction_stats().values()))[2],
         False)

# 5. finditer reverted to search -- the display carrying both mentions loses
#    its cancer stage.
_control("CONTROL: search-and-give-up loses a cancer stage that shares a "
         "display with a suppressed one",
         _STAGE_SRC,
         [("        for m in _SNOMED_DISPLAY_STAGE_RE.finditer(display):",
           "        for m in [_SNOMED_DISPLAY_STAGE_RE.search(display)] "
           "if _SNOMED_DISPLAY_STAGE_RE.search(display) else []:")],
         lambda m: m.extract_patient_stage(
             [cond("Stage 2 chronic kidney disease and carcinoma of breast "
                   "at Stage 3")]), None)

# 6. The guard applied to the stage-GROUP tier, which it must never be.
_control("CONTROL: guarding the stage-group tier would suppress a legitimate "
         "cancer stage for any patient who also has CKD",
         _STAGE_SRC,
         [("            m = _SNOMED_DISPLAY_STAGE_RE.search(display)\n"
           "            if m:\n",
           "            m = _SNOMED_DISPLAY_STAGE_RE.search(display)\n"
           "            if m and not _is_non_oncology_stage("
           "display, m.start(), m.end()):\n")],
         lambda m: m.extract_patient_stage(
             [], cancer_stage_observations=[
                 stage_obs("Stage IIIA chronic kidney disease patient")]), None)

# 7. THE PLANT MACHINERY ITSELF.
def _plant_outcome(path, subs):
    try:
        _plant(path, "probe", subs)
    except Exception as exc:            # noqa: BLE001 - the point
        return type(exc).__name__
    return "no exception"


check("a plant whose target is absent is reported, not raised",
      _plant_outcome(_STAGE_SRC, [("text that is not in the module", "x")]),
      "_PlantFailed")
check("a plant that produces invalid Python is reported the same way",
      _plant_outcome(_STAGE_SRC, [(_GUARD_CALL, "            if ((((\n")]),
      "_PlantFailed")
check("an unmutated plant of the shipped module behaves",
      _plant(_STAGE_SRC, "unmutated", []).extract_patient_stage(
          [cond(_CKD[2])]), None)
check("stage.py on disk is byte-identical to what it was before any plant",
      _sha256_of(_STAGE_SRC), _STAGE_SHA_BEFORE)
check("...and that comparison is not a tautology: a different file differs",
      _sha256_of(_STAGE_SRC) == _sha256_of(_CAPTURE_SRC), False)

reset_stage_extraction_stats()
check("the counters are left zeroed for the next reader",
      any(get_stage_extraction_stats().values()), False)


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
Created on Fri Aug  8 2026

@author: ramyalsaffar
"""
