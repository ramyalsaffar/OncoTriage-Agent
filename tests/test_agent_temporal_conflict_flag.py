###################################################################
# Stage 5: a disqualification that quotes a resolved condition
###################################################################

"""
Temporal Conflict Flag Test

WHAT THE DETECTOR IS FOR. The Stage 5 system prompt's RULE 4 says that where a
criterion requires an ACTIVE or CURRENT condition and the record shows that
condition resolved, inactive or in remission, the status is ``not_evaluable``
on an inclusion and ``not_violated`` on an exclusion -- never ``not_met`` and
never ``violated``. The model disobeys this under some context arrangements.
Three measured shapes, each reproduced as a synthetic row in section 2: an AML
resolved in 1997 marked ``not_met`` against a newly-diagnosed-AML criterion, a
terminated pregnancy read as a current one, and a concussion resolved in 2012
quoted to disqualify on active CNS leukaemia.

WHY IT ONLY LOOKS. A simulation against an independent rater measured the
precision of REWRITING such rows at 0.57 -- two correct rejections deleted for
every three bad ones repaired. So the mechanism detects, flags and counts, and
section 6 is the proof that it does nothing else: the node is run with the
detector present and with it bypassed, and every verdict, score, status and
assessment is required to be identical, with the added key the only difference
anywhere in the two structures.

WHAT EACH SECTION COVERS
  1  the vocabularies, the pure predicate, and the two word-boundary
     properties the whole predicate rests on
  2  the three measured shapes are flagged
  3  the STATUS gate: a not_evaluable row quoting "resolved" is not flagged
  4  the CRITERION gate: a not_met row with no active-requirement language is
     not flagged
  5  ordering against the absent-data validator, from source AND behaviourally
  6  no mutation: detector present vs bypassed
  7  end-to-end: the flag reaches trial_matches.criterion_details in a scratch
     database, and what the evaluation run harness copies
  8  the model cannot emit the key -- asserted against the real strict schema
  9  the log event carries only allowlisted, non-clinical fields, with the
     formatter's drop shown firing on a control
 10  NEGATIVE CONTROLS: six plants, each disabling one thing
 11  nothing on disk was touched

NO NETWORK, NO KEYS, NO SPEND, NO GIT HISTORY, NO CORPUS. Every model response
is a literal built in this file and served by a stub installed through
``oncotriage/agent/deps.py``. NOT in tests/run_serial_tests.py's collision
matrix: every plant goes into an in-memory copy with the source hashed before
any plant and compared at the end, every database write goes to a scratch file
in a temp directory asserted to differ from the production path, and the two
files it reads are written by neither of the suite's two writers.

    python tests/test_agent_temporal_conflict_flag.py
"""

import contextlib
import copy
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

from oncotriage import observability as _observability
from oncotriage.observability import (
    FIELD_DROPS,
    LOGGABLE_FIELDS,
    RESERVED_KEYS,
    get_logger,
)
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _evaluation_module
from oncotriage.agent.evaluation import (
    TEMPORAL_CONFLICT_ACTIVE_MARKERS,
    TEMPORAL_CONFLICT_FIELD,
    TEMPORAL_CONFLICT_RESOLVED_MARKERS,
    _ACTIVE_REQUIREMENT_MARKERS,
    _DISQUALIFYING_STATUS_PHRASES,
    _RESOLVED_STATE_MARKERS,
    detect_temporal_conflicts,
    node_llm_classifier_evaluation,
    temporal_conflict_marker_counts,
    temporal_conflict_markers,
)
from oncotriage.agent.response_schema import build_response_schema
from oncotriage.agent.state import (
    TRIAL_VERDICT_ELIGIBLE,
    TRIAL_VERDICT_NOT_ELIGIBLE,
)
from oncotriage.agent.terminal import node_finalize
from oncotriage.api.server import MatchResponse, PatientSummary
from oncotriage.evaluation.run_harness import collect_verdicts
from oncotriage.storage import database_logger as _database_logger

# ===========================================================================
# THIS FILE'S SUBJECT IS THE DORMANT OpenAI STAGE 5 REQUEST -- SO IT PINS IT
# ===========================================================================
#
# `config.MATCHING_PROVIDER` ships "bedrock_anthropic". Every Stage 5 stand-in
# below is installed at `deps.OPENAI_CLIENT` and wraps `chat.completions
# .create`, so at the shipped default the dispatch would reach
# `deps.BEDROCK_ANTHROPIC_CLIENT` and `converse` instead: the stand-in would
# never be called, every assertion here would compare against an empty
# recorder, and `config.get_bedrock_anthropic_client()` would BUILD -- boto3
# probing the instance metadata service from a suite that reports it makes no
# network call, and issuing live billed Converse requests on any host whose
# credential chain finds something.
#
# The pin, its cost and why it has one owner rather than a block per file are
# argued in tests/_provider_pin.py. THE SHIPPED ARM IS NOT COVERED BY THIS
# FILE; on Converse these subjects are covered by
# tests/test_agent_bedrock_anthropic_adapter.py and
# tests/test_agent_bedrock_anthropic_per_trial.py alone.
import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))


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


# The modules under test, located from their OWN __file__ rather than from this
# test's directory, so a future move cannot silently point a plant or a source
# scan at a same-named copy.
_EVAL_SRC = os.path.abspath(_evaluation_module.__file__)
_OBS_SRC = os.path.abspath(_observability.__file__)


def _sha256_of(path):
    return hashlib.sha256(
        open(path, encoding="utf-8").read().encode()).hexdigest()


# Taken before any plant runs, so section 11 compares against a real baseline
# rather than against itself.
_SHA_BEFORE = _sha256_of(_EVAL_SRC)
_OBS_SHA_BEFORE = _sha256_of(_OBS_SRC)


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
    "patient_id": "temporal-conflict-patient",
    "demographics": {"age": 57, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "91861009",
                    "display": "Acute myeloid leukemia",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(nct_id):
    """One trial, carrying every field Stage 5 and Stage 6 read off it."""
    return {
        "nct_id": nct_id, "title": "A Study of Something", "phase": "Phase 2",
        "conditions": ["Leukemia, Myeloid, Acute"],
        "mesh_terms": ["Leukemia, Myeloid, Acute"],
        "eligibility": {"inclusion_criteria": "Newly diagnosed AML",
                        "exclusion_criteria": "Active CNS leukemia",
                        "min_age": 18, "max_age": 99, "sex": "ALL"},
    }


# A patient_value the absent-data validator will NOT match and that carries no
# resolved-state marker, so a row built with it is neutral to both mechanisms.
NEUTRAL = "ECOG 1 recorded 2026-01-04"


def crit(status, patient_value=NEUTRAL, text="an eligibility criterion"):
    """One criterion row, exactly as the model returns it."""
    return {"criterion": text, "status": status, "patient_value": patient_value}


def entry(nct_id, eligible, inclusion=(), exclusion=(),
          assessment="Known disqualifier: the model said so."):
    """One evaluation entry as the model returns it."""
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


# A RAISE IS AN OUTCOME, NOT A REASON TO ABORT. Reverting anything this file
# covers can make Stage 5 raise, and with a bare call that raise escapes
# through check()'s argument list and takes the whole run down -- one traceback
# where the file owed every result below it. The same defect has been fixed in
# tests/test_storage_query_layer.py,
# tests/test_dashboard_reproducibility_tab.py,
# tests/test_agent_age_units_and_sex_filter.py,
# tests/test_agent_trial_verdict_normalization.py and
# tests/test_agent_unsupported_rejection.py.

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


def run_stage6(evaluations, nct_ids=("NCT00000001",)):
    """Drive Stage 6 over a chosen evaluation list."""
    state = {
        "patient_data": PATIENT, "evaluations": evaluations,
        "filtered_trials": [{"trial": trial(n), "rerank_score": 5.0,
                             "rerank_score_raw": 5.0} for n in nct_ids],
        "stage_timings": {},
    }
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            out = node_finalize(state)["result"]
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
    an IndexError that aborts the file.
    """
    if not records:
        return "<no such record>"
    return records[0].get(key, "<no such field>")


def eval_of(result, nct_id="NCT00000001"):
    """The evaluation entry for one trial, or an empty dict."""
    for e in result.get("evaluations", []):
        if e.get("nct_id") == nct_id:
            return e
    return {}


def rows_of(result, nct_id="NCT00000001"):
    """Both criteria arrays of one evaluation, inclusion first."""
    entry_ = eval_of(result, nct_id)
    return (list(entry_.get("inclusion_criteria") or [])
            + list(entry_.get("exclusion_criteria") or []))


def flags_of(result, nct_id="NCT00000001"):
    """The flag value of every row, using .get -- absent reads as None."""
    return [row.get(TEMPORAL_CONFLICT_FIELD) for row in rows_of(result, nct_id)]


# ---------------------------------------------------------------------------
# THE THREE MEASURED SHAPES, as synthetic rows
# ---------------------------------------------------------------------------

# 1. AML resolved in 1997, marked not_met against a newly-diagnosed criterion.
SHAPE_AML = crit("not_met",
                 text="Newly diagnosed acute myeloid leukemia",
                 patient_value="Acute myeloid leukemia, resolved 1997-06-12")

# 2. A terminated pregnancy read as a current one.
SHAPE_PREGNANCY = crit("violated",
                       text="Patient is currently pregnant",
                       patient_value="Pregnancy, terminated 2019-02-01")

# 3. A concussion resolved in 2012 quoted to disqualify on active CNS disease.
SHAPE_CONCUSSION = crit("violated",
                        text="Active CNS leukemia or CNS involvement",
                        patient_value="Concussion, resolved 2012-08-19")


# ===========================================================================
# SECTION 1 -- the vocabularies and the pure predicate
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 1 -- the vocabularies and the pure predicate")
print("=" * 75)

check("the row key is the documented name",
      TEMPORAL_CONFLICT_FIELD, "temporal_conflict_suspect")

check("the resolved-state vocabulary is the declared list",
      list(_RESOLVED_STATE_MARKERS),
      ["resolved", "resolve", "resolves", "resolving", "resolution",
       "remission", "inactive",
       "terminated", "terminate", "terminates", "terminating", "termination"])
check("the active-requirement vocabulary is the declared list",
      list(_ACTIVE_REQUIREMENT_MARKERS),
      ["active", "actively", "current", "currently", "newly diagnosed",
       "ongoing", "undergoing"])
check("neither vocabulary has a duplicate member",
      (len(set(_RESOLVED_STATE_MARKERS)) == len(_RESOLVED_STATE_MARKERS),
       len(set(_ACTIVE_REQUIREMENT_MARKERS)) == len(_ACTIVE_REQUIREMENT_MARKERS)),
      (True, True))
check("the two vocabularies are disjoint",
      sorted(set(_RESOLVED_STATE_MARKERS) & set(_ACTIVE_REQUIREMENT_MARKERS)),
      [])

# THE STATUS GATE READS THE EXISTING MAP rather than a second spelling of the
# same two words, so the two cannot drift apart.
check("the disqualifying statuses come from the composition map",
      sorted(_DISQUALIFYING_STATUS_PHRASES), ["not_met", "violated"])

# THE TWO WORD-BOUNDARY PROPERTIES THE PREDICATE RESTS ON. Neither is a
# convenience: "unresolved" means the opposite of every member of the resolved
# family, and "inactive" is a member of it -- so if either matched by substring
# the detector would fire on rows that say the reverse of what it looks for.
check("'unresolved' does NOT satisfy the resolved family",
      temporal_conflict_markers(
          crit("not_met", text="Active infection required",
               patient_value="Unresolved abscess, ongoing")),
      None)
check("'inactive' in a CRITERION does NOT satisfy the active-requirement "
      "family",
      temporal_conflict_markers(
          crit("violated", text="Inactive hepatitis B",
               patient_value="Hepatitis B, resolved 2015")),
      None)

# The predicate is PURE: it returns a reading and touches nothing.
_probe = dict(SHAPE_AML)
_before_probe = dict(_probe)
_reading = temporal_conflict_markers(_probe)
check("the predicate reads the AML shape as suspect",
      _reading, (["resolved"], ["newly diagnosed"]))
check("...and mutated nothing while doing it", _probe, _before_probe)

# It is total over shapes the pipeline has already dropped, because it is also
# called on hand-built dicts in this file.
check("a non-dict row is not a suspect", temporal_conflict_markers("nonsense"),
      None)
check("a row whose fields are not strings does not raise",
      temporal_conflict_markers(
          {"status": "not_met", "criterion": None, "patient_value": 17}),
      None)

# Matching is case-insensitive on both sides, and whitespace-tolerant inside a
# multi-word marker.
check("matching is case-insensitive and tolerates internal whitespace",
      temporal_conflict_markers(
          crit("not_met", text="NEWLY   DIAGNOSED AML",
               patient_value="AML IN REMISSION since 2001")),
      (["remission"], ["newly diagnosed"]))

# EVERY MARKER THAT FIRED IS REPORTED, not the first, which is what makes the
# per-marker counts able to say which vocabulary members earn their place.
check("every marker of both families is reported",
      temporal_conflict_markers(
          crit("not_met", text="Currently undergoing active therapy",
               patient_value="Therapy terminated; disease in remission")),
      (["remission", "terminated"], ["active", "currently", "undergoing"]))


# ===========================================================================
# SECTION 2 -- the three measured shapes are flagged
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 2 -- the three measured shapes")
print("=" * 75)

for _name, _row, _want in (
    ("AML resolved 1997 vs newly-diagnosed AML", SHAPE_AML,
     (["resolved"], ["newly diagnosed"])),
    ("terminated pregnancy read as current", SHAPE_PREGNANCY,
     (["terminated"], ["currently"])),
    ("concussion resolved 2012 vs active CNS leukemia", SHAPE_CONCUSSION,
     (["resolved"], ["active"])),
):
    check(f"the predicate flags: {_name}",
          temporal_conflict_markers(dict(_row)), _want)

# ...and each one flags THROUGH THE REAL NODE, on the arm it belongs to. The
# first is an inclusion; the other two are exclusions.
_res_aml, _err_aml = run_stage5(
    [entry("NCT00000001", "not_eligible", inclusion=[dict(SHAPE_AML)])])
check("through the node: the AML row carries the flag",
      flags_of(_res_aml), [True])
check("...and the verdict is still the rejection the model made",
      eval_of(_res_aml).get("eligible"), TRIAL_VERDICT_NOT_ELIGIBLE)
check("...and the status was NOT rewritten",
      [r.get("status") for r in rows_of(_res_aml)], ["not_met"])

_res_preg, _err_preg = run_stage5(
    [entry("NCT00000001", "not_eligible", exclusion=[dict(SHAPE_PREGNANCY)])])
check("through the node: the pregnancy row carries the flag",
      flags_of(_res_preg), [True])

_res_cns, _err_cns = run_stage5(
    [entry("NCT00000001", "not_eligible", exclusion=[dict(SHAPE_CONCUSSION)])])
check("through the node: the CNS row carries the flag",
      flags_of(_res_cns), [True])
check("...and its status is still violated",
      [r.get("status") for r in rows_of(_res_cns)], ["violated"])

# All three in one response, so the counts and the per-marker dicts are read on
# a population rather than on a single row.
ALL_THREE = [entry("NCT00000001", "not_eligible",
                   inclusion=[dict(SHAPE_AML), crit("met", text="Age 18+")],
                   exclusion=[dict(SHAPE_PREGNANCY), dict(SHAPE_CONCUSSION)])]
_res_all, _err_all = run_stage5(ALL_THREE)
check("three suspect rows and one clean row: only the three are flagged",
      flags_of(_res_all), [True, None, True, True])

_rec_all = log_records(_err_all, "temporal_conflict_suspect")
check("one log event was emitted", len(_rec_all), 1)
check("count is the number of ROWS", field(_rec_all, "count"), 3)
check("total is the number of distinct trials", field(_rec_all, "total"), 1)
check("nct_ids names the trial", field(_rec_all, "nct_ids"), ["NCT00000001"])
check("the resolved-marker counts are per marker",
      field(_rec_all, "temporal_conflict_resolved_markers"),
      {"resolved": 2, "terminated": 1})
check("the active-marker counts are per marker",
      field(_rec_all, "temporal_conflict_active_markers"),
      {"active": 1, "currently": 1, "newly diagnosed": 1})

# The per-call aggregation is a pure function of the audit list, so it is also
# checked directly -- and its non-summing property is stated rather than left
# for a reader to infer from a table.
_multi = detect_temporal_conflicts([{
    "nct_id": "NCT9", "exclusion_criteria": [],
    "inclusion_criteria": [crit(
        "not_met", text="Currently undergoing active therapy",
        patient_value="Therapy terminated; disease in remission")]}])
check("one row can contribute several markers to each family",
      temporal_conflict_marker_counts(_multi),
      ({"remission": 1, "terminated": 1},
       {"active": 1, "currently": 1, "undergoing": 1}))
check("...so a marker dict does NOT sum to the row count",
      (len(_multi), sum(temporal_conflict_marker_counts(_multi)[0].values())),
      (1, 2))

# The module-level counters moved with it. Snapshotted around the call, because
# they are cumulative over the process and every run above has already fed them.
_res_snap = dict(TEMPORAL_CONFLICT_RESOLVED_MARKERS)
_act_snap = dict(TEMPORAL_CONFLICT_ACTIVE_MARKERS)
detect_temporal_conflicts([{
    "nct_id": "NCT9", "exclusion_criteria": [],
    "inclusion_criteria": [dict(SHAPE_AML)]}])
check("the cumulative resolved counter moved by exactly the markers seen",
      TEMPORAL_CONFLICT_RESOLVED_MARKERS["resolved"]
      - _res_snap.get("resolved", 0), 1)
check("the cumulative active counter moved by exactly the markers seen",
      TEMPORAL_CONFLICT_ACTIVE_MARKERS["newly diagnosed"]
      - _act_snap.get("newly diagnosed", 0), 1)
check("non-degeneracy: the cumulative counters are not empty",
      sum(TEMPORAL_CONFLICT_RESOLVED_MARKERS.values()) > 0, True)


# ===========================================================================
# SECTION 3 -- the STATUS gate
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 3 -- a not_evaluable row quoting 'resolved' is NOT flagged")
print("=" * 75)

# This is RULE 4 being OBEYED. The row says the criterion could not be judged
# because the condition is over, which is exactly what the prompt asks for --
# and a detector that flagged it would report the model's correct answers as
# suspect at a far higher rate than its incorrect ones.
OBEYED = crit("not_evaluable",
              text="Newly diagnosed acute myeloid leukemia",
              patient_value="Acute myeloid leukemia, resolved 1997-06-12")

check("the predicate does not flag an obedient row",
      temporal_conflict_markers(dict(OBEYED)), None)
check("...and the two content gates would BOTH have passed, so it is the "
      "status that saved it",
      temporal_conflict_markers(dict(OBEYED, status="not_met")),
      (["resolved"], ["newly diagnosed"]))

_res_obeyed, _err_obeyed = run_stage5(
    [entry("NCT00000001", "eligible", inclusion=[dict(OBEYED)],
           assessment="No known disqualifiers.")])
check("through the node: no flag on the obedient row",
      flags_of(_res_obeyed), [None])
check("...and no log event was emitted at all",
      len(log_records(_err_obeyed, "temporal_conflict_suspect")), 0)
check("...and the verdict is untouched",
      eval_of(_res_obeyed).get("eligible"), TRIAL_VERDICT_ELIGIBLE)

# The other four statuses in the two arm vocabularies are equally uninteresting.
for _status in ("met", "not_violated", "not_evaluable"):
    check(f"status {_status!r} is not a disqualification, so not a suspect",
          temporal_conflict_markers(dict(SHAPE_AML, status=_status)), None)


# ===========================================================================
# SECTION 4 -- the CRITERION gate
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 4 -- a not_met row with no active-requirement language")
print("=" * 75)

# A past-tense criterion is RULE 4's OTHER branch: "history of" is satisfied by
# any documented occurrence, so a resolved condition legitimately disqualifies
# and there is nothing suspect about the row.
HISTORY = crit("violated",
               text="History of prior malignancy",
               patient_value="Breast carcinoma, resolved 2011-04-02")

check("the predicate does not flag a past-tense criterion",
      temporal_conflict_markers(dict(HISTORY)), None)
check("...and the other two gates would BOTH have passed, so it is the "
      "criterion text that saved it",
      temporal_conflict_markers(
          dict(HISTORY, criterion="Active prior malignancy")),
      (["resolved"], ["active"]))

_res_hist, _err_hist = run_stage5(
    [entry("NCT00000001", "not_eligible", exclusion=[dict(HISTORY)])])
check("through the node: no flag on the past-tense row",
      flags_of(_res_hist), [None])
check("...and no log event", len(
    log_records(_err_hist, "temporal_conflict_suspect")), 0)

# And the converse gate, for completeness: active language in the criterion but
# nothing resolved in the patient_value.
check("active language alone is not a suspect",
      temporal_conflict_markers(
          crit("not_met", text="Currently receiving active therapy",
               patient_value="No systemic therapy on record")),
      None)


# ===========================================================================
# SECTION 5 -- ordering against the absent-data validator
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 5 -- the absent-data validator runs FIRST")
print("=" * 75)

# THE ORDER, READ OFF THE SOURCE rather than asserted from memory.
_src = open(_EVAL_SRC, encoding="utf-8").read()
_i_absent = _src.find("Absent-data validator: catch")
_i_detect = _src.find("_temporal_suspects = detect_temporal_conflicts(")
_i_reconcile = _src.find("Reconciliation: every trial that entered Stage 5")
check("non-degeneracy: all three source markers were found",
      (_i_absent > 0, _i_detect > 0, _i_reconcile > 0), (True, True, True))
check("the absent-data validator is written BEFORE the detector",
      _i_absent < _i_detect, True)
check("...and the detector before the reconciliation",
      _i_detect < _i_reconcile, True)
check("non-degeneracy: the detector is called exactly once in the module",
      _src.count("_temporal_suspects = detect_temporal_conflicts("), 1)

# BEHAVIOURALLY, ON A ROW THAT SATISFIES BOTH PREDICATES. This is the case the
# ordering exists for: "No record of ..." is one of the validator's prefixes,
# and the same string carries a resolved-state marker, and the criterion
# carries active-requirement language. All three gates of the detector would
# pass on the row AS THE MODEL WROTE IT.
BOTH = crit("violated",
            text="Active systemic infection",
            patient_value="No record of infection; prior episode resolved 2012")

check("as the model wrote it, the row IS a suspect by every gate",
      temporal_conflict_markers(dict(BOTH)),
      (["resolved"], ["active"]))

_res_both, _err_both = run_stage5(
    [entry("NCT00000001", "not_eligible", exclusion=[dict(BOTH)])])
check("the validator corrected the status first",
      [r.get("status") for r in rows_of(_res_both)], ["not_evaluable"])
check("...so the detector never flagged it",
      flags_of(_res_both), [None])
check("...and emitted no event", len(
    log_records(_err_both, "temporal_conflict_suspect")), 0)
check("...while the validator DID record its correction",
      field(log_records(_err_both, "absent_data_correction"), "count"), 1)
check("...and the trial flipped to eligible, its only disqualifier removed",
      eval_of(_res_both).get("eligible"), TRIAL_VERDICT_ELIGIBLE)


# ===========================================================================
# SECTION 6 -- NO MUTATION: present versus bypassed
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 6 -- the added key is the ONLY difference")
print("=" * 75)

# The plant disables ONLY the detector call. Everything else in the node runs
# exactly as shipped, so what the comparison isolates is this mechanism.
_BYPASS = [(
    "    _temporal_suspects = detect_temporal_conflicts(evaluations)",
    "    _temporal_suspects = []  # PLANTED: the detector, bypassed",
)]


def _strip_flag(obj):
    """A deep copy with every temporal_conflict_suspect key removed."""
    if isinstance(obj, dict):
        return {k: _strip_flag(v) for k, v in obj.items()
                if k != TEMPORAL_CONFLICT_FIELD}
    if isinstance(obj, list):
        return [_strip_flag(v) for v in obj]
    return obj


try:
    _bypassed = _plant(_EVAL_SRC, "evaluation_no_detector", _BYPASS)
except _PlantFailed as _exc:
    _bypassed = None
    check(f"[THE PLANT ITSELF FAILED: {_exc}]", "plant-failed", "planted")

if _bypassed is not None:
    # A response exercising every branch the node has: a flagged rejection, a
    # clean eligible trial, an absent-data correction and a trial the model
    # rejected with nothing to support it.
    MIXED = [
        entry("NCT00000001", "not_eligible",
              inclusion=[dict(SHAPE_AML), crit("met", text="Age 18+")],
              exclusion=[dict(SHAPE_CONCUSSION)]),
        entry("NCT00000002", "eligible",
              inclusion=[crit("met", text="Adequate organ function")],
              assessment="No known disqualifiers."),
        entry("NCT00000003", "not_eligible",
              inclusion=[crit("not_met", patient_value="Not in patient record",
                              text="Documented FLT3 mutation")]),
        entry("NCT00000004", "not_eligible",
              inclusion=[crit("met", text="Age 18+")],
              exclusion=[crit("not_violated", text="Pregnancy")]),
    ]
    _ids = ("NCT00000001", "NCT00000002", "NCT00000003", "NCT00000004")

    _with, _ = run_stage5(MIXED, nct_ids=_ids)
    _without, _ = run_stage5(MIXED, nct_ids=_ids,
                             node=_bypassed.node_llm_classifier_evaluation)

    check("non-degeneracy: both runs produced the same four evaluations",
          (len(_with.get("evaluations", [])),
           len(_without.get("evaluations", []))), (4, 4))
    check("non-degeneracy: the shipped run DID flag something",
          sum(1 for e in _with.get("evaluations", [])
              for r in (list(e.get("inclusion_criteria") or [])
                        + list(e.get("exclusion_criteria") or []))
              if r.get(TEMPORAL_CONFLICT_FIELD)), 2)
    check("non-degeneracy: the bypassed run flagged nothing",
          sum(1 for e in _without.get("evaluations", [])
              for r in (list(e.get("inclusion_criteria") or [])
                        + list(e.get("exclusion_criteria") or []))
              if r.get(TEMPORAL_CONFLICT_FIELD)), 0)

    # THE PROOF. Every key of every evaluation, at every depth -- verdicts,
    # match_score, score_confirmed, score_denominator, criteria_not_applicable,
    # every criterion status, every patient_value, every assessment, the
    # composed text and the array ordering -- compared after removing the flag.
    check("with the flag removed, the two runs are IDENTICAL structures",
          _strip_flag(_with["evaluations"]), _without["evaluations"])

    # ...and separately, that removing the flag is the only edit the comparison
    # made: the two structures differ BEFORE the strip.
    check("non-degeneracy: they differ before the strip",
          _with["evaluations"] == _without["evaluations"], False)

    # The node's own returned scalars are untouched too.
    _scalars = ("llm_classifier_retries", "llm_classifier_calls",
                "cross_vocab_remaps", "hallucinated_trials",
                "llm_classifier_input_tokens", "llm_classifier_output_tokens",
                "not_evaluable_truncated", "llm_classifier_packed_chunks",
                "error")
    check("every returned scalar is unchanged",
          {k: _with.get(k) for k in _scalars},
          {k: _without.get(k) for k in _scalars})

    # And the patient's three buckets after Stage 6.
    _final_with, _ = run_stage6(copy.deepcopy(_with["evaluations"]), _ids)
    _final_without, _ = run_stage6(copy.deepcopy(_without["evaluations"]), _ids)
    check("Stage 6 sorts the patient into identical buckets",
          {g: [e["nct_id"] for e in _final_with[g]]
           for g in ("matches", "near_misses", "not_evaluable")},
          {g: [e["nct_id"] for e in _final_without[g]]
           for g in ("matches", "near_misses", "not_evaluable")})


# ===========================================================================
# SECTION 7 -- end to end: the flag reaches SQL and the run harness
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 7 -- criterion_details, and what the run harness copies")
print("=" * 75)

_db_dir = tempfile.mkdtemp(prefix="oncotriage_temporal_conflict_")
_db = os.path.join(_db_dir, "inferences_test.db")
try:
    check("the scratch database is NOT the production one",
          os.path.abspath(_db)
          == os.path.abspath(_database_logger.resolve_inference_db_path(None)),
          False)

    _e2e_stage5, _ = run_stage5(ALL_THREE)
    _e2e_final, _ = run_stage6(copy.deepcopy(_e2e_stage5["evaluations"]))

    # WHAT THE RUN HARNESS COPIES, checked on the same result object the
    # database write is about. collect_verdicts copies each entry with dict(),
    # which is shallow -- the criteria arrays are the same lists -- so the flag
    # rides through without the harness knowing it exists.
    _verdicts = collect_verdicts(_e2e_final)
    _harness_rows = [r for v in _verdicts
                     for r in (list(v.get("inclusion_criteria") or [])
                               + list(v.get("exclusion_criteria") or []))]
    check("non-degeneracy: the harness copied the trial's rows",
          len(_harness_rows), 4)
    check("the harness's copy carries the three flags",
          [r.get(TEMPORAL_CONFLICT_FIELD) for r in _harness_rows],
          [True, None, True, True])
    check("...and it is JSON-serialisable, which is how the harness persists it",
          json.loads(json.dumps(_harness_rows))[0].get(
              TEMPORAL_CONFLICT_FIELD), True)

    # THE THIRD SURFACE, MEASURED RATHER THAN ASSUMED. `MatchResponse.result`
    # is an untyped `Dict`, so `response_model=MatchResponse` serialises it
    # whole and the flag reaches an HTTP client of POST /match and
    # POST /match/file. That is an ADDITIVE, backwards-compatible key on a
    # nested object -- no existing field moves -- and it reveals nothing the
    # same response does not already carry: the criterion text and the
    # patient_value it was derived from are in that body already, three keys
    # away. It is asserted here so the surface is a checked fact rather than a
    # reading of the model definition.
    _body = json.loads(MatchResponse(
        patient_summary=PatientSummary(
            patient_id=PATIENT["patient_id"], age=57, sex="female",
            condition_count=1, medication_count=0, allergy_count=0),
        result=_e2e_final, processing_time_seconds=0.1).model_dump_json())
    _api_rows = [r for group in ("matches", "near_misses", "not_evaluable")
                 for v in _body["result"][group]
                 for r in (list(v.get("inclusion_criteria") or [])
                           + list(v.get("exclusion_criteria") or []))]
    check("non-degeneracy: the serialised response carried the trial's rows",
          len(_api_rows), 4)
    check("the flag reaches an HTTP client, as an additive key",
          [r.get(TEMPORAL_CONFLICT_FIELD) for r in _api_rows],
          [True, None, True, True])

    _err_db = io.StringIO()
    with contextlib.redirect_stderr(_err_db):
        _written = _database_logger.log_inference(_e2e_final, PATIENT,
                                                  db_path=_db)
    check("the row was written", getattr(_written, "ok", False), True)
    check("...to the scratch path", str(_written), _db)

    _conn = sqlite3.connect(_db)
    _row = _conn.execute(
        "SELECT eligible, criterion_details FROM trial_matches "
        "WHERE nct_id = ?", ("NCT00000001",)).fetchone()
    _conn.close()

    check("non-degeneracy: exactly one trial_matches row came back",
          _row is not None, True)
    if _row is not None:
        _eligible, _details = _row
        check("the stored verdict is still the model's rejection",
              _eligible, "not_eligible")

        _parsed = json.loads(_details)
        check("criterion_details carries both arrays",
              (len(_parsed["inclusion"]), len(_parsed["exclusion"])), (2, 2))
        check("the flag survived json.dumps into the column",
              [r.get(TEMPORAL_CONFLICT_FIELD)
               for r in _parsed["inclusion"] + _parsed["exclusion"]],
              [True, None, True, True])
        check("...as a JSON boolean true, not a string",
              _parsed["inclusion"][0][TEMPORAL_CONFLICT_FIELD], True)
        check("the clean row carries NO key at all -- absent, not false",
              TEMPORAL_CONFLICT_FIELD in _parsed["inclusion"][1], False)

        # THE POINT OF THE WHOLE MECHANISM, as a query. This is what an
        # auditor runs; it is checked here so the column is known to support it.
        check("a SQL reader can select the suspect rows",
              'temporal_conflict_suspect' in _details, True)

        # And the three fields a reader needs beside the flag are all still on
        # the stored row, unedited.
        _suspect = _parsed["inclusion"][0]
        check("the stored suspect row still carries criterion/value/status "
              "verbatim",
              (_suspect["criterion"], _suspect["patient_value"],
               _suspect["status"]),
              (SHAPE_AML["criterion"], SHAPE_AML["patient_value"], "not_met"))
finally:
    shutil.rmtree(_db_dir, ignore_errors=True)

check("the scratch database was removed", os.path.exists(_db_dir), False)


# ===========================================================================
# SECTION 8 -- the model cannot emit the key
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 8 -- the strict schema forbids it")
print("=" * 75)

# THE FLAG IS TRUSTWORTHY ONLY IF THE MODEL CANNOT WRITE IT. Stage 5 sends a
# STRICT json_schema; strict mode requires `additionalProperties: false` and
# enumerates the permitted keys, so a criterion row carrying this key was
# written by the detector. Asserted against the real schema rather than
# assumed, exactly as the unsupported-rejection marker already is.
_schema = build_response_schema()
_trial_items = _schema["properties"]["evaluations"]["items"]
for _arm in ("inclusion_criteria", "exclusion_criteria"):
    _crit_schema = _trial_items["properties"][_arm]["items"]
    check(f"{_arm}: the criterion object forbids extra keys",
          _crit_schema.get("additionalProperties"), False)
    check(f"{_arm}: the flag is not one of the keys it permits",
          TEMPORAL_CONFLICT_FIELD in _crit_schema["properties"], False)
    check(f"{_arm}: non-degeneracy -- it really does enumerate properties",
          sorted(_crit_schema["properties"]),
          ["criterion", "patient_value", "status"])
    check(f"{_arm}: and `required` names every one of them, which is what "
          f"makes the enumeration closed",
          sorted(_crit_schema["required"]),
          ["criterion", "patient_value", "status"])

check("the trial object forbids extra keys too",
      _trial_items.get("additionalProperties"), False)


# ===========================================================================
# SECTION 9 -- the log event carries no clinical content
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 9 -- allowlisted fields only, and the drop control")
print("=" * 75)

_event = log_records(_err_all, "temporal_conflict_suspect")
check("non-degeneracy: there is a record to inspect", len(_event), 1)

if _event:
    _record = _event[0]
    _unknown = sorted(set(_record) - RESERVED_KEYS - LOGGABLE_FIELDS)
    check("every key of the record is an envelope key or an allowlisted field",
          _unknown, [])
    check("nothing was dropped from it -- so no field was silently withheld",
          _record.get("dropped_fields", []), [])

    # THE CONTENT TEST, and it is about strings rather than about names: the
    # quoted patient_value and the criterion text must not be reachable
    # anywhere in the serialised line, however they got there.
    _serialised = json.dumps(_record)
    for _forbidden, _what in (
        (SHAPE_AML["patient_value"], "the quoted patient_value"),
        (SHAPE_AML["criterion"], "the criterion text"),
        ("1997", "a date out of the patient record"),
        ("leukemia", "a diagnosis word"),
    ):
        check(f"the record does not contain {_what}",
              _forbidden.lower() in _serialised.lower(), False)

    # The marker dicts ARE in it, keyed by our own vocabulary -- which is the
    # thing that makes them allowlistable.
    check("the marker keys are all members of our own vocabularies",
          (set(_record["temporal_conflict_resolved_markers"])
           <= set(_RESOLVED_STATE_MARKERS),
           set(_record["temporal_conflict_active_markers"])
           <= set(_ACTIVE_REQUIREMENT_MARKERS)),
          (True, True))

check("both new field names are on the allowlist",
      ({"temporal_conflict_resolved_markers",
        "temporal_conflict_active_markers"} <= LOGGABLE_FIELDS), True)
check("non-degeneracy: a name nobody added is NOT on it",
      "temporal_conflict_patient_value" in LOGGABLE_FIELDS, False)

# THE DROP CONTROL. The formatter, not the call site, is what enforces the
# allowlist -- so the control drives the REAL logger with a field that is not on
# the list and requires the value to be gone and the NAME to be reported.
_control_log = get_logger("oncotriage.tests.temporal_conflict_control")
_drops_before = FIELD_DROPS.get("patient_value", 0)
_ctrl_err = io.StringIO()
with contextlib.redirect_stderr(_ctrl_err):
    _control_log.info("control: a clinical field offered to the formatter",
                      stage=5, event="temporal_conflict_drop_control",
                      count=1,
                      patient_value=SHAPE_AML["patient_value"])
_ctrl = log_records(_ctrl_err.getvalue(), "temporal_conflict_drop_control")
check("CONTROL non-degeneracy: the control record was emitted", len(_ctrl), 1)
check("CONTROL: the allowlisted fields survived",
      (field(_ctrl, "stage"), field(_ctrl, "count")), (5, 1))
check("CONTROL: the non-allowlisted field is GONE from the record",
      "patient_value" in (_ctrl[0] if _ctrl else {}), False)
check("CONTROL: its value is nowhere in the line",
      SHAPE_AML["patient_value"] in _ctrl_err.getvalue(), False)
check("CONTROL: and the drop is reported by NAME",
      field(_ctrl, "dropped_fields"), ["patient_value"])
check("CONTROL: FIELD_DROPS counted it",
      FIELD_DROPS.get("patient_value", 0) - _drops_before, 1)

# The reason the two new names had to be ADDED rather than assumed: the same
# formatter would have dropped them.
check("non-degeneracy: had the names not been allowlisted they would drop -- "
      "shown on a near-miss spelling",
      _observability.filter_fields(
          {"temporal_conflict_resolved_marker": {"resolved": 1}})[1],
      ["temporal_conflict_resolved_marker"])


# ===========================================================================
# SECTION 10 -- NEGATIVE CONTROLS
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 10 -- each check is shown to be able to fail")
print("=" * 75)


def _control(label, subs, probe, expected):
    """Plant, probe the planted module, record. A raise IS an outcome."""
    try:
        module = _plant(_EVAL_SRC, f"planted_{abs(hash(label)):x}", subs)
    except _PlantFailed as exc:
        check(f"{label}  [THE PLANT ITSELF FAILED: {exc}]", "plant-failed",
              expected)
        return
    try:
        actual = probe(module)
    except Exception as exc:            # noqa: BLE001 - a raise IS an outcome
        actual = f"raised {type(exc).__name__}"
    check(label, actual, expected)


def _node_flags(module, payload, nct_ids=("NCT00000001",)):
    """The flag of every row, through a planted node."""
    result, _ = run_stage5(payload, nct_ids=nct_ids,
                           node=module.node_llm_classifier_evaluation)
    return flags_of(result)


# C1. The detector call, bypassed. Section 2's flags disappear.
_control(
    "C1. bypassing the detector loses every flag -- CAUGHT",
    _BYPASS,
    lambda m: _node_flags(m, ALL_THREE),
    [None, None, None, None],
)

# C2. The detector call, bypassed -- probed at the LOG rather than at the rows,
#     because a flag and an event are two separate promises.
_control(
    "C2. bypassing the detector loses the log event -- CAUGHT",
    _BYPASS,
    lambda m: len(log_records(run_stage5(
        ALL_THREE, node=m.node_llm_classifier_evaluation)[1],
        "temporal_conflict_suspect")),
    0,
)

# C3. THE STATUS GATE, removed. Section 3's obedient row -- RULE 4 correctly
#     applied -- is reported as suspect, which is the false positive that
#     would make the flag meaningless.
_control(
    "C3. dropping the status gate flags an OBEDIENT row -- CAUGHT",
    [("    if row.get(\"status\") not in _DISQUALIFYING_STATUS_PHRASES:\n"
      "        return None",
      "    if False:  # PLANTED: the status gate, removed\n"
      "        return None")],
    lambda m: _node_flags(m, [entry("NCT00000001", "eligible",
                                    inclusion=[dict(OBEYED)],
                                    assessment="No known disqualifiers.")]),
    [True],
)

# C4. THE CRITERION GATE, removed. Section 4's past-tense row is flagged --
#     "history of prior malignancy" is RULE 4's other branch and a resolved
#     condition satisfies it legitimately.
_control(
    "C4. dropping the active-requirement gate flags a past-tense row -- CAUGHT",
    [("    active = _markers_in(_row_text(row, \"criterion\"),\n"
      "                         _ACTIVE_REQUIREMENT_PATTERNS)\n"
      "    if not active:\n"
      "        return None",
      "    active = _markers_in(_row_text(row, \"criterion\"),\n"
      "                         _ACTIVE_REQUIREMENT_PATTERNS)\n"
      "    if False:  # PLANTED: the criterion gate, removed\n"
      "        return None")],
    lambda m: _node_flags(m, [entry("NCT00000001", "not_eligible",
                                    exclusion=[dict(HISTORY)])]),
    [True],
)

# C5. THE ORDERING, reversed. The detector is called BEFORE the absent-data
#     validator, and section 5's row -- which satisfies both predicates -- is
#     flagged as a suspect disqualification while the validator then rewrites
#     its status to not_evaluable. The stored row would carry a suspect flag
#     beside a status that is not a disqualification: a contradiction, and one
#     nothing else in this file could see.
_control(
    "C5. running the detector before the validator flags a row the validator "
    "then corrects -- CAUGHT",
    [("    absent_data_corrections = []  # audit log",
      "    detect_temporal_conflicts(evaluations)  # PLANTED: too early\n"
      "    absent_data_corrections = []  # audit log")],
    lambda m: (lambda res: (flags_of(res),
                            [r.get("status") for r in rows_of(res)]))(
        run_stage5([entry("NCT00000001", "not_eligible",
                          exclusion=[dict(BOTH)])],
                   node=m.node_llm_classifier_evaluation)[0]),
    ([True], ["not_evaluable"]),
)

# C6. THE NO-REWRITE PROMISE, broken. The detector is given the correction the
#     measurement forbids -- rewriting a suspect status to not_evaluable. This
#     is the control for section 6: nothing else in the file compares the two
#     runs' statuses, and this is the edit that would silently delete correct
#     rejections at 43%.
_control(
    "C6. a detector that REWRITES the status is caught by the no-mutation "
    "comparison -- CAUGHT",
    [("            row[TEMPORAL_CONFLICT_FIELD] = True",
      "            row[TEMPORAL_CONFLICT_FIELD] = True\n"
      "            row[\"status\"] = \"not_evaluable\"  # PLANTED: a rewrite")],
    lambda m: (lambda res: ([r.get("status") for r in rows_of(res)],
                            eval_of(res).get("eligible")))(
        run_stage5([entry("NCT00000001", "not_eligible",
                          inclusion=[dict(SHAPE_AML)])],
                   node=m.node_llm_classifier_evaluation)[0]),
    (["not_evaluable"], TRIAL_VERDICT_NOT_ELIGIBLE),
)

# C7. THE MARKER LISTS, emptied. Not a gate but the vocabulary, and it is the
#     one edit that leaves every branch intact and the detector finding nothing.
_control(
    "C7. an empty resolved-state vocabulary finds nothing -- CAUGHT",
    [("_RESOLVED_STATE_MARKERS = (\n", "_RESOLVED_STATE_MARKERS = (\n)\n_UNUSED = (\n")],
    lambda m: _node_flags(m, ALL_THREE),
    [None, None, None, None],
)


# ===========================================================================
# SECTION 11 -- nothing on disk was touched
# ===========================================================================

print("\n" + "=" * 75)
print("SECTION 11 -- every plant was in memory")
print("=" * 75)

check("oncotriage/agent/evaluation.py is byte-identical to its pre-run state",
      _sha256_of(_EVAL_SRC), _SHA_BEFORE)
check("oncotriage/observability.py is byte-identical to its pre-run state",
      _sha256_of(_OBS_SRC), _OBS_SHA_BEFORE)
check("non-degeneracy: the two baselines are different real digests",
      (_SHA_BEFORE == hashlib.sha256(b"").hexdigest(),
       _SHA_BEFORE == _OBS_SHA_BEFORE), (False, False))


# ===========================================================================

print("\n" + "=" * 75)

# --- RELEASE THE PROVIDER PIN, ABOVE THE SUMMARY ---------------------------
#
# ABOVE, NOT BELOW: a release under the results line still decides the exit
# code while being absent from the number the summary printed -- a run that
# reports "0 failed" and exits non-zero. The default-flip pass shipped exactly
# that in three of seven files, which is why the release is one function with
# one caller-visible answer rather than four hand-written lines here.
#
# THE OUTCOME IS RECORDED BEFORE THE RESTORE, so "there was a pin to release"
# cannot be satisfied by a process that never installed one.
_PIN_WHO, _PIN_PREVIOUS, _PIN_RESTORED = _provider_pin.release_openai_arm()
check("[provider pin] the OpenAI pin this file installed was released, and "
      "config.MATCHING_PROVIDER is back to the shipped provider",
      (_PIN_WHO == os.path.basename(__file__), _PIN_PREVIOUS, _PIN_RESTORED,
       _provider_pin.pin_state()),
      (True, _PROVIDER_BEFORE_PIN, True, (None, None)))

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
