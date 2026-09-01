###################################################################
# Every not_evaluable verdict states why, and only this file can say it
###################################################################

"""
Not-Evaluable Reason Coverage Test

WHAT THIS FILE IS ABOUT. ``trial_matches.not_evaluable_reason`` is the column a
campaign is asked when a trial was recorded as not evaluated. Eleven code paths
can produce that verdict; before the pass this file ships with, SIX of them left
the column NULL -- and NULL was documented as meaning three different things at
once (a model-declared non-evaluation, a Stage 5 Step 2 defect, and a row
written before the column existed), separable only by a four-term SQL predicate
whose last term was TRUE of two of the three. So the question the column exists
to answer had no answer for the populations most in need of one.

THE SIX THAT WERE SILENT, each driven below:

    Stage 5 Step 2  an unreadable label with no criteria returned
    Stage 5 Step 2  a readable eligible/not_eligible with no criteria returned
    Stage 5 Step 2  a model-DECLARED not_evaluable with no criteria
    Stage 5 Step 3  an unreadable label over criteria that disqualify nobody
    Stage 5 Step 3  a model-DECLARED not_evaluable with criteria present
    Stage 6         a label `normalize_trial_verdict` could not resolve

THE FIVE THAT WERE ALREADY STAMPED are driven too, because a coverage claim that
tests only the new half is a claim about the half somebody happened to change.

WHY A REASON THE MODEL COULD SUPPLY WOULD BE WORTHLESS. Every marker here is
PIPELINE-OWNED and none is model-emitted, and that is a property of two things
rather than one. The response schema is strict with ``additionalProperties:
false`` and a complete ``required`` list, so a conforming response cannot carry
the key -- section 1 asserts that against the REAL schema and plants two defects
that would break it. And ``_strip_forged_provenance`` removes the key from every
model-returned entry before any branch reads it, so the guarantee survives a
provider that does not enforce the schema -- which is not hypothetical: the
Bedrock Responses branch's item (3) and the Converse branch's A1 both record
that no AWS page states whether structured output is honoured on those
surfaces, and both name "accepted, no error, silently not enforced" as the
dangerous outcome. Section 5 drives that.

THE STRIP IS NOT REDUNDANT, AND SECTION 5 IS CAREFUL ABOUT WHY. Now that every
not_evaluable branch stamps, a forged marker on a not_evaluable entry is
OVERWRITTEN by the branch that decided the verdict -- so the strip and the
stamping are two independent barriers against one fabrication, and removing
either alone changes nothing there. What the strip buys ON ITS OWN is the case
no branch covers: an entry that ends ELIGIBLE or NOT_ELIGIBLE has no reason
written for it, so a forged one survives into the stored column beside a verdict
saying the trial WAS evaluated (control 5a). And what the pair buys is the
expensive one: ``assessment_composition_case`` BRANCHES on this key, so with
both barriers gone a model can select the corrected-rejection case and have the
pipeline store fixed text asserting it corrected a rejection it never saw
(control 5b). Control 4a shows a branch losing its stamp is a one-line edit.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO
DATABASE, NO GIT HISTORY, NO LIVE SERVER. Every model response is a literal
built in this file and served by a stub installed through
``oncotriage/agent/deps.py``; the one raising stub raises a plain RuntimeError.
It writes NOTHING anywhere, not even a temp directory. NOT in
tests/run_serial_tests.py's collision matrix: the three repository files it
reads (agent/evaluation.py, agent/terminal.py, agent/response_schema.py) are
written by neither of the suite's two writers and are sha256-compared at the
end. It DOES exec: in-memory copies of those files, one plant each, argued at
_EXEC_ALLOWLIST.

    python tests/test_agent_not_evaluable_reason_coverage.py
"""

import contextlib
import hashlib
import io
import json
import os
import sys
import types

# ABOVE THE IMPORTS, deliberately: oncotriage.agent.deps reads this once at ITS
# import, and an assignment underneath would reach nothing while the run still
# printed that no model was loaded.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

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

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _ev
from oncotriage.agent import response_schema as _schema
from oncotriage.agent import terminal as _terminal
from oncotriage.agent.evaluation import (
    ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
    ASSESSMENT_KEPT_NOT_EVALUABLE,
    NOT_EVALUABLE_CALL_FAILED,
    NOT_EVALUABLE_CONFLICTING_DUPLICATES,
    NOT_EVALUABLE_MODEL_OMITTED,
    NOT_EVALUABLE_REASONS,
    NOT_EVALUABLE_REASONS_CONSTRUCTED,
    NOT_EVALUABLE_REASONS_CORRECTED,
    NOT_EVALUABLE_REASONS_DECLARED,
    NOT_EVALUABLE_REASON_ANOMALIES,
    NOT_EVALUABLE_SPLIT_BUDGET,
    NOT_EVALUABLE_TRUNCATION_FLOOR,
    UNEVALUABLE_MODEL_DECLARED,
    UNEVALUABLE_NO_CRITERIA_RETURNED,
    UNEVALUABLE_REJECTION_UNSUPPORTED,
    UNEVALUABLE_REMAP_NO_SURVIVOR,
    UNEVALUABLE_UNRECOGNIZED_VERDICT,
    assessment_composition_case,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.state import (
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_ELIGIBLE,
    TRIAL_VERDICT_NOT_EVALUABLE,
    UNEVALUABLE_STAGE6_UNRESOLVED,
)
from oncotriage.agent.terminal import node_finalize


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


# Located from each module's OWN __file__, so a future move cannot silently
# point a plant at a same-named copy.
_EVAL_SRC = os.path.abspath(_ev.__file__)
_TERMINAL_SRC = os.path.abspath(_terminal.__file__)
_SCHEMA_SRC = os.path.abspath(_schema.__file__)


def _sha256_of(path):
    return hashlib.sha256(
        open(path, encoding="utf-8").read().encode()).hexdigest()


_SHA_BEFORE = {p: _sha256_of(p)
               for p in (_EVAL_SRC, _TERMINAL_SRC, _SCHEMA_SRC)}

# The anomaly counter is module-level and shared by every consumer in this
# process, so section 4 asserts on what THIS file caused rather than on the
# counter happening to start empty.
_ANOMALIES_BEFORE = dict(NOT_EVALUABLE_REASON_ANOMALIES)


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


def planted(path, name, subs):
    """`_plant`, with a plant failure converted into a named absence."""
    try:
        return _plant(path, name, subs)
    except _PlantFailed as exc:
        return f"<PLANT-FAILED: {exc}>"


# ===========================================================================
# FIXTURES
# ===========================================================================

PATIENT = {
    "patient_id": "reason-coverage-patient",
    "demographics": {"age": 62, "sex": "male", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254637007",
                    "display": "Non-small cell lung cancer",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}

# A patient_value the absent-data validator will NOT match, so it cannot
# rewrite a status underneath the case being driven.
DOCUMENTED = "ECOG 1 recorded 2026-01-04"


def trial(nct_id):
    return {
        "nct_id": nct_id, "title": "A Study of Something", "phase": "Phase 2",
        "conditions": ["Lung Neoplasms"], "mesh_terms": ["Lung Neoplasms"],
        "eligibility": {"inclusion_criteria": "Adults with NSCLC",
                        "exclusion_criteria": "Pregnancy",
                        "min_age": 18, "max_age": 99, "sex": "ALL"},
    }


def crit(status, patient_value=DOCUMENTED, text="an eligibility criterion"):
    return {"criterion": text, "status": status, "patient_value": patient_value}


def entry(nct_id, eligible, inclusion=(), exclusion=(),
          assessment="Not evaluable: the model said so.", **extra):
    e = {
        "nct_id": nct_id, "eligible": eligible, "match_score": 0.5,
        "assessment": assessment,
        "inclusion_criteria": list(inclusion),
        "exclusion_criteria": list(exclusion),
    }
    e.update(extra)
    return e


class _StubMessage:
    def __init__(self, content):
        self.content = content


class _StubChoice:
    def __init__(self, content, finish_reason):
        self.message = _StubMessage(content)
        self.finish_reason = finish_reason


class _StubUsage:
    prompt_tokens = 1000
    completion_tokens = 200


class _StubResponse:
    def __init__(self, content, finish_reason):
        self.choices = [_StubChoice(content, finish_reason)]
        self.usage = _StubUsage()
        # None means the response carried no model field, which the node
        # handles explicitly and which keeps MatchingModelMismatchError out of
        # a test that is not about it.
        self.model = None


class StubOpenAI:
    """Serves one chosen JSON payload. No network, no key, no spend."""

    def __init__(self, payload, finish_reason="stop", raise_on=()):
        self._payload = json.dumps(payload) if payload is not None else "{}"
        self._finish_reason = finish_reason
        self._raise_on = set(raise_on)
        self.calls = 0
        self.chat = self
        self.completions = self

    def create(self, model, messages, **kwargs):
        self.calls += 1
        if self.calls in self._raise_on:
            # A PLAIN RuntimeError, not an SDK exception. The node's per-trial
            # isolation catches Exception and keys the counter by type name;
            # what this drives is the isolation, not a taxonomy.
            raise RuntimeError("planted transport failure")
        return _StubResponse(self._payload, self._finish_reason)


def _raised_result(exc):
    return {"evaluations": [], "raised": type(exc).__name__}


def run_stage5(payload, nct_ids=("NCT00000001",), *, per_trial=None,
               finish_reason="stop", raise_on=(), node=None):
    """Drive Stage 5 with a stubbed model. Returns (result, stderr, stub).

    THE ARM IS SET ON `config` WHEN IT IS SET AT ALL, which is the seam the
    production code chose: the node reads `config.matching_call_mode()` live so
    the column the storage layer writes cannot disagree with it. `per_trial=None`
    means "whatever ships", which is what most of this file wants -- the reasons
    it covers are arm-independent -- and the two scenarios that are NOT
    arm-independent say which arm they need and why.
    """
    node = node or node_llm_classifier_evaluation
    stub = StubOpenAI(payload, finish_reason, raise_on)
    state = {
        "patient_data": PATIENT,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "llm_classifier_retries": 0,
        "mesh_filter_applied": True,
        "mesh_filter_skip_reason": "applied",
        "stage_timings": {},
    }
    saved_arm = config.MATCHING_PER_TRIAL_CALLS_ENABLED
    saved = deps.set_overrides({"openai_client": stub})
    err = io.StringIO()
    try:
        if per_trial is not None:
            config.MATCHING_PER_TRIAL_CALLS_ENABLED = per_trial
        with contextlib.redirect_stderr(err):
            result = node(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        result = _raised_result(exc)
    finally:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = saved_arm
        deps.restore_overrides(saved)
    return result, err.getvalue(), stub


def run_stage6(evaluations, nct_ids=("NCT00000001",), node=None):
    node = node or node_finalize
    state = {
        "patient_data": PATIENT, "evaluations": evaluations,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "stage_timings": {},
    }
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            out = node(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        out = {"result": {}, "evaluations": [], "raised": type(exc).__name__}
    return out, err.getvalue()


def eval_of(result, nct_id="NCT00000001"):
    for e in result.get("evaluations", []):
        if e.get("nct_id") == nct_id:
            return e
    return {}


def verdict_of(result, nct_id="NCT00000001"):
    return eval_of(result, nct_id).get("eligible", "<absent>")


def reason_of(result, nct_id="NCT00000001"):
    """The entry's not_evaluable_reason, or a NAMED ABSENCE.

    Never a bare index and never `.get(key)` alone: the defect this file exists
    to catch is a MISSING reason, and `None` is also what an explicit
    ``not_evaluable_reason=None`` would give. "<no key>" separates them.
    """
    return eval_of(result, nct_id).get("not_evaluable_reason", "<no key>")


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


def audit_reasons(stderr_text):
    """The `reason` field of the not_evaluable audit event, or a named absence.

    IT IS A LIST, not a string: the log line emits `sorted({...})` over the
    trials it is reporting. The first draft of this reader indexed it as a
    scalar and put a `TypeError: unhashable type` inside a `check()` argument,
    which took the run down at the first scenario and reported one traceback
    where the file owed every result below it. Nothing here can raise.
    """
    records = log_records(stderr_text, "not_evaluable")
    if not records:
        return "<no such record>"
    value = records[0].get("reason", "<no such field>")
    return sorted(value) if isinstance(value, list) else value


def unstamped(result):
    """[nct_id] for every not_evaluable entry carrying no reason.

    THE FILE'S ONE INVARIANT, as a function, so every scenario can assert it
    without restating it -- and so a scenario that produces a new population
    fails here rather than needing somebody to have thought of it.
    """
    return sorted(e.get("nct_id") for e in result.get("evaluations", [])
                  if e.get("eligible") == TRIAL_VERDICT_NOT_EVALUABLE
                  and not e.get("not_evaluable_reason"))


MET = [crit("met")]


print("=" * 78)
print("SECTION 1 -- the vocabulary is closed, partitioned, and unforgeable")
print("=" * 78)

check("the three writer classes partition the vocabulary",
      sorted(NOT_EVALUABLE_REASONS)
      == sorted(set(NOT_EVALUABLE_REASONS_CONSTRUCTED)
                | set(NOT_EVALUABLE_REASONS_CORRECTED)
                | set(NOT_EVALUABLE_REASONS_DECLARED)),
      True)
check("...and they are pairwise disjoint, so every member has exactly one "
      "writer class",
      [len(set(a) & set(b)) for a, b in (
          (NOT_EVALUABLE_REASONS_CONSTRUCTED, NOT_EVALUABLE_REASONS_CORRECTED),
          (NOT_EVALUABLE_REASONS_CONSTRUCTED, NOT_EVALUABLE_REASONS_DECLARED),
          (NOT_EVALUABLE_REASONS_CORRECTED, NOT_EVALUABLE_REASONS_DECLARED))],
      [0, 0, 0])
check("...with no duplicate inside the union",
      len(set(NOT_EVALUABLE_REASONS)), len(NOT_EVALUABLE_REASONS))
check("the exact membership, so a member added or removed is a decision",
      sorted(NOT_EVALUABLE_REASONS),
      sorted([NOT_EVALUABLE_TRUNCATION_FLOOR, NOT_EVALUABLE_SPLIT_BUDGET,
              NOT_EVALUABLE_MODEL_OMITTED, NOT_EVALUABLE_CONFLICTING_DUPLICATES,
              NOT_EVALUABLE_CALL_FAILED, UNEVALUABLE_UNRECOGNIZED_VERDICT,
              UNEVALUABLE_NO_CRITERIA_RETURNED,
              UNEVALUABLE_REJECTION_UNSUPPORTED, UNEVALUABLE_REMAP_NO_SURVIVOR,
              UNEVALUABLE_STAGE6_UNRESOLVED, UNEVALUABLE_MODEL_DECLARED]))
check("non-degeneracy: the vocabulary is not empty and not a singleton",
      len(NOT_EVALUABLE_REASONS) >= 11, True)
check("the CONSTRUCTED class is exactly the tuple that indexes "
      "_unevaluable_entry's explanation table",
      NOT_EVALUABLE_REASONS_CONSTRUCTED is _ev._NOT_EVALUABLE_REASONS, True)
check("no member is the empty string, which would be a reason of zero "
      "characters",
      [r for r in NOT_EVALUABLE_REASONS if not str(r).strip()], [])


# ---------------------------------------------------------------------------
# THE SCHEMA HALF. Asserted against the REAL schema rather than against
# TRIAL_FIELDS, because the property that matters is what a CONFORMING RESPONSE
# may carry, and that is decided by the emitted document.
# ---------------------------------------------------------------------------

def schema_admits(schema, key):
    """(key is a declared property anywhere, any object is open)."""
    declared = False
    open_object = False
    for _path, node in _schema.schema_object_paths(schema):
        if key in (node.get("properties") or {}):
            declared = True
        if node.get("additionalProperties") is not False:
            open_object = True
    return declared, open_object


_LIVE_SCHEMA = _schema.build_response_schema()
check("the shipped schema declares no not_evaluable_reason and closes every "
      "object, so a conforming response cannot carry the key",
      schema_admits(_LIVE_SCHEMA, "not_evaluable_reason"), (False, False))
check("non-degeneracy: the walk really visits objects and really finds the "
      "keys the schema DOES declare",
      (len(_schema.schema_object_paths(_LIVE_SCHEMA)) > 1,
       schema_admits(_LIVE_SCHEMA, "eligible")[0]),
      (True, True))
check("no member of the vocabulary is a value the `eligible` enum can carry, "
      "so a reason cannot arrive as a verdict either",
      sorted(set(NOT_EVALUABLE_REASONS) & set(_schema.TRIAL_VERDICT_ENUM)), [])

# CONTROL 1a: the key becomes a declared property. This is the shape a future
# edit takes when somebody decides the model should "explain itself".
#
# THE PLANT GOES INTO `properties`, NOT INTO TRIAL_FIELDS, and the first draft
# got that wrong: `_trial_schema()` writes its properties out by hand and uses
# TRIAL_FIELDS only for `required`, so adding the name there produces a schema
# that REQUIRES a property it does not declare -- a different defect, and one
# this checker correctly reports as not-emittable. What decides whether a
# conforming response may carry a key is the properties dict beside
# `additionalProperties`, which is what section 1 asks and what this plants.
_c1a = planted(_SCHEMA_SRC, "_schema_declared", [(
    '        "properties": {\n            # Emitted first,',
    '        "properties": {\n'
    '            "not_evaluable_reason": {"type": "string"},\n'
    '            # Emitted first,')])
check("CONTROL 1a: a pipeline-owned value becomes emittable when the key joins "
      "TRIAL_FIELDS -- CAUGHT",
      (schema_admits(_c1a.build_response_schema(), "not_evaluable_reason")
       if not isinstance(_c1a, str) else _c1a),
      (True, False))

# CONTROL 1b: the object opens. The key is still undeclared, and `strict` no
# longer forbids it -- which is the half a properties-only check cannot see.
_c1b = planted(_SCHEMA_SRC, "_schema_open", [(
    '        "required": list(TRIAL_FIELDS),\n'
    '        "additionalProperties": False,',
    '        "required": list(TRIAL_FIELDS),\n'
    '        "additionalProperties": True,')])
check("CONTROL 1b: a pipeline-owned value becomes emittable when the trial "
      "object stops being closed -- CAUGHT",
      (schema_admits(_c1b.build_response_schema(), "not_evaluable_reason")
       if not isinstance(_c1b, str) else _c1b),
      (False, True))


print()
print("=" * 78)
print("SECTION 2 -- the five CONSTRUCTED paths, each driven end to end")
print("=" * 78)

# (a) the model returned a well-formed response with no entry for a trial it
#     was sent. Two trials sent, one answered.
_omit, _omit_err, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "eligible", MET)]},
    ("NCT00000001", "NCT00000002"))
check("omitted trial: recorded not_evaluable",
      verdict_of(_omit, "NCT00000002"), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped omitted_from_model_response",
      reason_of(_omit, "NCT00000002"), NOT_EVALUABLE_MODEL_OMITTED)
check("...and the reconciliation said so", len(
    log_records(_omit_err, "reconciliation")), 1)
check("...and nothing in that run is unstamped", unstamped(_omit), [])

# (b) the model answered twice for one trial and disagreed.
_dupe, _dupe_err, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "eligible", MET),
                     entry("NCT00000001", "not_eligible", [crit("not_met")])]})
check("conflicting duplicates: recorded not_evaluable",
      verdict_of(_dupe), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped conflicting_duplicate_answers",
      reason_of(_dupe), NOT_EVALUABLE_CONFLICTING_DUPLICATES)
check("...and nothing in that run is unstamped", unstamped(_dupe), [])

# (c) one trial sent alone and the response still hit the ceiling.
_floor, _floor_err, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "eligible", MET)]},
    finish_reason="length")
check("truncation floor: recorded not_evaluable",
      verdict_of(_floor), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped truncation_floor",
      reason_of(_floor), NOT_EVALUABLE_TRUNCATION_FLOOR)
check("...and nothing in that run is unstamped", unstamped(_floor), [])

# (d) THE SPLIT BUDGET, WHICH NEEDS THE GROUPED ARM AND SAYS SO. A per-trial
#     chunk is a singleton by construction, so it can only ever reach the FLOOR
#     above; the budget branch requires a chunk of more than one still
#     truncating at MAX_TRUNCATION_SPLITS, which only the packer can build.
_BUDGET_IDS = tuple("NCT%08d" % i for i in range(1, 17))
_budget, _budget_err, _ = run_stage5(
    {"evaluations": [entry(n, "eligible", MET) for n in _BUDGET_IDS]},
    _BUDGET_IDS, per_trial=False, finish_reason="length")
check("split budget: every trial in the exhausted chunk is not_evaluable",
      sorted({verdict_of(_budget, n) for n in _BUDGET_IDS}),
      [TRIAL_VERDICT_NOT_EVALUABLE])
check("...and every one is stamped truncation_split_budget_exhausted",
      sorted({reason_of(_budget, n) for n in _BUDGET_IDS}),
      [NOT_EVALUABLE_SPLIT_BUDGET])
check("...and the budget branch is what fired, not the floor",
      (len(log_records(_budget_err, "split_budget_exhausted")) > 0,
       len(log_records(_budget_err, "truncation_floor"))),
      (True, 0))
check("...and nothing in that run is unstamped", unstamped(_budget), [])

# (e) THE PER-TRIAL CALL FAILURE, WHICH NEEDS THE PER-TRIAL ARM AND SAYS SO.
#     Grouped mode has nothing to isolate a raised call from: the whole batch
#     is the request, and the node returns the API-error result instead.
#     Call 1 is the cache warmup; call 2 and call 3 are the two trials.
_fail, _fail_err, _fail_stub = run_stage5(
    {"evaluations": [entry("NCT00000001", "eligible", MET),
                     entry("NCT00000002", "eligible", MET)]},
    ("NCT00000001", "NCT00000002"), per_trial=True, raise_on=(3,))
check("per-trial call failure: the isolated trial is not_evaluable",
      verdict_of(_fail, "NCT00000002"), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped per_trial_call_failed",
      reason_of(_fail, "NCT00000002"), NOT_EVALUABLE_CALL_FAILED)
check("...while the trial whose call succeeded is untouched",
      (verdict_of(_fail, "NCT00000001"), reason_of(_fail, "NCT00000001")),
      (TRIAL_VERDICT_ELIGIBLE, "<no key>"))
check("non-degeneracy: the planted failure really was reached",
      _fail_stub.calls >= 3, True)
check("...and nothing in that run is unstamped", unstamped(_fail), [])

# EVERY CONSTRUCTED REASON THROUGH ITS ONE WRITER, as a unit. The five drives
# above reach five call sites; this reaches the constructor they all share, so
# a sixth call site added with a value the explanation table does not carry
# fails here rather than at the first patient who meets it.
for _reason in NOT_EVALUABLE_REASONS_CONSTRUCTED:
    _built = _ev._unevaluable_entry({"trial": trial("NCT00000009")}, _reason)
    check(f"_unevaluable_entry({_reason!r}) stamps it and says not_evaluable",
          (_built.get("eligible"), _built.get("not_evaluable_reason")),
          (TRIAL_VERDICT_NOT_EVALUABLE, _reason))


print()
print("=" * 78)
print("SECTION 3 -- the six paths that were silent, each driven end to end")
print("=" * 78)

# (a) Step 2, arm 1: an unreadable label with no criteria returned.
_s2a, _s2a_err, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "elligible")]})
check("Step 2 / unreadable label, no criteria: not_evaluable",
      verdict_of(_s2a), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped with the label reason",
      reason_of(_s2a), UNEVALUABLE_UNRECOGNIZED_VERDICT)
check("...which is the SAME string the audit log carries for it, so the log "
      "and the column cannot disagree",
      audit_reasons(_s2a_err), [UNEVALUABLE_UNRECOGNIZED_VERDICT])
check("...and nothing in that run is unstamped", unstamped(_s2a), [])

# (b) Step 2, arm 2: a readable rejection with no criteria returned.
_s2b, _s2b_err, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "not_eligible")]})
check("Step 2 / readable label, no criteria: not_evaluable",
      verdict_of(_s2b), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped model returned no criteria",
      reason_of(_s2b), UNEVALUABLE_NO_CRITERIA_RETURNED)
check("...which is the SAME string the audit log carries",
      audit_reasons(_s2b_err), [UNEVALUABLE_NO_CRITERIA_RETURNED])
check("...and nothing in that run is unstamped", unstamped(_s2b), [])

# (c) Step 2, arm 3: a model-DECLARED not_evaluable with the empty arrays its
#     own contract mandates. Nothing was corrected, and the reason says so.
_s2c, _s2c_err, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "not_evaluable")]})
check("Step 2 / model-declared, no criteria: stays not_evaluable",
      verdict_of(_s2c), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped model declared this trial not evaluable",
      reason_of(_s2c), UNEVALUABLE_MODEL_DECLARED)
check("...and is NOT in the audit list, because that list is a report about "
      "verdicts this node MOVED",
      len(log_records(_s2c_err, "not_evaluable")), 0)
check("...and keeps the model's own draft rather than composing over it",
      assessment_composition_case(eval_of(_s2c)), ASSESSMENT_KEPT_NOT_EVALUABLE)
check("...and nothing in that run is unstamped", unstamped(_s2c), [])

# (d) Step 3: an unreadable label over criteria that disqualify nobody.
_s3a, _s3a_err, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "elligible", MET)]})
check("Step 3 / unreadable label, non-disqualifying criteria: not_evaluable",
      verdict_of(_s3a), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped with the label reason",
      reason_of(_s3a), UNEVALUABLE_UNRECOGNIZED_VERDICT)
check("...and it is NEITHER corrected-rejection marker, so no composed "
      "sentence is written over it",
      (assessment_composition_case(eval_of(_s3a)),
       reason_of(_s3a) in (UNEVALUABLE_REJECTION_UNSUPPORTED,
                           UNEVALUABLE_REMAP_NO_SURVIVOR)),
      (ASSESSMENT_KEPT_NOT_EVALUABLE, False))
check("...and nothing in that run is unstamped", unstamped(_s3a), [])

# (e) Step 3: a model-DECLARED not_evaluable WITH criteria present.
_s3b, _s3b_err, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "not_evaluable", MET)]})
check("Step 3 / model-declared with criteria: stays not_evaluable",
      verdict_of(_s3b), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and stamped the same DECLARED reason as its empty-array sibling",
      reason_of(_s3b), UNEVALUABLE_MODEL_DECLARED)
check("...and nothing in that run is unstamped", unstamped(_s3b), [])

# (f) STAGE 6. Unreachable through Stage 5 -- Step 0 resolves every label into
#     the three-member vocabulary -- so it is driven through node_finalize
#     directly, which is what an outside caller building `evaluations` does.
_s6, _s6_err = run_stage6(
    [{"nct_id": "NCT00000001", "eligible": "elligible", "match_score": 0.5}])
_s6_entry = (_s6.get("result", {}).get("not_evaluable") or [{}])[0]
check("Stage 6 / unresolvable label: bucketed as a non-evaluation",
      (_s6_entry.get("nct_id"), _s6_entry.get("eligible")),
      ("NCT00000001", TRIAL_VERDICT_NOT_EVALUABLE))
check("...and stamped with Stage 6's own reason",
      _s6_entry.get("not_evaluable_reason", "<no key>"),
      UNEVALUABLE_STAGE6_UNRESOLVED)
check("...whose constant lives in state.py, which BOTH stages import and "
      "neither owns the other",
      (UNEVALUABLE_STAGE6_UNRESOLVED in NOT_EVALUABLE_REASONS_CORRECTED,
       "oncotriage/agent/state.py".replace("/", os.sep)
       in os.path.abspath(sys.modules["oncotriage.agent.state"].__file__)),
      (True, True))
check("non-degeneracy: Stage 6 also warned about it",
      len(log_records(_s6_err)) >= 1, True)

# THE TWO THAT WERE ALREADY STAMPED, driven here so the coverage claim is over
# every path rather than over the ones this pass touched.
_unsup, _, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "not_eligible", MET,
                           assessment="Known disqualifier: the model said so.")]})
check("Step 3 / rejection with no disqualifying row: stamped, unchanged",
      (verdict_of(_unsup), reason_of(_unsup)),
      (TRIAL_VERDICT_NOT_EVALUABLE, UNEVALUABLE_REJECTION_UNSUPPORTED))
check("...and it is the ONE population whose draft is replaced",
      assessment_composition_case(eval_of(_unsup)),
      ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION)

_remap, _, _ = run_stage5(
    {"evaluations": [entry("NCT00000001", "not_eligible",
                           [crit("violated", text="wrong-arm label")],
                           assessment="Known disqualifier: the model said so.")]})
check("Step 3 / rejection whose disqualifiers did not survive: stamped, "
      "unchanged",
      (verdict_of(_remap), reason_of(_remap)),
      (TRIAL_VERDICT_NOT_EVALUABLE, UNEVALUABLE_REMAP_NO_SURVIVOR))


print()
print("=" * 78)
print("SECTION 4 -- the totality invariant and its production tripwire")
print("=" * 78)

# EVERY REASON THIS FILE OBSERVED, against the closed vocabulary. Built from
# the scenarios above rather than retyped, so a scenario that starts producing
# a value from nowhere fails here.
_OBSERVED = sorted({
    reason_of(_omit, "NCT00000002"), reason_of(_dupe), reason_of(_floor),
    reason_of(_budget, _BUDGET_IDS[0]), reason_of(_fail, "NCT00000002"),
    reason_of(_s2a), reason_of(_s2b), reason_of(_s2c), reason_of(_s3a),
    reason_of(_s3b), reason_of(_unsup), reason_of(_remap),
    _s6_entry.get("not_evaluable_reason", "<no key>"),
})
check("every reason this file observed is a member of the closed vocabulary",
      [r for r in _OBSERVED if r not in NOT_EVALUABLE_REASONS], [])
check("...and the drives cover ELEVEN of the eleven members, so no path is "
      "represented only by an assertion about the code",
      sorted(_OBSERVED), sorted(NOT_EVALUABLE_REASONS))

# THE TRIPWIRE. Every scenario above already asserted `unstamped(...) == []`;
# this asserts the production counter agrees, which is the thing an operator
# would see. A `missing:` key here would mean this file's own invariant is
# false in a way none of the scenarios happened to look at.
_MISSING_KEYS = sorted(k for k in NOT_EVALUABLE_REASON_ANOMALIES
                       if k.startswith("missing:")
                       and NOT_EVALUABLE_REASON_ANOMALIES[k]
                       != _ANOMALIES_BEFORE.get(k, 0))
check("no scenario in this file left a not_evaluable entry without a reason",
      _MISSING_KEYS, [])

# CONTROL 4a: one arm's stamp dropped. The verdict is still right, the run
# still succeeds, and the ONLY trace is the counter and the empty column --
# which is exactly the silent state this pass removed.
_c4a = planted(_EVAL_SRC, "_ev_no_declared_stamp", [(
    "                # non-evaluation was never a rejection and was never "
    "moved.\n"
    '                eval_result["not_evaluable_reason"] = '
    "UNEVALUABLE_MODEL_DECLARED\n",
    "                # non-evaluation was never a rejection and was never "
    "moved.\n"
    "                pass  # PLANTED: the DECLARED stamp, dropped\n")])
if isinstance(_c4a, str):
    check("CONTROL 4a: the plant applied", _c4a, "<applied>")
else:
    _c4a_res, _c4a_err, _ = run_stage5(
        {"evaluations": [entry("NCT00000001", "not_evaluable")]},
        node=_c4a.node_llm_classifier_evaluation)
    check("CONTROL 4a: dropping one arm's stamp leaves an unexplainable row "
          "-- CAUGHT",
          (verdict_of(_c4a_res), reason_of(_c4a_res),
           unstamped(_c4a_res)),
          (TRIAL_VERDICT_NOT_EVALUABLE, "<no key>", ["NCT00000001"]))
    check("CONTROL 4a: and the planted module's own tripwire counted it, "
          "keyed by which half of the node lost the stamp",
          sorted(k for k in _c4a.NOT_EVALUABLE_REASON_ANOMALIES
                 if k.startswith("missing:")),
          ["missing:canonical"])
    check("CONTROL 4a: and it warned rather than failing the patient",
          (len(log_records(_c4a_err, "not_evaluable_reason_missing")),
           _c4a_res.get("error", "")),
          (1, ""))
check("CONTROL 4a clean arm: the SHIPPED module stamps that same payload",
      reason_of(_s2c), UNEVALUABLE_MODEL_DECLARED)

# CONTROL 4b: the tripwire itself, deleted. Every stamp still lands, so no
# scenario in this file changes -- which is the point: this control is for the
# TRIPWIRE, and without it a future stamp-dropping edit is silent again.
_c4b = planted(_EVAL_SRC, "_ev_no_tripwire", [(
    "    _account_missing_not_evaluable_reason(evaluations)\n",
    "    pass  # PLANTED: the tripwire, removed\n")])
if isinstance(_c4b, str):
    check("CONTROL 4b: the plant applied", _c4b, "<applied>")
else:
    # Drive the same defect as 4a THROUGH the tripwire-less module, by handing
    # it an entry the node cannot stamp -- a construct with the key removed.
    _c4b_res, _c4b_err, _ = run_stage5(
        {"evaluations": [entry("NCT00000001", "not_evaluable")]},
        node=_c4b.node_llm_classifier_evaluation)
    _c4b_probe = _c4b._account_missing_not_evaluable_reason(
        [{"nct_id": "X", "eligible": TRIAL_VERDICT_NOT_EVALUABLE}])
    check("CONTROL 4b: with the call removed the node emits no missing-reason "
          "record even for a payload the scan would count -- CAUGHT",
          (len(log_records(_c4b_err, "not_evaluable_reason_missing")),
           _c4b_probe),
          (0, 1))
check("CONTROL 4b clean arm: the SHIPPED node calls the scan, so the same "
      "unstamped entry WOULD be recorded",
      _ev._account_missing_not_evaluable_reason(
          [{"nct_id": "X", "eligible": TRIAL_VERDICT_NOT_EVALUABLE}]), 1)


print()
print("=" * 78)
print("SECTION 5 -- a model-supplied marker never reaches a branch")
print("=" * 78)

# A response carrying the key. Under the shipped schema this cannot happen; on
# a provider that does not enforce it, it can. Note the entry ALSO ends
# not_evaluable through Step 2's declared arm, so the surviving value proves
# which writer won rather than proving the key is simply absent.
_FORGED = {"evaluations": [entry(
    "NCT00000001", "not_evaluable",
    not_evaluable_reason=UNEVALUABLE_REJECTION_UNSUPPORTED,
    assessment="Not evaluable: the model said so.")]}
_forge, _forge_err, _ = run_stage5(_FORGED)
check("a forged marker is replaced by the reason this node decided",
      reason_of(_forge), UNEVALUABLE_MODEL_DECLARED)
check("...so the composed assessment is NOT the corrected-rejection text the "
      "forged marker selects",
      assessment_composition_case(eval_of(_forge)), ASSESSMENT_KEPT_NOT_EVALUABLE)
check("...and the forgery was counted as one of OUR strings rather than as "
      "text from nowhere",
      NOT_EVALUABLE_REASON_ANOMALIES["forged:vocabulary_member"]
      - _ANOMALIES_BEFORE.get("forged:vocabulary_member", 0), 1)
check("...and announced, with the TYPE and never the value",
      (len(log_records(_forge_err, "forged_provenance_marker")),
       "error_type" in (log_records(_forge_err, "forged_provenance_marker")
                        or [{}])[0]),
      (1, True))

_FOREIGN = {"evaluations": [entry(
    "NCT00000001", "eligible", MET, not_evaluable_reason="because I said so")]}
_foreign, _, _ = run_stage5(_FOREIGN)
check("a forged value from OUTSIDE the vocabulary is dropped too, on an entry "
      "that ends eligible and would otherwise have carried it into the row",
      (verdict_of(_foreign), reason_of(_foreign)),
      (TRIAL_VERDICT_ELIGIBLE, "<no key>"))
check("...and keyed as foreign, which is the operationally different finding",
      NOT_EVALUABLE_REASON_ANOMALIES["forged:foreign_value"]
      - _ANOMALIES_BEFORE.get("forged:foreign_value", 0), 1)

# CONTROL 5a: the strip removed, driven on an ELIGIBLE entry. This is the one
# consequence that is live TODAY: no branch of the normalizer writes a reason
# for a verdict that was evaluated, so nothing overwrites a forged one and the
# stored row carries "why this trial was not evaluated" beside a verdict saying
# it WAS.
_c5a = planted(_EVAL_SRC, "_ev_no_strip", [(
    "        _strip_forged_provenance(eval_result)\n",
    "        pass  # PLANTED: the forgery strip, removed\n")])
if isinstance(_c5a, str):
    check("CONTROL 5a: the plant applied", _c5a, "<applied>")
else:
    _c5a_res, _, _ = run_stage5(
        _FOREIGN, node=_c5a.node_llm_classifier_evaluation)
    check("CONTROL 5a: without the strip a model-written reason reaches an "
          "EVALUATED row -- CAUGHT",
          (verdict_of(_c5a_res), reason_of(_c5a_res)),
          (TRIAL_VERDICT_ELIGIBLE, "because I said so"))
check("CONTROL 5a clean arm: the SHIPPED module leaves that row with no reason",
      (verdict_of(_foreign), reason_of(_foreign)),
      (TRIAL_VERDICT_ELIGIBLE, "<no key>"))

# CONTROL 5b: TWO PLANTS, AND THE PAIR IS THE POINT. On a not_evaluable entry
# the branch's own stamp overwrites a forged marker, so the strip and the
# stamping are two independent barriers against the same fabrication and
# removing either one alone changes nothing. Removing BOTH lets the model
# choose the stored assessment: it selects the corrected-rejection case and the
# row asserts that this pipeline corrected a rejection it never saw.
#
# THAT IS WHY THE STRIP IS NOT REDUNDANT. Control 4a shows a branch losing its
# stamp is a one-line edit; with the strip present that edit costs a NULL
# column, and without it, it costs a fabricated clinical sentence.
_c5b = planted(_EVAL_SRC, "_ev_no_strip_no_stamp", [
    ("        _strip_forged_provenance(eval_result)\n",
     "        pass  # PLANTED: the forgery strip, removed\n"),
    ("                # non-evaluation was never a rejection and was never "
     "moved.\n"
     '                eval_result["not_evaluable_reason"] = '
     "UNEVALUABLE_MODEL_DECLARED\n",
     "                # non-evaluation was never a rejection and was never "
     "moved.\n"
     "                pass  # PLANTED: the DECLARED stamp, dropped\n")])
if isinstance(_c5b, str):
    check("CONTROL 5b: the plant applied", _c5b, "<applied>")
else:
    _c5b_res, _, _ = run_stage5(
        _FORGED, node=_c5b.node_llm_classifier_evaluation)
    check("CONTROL 5b: with BOTH barriers gone the model chooses the stored "
          "assessment -- CAUGHT",
          (reason_of(_c5b_res),
           eval_of(_c5b_res).get("assessment")
           == _ev.ASSESSMENT_UNSUPPORTED_REJECTION_TEXT),
          (UNEVALUABLE_REJECTION_UNSUPPORTED, True))
check("CONTROL 5b clean arm: the SHIPPED module stores the model's own draft "
      "for that payload",
      eval_of(_forge).get("assessment"), "Not evaluable: the model said so.")


print()
print("=" * 78)
print("SECTION 6 -- nothing was written to disk")
print("=" * 78)

for _path in (_EVAL_SRC, _TERMINAL_SRC, _SCHEMA_SRC):
    check(f"{os.path.basename(_path)} is byte-identical to its pre-run state",
          _sha256_of(_path), _SHA_BEFORE[_path])
check("non-degeneracy: the baseline hashes are real digests of real, DIFFERENT "
      "content rather than one file hashed three times",
      len(set(_SHA_BEFORE.values())), 3)


print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 2026-08-31

@author: ramyalsaffar
"""
