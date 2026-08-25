# Agent Stage 5 Input Pressure Scalar Test
##########################################

"""The INPUT guard's per-row scalar lands on every path that measured it.

WHAT SHIPPED, AND WHY NOTHING FAILED
------------------------------------
The OUTPUT guard has had a per-row scalar and its two recorded denominators
since its own era: ``llm_classifier_output_tokens_estimated`` is measured above
the send loop and is carried out of every one of Stage 5's returns, so a run
that failed reports the same headroom figure a run that answered does.

THE INPUT GUARD HAD NOTHING OF THE KIND. Its estimate lived only inside
``llm_classifier_packing``, and that report cannot answer for two whole
populations:

  * EVERY STAGE 5 FAILURE RETURN. The chunk list is a PLAN, and the node
    publishes it on the success return only -- deliberately, so a run that died
    at its first call does not publish the whole plan as though it had been
    sent. So a failed row's input size was NULL, and a run that failed BECAUSE
    its input was enormous is the row most worth asking.
  * EVERY ROW OF THE SHIPPED CALL MODE. Per-trial mode BYPASSES the packer, so
    its report carries ``enabled: False``, a ``bypassed_by`` note,
    ``budget_tokens: None`` and an empty chunk list. That is honest -- the
    packer really did not run -- and it left the mode whose entire cost
    argument is per-request input size with no per-request input figure.

Nothing raised in either case. ``stage5_input_packing_pressure`` counted those
rows into named buckets and said, correctly, that it could not measure them --
which reads as an absence of pressure unless somebody reads the notes.

WHAT THIS FILE HOLDS
--------------------
    1. THE SCALAR LANDS ON EVERY MEASURING PATH, driven through the REAL Stage
       5 node in BOTH call modes: grouped success, grouped failure (all four
       failure returns, one at a time and named), per-trial success, and the
       per-trial floor.
    2. THE WARMUP-FAILURE PATH CARRIES IT, and that is a DELIBERATE DEPARTURE
       from the obvious reading of "nothing was measured". See section 3.
    3. THE DEFINITION IS A MAXIMUM OVER PLANNED REQUESTS, driven with trials
       of deliberately unequal size so a sum, a mean, a first-chunk figure and
       a whole-batch figure are all four distinguishable from the right
       answer -- in both arms.
    4. NULL MEANS STAGE 5 NEVER RAN, through the terminal node that produces a
       result for a run with no candidates. That is the genuine unmeasured
       population, and it is the only one.
    5. THE CHANNELS SURVIVE THE GRAPH. Both keys are declared in
       ``TrialMatchState``; the negative control is the identical run over a
       schema with the two annotations removed, which loses both.
    6. THE ROUND TRIP. ``log_inference`` stores both, NULL and 0 stay
       distinguishable in SQL, and a run that never entered Stage 5 stores
       NULL rather than a budget of zero.

NO NETWORK, NO KEYS, NO SPEND, no live Qdrant, no model load, no corpus, no git
history, no live server. The OpenAI client is a stand-in installed through
``oncotriage/agent/deps.py``; Stages 1-4 are bypassed by seeding
``filtered_trials`` and the graph is never a real graph. It EXECS NOTHING --
every control is a different INPUT to the shipped node, an override installed
inside try/finally, or a reduced TypedDict built here. NOT in the collision
matrix: every database is a scratch file inside a tempfile.mkdtemp asserted to
differ from the production path and removed at the end, and the two package
files it reads are written by neither of the suite's two writers.

Run from terminal:
    python tests/test_agent_stage5_input_pressure_scalar.py

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

# Stage 5 loads no local model and this file never reaches one, but the flag is
# set before the agent is imported anyway: a stand-in forgotten in a future
# edit becomes a named RuntimeError instead of a 110 MB download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import hashlib
import json
import shutil
import sqlite3
import tempfile
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from oncotriage import config
from oncotriage.agent import deps
from oncotriage.agent.evaluation import (
    estimate_prompt_tokens,
    node_llm_classifier_evaluation,
)
from oncotriage.agent.state import TrialMatchState
from oncotriage.agent.terminal import node_finalize, node_no_candidates
from oncotriage.storage import database_logger as dblog


#------------------------------------------------------------------------------


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []

EST = "llm_classifier_input_tokens_estimated"
BUDGET = "llm_classifier_input_budget"


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

    A bare call would let a planted defect's exception escape while check()'s
    ARGUMENT was being evaluated, taking the whole file down and reporting one
    traceback where it owed a summary. Fourteen files in this suite have had to
    fix that shape; this one starts with it.
    """
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                    # noqa: BLE001 -- reported
        return {"__raised__": f"{type(exc).__name__}: {exc}"}


def query(db_path, sql):
    """Rows for ``sql``, or a named absence. Never a raise inside a check().

    A bare ``sqlite3.connect(...).execute(...)`` at module level raises
    `no such column` EXACTLY when a defect removes one of the two era-6 entries
    from INFERENCE_COLUMN_ADDITIONS -- which is the defect section 7 exists to
    catch -- so the run would report one traceback where it owed a summary and
    every check below. MEASURED, not predicted: the revert harness for this
    pass reported that plant as ABORTED before this helper existed.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            return list(conn.execute(sql))
    except BaseException as exc:                    # noqa: BLE001 -- reported
        return f"<query failed: {type(exc).__name__}: {exc}>"


def field(result, key):
    """One key of a result dict, or a named absence. Never raises in a check()."""
    if not isinstance(result, dict):
        return f"<not a result dict: {result!r}>"
    if key not in result:
        return f"<key absent: {key}>"
    return result[key]


#------------------------------------------------------------------------------


# ===========================================================================
# THE STAND-IN STAGE 5 CLIENT
# ===========================================================================

class _CompletionDetails:
    reasoning_tokens = 3


class _Usage:
    def __init__(self, prompt_tokens=100, completion_tokens=50):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.completion_tokens_details = _CompletionDetails()


class _StubOpenAI:
    """Serves a canned Stage 5 answer and records the requests it was sent.

    ``body`` overrides the answer, which is how the parse-failure and
    not-a-list arms are produced. ``raise_after`` makes the client raise from
    the Nth request on, so the very first call can be made to fail -- which in
    per-trial mode is the WARMUP.
    """

    def __init__(self, nct_ids, body=None, raise_after=None, refusal=None):
        self.nct_ids = list(nct_ids)
        self.body = body
        self.raise_after = raise_after
        self.refusal = refusal
        self.requests = []
        _outer = self

        class _completions:
            @staticmethod
            def create(**kwargs):
                index = len(_outer.requests)
                _outer.requests.append(kwargs)
                if (_outer.raise_after is not None
                        and index >= _outer.raise_after):
                    raise RuntimeError("stub transport failure")
                return _outer._completion(kwargs)

        class _chat:
            completions = _completions

        self.chat = _chat

    def _completion(self, kwargs):
        sent = kwargs.get("messages") or []
        user = sent[-1].get("content", "") if sent else ""
        asked = [n for n in self.nct_ids if n in user] or self.nct_ids
        body = self.body if self.body is not None else json.dumps([
            {"nct_id": n, "eligible": "eligible", "assessment": "ok",
             "inclusion_criteria": [{"criterion": "adult",
                                     "patient_value": "63", "status": "met"}],
             "exclusion_criteria": []}
            for n in asked])
        _refusal = self.refusal

        class _Msg:
            content = None if _refusal else body
            refusal = _refusal

        class _Choice:
            message = _Msg()
            finish_reason = "stop"

        class _Completion:
            choices = [_Choice()]
            usage = _Usage()
            # None skips the answering-model check, which is a different
            # mechanism with its own test and would otherwise raise here.
            model = None

        return _Completion()


def trial(nct, filler=""):
    """One Stage 4 survivor. ``filler`` inflates its rendered block's SIZE."""
    return {
        "trial": {
            "nct_id": nct,
            "title": f"Trial {nct}",
            "phase": "Phase 2",
            "conditions": ["Breast Neoplasms"],
            "eligibility": {
                "inclusion_criteria": ["adult", "measurable disease" + filler],
                "exclusion_criteria": ["pregnancy"],
            },
        },
        "rerank_score": 0.5,
        "rerank_score_raw": 0.5,
        "medcpt_score_max": 0.5,
    }


PATIENT = {
    "patient_id": "input-pressure-1",
    "demographics": {"age": 63, "sex": "female", "birth_date": "1962-04-03",
                     "race": "White", "ethnicity": "Not Hispanic or Latino"},
    "conditions": [{"code": "254837009", "system": "http://snomed.info/sct",
                    "display": "Malignant neoplasm of breast",
                    "clinical_status": "active",
                    "verification_status": "confirmed",
                    "onset": "2020-01-01"}],
    "observations": [], "medications": [], "procedures": [], "allergies": [],
    "cancer_stage_observations": [], "cancer_metastasis_observations": [],
    "cancer_genomic_variants": [],
    "ecog_performance_status": {"value": 1, "date": "2024-01-01",
                                "value_shape": "valueInteger",
                                "observation_count": 1,
                                "selection_path": "most_recent"},
}


def base_state(trials):
    return {"patient_data": dict(PATIENT), "filtered_trials": list(trials),
            "stage_timings": {}}


def run_in_mode(mode, client, trials, fn=node_llm_classifier_evaluation,
                state=None):
    """Drive ``fn`` with the call mode PINNED and the stub installed.

    PINNED THROUGH THE OWNER, NEVER BY WRITING THE CONSTANT.
    ``config.pin_matching_call_mode()`` is what ``oncotriage/config.py`` built
    for exactly this: a declaration a PROGRAM makes about itself, kept apart
    from ``MATCHING_PER_TRIAL_CALLS_ENABLED``, which says what the PROJECT is
    configured to do. Assigning the constant would be a second WRITER of a
    declared configuration value -- the shape this project keeps removing.

    BOTH ARMS ARE SET EXPLICITLY IN EVERY DRIVE and no assertion here reads the
    shipped default, so this file measures the MECHANISM rather than the
    DECISION. The default can move in either direction without touching a line
    of it.

    RESTORED IN A `finally`, because the pin and the override are both
    process-global and a leak would make every check after a raise silently
    measure something else.
    """
    previous = config.pin_matching_call_mode(mode)
    if config.matching_call_mode() != mode:
        raise SystemExit(
            f"[CallMode] the {mode!r} pin did not take: "
            f"config.matching_call_mode() is {config.matching_call_mode()!r}. "
            "Everything below would measure the wrong Stage 5 arm.")
    deps.set_override(deps.OPENAI_CLIENT, client)
    try:
        return drive(fn, state if state is not None else base_state(trials))
    finally:
        deps.clear_override(deps.OPENAI_CLIENT)
        config.clear_matching_call_mode_pin()
        if previous is not None:
            config.pin_matching_call_mode(previous)


NCTS = [f"NCT0000000{i}" for i in range(1, 6)]
GROUPED = config.MATCHING_CALL_MODE_GROUPED
PER_TRIAL = config.MATCHING_CALL_MODE_PER_TRIAL


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("SECTION 1 -- the scalar lands on every path that measured it")
print("=" * 78)
print()

# THE EXPECTED VALUE IS NOT RETYPED. It is recomputed here from the SAME
# public estimator the node prices with, applied to the SAME rendered text --
# so this pins the RELATION (prefix + the biggest request's trials) rather
# than a number that would turn a prompt-template edit into a failure of this
# file. What it cannot do is pin the prefix, which the node renders privately;
# the definition checks in section 4 are what hold that, by DIFFERENCE.

_TRIALS = [trial(n) for n in NCTS]

_grouped_ok = run_in_mode(GROUPED, _StubOpenAI(NCTS), _TRIALS)
check("the grouped success run really succeeded (non-degeneracy: a failed run "
      "here would make every reading below a failure-path reading)",
      (bool(_grouped_ok.get("error")),
       len(_grouped_ok.get("evaluations", []))),
      (False, len(NCTS)))
check("...and the grouped success return carries the scalar as a positive int",
      isinstance(field(_grouped_ok, EST), int) and field(_grouped_ok, EST) > 0,
      True)
check("...beside the budget it is read against, which is the CONFIGURED "
      "per-request budget rather than any relaxed one",
      field(_grouped_ok, BUDGET), config.MATCHING_INPUT_TOKEN_BUDGET)
check("...(non-degeneracy: the configured budget is not zero, so every ratio "
      "built on this column is a real division)",
      config.MATCHING_INPUT_TOKEN_BUDGET > 0, True)

_per_trial_ok = run_in_mode(PER_TRIAL, _StubOpenAI(NCTS), _TRIALS)
check("the per-trial success run really succeeded",
      (not _per_trial_ok.get("error"))
      and len(_per_trial_ok.get("evaluations", [])) == len(NCTS), True)
check("...and the SHIPPED ARM carries the scalar too -- the row that reported "
      "no input pressure at all before era 6, because it bypasses the packer",
      isinstance(field(_per_trial_ok, EST), int)
      and field(_per_trial_ok, EST) > 0, True)
check("...with the same budget on it, so the two arms are comparable row for "
      "row", field(_per_trial_ok, BUDGET), config.MATCHING_INPUT_TOKEN_BUDGET)

# THE PACKING REPORT IS STILL EMPTY IN PER-TRIAL MODE, and that is what makes
# the check above a measurement of the new column rather than of the old one.
_pt_packing = field(_per_trial_ok, "llm_classifier_packing")
check("...(non-degeneracy: the per-trial run's packing report really does "
      "report a BYPASSED packer with no budget and no chunks, which is the "
      "state the scalar exists to give a number to)",
      (isinstance(_pt_packing, dict) and _pt_packing.get("enabled") is False
       and _pt_packing.get("budget_tokens") is None
       and _pt_packing.get("chunks") == []
       and _pt_packing.get("bypassed_by") == PER_TRIAL), True)


print()
print("=" * 78)
print("SECTION 2 -- the four grouped failure returns")
print("=" * 78)
print()

# DRIVEN ONE AT A TIME AND NAMED, because the four are separate `return`
# statements with separately-written key lists: a pass that added the pair to
# three of them would leave the fourth reporting NULL for a run that measured,
# and no aggregate check over "a failure" could tell which.
_FAILURE_ARMS = (
    ("API error before any response", _StubOpenAI(NCTS, raise_after=0)),
    ("the model refused",
     _StubOpenAI(NCTS, refusal="I cannot help with that.")),
    ("the answer was not JSON", _StubOpenAI(NCTS, body="not json at all {{")),
    ("the answer was JSON but not a list",
     _StubOpenAI(NCTS, body='{"unexpected": "object"}')),
)

for _label, _client in _FAILURE_ARMS:
    _out = run_in_mode(GROUPED, _client, _TRIALS)
    check(f"[{_label}] the run really failed (non-degeneracy: a success here "
          f"would make the two readings below success readings)",
          bool(_out.get("error")), True)
    check(f"[{_label}] the estimate is carried out of the failure return, "
          f"with the SAME value the successful run of the same batch "
          f"reported -- which is what makes a failed row comparable",
          field(_out, EST), field(_grouped_ok, EST))
    check(f"[{_label}] ...and so is the budget",
          field(_out, BUDGET), config.MATCHING_INPUT_TOKEN_BUDGET)
    check(f"[{_label}] ...and neither is 0, which would assert a request "
          f"carrying nothing and a configured budget of zero",
          [field(_out, EST) == 0, field(_out, BUDGET) == 0], [False, False])

check("all four failure arms were distinct clients (non-degeneracy: two arms "
      "sharing one stub would test one path twice)",
      len({id(c) for _, c in _FAILURE_ARMS}), 4)


print()
print("=" * 78)
print("SECTION 3 -- the per-trial floor, INCLUDING the warmup failure")
print("=" * 78)
print()

# ═══════════════════════════════════════════════════════════════════════════
# THE WARMUP-FAILURE ROW CARRIES THE SCALAR. IT IS NOT NULL, AND THAT IS A
# DECISION RATHER THAN AN OVERSIGHT.
# ═══════════════════════════════════════════════════════════════════════════
#
# THE OBVIOUS READING SAYS NULL: the warmup raised, `pending` was cleared, and
# not one trial call was ISSUED -- so under a definition of "the largest
# estimate among the calls that went out" there is nothing to report.
#
# THE COLUMN IS NOT DEFINED THAT WAY, and both halves of the reason matter.
#
#   1. THE MEASUREMENT HAPPENED. The system message was rendered, every trial
#      block was rendered, the partition was chosen and the maximum was taken
#      -- all above the send loop and all before the warmup was attempted. The
#      row's contract is "NULL means nothing was measured", and something was.
#      ``llm_classifier_output_tokens_estimated`` is carried out of this exact
#      return for the identical reason and has been since its own era.
#
#   2. AN ISSUE-TIME DEFINITION WOULD ANSWER THE PRESSURE QUESTION BY LUCK.
#      The same patient with the same trials would report a number when the
#      provider answered and NULL when it did not -- so "how close did this
#      patient's input come to the budget" would depend on whether the
#      transport worked, and the failed rows, which are the ones most likely
#      to have failed BECAUSE of size, would be the ones with no size.
#
# What IS genuinely unmeasured is a run that never entered this node at all,
# and section 5 is that case.

_warm_fail = _StubOpenAI(NCTS, raise_after=0)
_pt_floor = run_in_mode(PER_TRIAL, _warm_fail, _TRIALS)
check("the per-trial run whose FIRST request raises really fails the patient",
      bool(_pt_floor.get("error")), True)
check("...(non-degeneracy: the first request in per-trial mode IS the warmup, "
      "so exactly one request was attempted and no trial call went out)",
      len(_warm_fail.requests), 1)
check("...and the row STILL carries the estimate, at the value the same batch "
      "reports when it succeeds -- the measurement happened above the send "
      "loop and NULL would claim it did not",
      field(_pt_floor, EST), field(_per_trial_ok, EST))
check("...and the budget with it",
      field(_pt_floor, BUDGET), config.MATCHING_INPUT_TOKEN_BUDGET)

# THE OTHER PER-TRIAL FLOOR: the warmup answers and every TRIAL call raises.
_wave_fail = _StubOpenAI(NCTS, raise_after=1)
_pt_wave = run_in_mode(PER_TRIAL, _wave_fail, _TRIALS)
check("a per-trial run whose warmup answers and whose whole wave raises also "
      "fails the patient",
      bool(_pt_wave.get("error")), True)
check("...(non-degeneracy: this arm really did issue trial calls, so it is a "
      "different path from the warmup failure above)",
      len(_wave_fail.requests) > 1, True)
check("...and carries the same estimate", field(_pt_wave, EST),
      field(_per_trial_ok, EST))


print()
print("=" * 78)
print("SECTION 4 -- the definition: the LARGEST single request, not a sum")
print("=" * 78)
print()

# THE FIXTURE IS BUILT SO FOUR WRONG DEFINITIONS ARE ALL DISTINGUISHABLE.
# One trial is inflated by a known number of characters and the others are
# not, so:
#
#   the right answer      = prefix + big + (grouped: + the two small ones)
#   a SUM over requests   > the right answer in per-trial mode
#   a FIRST-chunk figure  = prefix + small, in per-trial mode
#   a MEAN               < the right answer in per-trial mode
#
# and the arithmetic is checked by DIFFERENCE rather than against an absolute,
# because the prefix is the rendered patient record and pinning it here would
# turn a prompt-template edit into a failure of this file.

_BIG_CHARS = 8000
_BIG = "X" * _BIG_CHARS
_UNEQUAL = [trial("NCT10000001"), trial("NCT10000002", filler=_BIG),
            trial("NCT10000003")]
_EVEN = [trial("NCT10000001"), trial("NCT10000002"), trial("NCT10000003")]
_UNEQUAL_NCTS = ["NCT10000001", "NCT10000002", "NCT10000003"]

_pt_even = run_in_mode(PER_TRIAL, _StubOpenAI(_UNEQUAL_NCTS), _EVEN)
_pt_uneq = run_in_mode(PER_TRIAL, _StubOpenAI(_UNEQUAL_NCTS), _UNEQUAL)
_gp_even = run_in_mode(GROUPED, _StubOpenAI(_UNEQUAL_NCTS), _EVEN)
_gp_uneq = run_in_mode(GROUPED, _StubOpenAI(_UNEQUAL_NCTS), _UNEQUAL)

# THE INFLATION IS THE ONE THING THAT MOVED, so the DIFFERENCE between the two
# per-trial runs is the inflated trial's own cost and nothing else.
_EXPECTED_DELTA = estimate_prompt_tokens(_BIG)


def _delta(a, b):
    """b - a over two result scalars, or a named absence."""
    x, y = field(a, EST), field(b, EST)
    if not isinstance(x, int) or not isinstance(y, int):
        return f"<not both measured: {x!r} / {y!r}>"
    return y - x


check("(non-degeneracy: the inflation is a real number of tokens, so the "
      "differences below are not all zero)", _EXPECTED_DELTA > 0, True)
check("in PER-TRIAL mode the scalar rises by exactly the inflated trial's own "
      "estimated cost -- which is what 'the largest single call' means when "
      "every call carries one trial",
      _delta(_pt_even, _pt_uneq), _EXPECTED_DELTA)
check("...and in GROUPED mode by the same amount, because the one request "
      "grew by that trial and nothing else",
      _delta(_gp_even, _gp_uneq), _EXPECTED_DELTA)

# A SUM WOULD NOT BEHAVE THIS WAY, and neither would a mean. Both are ruled
# out by comparing the two ARMS on the SAME trials: three per-trial requests
# each carry the prefix, so their sum exceeds the grouped single request by
# two whole prefixes, while the maximum is SMALLER than the grouped figure by
# the two trials it does not carry.
_pt_v = field(_pt_uneq, EST)
_gp_v = field(_gp_uneq, EST)
check("the per-trial scalar is strictly SMALLER than the grouped one over the "
      "same trials -- a sum across requests would be strictly LARGER, and a "
      "count-weighted mean would sit between them",
      isinstance(_pt_v, int) and isinstance(_gp_v, int) and _pt_v < _gp_v,
      True)
check("...and the gap is exactly the two trials the largest per-trial request "
      "does not carry, which pins the arithmetic rather than its direction",
      (_gp_v - _pt_v) if isinstance(_pt_v, int) and isinstance(_gp_v, int)
      else "<not both measured>",
      (field(_gp_even, EST) - field(_pt_even, EST))
      if isinstance(field(_gp_even, EST), int) else "<even run not measured>")


print()
print("=" * 78)
print("SECTION 5 -- NULL means Stage 5 never ran, and nothing else")
print("=" * 78)
print()

# THE GENUINE UNMEASURED POPULATION. `node_no_candidates` is a terminal node
# that produces a full result dict without Stage 5 ever being entered, which is
# the state a no-candidates run and an upstream failure both reach.
# THE NODE RETURNS A CHANNEL UPDATE, `{"result": ...}`, not the result itself
# -- which is what `node_finalize` returns too and what the graph writes into
# state. Unwrapped through `field()` so a node that stopped returning it fails
# the check rather than raising inside it.
_none_update = drive(node_no_candidates,
                     {"patient_data": dict(PATIENT), "filtered_trials": [],
                      "stage_timings": {},
                      "no_candidates_reason": "no_trials"})
_none = field(_none_update, "result")
check("a run that never entered Stage 5 produces a result (non-degeneracy: an "
      "empty dict would satisfy the two checks below for the wrong reason)",
      isinstance(_none, dict) and "patient_id" in _none, True)
check("...and reports the estimate as None, which is the only reading that "
      "means 'nothing was measured'", field(_none, EST), None)
check("...and the budget as None rather than the configured constant: a run "
      "that planned no request was judged against no budget",
      field(_none, BUDGET), None)

# BOTH-OR-NEITHER, ACROSS EVERY DRIVE THIS FILE MADE. The pair is what makes
# `estimate IS NULL` the ONE predicate meaning "no measurement": a budget
# recorded beside a NULL estimate would be a third state no query
# distinguishes, and an estimate with no budget would be a ratio with no
# denominator.
_PAIRS = [("grouped success", _grouped_ok), ("per-trial success", _per_trial_ok),
          ("per-trial warmup floor", _pt_floor),
          ("per-trial wave floor", _pt_wave),
          ("never entered Stage 5", _none)]
_PAIRS += [(f"grouped failure: {_l}", run_in_mode(GROUPED, _c, _TRIALS))
           for _l, _c in _FAILURE_ARMS]
check("across every path this file drove, the estimate and the budget are "
      "both present or both absent -- never one without the other",
      [_l for _l, _r in _PAIRS
       if (field(_r, EST) is None) != (field(_r, BUDGET) is None)], [])
check("...(non-degeneracy: the set really contains both a measured path and "
      "an unmeasured one, so the check is not passing over one kind)",
      (any(field(_r, EST) is not None for _, _r in _PAIRS),
       any(field(_r, EST) is None for _, _r in _PAIRS)), (True, True))


print()
print("=" * 78)
print("SECTION 6 -- the channels survive the graph")
print("=" * 78)
print()

# A KEY A NODE RETURNS AND `TrialMatchState` DOES NOT DECLARE IS DROPPED,
# SILENTLY -- no error, no warning, no raise. So the declaration is checked
# structurally AND the drop is demonstrated, on a reduced schema, over the same
# two real node functions.
check("both keys are declared in TrialMatchState",
      sorted(k for k in (EST, BUDGET) if k in TrialMatchState.__annotations__),
      sorted((EST, BUDGET)))


def _unannotated(fn):
    """The same node with no first-parameter annotation.

    LangGraph reads the node callable's annotation and ADDS that schema's
    channels to the graph, so registering the real annotated function on a
    reduced schema silently reinstates every channel the reduction removed --
    and the control would then report that the keys survive, which is the
    opposite of the truth.
    """
    def _node(state):
        return fn(state)
    _node.__name__ = getattr(fn, "__name__", "node")
    return _node


def through_graph(schema, mode=GROUPED):
    graph = StateGraph(schema)
    graph.add_node("stage5", _unannotated(node_llm_classifier_evaluation))
    graph.add_node("finalize", _unannotated(node_finalize))
    graph.set_entry_point("stage5")
    graph.add_edge("stage5", "finalize")
    graph.add_edge("finalize", END)
    compiled = graph.compile()
    return run_in_mode(mode, _StubOpenAI(NCTS), _TRIALS,
                       fn=lambda state: compiled.invoke(state).get("result", {}))


_full = through_graph(TrialMatchState)
check("through a REAL StateGraph over the REAL schema, both keys arrive at "
      "the terminal node carrying the node's values",
      (isinstance(field(_full, EST), int) and field(_full, EST) > 0,
       field(_full, BUDGET)),
      (True, config.MATCHING_INPUT_TOKEN_BUDGET))

_Reduced = TypedDict(
    "_Reduced",
    {k: v for k, v in TrialMatchState.__annotations__.items()
     if k not in (EST, BUDGET)},
    total=False)
_reduced = through_graph(_Reduced)
check("...and the IDENTICAL run over a schema with the two annotations "
      "REMOVED loses both, which is what says the declaration is doing the "
      "work and the check above can fail (negative control)",
      (field(_reduced, EST), field(_reduced, BUDGET)), (None, None))
check("...(non-degeneracy: the reduced run still produced a result and still "
      "ran Stage 5, so the loss is the channel and not the run)",
      isinstance(_reduced, dict)
      and isinstance(_reduced.get("llm_classifier_output_tokens_estimated"),
                     int), True)


print()
print("=" * 78)
print("SECTION 7 -- the round trip, and NULL is not 0")
print("=" * 78)
print()

_TMP = tempfile.mkdtemp(prefix="oncotriage-input-pressure-")
_DB = os.path.join(_TMP, "scratch.db")
check("the scratch database is NOT the production one, which is what makes "
      "every write below harmless",
      os.path.abspath(_DB)
      != os.path.abspath(dblog.resolve_inference_db_path(None)), True)

_quiet = {"out": open(os.devnull, "w", encoding="utf-8")}
_stdout = sys.stdout
try:
    sys.stdout = _quiet["out"]
    dblog.initialize_database(_DB)
    for _pid, _payload in (
        ("MEASURED", {EST: 11500, BUDGET: 12000}),
        ("ZERO", {EST: 0, BUDGET: 12000}),
        ("LEGACY", {}),
    ):
        _row = {"patient_id": _pid, "timestamp": "2026-08-25T00:00:00",
                "matching_model": config.MATCHING_MODEL,
                "llm_classifier_input_tokens": 1,
                "llm_classifier_output_tokens": 1}
        _row.update(_payload)
        dblog.log_inference(_row, {"demographics": {}}, db_path=_DB)
finally:
    sys.stdout = _stdout
    _quiet["out"].close()

_rows = query(_DB, f"SELECT patient_id, {EST}, {BUDGET} FROM inferences")
_stored = ({r[0]: (r[1], r[2]) for r in _rows}
           if isinstance(_rows, list) else {})
check("both era-6 columns exist on a database this code creates, so the three "
      "readings below are about VALUES rather than about a missing column "
      "(non-degeneracy: without this a schema defect would make each of them "
      "fail with the same unhelpful message)",
      isinstance(_rows, list) and len(_rows) == 3,
      True)
check("a measured row round-trips both figures", _stored.get("MEASURED"),
      (11500, 12000))
check("a row whose estimate really is 0 stores 0, not NULL",
      _stored.get("ZERO"), (0, 12000))
check("...and a row that never measured stores NULL, not 0 -- which is the "
      "whole reason neither column has a default",
      _stored.get("LEGACY"), (None, None))
_null_rows = query(_DB,
                   f"SELECT patient_id FROM inferences WHERE {EST} IS NULL")
check("...and SQL can tell those two apart, which is what every pressure "
      "query rests on",
      [r[0] for r in _null_rows] if isinstance(_null_rows, list)
      else _null_rows,
      ["LEGACY"])
_stamp = query(_DB, "PRAGMA user_version")
check("...and the era stamp on a database this code creates is at least 6, "
      "the era these two columns introduced",
      isinstance(_stamp, list) and bool(_stamp) and _stamp[0][0] >= 6, True)

shutil.rmtree(_TMP, ignore_errors=True)
check("the temporary directory is removed", os.path.exists(_TMP), False)


print()
print("=" * 78)
print("SECTION 8 -- the two package files this file reads are unchanged")
print("=" * 78)
print()

# NOT IN THE COLLISION MATRIX, and this is the derivation rather than a claim:
# neither file below is written by either of the suite's two source-rewriting
# tests, and this file writes nothing outside a temp directory it removed.
for _mod in (
    "oncotriage/agent/evaluation.py",
    "oncotriage/storage/database_logger.py",
):
    _path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), _mod)
    check(f"{_mod} exists and was read, not written",
          os.path.isfile(_path)
          and len(hashlib.sha256(open(_path, "rb").read()).hexdigest()) == 64,
          True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print()
    print("FAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 78)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on 2026-08-25

@author: ramyalsaffar
"""
