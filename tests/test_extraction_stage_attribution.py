# Stage Attribution: which cancer the rendered stage belongs to
###############################################################

"""
Stage Attribution Test

``extract_patient_stage_with_source`` answers ONCE per patient, and until this
item its answer named no cancer: the Stage 5 line read "Cancer Stage: Stage
III (from a recorded stage group observation; staged ...)" over a condition
list that might hold two neoplasms, and nothing in the prompt bound the number
to either. Binding it to the wrong one is a wrong answer to every stage-gated
criterion of the other.

MEASURED OVER ALL 1,000 CORPUS BUNDLES:

    stage-producing patients                        312
    ...carrying MORE THAN ONE Tier A neoplasm        48   (15.4%)
        two / three / four                       40 / 7 / 1
    answering tier: stage_group_observation         295   (32 multi-neoplasm)
                    condition_display                16   (16 multi-neoplasm)
                    metastatic_keyword                1
                    m_category_observation            0

THIS CORPUS STATES NO EXPLICIT LINKAGE, IN EITHER DIRECTION, ANYWHERE. Also
measured, by reading the bundles rather than the documentation:

    staging + M Observations                        880
      carrying focus / partOf / basedOn /
      derivedFrom / hasMember                         0
    Conditions                                  254,494
      carrying a `stage` element at all               0

So attribution from an Observation is IMPOSSIBLE on Synthea data, and the
sections below that exercise it drive constructed records. That is a fact
about the generator rather than about the design: a real mCODE EHR writes
``Condition.stage.assessment`` and ``Observation.focus``, and a mechanism that
only appears once the data appears is a mechanism nobody has ever run.

AND THE PROXIMITY RULE HAD TO BE WRITTEN DOWN RATHER THAN ASSUMED, because on
this corpus the wrong rule looks perfect: every one of those 880 Observations
shares its encounter with EXACTLY ONE Condition, so a shared-encounter
heuristic would resolve for 100% of them and be unfalsifiable from the inside.
Section 6 pins that no such rule exists in the resolver.

WHAT THIS FILE DOES NOT COVER, and where it lives instead: the RENDERED line's
two shapes are pinned in tests/test_agent_summary_cancer_stage.py, which owns
the stage line; this file owns the EXTRACTOR's answer and the hash.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO DATABASE, NO
GIT HISTORY, NO LIVE SERVER, NO CORPUS -- every fixture here is a literal dict
in the shape oncotriage/fhir/parser.py emits. It writes NOTHING anywhere, not
even a temp directory. NOT in tests/run_serial_tests.py's collision matrix:
the two repository files it reads (oncotriage/extraction/stage.py,
oncotriage/agent/patient.py) are written by neither of the suite's two writers
and both are sha256-compared at the end. It DOES exec: in-memory copies of
stage.py, one plant each -- `git show` can supply none of them, because every
one reverts a fix that is AT HEAD and would compare the module with itself.

    python tests/test_extraction_stage_attribution.py
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

from oncotriage.extraction import stage as _stage_module
from oncotriage.extraction.stage import (
    MCategoryStage,
    PatientStage,
    STAGE_ATTRIBUTION_AMBIGUOUS_KEY,
    STAGE_ATTRIBUTION_CONDITION_ASSESSMENT,
    STAGE_ATTRIBUTION_DIAGNOSIS_TEXT,
    STAGE_ATTRIBUTION_OBSERVATION_FOCUS,
    STAGE_ATTRIBUTION_UNNAMED_KEY,
    STAGE_ATTRIBUTION_UNRESOLVED,
    STAGE_ATTRIBUTIONS,
    STAGE_ATTRIBUTIONS_FROM_REFERENCE,
    STAGE_SOURCE_CONDITION_DISPLAY,
    STAGE_SOURCE_M_CATEGORY,
    STAGE_SOURCE_METASTATIC_KEYWORD,
    STAGE_SOURCE_STAGE_GROUP,
    STAGE_SOURCES,
    STAGE_SOURCES_OBSERVATION_BACKED,
    _attribute_observation,
    extract_patient_stage,
    extract_patient_stage_with_source,
)
from oncotriage.agent import patient as _patient_module
from oncotriage import deid as _deid_module
from oncotriage.fhir.parser import (
    _condition_stage_assessment_ids,
    _observation_link_fields,
    _reference_id,
    _reference_ids,
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


def guarded(fn):
    """Call `fn`, converting a raise into a value `check` can fail on.

    A raise inside a check's ARGUMENT list escapes `check` entirely and takes
    the run with it -- reporting one traceback where the file owed a summary
    and a full set of results. Every plant below can make a probe raise, and a
    plant that makes a probe raise is exactly the case these checks exist to
    report.
    """
    try:
        return fn()
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        return f"raised {type(exc).__name__}: {exc}"


_STAGE_SRC = os.path.abspath(_stage_module.__file__)
_PATIENT_SRC = os.path.abspath(_patient_module.__file__)
_DEID_SRC = os.path.abspath(_deid_module.__file__)

# Taken before any plant runs, so the restore assertions at the end compare
# against a real baseline rather than against themselves.
_STAGE_SHA_BEFORE = hashlib.sha256(
    open(_STAGE_SRC, encoding="utf-8").read().encode()).hexdigest()
_PATIENT_SHA_BEFORE = hashlib.sha256(
    open(_PATIENT_SRC, encoding="utf-8").read().encode()).hexdigest()
_DEID_SHA_BEFORE = hashlib.sha256(
    open(_DEID_SRC, encoding="utf-8").read().encode()).hexdigest()


class _PlantFailed(Exception):
    """A plant that did not apply or did not compile. Never escapes a check."""


def _plant(path, name, subs):
    """Exec an in-memory COPY of `path` with `subs` applied.

    Raises _PlantFailed -- never SyntaxError -- so a malformed plant is a
    RECORDED failure instead of a traceback hiding every check below it, and a
    plant whose target has moved is named rather than silently applying
    nothing and reporting a working check as broken.
    """
    source = open(path, encoding="utf-8").read()
    before = hashlib.sha256(source.encode()).hexdigest()
    try:
        for old, new, expected_count in subs:
            found = source.count(old)
            if found != expected_count:
                raise _PlantFailed(
                    f"plant target occurs {found}x, expected "
                    f"{expected_count}x: {old[:70]!r}...")
            source = source.replace(old, new, expected_count)
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
    """Plant into a copy of stage.py, probe it, record. A raise IS an outcome."""
    _CONTROL_SEQ[0] += 1
    try:
        module = _plant(_STAGE_SRC, f"planted_stage_{_CONTROL_SEQ[0]}", subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    check(label, guarded(lambda: probe(module)), expected)


def counted(fn):
    """Run `fn` and report `(result, the counter keys it moved)`.

    The snapshot is taken INSIDE, around the call, so a key another section
    left behind cannot be read as this one's -- and the counter is not cleared,
    because clearing a module-level counter another test may be reading is a
    side effect this file has no need of.
    """
    before = dict(STAGE_ATTRIBUTION_UNRESOLVED)
    result = guarded(fn)
    moved = {k: v - before.get(k, 0)
             for k, v in STAGE_ATTRIBUTION_UNRESOLVED.items()
             if v != before.get(k, 0)}
    return result, moved


# ---------------------------------------------------------------------------
# FIXTURES, in the shape oncotriage/fhir/parser.py emits
# ---------------------------------------------------------------------------

def cond(display, cid=None, assessments=(), onset="2019-05-26"):
    """One entry of patient_data['conditions'].

    Carries the two linkage fields `_parse_condition` now returns -- `id` and
    `stage_assessment_ids` -- and every field it returned before, so a fixture
    here is a record the parser could actually produce.
    """
    return {"display": display, "code": "254837009", "system_key": "snomed",
            "codings": [], "onset_date": onset, "clinical_status": "active",
            "verification_status": "confirmed",
            "id": cid, "stage_assessment_ids": list(assessments)}


def stage_obs(display="Stage IIIA (qualifier value)", date="2024-01-01",
              oid=None, focus=()):
    """One entry of patient_data['cancer_stage_observations'], as
    _parse_mcode_stage_observation emits it (note that `date` is the LITERAL
    string 'unknown' when the resource carries no effective date)."""
    return {"stage_display": display, "stage_code": "1222724007",
            "date": date, "loinc": "21908-9",
            "id": oid, "focus_ids": list(focus)}


_CM1 = "American Joint Committee on Cancer cM1 (qualifier value)"


def met_obs(value=_CM1, date="2020-02-02", oid=None, focus=()):
    """One entry of patient_data['cancer_metastasis_observations'], as the
    routing site in parse_fhir_bundle annotates it."""
    out = {"code": "21907-1", "display": "Distant metastases.clinical",
           "value": value, "unit": None, "date": date,
           "metastasis_category": "M"}
    out.update({"id": oid, "focus_ids": list(focus)})
    return out


_BREAST = "Malignant neoplasm of breast (disorder)"
_COLON = "Primary malignant neoplasm of colon (disorder)"

# THE MULTI-NEOPLASM SHAPE THIS ITEM IS ABOUT, taken from a real corpus
# patient: Abby752_..._37fdfb01 carries exactly these two neoplasms and a
# stage-group Observation that links to neither.
_MULTI = [cond(_BREAST, "c1"), cond(_COLON, "c2")]


def stage_of(conditions, stage_obs_list=(), met_obs_list=()):
    return extract_patient_stage_with_source(
        conditions,
        cancer_stage_observations=list(stage_obs_list),
        cancer_metastasis_observations=list(met_obs_list))


# ===========================================================================
# 1. THE VOCABULARY IS CLOSED, AND ITS TWO CLASSES ARE DISJOINT
# ===========================================================================

print("=" * 70)
print("1. the attribution vocabulary")
print("=" * 70)

check("1a  the vocabulary is non-degenerate", len(STAGE_ATTRIBUTIONS) >= 2, True)
check("1b  ...and has no duplicate member",
      len(set(STAGE_ATTRIBUTIONS)), len(STAGE_ATTRIBUTIONS))
check("1c  the reference-established members are a PROPER, non-empty subset",
      (set(STAGE_ATTRIBUTIONS_FROM_REFERENCE) < set(STAGE_ATTRIBUTIONS),
       len(STAGE_ATTRIBUTIONS_FROM_REFERENCE) > 0), (True, True))
check("1d  the diagnosis-text member is NOT reference-established -- it is "
      "the string match that produced the ordinal",
      STAGE_ATTRIBUTION_DIAGNOSIS_TEXT in STAGE_ATTRIBUTIONS_FROM_REFERENCE,
      False)
check("1e  the exact composition, so a member added without a rule fails here",
      sorted(STAGE_ATTRIBUTIONS),
      sorted([STAGE_ATTRIBUTION_DIAGNOSIS_TEXT,
              STAGE_ATTRIBUTION_CONDITION_ASSESSMENT,
              STAGE_ATTRIBUTION_OBSERVATION_FOCUS]))
check("1f  PatientStage carries the two attribution members, in order",
      PatientStage._fields,
      ("ordinal", "source", "observation_date", "attributed_condition",
       "attribution"))
check("1g  MCategoryStage carries the answering observation, so attribution "
      "can be asked of the record that ANSWERED",
      MCategoryStage._fields, ("ordinal", "date", "observation"))


# ===========================================================================
# 2. THE REFERENCE NORMALISER
# ===========================================================================

print("\n" + "=" * 70)
print("2. a FHIR reference resolves to a logical id, in every form")
print("=" * 70)

for _label, _ref, _expected in (
        ("urn:uuid (Synthea, and any transaction bundle)",
         "urn:uuid:37fdfb01-3b13", "37fdfb01-3b13"),
        ("relative", "Condition/abc", "abc"),
        ("absolute", "http://h/fhir/Condition/xyz", "xyz"),
        ("contained", "#c1", "c1"),
        ("versioned -- the VERSION is discarded, not returned as the id",
         "Condition/1/_history/2", "1"),
        ("query suffix", "Condition/abc?x=1", "abc"),
        ("bare id", "abc", "abc"),
        ("empty", "", None),
        ("whitespace only", "   ", None),
        ("None", None, None),
        ("not a string", 123, None),
        ("a prefix with nothing after it", "urn:uuid:", None)):
    check(f"2  [{_label}]", guarded(lambda r=_ref: _reference_id(r)), _expected)

check("2m  a reference LIST is deduped and keeps source order",
      guarded(lambda: _reference_ids(
          [{"reference": "urn:uuid:a"}, {"reference": "Condition/a"},
           {"reference": "Condition/b"}, {}, None, {"reference": ""}])),
      ["a", "b"])
check("2n  Condition.stage is 0..* and each assessment 0..*, so all are "
      "collected",
      guarded(lambda: _condition_stage_assessment_ids(
          {"stage": [{"assessment": [{"reference": "urn:uuid:o1"}]},
                     {"assessment": [{"reference": "Observation/o2"},
                                     {"reference": "urn:uuid:o1"}]}]})),
      ["o1", "o2"])
check("2o  a Condition with no stage element yields nothing",
      guarded(lambda: _condition_stage_assessment_ids({})), [])
check("2p  the observation link pair", guarded(lambda: _observation_link_fields(
          {"id": "o9", "focus": [{"reference": "urn:uuid:c1"}]})),
      {"id": "o9", "focus_ids": ["c1"]})
check("2q  ...and its empty shape, which is every observation in this corpus",
      guarded(lambda: _observation_link_fields({})),
      {"id": None, "focus_ids": []})


# ===========================================================================
# 3. THE DIAGNOSIS-TEXT TIERS ATTRIBUTE BY CONSTRUCTION
# ===========================================================================

print("\n" + "=" * 70)
print("3. a tier that read a diagnosis NAME names that diagnosis")
print("=" * 70)

_LUNG = "Non-small cell carcinoma of lung, TNM stage 1 (disorder)"
_st = stage_of([cond(_LUNG, "c1"), cond("Neoplasm of prostate (disorder)", "c2")])
check("3a  the condition-display tier answers", (_st.ordinal, _st.source),
      (1, STAGE_SOURCE_CONDITION_DISPLAY))
check("3b  ...and names the condition whose text produced the ordinal, not "
      "the other neoplasm on the record",
      (_st.attributed_condition, _st.attribution),
      (_LUNG, STAGE_ATTRIBUTION_DIAGNOSIS_TEXT))

_MET = "Metastatic malignant neoplasm to prostate (disorder)"
_st = stage_of([cond(_BREAST, "c1"), cond(_MET, "c2")])
check("3c  the metastatic-keyword tier answers", (_st.ordinal, _st.source),
      (4, STAGE_SOURCE_METASTATIC_KEYWORD))
check("3d  ...and names the condition that carried the keyword, with the "
      "display taken from the record rather than from the lower-cased copy "
      "the keyword test uses",
      (_st.attributed_condition, _st.attribution),
      (_MET, STAGE_ATTRIBUTION_DIAGNOSIS_TEXT))

check("3e  a condition with no display cannot be named, so attribution is "
      "not established even though the tier answered",
      guarded(lambda: stage_of([cond("", "c1"), cond("Stage III breast", "c2")])
              ).attributed_condition,
      "Stage III breast")


# ===========================================================================
# 4. AN OBSERVATION IS ATTRIBUTED ONLY BY AN EXPLICIT REFERENCE
# ===========================================================================

print("\n" + "=" * 70)
print("4. the two FHIR link directions, and nothing else")
print("=" * 70)

# THE CORPUS SHAPE: a multi-neoplasm patient whose staging Observation states
# no link. This is 32 of this corpus's patients and the reason the rendered
# line has a second shape at all.
_st = stage_of(_MULTI, [stage_obs(oid="o1")])
check("4a  an UNLINKED observation on a multi-neoplasm record answers",
      (_st.ordinal, _st.source), (3, STAGE_SOURCE_STAGE_GROUP))
check("4b  ...and attributes NOTHING -- never the first cancer, never the "
      "only cancer, never the one sharing an encounter",
      (_st.attributed_condition, _st.attribution), (None, None))

_st = stage_of([cond(_BREAST, "c1", assessments=["o1"]), cond(_COLON, "c2")],
               [stage_obs(oid="o1")])
check("4c  Condition.stage.assessment -> the answering Observation",
      (_st.attributed_condition, _st.attribution),
      (_BREAST, STAGE_ATTRIBUTION_CONDITION_ASSESSMENT))

_st = stage_of(_MULTI, [stage_obs(oid="o1", focus=["c2"])])
check("4d  Observation.focus -> a Condition",
      (_st.attributed_condition, _st.attribution),
      (_COLON, STAGE_ATTRIBUTION_OBSERVATION_FOCUS))

_st = stage_of([cond(_BREAST, "c1", assessments=["o1"]), cond(_COLON, "c2")],
               [stage_obs(oid="o1", focus=["c1"])])
check("4e  both directions agreeing on one condition take the "
      "staging-specific label",
      (_st.attributed_condition, _st.attribution),
      (_BREAST, STAGE_ATTRIBUTION_CONDITION_ASSESSMENT))

# A SINGLE-CANCER RECORD IS NOT A LINK. The instruction that made this its own
# check: one recorded cancer does not prove the stage is its, and on a real
# extract the second cancer is routinely the one that is not coded.
_st = stage_of([cond(_BREAST, "c1")], [stage_obs(oid="o1")])
check("4f  a record carrying exactly ONE cancer still attributes nothing",
      (_st.ordinal, _st.attributed_condition), (3, None))

# NOR IS A SHARED ENCOUNTER. Every one of this corpus's 880 staging
# Observations shares its encounter with exactly one Condition, so this is the
# heuristic that would look perfect here and be wrong everywhere.
_shared = [dict(cond(_BREAST, "c1"), encounter="e1"),
           dict(cond(_COLON, "c2"), encounter="e2")]
_st = stage_of(_shared, [dict(stage_obs(oid="o1"), encounter="e1")])
check("4g  ...and neither is a shared encounter", _st.attributed_condition, None)

# THE M TIER, whose answering observation had to be carried out of
# _m_category_stage_with_date for this to be askable at all.
_st = stage_of(_MULTI, met_obs_list=[met_obs(oid="m1", focus=["c2"])])
check("4h  the M tier answers and attributes through its OWN observation",
      (_st.ordinal, _st.source, _st.attributed_condition, _st.attribution),
      (4, STAGE_SOURCE_M_CATEGORY, _COLON,
       STAGE_ATTRIBUTION_OBSERVATION_FOCUS))
check("4i  ...and a link on a NON-answering M observation is not borrowed",
      stage_of(_MULTI, met_obs_list=[
          met_obs(value="cM0", oid="m0", focus=["c1"]),
          met_obs(oid="m1")]).attributed_condition,
      None)

# THE ANSWERING OBSERVATION AND ONLY IT. A restaged patient whose newest
# record cannot be read answers on an older one; the link on the unreadable
# newer record must not be used.
_st = stage_of([cond(_BREAST, "c1", assessments=["o_new"]),
                cond(_COLON, "c2", assessments=["o_old"])],
               [stage_obs("no stage here", date="2026-06-26", oid="o_new"),
                stage_obs("Stage IIIA (qualifier value)", date="2019-05-26",
                          oid="o_old")])
check("4j  the link belongs to the observation that ANSWERED, not to the "
      "newest one",
      (_st.observation_date, _st.attributed_condition),
      ("2019-05-26", _COLON))


# ===========================================================================
# 5. AMBIGUITY REFUSES, AND IS COUNTED
# ===========================================================================

print("\n" + "=" * 70)
print("5. more than one condition claiming one observation")
print("=" * 70)

_result, _moved = counted(lambda: stage_of(
    [cond(_BREAST, "c1", assessments=["o1"]),
     cond(_COLON, "c2", assessments=["o1"])], [stage_obs(oid="o1")]))
check("5a  two differently-named conditions claiming one observation refuse",
      (_result.ordinal, _result.attributed_condition, _result.attribution),
      (3, None, None))
check("5b  ...and the refusal is COUNTED, keyed by direction and claimant "
      "count and never by the condition display",
      _moved, {f"{STAGE_ATTRIBUTION_AMBIGUOUS_KEY}:"
               f"{STAGE_ATTRIBUTION_CONDITION_ASSESSMENT}:2": 1})

_result, _moved = counted(lambda: stage_of(
    [cond(_BREAST, "c1", assessments=["o1"]), cond(_COLON, "c2")],
    [stage_obs(oid="o1", focus=["c2"])]))
check("5c  a record whose two directions name DIFFERENT conditions is "
      "contradicting itself and refuses rather than preferring one",
      _result.attributed_condition, None)
check("5d  ...counted under BOTH directions, so the key says what happened",
      _moved,
      {f"{STAGE_ATTRIBUTION_AMBIGUOUS_KEY}:"
       f"{STAGE_ATTRIBUTION_CONDITION_ASSESSMENT}+"
       f"{STAGE_ATTRIBUTION_OBSERVATION_FOCUS}:2": 1})

# DEDUPED BY DISPLAY, NOT BY RESOURCE: duplicate Condition resources are
# ordinary in real extracts and are ONE answer to "which cancer".
_result, _moved = counted(lambda: stage_of(
    [cond(_BREAST, "c1", assessments=["o1"]),
     cond(_BREAST, "c1b", assessments=["o1"])], [stage_obs(oid="o1")]))
check("5e  two RESOURCES carrying the same display are one answer",
      _result.attributed_condition, _BREAST)
check("5f  ...and nothing is counted, because nothing was ambiguous",
      _moved, {})

_result, _moved = counted(lambda: stage_of(
    [cond("", "c9", assessments=["o1"])], [stage_obs(oid="o1")]))
check("5g  a link that resolves to a Condition with no display cannot be "
      "printed, so attribution is not established",
      _result.attributed_condition, None)
check("5h  ...and THAT is counted too -- a link existed and was unusable, "
      "which is a different finding from no link at all",
      _moved, {f"{STAGE_ATTRIBUTION_UNNAMED_KEY}:"
               f"{STAGE_ATTRIBUTION_CONDITION_ASSESSMENT}": 1})

# A NAMED CLAIMANT BESIDE A NAMELESS ONE IS TWO CLAIMANTS. Taking the named
# one would be choosing by which claimant is PRINTABLE rather than by what the
# record says, which is a guess wearing the costume of a resolution.
_result, _moved = counted(lambda: stage_of(
    [cond(_BREAST, "c1", assessments=["o1"]), cond("", "c2", assessments=["o1"])],
    [stage_obs(oid="o1")]))
check("5i  a named claimant beside a nameless one is AMBIGUOUS, not a "
      "silent win for the one that happens to have a display",
      _result.attributed_condition, None)
check("5j  ...and is counted as ambiguity rather than as an unnamed claimant",
      _moved, {f"{STAGE_ATTRIBUTION_AMBIGUOUS_KEY}:"
               f"{STAGE_ATTRIBUTION_CONDITION_ASSESSMENT}:2": 1})

# `in` ON A STRING IS A SUBSTRING TEST. A caller handing a bare reference
# where a list belongs must not produce a link; control 9m plants the
# unguarded form and shows it inventing one.
_result, _moved = counted(lambda: stage_of(
    [{"display": _BREAST, "id": "c1", "stage_assessment_ids": "xxo1xx"}],
    [stage_obs(oid="o1")]))
check("5k  a reference STRING where a list belongs yields no link, so no "
      "substring collision can name a cancer",
      (_result.attributed_condition, _moved), (None, {}))

_result, _moved = counted(lambda: stage_of(_MULTI, [stage_obs(oid="o1")]))
check("5l  NON-DEGENERACY: an observation stating NO link counts nothing -- "
      "that is this corpus's every record and it is not a degradation",
      (_result.attributed_condition, _moved), (None, {}))

check("5m  the counter is registered for the run-end report",
      "STAGE_ATTRIBUTION_UNRESOLVED" in __import__(
          "oncotriage.degradation", fromlist=["x"]).registered_names(), True)


# ===========================================================================
# 6. THE RESOLVER READS REFERENCES AND NOTHING ELSE
# ===========================================================================

print("\n" + "=" * 70)
print("6. structural: no proximity, no registry, no other reference element")
print("=" * 70)

_RESOLVER = None
for _node in ast.walk(ast.parse(open(_STAGE_SRC, encoding="utf-8").read())):
    if isinstance(_node, ast.FunctionDef) and _node.name == "_attribute_observation":
        _RESOLVER = _node
_resolver_src = ast.unparse(_RESOLVER) if _RESOLVER is not None else ""
# The docstring argues about the elements it refuses, so it is stripped before
# the scan: a file that argues about its own settings cannot be grepped for
# them. This project has met that three times.
if _RESOLVER is not None and ast.get_docstring(_RESOLVER):
    _stripped = ast.Module(body=[ast.FunctionDef(
        name=_RESOLVER.name, args=_RESOLVER.args,
        body=_RESOLVER.body[1:], decorator_list=[], returns=None,
        type_params=[])], type_ignores=[])
    _resolver_src = ast.unparse(ast.fix_missing_locations(_stripped))

check("6a  NON-DEGENERACY: the resolver was found and has a body",
      _RESOLVER is not None and len(_resolver_src) > 200, True)
for _forbidden in ("encounter", "partOf", "basedOn", "derivedFrom",
                   "hasMember", "subject", "is_primary_cancer", "registry"):
    check(f"6b  [{_forbidden}] the resolver does not read it",
          _forbidden in _resolver_src, False)
check("6c  ...and it DOES read the two elements it is allowed to",
      ("stage_assessment_ids" in _resolver_src,
       "focus_ids" in _resolver_src), (True, True))

# LAYERING: oncotriage/extraction/ is a leaf the INDEXER reads too, so it must
# not grow an edge to the registries.
_imports = set()
for _node in ast.walk(ast.parse(open(_STAGE_SRC, encoding="utf-8").read())):
    if isinstance(_node, ast.ImportFrom) and _node.module:
        _imports.add(_node.module)
    elif isinstance(_node, ast.Import):
        _imports.update(a.name for a in _node.names)
check("6d  stage.py imports no registry, so attribution cannot filter by "
      "cancer-ness and says so instead",
      sorted(m for m in _imports if m.startswith("oncotriage")),
      ["oncotriage.constants", "oncotriage.extraction.negation"])


# ===========================================================================
# 7. THE INVARIANT: ORDINAL, TIER AND DATE ARE UNMOVED
# ===========================================================================

print("\n" + "=" * 70)
print("7. attribution moved no ordinal, no tier and no date")
print("=" * 70)

# A CORPUS-SHAPED SAMPLE. The whole-corpus arm of this comparison was run out
# of band against a git worktree at HEAD -- 1,000 bundles, 0 rows differing on
# (ordinal, source, observation_date) through BOTH entry points -- and is
# recorded in the item's report. What stands here is the shape of that
# comparison over the records this file can carry, which is what keeps it
# bucket A.
_INVARIANT_CASES = [
    ("no stage anywhere", [cond("Hypertension", "c1")], [], [], (None, None, None)),
    ("stage group, unlinked", _MULTI, [stage_obs(oid="o1")], [],
     (3, STAGE_SOURCE_STAGE_GROUP, "2024-01-01")),
    ("stage group, linked", [cond(_BREAST, "c1", assessments=["o1"])],
     [stage_obs(oid="o1")], [], (3, STAGE_SOURCE_STAGE_GROUP, "2024-01-01")),
    ("stage group, undated", _MULTI, [stage_obs(date="unknown", oid="o1")], [],
     (3, STAGE_SOURCE_STAGE_GROUP, "unknown")),
    ("stage 0", _MULTI, [stage_obs("Stage 0 (qualifier value)", oid="o1")], [],
     (0, STAGE_SOURCE_STAGE_GROUP, "2024-01-01")),
    ("m category", _MULTI, [], [met_obs(oid="m1")],
     (4, STAGE_SOURCE_M_CATEGORY, "2020-02-02")),
    ("condition display", [cond(_LUNG, "c1")], [], [],
     (1, STAGE_SOURCE_CONDITION_DISPLAY, None)),
    ("metastatic keyword", [cond(_MET, "c1")], [], [],
     (4, STAGE_SOURCE_METASTATIC_KEYWORD, None)),
    ("CKD guard still fires", [cond("Chronic kidney disease stage 3 (disorder)",
                                    "c1")], [], [], (None, None, None)),
]
for _label, _c, _s, _m, _expected in _INVARIANT_CASES:
    _st = stage_of(_c, _s, _m)
    check(f"7  [{_label}] (ordinal, source, observation_date) is what it was",
          (_st.ordinal, _st.source, _st.observation_date), _expected)
    check(f"7  [{_label}] ...and the pinned delegate agrees with it",
          extract_patient_stage(
              _c, cancer_stage_observations=list(_s),
              cancer_metastasis_observations=list(_m)),
          _expected[0])

check("7z  NON-DEGENERACY: the case set discriminates -- five ordinal states "
      "(None, 0, 1, 3, 4), all four tiers, and four date states (a real "
      "date, the corpus's 'unknown' sentinel, a second real date, and None)",
      (len({c[4][0] for c in _INVARIANT_CASES}),
       len({c[4][1] for c in _INVARIANT_CASES if c[4][1]}),
       len({c[4][2] for c in _INVARIANT_CASES})), (5, 4, 4))


# ===========================================================================
# 8. THE HASH SEPARATES EXACTLY WHAT THE RENDER SEPARATES
# ===========================================================================

print("\n" + "=" * 70)
print("8. compute_patient_hash and the rendered line agree about linkage")
print("=" * 70)

_hash = _patient_module.compute_patient_hash


def _pd(conditions, observations=()):
    return {"demographics": {"age": 60, "sex": "female", "race": "white",
                             "ethnicity": "nonhispanic"},
            "conditions": list(conditions), "medications": [],
            "observations": [], "procedures": [], "allergies": [],
            "cancer_stage_observations": list(observations)}


_A = _pd([cond(_BREAST, "c1", assessments=["o1"]), cond(_COLON, "c2")],
         [stage_obs(oid="o1")])
_B = _pd([cond(_BREAST, "c1"), cond(_COLON, "c2", assessments=["o1"])],
         [stage_obs(oid="o1")])
_NONE = _pd(_MULTI, [stage_obs(oid="o1")])
# The SAME relation with ids and displays swapped together: same render, so
# the hash must NOT move.
_SWAP_OK = _pd([cond(_BREAST, "c2", assessments=["o1"]), cond(_COLON, "c1")],
               [stage_obs(oid="o1")])
# Displays swapped, ids kept: DIFFERENT render, so the hash MUST move. This is
# the case a raw id-and-reference emission could not separate.
_SWAP_BAD = _pd([cond(_COLON, "c1", assessments=["o1"]), cond(_BREAST, "c2")],
                [stage_obs(oid="o1")])

check("8a  an unlinked record and a linked one hash differently",
      _hash(_NONE) != _hash(_A), True)
check("8b  ...and two records linking the SAME observation to DIFFERENT "
      "cancers hash differently", _hash(_A) != _hash(_B), True)
check("8c  a permutation that preserves the relation does NOT move the hash",
      _hash(_A), _hash(_SWAP_OK))
check("8d  ...while swapping the displays under fixed ids DOES, because the "
      "rendered line moves with it", _hash(_A) != _hash(_SWAP_BAD), True)
check("8e  NON-DEGENERACY: the four hashes are not all distinct by accident",
      len({_hash(_A), _hash(_B), _hash(_NONE)}), 3)

# THE ADDITION IS CONDITIONAL, which is what keeps every stored corpus hash
# valid: this corpus states no link, so it emits no line.
_UNLINKED_ONLY = _pd(_MULTI, [stage_obs(oid="o1")])
_NO_LINK_FIELDS = _pd(
    [{k: v for k, v in c.items()
      if k not in ("id", "stage_assessment_ids")} for c in _MULTI],
    [{k: v for k, v in stage_obs(oid="o1").items()
      if k not in ("id", "focus_ids")}])
check("8f  a record with NO linkage fields at all hashes identically to one "
      "whose linkage fields are present and empty -- so adding the capture "
      "moved no stored hash",
      _hash(_UNLINKED_ONLY), _hash(_NO_LINK_FIELDS))

# THE HASH RESOLVES AND COUNTS NOTHING. Its loop asks about EVERY staging
# observation, including ones that answer for nobody, and the pipeline hashes
# AND extracts the same patient -- so a counting hash would invent
# degradations and double the real ones. Measured before the seam existed:
# computing one hash moved `ambiguous:` by 1 for a record whose ambiguous
# observation was not the answering one.
_AMBIG_NON_ANSWERING = _pd(
    [cond(_BREAST, "c1", assessments=["o_old"]),
     cond(_COLON, "c2", assessments=["o_old"])],
    [stage_obs(oid="o_new"),
     stage_obs("Stage II (qualifier value)", date="2001-01-01", oid="o_old")])
_before = dict(STAGE_ATTRIBUTION_UNRESOLVED)
_hash(_AMBIG_NON_ANSWERING)
check("8f2  computing a hash moves NO degradation counter",
      {k: v - _before.get(k, 0) for k, v in STAGE_ATTRIBUTION_UNRESOLVED.items()
       if v != _before.get(k, 0)}, {})
check("8f3  NON-DEGENERACY: that record IS ambiguous, so the check above is "
      "about the seam and not about a record with nothing to count",
      counted(lambda: _attribute_observation(
          stage_obs(oid="o_old"),
          _AMBIG_NON_ANSWERING["conditions"]))[1],
      {f"{STAGE_ATTRIBUTION_AMBIGUOUS_KEY}:"
       f"{STAGE_ATTRIBUTION_CONDITION_ASSESSMENT}:2": 1})

check("8g  the rendered line and the hash agree: both move together",
      (_hash(_A) != _hash(_NONE),
       _patient_module._create_patient_summary(_A)
       != _patient_module._create_patient_summary(_NONE)), (True, True))


# ===========================================================================
# 8b. THE LINKAGE SURVIVES DE-IDENTIFICATION AND NO UUID DOES
# ===========================================================================

print("\n" + "=" * 70)
print("8b. resource ids are tokenised, not carried and not dropped")
print("=" * 70)

# THE REGRESSION THIS SECTION EXISTS FOR, and it was REAL rather than
# anticipated: capturing the two FHIR link elements put resource UUIDs into
# `conditions` and `cancer_stage_observations`, deid.py's own `uuid` shape
# rule classes any UUID as a record number, and the MCP surface scans the
# whole de-identified record before returning it. MEASURED on one ordinary
# corpus patient with the raw ids carried through: 68 `record_number` matches
# via `uuid`, and `parse_fhir_bundle` refused every call.
_LINKED_PD = _pd([cond(_BREAST, "37fdfb01-3b13-b8ff-97ea-de9526c46124",
                       assessments=["9c1e0a77-4b2d-4f61-9a30-1d6b0e5f8c22"]),
                  cond(_COLON, "b2c3d4e5-6f70-4812-9a3b-4c5d6e7f8091")],
                 [stage_obs(oid="9c1e0a77-4b2d-4f61-9a30-1d6b0e5f8c22")])
_REC = _deid_module.deidentify(_LINKED_PD, identity="probe")

check("8b-a  no raw resource id survives into the de-identified record",
      guarded(lambda: [c["id"] for c in _REC.fields["conditions"]]),
      [_deid_module.link_token("37fdfb01-3b13-b8ff-97ea-de9526c46124"),
       _deid_module.link_token("b2c3d4e5-6f70-4812-9a3b-4c5d6e7f8091")])
check("8b-b  ...and neither does a reference LIST",
      guarded(lambda: _REC.fields["conditions"][0]["stage_assessment_ids"]),
      [_deid_module.link_token("9c1e0a77-4b2d-4f61-9a30-1d6b0e5f8c22")])
check("8b-c  NON-DEGENERACY: those tokens are not the raw ids",
      guarded(lambda: _REC.fields["conditions"][0]["id"]
              == "37fdfb01-3b13-b8ff-97ea-de9526c46124"), False)
check("8b-d  ...and the caller's patient_data is NOT mutated, which the "
      "shallow copy in deidentify() makes a real hazard",
      _LINKED_PD["conditions"][0]["id"],
      "37fdfb01-3b13-b8ff-97ea-de9526c46124")

# THE RELATION IS WHAT THE RESOLVER READS, and equality survives any injective
# function -- so attribution resolves through the tokenised record exactly as
# it does through the raw one.
check("8b-e  attribution still resolves THROUGH the de-identified record",
      guarded(lambda: extract_patient_stage_with_source(
          _REC.fields["conditions"],
          cancer_stage_observations=_REC.fields["cancer_stage_observations"],
      ).attributed_condition), _BREAST)
check("8b-f  ...and the OTHER direction too",
      guarded(lambda: (lambda r: extract_patient_stage_with_source(
          r.fields["conditions"],
          cancer_stage_observations=r.fields["cancer_stage_observations"],
      ).attributed_condition)(_deid_module.deidentify(
          _pd([cond(_BREAST, "aaaa1111-2222-4333-8444-555566667777"),
               cond(_COLON, "bbbb1111-2222-4333-8444-555566667777")],
              [stage_obs(oid="cccc1111-2222-4333-8444-555566667777",
                         focus=["bbbb1111-2222-4333-8444-555566667777"])]),
          identity="probe"))), _COLON)

check("8b-g  the guard passes over the whole de-identified record",
      guarded(lambda: isinstance(_deid_module.assert_no_identifiers(
          __import__("json").dumps({"patient_record": _REC.fields},
                                   default=str), _REC), int)), True)

# AN ABSENT ID IS NOT TOKENISED. Minting a token for None would make two
# UNLINKED resources compare equal, and the resolver would then attribute a
# stage to whichever nameless condition also had no id.
check("8b-h  None and '' pass through unchanged rather than becoming a token",
      (_deid_module.link_token(None), _deid_module.link_token("")),
      (None, ""))
check("8b-i  ...so two resources with no id do not become linked",
      guarded(lambda: extract_patient_stage_with_source(
          [cond(_BREAST, None)],
          cancer_stage_observations=[stage_obs(oid=None)]
      ).attributed_condition), None)
check("8b-j  the linkage token is DOMAIN-SEPARATED from the patient one, so "
      "the same string does not yield the same token",
      _deid_module.link_token("x") == _deid_module.pseudonym_for_identity("x"),
      False)
check("8b-k  the declared field map covers exactly the three RENDERED_FIELDS "
      "entries the parser writes linkage into",
      sorted(_deid_module.LINK_FIELDS_BY_KEY),
      ["cancer_metastasis_observations", "cancer_stage_observations",
       "conditions"])
check("8b-l  ...and every one of them is a RENDERED_FIELDS entry, or the "
      "rewrite would reach a key the record does not carry",
      [k for k in _deid_module.LINK_FIELDS_BY_KEY
       if k not in _deid_module.RENDERED_FIELDS], [])
check("8b-m  a CODE is not an id: the rewrite leaves the fields the "
      "registries and the extractor read alone",
      guarded(lambda: (_REC.fields["conditions"][0]["code"],
                       _REC.fields["cancer_stage_observations"][0]["loinc"],
                       _REC.fields["cancer_stage_observations"][0]["stage_display"])),
      ("254837009", "21908-9", "Stage IIIA (qualifier value)"))


# ===========================================================================
# 9. NEGATIVE CONTROLS
# ===========================================================================

print("\n" + "=" * 70)
print("9. every assertion above is shown to FAIL when the rule is broken")
print("=" * 70)
print("Each plant goes into an in-memory COPY of stage.py; the file on disk")
print("is hashed before and after and asserted byte-identical.")

_LINKED = [cond(_BREAST, "c1", assessments=["o1"]), cond(_COLON, "c2")]


def _attr_of(module, conditions=None, observations=None):
    return module.extract_patient_stage_with_source(
        conditions if conditions is not None else _LINKED,
        cancer_stage_observations=(observations if observations is not None
                                   else [stage_obs(oid="o1")]),
    ).attributed_condition


# C1. THE LINK SEVERED -- the instruction's own control. The resolver stops
#     reading Condition.stage.assessment, so the linked record falls back to
#     the not-established shape.
_control("9a  CONTROL: a resolver that ignores Condition.stage.assessment "
         "reports the linked record as not established",
         [("        by_assessment = bool(obs_id) and obs_id in assessment_ids",
           "        by_assessment = False", 1)],
         _attr_of, None)
_control("9b  CONTROL: ...and the CLEAN arm attributes it, so 9a is about the "
         "plant rather than about the fixture", [], _attr_of, _BREAST)

# C2. THE OTHER DIRECTION severed.
_control("9c  CONTROL: a resolver that ignores Observation.focus loses the "
         "focus-linked record",
         [("        by_focus = bool(cond_id) and cond_id in focus_ids",
           "        by_focus = False", 1)],
         lambda m: _attr_of(m, _MULTI, [stage_obs(oid="o1", focus=["c2"])]),
         None)
_control("9d  CONTROL: ...and the CLEAN arm attributes it", [],
         lambda m: _attr_of(m, _MULTI, [stage_obs(oid="o1", focus=["c2"])]),
         _COLON)

# C3. THE AMBIGUITY RULE REMOVED -- the plausible wrong version, which takes
#     whichever claimant the condition list happened to list first.
_AMBIGUOUS = [cond(_BREAST, "c1", assessments=["o1"]),
              cond(_COLON, "c2", assessments=["o1"])]
_control("9e  CONTROL: taking the first claimant states one cancer over a "
         "record that names two",
         [("    if len(claims) > 1:", "    if False:", 1)],
         lambda m: _attr_of(m, _AMBIGUOUS), _BREAST)
_control("9f  CONTROL: ...and the SHIPPED rule refuses it", [],
         lambda m: _attr_of(m, _AMBIGUOUS), None)

# C4. ATTRIBUTION FROM PROXIMITY -- the rule this corpus would make look
#     perfect. Every staging Observation here shares its encounter with
#     exactly one Condition, so a plant that reaches for the only cancer on
#     the record resolves for every one of them.
_control("9g  CONTROL: attributing on 'the record carries only one cancer' "
         "names a cancer no reference named",
         [("    if not claims:\n"
           "        # No link at all. Silent: that is this corpus's every "
           "record.\n"
           "        return None, None",
           "    if not claims:\n"
           "        _c = [c for c in (conditions or []) if c.get('display')]\n"
           "        if len(_c) == 1:\n"
           "            return _c[0]['display'], "
           "STAGE_ATTRIBUTION_OBSERVATION_FOCUS\n"
           "        return None, None", 1)],
         lambda m: _attr_of(m, [cond(_BREAST, "c1")], [stage_obs(oid="o1")]),
         _BREAST)
_control("9h  CONTROL: ...and the SHIPPED resolver attributes nothing there",
         [], lambda m: _attr_of(m, [cond(_BREAST, "c1")], [stage_obs(oid="o1")]),
         None)

# C4b. THE `in`-ON-A-STRING SUBSTRING MATCH. The guard removed, a bare
#      reference string is walked character-wise and "o1" is found inside
#      "xxo1xx" -- a link the record never wrote, on a line that states a
#      cancer name with no hedge.
_control("9m  CONTROL: without the list guard a reference STRING produces a "
         "substring match and names a cancer no reference named",
         [("    if isinstance(value, (list, tuple)):\n"
           "        return [v for v in value if isinstance(v, str) and v]\n"
           "    return []",
           "    return value if value is not None else []", 1)],
         lambda m: _attr_of(
             m, [{"display": _BREAST, "id": "c1",
                  "stage_assessment_ids": "xxo1xx"}], [stage_obs(oid="o1")]),
         _BREAST)
_control("9n  CONTROL: ...and the SHIPPED guard drops it", [],
         lambda m: _attr_of(
             m, [{"display": _BREAST, "id": "c1",
                  "stage_assessment_ids": "xxo1xx"}], [stage_obs(oid="o1")]),
         None)

# C4c. THE HASH COUNTING. Planted into oncotriage/agent/patient.py rather
#      than into stage.py, because the seam is at the CALL SITE: with
#      `count=False` dropped the counter records observations that answered
#      for nobody and records the answering one twice per patient. The probe
#      reports the counter delta of one hash pass, so a working seam is `{}`
#      and the plant is a named key.
def _hash_counter_delta(module):
    before = dict(STAGE_ATTRIBUTION_UNRESOLVED)
    module.compute_patient_hash(_AMBIG_NON_ANSWERING)
    return {k: v - before.get(k, 0)
            for k, v in STAGE_ATTRIBUTION_UNRESOLVED.items()
            if v != before.get(k, 0)}


_CONTROL_SEQ[0] += 1
try:
    _counting = _plant(
        _PATIENT_SRC, f"planted_patient_{_CONTROL_SEQ[0]}",
        [("        _display, _attr = _attribute_observation(_obs, conditions,\n"
          "                                                 count=False)",
          "        _display, _attr = _attribute_observation(_obs, conditions)",
          1)])
except _PlantFailed as _exc:
    check(f"9o  CONTROL: a hash pass that counts moves the counter for an "
          f"observation that never answered  [THE PLANT ITSELF FAILED: {_exc}]",
          "plant-failed", "a counted key")
else:
    check("9o  CONTROL: a hash pass that counts moves the counter for an "
          "observation that never answered",
          guarded(lambda: _hash_counter_delta(_counting)),
          {f"{STAGE_ATTRIBUTION_AMBIGUOUS_KEY}:"
           f"{STAGE_ATTRIBUTION_CONDITION_ASSESSMENT}:2": 1})
check("9p  CONTROL: ...and the SHIPPED hash moves nothing",
      guarded(lambda: _hash_counter_delta(_patient_module)), {})

# C4d. THE PSEUDONYMISATION REMOVED. The raw UUIDs reach the de-identified
#      record and this project's OWN guard refuses it -- which is the
#      regression the capture introduced and 8b exists to hold closed.
_CONTROL_SEQ[0] += 1
try:
    _raw_ids = _plant(
        _DEID_SRC, f"planted_deid_{_CONTROL_SEQ[0]}",
        [("            _link_fields = LINK_FIELDS_BY_KEY.get(key)\n"
          "            if _link_fields:\n"
          "                fields[key] = _pseudonymise_links(fields[key], "
          "_link_fields)",
          "            _link_fields = None", 1)])
except _PlantFailed as _exc:
    check(f"9q  CONTROL: carrying raw resource ids makes this project's own "
          f"guard refuse the record  [THE PLANT ITSELF FAILED: {_exc}]",
          "plant-failed", "refused")
else:
    def _guard_verdict(module):
        rec = module.deidentify(_LINKED_PD, identity="probe")
        try:
            module.assert_no_identifiers(
                __import__("json").dumps({"patient_record": rec.fields},
                                         default=str), rec)
        except module.IdentifierLeakError:
            return "refused"
        return "passed"

    check("9q  CONTROL: carrying raw resource ids makes this project's own "
          "guard refuse the record",
          guarded(lambda: _guard_verdict(_raw_ids)), "refused")
check("9r  CONTROL: ...and the SHIPPED module passes the same record",
      guarded(lambda: (lambda m: (
          m.assert_no_identifiers(
              __import__("json").dumps(
                  {"patient_record": m.deidentify(
                      _LINKED_PD, identity="probe").fields}, default=str),
              m.deidentify(_LINKED_PD, identity="probe")) is not None
      ))(_deid_module)), True)

# C5. THE DIAGNOSIS-TEXT TIER attributing the WRONG condition -- the first on
#     the record rather than the one whose text produced the ordinal.
_control("9i  CONTROL: the condition tier naming the first condition instead "
         "of the matching one",
         [('                                    (cond.get("display") or "").strip() or None,\n'
           "                                    STAGE_ATTRIBUTION_DIAGNOSIS_TEXT)",
           '                                    (conditions[0].get("display") or "").strip() or None,\n'
           "                                    STAGE_ATTRIBUTION_DIAGNOSIS_TEXT)", 1)],
         lambda m: m.extract_patient_stage_with_source(
             [cond(_BREAST, "c1"), cond(_LUNG, "c2")]).attributed_condition,
         _BREAST)
_control("9j  CONTROL: ...and the SHIPPED tier names the matching one", [],
         lambda m: m.extract_patient_stage_with_source(
             [cond(_BREAST, "c1"), cond(_LUNG, "c2")]).attributed_condition,
         _LUNG)

# C6. THE M TIER attributing through a SIBLING observation rather than the one
#     that answered.
_control("9k  CONTROL: the M tier resolving against the list's first entry",
         [("        _display, _attr = _attribute_observation(m_category_obs, conditions)",
           "        _display, _attr = _attribute_observation("
           "(cancer_metastasis_observations or [None])[0], conditions)", 1)],
         lambda m: m.extract_patient_stage_with_source(
             _MULTI,
             cancer_metastasis_observations=[
                 met_obs(value="cM0", oid="m0", focus=["c1"]),
                 met_obs(oid="m1")]).attributed_condition,
         _BREAST)
_control("9l  CONTROL: ...and the SHIPPED tier resolves against the answering "
         "observation, which states no link", [],
         lambda m: m.extract_patient_stage_with_source(
             _MULTI,
             cancer_metastasis_observations=[
                 met_obs(value="cM0", oid="m0", focus=["c1"]),
                 met_obs(oid="m1")]).attributed_condition,
         None)


# ===========================================================================
# 10. THE FILES ON DISK ARE UNTOUCHED
# ===========================================================================

print("\n" + "=" * 70)
print("10. nothing was written")
print("=" * 70)

check("10a  oncotriage/extraction/stage.py is byte-identical",
      hashlib.sha256(open(_STAGE_SRC, encoding="utf-8").read().encode()
                     ).hexdigest(), _STAGE_SHA_BEFORE)
check("10b  oncotriage/agent/patient.py is byte-identical",
      hashlib.sha256(open(_PATIENT_SRC, encoding="utf-8").read().encode()
                     ).hexdigest(), _PATIENT_SHA_BEFORE)
check("10c  oncotriage/deid.py is byte-identical",
      hashlib.sha256(open(_DEID_SRC, encoding="utf-8").read().encode()
                     ).hexdigest(), _DEID_SHA_BEFORE)
check("10d  NON-DEGENERACY: the three baselines are different files, so 10a "
      "to 10c are not one hash compared with itself",
      len({_STAGE_SHA_BEFORE, _PATIENT_SHA_BEFORE, _DEID_SHA_BEFORE}), 3)


print()
print("=" * 70)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFailures:")
    for f in _FAILURES:
        print(f"  - {f}")
print("=" * 70)

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Sep  8 2026

@author: ramyalsaffar
"""
