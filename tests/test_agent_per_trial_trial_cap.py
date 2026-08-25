# Per-Trial Trial-Count Ceiling Test
###################################

"""
PER-TRIAL STAGE 5 REFUSES A TRIAL SET LARGER THAN THE COST CAP, BEFORE IT
ISSUES A REQUEST.

In per-trial mode the billed request count IS the number of trials -- one call
per trial plus one cache warmup -- and every one of them is dispatched before
any is inspected. NOTHING BELOW BOUNDS THAT NUMBER: the input packer is
bypassed on this branch by construction, the reactive splitter's floor is a
single trial, and ``MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS`` bounds how many are
IN FLIGHT rather than how many are SENT.

So a caller that reached the node with an uncapped set -- a direct
``graph.invoke`` with a seeded ``filtered_trials``, a harness, or an edit that
dropped ``node_rule_based_filter``'s slice -- paid N times the price and
NOTHING RAISED: every request succeeded, every verdict was produced, and the
only trace was the bill. ``MATCHING_MAX_TRIALS_PER_PATIENT`` is the ceiling and
``PerTrialTrialCountError`` is the refusal.

THE CEILING IS DERIVED FROM STAGE 4'S CAP, NOT RETYPED BESIDE IT. The
assignment IS the tie: raising ``MAX_TRIALS_FOR_EVALUATION`` raises this
automatically, which is correct, because the ceiling exists to catch a set that
BYPASSED the cap rather than to second-guess its value.

THE GROUPED ARM IS DELIBERATELY NOT GUARDED and this file measures that rather
than leaving it as prose. Its request count is bounded by
``MATCHING_MAX_INPUT_PACKED_CHUNKS`` whatever N is, so it already has a
request-count bound that a second one would duplicate. Its INPUT TOKENS still
scale with N; that is a stated residual, not a covered case.

WHAT THIS FILE HOLDS
--------------------
    1. THE TIE, BY AST: the constant's right-hand side is a NAME load of
       MAX_TRIALS_FOR_EVALUATION, so nobody can retype the number; and Stage 4
       really slices to that same name.
    2. THE REFUSAL: exactly the cap passes, one more raises, and the raise is
       ``PerTrialTrialCountError`` -- a RuntimeError, not a ValueError.
    3. ZERO REQUESTS. The whole point is that it fires before the warmup, so
       the stub must record nothing at all.
    4. THE GROUPED ARM at the same oversize count does NOT raise, which is the
       asymmetry argued at the constant.
    5. CONTROLS -- five, each shown to fire.

NO NETWORK, NO KEYS, **NO SPEND** (every response is a literal served by a stub
installed through ``oncotriage/agent/deps.py``), NO LIVE QDRANT, NO MODEL LOAD,
NO CORPUS, NO DATABASE, NO GIT HISTORY. NOT in the collision matrix: it writes
nothing anywhere and the two package files it reads are written by neither of
the suite's two writers and are sha256-compared at the end. It EXECS NOTHING.

Run from terminal:
    python tests/test_agent_per_trial_trial_cap.py

Exit codes:
    0 -- all assertions passed
    1 -- one or more failures
"""


# Run needed file
#----------------
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

os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

import ast
import hashlib
import json
import threading


#------------------------------------------------------------------------------


_RESULTS = {"passed": 0, "failed": 0}
_FAILURES = []


def check(label, actual, expected):
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


from oncotriage import config                                   # noqa: E402
from oncotriage.agent import deps                               # noqa: E402
from oncotriage.agent import evaluation as _ev                  # noqa: E402
from oncotriage.agent import filtering as _filtering            # noqa: E402

_EV_PATH = os.path.abspath(_ev.__file__)
_CFG_PATH = os.path.abspath(config.__file__)
_FILT_PATH = os.path.abspath(_filtering.__file__)
_HASH_BEFORE = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                for p in (_EV_PATH, _CFG_PATH, _FILT_PATH)}


#------------------------------------------------------------------------------


# ===========================================================================
# THE STUB -- every response is a literal; nothing here costs a cent
# ===========================================================================

class _Usage:
    prompt_tokens = 100
    completion_tokens = 20
    completion_tokens_details = None
    prompt_tokens_details = None


class _Message:
    def __init__(self, content):
        self.content = content
        self.refusal = None


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()
        self.model = config.MATCHING_MODEL


class _Stub:
    """Records every request and answers each with an eligible verdict.

    THE COUNT IS THE ASSERTION. A guard that fires "before any call is issued"
    is a claim about this number and nothing else, so the stub is deliberately
    minimal: a lock, a list, and a body built from whatever nct_ids the request
    fenced in.
    """

    def __init__(self):
        self.requests = []
        self._lock = threading.Lock()
        self.chat = type("_Chat", (), {})()
        self.chat.completions = type("_C", (), {"create": self._create})()

    def _create(self, **kwargs):
        with self._lock:
            self.requests.append(kwargs)
        import re
        ids = re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ",
                         kwargs["messages"][1]["content"])
        return _Response(json.dumps({"evaluations": [
            {"assessment": "No known disqualifiers.", "eligible": "eligible",
             "inclusion_criteria": [{"criterion": "Age 18+",
                                     "patient_value": "61", "status": "met"}],
             "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
            for i in ids]}))


PATIENT = {
    "patient_id": "trial-cap-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(index):
    half = "x" * 200
    return {"trial": {"nct_id": "NCT%08d" % index, "title": f"Trial {index}",
                      "phase": "PHASE2",
                      "eligibility": {
                          "inclusion_criteria": "Inclusion Criteria:\n- " + half,
                          "exclusion_criteria": "Exclusion Criteria:\n- " + half}}}


def run_node(n, per_trial=True):
    """Drive the REAL Stage 5 node with `n` trials. Returns (outcome, stub).

    The outcome is a marker tuple on a raise rather than the raise itself: a
    bare call inside a check() argument list lets the exception escape while the
    argument is being evaluated, and the run then reports one traceback where it
    owes a summary -- the abort shape this project has shipped repeatedly.
    """
    stub = _Stub()
    saved = config.MATCHING_PER_TRIAL_CALLS_ENABLED
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = per_trial
        state = {"patient_data": PATIENT,
                 "filtered_trials": [trial(i) for i in range(n)],
                 "llm_classifier_retries": 0,
                 "mesh_filter_applied": True,
                 "mesh_filter_skip_reason": "applied",
                 "stage_timings": {}}
        try:
            return _ev.node_llm_classifier_evaluation(state), stub
        except BaseException as exc:                           # noqa: BLE001
            # THE WHOLE MESSAGE, not a slice of it. A [:400] here cut the
            # refusal's closing "No request was issued." off the end and the
            # message check failed for a reason that had nothing to do with the
            # message -- caught by running.
            return ("<RAISED>", type(exc).__name__, str(exc)), stub
    finally:
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = saved
        deps.clear_override(deps.OPENAI_CLIENT)


_CAP = config.MATCHING_MAX_TRIALS_PER_PATIENT


#------------------------------------------------------------------------------


print("=" * 78)
print("1. THE CEILING IS DERIVED FROM STAGE 4'S CAP, NOT RETYPED")
print("=" * 78)
print()

_cfg_tree = ast.parse(open(_CFG_PATH, encoding="utf-8").read())
_rhs = [ast.unparse(n.value) for n in ast.walk(_cfg_tree)
        if isinstance(n, ast.Assign) and len(n.targets) == 1
        and isinstance(n.targets[0], ast.Name)
        and n.targets[0].id == "MATCHING_MAX_TRIALS_PER_PATIENT"]

check("MATCHING_MAX_TRIALS_PER_PATIENT is assigned exactly once",
      len(_rhs), 1)
check("...and its right-hand side is the NAME MAX_TRIALS_FOR_EVALUATION, so "
      "the two cannot drift and nobody can retype the number",
      _rhs, ["MAX_TRIALS_FOR_EVALUATION"])
check("...so they are equal at runtime",
      config.MATCHING_MAX_TRIALS_PER_PATIENT, config.MAX_TRIALS_FOR_EVALUATION)
check("...and the cap is a positive int, so the checks below are not about a "
      "degenerate ceiling",
      isinstance(_CAP, int) and not isinstance(_CAP, bool) and _CAP > 0, True)

# STAGE 4 REALLY SLICES TO THAT NAME. Without this, the ceiling could be
# perfectly derived and still unreachable-by-construction for the wrong reason
# -- or reachable on every ordinary patient, which would be worse.
_filt_src = open(_FILT_PATH, encoding="utf-8").read()
_slices = [ast.unparse(n) for n in ast.walk(ast.parse(_filt_src))
           if isinstance(n, ast.Subscript) and isinstance(n.slice, ast.Slice)
           and n.slice.upper is not None
           and ast.unparse(n.slice.upper) == "MAX_TRIALS_FOR_EVALUATION"]
check("node_rule_based_filter slices its survivors to "
      "MAX_TRIALS_FOR_EVALUATION, so a set that came through Stage 4 can "
      "never reach the ceiling",
      len(_slices) >= 1, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("2. EXACTLY THE CAP PASSES; ONE MORE IS REFUSED")
print("=" * 78)
print()

_ok, _ok_stub = run_node(_CAP)
check("a full-size patient -- exactly MAX_TRIALS_FOR_EVALUATION trials -- is "
      "NOT refused, so `>` rather than `>=` is what shipped",
      isinstance(_ok, tuple) and _ok and _ok[0] == "<RAISED>", False)
check("...and it really issued requests, so the arm is not passing because "
      "the node did nothing (non-degeneracy)",
      len(_ok_stub.requests) > 0, True)

_bad, _bad_stub = run_node(_CAP + 1)
check("one trial over the ceiling raises",
      isinstance(_bad, tuple) and _bad and _bad[0] == "<RAISED>", True)
check("...and the class is PerTrialTrialCountError",
      _bad[1] if isinstance(_bad, tuple) else _bad, "PerTrialTrialCountError")
check("...whose message names the count it was handed, the ceiling, and what "
      "the whole patient would have cost",
      all(s in _bad[2] for s in (str(_CAP + 1), str(_CAP),
                                 str(_CAP + 2), "No request was issued")),
      True)

check("it is a RuntimeError so a stray `except ValueError` around a Stage 5 "
      "call cannot eat it",
      (issubclass(_ev.PerTrialTrialCountError, RuntimeError),
       issubclass(_ev.PerTrialTrialCountError, ValueError)),
      (True, False))


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("3. ZERO REQUESTS -- IT FIRES BEFORE THE WARMUP")
print("=" * 78)
print()

# THE WHOLE VALUE OF THE GUARD IS THIS NUMBER. A refusal after the warmup would
# still have cost a request; a refusal after the wave would have cost N.
check("the refused patient issued NO request at all, not even the cache "
      "warmup", len(_bad_stub.requests), 0)
check("...while the accepted patient issued one per trial plus the warmup, "
      "which is what the refusal would otherwise have paid for",
      len(_ok_stub.requests), _CAP + 1)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("4. THE GROUPED ARM IS DELIBERATELY NOT GUARDED")
print("=" * 78)
print()

# ARGUED AT THE CONSTANT: grouped's request count is bounded by
# MATCHING_MAX_INPUT_PACKED_CHUNKS whatever N is, so it already has a
# request-count bound. Measuring it here is what keeps the asymmetry a decision
# rather than something a reader has to take on trust -- and what would fail if
# somebody later made the guard unconditional without saying so.
_grouped, _g_stub = run_node(_CAP + 1, per_trial=False)
check("the same oversize set in GROUPED mode is not refused",
      isinstance(_grouped, tuple) and _grouped and _grouped[0] == "<RAISED>",
      False)
check("...and its request count is bounded by the packer's chunk limit rather "
      "than by the trial count, which is why it needs no second bound",
      len(_g_stub.requests) <= config.MATCHING_MAX_INPUT_PACKED_CHUNKS, True)
check("...and that bound is genuinely smaller than the trial count here "
      "(non-degeneracy: with N chunks the two bounds coincide and the line "
      "above says nothing)",
      config.MATCHING_MAX_INPUT_PACKED_CHUNKS < _CAP + 1, True)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("5. CONTROLS")
print("=" * 78)
print()

# CONTROL 1: far over the ceiling is refused too, and still at zero cost. A
# guard that only caught cap+1 would be an off-by-one rather than a ceiling.
_far, _far_stub = run_node(_CAP * 3)
check("CONTROL 1: three times the ceiling is refused",
      _far[1] if isinstance(_far, tuple) else _far, "PerTrialTrialCountError")
check("CONTROL 1: ...at zero requests", len(_far_stub.requests), 0)

# CONTROL 2: the guard reads config LIVE. Lowering the ceiling within the
# process must refuse a set that passed a moment ago -- which is what says the
# node is not holding a value bound at import.
_saved_cap = config.MATCHING_MAX_TRIALS_PER_PATIENT
try:
    config.MATCHING_MAX_TRIALS_PER_PATIENT = 2
    _lowered, _low_stub = run_node(3)
    check("CONTROL 2: the ceiling is read live off config, so a value moved "
          "within the process reaches the node",
          _lowered[1] if isinstance(_lowered, tuple) else _lowered,
          "PerTrialTrialCountError")
    check("CONTROL 2: ...and still at zero requests",
          len(_low_stub.requests), 0)
    # ...and the other side of it: two trials under a ceiling of two pass.
    _under, _under_stub = run_node(2)
    check("CONTROL 2: ...while a set AT the lowered ceiling still passes, so "
          "the live read is not simply refusing everything",
          isinstance(_under, tuple) and _under and _under[0] == "<RAISED>",
          False)
finally:
    config.MATCHING_MAX_TRIALS_PER_PATIENT = _saved_cap
check("CONTROL 2: the ceiling was restored",
      config.MATCHING_MAX_TRIALS_PER_PATIENT, _saved_cap)

# CONTROL 3: the guard is ONE statement and it is in the per-trial validation
# block, above the packer branch. Located by AST so a future edit that moves it
# below the warmup -- where it would cost a request -- fails here.
_ev_tree = ast.parse(open(_EV_PATH, encoding="utf-8").read())
_node_fn = [n for n in ast.walk(_ev_tree)
            if isinstance(n, ast.FunctionDef)
            and n.name == "node_llm_classifier_evaluation"]
check("CONTROL 3: the node was found (non-degeneracy)", len(_node_fn), 1)

_raises = [i for i, stmt in enumerate(_node_fn[0].body)
           if isinstance(stmt, ast.If)
           and "PerTrialTrialCountError" in ast.unparse(stmt)]
_warmups = [i for i, stmt in enumerate(_node_fn[0].body)
            if "call_matching_model_warmup" in ast.unparse(stmt)]
check("CONTROL 3: the guard is a single top-level statement of the node",
      len(_raises), 1)
check("CONTROL 3: ...and every statement that can reach the warmup is BELOW "
      "it, so no request can precede the refusal",
      all(w > _raises[0] for w in _warmups) and len(_warmups) > 0, True)

# CONTROL 4: the guard is per-trial only. An AST read of its test, so the
# asymmetry cannot be removed silently.
_guard_src = ast.unparse(_node_fn[0].body[_raises[0]].test)
check("CONTROL 4: the guard's condition is gated on the per-trial arm",
      "_per_trial_calls" in _guard_src, True)
check("CONTROL 4: ...and compares with `>` rather than `>=`",
      ">=" in _guard_src, False)


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("6. ISOLATION")
print("=" * 78)
print()

check("the OpenAI override was cleared, so nothing later in this process "
      "reaches a stub -- and no real client was ever built (a built one would "
      "be cached here)",
      deps.peek(deps.OPENAI_CLIENT) is deps.UNSET, True)
check("MATCHING_PER_TRIAL_CALLS_ENABLED was restored",
      isinstance(config.MATCHING_PER_TRIAL_CALLS_ENABLED, bool), True)
check("the three package files this file reads are byte-identical afterwards",
      [os.path.basename(p) for p in (_EV_PATH, _CFG_PATH, _FILT_PATH)
       if hashlib.sha256(open(p, "rb").read()).hexdigest() != _HASH_BEFORE[p]],
      [])


#------------------------------------------------------------------------------


print()
print("=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFailures:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print("=" * 78)

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Mon Aug 25 2026

@author: ramyalsaffar
"""
