###################################################################
# Stage 5: a rejection its own criteria arrays do not support
###################################################################

"""
Unsupported Rejection Test

THE DEFECT. ``node_llm_classifier_evaluation``'s post-processing loop ended in
a fall-through branch that received, among other things, a model-declared
``not_eligible`` carrying NO row with a disqualifying status -- no inclusion
"not_met", no exclusion "violated" -- and no label remap that had removed one.
It passed that entry through untouched, so a rejection with nothing in the
record to quote was stored as a rejection, flowed into ``near_misses``, and
dropped the trial off the patient's list.

The branch immediately above it already corrected the SIBLING case: a rejection
whose every disqualifier was an out-of-vocabulary label that Step 1 resolved
away. Same missing evidence, same correction -- ``not_evaluable``, zero score,
an entry in ``unevaluable_trials`` -- reached by a different route. The only
difference between the two is whether the disqualifiers were written wrong or
never written at all, which is not a difference a clinician can act on.

WHY IT MATTERS MORE THAN THE OPPOSITE ERROR. A false "eligible" is checked: a
clinician opens the trial and reads the criteria. A false "not_eligible"
silently removes a trial from a patient's list and nobody looks at it again.
Measured on real evaluation runs, 6 of 54 rejections had this shape -- one of
them citing a 1963 tubal ligation as its support for a hypothyroidism
diagnosis.

WHAT THE FIX DOES NOT DO. It does not promote to "eligible": the model never
asserted a match, and asserting one here would be the same fabrication pointing
the other way. It does not touch either criteria array: they are the evidence
that there was no evidence, and ``criterion_details`` stores them verbatim. It
does not weaken ``ASSESSMENT_KEPT_NO_DISQUALIFIER``, which stays as the
backstop detector and which section 6 proves is now unreachable from the
pipeline -- the thing its own comment had claimed while the fall-through branch
was making it false.

THE ORDERING THAT HAD TO BE CHECKED, section 7. The absent-data validator runs
AFTER this normalizer and processes only ``not_eligible`` trials whose
disqualifying rows carry absent patient_values. The two cannot fight over one
trial, and the reason is structural rather than lucky: this correction fires
only when NO disqualifying row exists, while the validator fires only when at
least one does. So a trial the validator would have promoted to "eligible" can
never be taken by this correction first -- which would be as bad as the defect,
in the same direction.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every model response
here is a literal built in this file and served by a stub installed through
``oncotriage/agent/deps.py``. NOT in tests/run_serial_tests.py's collision
matrix: it writes nothing anywhere -- the negative control plants into an
in-memory copy, with the source file hashed before any plant and compared at
the end -- and the file it reads is written by neither of the suite's two
writers.

    python tests/test_agent_unsupported_rejection.py
"""

import contextlib
import hashlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
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
from oncotriage.agent import evaluation as _evaluation_module
from oncotriage.agent.evaluation import (
    ASSESSMENT_CASES,
    ASSESSMENT_COMPOSITION_ANOMALIES,
    ASSESSMENT_KEPT_NOT_EVALUABLE,
    ASSESSMENT_KEPT_NO_DISQUALIFIER,
    ASSESSMENT_NOT_ELIGIBLE_OPENING,
    UNEVALUABLE_REJECTION_UNSUPPORTED,
    UNEVALUABLE_UNRECOGNIZED_VERDICT,
    assessment_composition_case,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.state import (
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_ELIGIBLE,
    TRIAL_VERDICT_NOT_EVALUABLE,
)
from oncotriage.agent.terminal import node_finalize
from oncotriage.storage import database_logger as _database_logger


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


# The module under test, located from its OWN __file__ rather than from this
# test's directory, so a future move cannot silently point the plant at a
# same-named copy.
_EVAL_SRC = os.path.abspath(_evaluation_module.__file__)


def _sha256_of(path):
    return hashlib.sha256(
        open(path, encoding="utf-8").read().encode()).hexdigest()


# Taken before any plant runs, so the restore assertion in section 9 compares
# against a real baseline rather than against itself.
_SHA_BEFORE = _sha256_of(_EVAL_SRC)

# The module-level anomaly counter is shared by every consumer in the process.
# Snapshotting it means section 6 asserts on what THIS file caused rather than
# on the counter happening to start at zero.
_ANOMALIES_BEFORE = dict(ASSESSMENT_COMPOSITION_ANOMALIES)


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


# ===========================================================================
# FIXTURES: a patient, some trials, and a stub that serves a chosen response
# ===========================================================================

PATIENT = {
    "patient_id": "unsupported-rejection-patient",
    "demographics": {"age": 62, "sex": "male", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254637007",
                    "display": "Non-small cell lung cancer",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(nct_id):
    """One trial, carrying every field Stage 5 and Stage 6 read off it."""
    return {
        "nct_id": nct_id, "title": "A Study of Something", "phase": "Phase 2",
        "conditions": ["Lung Neoplasms"], "mesh_terms": ["Lung Neoplasms"],
        "eligibility": {"inclusion_criteria": "Adults with NSCLC",
                        "exclusion_criteria": "Pregnancy",
                        "min_age": 18, "max_age": 99, "sex": "ALL"},
    }


# DOCUMENTED, deliberately. Every criterion in this file that is not testing
# the absent-data validator carries a patient_value the validator will not
# match, so it cannot rewrite a status underneath the case being tested.
DOCUMENTED = "ECOG 1 recorded 2026-01-04"

# What the validator DOES match. One of its exact phrases, so the interaction
# tests in section 7 exercise the real predicate rather than a near-miss.
ABSENT = "Not in patient record"


def crit(status, patient_value=DOCUMENTED, text="an eligibility criterion"):
    """One criterion row."""
    return {"criterion": text, "status": status, "patient_value": patient_value}


def entry(nct_id, eligible, inclusion=(), exclusion=(),
          assessment="Known disqualifier: the model said so."):
    """One evaluation entry as the model returns it.

    The default assessment opens with the mandated rejection opening, because
    the population this file is about is one the model believed it was
    rejecting -- and section 6 asserts on what happens to that text.
    """
    return {
        "nct_id": nct_id, "eligible": eligible, "match_score": 0.5,
        "assessment": assessment,
        "inclusion_criteria": list(inclusion),
        "exclusion_criteria": list(exclusion),
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
        # None means "the response carried no model field", which
        # node_llm_classifier_evaluation handles explicitly and which keeps
        # MatchingModelMismatchError out of a test that is not about it.
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


# A RAISE IS AN OUTCOME, NOT A REASON TO ABORT. Reverting any of the fixes this
# file covers can make Stage 5 raise, and with a bare call that raise escapes
# through check()'s argument list and takes the whole run down -- one traceback
# where the file owed every result below it. The same defect has been fixed in
# tests/test_storage_query_layer.py,
# tests/test_dashboard_reproducibility_tab.py,
# tests/test_docker_qdrant_override_and_readiness.py,
# tests/test_agent_age_units_and_sex_filter.py and
# tests/test_agent_trial_verdict_normalization.py. The two drivers below never
# propagate: they return a result-shaped stand-in carrying `raised`, so every
# downstream check FAILS with a named exception instead.

def _raised_result(exc):
    return {"evaluations": [], "raised": type(exc).__name__}


def _raised_final(exc):
    return {"matches": [], "near_misses": [], "not_evaluable": [],
            "raised": type(exc).__name__}


def run_stage5(payload, nct_ids=("NCT00000001",), node=None):
    """Drive Stage 5 with a stubbed model. Returns (result, stderr_text)."""
    node = node or node_llm_classifier_evaluation
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
            result = node(state)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        result = _raised_result(exc)
    finally:
        deps.restore_overrides(saved)
    return result, err.getvalue()


def run_stage6(evaluations, nct_ids=("NCT00000001",), node=None):
    """Drive Stage 6 over a chosen evaluation list."""
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
            out = node(state)["result"]
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        out = _raised_final(exc)
    return out, err.getvalue()


def log_records(stderr_text, event=None):
    """Every structured record on the captured stream, optionally by event.

    The audit lists this file asserts on are function locals whose only
    consumer is a log line, so the log IS the observation point -- and reading
    it exercises the real emission path rather than a private variable.
    """
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


def field(records, key):
    """One field off the FIRST record, or a named absence.

    NEVER ``records[0][key]``: a defect that stops a record being emitted is
    exactly what these checks exist to catch, and a bare index turns that into
    an IndexError at module level.
    """
    if not records:
        return "<no such record>"
    return records[0].get(key, "<no such field>")


def eval_of(result, nct_id="NCT00000001"):
    """The evaluation entry for one trial, or a named absence."""
    for e in result.get("evaluations", []):
        if e.get("nct_id") == nct_id:
            return e
    return {}


def verdict_of(result, nct_id="NCT00000001"):
    return eval_of(result, nct_id).get("eligible", "<absent>")


def bucket_of(final_result, nct_id="NCT00000001"):
    for name in ("matches", "near_misses", "not_evaluable"):
        if any(e.get("nct_id") == nct_id for e in final_result[name]):
            return name
    return "<absent>"


# The response shape this whole file is about: a readable rejection with two
# criteria, neither of them disqualifying.
UNSUPPORTED = [entry("NCT00000001", "not_eligible",
                     inclusion=[crit("met", text="Age 18 or older")],
                     exclusion=[crit("not_violated", text="Pregnancy")])]


# ===========================================================================
# SECTION 1 -- the reason constant
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 1 -- the reason constant")
print("=" * 75)

check("the reason names what is missing: the arrays, not the label",
      UNEVALUABLE_REJECTION_UNSUPPORTED,
      "model rejection unsupported by its own criteria arrays")
check("it is DISTINCT from the unrecognised-label reason beside it",
      UNEVALUABLE_REJECTION_UNSUPPORTED == UNEVALUABLE_UNRECOGNIZED_VERDICT,
      False)

# It is a `reason` in unevaluable_trials, NOT a value of the
# not_evaluable_reason FIELD: _unevaluable_entry indexes a fixed explanation
# table with those, so a member added there would be a KeyError waiting for the
# first caller who passed it.
check("it is not a member of the not_evaluable_reason vocabulary",
      UNEVALUABLE_REJECTION_UNSUPPORTED
      in _evaluation_module._NOT_EVALUABLE_REASONS,
      False)
check("non-degeneracy: that vocabulary is non-empty, so the check above is "
      "not a test against an empty tuple",
      len(_evaluation_module._NOT_EVALUABLE_REASONS) > 0, True)


# ===========================================================================
# SECTION 2 -- the correction fires, and what it writes
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 2 -- an unsupported rejection is corrected to not_evaluable")
print("=" * 75)

_res, _err = run_stage5(UNSUPPORTED)
_e = eval_of(_res)

check("the verdict is corrected to not_evaluable",
      _res and verdict_of(_res), TRIAL_VERDICT_NOT_EVALUABLE)
check("the score is zero by verdict", _e.get("match_score"), 0.0)
check("score_confirmed is recorded, not left absent",
      _e.get("score_confirmed"), 0)
check("score_denominator is recorded, not left absent",
      _e.get("score_denominator"), 0)

# THE ARRAYS ARE THE EVIDENCE THAT THERE WAS NO EVIDENCE. Both are stored
# verbatim in criterion_details, so a reader can see for themselves that no row
# carried a disqualifying status.
check("the inclusion array survives, unchanged",
      [(c["criterion"], c["status"]) for c in _e.get("inclusion_criteria", [])],
      [("Age 18 or older", "met")])
check("the exclusion array survives, unchanged",
      [(c["criterion"], c["status"]) for c in _e.get("exclusion_criteria", [])],
      [("Pregnancy", "not_violated")])

# The audit entry, read off the log line that consumes unevaluable_trials.
_rec = log_records(_err, "not_evaluable")
check("the correction is recorded in the not_evaluable audit",
      field(_rec, "reason"), [UNEVALUABLE_REJECTION_UNSUPPORTED])
check("exactly one trial is recorded",
      field(_rec, "not_evaluable"), 1)

# It is NOT reported as a label defect: the label was perfectly readable.
check("nothing is recorded in verdict_normalizations -- the LABEL was fine",
      len(log_records(_err, "verdict_normalization")), 0)

# NON-DEGENERACY: the same driver, on a response that needs no correction,
# emits no not_evaluable record at all. Without this the checks above could be
# satisfied by a node that recorded everything.
_clean, _clean_err = run_stage5(
    [entry("NCT00000001", "eligible", inclusion=[crit("met")])])
check("non-degeneracy: a clean response emits no not_evaluable record",
      len(log_records(_clean_err, "not_evaluable")), 0)


# ===========================================================================
# SECTION 3 -- it does NOT fire when a disqualifier survives
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 3 -- one surviving disqualifier, however weak, is enough")
print("=" * 75)

# One row per arm, each on its own, plus a row buried among confirming rows.
# "However weak" is the point: this check reads the STATUS and nothing else --
# it does not weigh the criterion, the patient_value or the model's prose.
_SURVIVING = (
    ("an inclusion criterion marked not_met",
     [entry("NCT00000001", "not_eligible",
            inclusion=[crit("met"), crit("not_met", text="Prior therapy")],
            exclusion=[crit("not_violated")])]),
    ("an exclusion criterion marked violated",
     [entry("NCT00000001", "not_eligible",
            inclusion=[crit("met")],
            exclusion=[crit("not_violated"), crit("violated", text="Pregnancy")])]),
    ("a lone not_met with nothing else in either array",
     [entry("NCT00000001", "not_eligible", inclusion=[crit("not_met")])]),
    ("a disqualifier whose criterion text is empty",
     [entry("NCT00000001", "not_eligible", inclusion=[crit("not_met", text="")])]),
)

for _label, _payload in _SURVIVING:
    _r, _rerr = run_stage5(_payload)
    check(f"{_label}: the rejection STANDS", verdict_of(_r),
          TRIAL_VERDICT_NOT_ELIGIBLE)
    check(f"{_label}: nothing is recorded as not evaluable",
          len(log_records(_rerr, "not_evaluable")), 0)

# The sibling branch is untouched: a rejection whose only disqualifier was an
# out-of-vocabulary label still resolves under its OWN reason, not this one.
_remap, _remap_err = run_stage5(
    [entry("NCT00000001", "not_eligible",
           inclusion=[crit("violated", text="a cross-arm label")])])
check("the sibling out-of-vocabulary branch still answers, under its own "
      "reason", field(log_records(_remap_err, "not_evaluable"), "reason"),
      ["sole disqualifier was an out-of-vocabulary label"])
check("...and it still reaches not_evaluable", verdict_of(_remap),
      TRIAL_VERDICT_NOT_EVALUABLE)


# ===========================================================================
# SECTION 4 -- the other two verdicts are untouched
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 4 -- eligible and not_evaluable are untouched")
print("=" * 75)

_elig, _elig_err = run_stage5(
    [entry("NCT00000001", "eligible",
           inclusion=[crit("met")], exclusion=[crit("not_violated")],
           assessment="No known disqualifiers.")])
check("an eligible trial stays eligible", verdict_of(_elig),
      TRIAL_VERDICT_ELIGIBLE)
check("...and is SCORED by ratio, not zeroed by verdict",
      eval_of(_elig).get("match_score"), 1.0)
check("...and denominator is the applicable-criteria count",
      eval_of(_elig).get("score_denominator"), 2)
check("...and nothing is recorded as not evaluable",
      len(log_records(_elig_err, "not_evaluable")), 0)

# A model-declared not_evaluable WITH criteria present reaches the same
# fall-through branch this change edits, and must come out of it unchanged.
_ne, _ne_err = run_stage5(
    [entry("NCT00000001", "not_evaluable",
           inclusion=[crit("not_evaluable")],
           assessment="Not evaluable: the criteria text was unreadable.")])
check("a model-declared not_evaluable stays not_evaluable", verdict_of(_ne),
      TRIAL_VERDICT_NOT_EVALUABLE)
check("...and is NOT recorded under the new reason -- it was never a "
      "rejection", len(log_records(_ne_err, "not_evaluable")), 0)

# The synonym path: a recovered non-canonical rejection label reaches the same
# branch, so the correction must apply to it too -- and the SPELLING must be
# recorded separately, in verdict_normalizations, not folded into this finding.
#
# EVERY MEMBER BELOW IS ONE normalize_trial_verdict CAN READ, and that had to
# be measured rather than assumed: the first version of this list carried
# "Not Eligible", with a space, which the recovery vocabulary does NOT contain
# -- it folds case, whitespace and the underscore form, and adds the four
# synonyms node_finalize has always carried, and nothing else. That entry took
# the unrecognised-label arm instead and the check failed, correctly. A label
# the normalizer cannot read never reaches this correction at all; that case is
# `_unk` below.
for _raw in ("NOT_ELIGIBLE", "  not_eligible  ", False, "no"):
    _s, _serr = run_stage5(
        [entry("NCT00000001", _raw, inclusion=[crit("met")])])
    check(f"a recovered {_raw!r} rejection is corrected too", verdict_of(_s),
          TRIAL_VERDICT_NOT_EVALUABLE)
    check(f"...recorded under the evidence reason, with the canonical label",
          field(log_records(_serr, "not_evaluable"), "reason"),
          [UNEVALUABLE_REJECTION_UNSUPPORTED])
    check(f"...and the spelling is reported separately",
          len(log_records(_serr, "verdict_normalization")), 1)

# An UNRECOGNISED label with non-disqualifying criteria keeps its own reason:
# the two arms of the fall-through branch are disjoint and must stay so.
_unk, _unk_err = run_stage5(
    [entry("NCT00000001", "elligible", inclusion=[crit("met")])])
check("an unrecognised label keeps the label reason, not the evidence reason",
      field(log_records(_unk_err, "not_evaluable"), "reason"),
      [UNEVALUABLE_UNRECOGNIZED_VERDICT])


# ===========================================================================
# SECTION 5 -- node_finalize: the corrected trial is not a near miss
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 5 -- Stage 6 puts it in not_evaluable, never in near_misses")
print("=" * 75)

_res5, _ = run_stage5(UNSUPPORTED)
_final, _ = run_stage6(_res5["evaluations"])

check("the corrected trial lands in not_evaluable", bucket_of(_final),
      "not_evaluable")
check("near_misses is empty", len(_final["near_misses"]), 0)
check("matches is empty -- it was NOT promoted", len(_final["matches"]), 0)
check("the reported non-evaluation count sees it",
      _final.get("not_evaluable_trials"), 1)

# NON-DEGENERACY: the same driver puts a REAL rejection in near_misses, so the
# bucket assertion above is about the correction rather than about a harness
# that reports "not_evaluable" for everything.
_real, _ = run_stage5(
    [entry("NCT00000001", "not_eligible", inclusion=[crit("not_met")])])
_real_final, _ = run_stage6(_real["evaluations"])
check("non-degeneracy: a supported rejection still lands in near_misses",
      bucket_of(_real_final), "near_misses")


# ===========================================================================
# SECTION 6 -- the composed assessment, and the anomaly counter
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 6 -- composition case and the backstop counter")
print("=" * 75)

check("the corrected trial composes as kept_draft_not_evaluable",
      assessment_composition_case(_e), ASSESSMENT_KEPT_NOT_EVALUABLE)
check("...which is NOT one of the two anomaly cases",
      assessment_composition_case(_e) == ASSESSMENT_KEPT_NO_DISQUALIFIER,
      False)

# WHAT A DATABASE READER SEES, asserted rather than described. The stored
# assessment for a corrected trial is the model's own draft, opening "Known
# disqualifier:" -- because this column stores the draft for every
# not_evaluable trial, and nothing was composed. It is recorded here so the
# next reader of trial_matches.assessment meets this in a test rather than in
# a patient record.
check("the STORED assessment is the model's draft, opening 'Known "
      "disqualifier:'",
      str(_e.get("assessment", "")).startswith(ASSESSMENT_NOT_ELIGIBLE_OPENING),
      True)
check("...and the draft is preserved beside it, unchanged",
      _e.get("assessment_draft"), _e.get("assessment"))

# The counter is the backstop, and it must read zero on the pipeline path.
_anomaly_delta = {
    k: ASSESSMENT_COMPOSITION_ANOMALIES.get(k, 0) - _ANOMALIES_BEFORE.get(k, 0)
    for k in set(ASSESSMENT_COMPOSITION_ANOMALIES) | set(_ANOMALIES_BEFORE)
}
check("no composition anomaly was recorded by anything this file ran",
      {k: v for k, v in _anomaly_delta.items() if v}, {})
check("the anomaly counter still exists and is unweakened",
      ASSESSMENT_KEPT_NO_DISQUALIFIER in ASSESSMENT_CASES, True)
check("non-degeneracy: the counter is a live Counter, not a stub that "
      "swallows increments",
      _evaluation_module.ASSESSMENT_COMPOSITION_ANOMALIES
      is ASSESSMENT_COMPOSITION_ANOMALIES, True)


# ===========================================================================
# SECTION 7 -- ordering against the absent-data validator
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 7 -- the two mechanisms cannot fight over one trial")
print("=" * 75)

# THE ORDER, read off the source rather than asserted from memory: the
# normalizer's per-evaluation loop runs FIRST and the absent-data validator
# runs after it, over the same list.
_src = open(_EVAL_SRC, encoding="utf-8").read()
_i_norm = _src.find("Step 3: disqualification check")
_i_absent = _src.find("Absent-data validator")
check("non-degeneracy: both markers were found in the source",
      _i_norm > 0 and _i_absent > 0, True)
check("the normalizer runs BEFORE the absent-data validator",
      _i_norm < _i_absent, True)

# THE STRUCTURAL REASON THEY CANNOT COLLIDE. This correction fires only when NO
# disqualifying row exists; the validator fires only when at least one does.
# So a trial the validator would have promoted to "eligible" cannot be taken by
# the correction first -- which would be as damaging as the defect itself,
# recording a real match as unassessable.
_absent_only, _absent_err = run_stage5(
    [entry("NCT00000001", "not_eligible",
           inclusion=[crit("not_met", patient_value=ABSENT,
                           text="Documented EGFR mutation")])])
check("a rejection resting on absent data is still flipped to ELIGIBLE by the "
      "validator", verdict_of(_absent_only), TRIAL_VERDICT_ELIGIBLE)
check("...and the correction did NOT claim it first",
      len(log_records(_absent_err, "not_evaluable")), 0)
check("...and the validator recorded its own correction",
      field(log_records(_absent_err, "absent_data_correction"), "count"), 1)

# Mixed: one absent-data disqualifier and one real one. The validator corrects
# the first, the second survives, the rejection stands -- and this correction
# still stays out of it, because it already ran and saw a disqualifier.
_mixed, _mixed_err = run_stage5(
    [entry("NCT00000001", "not_eligible",
           inclusion=[crit("not_met", patient_value=ABSENT),
                      crit("not_met", text="Prior therapy")])])
check("a real disqualifier beside an absent-data one keeps the rejection",
      verdict_of(_mixed), TRIAL_VERDICT_NOT_ELIGIBLE)
check("...and nothing was recorded as not evaluable",
      len(log_records(_mixed_err, "not_evaluable")), 0)

# The other direction: a trial this correction moved to not_evaluable is
# skipped by the validator outright, because the validator's first test is
# `eligible != "not_eligible": continue`. Asserted through the outcome -- the
# verdict does not move again -- and through the absence of a correction record.
check("a corrected trial is not touched by the validator afterwards",
      len(log_records(_err, "absent_data_correction")), 0)
check("...and its verdict is still not_evaluable at the end of the node",
      verdict_of(_res), TRIAL_VERDICT_NOT_EVALUABLE)


# ===========================================================================
# SECTION 8 -- what a database reader sees, run rather than described
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 8 -- the stored row for a corrected trial")
print("=" * 75)

# No schema change was made by this pass, so the whole question is what the
# EXISTING columns say -- and reading the INSERT statement is not the same as
# writing a row and reading it back. Written to a scratch database in a temp
# directory, asserted to differ from the production path, and removed.
_db_dir = tempfile.mkdtemp(prefix="oncotriage_unsupported_rejection_")
_db = os.path.join(_db_dir, "inferences_test.db")
try:
    check("the scratch database is NOT the production one",
          os.path.abspath(_db)
          == os.path.abspath(_database_logger.resolve_inference_db_path(None)),
          False)

    _stage6, _ = run_stage6(run_stage5(UNSUPPORTED)[0]["evaluations"])
    _err_db = io.StringIO()
    with contextlib.redirect_stderr(_err_db):
        _written = _database_logger.log_inference(_stage6, PATIENT, db_path=_db)
    check("the row was written", getattr(_written, "ok", False), True)
    check("...to the scratch path", str(_written), _db)

    _conn = sqlite3.connect(_db)
    _row = _conn.execute(
        "SELECT eligible, match_score, score_confirmed, score_denominator, "
        "       assessment, criterion_details, hallucinated, "
        "       emission_index, call_index "
        "FROM trial_matches WHERE nct_id = ?", ("NCT00000001",)).fetchone()
    _conn.close()

    check("non-degeneracy: exactly one trial_matches row came back",
          _row is not None, True)
    if _row is not None:
        (_eligible, _score, _conf, _denom, _assess, _details, _hall,
         _emission, _call) = _row

        check("eligible reads not_evaluable", _eligible, "not_evaluable")
        check("match_score is 0.0, not NULL", _score, 0.0)
        check("score_confirmed is 0, not NULL", _conf, 0)
        check("score_denominator is 0, not NULL", _denom, 0)
        check("hallucinated is 0 -- the detector RAN", _hall, 0)

        # THE DISCRIMINATOR. A trial the pipeline CONSTRUCTED as not evaluable
        # (a truncation floor, a split budget, a model omission, conflicting
        # duplicates) carries NULL on both of these by design. A corrected
        # rejection stood in a real model response, so both are integers. That
        # is the only thing in the schema separating the two populations, and
        # it is why no reader should treat eligible='not_evaluable' as one kind
        # of row.
        check("emission_index is a real position, not NULL",
              isinstance(_emission, int), True)
        check("call_index is a real position, not NULL",
              isinstance(_call, int), True)

        # criterion_details holds the arrays verbatim -- the evidence that
        # there was no evidence -- and no row in them disqualifies anybody.
        _parsed = json.loads(_details)
        check("criterion_details carries both arrays",
              (len(_parsed["inclusion"]), len(_parsed["exclusion"])), (1, 1))
        check("...and NO stored row carries a disqualifying status",
              [c["status"] for c in _parsed["inclusion"] + _parsed["exclusion"]
               if c["status"] in ("not_met", "violated")], [])

        # AND THE ONE THING A READER MUST BE WARNED ABOUT. `assessment` stores
        # the model's draft for every not_evaluable trial, so a corrected
        # rejection's stored prose still opens "Known disqualifier:" beside a
        # verdict that is not a rejection and criteria that carry none. No
        # column names the correction; the Stage 5 log event does. Asserted
        # here rather than described, so the next reader meets it in a test.
        check("assessment still opens with the rejection opening",
              str(_assess).startswith(ASSESSMENT_NOT_ELIGIBLE_OPENING), True)
finally:
    shutil.rmtree(_db_dir, ignore_errors=True)

check("the scratch database was removed", os.path.exists(_db_dir), False)


# ===========================================================================
# SECTION 9 -- NEGATIVE CONTROL: the defect, reproduced
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 9 -- with the new branch bypassed, the fabrication returns")
print("=" * 75)

# The plant disables ONLY the new arm. Everything else in the file -- Step 0,
# Step 1, Step 3, the sibling out-of-vocabulary branch, the absent-data
# validator, the composition -- runs exactly as shipped, so what the control
# demonstrates is this branch and nothing else.
_BYPASS = [(
    '            elif eval_result["eligible"] == TRIAL_VERDICT_NOT_ELIGIBLE:\n'
    '                # `original_label` is the CANONICAL constant',
    '            elif False:  # PLANTED: the new arm, bypassed\n'
    '                # `original_label` is the CANONICAL constant',
)]

try:
    _pre_fix = _plant(_EVAL_SRC, "evaluation_pre_fix", _BYPASS)
except _PlantFailed as _exc:
    _pre_fix = None
    check(f"[THE PLANT ITSELF FAILED: {_exc}]", "plant-failed", "planted")

if _pre_fix is not None:
    check("the plant applied and the copy imported", True, True)

    _old, _old_err = run_stage5(
        UNSUPPORTED, node=_pre_fix.node_llm_classifier_evaluation)
    check("CONTROL: the unsupported rejection SURVIVES as a rejection",
          verdict_of(_old), TRIAL_VERDICT_NOT_ELIGIBLE)
    check("CONTROL: nothing is recorded as not evaluable",
          len(log_records(_old_err, "not_evaluable")), 0)

    _old_final, _ = run_stage6(_old["evaluations"])
    check("CONTROL: it reaches the patient's near_misses",
          bucket_of(_old_final), "near_misses")
    check("CONTROL: and not the non-evaluations",
          len(_old_final["not_evaluable"]), 0)

    # AND THE BACKSTOP FIRES, in the planted module's OWN counter -- which is
    # what makes ASSESSMENT_KEPT_NO_DISQUALIFIER's comment true for the first
    # time. Before this fix that case was reachable from the pipeline; the
    # counter was not a guard against an impossible state, it was the only
    # thing recording a real one.
    check("CONTROL: the composition anomaly counter fires in the copy",
          _pre_fix.ASSESSMENT_COMPOSITION_ANOMALIES.get(
              ASSESSMENT_KEPT_NO_DISQUALIFIER, 0), 1)
    check("CONTROL: the case is the no-disqualifier one",
          _pre_fix.assessment_composition_case(eval_of(_old)),
          ASSESSMENT_KEPT_NO_DISQUALIFIER)

    # NON-DEGENERACY: the planted module still agrees with the shipped one
    # everywhere the plant does not reach, so "the control fires" is about the
    # branch rather than about a copy that is broken generally.
    for _name, _payload, _want in (
        ("a supported rejection", [entry(
            "NCT00000001", "not_eligible", inclusion=[crit("not_met")])],
         TRIAL_VERDICT_NOT_ELIGIBLE),
        ("an eligible trial", [entry(
            "NCT00000001", "eligible", inclusion=[crit("met")])],
         TRIAL_VERDICT_ELIGIBLE),
        ("the sibling out-of-vocabulary case", [entry(
            "NCT00000001", "not_eligible", inclusion=[crit("violated")])],
         TRIAL_VERDICT_NOT_EVALUABLE),
    ):
        check(f"CONTROL non-degeneracy: {_name} is unchanged by the plant",
              verdict_of(run_stage5(
                  _payload, node=_pre_fix.node_llm_classifier_evaluation)[0]),
              _want)


# ===========================================================================
# SECTION 10 -- nothing on disk was touched
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 10 -- every plant was in memory")
print("=" * 75)

check("oncotriage/agent/evaluation.py is byte-identical to its pre-run state",
      _sha256_of(_EVAL_SRC), _SHA_BEFORE)
check("non-degeneracy: the baseline hash is a real digest of real content",
      _SHA_BEFORE == hashlib.sha256(b"").hexdigest(), False)


# ===========================================================================

print("\n" + "=" * 75)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 75)
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Aug 13 2026

@author: ramyalsaffar
"""
