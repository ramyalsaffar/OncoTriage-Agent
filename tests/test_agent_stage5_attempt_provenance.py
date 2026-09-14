# Agent Stage 5 Attempt Provenance Test
#######################################

"""A stored Stage 5 row describes ONE attempt, and no field of it may be left
over from an earlier one.

WHAT SHIPPED, AND WHY NOTHING FAILED
------------------------------------
LangGraph keeps a state channel's last written value for as long as no later
node writes that channel. Stage 5 re-enters itself on a parse failure, a
non-list body, an untagged API error and a floor that is not terminal, and two
of its helpers wrote ATTEMPT-SCOPED keys on some exits and left them ABSENT on
others:

  * ``_billed_so_far()`` returned ``{}`` whenever the attempt obtained no usage
    object, on the argument that ``_pipeline_provenance()``'s ``state.get(..., 0)``
    would supply a zero. That argument holds for a patient's FIRST attempt and is
    false for every later one: the channel still held the earlier attempt's
    figure, so the row published it. The smoke database shows the shape --
    row 7 stores ``llm_classifier_calls = 1`` and 12,101 input tokens (exactly
    that patient's warmup prompt) beside ``llm_classifier_call_details = '[]'``
    and an error saying the final attempt issued nothing.
  * ``_per_trial_call_census()`` was spread on the floor and the success return
    only, so a floor followed by a mid-loop failure published the floor's census
    beside the later attempt's tokens.

WHAT THIS FILE HOLDS
--------------------
    1. BOTH ORDERS THROUGH THE REAL WRITE PATH. The real Stage 5 node, the real
       ``route_after_llm_classifier``, the real terminal nodes on a StateGraph
       over the real ``TrialMatchState``, then the real ``log_inference`` into a
       scratch database, read back:
         A. a BILLED attempt, then final attempts that issue nothing (the spend
            cap declines the warmup) -- the P1 shape;
         B. a billed failed attempt, then a SUCCESS;
         C. a floor that writes the wave census, then mid-loop failures -- the
            census half of the same defect.
       Each checks the stored row against the FINAL attempt and the spend ledger
       against EVERY attempt, because resetting a final-attempt field must not
       erase billed work from the cumulative accounting the cap enforces.
    2. THE STRUCTURAL GUARD: every exit of the node that can be followed by a
       re-entry writes all six attempt-scoped keys, and both helpers write every
       key on every branch. Planted control in an AST copy.
    3. PER_TRIAL_CALL_FAILURES' DESCRIPTION, pinned in the registry and in the
       run-end report that prints it, and the corrected claim DRIVEN: a failure
       in an attempt that was later discarded is counted, and so is a patient
       whose calls all failed.
    4. FIRING CONTROLS: the leak restored in an in-memory copy of
       ``oncotriage/agent/evaluation.py``, once per helper, each caught by the
       same scenario the shipped module passes.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY. The OpenAI client is a stand-in installed through
``oncotriage/agent/deps.py`` and the patient-level backoff is replaced by a
zero-delay hook through ``oncotriage/provider_resilience.py``'s own module
attribute, restored and asserted by identity. Every database is inside a
``tempfile.mkdtemp`` asserted to differ from the production path, removed at the
end and asserted gone.

NOT IN THE COLLISION MATRIX, derived: it writes nothing in the repository, and
the five source files it reads are written by neither of the suite's two
writers and are sha256-compared at the end.

IT EXECS two in-memory copies of ``oncotriage/agent/evaluation.py``, one plant
each, argued at ``_EXEC_ALLOWLIST`` in ``tests/test_package_invariants.py``:
both helpers are NESTED inside the node, so nothing short of a patched copy can
reach them, and the fixed module is at HEAD, so a git blob would compare it with
itself.

Run from terminal:
    python tests/test_agent_stage5_attempt_provenance.py

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

# A stand-in forgotten in a future edit becomes a named RuntimeError instead of
# a model download.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
import threading
import types
from pathlib import Path

from langgraph.graph import END, StateGraph

from oncotriage import config
from oncotriage import degradation as _degradation
from oncotriage import paths as _paths
from oncotriage import provider_resilience as _pr
from oncotriage import spend as _spend
from oncotriage.agent import deps
from oncotriage.agent import evaluation as _ev
from oncotriage.agent import graph as _graph
from oncotriage.agent import terminal as _terminal
from oncotriage.agent.patient import compute_patient_hash
from oncotriage.agent.state import TrialMatchState
from oncotriage.storage import database_logger as _dl
from oncotriage.utils import get_model_cost

# ===========================================================================
# THIS FILE DRIVES THE DORMANT OpenAI STAGE 5 REQUEST -- SO IT PINS IT
# ===========================================================================
#
# The stand-in is installed at `deps.OPENAI_CLIENT`; at the shipped provider the
# dispatch would reach `converse` instead, never call it, and BUILD a real
# Converse client. The pin, its cost and why it has one owner are argued in
# tests/_provider_pin.py. The defect this file is about lives in the node's own
# returns and helpers, which every provider arm shares.
import _provider_pin                                             # noqa: E402

_PROVIDER_BEFORE_PIN = _provider_pin.pin_openai_arm(os.path.basename(__file__))


#------------------------------------------------------------------------------


# ===========================================================================
# MINIMAL ASSERTION HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
    if actual == expected:
        _RESULTS["passed"] += 1
        print(f"  PASS  {label}")
    else:
        _RESULTS["failed"] += 1
        _FAILURES.append((label, expected, actual))
        print(f"  FAIL  {label}\n          expected: {expected!r}"
              f"\n          actual:   {actual!r}")


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


class _Absent:
    """A value that equals nothing a check expects, and NEVER raises.

    Every raise-capable read below goes through ``at()`` or ``drive()``: a bare
    subscript inside a ``check(...)`` argument raises on exactly the defect the
    check exists to catch, and the run then prints one traceback where it owes a
    summary.
    """

    def __init__(self, why):
        self._why = why

    def __eq__(self, other):
        return isinstance(other, _Absent) and other._why == self._why

    def __hash__(self):
        return hash(("_Absent", self._why))

    def __len__(self):
        return 0

    def __iter__(self):
        return iter(())

    def __repr__(self):
        return f"<absent: {self._why}>"


def at(container, key, why=None):
    try:
        return container[key]
    except Exception as exc:                                  # noqa: BLE001
        return _Absent(why or f"{type(exc).__name__}: {exc}")


def drive(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:                              # noqa: BLE001
        return _Absent(f"raised {type(exc).__name__}: {exc}")


def digest(path):
    if not os.path.exists(path):
        return "absent"
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


#------------------------------------------------------------------------------


# ===========================================================================
# FIXTURES
# ===========================================================================

_EV_PY = os.path.abspath(_ev.__file__)
_WATCHED = [os.path.abspath(m.__file__)
            for m in (_ev, _degradation, _graph, _terminal, _dl)]
_WATCHED_BEFORE = {p: digest(p) for p in _WATCHED}

_TMP = tempfile.mkdtemp(prefix="oncotriage-attempt-prov-")
_PROD_DB = _paths.inferences_path
_PROD_DIGEST_BEFORE = digest(_PROD_DB)

# Captured once, before anything writes them, so the restore checks compare
# against the tree as found rather than against a literal.
_CONFIG_KEYS = ("MATCHING_PER_TRIAL_CALLS_ENABLED",
                "MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS",
                "SPEND_CAP_USD", "SPEND_CAP_ENFORCED",
                "SERVING_SPEND_CAP_USD")
_CONFIG_START = {k: getattr(config, k) for k in _CONFIG_KEYS}
_JITTER_START = _pr.full_jitter_delay
_POLICY_START = _spend.policy()

PATIENT = {
    "patient_id": "stage5-attempt-provenance-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(index):
    return {"trial": {
        "nct_id": "NCT%08d" % index, "title": f"Trial {index}",
        "phase": "PHASE2",
        "eligibility": {"inclusion_criteria": "Inclusion Criteria:\n- " + "x" * 200,
                        "exclusion_criteria": "Exclusion Criteria:\n- " + "y" * 200},
    }}


TRIALS = [trial(i) for i in range(3)]
T0, T1 = TRIALS[0]["trial"]["nct_id"], TRIALS[1]["trial"]["nct_id"]

# THE TWO ATTEMPT SHAPES CARRY DIFFERENT TOKEN COUNTS, which is what makes a
# leaked figure distinguishable from a fresh one: an attempt-1 total of 4,000
# input tokens can never be mistaken for attempt 2's 8,000.
SMALL = (1000, 100)
LARGE = (2000, 200)
_WIRE = config.matching_wire_model()
COST_SMALL = get_model_cost(_WIRE, *SMALL)
COST_LARGE = get_model_cost(_WIRE, *LARGE)

# The six keys that describe ONE attempt and are written by every exit that a
# re-entry can follow. Classified in the CLAUDE.md entry beside the fields that
# describe the whole invocation (retries, the Stage 5 time, the spend ledger).
BILLED_KEYS = ("llm_classifier_input_tokens", "llm_classifier_output_tokens",
               "llm_classifier_calls")
CENSUS_KEYS = ("llm_classifier_per_trial_calls_attempted",
               "llm_classifier_per_trial_calls_failed",
               "llm_classifier_per_trial_calls_answered")


#------------------------------------------------------------------------------


# ===========================================================================
# THE STUB
# ===========================================================================

class _Usage:
    def __init__(self, prompt, completion):
        self.prompt_tokens = prompt
        self.completion_tokens = completion
        self.completion_tokens_details = None


class _Choice:
    def __init__(self, content, finish_reason="stop"):
        self.message = types.SimpleNamespace(content=content, refusal=None)
        self.finish_reason = finish_reason


class _Response:
    def __init__(self, content, tokens, finish_reason="stop"):
        self.choices = [_Choice(content, finish_reason)]
        self.usage = _Usage(*tokens)
        self.model = _WIRE


def _eligible_body(nct_ids):
    return json.dumps({"evaluations": [
        {"assessment": "No known disqualifiers.", "eligible": "eligible",
         "inclusion_criteria": [{"criterion": "Age 18+",
                                 "patient_value": "61", "status": "met"}],
         "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
        for i in nct_ids]})


class _Stub:
    """Answers per GRAPH ATTEMPT, which the graph wrapper below publishes.

    ``plan[attempt]`` names the trials whose request raises (``fail``) or whose
    body is unparseable (``bad_json``) and the token shape every response of
    that attempt reports. The attempt number is read from the harness rather
    than inferred from the warmup count, because an attempt the spend gate
    declined issues no warmup at all.
    """

    def __init__(self, plan, attempt_ref):
        self.plan = plan
        self.attempt_ref = attempt_ref
        self.requests = []
        self._lock = threading.Lock()
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kwargs):
        attempt = self.attempt_ref[0]
        step = self.plan.get(attempt, {})
        tokens = step.get("tokens", SMALL)
        user = kwargs["messages"][1]["content"]
        ids = re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ", user)
        with self._lock:
            self.requests.append((attempt, tuple(ids)))
        if user == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE:
            return _Response("", tokens, finish_reason="length")
        if set(ids) & set(step.get("fail", ())):
            raise RuntimeError(f"stub failure for {sorted(ids)}")
        if set(ids) & set(step.get("bad_json", ())):
            return _Response("{not json at all", tokens)
        return _Response(_eligible_body(ids), tokens)


#------------------------------------------------------------------------------


# ===========================================================================
# THE REAL GRAPH, THE REAL WRITER
# ===========================================================================

def build_graph(stage5_node, attempts, attempt_ref):
    """Stage 5 -> the real router -> the real terminal nodes, over the REAL
    ``TrialMatchState``. The wrapper records what each attempt RETURNED and
    publishes the attempt number the stub answers by; it changes nothing the node
    reads or writes."""
    def _stage5(state):
        attempt_ref[0] = len(attempts) + 1
        out = stage5_node(state)
        attempts.append(dict(out))
        return out

    g = StateGraph(TrialMatchState)
    g.add_node("llm_classifier_evaluation", _stage5)
    g.add_node("finalize", _terminal.node_finalize)
    g.add_node("error_handler", _terminal.node_error_handler)
    g.set_entry_point("llm_classifier_evaluation")
    g.add_conditional_edges(
        "llm_classifier_evaluation", _graph.route_after_llm_classifier,
        {"finalize": "finalize",
         "llm_classifier_retry": "llm_classifier_evaluation",
         "error_handler": "error_handler"})
    g.add_edge("finalize", END)
    g.add_edge("error_handler", END)
    return g.compile()


def read_row(db, patient_id):
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        found = conn.execute("SELECT * FROM inferences WHERE patient_id = ?",
                             (patient_id,)).fetchall()
        return ([dict(r) for r in found] if found
                else [{"__missing__": patient_id}])
    finally:
        conn.close()


def run_scenario(name, plan, *, node=None, policy=_spend.SPEND_POLICY_CAMPAIGN,
                 cap=None, serving_cap=None, seed_window_usd=None,
                 lift_serving_cap_before=None, reservation_usd=None):
    """Drive one patient through the real graph and the real writer.

    ``lift_serving_cap_before=k`` raises ``config.SERVING_SPEND_CAP_USD`` inside
    the backoff hook ahead of attempt ``k``. It models the serving-window
    policy's own recovery -- a rolling window that ages the charge out while the
    patient waits -- deterministically, rather than by sleeping through a real
    window.
    """
    node = node or _ev.node_llm_classifier_evaluation
    attempts, attempt_ref = [], [0]
    stub = _Stub(plan, attempt_ref)
    db = os.path.join(_TMP, f"{name}.db")
    _dl._INITIALIZED_DATABASES.discard(os.path.abspath(db))
    backoffs = []

    def _no_backoff(retry_number, rng=None):
        backoffs.append(retry_number)
        if (lift_serving_cap_before is not None
                and retry_number + 1 == lift_serving_cap_before):
            config.SERVING_SPEND_CAP_USD = 1_000_000.0
        return 0.0

    out = {"attempts": attempts, "stub": stub, "backoffs": backoffs}
    _spend.set_policy(policy, source=os.path.basename(__file__))
    _spend.SPEND_LEDGER.reset()
    _spend.SPEND_STOP.reset()
    _spend.SPEND_GATE_SKIPS.clear()
    _spend.SPEND_ADMISSION_DECLINES.clear()
    deps.set_override(deps.OPENAI_CLIENT, stub)
    _pr.full_jitter_delay = _no_backoff
    _real_bound = config.stage5_attempt_bound
    try:
        if reservation_usd is not None:
            # E1: the SUPPLIED reservation. See scenario A.
            config.stage5_attempt_bound = (
                lambda *a, **k: dict(_real_bound(*a, **k),
                                     usd=reservation_usd))
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = True
        config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = 3
        config.SPEND_CAP_ENFORCED = True
        config.SPEND_CAP_USD = cap
        config.SERVING_SPEND_CAP_USD = serving_cap
        if seed_window_usd is not None:
            _spend.SPEND_LEDGER.charge_usd(seed_window_usd,
                                           _spend.SPEND_SOURCE_STAGE5)
        out["measured_before"] = _spend.SPEND_LEDGER.measured
        out["calls_before"] = _spend.SPEND_LEDGER.calls
        state = _graph.build_initial_state(PATIENT)
        state.update(filtered_trials=TRIALS, mesh_filter_applied=True,
                     mesh_filter_skip_reason="applied")
        final = drive(build_graph(node, attempts, attempt_ref).invoke, state)
        result = at(final, "result")
        out["result"] = result
        out["measured"] = _spend.SPEND_LEDGER.measured
        out["ledger_calls"] = _spend.SPEND_LEDGER.calls
        out["cap_exceeded"] = _spend.cap_exceeded(_spend.SPEND_SOURCE_STAGE5)
        out["gate_skips"] = dict(_spend.SPEND_GATE_SKIPS)
        out["admission_declines"] = dict(_spend.SPEND_ADMISSION_DECLINES)
        if isinstance(result, dict):
            result["qdrant_collection"] = "stub-collection"
            result["patient_data_hash"] = compute_patient_hash(PATIENT)
            out["write"] = drive(_dl.log_inference, result, PATIENT, db_path=db)
            out["rows"] = drive(read_row, db, PATIENT["patient_id"])
        else:
            out["write"] = result
            out["rows"] = [{"__missing__": "no result"}]
    finally:
        config.stage5_attempt_bound = _real_bound
        _pr.full_jitter_delay = _JITTER_START
        deps.clear_override(deps.OPENAI_CLIENT)
        for _k, _v in _CONFIG_START.items():
            setattr(config, _k, _v)
        _spend.set_policy(_POLICY_START, source=os.path.basename(__file__))
        _spend.SPEND_LEDGER.reset()
        _spend.SPEND_STOP.reset()
        _spend.SPEND_GATE_SKIPS.clear()
    out["row"] = at(out["rows"], 0)
    return out


def exec_copy(mutations, module_name):
    """An in-memory copy of evaluation.py with each ``(old, new, count)`` applied.

    A plant that matched a different number of times is a named failure rather
    than a control that quietly agrees with the shipped module.
    """
    text = Path(_EV_PY).read_text(encoding="utf-8")
    for old, new, expect in mutations:
        seen = text.count(old)
        if seen != expect:
            return _Absent(f"plant matched {seen} time(s), expected {expect}: "
                           f"{old[:60]!r}")
        text = text.replace(old, new)
    module = types.ModuleType(module_name)
    module.__file__ = _EV_PY
    module.__package__ = "oncotriage.agent"
    sys.modules[module_name] = module
    try:
        exec(compile(text, _EV_PY, "exec"), module.__dict__)
    except BaseException as exc:                              # noqa: BLE001
        return _Absent(f"planted copy failed to load: {type(exc).__name__}: {exc}")
    return module


if config.matching_call_mode_pin() is not None:
    raise SystemExit(
        "[CallMode] a call-mode pin is installed in this process "
        f"({config.matching_call_mode_pin()!r}); every scenario below sets the "
        "per-trial switch and would silently measure the pinned arm instead.")


#------------------------------------------------------------------------------


section("SECTION 0 -- the harness is isolated before it writes anything")

check("0a every scratch database is outside the production path",
      os.path.abspath(_TMP) != os.path.dirname(os.path.abspath(_PROD_DB)), True)
check("0b the two attempt shapes price differently (non-degenerate)",
      COST_SMALL > 0 and COST_LARGE > COST_SMALL, True)


#------------------------------------------------------------------------------


section("SECTION A -- a billed attempt, then final attempts that issue nothing")

# Attempt 1 answers the warmup and the wave and then fails to parse trial 0, so
# it is billed four responses.
#
# E1 RE-DERIVED THE CAP. It was 3.5 responses, which relied on the overshoot
# admission removed: the fourth response only went out because nothing counted
# the three in flight. Each attempt now reserves a SUPPLIED amount -- one small
# response's price here -- and is admitted only when committed + held + that
# fits. Attempt 1's peak liability is the settled warmup plus three trial holds,
# 4 x COST_SMALL, so a 4.5-response cap admits all four; after they settle,
# 4 x COST_SMALL committed plus one more reservation is 5 x COST_SMALL > 4.5, so
# every later warmup is declined -- at ADMISSION (`budget_exhausted`), because
# committed spend is still below the cap and the call-site gate passes it.
_A = run_scenario("scenario_a", {1: {"bad_json": [T0], "tokens": SMALL}},
                  cap=COST_SMALL * 4.5, reservation_usd=COST_SMALL)
_A1, _Afinal, _Arow = at(_A["attempts"], 0), at(_A["attempts"], -1), _A["row"]

check("A0 three attempts ran (non-degenerate: the leak needs a re-entry)",
      len(_A["attempts"]), 3)
check("A0 attempt 1 was BILLED: four responses, 4,000 / 400 tokens",
      tuple(at(_A1, k) for k in BILLED_KEYS), (4000, 400, 4))
check("A0 ...and every request the stub received belongs to attempt 1",
      sorted({a for a, _ in _A["stub"].requests}), [1])
check("A0 the final attempt issued nothing: the spend cap declined its warmup",
      ("spend limit" in str(at(_Afinal, "error"))), True)

check("A1 the FINAL attempt writes all three billed keys explicitly, as zeros",
      tuple(at(_Afinal, k, "absent from the final attempt's return")
            for k in BILLED_KEYS), (0, 0, 0))
check("A2 the published result carries the final attempt's figures",
      tuple(at(_A["result"], k) for k in BILLED_KEYS), (0, 0, 0))
check("A3 the STORED row: calls, input and output tokens are the final "
      "attempt's zeros, not attempt 1's 4 / 4,000 / 400",
      (at(_Arow, "llm_classifier_calls"),
       at(_Arow, "llm_classifier_input_tokens"),
       at(_Arow, "llm_classifier_output_tokens")), (0, 0, 0))
check("A4 ...beside the final attempt's empty ledger, so the two now agree",
      at(_Arow, "llm_classifier_call_details"), "[]")
check("A5 ...and the row prices at zero, which is what the row's tokens say",
      at(_Arow, "estimated_cost_usd"), 0.0)
check("A6 the retry count is CUMULATIVE: three attempts",
      at(_Arow, "llm_classifier_retries"), 3)
_A_times = [at(at(a, "stage_timings"), "llm_classifier_evaluation")
            for a in _A["attempts"]]
check("A7 the Stage 5 time is CUMULATIVE: non-decreasing across attempts and "
      "stored as the last attempt's running total",
      (all(isinstance(t, float) for t in _A_times)
       and _A_times == sorted(_A_times),
       at(_Arow, "llm_classifier_evaluation_time") == at(_A_times, -1)),
      (True, True))
check("A8 the final floor's census is its own: a per-trial attempt that "
      "attempted, lost and answered nothing",
      tuple(at(_A["result"], k) for k in CENSUS_KEYS), (0, 0, 0))

# THE CUMULATIVE HALF. The row forgot attempt 1 because it describes the final
# attempt; the ledger the cap enforces must not.
check("A9 the spend ledger still holds attempt 1's four billed responses",
      (round(_A["measured"] - _A["measured_before"], 12),
       _A["ledger_calls"] - _A["calls_before"]),
      (round(COST_SMALL * 4, 12), 4))
check("A10 ...and it is that earlier charge admission enforced (E1): committed "
      "spend is below the cap, yet another attempt's reservation does not fit, "
      "so both later warmups were declined budget_exhausted at admission and "
      "none at the call-site gate",
      (_A["cap_exceeded"],
       _A["admission_declines"].get(
           f"{_spend.SPEND_SOURCE_STAGE5}:{_spend.ADMISSION_DECLINE_EXHAUSTED}",
           0),
       _A["gate_skips"].get(
           f"{_spend.SPEND_SKIP_WARMUP_KEY_PREFIX}{_spend.SPEND_LIMIT_CAP}", 0)),
      (False, 2, 0))


#------------------------------------------------------------------------------


section("SECTION B -- a billed failed attempt, then a success")

_B = run_scenario("scenario_b", {1: {"bad_json": [T0], "tokens": SMALL},
                                 2: {"tokens": LARGE}})
_B1, _Brow = at(_B["attempts"], 0), _B["row"]
_B_ledger = at(_Brow, "llm_classifier_call_details")
_B_ledger = json.loads(_B_ledger) if isinstance(_B_ledger, str) else []

check("B0 two attempts, the first billed and failed, the second successful",
      (len(_B["attempts"]), tuple(at(_B1, k) for k in BILLED_KEYS),
       at(_Brow, "error")), (2, (4000, 400, 4), ""))
check("B1 the STORED row carries the successful attempt's tokens and calls "
      "only: 4 calls at 2,000 / 200 each",
      (at(_Brow, "llm_classifier_calls"),
       at(_Brow, "llm_classifier_input_tokens"),
       at(_Brow, "llm_classifier_output_tokens")), (4, 8000, 800))
check("B2 ...its ledger is that attempt's four calls, all at the LARGE shape",
      [c.get("prompt_tokens") for c in _B_ledger], [2000] * 4)
check("B3 ...its census is the successful wave's",
      tuple(at(_B["result"], k) for k in CENSUS_KEYS), (3, 0, 3))
check("B4 ...and the retry count records the discarded attempt",
      at(_Brow, "llm_classifier_retries"), 1)
check("B5 the row prices the successful attempt only",
      round(at(_Brow, "estimated_cost_usd") or -1, 9), round(COST_LARGE * 4, 9))
check("B6 while the spend ledger holds BOTH attempts' eight billed responses",
      (round(_B["measured"] - _B["measured_before"], 12),
       _B["ledger_calls"] - _B["calls_before"]),
      (round(COST_SMALL * 4 + COST_LARGE * 4, 12), 8))


#------------------------------------------------------------------------------


section("SECTION C -- a floor that writes the wave census, then mid-loop failures")

# Under the serving-window policy a declined attempt does not latch the run, so
# the patient re-enters. Attempt 1 is declined at the warmup (a floor, census
# 0/0/0); the window then ages the charge out, and attempts 2 and 3 are billed
# and fail to parse trial 0 -- a MID-LOOP return, whose part-read wave has no
# census to publish.
_C = run_scenario("scenario_c", {2: {"bad_json": [T0], "tokens": LARGE},
                                 3: {"bad_json": [T0], "tokens": LARGE}},
                  policy=_spend.SPEND_POLICY_WINDOW, serving_cap=0.5,
                  seed_window_usd=1.0, lift_serving_cap_before=2)
_C1, _Cfinal, _Crow = at(_C["attempts"], 0), at(_C["attempts"], -1), _C["row"]

check("C0 three attempts ran and attempt 1 was the floor: its census is 0/0/0 "
      "and it issued nothing (non-degenerate: that is the value that leaked)",
      (len(_C["attempts"]), tuple(at(_C1, k) for k in CENSUS_KEYS),
       tuple(at(_C1, k) for k in BILLED_KEYS)), (3, (0, 0, 0), (0, 0, 0)))
check("C1 the final MID-LOOP attempt writes all three census keys explicitly "
      "as None",
      tuple(at(_Cfinal, k, "absent from the final attempt's return")
            for k in CENSUS_KEYS), (None, None, None))
check("C2 the published result carries None, not the floor's 0/0/0 beside "
      "an attempt that issued four calls",
      tuple(at(_C["result"], k) for k in CENSUS_KEYS), (None, None, None))
check("C3 the STORED row carries the final attempt's own billed figures",
      (at(_Crow, "llm_classifier_calls"),
       at(_Crow, "llm_classifier_input_tokens"),
       at(_Crow, "llm_classifier_output_tokens")), (4, 8000, 800))
check("C4 the spend ledger holds both billed attempts (eight responses), not "
      "only the four the row reports",
      (round(_C["measured"] - _C["measured_before"], 12),
       _C["ledger_calls"] - _C["calls_before"]),
      (round(COST_LARGE * 8, 12), 8))


#------------------------------------------------------------------------------


section("SECTION S -- every exit a re-entry can follow writes every attempt key")


def _own_dict_returns(fn):
    found, stack = [], list(fn.body)
    while stack:
        n = stack.pop()
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        if isinstance(n, ast.Return) and isinstance(n.value, ast.Dict):
            found.append(n)
        stack.extend(ast.iter_child_nodes(n))
    return sorted(found, key=lambda r: r.lineno)


def _spreads(ret):
    """``{helper name: tuple of (keyword, constant)}`` for each ``**helper(...)``."""
    out = {}
    for k, v in zip(ret.value.keys, ret.value.values):
        if k is None and isinstance(v, ast.Call) and isinstance(v.func, ast.Name):
            out[v.func.id] = tuple(
                (kw.arg, getattr(kw.value, "value", "<non-constant>"))
                for kw in v.keywords)
    return out


def _literal_keys(ret):
    return {k.value for k in ret.value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)}


def attempt_key_violations(source):
    """Every way ``source`` lets an attempt-scoped key survive a re-entry.

    A pure function of text, so the control below can hand it a planted copy.
    """
    tree = ast.parse(source)
    node = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                 and n.name == "node_llm_classifier_evaluation"), None)
    if node is None:
        return ["node_llm_classifier_evaluation not found"]
    problems = []
    helpers = {n.name: n for n in ast.walk(node)
               if isinstance(n, ast.FunctionDef)
               and n.name in ("_billed_so_far", "_per_trial_call_census")}
    for name, keys in (("_billed_so_far", BILLED_KEYS),
                       ("_per_trial_call_census", CENSUS_KEYS)):
        fn = helpers.get(name)
        if fn is None:
            problems.append(f"{name} not found")
            continue
        rets = [r for r in ast.walk(fn) if isinstance(r, ast.Return)]
        for r in rets:
            if not (isinstance(r.value, ast.Dict)
                    and set(keys) <= _literal_keys(r)):
                problems.append(f"{name}:{r.lineno} returns without every key")
    returns = _own_dict_returns(node)
    deid = [r for r in returns if "_billed_so_far" not in _spreads(r)
            and "llm_classifier_call_details" not in _literal_keys(r)]
    for r in returns:
        if r in deid:
            continue
        keys, spreads = _literal_keys(r), _spreads(r)
        if not (set(BILLED_KEYS) <= keys or "_billed_so_far" in spreads):
            problems.append(f"return:{r.lineno} lacks the billed keys")
        if not (set(CENSUS_KEYS) <= keys or "_per_trial_call_census" in spreads):
            problems.append(f"return:{r.lineno} lacks the census keys")
        if "llm_classifier_call_details" not in keys:
            problems.append(f"return:{r.lineno} lacks the ledger")
    if len(deid) != 1:
        problems.append(f"expected exactly one pre-attempt return, found "
                        f"{[r.lineno for r in deid]}")
    return problems


_EV_SOURCE = Path(_EV_PY).read_text(encoding="utf-8")
_EV_TREE = ast.parse(_EV_SOURCE)
_NODE = next(n for n in ast.walk(_EV_TREE) if isinstance(n, ast.FunctionDef)
             and n.name == "node_llm_classifier_evaluation")
_RETURNS = _own_dict_returns(_NODE)
_WAVE_FINAL = sorted(dict(_spreads(r).get("_per_trial_call_census", ()))
                     .get("wave_final", "<none>") for r in _RETURNS
                     if "_per_trial_call_census" in _spreads(r))

check("S0 non-degenerate: the node has its seven dict returns",
      len(_RETURNS), 7)
check("S1 no exit lets an attempt-scoped key survive a re-entry",
      attempt_key_violations(_EV_SOURCE), [])
# THE COUNT IS EXACT. Two exits have read their whole wave (the floor and the
# success) and four have not (the API error, the refusal, the parse error and
# the non-list body). A new exit changes this pin, and has to decide which it is.
check("S2 the census is spread with an explicit wave_final on every exit: "
      "two whose wave was read whole, four part-read",
      _WAVE_FINAL, [False, False, False, False, True, True])
check("S3 the one exit outside the rule is the de-identification refusal, "
      "which precedes every billed call and writes its zeros literally",
      [tuple(sorted(set(BILLED_KEYS) & _literal_keys(r))) for r in _RETURNS
       if "_billed_so_far" not in _spreads(r)
       and "llm_classifier_call_details" not in _literal_keys(r)],
      [tuple(sorted(BILLED_KEYS))])

# PLANTED CONTROL: the census spread removed from ONE mid-loop exit.
_planted_src = _EV_SOURCE.replace(
    "                **_per_trial_call_census(wave_final=False),\n", "", 1)
check("S4 control: the plant took (non-degenerate)",
      _planted_src != _EV_SOURCE, True)
check("S5 control: an exit that forgets the census is REPORTED",
      any("lacks the census keys" in p
          for p in attempt_key_violations(_planted_src)), True)
_planted_helper = _EV_SOURCE.replace(
    '        return {\n'
    '            "llm_classifier_input_tokens": input_tokens,',
    '        if not calls_made:\n'
    '            return {}\n'
    '        return {\n'
    '            "llm_classifier_input_tokens": input_tokens,', 1)
check("S6 control: the absent-when-zero guard restored in _billed_so_far is "
      "REPORTED",
      (_planted_helper != _EV_SOURCE,
       any(p.startswith("_billed_so_far:")
           for p in attempt_key_violations(_planted_helper))), (True, True))


#------------------------------------------------------------------------------


section("SECTION P -- PER_TRIAL_CALL_FAILURES says what it counts")

_MEANING = at(_degradation._MEANINGS, "PER_TRIAL_CALL_FAILURES")
_MEANING_TEXT = _MEANING if isinstance(_MEANING, str) else ""
check("P0 the counter is registered with a meaning (non-degenerate)",
      len(_MEANING_TEXT) > 200, True)
# THE RETIRED SENTENCE, NOT A PHRASE FROM IT. The first version of this check
# lowercased the text and searched for "is not here", and FAILED against the
# corrected description, which says -- truly -- that a request a gate declined
# "is not here". A phrase is not the claim; the claim is about the patient
# whose calls all failed, so that is what is searched for, in both cases.
_RETIRED = "a patient whose calls all failed is not here"
check("P1 the retired claim is gone: it said a patient whose calls all failed "
      "is not counted",
      _RETIRED in " ".join(_MEANING_TEXT.lower().split()), False)
check("P1b ...and the replacement says the opposite about that patient",
      "a patient whose calls all failed is counted"
      in " ".join(_MEANING_TEXT.lower().split()), True)
check("P2 it states the scope: failed per-trial requests ACROSS ALL ATTEMPTS, "
      "INCLUDING ATTEMPTS LATER DISCARDED",
      ("across all attempts" in _MEANING_TEXT.lower()
       and "later discarded" in _MEANING_TEXT.lower()), True)
_REPORT = drive(_degradation.report_lines,
                {"PER_TRIAL_CALL_FAILURES": {"RuntimeError": 2}})
_REPORT = _REPORT if isinstance(_REPORT, list) else []
check("P3 the run-end report prints the corrected description verbatim",
      (any(line.strip() == "PER_TRIAL_CALL_FAILURES  (2)" for line in _REPORT),
       any(line.strip() == _MEANING_TEXT.strip() for line in _REPORT)),
      (True, True))


def _failure_delta(before):
    after = dict(_ev.PER_TRIAL_CALL_FAILURES)
    return {k: after.get(k, 0) - before.get(k, 0)
            for k in set(after) | set(before) if after.get(k, 0) != before.get(k, 0)}


_before = dict(_ev.PER_TRIAL_CALL_FAILURES)
_D1 = run_scenario("scenario_d1", {1: {"fail": [T0], "bad_json": [T1]},
                                   2: {}})
_D1_delta = _failure_delta(_before)
check("P4 DRIVEN: attempt 1 lost trial 0 and was then DISCARDED by a parse "
      "failure; attempt 2 succeeded",
      (len(_D1["attempts"]), at(_D1["result"], "error")), (2, ""))
check("P5 ...the counter counts the discarded attempt's failure",
      _D1_delta, {"RuntimeError": 1})
check("P6 ...while the stored result reports none, so the counter is NOT the "
      "sum of the per-patient column",
      at(_D1["result"], "llm_classifier_per_trial_calls_failed"), 0)

_before = dict(_ev.PER_TRIAL_CALL_FAILURES)
_D2 = run_scenario("scenario_d2", {a: {"fail": [t["trial"]["nct_id"]
                                               for t in TRIALS]}
                                   for a in (1, 2, 3)})
_D2_delta = _failure_delta(_before)
check("P7 DRIVEN: a patient whose trial calls ALL failed ends in the error "
      "handler (non-degenerate)",
      bool(at(_D2["result"], "error")), True)
check("P8 ...and every one of those failures is counted, on every attempt",
      _D2_delta, {"RuntimeError": 3 * len(_D2["attempts"])})


#------------------------------------------------------------------------------


section("SECTION X -- firing controls: the leak restored in a copy is caught")

# X1: `_billed_so_far` leaves its keys absent when the attempt obtained no
# usage -- the shipped defect, byte for byte.
_X1 = exec_copy([(
    '        return {\n'
    '            "llm_classifier_input_tokens": input_tokens,',
    '        if not calls_made:\n'
    '            return {}\n'
    '        return {\n'
    '            "llm_classifier_input_tokens": input_tokens,', 1)],
    "_attempt_prov_x1_evaluation")
check("X1a the plant took", isinstance(_X1, types.ModuleType), True)
if isinstance(_X1, types.ModuleType):
    _X1r = run_scenario("control_x1", {1: {"bad_json": [T0], "tokens": SMALL}},
                        node=_X1.node_llm_classifier_evaluation,
                        cap=COST_SMALL * 4.5, reservation_usd=COST_SMALL)
    check("X1b CAUGHT: scenario A's stored row carries attempt 1's 4 / 4,000 / "
          "400 again beside an empty ledger -- check A3 would fail",
          (at(_X1r["row"], "llm_classifier_calls"),
           at(_X1r["row"], "llm_classifier_input_tokens"),
           at(_X1r["row"], "llm_classifier_output_tokens"),
           at(_X1r["row"], "llm_classifier_call_details")),
          (4, 4000, 400, "[]"))

# X2: the mid-loop exits stop writing the census.
_X2 = exec_copy([("                **_per_trial_call_census(wave_final=False),\n",
                  "", 4)], "_attempt_prov_x2_evaluation")
check("X2a the plant took", isinstance(_X2, types.ModuleType), True)
if isinstance(_X2, types.ModuleType):
    _X2r = run_scenario("control_x2",
                        {2: {"bad_json": [T0], "tokens": LARGE},
                         3: {"bad_json": [T0], "tokens": LARGE}},
                        node=_X2.node_llm_classifier_evaluation,
                        policy=_spend.SPEND_POLICY_WINDOW, serving_cap=0.5,
                        seed_window_usd=1.0, lift_serving_cap_before=2)
    check("X2b CAUGHT: scenario C's result carries the floor's 0/0/0 beside an "
          "attempt that issued four calls -- check C2 would fail",
          (tuple(at(_X2r["result"], k) for k in CENSUS_KEYS),
           at(_X2r["row"], "llm_classifier_calls")),
          ((0, 0, 0), 4))


#------------------------------------------------------------------------------


section("SECTION Z -- nothing was left changed")

check("Z1 the backoff hook, the dependency seam and the spend policy are "
      "restored",
      (_pr.full_jitter_delay is _JITTER_START,
       deps.OPENAI_CLIENT in deps.active_overrides(),
       _spend.policy()), (True, False, _POLICY_START))
check("Z2 every config constant this file wrote is back to its start value",
      {k: getattr(config, k) for k in _CONFIG_KEYS}, _CONFIG_START)
check("Z3 the five source files this file reads are byte-identical",
      {p: digest(p) for p in _WATCHED}, _WATCHED_BEFORE)
check("Z4 the production inference database is unchanged",
      digest(_PROD_DB), _PROD_DIGEST_BEFORE)
for _name in ("_attempt_prov_x1_evaluation", "_attempt_prov_x2_evaluation"):
    sys.modules.pop(_name, None)
shutil.rmtree(_TMP, ignore_errors=True)
check("Z5 the scratch directory is gone", os.path.exists(_TMP), False)

# THE OUTCOME IS RECORDED BEFORE THE RESTORE, so "there was a pin to release"
# cannot be satisfied by a process that never installed one.
_PIN_WHO, _PIN_PREVIOUS, _PIN_RESTORED = _provider_pin.release_openai_arm()
check("[provider pin] the OpenAI pin this file installed was released, and "
      "config.MATCHING_PROVIDER is back to the shipped provider",
      (_PIN_WHO == os.path.basename(__file__), _PIN_PREVIOUS, _PIN_RESTORED,
       _provider_pin.pin_state()),
      (True, _PROVIDER_BEFORE_PIN, True, (None, None)))


print("\n" + "=" * 78)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed")
print("=" * 78)
for _label, _expected, _actual in _FAILURES:
    print(f"  FAILED: {_label}\n    expected: {_expected!r}\n    actual:   {_actual!r}")


if __name__ == "__main__":
    sys.exit(0 if _RESULTS["failed"] == 0 else 1)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Sep 13 2026

@author: ramyalsaffar
"""
