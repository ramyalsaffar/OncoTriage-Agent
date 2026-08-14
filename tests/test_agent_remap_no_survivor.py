###################################################################
# Stage 5: a rejection whose disqualifiers did not survive normalisation
###################################################################

"""
Remap-No-Survivor Test

THE DEFECT. ``node_llm_classifier_evaluation``'s post-processing loop corrects
a model-declared ``not_eligible`` whose every disqualifying label was out of
its arm's vocabulary: Step 1 resolves those labels to ``not_evaluable``, Step 3
then finds no surviving disqualifier, and the branch guarded by
``remapped_here`` records the trial under ``UNEVALUABLE_REMAP_NO_SURVIVOR`` and
moves the verdict to ``not_evaluable``.

It moved the VERDICT and left the ASSESSMENT alone. The branch set no marker on
the entry, so ``assessment_composition_case`` routed it to the keep-the-draft
fall-through and the stored assessment stayed the model's rejection prose --
"Known disqualifier: ..." -- in the column a clinician reads, beside a verdict
saying the trial was never evaluated. The row contradicted itself.

That is the same self-contradicting row the SIBLING branch
(``UNEVALUABLE_REJECTION_UNSUPPORTED``, a rejection that cited no disqualifying
criterion at all) was fixed for, reached by the other route. The fix here is
the same shape: a marker on the entry, a case of its own, and a fixed composed
text.

WHY THE TEXT IS NOT THE SIBLING'S, which is the whole of what this file has to
prove. ``remapped_here`` is true when ANY row on the trial was remapped --
including a row that was never disqualifying, and including a non-dict entry
that ``_normalize_arm`` DROPPED rather than relabelled. And a remapped row may
well have been a real disqualifier the model spelled wrong: ``_normalize_arm``
refuses to guess, on purpose, because guessing would let an unparseable label
disqualify a patient with nothing quotable behind it. So this population's
composed text may not say the model cited no disqualifying criterion (the
sibling's sentence, and the one claim the evidence here cannot support), and it
may not say the remapped rows were the disqualifiers either. It says only what
is known: the rejection was made, after label normalisation no disqualifying
row is left, and the verdict was corrected.

WHY NOT KEEP THE DRAFT, which is what the code did and argued for. The argument
was that a composed text would over-claim, and it was sound about every text
then on offer. Its conclusion was not: keeping the draft does not avoid a false
statement, it stores a different one -- a rejection in the assessment column of
a row whose verdict is not_evaluable. The choice was between over-claiming and
contradicting; a text that does neither settles it.

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every model response
here is a literal built in this file and served by a stub installed through
``oncotriage/agent/deps.py``. NOT in tests/run_serial_tests.py's collision
matrix: it writes nothing anywhere -- every plant goes into an in-memory copy,
with the source file hashed before any plant and compared at the end -- and the
one repository file it reads is written by neither of the suite's two writers.

    python tests/test_agent_remap_no_survivor.py
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
    ASSESSMENT_COMPOSED_CASES,
    ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR,
    ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
    ASSESSMENT_COMPOSITION_ANOMALIES,
    ASSESSMENT_KEPT_NOT_EVALUABLE,
    ASSESSMENT_KEPT_NO_DISQUALIFIER,
    ASSESSMENT_NOT_ELIGIBLE_OPENING,
    ASSESSMENT_NOT_EVALUABLE_OPENING,
    ASSESSMENT_REMAP_NO_SURVIVOR_TEXT,
    ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
    NOT_EVALUABLE_MODEL_OMITTED,
    UNEVALUABLE_REJECTION_UNSUPPORTED,
    UNEVALUABLE_REMAP_NO_SURVIVOR,
    UNEVALUABLE_UNRECOGNIZED_VERDICT,
    assessment_composition_case,
    compose_assessment,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.response_schema import build_response_schema
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


# Taken before any plant runs, so the restore assertion in the last section
# compares against a real baseline rather than against itself.
_SHA_BEFORE = _sha256_of(_EVAL_SRC)

# The module-level anomaly counter is shared by every consumer in the process.
# Snapshotting it means section 5 asserts on what THIS file caused rather than
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
    "patient_id": "remap-no-survivor-patient",
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
# test in section 6 exercises the real predicate rather than a near-miss.
ABSENT = "Not in patient record"

DRAFT = "Known disqualifier: the model said so."


def crit(status, patient_value=DOCUMENTED, text="an eligibility criterion"):
    """One criterion row."""
    return {"criterion": text, "status": status, "patient_value": patient_value}


def entry(nct_id, eligible, inclusion=(), exclusion=(), assessment=DRAFT):
    """One evaluation entry as the model returns it.

    The default assessment opens with the mandated rejection opening, because
    the population this file is about is one the model believed it was
    rejecting -- and that text is exactly what must stop being stored.
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


# A RAISE IS AN OUTCOME, NOT A REASON TO ABORT. Reverting the fix this file
# covers can make Stage 5 raise, and with a bare call that raise escapes
# through check()'s argument list and takes the whole run down -- one traceback
# where the file owed every result below it. The two drivers below never
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
    """Every structured record on the captured stream, optionally by event."""
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


def marker_of(result, nct_id="NCT00000001"):
    """The entry's not_evaluable_reason, or a named absence."""
    return eval_of(result, nct_id).get("not_evaluable_reason", "<no key>")


# ---------------------------------------------------------------------------
# THE THREE SHAPES THAT REACH THIS BRANCH, and they are genuinely different
# inputs rather than one case written three ways. All three are a rejection
# that leaves no disqualifying row behind, and `remapped_here` is true for each
# for a different reason -- which is precisely why the composed text may not
# say what was remapped or why.
# ---------------------------------------------------------------------------

# A label that IS a disqualifier, in the wrong arm's vocabulary. "violated" is
# an exclusion status; on an inclusion row it is out of vocabulary and
# _normalize_arm resolves it rather than guessing the model meant "not_met".
REMAP_DISQUALIFYING = [entry(
    "NCT00000001", "not_eligible",
    inclusion=[crit("violated", text="a cross-arm disqualifying label")])]

# A remapped row that was NEVER disqualifying, beside a confirming one. The old
# reason string was false of exactly this shape, and it is why nothing composed
# here may name the remapped row as the disqualifier.
REMAP_INNOCENT = [entry(
    "NCT00000001", "not_eligible",
    inclusion=[crit("met", text="Age 18 or older"),
               crit("mumble", text="a row that was never disqualifying")])]

# A non-dict entry, DROPPED by _normalize_arm rather than relabelled. It counts
# into label_remaps all the same, so `remapped_here` is true and this branch
# fires -- and no label was rewritten at all, which is the shape that forbids
# the composed text from claiming one was.
REMAP_DROPPED_ROW = [entry(
    "NCT00000001", "not_eligible",
    inclusion=[crit("met", text="Age 18 or older"), "a bare string, not a row"])]

SHAPES = (
    ("a remapped DISQUALIFYING label", REMAP_DISQUALIFYING),
    ("a remapped row that was never disqualifying", REMAP_INNOCENT),
    ("a dropped non-dict row, where no label was rewritten", REMAP_DROPPED_ROW),
)


# ===========================================================================
# SECTION 1 -- the constants
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 1 -- the marker, the case and the text")
print("=" * 75)

check("the reason is unchanged by this pass: it asserts only what "
      "normalization can know", UNEVALUABLE_REMAP_NO_SURVIVOR,
      "no disqualifying row survived label normalisation")
check("...and is DISTINCT from the sibling reason it must not be folded into",
      len({UNEVALUABLE_REMAP_NO_SURVIVOR, UNEVALUABLE_REJECTION_UNSUPPORTED,
           UNEVALUABLE_UNRECOGNIZED_VERDICT}), 3)

# It is a `reason` in unevaluable_trials and a value of the
# not_evaluable_reason FIELD, but NOT a member of the vocabulary
# _unevaluable_entry indexes: those members key a fixed explanation table, so a
# member added there would be a KeyError waiting for the first caller.
check("it is not a member of the not_evaluable_reason CONSTRUCT vocabulary",
      UNEVALUABLE_REMAP_NO_SURVIVOR
      in _evaluation_module._NOT_EVALUABLE_REASONS, False)
check("non-degeneracy: that vocabulary is non-empty, so the check above is "
      "not a test against an empty tuple",
      len(_evaluation_module._NOT_EVALUABLE_REASONS) > 0, True)

check("the case is a member of the closed case vocabulary",
      ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR in ASSESSMENT_CASES, True)
check("...and a COMPOSED one, so `kept = total - composed` counts it",
      ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR in ASSESSMENT_COMPOSED_CASES, True)
check("...and NOT an anomaly case",
      ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR
      in _evaluation_module._ASSESSMENT_ANOMALY_CASES, False)
check("...and distinct from the sibling's case",
      ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR
      == ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION, False)

check("the composed text, verbatim", ASSESSMENT_REMAP_NO_SURVIVOR_TEXT,
      "Not evaluable: The model rejected this trial, but after label "
      "normalisation no row in either criteria array carried a disqualifying "
      "status, so the verdict was corrected to not evaluable.")
check("...opens with the mandated non-evaluation opening, not the rejection "
      "one",
      (ASSESSMENT_REMAP_NO_SURVIVOR_TEXT.startswith(
          ASSESSMENT_NOT_EVALUABLE_OPENING + " "),
       ASSESSMENT_REMAP_NO_SURVIVOR_TEXT.startswith(
           ASSESSMENT_NOT_ELIGIBLE_OPENING)),
      (True, False))
check("...and says what the model did and what the node did",
      ("rejected this trial" in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT,
       "corrected to not evaluable" in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT),
      (True, True))

# THE LIMIT, ASSERTED RATHER THAN INTENDED. These are the two claims the
# evidence cannot support, and the second is the sibling text's own words --
# so a future edit that "unified" the two texts fails here.
check("it does NOT claim the model cited no disqualifying criterion",
      ("cited no disqualifying" in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT,
       "no disqualifying criterion" in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT),
      (False, False))
check("it does NOT claim the remapped rows were the disqualifiers",
      [w for w in ("out-of-vocabulary", "out of vocabulary", "remapped",
                   "the disqualifier", "sole")
       if w in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT], [])
check("the two composed texts are genuinely different strings",
      ASSESSMENT_REMAP_NO_SURVIVOR_TEXT == ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
      False)
check("non-degeneracy: the sibling text DOES make the claim this one avoids, "
      "so the checks above discriminate rather than passing on empty text",
      "cited no disqualifying criterion" in ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
      True)

# NO DIGITS. The scaffolding of every composed text in this module contributes
# none, which is what makes "a numeric token in a stored assessment came from a
# criterion row" true.
check("the text carries no digit, so it can assert no quantity",
      [c for c in ASSESSMENT_REMAP_NO_SURVIVOR_TEXT if c.isdigit()], [])

# THE MARKER IS TRUSTWORTHY ONLY IF THE MODEL CANNOT WRITE IT. Stage 5 sends a
# STRICT json_schema; strict mode requires `additionalProperties: false` and
# enumerates the permitted keys, so `not_evaluable_reason` cannot arrive in a
# model response and an entry carrying it was written by the node. Asserted
# against the real schema rather than assumed, because the whole of section 2
# rests on it.
_schema = build_response_schema()
_trial_props = _schema["properties"]["evaluations"]["items"]
check("the response schema forbids extra keys on a trial entry",
      _trial_props.get("additionalProperties"), False)
check("...and not_evaluable_reason is not one of the keys it permits",
      "not_evaluable_reason" in _trial_props["properties"], False)
check("non-degeneracy: the schema really does enumerate properties, so the "
      "check above is not reading an empty dict",
      sorted(_trial_props["properties"]),
      ["assessment", "eligible", "exclusion_criteria", "inclusion_criteria",
       "match_score", "nct_id"])


# ===========================================================================
# SECTION 2 -- the marker is written, on all three shapes
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 2 -- the branch marks the entry it corrects")
print("=" * 75)

for _label, _payload in SHAPES:
    _r, _rerr = run_stage5(_payload)
    check(f"{_label}: the verdict is corrected to not_evaluable",
          verdict_of(_r), TRIAL_VERDICT_NOT_EVALUABLE)
    check(f"{_label}: recorded in the not_evaluable audit under its reason",
          field(log_records(_rerr, "not_evaluable"), "reason"),
          [UNEVALUABLE_REMAP_NO_SURVIVOR])
    check(f"{_label}: THE MARKER is on the entry",
          marker_of(_r), UNEVALUABLE_REMAP_NO_SURVIVOR)
    check(f"{_label}: and it is NOT the sibling's marker",
          marker_of(_r) == UNEVALUABLE_REJECTION_UNSUPPORTED, False)
    check(f"{_label}: the score is zero by verdict",
          (eval_of(_r).get("match_score"), eval_of(_r).get("score_confirmed"),
           eval_of(_r).get("score_denominator")), (0.0, 0, 0))

# NON-DEGENERACY for the three shapes: each must reach the branch for its own
# reason, or the loop above is one case run three times.
check("non-degeneracy: the three shapes are genuinely different inputs",
      (REMAP_DISQUALIFYING[0]["inclusion_criteria"][0]["status"],
       [c["status"] for c in REMAP_INNOCENT[0]["inclusion_criteria"]],
       [type(c).__name__ for c in REMAP_DROPPED_ROW[0]["inclusion_criteria"]]),
      ("violated", ["met", "mumble"], ["dict", "str"]))
for _label, _payload in SHAPES:
    check(f"non-degeneracy: {_label} really did register a remap",
          len(log_records(run_stage5(_payload)[1], "label_remap")), 1)
# AND THE DROPPED-ROW SHAPE REWROTE NO LABEL AT ALL, which is the measurement
# behind the claim that this population's composed text may not say a label was
# rewritten. Read off the surviving ARRAYS rather than off the label_remap log
# line: that line carries `count` and `total` and nothing else, so a check
# reading `original_status` from it gets None for every shape and passes
# vacuously -- which the first version of this pair did, in both directions.
check("the dropped-row shape LOSES a row and rewrites none: no label was "
      "changed, so nothing composed here may say one was",
      ([(c["criterion"], c["status"])
        for c in eval_of(run_stage5(REMAP_DROPPED_ROW)[0])
        .get("inclusion_criteria", [])],
       len(REMAP_DROPPED_ROW[0]["inclusion_criteria"])),
      ([("Age 18 or older", "met")], 2))
check("...unlike a real relabel, which KEEPS the row and rewrites its status "
      "-- so the two shapes are distinguishable and the check above is not "
      "true of everything",
      ([(c["criterion"], c["status"])
        for c in eval_of(run_stage5(REMAP_DISQUALIFYING)[0])
        .get("inclusion_criteria", [])],
       len(REMAP_DISQUALIFYING[0]["inclusion_criteria"])),
      ([("a cross-arm disqualifying label", "not_evaluable")], 1))

# THE ARRAYS ARE THE EVIDENCE, and they survive with the remapped statuses in
# them: a reader who wants to know what the model wrote reads criterion_details
# rather than the composed sentence.
_res, _err = run_stage5(REMAP_DISQUALIFYING)
_e = eval_of(_res)
check("the inclusion array survives, carrying the normalised status",
      [(c["criterion"], c["status"])
       for c in _e.get("inclusion_criteria", [])],
      [("a cross-arm disqualifying label", "not_evaluable")])


# ===========================================================================
# SECTION 3 -- the marker is NOT written anywhere else
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 3 -- exactly this population, and no other")
print("=" * 75)

# (a) A SUPPORTED rejection. No correction at all.
_sup, _sup_err = run_stage5(
    [entry("NCT00000001", "not_eligible",
           inclusion=[crit("not_met", text="Prior therapy")])])
check("a supported rejection STANDS", verdict_of(_sup),
      TRIAL_VERDICT_NOT_ELIGIBLE)
check("...and carries no marker", marker_of(_sup), "<no key>")
check("...and nothing is recorded as not evaluable",
      len(log_records(_sup_err, "not_evaluable")), 0)

# (b) A SURVIVING disqualifier beside an UNRELATED remap. This is the case the
# branch must not claim: `remapped_here` is true, and Step 3 outranks it
# because a row the model wrote still disqualifies.
_survivor, _survivor_err = run_stage5(
    [entry("NCT00000001", "not_eligible",
           inclusion=[crit("not_met", text="Prior therapy"),
                      crit("mumble", text="an unrelated row")])])
check("a surviving disqualifier beside an unrelated remap: the rejection "
      "STANDS", verdict_of(_survivor), TRIAL_VERDICT_NOT_ELIGIBLE)
check("...and carries no marker", marker_of(_survivor), "<no key>")
check("...and nothing is recorded as not evaluable",
      len(log_records(_survivor_err, "not_evaluable")), 0)
check("non-degeneracy: that payload really did remap a row, so the check "
      "above is about Step 3 outranking the branch rather than about a "
      "payload that never reached it",
      len(log_records(_survivor_err, "label_remap")), 1)

# (c) A model-declared not_evaluable with the empty arrays its contract
# mandates. It reaches the fall-through branch and must leave it untouched.
_ne, _ne_err = run_stage5(
    [entry("NCT00000001", "not_evaluable",
           assessment="Not evaluable: the criteria text was unreadable.")])
check("a model-declared not_evaluable with empty arrays stays not_evaluable",
      verdict_of(_ne), TRIAL_VERDICT_NOT_EVALUABLE)
check("...and carries no marker", marker_of(_ne), "<no key>")
check("...and keeps its own draft",
      (assessment_composition_case(eval_of(_ne)),
       eval_of(_ne).get("assessment")),
      (ASSESSMENT_KEPT_NOT_EVALUABLE,
       "Not evaluable: the criteria text was unreadable."))

# (d) THE SIBLING CLASS. A readable rejection with no disqualifying row and NO
# remap: it must keep its OWN marker, its OWN case and its OWN text. This is
# the check that stops the two branches being collapsed into one.
_sib, _sib_err = run_stage5(
    [entry("NCT00000001", "not_eligible",
           inclusion=[crit("met", text="Age 18 or older")],
           exclusion=[crit("not_violated", text="Pregnancy")])])
check("the sibling class keeps its own marker", marker_of(_sib),
      UNEVALUABLE_REJECTION_UNSUPPORTED)
check("...its own composition case",
      assessment_composition_case(eval_of(_sib)),
      ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION)
check("...and its own text, which is the stronger sentence",
      eval_of(_sib).get("assessment"), ASSESSMENT_UNSUPPORTED_REJECTION_TEXT)
check("non-degeneracy: the sibling payload registered NO remap, which is what "
      "routes it to the other branch",
      len(log_records(_sib_err, "label_remap")), 0)

# (e) An UNRECOGNISED label with non-disqualifying criteria keeps its own
# reason and gets NO marker: the fall-through branch's two arms are disjoint.
_unk, _unk_err = run_stage5(
    [entry("NCT00000001", "elligible", inclusion=[crit("met")])])
check("an unrecognised label keeps the label reason",
      field(log_records(_unk_err, "not_evaluable"), "reason"),
      [UNEVALUABLE_UNRECOGNIZED_VERDICT])
check("...and gets no marker at all", marker_of(_unk), "<no key>")

# (f) An eligible trial, and a CONSTRUCTED non-evaluation, for completeness.
_elig, _ = run_stage5([entry("NCT00000001", "eligible",
                             inclusion=[crit("met")],
                             assessment="No known disqualifiers.")])
check("an eligible trial carries no marker", marker_of(_elig), "<no key>")
_recon, _ = run_stage5(
    [entry("NCT00000001", "eligible", inclusion=[crit("met")])],
    nct_ids=("NCT00000001", "NCT00000002"))
check("a CONSTRUCTED non-evaluation carries its own reason, not this marker",
      (marker_of(_recon, "NCT00000002"),
       marker_of(_recon, "NCT00000002") == UNEVALUABLE_REMAP_NO_SURVIVOR),
      (NOT_EVALUABLE_MODEL_OMITTED, False))


# ===========================================================================
# SECTION 4 -- composition: the new text, and the draft beside it
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 4 -- what is stored, and what is kept")
print("=" * 75)

for _label, _payload in SHAPES:
    _r, _ = run_stage5(_payload)
    _ent = eval_of(_r)
    check(f"{_label}: composes under its own case",
          assessment_composition_case(_ent),
          ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR)
    check(f"{_label}: the STORED assessment is the new text",
          _ent.get("assessment"), ASSESSMENT_REMAP_NO_SURVIVOR_TEXT)
    check(f"{_label}: the DRAFT is the model's rejection prose, preserved",
          _ent.get("assessment_draft"), DRAFT)
    check(f"{_label}: non-degeneracy: stored and draft genuinely differ, so "
          f"'composed' is not a name for keeping the draft",
          _ent.get("assessment") == _ent.get("assessment_draft"), False)
    check(f"{_label}: the row no longer contradicts its own verdict",
          (str(_ent.get("assessment", "")).startswith(
              ASSESSMENT_NOT_EVALUABLE_OPENING),
           str(_ent.get("assessment", "")).startswith(
               ASSESSMENT_NOT_ELIGIBLE_OPENING)),
          (True, False))

# THE TEXT IS EMITTED FOR THIS MARKER ONLY. Driven as a pure function over
# hand-built verdicts, so the marker and the verdict can be varied
# independently -- which no node driver can do.
_v_marked_ne = {"eligible": TRIAL_VERDICT_NOT_EVALUABLE,
                "inclusion_criteria": [crit("not_evaluable")],
                "exclusion_criteria": [],
                "assessment": DRAFT,
                "not_evaluable_reason": UNEVALUABLE_REMAP_NO_SURVIVOR}
check("the marker on a not_evaluable verdict composes the new text",
      (assessment_composition_case(_v_marked_ne),
       compose_assessment(_v_marked_ne)),
      (ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR,
       ASSESSMENT_REMAP_NO_SURVIVOR_TEXT))

_v_marked_elig = dict(_v_marked_ne, eligible=TRIAL_VERDICT_ELIGIBLE,
                      inclusion_criteria=[crit("met")])
check("the marker ALONE does not compose it: the verdict must also be "
      "not_evaluable",
      (assessment_composition_case(_v_marked_elig),
       compose_assessment(_v_marked_elig)
       == ASSESSMENT_REMAP_NO_SURVIVOR_TEXT),
      (_evaluation_module.ASSESSMENT_COMPOSED_ELIGIBLE, False))

_v_other = dict(_v_marked_ne, not_evaluable_reason=NOT_EVALUABLE_MODEL_OMITTED,
                assessment="Not evaluable: x")
check("a not_evaluable carrying a DIFFERENT reason keeps its draft",
      (assessment_composition_case(_v_other), compose_assessment(_v_other)),
      (ASSESSMENT_KEPT_NOT_EVALUABLE, "Not evaluable: x"))

check("compose_assessment is still PURE: the draft on the verdict it was "
      "handed is untouched", _v_marked_ne["assessment"], DRAFT)

# ALL FOUR CONSTRUCTED REASONS still keep their purpose-written text.
for _reason in _evaluation_module._NOT_EVALUABLE_REASONS:
    _built = _evaluation_module._unevaluable_entry(
        {"trial": trial("NCT00000009")}, _reason)
    check(f"constructed {_reason!r} keeps its text",
          (assessment_composition_case(_built),
           compose_assessment(_built) == _built["assessment"]),
          (ASSESSMENT_KEPT_NOT_EVALUABLE, True))


# ===========================================================================
# SECTION 5 -- the counters, and the buckets
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 5 -- counted as composed, and filed as a non-evaluation")
print("=" * 75)

_comp = log_records(_err, "assessment_composition")
check("the composition event counts the corrected trial as COMPOSED, not kept",
      (field(_comp, "count"), field(_comp, "kept"), field(_comp, "total")),
      (1, 0, 1))
check("...and names the new case", field(_comp, "reason"),
      [ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR])
_comp_kept = log_records(_ne_err, "assessment_composition")
check("non-degeneracy: a kept entry is counted as KEPT, so the composed count "
      "above is not what this event always reports",
      (field(_comp_kept, "count"), field(_comp_kept, "kept"),
       field(_comp_kept, "total")), (0, 1, 1))

_final, _ = run_stage6(run_stage5(REMAP_DISQUALIFYING)[0]["evaluations"])
check("the corrected trial lands in not_evaluable", bucket_of(_final),
      "not_evaluable")
check("near_misses is empty -- it is NOT a near miss",
      len(_final["near_misses"]), 0)
check("matches is empty -- it was NOT promoted", len(_final["matches"]), 0)
_real_final, _ = run_stage6(run_stage5(
    [entry("NCT00000001", "not_eligible", inclusion=[crit("not_met")])]
)[0]["evaluations"])
check("non-degeneracy: a supported rejection still lands in near_misses",
      bucket_of(_real_final), "near_misses")

_anomaly_delta = {
    k: ASSESSMENT_COMPOSITION_ANOMALIES.get(k, 0) - _ANOMALIES_BEFORE.get(k, 0)
    for k in set(ASSESSMENT_COMPOSITION_ANOMALIES) | set(_ANOMALIES_BEFORE)
}
check("no composition anomaly was recorded by anything this file ran",
      {k: v for k, v in _anomaly_delta.items() if v}, {})
check("non-degeneracy: the counter is a live Counter, not a stub that "
      "swallows increments",
      _evaluation_module.ASSESSMENT_COMPOSITION_ANOMALIES
      is ASSESSMENT_COMPOSITION_ANOMALIES, True)
check("the anomaly case is untouched by this pass",
      ASSESSMENT_KEPT_NO_DISQUALIFIER
      in _evaluation_module._ASSESSMENT_ANOMALY_CASES, True)


# ===========================================================================
# SECTION 6 -- the absent-data validator cannot fight over this trial
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 6 -- ordering against the absent-data validator")
print("=" * 75)

# A rejection whose ONLY disqualifier rests on absent data is flipped to
# ELIGIBLE by the validator, and this branch must not have claimed it first --
# taking it would record a real match as unassessable. It cannot: the
# disqualifying label there is IN vocabulary, so nothing is remapped and Step 3
# keeps the rejection for the validator to correct.
_absent, _absent_err = run_stage5(
    [entry("NCT00000001", "not_eligible",
           inclusion=[crit("not_met", patient_value=ABSENT,
                           text="Documented EGFR mutation")])])
check("a rejection resting on absent data is still flipped to ELIGIBLE",
      verdict_of(_absent), TRIAL_VERDICT_ELIGIBLE)
check("...and this branch did NOT claim it first",
      len(log_records(_absent_err, "not_evaluable")), 0)
check("...and it carries no marker", marker_of(_absent), "<no key>")

# The other direction: a trial this branch corrected is skipped by the
# validator outright, because the validator's first test is
# `eligible != "not_eligible": continue`.
check("a corrected trial is not touched by the validator afterwards",
      len(log_records(_err, "absent_data_correction")), 0)
check("...and its verdict is still not_evaluable at the end of the node",
      verdict_of(_res), TRIAL_VERDICT_NOT_EVALUABLE)


# ===========================================================================
# SECTION 7 -- what a database reader sees, run rather than described
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 7 -- the stored row for a corrected trial")
print("=" * 75)

# No schema change was made by this pass, so the whole question is what the
# EXISTING columns say -- and reading the INSERT statement is not the same as
# writing a row and reading it back. `not_evaluable_reason` is not a column of
# trial_matches, so the composed assessment is the only stored value that names
# what happened, which is why the text is a fixed constant a reader can match.
_db_dir = tempfile.mkdtemp(prefix="oncotriage_remap_no_survivor_")
_db = os.path.join(_db_dir, "inferences_test.db")
try:
    check("the scratch database is NOT the production one",
          os.path.abspath(_db)
          == os.path.abspath(_database_logger.resolve_inference_db_path(None)),
          False)

    _stage6, _ = run_stage6(run_stage5(REMAP_DISQUALIFYING)[0]["evaluations"])
    _err_db = io.StringIO()
    with contextlib.redirect_stderr(_err_db):
        _written = _database_logger.log_inference(_stage6, PATIENT, db_path=_db)
    check("the row was written", getattr(_written, "ok", False), True)
    check("...to the scratch path", str(_written), _db)

    _conn = sqlite3.connect(_db)
    _row = _conn.execute(
        "SELECT eligible, match_score, assessment, criterion_details "
        "FROM trial_matches WHERE nct_id = ?", ("NCT00000001",)).fetchone()
    _conn.close()

    check("non-degeneracy: exactly one trial_matches row came back",
          _row is not None, True)
    if _row is not None:
        _eligible, _score, _assess, _details = _row
        check("eligible reads not_evaluable", _eligible, "not_evaluable")
        check("match_score is 0.0, not NULL", _score, 0.0)
        check("assessment is the composed text, in the database",
              _assess, ASSESSMENT_REMAP_NO_SURVIVOR_TEXT)
        check("...so the stored row no longer contradicts its own verdict",
              (str(_assess).startswith(ASSESSMENT_NOT_EVALUABLE_OPENING),
               str(_assess).startswith(ASSESSMENT_NOT_ELIGIBLE_OPENING)),
              (True, False))
        _parsed = json.loads(_details)
        check("criterion_details carries the array, remapped status and all",
              [c["status"] for c in _parsed["inclusion"]], ["not_evaluable"])
finally:
    shutil.rmtree(_db_dir, ignore_errors=True)

check("the scratch database was removed", os.path.exists(_db_dir), False)


# ===========================================================================
# SECTION 8 -- NEGATIVE CONTROLS
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 8 -- each mechanism, disabled, and the defect returns")
print("=" * 75)


def _control(label, subs, probe, expected):
    """Plant, probe the planted module, record. A raise IS an outcome."""
    try:
        module = _plant(_EVAL_SRC, f"planted_{abs(hash(label)) % 10**8}", subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    try:
        actual = probe(module)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        actual = f"raised {type(exc).__name__}"
    check(label, actual, expected)


def _stored(module, payload=None):
    """(composition case, stored assessment) for the corrected trial."""
    res, _ = run_stage5(payload or REMAP_DISQUALIFYING,
                        node=module.node_llm_classifier_evaluation)
    ent = eval_of(res)
    return (module.assessment_composition_case(ent), ent.get("assessment"))


_MARKER_LINE = ('            eval_result["not_evaluable_reason"] = '
                'UNEVALUABLE_REMAP_NO_SURVIVOR\n')

# C1. THE MARKER WRITE, DELETED -- the defect exactly as it stood. The verdict
#     is still corrected, so a test that only checked the verdict would pass,
#     and the stored assessment silently reverts to the model's rejection prose
#     beside a not_evaluable verdict.
_control(
    "C1. dropping the marker write returns the contradictory draft -- CAUGHT",
    [(_MARKER_LINE, "            pass  # PLANTED: the marker write, dropped\n")],
    lambda m: _stored(m),
    (ASSESSMENT_KEPT_NOT_EVALUABLE, DRAFT),
)

# C2. THE COMPOSITION CASE, BYPASSED. The same regression reached the other
#     way, which is why both halves are controlled rather than one standing in
#     for the other.
_control(
    "C2. dropping the composition case returns the draft -- CAUGHT",
    [('        if reason == UNEVALUABLE_REMAP_NO_SURVIVOR:\n'
      "            return ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR",
      "        if False:  # PLANTED: the composed case, bypassed\n"
      "            return ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR")],
    lambda m: _stored(m),
    (ASSESSMENT_KEPT_NOT_EVALUABLE, DRAFT),
)

# C3. THE MEMBER DROPPED FROM ASSESSMENT_COMPOSED_CASES. THE TEXT IS STILL
#     COMPOSED -- only the arithmetic moves, and it still adds up, so nothing
#     but a check on both numbers can see it.
_control(
    "C3. losing the case from ASSESSMENT_COMPOSED_CASES miscounts it as "
    "kept -- CAUGHT",
    [("    ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,\n"
      "    ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR,\n)",
      "    ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,\n)")],
    lambda m: (lambda rec: (field(rec, "count"), field(rec, "kept")))(
        log_records(run_stage5(
            REMAP_DISQUALIFYING,
            node=m.node_llm_classifier_evaluation)[1],
            "assessment_composition")),
    (0, 1),
)

# C4. THE TWO BRANCHES COLLAPSED. This is the control that stops the change
#     being a rename: pointing this branch at the sibling's marker composes
#     "cited no disqualifying criterion" over a trial whose model DID write a
#     disqualifying label -- "violated", in the wrong arm. That is the
#     over-claim UNEVALUABLE_REMAP_NO_SURVIVOR's comment block forbids.
_control(
    "C4. routing this branch to the sibling marker composes a claim it cannot "
    "support -- CAUGHT",
    [(_MARKER_LINE,
      '            eval_result["not_evaluable_reason"] = '
      "UNEVALUABLE_REJECTION_UNSUPPORTED  # PLANTED\n")],
    lambda m: _stored(m, REMAP_DISQUALIFYING)[1],
    ASSESSMENT_UNSUPPORTED_REJECTION_TEXT,
)
check("C4 non-degeneracy: the SHIPPED module composes the weaker text for "
      "that shape",
      _stored(_evaluation_module, REMAP_DISQUALIFYING),
      (ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR, ASSESSMENT_REMAP_NO_SURVIVOR_TEXT))

# C5. THE MARKER GIVEN TO THE WRONG POPULATION. Writing it unconditionally at
#     the top of the loop marks entries this branch never touched -- including
#     a supported rejection -- and the composition would then have to be
#     trusted to ignore it. It is not: only the verdict keeps it out, so the
#     control shows a model-declared not_evaluable losing its own draft.
_control(
    "C5. writing the marker outside the branch reaches entries it must not -- "
    "CAUGHT",
    [("    for eval_result in evaluations:\n"
      '        nct_id = eval_result.get("nct_id", "")',
      "    for eval_result in evaluations:\n"
      '        eval_result["not_evaluable_reason"] = '
      "UNEVALUABLE_REMAP_NO_SURVIVOR  # PLANTED\n"
      '        nct_id = eval_result.get("nct_id", "")')],
    lambda m: _stored(m, [entry(
        "NCT00000001", "not_evaluable",
        assessment="Not evaluable: the criteria text was unreadable.")]),
    (ASSESSMENT_COMPOSED_REMAP_NO_SURVIVOR, ASSESSMENT_REMAP_NO_SURVIVOR_TEXT),
)
check("C5 non-degeneracy: the SHIPPED module leaves that entry's draft alone",
      _stored(_evaluation_module, [entry(
          "NCT00000001", "not_evaluable",
          assessment="Not evaluable: the criteria text was unreadable.")]),
      (ASSESSMENT_KEPT_NOT_EVALUABLE,
       "Not evaluable: the criteria text was unreadable."))

# C6. THE WHOLE BRANCH, BYPASSED -- AND THE RESULT IS NOT WHAT IT LOOKS LIKE,
#     which is why this control is worth more than the "the rejection returns"
#     one it replaces. Written first as that, it FAILED and the reason is the
#     finding: with this branch gone the entry falls through to the arm below,
#     which corrects it under UNEVALUABLE_REJECTION_UNSUPPORTED. So the verdict
#     is still not_evaluable and no anomaly fires -- and the stored assessment
#     becomes the sibling's sentence, "cited no disqualifying criterion", over
#     a trial whose model DID write a disqualifying label ("violated", in the
#     wrong arm). That is exactly the misclassification
#     UNEVALUABLE_REMAP_NO_SURVIVOR's comment block rules out, and this branch's
#     ordering above the fall-through is the only thing preventing it.
_control(
    "C6. bypassing the branch hands this population to the sibling's stronger "
    "sentence -- CAUGHT",
    [('        elif eval_result["eligible"] == TRIAL_VERDICT_NOT_ELIGIBLE '
      "and remapped_here:",
      "        elif False:  # PLANTED: the branch, bypassed")],
    lambda m: (verdict_of(run_stage5(
        REMAP_DISQUALIFYING, node=m.node_llm_classifier_evaluation)[0]),
        _stored(m, REMAP_DISQUALIFYING)),
    (TRIAL_VERDICT_NOT_EVALUABLE,
     (ASSESSMENT_COMPOSED_UNSUPPORTED_REJECTION,
      ASSESSMENT_UNSUPPORTED_REJECTION_TEXT)),
)

# CONTROL NON-DEGENERACY: a planted copy still agrees with the shipped module
# everywhere the plant does not reach, so "the control fires" is about the
# mechanism rather than about a copy that is broken generally.
try:
    _c1 = _plant(_EVAL_SRC, "planted_nondegeneracy",
                 [(_MARKER_LINE,
                   "            pass  # PLANTED: the marker write, dropped\n")])
except _PlantFailed as _exc:
    _c1 = None
    check(f"[THE NON-DEGENERACY PLANT FAILED: {_exc}]", "plant-failed",
          "planted")
if _c1 is not None:
    for _name, _payload, _want in (
        ("a supported rejection", [entry(
            "NCT00000001", "not_eligible", inclusion=[crit("not_met")])],
         TRIAL_VERDICT_NOT_ELIGIBLE),
        ("an eligible trial", [entry(
            "NCT00000001", "eligible", inclusion=[crit("met")])],
         TRIAL_VERDICT_ELIGIBLE),
        ("the sibling unsupported-rejection case", [entry(
            "NCT00000001", "not_eligible", inclusion=[crit("met")])],
         TRIAL_VERDICT_NOT_EVALUABLE),
    ):
        check(f"CONTROL non-degeneracy: {_name} is unchanged by the plant",
              verdict_of(run_stage5(
                  _payload, node=_c1.node_llm_classifier_evaluation)[0]),
              _want)
    check("CONTROL non-degeneracy: the sibling class keeps ITS marker in the "
          "planted copy, so C1 removed one marker write and not both",
          run_stage5([entry("NCT00000001", "not_eligible",
                            inclusion=[crit("met")])],
                     node=_c1.node_llm_classifier_evaluation
                     )[0]["evaluations"][0].get("not_evaluable_reason"),
          UNEVALUABLE_REJECTION_UNSUPPORTED)


# ===========================================================================
# SECTION 9 -- nothing on disk was touched
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 9 -- every plant was in memory")
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
