##############################################
# The run spend gate and its call-count breaker
##############################################

"""No run of this pipeline can outspend a configured budget.

WHY THIS FILE EXISTS
--------------------
``oncotriage/spend.py``'s docstring states the gap: provider-side budget alarms
observe a bill that has already been incurred, and until the gate existed the
only brake on a campaign was an operator who already knew something was wrong.
This is the demonstration that the brake works, in both directions, and that its
edges are the ones ``config.SPEND_CAP_USD`` states rather than whatever they
happen to be.

WHAT IT HOLDS
-------------
    1. THE CONFIGURATION SURFACE: the cap, the two enforcement flags, the unset
       semantics argued at the constant, and the two closed vocabularies --
       including the one that must stay parallel to
       ``evaluation.SHUTDOWN_SKIP_KEY_PREFIXES``, so a fourth billed call site
       added to one and not the other fails here.
    2. THE LEDGER: charged from the provider's own usage block, priced by the
       SAME function the stored row is priced by, exact under MAX_WORKERS
       threads, and honest about what it could not price.
    3. THE DERIVED CALL CEILING, both arms, derived from the configuration
       rather than compared against a literal typed here.
    4. THE GATE, DRIVEN THROUGH THE REAL STAGE 5 NODE, both directions: under
       the cap every request goes out; at the cap the warmup is declined and
       the patient sends NOTHING; crossing it mid-wave declines the queued
       calls and FAILS the patient -- which is the c33 lesson, because a patient
       that COMPLETED with most of its trials unjudged would be checkpointed and
       skipped forever.
    5. THE OVERSHOOT BOUND, MEASURED. Every request already past the gate is
       issued and no more, and the count is exactly the in-flight bound the
       config block names.
    6. THE BREAKER: a Stage 5 invocation that asks for more billed calls than
       its configuration can produce is declined at the ceiling, with its own
       reason and its own counter.
    7. THE RESUME, against a real database with known rows: the chain is walked
       exactly as ``queries.campaign_summary`` stitches it -- pinned against
       that query rather than described -- a fingerprint change breaks the
       chain, and a row with no cost makes the baseline a FLOOR that says so.
    8. THE RUNNER: in-flight patients complete and are written, nothing new
       starts, and the run records STOPPED with a machine-readable reason that
       is not an operator stop and not a crash.
    9. THE BILLED CALL SITES ARE ENUMERATED FROM SOURCE AND PINNED, so a future
       billed path cannot be added outside the gate silently -- with a planted
       bypass shown to fail and a clean control shown to pass.

WHAT IT COSTS TO RUN: nothing. No network, no keys, NO SPEND, no live Qdrant, no
model load (``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set above the imports and
section 10 asserts torch and transformers never entered ``sys.modules``), no
corpus, no git history, no live server. Every model response is a literal served
by a stub installed through ``oncotriage/agent/deps.py``.

IT IS NOT IN THE COLLISION MATRIX. Every database is a scratch file inside a
``tempfile.mkdtemp`` it removes and asserts gone, ``paths._RESOLVED`` is seeded
so nothing can resolve to the production tree, and the three repository files it
reads (``agent/evaluation.py``, ``spend.py``, ``storage/database_logger.py``)
are written by neither of the suite's two writers and are sha256-compared at the
end. It DOES exec: in-memory copies of ``agent/evaluation.py``, one plant each,
argued at ``_EXEC_ALLOWLIST``.
"""

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import types

# ABOVE THE IMPORTS, for oncotriage/fixtures/replay.py's reason: `agent.deps`
# reads this at ITS import, so an assignment below the first `oncotriage` import
# reaches nothing and MedCPT loads for real.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODE = os.path.dirname(_HERE)
if _CODE not in sys.path:
    sys.path.insert(0, _CODE)

import ast                                                      # noqa: E402

from oncotriage import config                                   # noqa: E402
from oncotriage import paths as _paths                          # noqa: E402
from oncotriage import spend                                    # noqa: E402
from oncotriage.agent import deps                               # noqa: E402
from oncotriage.agent import evaluation as _evaluation          # noqa: E402
from oncotriage.storage import database_logger as _dl           # noqa: E402
from oncotriage.storage import queries as _queries              # noqa: E402
from oncotriage.utils import get_model_cost                     # noqa: E402


_EVAL_PATH = os.path.abspath(_evaluation.__file__)
_SPEND_PATH = os.path.abspath(spend.__file__)
_DB_PATH = os.path.abspath(_dl.__file__)

# Hashed BEFORE anything else runs, so section 10 compares against the tree as
# it was found rather than against whatever a plant left behind.
_BASELINE_HASHES = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
                    for p in (_EVAL_PATH, _SPEND_PATH, _DB_PATH)}

# CAPTURED AT IMPORT, NOT TYPED AS A LITERAL. Section 10's restore check
# compares against THESE, so a legitimate change to a shipped default moves
# check 1a and nothing else -- the lesson
# tests/test_agent_stage5_per_trial_calls.py records about its own 10e.
_START_RATER_CAP = config.RATER_SPEND_CAP_USD
"""The judge's OWN cap, captured at import so the probes below can be shown to
have put it back. It is a separate constant from the campaign's by operator
ruling -- see `spend.SPEND_BUDGETS`."""

_START_CONFIG = (config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED,
                 config.SPEND_CALL_CEILING_ENFORCED,
                 config.MATCHING_PER_TRIAL_CALLS_ENABLED,
                 config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)

_TMP = tempfile.mkdtemp(prefix="oncotriage-spend-gate-")

# EVERY PATH THIS FILE COULD RESOLVE IS SEEDED, so nothing can reach the
# production tree even if a helper resolves one -- tests/test_ablation_db_
# isolation.py's seam, used for its reason.
_SAVED_RESOLVED = dict(_paths._RESOLVED)
_paths._RESOLVED["inferences_path"] = os.path.join(_TMP, "never-written.db")


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


def check_true(label, actual):
    check(label, bool(actual), True)


def section(title):
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


class _Absent:
    """A value that equals nothing a check expects, and NEVER raises.

    THE ABORT CLASS THIS PROJECT HAS SHIPPED SIXTEEN TIMES. A bare
    ``result["key"]`` inside a ``check(...)`` argument list raises while the
    argument is being EVALUATED -- on precisely the defect the check exists to
    catch -- so the run prints one traceback where it owed a summary and a
    hundred results. Every raise-capable read in this file goes through
    ``at()``, ``drive()`` or ``raised()``.
    """

    def __init__(self, why):
        self.why = why

    def __repr__(self):
        return f"<absent: {self.why}>"

    def __eq__(self, other):
        return isinstance(other, _Absent) and other.why == self.why

    def __bool__(self):
        return False

    def __len__(self):
        return 0


def at(mapping, key):
    """``mapping[key]`` or a named absence. Never raises."""
    try:
        return mapping[key]
    except Exception as exc:                                    # noqa: BLE001
        return _Absent(f"{key}: {type(exc).__name__}")


def drive(fn, *a, **kw):
    """Call ``fn``; a raise becomes a value a check can fail on."""
    try:
        return fn(*a, **kw)
    except BaseException as exc:                                # noqa: BLE001
        return _Absent(f"{type(exc).__name__}: {exc}")


def raised(fn, *a, **kw):
    """The exception type name a call raised, or None."""
    try:
        fn(*a, **kw)
        return None
    except BaseException as exc:                                # noqa: BLE001
        return type(exc).__name__


# ===========================================================================
# THE STUB
# ===========================================================================

class _Usage:
    def __init__(self, prompt_tokens, completion_tokens):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.completion_tokens_details = None
        self.prompt_tokens_details = None


class _Message:
    def __init__(self, content):
        self.content = content
        self.refusal = None


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)
        self.finish_reason = "stop"


class _Response:
    def __init__(self, content, prompt_tokens=1000, completion_tokens=100,
                 model=None):
        self.choices = [_Choice(content)]
        self.usage = _Usage(prompt_tokens, completion_tokens)
        self.model = model or config.MATCHING_MODEL


# ONE CALL'S COST, DERIVED FROM THE SAME FUNCTION THE LEDGER USES rather than
# typed here. A literal would be a second copy of PRICING_CONFIG's arithmetic
# and would go stale the day a rate moves -- which is the class of defect this
# whole project removes.
CALL_TOKENS = (1000, 100)
CALL_COST = get_model_cost(config.matching_wire_model(), *CALL_TOKENS)


def _body(nct_ids):
    return json.dumps({"evaluations": [
        {"assessment": "No known disqualifiers.", "eligible": "eligible",
         "inclusion_criteria": [{"criterion": "Age 18+",
                                 "patient_value": "61", "status": "met"}],
         "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
        for i in nct_ids]})


class _Stub:
    """Answers the trials it was asked about and records every request.

    ``barrier_size`` makes every TRIAL call wait for that many peers before it
    returns, which is what proves the overshoot is measured with the requests
    provably in flight together rather than with a scheduler that happened to
    serialize them.
    """

    def __init__(self, *, barrier_size=None, barrier_timeout=15.0):
        self.requests = []
        self._lock = threading.Lock()
        self._barrier = (threading.Barrier(barrier_size)
                         if barrier_size else None)
        self._barrier_timeout = barrier_timeout
        self.barrier_broken = False

    @property
    def chat(self):
        return types.SimpleNamespace(completions=self)

    def create(self, **kwargs):
        messages = kwargs["messages"]
        user = messages[1]["content"]
        with self._lock:
            self.requests.append(kwargs)
        ids = sorted(set(_NCT_RE.findall(user)))
        if not ids:
            # THE WARMUP, recognised by its USER MESSAGE rather than by "the
            # first request": a defect that stopped sending it would make
            # request 0 a trial call, and a stub that DEFINED request 0 as the
            # warmup could not see that.
            return _Response("{}", *CALL_TOKENS)
        if self._barrier is not None:
            try:
                self._barrier.wait(timeout=self._barrier_timeout)
            except threading.BrokenBarrierError:
                self.barrier_broken = True
        return _Response(_body(ids), *CALL_TOKENS)

    @property
    def trial_requests(self):
        return [r for r in self.requests
                if _NCT_RE.findall(r["messages"][1]["content"])]

    @property
    def warmups(self):
        return [r for r in self.requests
                if not _NCT_RE.findall(r["messages"][1]["content"])]


import re                                                       # noqa: E402
_NCT_RE = re.compile(r"NCT\d{8}")


PATIENT = {
    "patient_id": "spend-gate-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def trial(index):
    half = "x" * 200
    return {"trial": {"nct_id": "NCT%08d" % index,
                      "title": f"Trial {index}",
                      "phase": "PHASE2",
                      "eligibility": {
                          "inclusion_criteria": "Inclusion Criteria:\n- " + half,
                          "exclusion_criteria": "Exclusion Criteria:\n- " + half}},
            "score": 0.5, "rerank_score": 0.5}


_SIX = [trial(i) for i in range(6)]


def run_node(trials, *, cap=None, enforced=True, ceiling_enforced=True,
             parallel=1, per_trial=True, seed_usd=0.0, node=None, stub=None,
             **stub_kw):
    """Drive Stage 5 once under a chosen budget. Returns ``(result, stub)``.

    THE LEDGER IS RESET PER DRIVE, deliberately, because it is process-global
    state and a scenario that inherited the previous one's spend would be
    measuring the wrong campaign -- which is the same reason
    ``oncotriage/batch/runner.py:main()`` resets it.
    """
    node = node or _evaluation.node_llm_classifier_evaluation
    stub = stub if stub is not None else _Stub(**stub_kw)
    saved = (config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED,
             config.SPEND_CALL_CEILING_ENFORCED,
             config.MATCHING_PER_TRIAL_CALLS_ENABLED,
             config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)
    spend.SPEND_LEDGER.reset()
    spend.SPEND_STOP.reset()
    spend.SPEND_GATE_SKIPS.clear()
    spend.SPEND_CEILING_TRIPS.clear()
    spend.SPEND_LEDGER_FAULTS.clear()
    if seed_usd:
        spend.SPEND_LEDGER.seed(spend.LedgerSeed(
            usd=seed_usd, rows=1, runs=1,
            source=spend.SEED_SOURCE_CAMPAIGN))
    deps.set_override(deps.OPENAI_CLIENT, stub)
    try:
        config.SPEND_CAP_USD = cap
        config.SPEND_CAP_ENFORCED = enforced
        config.SPEND_CALL_CEILING_ENFORCED = ceiling_enforced
        config.MATCHING_PER_TRIAL_CALLS_ENABLED = per_trial
        config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS = parallel
        state = {"patient_data": PATIENT, "filtered_trials": trials,
                 "llm_classifier_retries": 0, "mesh_filter_applied": True,
                 "mesh_filter_skip_reason": "applied", "stage_timings": {}}
        return drive(node, state), stub
    finally:
        (config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED,
         config.SPEND_CALL_CEILING_ENFORCED,
         config.MATCHING_PER_TRIAL_CALLS_ENABLED,
         config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS) = saved
        deps.clear_override(deps.OPENAI_CLIENT)


_EVAL_SRC = open(_EVAL_PATH, encoding="utf-8").read()


def module_from(source, name):
    """exec a patched copy of evaluation.py into its own namespace.

    A PATCHED IN-MEMORY COPY, never an edit to the file: this project's stated
    preference, and what keeps this file out of the collision matrix.
    """
    module = types.ModuleType(name)
    module.__file__ = _EVAL_PATH
    exec(compile(source, f"<{name}>", "exec"), module.__dict__)
    return module


# ===========================================================================
# SECTION 1 -- THE CONFIGURATION SURFACE
# ===========================================================================

section("SECTION 1 -- the cap, the flags and the two closed vocabularies")

check("1a  the shipped cap is the operator's ruling: 300 US dollars",
      _START_CONFIG[0], 300.00)
check("1b  ...and both enforcement flags ship ON, so the brake is a brake",
      (_START_CONFIG[1], _START_CONFIG[2]), (True, True))

# THE UNSET SEMANTICS, DRIVEN. `None` is no cap and is reachable only by an
# explicit edit; a negative number is NOT read as unlimited, and a non-number
# is not read as anything.
_saved_cap = config.SPEND_CAP_USD
try:
    config.SPEND_CAP_USD = None
    check("1c  None means NO CAP rather than a cap of nothing",
          (spend.spend_cap(), spend.cap_exceeded(spend.SPEND_SOURCE_STAGE5), spend.remaining(spend.SPEND_SOURCE_STAGE5)),
          (None, False, None))
    check("1c-i ...and the banner SAYS SO, so the unbounded state is not the "
          "quiet one",
          "NO SPEND CAP IS SET" in spend.describe_cap(), True)
    config.SPEND_CAP_USD = 0.0
    check("1d  zero IS a cap and is honoured -- a rehearsal of the unbilled "
          "path, not an absence",
          (spend.spend_cap(), spend.cap_exceeded(spend.SPEND_SOURCE_STAGE5)), (0.0, True))
    config.SPEND_CAP_USD = -1.0
    check("1e  a NEGATIVE cap is REFUSED by name, never read as unlimited",
          raised(spend.spend_cap), "SpendCapConfigurationError")
    config.SPEND_CAP_USD = "300"
    check("1e-i ...and so is a value that is not a number at all",
          raised(spend.spend_cap), "SpendCapConfigurationError")
    config.SPEND_CAP_USD = True
    check("1e-ii ...bool included, on this project's standing footing: a cap "
          "of True priced as one dollar is a budget nobody set",
          raised(spend.spend_cap), "SpendCapConfigurationError")
    config.SPEND_CAP_USD = -1.0
    check("1e-iii ...and an unreadable cap does NOT stop the run from inside a "
          "worker: cap_exceeded() swallows it, because a configuration defect "
          "surfacing as a per-request transport failure is a worse diagnosis "
          "of the same fact",
          spend.cap_exceeded(spend.SPEND_SOURCE_STAGE5), False)
    check("1e-iv ...it reaches the operator through the BANNER instead, before "
          "anything is spent",
          "REFUSING TO READ THE CAP" in spend.describe_cap(), True)
finally:
    config.SPEND_CAP_USD = _saved_cap
check("1f  ...and the cap was restored", config.SPEND_CAP_USD, _START_CONFIG[0])

# THE TWO CLOSED VOCABULARIES.
check("1g  SPEND_LIMITS is closed and its two members are distinct findings "
      "with distinct remedies",
      spend.SPEND_LIMITS,
      (spend.SPEND_LIMIT_CAP, spend.SPEND_LIMIT_CALL_CEILING))
check("1h  SPEND_SKIP_KEY_PREFIXES is closed",
      spend.SPEND_SKIP_KEY_PREFIXES,
      (spend.SPEND_SKIP_WARMUP_KEY_PREFIX, spend.SPEND_SKIP_WAVE_KEY_PREFIX,
       spend.SPEND_SKIP_SEND_KEY_PREFIX))

# *** THE ONE THAT MATTERS MOST IN THIS SECTION. *** Both tuples partition THE
# PLACES A STAGE 5 REQUEST CAN BE DECLINED, and there are three because there
# are three billed call sites. A fourth site added to one tuple and not the
# other is a phase that arrives in an operator's report as an unclassified key,
# so they are required to stay parallel rather than merely both being closed.
check("1i  *** the spend phases and the shutdown phases are the SAME THREE, "
      "so a fourth billed call site cannot join one vocabulary and not the "
      "other ***",
      sorted(spend.SPEND_SKIP_KEY_PREFIXES),
      sorted(_evaluation.SHUTDOWN_SKIP_KEY_PREFIXES))
check("1i-i non-degeneracy: there are three of them, so 1i is not comparing "
      "two empty tuples",
      len(spend.SPEND_SKIP_KEY_PREFIXES), 3)

# THIS PIN MOVED FROM TWO MEMBERS TO THREE AT THE SPEND-COVERAGE PASS, AND IT
# IS THE CHECK WORKING RATHER THAN A NUMBER RETYPED. `rater_state` is a THIRD
# seed source and not a reuse of `campaign_rows`: that one names a sum over
# `inferences.estimated_cost_usd` walked over the `runs` chain, and this one
# names a running total `oncotriage/evaluation/rater.py` writes into its own
# state file after each batch is collected -- different store, different
# arithmetic, different price table. The pin stays EXACT, which is what makes
# a fourth member added without an argument fail here.
check("1j  SEED_SOURCES is closed and `fresh` is a VALUE rather than an "
      "absence: 'this campaign has no prior spend' and 'nobody asked' are "
      "different statements",
      spend.SEED_SOURCES,
      (spend.SEED_SOURCE_NONE, spend.SEED_SOURCE_CAMPAIGN,
       spend.SEED_SOURCE_RATER_STATE))
check("1j-i ...and every member is a distinct non-empty string, so the tuple "
      "cannot grow a member that reads as one already there",
      (len(set(spend.SEED_SOURCES)), all(spend.SEED_SOURCES)),
      (len(spend.SEED_SOURCES), True))

check("1k  RUN_STOP_REASONS is closed and has no duplicate -- a duplicated "
      "member would make a GROUP BY over runs.stop_reason report two "
      "mechanisms as one",
      (_dl.RUN_STOP_REASONS,
       len(set(_dl.RUN_STOP_REASONS)) == len(_dl.RUN_STOP_REASONS)),
      (("operator", "spend_cap", "call_ceiling"), True))
check("1k-i ...and the two spend members are named by the SAME strings the "
      "gate's own limit vocabulary uses, so a row and a counter key can be "
      "joined without a translation table",
      (_dl.RUN_STOP_REASON_SPEND_CAP, _dl.RUN_STOP_REASON_CALL_CEILING),
      (spend.SPEND_LIMIT_CAP, spend.SPEND_LIMIT_CALL_CEILING))

# THE THREE COUNTERS REACH THE RUN-END REPORT. A counter with no reader is the
# shape oncotriage/degradation.py exists to remove.
from oncotriage import degradation as _degradation              # noqa: E402
check("1l  all three spend counters are in the degradation registry, so a "
      "gated run says so on its own report",
      sorted(n for n in _degradation.registered_names()
             if n.startswith("SPEND_")),
      ["SPEND_CEILING_TRIPS", "SPEND_GATE_SKIPS", "SPEND_LEDGER_FAULTS"])


# ===========================================================================
# SECTION 2 -- THE LEDGER
# ===========================================================================

section("SECTION 2 -- what the ledger measures, and what it cannot see")

spend.SPEND_LEDGER.reset()
spend.SPEND_LEDGER_FAULTS.clear()

check("2a  a fresh ledger is empty in every field",
      (spend.SPEND_LEDGER.total, spend.SPEND_LEDGER.measured,
       spend.SPEND_LEDGER.calls, spend.SPEND_LEDGER.seeded.runs),
      (0.0, 0.0, 0, 0))

_charged = spend.SPEND_LEDGER.charge(config.matching_wire_model(), 1000, 100)
check("2b  a charge is priced by the SAME function the stored row is priced "
      "by, so the gate and inferences.estimated_cost_usd cannot disagree "
      "about a rate",
      (round(_charged, 10), round(spend.SPEND_LEDGER.total, 10)),
      (round(CALL_COST, 10), round(CALL_COST, 10)))
check("2b-i non-degeneracy: that cost is not zero, so 2b is not comparing two "
      "zeroes",
      CALL_COST > 0, True)

# A MODEL WITH NO PRICE IS COUNTED, NOT PRICED, AND NOT RAISED.
_before = spend.SPEND_LEDGER.total
_r = spend.SPEND_LEDGER.charge("a-model-nobody-priced", 1000, 100)
check("2c  an unpriced model does NOT raise inside a worker -- the loud "
      "failure already exists at log_inference, and a raise here would become "
      "a transport failure and a SECOND billed call",
      (_r, spend.SPEND_LEDGER.total), (0.0, _before))
check("2c-i ...it is COUNTED, because every key here is spend the gate cannot "
      "see and the cap is then enforced against a number lower than the truth",
      [k for k in spend.SPEND_LEDGER_FAULTS if k.startswith("unpriced_model:")],
      ["unpriced_model:a-model-nobody-priced"])
check("2c-ii ...and the CALL is still counted, which is what makes the fault "
      "count readable: it is the numerator and `calls` is the denominator",
      spend.SPEND_LEDGER.calls, 2)

for _bad, _why in ((None, "None"), ("1000", "str"), (True, "bool"),
                   (-5, "negative")):
    _t = spend.SPEND_LEDGER.total
    spend.SPEND_LEDGER.charge(config.matching_wire_model(), _bad, 100)
    check(f"2d  a usage figure that is not a token count ({_why}) is counted "
          f"and contributes nothing",
          spend.SPEND_LEDGER.total, _t)
check("2d-i ...under `bad_usage:`, which is a different remedy from an "
      "unpriced model and therefore a different key",
      sum(v for k, v in spend.SPEND_LEDGER_FAULTS.items()
          if k.startswith("bad_usage:")), 4)

# THE SEED.
spend.SPEND_LEDGER.reset()
spend.SPEND_LEDGER.seed(spend.LedgerSeed(usd=10.0, rows=4, unpriced=1, runs=2,
                                         source=spend.SEED_SOURCE_CAMPAIGN))
spend.SPEND_LEDGER.charge(config.matching_wire_model(), 1000, 100)
check("2e  the total is the seeded baseline PLUS this process's spend, which "
      "is what makes the cap a campaign budget rather than a per-invocation "
      "allowance",
      round(spend.SPEND_LEDGER.total, 10), round(10.0 + CALL_COST, 10))
check("2e-i ...and `measured` still reports only what THIS process was billed",
      round(spend.SPEND_LEDGER.measured, 10), round(CALL_COST, 10))
check("2f  a seed carrying an unpriced row is a FLOOR and says so",
      (spend.SPEND_LEDGER.seeded.is_floor,
       "A FLOOR, NOT A TOTAL" in spend.describe_seed(spend.SPEND_LEDGER.seeded)),
      (True, True))
check("2f-i ...and one that does not is not",
      spend.LedgerSeed(usd=1.0, rows=2, runs=1).is_floor, False)

spend.SPEND_LEDGER.reset()
check("2g  reset forgets everything an earlier run in this process spent -- "
      "the sixth piece of per-run module state",
      (spend.SPEND_LEDGER.total, spend.SPEND_LEDGER.calls,
       spend.SPEND_LEDGER.seeded.runs), (0.0, 0, 0))

# EXACT UNDER CONTENTION. The write is locked precisely because it is a
# read-modify-write charged from MAX_WORKERS x per_trial_parallel_bound()
# worker threads at once, and `total += cost` is not atomic.
_N_THREADS, _PER_THREAD = config.MAX_WORKERS, 200
_barrier = threading.Barrier(_N_THREADS)


def _hammer():
    _barrier.wait(timeout=15)
    for _ in range(_PER_THREAD):
        spend.SPEND_LEDGER.charge(config.matching_wire_model(), 1000, 100)


_threads = [threading.Thread(target=_hammer) for _ in range(_N_THREADS)]
for _t in _threads:
    _t.start()
for _t in _threads:
    _t.join(timeout=30)
check("2h  *** the ledger is EXACT under MAX_WORKERS threads, which is what a "
      "decision may be made on -- an unlocked `total += cost` loses "
      "increments and would enforce a cap against a number lower than the "
      "truth ***",
      (spend.SPEND_LEDGER.calls,
       round(spend.SPEND_LEDGER.total, 8)),
      (_N_THREADS * _PER_THREAD,
       round(CALL_COST * _N_THREADS * _PER_THREAD, 8)))
spend.SPEND_LEDGER.reset()
spend.SPEND_LEDGER_FAULTS.clear()


# ===========================================================================
# SECTION 3 -- THE DERIVED CALL CEILING
# ===========================================================================

section("SECTION 3 -- the ceiling is derived from configuration, not chosen")

check("3a  per-trial: one warmup plus one call per candidate trial, and "
      "nothing else -- a per-trial chunk is a singleton and the splitter "
      "refuses to halve one",
      spend.stage5_call_ceiling(config.MATCHING_CALL_MODE_PER_TRIAL),
      1 + config.MAX_TRIALS_FOR_EVALUATION)
check("3b  grouped: the packer's chunk ceiling times the reactive splitter's "
      "own 2**(D+1)-1, the identical expression "
      "HARNESS_POST_READ_TIMEOUT_SECONDS is written over",
      spend.stage5_call_ceiling(config.MATCHING_CALL_MODE_GROUPED),
      config.MATCHING_MAX_INPUT_PACKED_CHUNKS
      * (2 ** (config.MAX_TRUNCATION_SPLITS + 1) - 1))
check("3c  non-degeneracy: the two arms differ, so 3a/3b are not one number "
      "checked twice",
      spend.stage5_call_ceiling(config.MATCHING_CALL_MODE_PER_TRIAL)
      != spend.stage5_call_ceiling(config.MATCHING_CALL_MODE_GROUPED), True)
check("3d  ...and it MOVES with the configuration rather than being a literal "
      "that agrees with it today",
      spend.stage5_call_ceiling(config.MATCHING_CALL_MODE_PER_TRIAL) > 1, True)

_saved_trials = config.MAX_TRIALS_FOR_EVALUATION
try:
    config.MAX_TRIALS_FOR_EVALUATION = _saved_trials + 7
    check("3d-i driven: raising MAX_TRIALS_FOR_EVALUATION raises the per-trial "
          "ceiling by exactly the same amount",
          spend.stage5_call_ceiling(config.MATCHING_CALL_MODE_PER_TRIAL),
          1 + _saved_trials + 7)
finally:
    config.MAX_TRIALS_FOR_EVALUATION = _saved_trials
check("3d-ii ...and it was restored",
      config.MAX_TRIALS_FOR_EVALUATION, _saved_trials)

check("3e  with no argument it asks the owner, so a caller that has not read "
      "the mode still gets the right ceiling",
      spend.stage5_call_ceiling(),
      spend.stage5_call_ceiling(config.matching_call_mode()))

# THE COUNTER CARRIES THE MODE IT WAS DERIVED FOR.
_counter = spend.Stage5CallCounter(2, "per_trial")
check("3f  a counter carries the mode its ceiling was DERIVED from, so a trip "
      "key cannot pair a ceiling with a mode it was not computed for",
      (_counter.ceiling, _counter.call_mode), (2, "per_trial"))
check("3g  take() claims a call and refuses past the ceiling, and NAMES THE "
      "FIRST REFUSAL -- which is what lets the trip counter report invocations "
      "while the skip counter reports requests",
      [_counter.take() for _ in range(4)],
      [(True, False), (True, False), (False, True), (False, False)])
check("3g-i ...and the issued count stops at the ceiling rather than running "
      "past it, while the refusals keep counting",
      (_counter.issued, _counter.refusals), (2, 2))

# THE CLAIM IS ONE LOCKED OPERATION. A check-then-act split across the lock
# admits one extra call per worker, which is exactly the race the wave's own
# pool would produce.
_c2 = spend.Stage5CallCounter(50, "per_trial")
_taken = []
_tl = threading.Lock()
_b2 = threading.Barrier(_N_THREADS)


def _claim():
    _b2.wait(timeout=15)
    got = sum(1 for _ in range(20) if _c2.take()[0])
    with _tl:
        _taken.append(got)


_ts = [threading.Thread(target=_claim) for _ in range(_N_THREADS)]
for _t in _ts:
    _t.start()
for _t in _ts:
    _t.join(timeout=30)
check("3h  *** the claim is EXACT under threads: never more than the ceiling, "
      "however many workers race for it ***",
      (sum(_taken), _c2.issued), (50, 50))

# EXACTLY ONE WORKER IS TOLD IT WAS THE FIRST REFUSAL, WHICH IS THE PROPERTY
# THE TRIP COUNTER RESTS ON. Driven on a counter that can grant nothing, with
# every thread racing for the same refusal -- a caller deriving "first" from a
# `refusals == 1` read after the lock was released would see zero of them or
# several.
_c4 = spend.Stage5CallCounter(0, "per_trial")
_firsts = []
_fl = threading.Lock()
_b3 = threading.Barrier(_N_THREADS)


def _race_refusal():
    _b3.wait(timeout=15)
    got = sum(1 for _ in range(20) if _c4.take()[1])
    with _fl:
        _firsts.append(got)


_ts4 = [threading.Thread(target=_race_refusal) for _ in range(_N_THREADS)]
for _t in _ts4:
    _t.start()
for _t in _ts4:
    _t.join(timeout=30)
check("3h-i ...and exactly ONE of "
      f"{_N_THREADS * 20} racing refusals is told it was the FIRST, which is "
      "what makes SPEND_CEILING_TRIPS a count of INVOCATIONS while "
      "SPEND_GATE_SKIPS counts requests",
      (sum(_firsts), _c4.refusals), (1, _N_THREADS * 20))

# THE OFF SWITCH DOES NOT STOP COUNTING.
_saved_ce = config.SPEND_CALL_CEILING_ENFORCED
try:
    config.SPEND_CALL_CEILING_ENFORCED = False
    _c3 = spend.Stage5CallCounter(1, "per_trial")
    check("3i  with the ceiling not enforced every claim is granted -- and the "
          "count is still kept, so a measurement mode measures",
          ([_c3.take()[0] for _ in range(3)], _c3.issued),
          ([True, True, True], 3))
finally:
    config.SPEND_CALL_CEILING_ENFORCED = _saved_ce


# ===========================================================================
# SECTION 4 -- THE GATE, DRIVEN THROUGH THE REAL STAGE 5 NODE
# ===========================================================================

section("SECTION 4 -- under the cap it proceeds; at the cap it stops cleanly")

# --- 4a: under the cap, everything goes out --------------------------------
_ok, _ok_stub = run_node(_SIX, cap=100.0)
check("4a  under the cap the whole patient is sent: one warmup and one call "
      "per trial",
      (len(_ok_stub.warmups), len(_ok_stub.trial_requests)), (1, 6))
check("4a-i ...and the patient SUCCEEDS, with a verdict per trial",
      (at(_ok, "error"), len(at(_ok, "evaluations") or [])), ("", 6))
check("4a-ii ...and the ledger charged every one of the seven",
      (spend.SPEND_LEDGER.calls, round(spend.SPEND_LEDGER.total, 10)),
      (7, round(7 * CALL_COST, 10)))
check("4a-iii ...and nothing was declined",
      dict(spend.SPEND_GATE_SKIPS), {})
check("4a-iv ...and the latch did not trip",
      (spend.SPEND_STOP.requested, spend.SPEND_STOP.limit), (False, None))

# --- 4b: at the cap BEFORE the first request -------------------------------
# The campaign's budget is already spent when this patient starts, which is the
# state every patient after the first gated one is in.
_over, _over_stub = run_node(_SIX, cap=1.0, seed_usd=1.0)
check("4b  *** at the cap the patient sends NOTHING AT ALL -- not the warmup, "
      "not one trial ***",
      len(_over_stub.requests), 0)
check("4b-i ...and the ledger is untouched, because nothing was billed",
      (spend.SPEND_LEDGER.calls, spend.SPEND_LEDGER.measured), (0, 0.0))
check("4b-ii ...the decline is counted under the WARMUP phase, which is what "
      "says this patient cost nothing rather than being cut off part-way",
      dict(spend.SPEND_GATE_SKIPS),
      {f"{spend.SPEND_SKIP_WARMUP_KEY_PREFIX}{spend.SPEND_LIMIT_CAP}": 1})
check("4b-iii ...and the patient FAILS rather than completing: a completed "
      "patient is CHECKPOINTED, and a resume would skip it forever with none "
      "of its trials judged",
      (bool(at(_over, "error")), at(_over, "evaluations")), (True, []))
check("4b-iv ...the error names the campaign's spend, so an operator reading "
      "one failed row knows which of the failures this is",
      "campaign has spent" in str(at(_over, "error")), True)
check("4b-iv-a ...and the floor names the BUDGET rather than a shutdown: "
      "WARMUP_SOURCE_SPEND_LIMIT is its own member precisely so this row does "
      "not tell an operator that somebody interrupted a run nobody touched",
      ("spend limit was reached" in str(at(_over, "error")),
       "shutdown was requested" in str(at(_over, "error"))), (True, False))
check("4b-v ...and the latch tripped on the CAP rather than on the ceiling",
      (spend.SPEND_STOP.requested, spend.SPEND_STOP.limit),
      (True, spend.SPEND_LIMIT_CAP))

# THE ANNOUNCEMENT SAYS WHAT IT KNOWS AND NOT MORE. The latch is reachable from
# every Stage 5 caller -- the API has no patients, no checkpoint and no `runs`
# row -- so a banner promising a checkpoint would be false on a real path.
_ann = []
_saved_out = spend.console.out
try:
    spend.console.out = lambda *a: _ann.append(a[0] if a else "")
    spend.SPEND_STOP.reset()
    spend.SPEND_LEDGER.seed(spend.LedgerSeed(usd=1000.0, rows=1, runs=1,
                                             source=spend.SEED_SOURCE_CAMPAIGN))
    spend.SPEND_STOP.poll(where="a probe",
                      source=spend.SPEND_SOURCE_STAGE5)
finally:
    spend.console.out = _saved_out
    spend.SPEND_LEDGER.reset()
    spend.SPEND_STOP.reset()
_ann_text = "\n".join(_ann)
check("4b-vi the announcement speaks about REQUESTS, not about a checkpoint or "
      "a run row -- it is reachable from callers that have neither",
      ("No further billed request" in _ann_text,
       "checkpoint" in _ann_text, "recorded STOPPED" in _ann_text),
      (True, False, False))
check("4b-vi-i non-degeneracy: the announcement was captured at all, so the "
      "two absences above are about its text rather than about an empty string",
      len(_ann) >= 4, True)

# --- 4c: crossing the cap MID-WAVE -----------------------------------------
# Serialized (parallel=1) so the count is decided by the gate rather than by the
# scheduler: the warmup and two trial calls fit inside a cap of three calls, and
# the third trial call meets a ledger that has reached it.
_mid, _mid_stub = run_node(_SIX, cap=3 * CALL_COST, parallel=1)
check("4c  *** crossing the cap mid-wave issues exactly what the budget "
      "allowed and declines the rest ***",
      (len(_mid_stub.warmups), len(_mid_stub.trial_requests)), (1, 2))
check("4c-i ...the declines are counted under the WAVE phase, which is what "
      "separates 'we stopped before this patient' from 'we stopped inside it'",
      dict(spend.SPEND_GATE_SKIPS),
      {f"{spend.SPEND_SKIP_WAVE_KEY_PREFIX}{spend.SPEND_LIMIT_CAP}": 4})
check("4c-ii ...and the patient FAILS rather than completing with four "
      "not-evaluable trials -- the c33 lesson, reached for money instead of "
      "for a signal",
      (bool(at(_mid, "error")), at(_mid, "evaluations")), (True, []))
check("4c-iii ...the tokens it WAS billed are carried on the failure return, "
      "so estimated_cost_usd on that row is not a false zero",
      at(_mid, "llm_classifier_calls"), 3)

# --- 4d: the measurement mode ----------------------------------------------
_meas, _meas_stub = run_node(_SIX, cap=CALL_COST, enforced=False)
check("4d  with SPEND_CAP_ENFORCED False nothing is declined -- the ledger "
      "still accumulates, so a first campaign can be run with the gate "
      "OBSERVING before it is trusted to stop one",
      (len(_meas_stub.requests), round(spend.SPEND_LEDGER.total, 10)),
      (7, round(7 * CALL_COST, 10)))
check("4d-i ...and the banner says the cap is measured only, so the state is "
      "not mistaken for a working brake",
      "MEASURED ONLY" in spend.describe_cap()
      if not config.SPEND_CAP_ENFORCED else True, True)

# --- 4e: no cap at all -----------------------------------------------------
_none, _none_stub = run_node(_SIX, cap=None)
check("4e  with no cap set the gate declines nothing",
      (len(_none_stub.requests), dict(spend.SPEND_GATE_SKIPS)), (7, {}))

# --- 4f: GROUPED mode is gated too ----------------------------------------
# The retained comparison arm has no warmup and one sequential send loop, so its
# only phase is `send:` -- and it is the phase that would be silently ungated if
# the gate had been put on the per-trial path alone.
_grp_over, _grp_stub = run_node(_SIX, cap=1.0, seed_usd=1.0, per_trial=False)
check("4f  *** the RETAINED GROUPED arm is gated at its own phase, not left "
      "to the per-trial path ***",
      (len(_grp_stub.requests),
       dict(spend.SPEND_GATE_SKIPS)),
      (0, {f"{spend.SPEND_SKIP_SEND_KEY_PREFIX}{spend.SPEND_LIMIT_CAP}": 1}))
check("4f-i ...and the patient fails there too",
      bool(at(_grp_over, "error")), True)
_grp_ok, _grp_ok_stub = run_node(_SIX, cap=100.0, per_trial=False)
check("4f-ii ...while an ordinary grouped patient is untouched: one packed "
      "request, no warmup, no decline",
      (len(_grp_ok_stub.requests), len(_grp_ok_stub.warmups),
       dict(spend.SPEND_GATE_SKIPS)), (1, 0, {}))


# ===========================================================================
# SECTION 5 -- THE OVERSHOOT BOUND
# ===========================================================================

section("SECTION 5 -- the cap is honoured to within the requests in flight")

# EVERY REQUEST ALREADY PAST THE GATE IS ISSUED AND NO MORE. Driven with a
# BARRIER of `parallel` on the trial calls, so the workers are provably past the
# gate together rather than having been serialized by a scheduler that happened
# to be idle -- which is the difference between measuring the bound and
# measuring this machine.
_PARALLEL = 4
_boundary, _b_stub = run_node(
    _SIX, cap=2 * CALL_COST, parallel=_PARALLEL, barrier_size=_PARALLEL)
check("5a  the barrier was reached, so the requests really were in flight "
      "together (non-degeneracy: a broken barrier means the run was "
      "serialized and 5b would be measuring nothing)",
      _b_stub.barrier_broken, False)
check("5b  *** the overshoot is EXACTLY the in-flight bound: the warmup plus "
      "`parallel` trial calls, and not one more ***",
      (len(_b_stub.warmups), len(_b_stub.trial_requests)), (1, _PARALLEL))
check("5b-i ...which is what config.SPEND_CAP_USD's overshoot block states, "
      "per patient: MAX_WORKERS x per_trial_parallel_bound() requests, of "
      "which this is the per_trial_parallel_bound() half",
      len(_b_stub.trial_requests) <= config.per_trial_parallel_bound(), True)
check("5c  the remaining trials were declined rather than issued",
      sum(spend.SPEND_GATE_SKIPS.values()), len(_SIX) - _PARALLEL)
check("5d  and the ledger's own total exceeds the cap by exactly what those "
      "in-flight requests cost -- the overshoot is measured, not asserted",
      round(spend.SPEND_LEDGER.total - 2 * CALL_COST, 10),
      round((1 + _PARALLEL - 2) * CALL_COST, 10))


# ===========================================================================
# SECTION 6 -- THE BREAKER
# ===========================================================================

section("SECTION 6 -- the per-invocation billed-call ceiling")

# The ceiling is derived from configuration, so it is driven by MOVING the
# configuration rather than by patching the ceiling: with room for two trials a
# six-trial patient must be declined at the third.
_saved_max = config.MAX_TRIALS_FOR_EVALUATION
try:
    config.MAX_TRIALS_FOR_EVALUATION = 2      # ceiling = 1 warmup + 2 trials
    _ceil, _ceil_stub = run_node(_SIX, cap=None, parallel=1)
finally:
    config.MAX_TRIALS_FOR_EVALUATION = _saved_max
check("6a  *** a Stage 5 invocation that asks for more billed calls than its "
      "configuration can produce is declined at the ceiling, with NO cap set "
      "at all -- so this is not the budget firing ***",
      (len(_ceil_stub.warmups), len(_ceil_stub.trial_requests)), (1, 2))
check("6a-i ...and it is counted under its OWN limit, never as a spend event: "
      "the campaign may be nowhere near its cap and an operator sent to "
      "config.SPEND_CAP_USD would be sent to the wrong file",
      sorted(spend.SPEND_GATE_SKIPS),
      [f"{spend.SPEND_SKIP_WAVE_KEY_PREFIX}{spend.SPEND_LIMIT_CALL_CEILING}"])
check("6a-ii ...with its own counter, keyed by the mode the ceiling was "
      "derived for and the ceiling itself",
      dict(spend.SPEND_CEILING_TRIPS), {"per_trial:3": 1})
check("6a-iii ...the latch records the CEILING, so the run's stop_reason is "
      "not spend_cap",
      spend.SPEND_STOP.limit, spend.SPEND_LIMIT_CALL_CEILING)
check("6a-iv ...and the patient FAILS rather than completing, for the cap's "
      "reason: a completed patient would be checkpointed",
      bool(at(_ceil, "error")), True)

# A CAP-DECLINED REQUEST MUST NOT CONSUME A CEILING CLAIM. The ceiling bounds
# what one invocation SENDS, and a request the budget declined was never sent --
# charging it against the ceiling would make a patient stopped by money look
# like a patient that had looped, which is the one thing the two limits exist to
# keep apart.
_capped, _capped_stub = run_node(_SIX, cap=3 * CALL_COST, parallel=1)
check("6a-v  a request the CAP declined leaves the ceiling untouched, so the "
      "two limits cannot be confused for each other on one patient",
      (dict(spend.SPEND_CEILING_TRIPS),
       sorted(spend.SPEND_GATE_SKIPS)),
      ({}, [f"{spend.SPEND_SKIP_WAVE_KEY_PREFIX}{spend.SPEND_LIMIT_CAP}"]))

_saved_max = config.MAX_TRIALS_FOR_EVALUATION
try:
    config.MAX_TRIALS_FOR_EVALUATION = 2
    _noceil, _noceil_stub = run_node(_SIX, cap=None, parallel=1,
                                     ceiling_enforced=False)
    # CAPTURED AT THE DRIVE, NOT READ AT THE CHECK. These are module-global
    # counters and `run_node` clears them per drive, so a check added between
    # the drive and its assertion would silently move them -- which is exactly
    # what happened when 6a-v was inserted above.
    _noceil_skips = dict(spend.SPEND_GATE_SKIPS)
finally:
    config.MAX_TRIALS_FOR_EVALUATION = _saved_max
check("6b  with SPEND_CALL_CEILING_ENFORCED False the same invocation is not "
      "declined, which is the control that says 6a is the ceiling firing "
      "rather than something else about a two-trial configuration",
      (len(_noceil_stub.trial_requests), _noceil_skips), (6, {}))


# ===========================================================================
# SECTION 7 -- THE RESUME: WHAT THE PRIOR RUNS ALREADY SPENT
# ===========================================================================

section("SECTION 7 -- the resumed baseline, derived from the rows")

_RESUME_DB = os.path.join(_TMP, "resume.db")

_FP = {"fingerprint_version": 3,
       "llm_classifier_prompt_version": "1.9.0",
       "llm_classifier_renderer_digest": "abc123",
       "matching_model_configured": "gpt-5.6-terra",
       "matching_call_mode": "per_trial",
       "qdrant_collection": "trial_criteria_20260101_000000",
       "collection_points": 12067,
       "data_snapshot_date": "2026-08-03"}


def _open_run(db, *, resumed, status, fingerprint=None, costs=()):
    """Open a run row, give it inference rows, and finalize it.

    ``costs`` is one entry per row: a float, or ``None`` for a row whose cost
    was never recorded -- which is what makes a seeded baseline a FLOOR.
    """
    rid = _dl.start_run_record("batch", db_path=db, resumed=resumed,
                               fingerprint=fingerprint or _FP)
    conn = sqlite3.connect(db)
    try:
        for i, cost in enumerate(costs):
            conn.execute(
                "INSERT INTO inferences (patient_id, timestamp, "
                "estimated_cost_usd, run_id) VALUES (?, ?, ?, ?)",
                (f"p{rid}-{i}", "2026-08-30T00:00:00", cost, rid))
        conn.commit()
    finally:
        conn.close()
    if status is not None:
        _dl.finalize_run_record(rid, status, db_path=db)
    return rid


# A CHAIN OF THREE: a crash, a resume that also crashed, and the run asking.
_r1 = _open_run(_RESUME_DB, resumed=False, status="KILLED", costs=(1.00, 2.00))
_r2 = _open_run(_RESUME_DB, resumed=True, status="STOPPED", costs=(4.00,))
_r3 = _dl.start_run_record("batch", db_path=_RESUME_DB, resumed=True,
                           fingerprint=_FP)

_spent = _dl.campaign_spend_before(_r3, db_path=_RESUME_DB)
check("7a  *** a resumed run's baseline is what its predecessors actually "
      "spent, read out of the rows ***",
      (round(_spent.usd, 2), _spent.rows, _spent.runs), (7.00, 3, 2))
check("7a-i ...and the chain is walked TRANSITIVELY, oldest first, so a "
      "campaign that crashed twice is one budget and not two",
      _spent.run_ids, (_r1, _r2))
check("7a-ii ...with nothing unpriced, so the figure is a total rather than a "
      "floor",
      (_spent.unpriced, spend.LedgerSeed(**{
          "usd": _spent.usd, "rows": _spent.rows,
          "unpriced": _spent.unpriced, "runs": _spent.runs}).is_floor),
      (0, False))

# *** PINNED AGAINST campaign_summary, WHICH OWNS THE STITCH RULE. ***
# A restated rule is a rule that can drift, so it is checked rather than
# promised -- RUN_RECORD_TERMINAL_STATUSES' precedent, one module over.
_conn = sqlite3.connect(_RESUME_DB)
try:
    _camp = _queries.run(_conn, "campaign_summary")
finally:
    _conn.close()
_rows = _camp.to_dict("records")
check("7b  *** campaign_summary stitches the SAME chain this walk does: one "
      "campaign, and its run_ids are the two predecessors plus the run that "
      "is asking ***",
      ([r["run_ids"] for r in _rows],
       [r["runs"] for r in _rows]),
      ([f"{_r1} -> {_r2} -> {_r3}"], [3]))
check("7b-i non-degeneracy: the query returned a campaign at all, so 7b is "
      "not comparing two empty lists",
      len(_rows), 1)

# A ROW WITH NO COST MAKES THE BASELINE A FLOOR, AND IT SAYS SO.
_FLOOR_DB = os.path.join(_TMP, "floor.db")
_f1 = _open_run(_FLOOR_DB, resumed=False, status="KILLED",
                costs=(1.50, None, None))
_f2 = _dl.start_run_record("batch", db_path=_FLOOR_DB, resumed=True,
                           fingerprint=_FP)
_floor = _dl.campaign_spend_before(_f2, db_path=_FLOOR_DB)
check("7c  *** a row whose cost was never recorded contributes NOTHING and is "
      "COUNTED, so the baseline is a FLOOR -- which under-counts, so the gate "
      "lets the campaign spend more than it should ***",
      (round(_floor.usd, 2), _floor.rows, _floor.unpriced),
      (1.50, 3, 2))
check("7c-i ...and every consumer says so rather than presenting it as a "
      "total: print_cost_by_model's '<- A FLOOR, NOT A TOTAL' precedent",
      "A FLOOR, NOT A TOTAL" in spend.describe_seed(spend.LedgerSeed(
          usd=_floor.usd, rows=_floor.rows, unpriced=_floor.unpriced,
          runs=_floor.runs, source=spend.SEED_SOURCE_CAMPAIGN)), True)

# A FRESH RUN INHERITS NOTHING.
_fresh = _dl.start_run_record("batch", db_path=_RESUME_DB, resumed=False,
                              fingerprint=_FP)
check("7d  a run that is resuming nothing inherits nothing, however many "
      "prior runs share its configuration",
      (_dl.campaign_spend_before(_fresh, db_path=_RESUME_DB).usd,
       _dl.campaign_spend_before(_fresh, db_path=_RESUME_DB).runs), (0.0, 0))

# A CONFIGURATION CHANGE BREAKS THE CHAIN.
_CFG_DB = os.path.join(_TMP, "config_change.db")
_c1 = _open_run(_CFG_DB, resumed=False, status="KILLED", costs=(9.00,))
_other = dict(_FP, llm_classifier_prompt_version="2.0.0")
_c2r = _dl.start_run_record("batch", db_path=_CFG_DB, resumed=True,
                            fingerprint=_other)
check("7e  *** a prompt bump between the crash and the resume BREAKS the "
      "chain: a re-configured run is a new campaign, so it does not inherit "
      "the old one's budget -- campaign_summary's own rule ***",
      (_dl.campaign_spend_before(_c2r, db_path=_CFG_DB).usd,
       _dl.campaign_spend_before(_c2r, db_path=_CFG_DB).runs), (0.0, 0))

# AN UNSTAMPED RUN STITCHES TO NOTHING.
_NOSTAMP_DB = os.path.join(_TMP, "nostamp.db")
_n1 = _open_run(_NOSTAMP_DB, resumed=False, status="KILLED", costs=(3.00,),
                fingerprint=None)
_conn = sqlite3.connect(_NOSTAMP_DB)
try:
    _conn.execute("UPDATE runs SET fingerprint_version = NULL")
    _conn.commit()
finally:
    _conn.close()
_n2 = _dl.start_run_record("batch", db_path=_NOSTAMP_DB, resumed=True)
_conn = sqlite3.connect(_NOSTAMP_DB)
try:
    _conn.execute("UPDATE runs SET fingerprint_version = NULL WHERE id = ?",
                  (_n2,))
    _conn.commit()
finally:
    _conn.close()
check("7f  two runs with NO STAMP AT ALL are not one campaign, even though "
      "SQLite's null-safe IS makes every one of their fingerprint columns "
      "compare equal -- the guard is fingerprint_version IS NOT NULL, which "
      "is campaign_summary's own",
      (_dl.campaign_spend_before(_n2, db_path=_NOSTAMP_DB).usd,
       _dl.campaign_spend_before(_n2, db_path=_NOSTAMP_DB).runs), (0.0, 0))

# A FINISHED PREDECESSOR IS NOT RESUMED ONTO.
_FIN_DB = os.path.join(_TMP, "finished.db")
_x1 = _open_run(_FIN_DB, resumed=False, status="FINISHED", costs=(5.00,))
_x2 = _dl.start_run_record("batch", db_path=_FIN_DB, resumed=True,
                           fingerprint=_FP)
check("7g  a FINISHED campaign has nothing left to resume, so gluing a later "
      "invocation onto it would turn a re-run into a continuation",
      _dl.campaign_spend_before(_x2, db_path=_FIN_DB).runs, 0)
check("7g-i ...and that is the STATUS list deciding it, read from the one "
      "owner rather than retyped -- so STOPPED, which the spend gate writes, "
      "IS resumable",
      (_dl.RUN_STOP_REASON_SPEND_CAP is not None,
       "STOPPED" in _dl.CAMPAIGN_RESUMABLE_STATUSES,
       "FINISHED" in _dl.CAMPAIGN_RESUMABLE_STATUSES), (True, True, False))
check("7g-ii ...and queries.py reads that SAME tuple rather than a second copy",
      _queries.CAMPAIGN_RESUMABLE_STATUSES
      is _dl.CAMPAIGN_RESUMABLE_STATUSES, True)

# IT NEVER RAISES.
check("7h  a run id that is not in the table returns an EMPTY seed rather "
      "than raising -- a read-only bookkeeping query must not be able to stop "
      "a campaign",
      _dl.campaign_spend_before(999999, db_path=_RESUME_DB), _dl.CampaignSpend())
check("7h-i ...and so does None, which is what a caller with no run row has",
      _dl.campaign_spend_before(None, db_path=_RESUME_DB), _dl.CampaignSpend())
check("7h-ii ...and an unreadable database is COUNTED rather than silent",
      (_dl.campaign_spend_before(1, db_path=os.path.join(_TMP, "no", "x.db")),
       any(k.startswith("campaign_spend:")
           for k in _dl.RUN_RECORD_FAILURES)),
      (_dl.CampaignSpend(), True))

# THE END-TO-END PROPERTY: A RESUMED RUN CONTINUES UNDER THE REMAINING BUDGET.
_seed = _dl.campaign_spend_before(_r3, db_path=_RESUME_DB)
_resumed_result, _resumed_stub = run_node(
    _SIX, cap=_seed.usd + 2 * CALL_COST, parallel=1, seed_usd=_seed.usd)
check("7i  *** a resumed run gets the REMAINING budget, not a fresh cap: with "
      "$7.00 already spent and a $7.00-plus-two-calls cap it sends exactly "
      "two requests ***",
      len(_resumed_stub.requests), 2)
check("7i-i ...and without the seed the same cap would have bought the whole "
      "patient, which is the control that says the baseline is what stopped it",
      len(run_node(_SIX, cap=_seed.usd + 2 * CALL_COST, parallel=1)[1].requests),
      7)


# ===========================================================================
# SECTION 8 -- THE RUNNER: IN-FLIGHT PATIENTS FINISH, NOTHING NEW STARTS
# ===========================================================================

section("SECTION 8 -- run_batch honours the gate at the checkpoint's cadence")

from oncotriage.batch import runner as _runner                  # noqa: E402

_RUNNER_PATH = os.path.abspath(_runner.__file__)
_BASELINE_HASHES[_RUNNER_PATH] = hashlib.sha256(
    open(_RUNNER_PATH, "rb").read()).hexdigest()

_PATIENT_COST = 1.00
_N_PATIENTS = 40


def drive_run_batch(*, cap, workers, hold_until=None):
    """Drive the REAL run_batch with a stand-in patient. Returns a record.

    ``process_patient`` IS THE STAND-IN AND NOTHING ELSE IS. ``run_batch``,
    ``_on_done``, ``_start_patient_unless_stopped``, the submit loop, the sweep
    and the executor lifecycle are the shipped ones -- which is the point: what
    is under test is whether the runner stops STARTING patients, and a harness
    that replaced the loop could not say.

    THE GRAPH IS NEVER INVOKED and no billed call is reachable: the stand-in
    charges the ledger directly with a usage figure of its own, which is what a
    completed patient's Stage 5 would have charged.
    """
    started, finished = [], []
    lock = threading.Lock()
    gate = threading.Event()
    timed_out = threading.Event()

    def _process(fhir_path, graph, is_resample=False, run_id=None,
                 db_path=None):
        with lock:
            started.append(fhir_path)
            n = len(started)
        if hold_until is not None and n <= hold_until:
            # THE TIMEOUT IS A BACKSTOP AND NOTHING RESTS ON IT. The releaser
            # above frees these workers as soon as `hold_until` of them are
            # parked; check 8a-0 below asserts the release really happened, so a
            # scenario rescued by this timeout is a recorded FAILURE rather than
            # a slow pass.
            if not gate.wait(timeout=20):
                timed_out.set()
        # WHAT A PATIENT COSTS, charged the way Stage 5 charges it.
        spend.SPEND_LEDGER.charge(config.matching_wire_model(),
                                  500000, 0)     # 500k input tokens = $1.00
        with lock:
            finished.append(fhir_path)
        return {"patient_id": os.path.basename(fhir_path), "status": "success",
                "eligible_matches": 1, "near_misses": 0, "not_evaluable": 0,
                "total_time": 0.0, "timestamp": "2026-08-30T00:00:00",
                "error": None, "is_resample": is_resample}

    saved = (_runner.process_patient, _runner.save_checkpoint,
             _runner.flush_health, _runner.append_result,
             config.SPEND_CAP_USD, config.MAX_WORKERS)
    spend.SPEND_LEDGER.reset()
    spend.SPEND_STOP.reset()
    spend.SPEND_GATE_SKIPS.clear()
    _runner.STOP_SWITCH.reset()
    results = []
    try:
        _runner.process_patient = _process
        _runner.save_checkpoint = lambda *a, **k: None
        _runner.flush_health = lambda *a, **k: True
        _runner.append_result = lambda lst, entry: lst.append(entry)
        config.SPEND_CAP_USD = cap
        _runner.MAX_WORKERS = workers
        files = [os.path.join(_TMP, f"p{i:03d}.json")
                 for i in range(_N_PATIENTS)]
        completed = set()
        if hold_until is not None:
            # RELEASE WHEN THE POOL IS PROVABLY SATURATED, which is what makes
            # "N patients in flight" a measurement rather than a hope: every
            # worker of the pool is parked inside `process_patient`, so the
            # runner cannot have started anything else.
            #
            # THE FIRST VERSION OF THIS WAITED FOR THE LATCH TO TRIP AND COULD
            # NOT: the held workers are the only things that charge the ledger,
            # so nothing could cross the cap while they were held. It deadlocked
            # and was rescued by `gate.wait`'s own 20-SECOND TIMEOUT -- so the
            # scenario passed, took 20 seconds, and was measuring the timeout
            # rather than the gate. Measured, not reasoned about: removing the
            # timeout hung the file.
            def _release():
                waited = threading.Event()
                while not waited.wait(0.01):
                    with lock:
                        if len(started) >= hold_until:
                            break
                gate.set()
            releaser = threading.Thread(target=_release, daemon=True)
            releaser.start()
        out = drive(_runner.run_batch, fhir_files=files, bm25_index=None,
                    nct_ids=[], graph=None, completed_ids=completed,
                    results_list=results)
        gate.set()
        return types.SimpleNamespace(started=list(started),
                                     finished=list(finished),
                                     results=list(results), out=out,
                                     completed=completed,
                                     timed_out=timed_out.is_set())
    finally:
        gate.set()
        (_runner.process_patient, _runner.save_checkpoint,
         _runner.flush_health, _runner.append_result,
         config.SPEND_CAP_USD, _runner.MAX_WORKERS) = saved


# --- 8a: the cap stops the run and the checkpoint stays current -------------
_WORKERS = 4
_rec = drive_run_batch(cap=5.0, workers=_WORKERS, hold_until=_WORKERS)
check("8a-0 the held workers were RELEASED by the harness rather than by a "
      "timeout, so what follows measures the gate and not a backstop",
      _rec.timed_out, False)
check("8a  *** the run STOPS: it does not start all 40 patients ***",
      len(_rec.started) < _N_PATIENTS, True)
check("8a-i *** every patient it DID start finished and was written -- an "
      "in-flight patient is already paid for and its row is worth having ***",
      (len(_rec.started), len(_rec.finished)),
      (len(_rec.started), len(_rec.started)))
check("8a-ii ...and every one of them is in the results list, so nothing that "
      "ran was lost",
      len(_rec.results), len(_rec.started))
check("8a-iii ...and in the completed set, so the checkpoint a resume reads is "
      "current at the moment the stop is announced",
      len(_rec.completed), len(_rec.started))
check("8a-iv the latch tripped on the cap",
      (spend.SPEND_STOP.requested, spend.SPEND_STOP.limit),
      (True, spend.SPEND_LIMIT_CAP))
# MEASURED ON THIS HARNESS: 8 started for a $5 cap at $1 a patient with 4
# workers -- five the budget bought and three that were already in flight. BOTH
# BOUNDS ARE ASSERTED: the upper one is what the overshoot block promises, and
# the lower one is what says the budget was actually SPENT rather than the run
# having stopped for some other reason and flattered the upper bound.
check("8a-v  *** the overshoot is bounded by the workers in flight: at least "
      "the 5 patients the cap bought were started, and at most that many plus "
      "MAX_WORKERS -- which is config.SPEND_CAP_USD's stated bound, at the "
      "patient grain ***",
      (5 <= len(_rec.started) <= 5 + _WORKERS,
       round(spend.SPEND_LEDGER.total, 2)),
      (True, round(len(_rec.started) * _PATIENT_COST, 2)))
check("8a-vi ...and the pass reports itself INCOMPLETE, which is what makes "
      "main() record the run STOPPED rather than FINISHED",
      at(_rec.out, 1), False)

# --- 8b: under the cap nothing is stopped ----------------------------------
_full = drive_run_batch(cap=1000.0, workers=_WORKERS)
check("8b  under the cap every patient is started and finished, which is the "
      "control that says 8a is the gate firing rather than the harness",
      (len(_full.started), len(_full.finished), at(_full.out, 1)),
      (_N_PATIENTS, _N_PATIENTS, True))
check("8b-i ...and nothing was declined",
      (spend.SPEND_STOP.requested, dict(spend.SPEND_GATE_SKIPS)), (False, {}))

# --- 8c: the run record -----------------------------------------------------
_REC_DB = os.path.join(_TMP, "record.db")
_rid = _dl.start_run_record("batch", db_path=_REC_DB, fingerprint=_FP)


def _row(db, rid, column):
    conn = sqlite3.connect(db)
    try:
        got = conn.execute(f"SELECT {column} FROM runs WHERE id = ?",
                           (rid,)).fetchone()
    finally:
        conn.close()
    return got[0] if got else _Absent("no row")


check("8c  runs.stop_reason is NULL at open, so `stop_reason IS NULL` means "
      "'this run was not stopped' on every row of every era",
      _row(_REC_DB, _rid, "stop_reason"), None)
_dl.finalize_run_record(_rid, _dl.RUN_RECORD_STATUS_STOPPED, db_path=_REC_DB,
                        stop_reason=_dl.RUN_STOP_REASON_SPEND_CAP)
check("8c-i *** a spend-stopped run is STOPPED with a machine-readable "
      "reason: distinguishable from an operator stop and from a crash by ONE "
      "column, without parsing prose ***",
      (_row(_REC_DB, _rid, "status"), _row(_REC_DB, _rid, "stop_reason")),
      ("STOPPED", "spend_cap"))

_rid2 = _dl.start_run_record("batch", db_path=_REC_DB, fingerprint=_FP)
_dl.finalize_run_record(_rid2, _dl.RUN_RECORD_STATUS_STOPPED, db_path=_REC_DB,
                        stop_reason=_dl.RUN_STOP_REASON_OPERATOR)
check("8c-ii ...and an operator stop is the SAME status with a different "
      "reason, which is why this is a column and not two more statuses: every "
      "consumer that branches on status wants the identical answer for both",
      (_row(_REC_DB, _rid2, "status"), _row(_REC_DB, _rid2, "stop_reason")),
      ("STOPPED", "operator"))

_rid3 = _dl.start_run_record("batch", db_path=_REC_DB, fingerprint=_FP)
_dl.finalize_run_record(_rid3, _dl.RUN_RECORD_STATUS_FINISHED, db_path=_REC_DB)
check("8c-iii ...and a run nobody stopped carries NULL",
      (_row(_REC_DB, _rid3, "status"), _row(_REC_DB, _rid3, "stop_reason")),
      ("FINISHED", None))

_rid4 = _dl.start_run_record("batch", db_path=_REC_DB, fingerprint=_FP)
_dl.RUN_RECORD_FAILURES.clear()
_dl.finalize_run_record(_rid4, _dl.RUN_RECORD_STATUS_STOPPED, db_path=_REC_DB,
                        stop_reason="whatever-somebody-typed")
check("8c-iv *** an unrecognised reason is REFUSED and counted, never stored: "
      "the column exists to be grouped on, and a value outside the closed "
      "vocabulary is a bucket no GROUP BY consumer knows about ***",
      (_row(_REC_DB, _rid4, "stop_reason"),
       [k for k in _dl.RUN_RECORD_FAILURES
        if k.startswith("finalize:unknown_stop_reason:")]),
      (None, ["finalize:unknown_stop_reason:whatever-somebody-typed"]))
check("8c-v ...and the STATUS still landed, so a bookkeeping refusal does not "
      "cost the run its verdict",
      _row(_REC_DB, _rid4, "status"), "STOPPED")

# --- 8d: main()'s ONE derivation of the reason -----------------------------
_RUNNER_SRC = open(_RUNNER_PATH, encoding="utf-8").read()
_RUNNER_TREE = ast.parse(_RUNNER_SRC)
_MAIN = next((n for n in ast.walk(_RUNNER_TREE)
              if isinstance(n, ast.FunctionDef) and n.name == "main"), None)
_reason_assigns = [] if _MAIN is None else [
    n for n in ast.walk(_MAIN) if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "_stop_reason"
            for t in n.targets)]
check("8d  main() derives the stop reason exactly ONCE, so the column and the "
      "console block cannot disagree -- `_terminal_status`'s own argument, "
      "which that note records the cost of getting wrong",
      len(_reason_assigns), 1)
_reason_names = sorted({n.id for a in _reason_assigns for n in ast.walk(a)
                        if isinstance(n, ast.Name)
                        and n.id.startswith("RUN_STOP_REASON_")})
check("8d-i ...from the named constants rather than from literals: "
      "finalize_run_record REFUSES a value outside the vocabulary, so a typo "
      "would lose the reason silently at the one line whose job is to record it",
      _reason_names, ["RUN_STOP_REASON_CALL_CEILING",
                      "RUN_STOP_REASON_OPERATOR", "RUN_STOP_REASON_SPEND_CAP"])
_finalize_calls = [] if _MAIN is None else [
    n for n in ast.walk(_MAIN) if isinstance(n, ast.Call)
    and getattr(n.func, "id", None) == "finalize_run_record"]
_reason_reads = [c for c in _finalize_calls
                 if any(isinstance(k.value, ast.Name)
                        and k.value.id == "_stop_reason"
                        for k in c.keywords)]
check("8d-ii ...and the row is finalized with THAT local",
      len(_reason_reads), 1)
check("8d-iii the OPERATOR outranks the gate, so a run an operator asked to "
      "stop is never reported as one that ran out of money",
      ast.unparse(_reason_assigns[0]).index("RUN_STOP_REASON_OPERATOR")
      if _reason_assigns else -1,
      min(ast.unparse(_reason_assigns[0]).index(n)
          for n in _reason_names) if _reason_assigns else -1)


# ===========================================================================
# SECTION 9 -- NO BILLED PATH CAN BE ADDED OUTSIDE THE GATE
# ===========================================================================

section("SECTION 9 -- the billed call sites are enumerated and pinned")

# *** THE ENUMERATION IS DERIVED FROM SOURCE, NOT LISTED. ***
# Every call to either billed entry point, with the enclosing function, walked
# at every nesting depth -- which matters here more than usual, because two of
# the three sites are CLOSURES over the node's frame and a top-level walk would
# report the file as having one.
_BILLED_ENTRY_POINTS = ("call_matching_model", "call_matching_model_warmup")


def billed_sites(tree):
    """Every billed call site as ``(qualified enclosing name, entry point)``."""
    found = []

    def walk(node, stack):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, stack + [child.name])
                continue
            if isinstance(child, ast.Call):
                fn = child.func
                name = (fn.id if isinstance(fn, ast.Name)
                        else fn.attr if isinstance(fn, ast.Attribute) else None)
                if name in _BILLED_ENTRY_POINTS:
                    found.append(("::".join(stack), name))
            walk(child, stack)

    walk(tree, [])
    return sorted(found)


_EVAL_TREE = ast.parse(_EVAL_SRC)
check("9a  *** there are exactly THREE billed call sites in Stage 5, and they "
      "are these three -- a fourth added anywhere fails here rather than "
      "arriving as an ungated path ***",
      billed_sites(_EVAL_TREE),
      [("node_llm_classifier_evaluation", "call_matching_model_warmup"),
       ("node_llm_classifier_evaluation::_issue", "call_matching_model"),
       ("node_llm_classifier_evaluation::_obtain", "call_matching_model")])
check("9a-i ...and there are three of them, which is the same number as the "
      "phases both skip vocabularies declare",
      (len(billed_sites(_EVAL_TREE)),
       len(spend.SPEND_SKIP_KEY_PREFIXES)), (3, 3))


def _enclosing(tree, qualname):
    """The FunctionDef named by a `a::b` path."""
    node = tree
    for part in qualname.split("::"):
        node = next(n for n in ast.walk(node)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and n.name == part)
    return node


# EVERY ONE OF THEM IS BRACKETED. The gate is called in the same function the
# request goes out of, and so is the charge -- which is what bounds the
# overshoot at the requests in flight rather than at a patient's whole wave.
_bracketed = {}
for _qual, _entry in billed_sites(_EVAL_TREE):
    _fn = _enclosing(_EVAL_TREE, _qual)
    _calls = [n for n in ast.walk(_fn) if isinstance(n, ast.Call)]
    _names = {n.func.id for n in _calls if isinstance(n.func, ast.Name)}
    _bracketed[(_qual, _entry)] = ("_spend_gate" in _names,
                                   "_charge_spend" in _names)
check("9b  *** every billed call site calls the gate BEFORE and the ledger "
      "AFTER, in the same function the request goes out of ***",
      sorted((k, v) for k, v in _bracketed.items()),
      sorted((k, (True, True)) for k in _bracketed))

# THE GATE'S OWN PHASES COVER THE THREE SITES AND NOTHING ELSE.
_phase_args = sorted({
    ast.unparse(n.args[0]) for n in ast.walk(_EVAL_TREE)
    if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "_spend_gate"
    and n.args})
check("9c  ...and the phase each one declares is a member of the closed "
      "vocabulary, one per site, none reused",
      _phase_args,
      sorted(f"spend.{n}" for n in
             ("SPEND_SKIP_SEND_KEY_PREFIX", "SPEND_SKIP_WARMUP_KEY_PREFIX",
              "SPEND_SKIP_WAVE_KEY_PREFIX")))


# ── THE CONTROLS ────────────────────────────────────────────────────────────
#
# Every plant goes into an in-memory COPY of the module, never an edit to the
# file: this project's stated preference, and what keeps this file out of the
# collision matrix. `plant()` counts its needle FIRST and records a
# PLANT-FAILED result when the count is not 1 -- a revert reporting MISSED can
# mean the check is weak OR that the plant never applied, and those are not the
# same finding.

_CONTROLS_RUN = [0]


def plant(label, subs, probe, expected):
    _CONTROLS_RUN[0] += 1
    patched = _EVAL_SRC
    for old, new in subs:
        if patched.count(old) != 1:
            check(f"{label} [PLANT-FAILED: needle appears "
                  f"{patched.count(old)} times, expected 1]", 0, 1)
            return
        patched = patched.replace(old, new)
    module = drive(module_from, patched,
                   f"oncotriage.agent._spend_control_{_CONTROLS_RUN[0]}")
    if isinstance(module, _Absent):
        check(f"{label} [PLANT-FAILED: the copy would not import]",
              module, "an importable module")
        return
    check(label, drive(probe, module), expected)


def _requests_under_cap(module):
    """How many requests a capped patient sends through this module's node."""
    return len(run_node(_SIX, cap=1.0, seed_usd=1.0, parallel=1,
                        node=module.node_llm_classifier_evaluation)[1].requests)


# --- THE CLEAN CONTROL, FIRST ----------------------------------------------
# Without it a probe that always reported "0 requests" would report every plant
# as caught while measuring nothing.
plant("9d  CLEAN CONTROL: the UNPLANTED module sends nothing when the budget "
      "is already spent -- so the plants below are measured against a probe "
      "that can tell the difference",
      [("_CLEAN_CONTROL_NO_OP", "_CLEAN_CONTROL_NO_OP")]
      if "_CLEAN_CONTROL_NO_OP" in _EVAL_SRC else [], _requests_under_cap, 0)

# --- 9e: the wave's gate is removed ----------------------------------------
# DRIVEN ON A BUDGET THAT CROSSES **MID-WAVE**, NOT ONE ALREADY SPENT, AND THAT
# IS THE MEASUREMENT RATHER THAN A CONVENIENCE: with the budget spent before
# the patient starts, the WARMUP gate declines first and clears the queue, so a
# probe run there reports 0 requests for the planted module AND for the shipped
# one -- the plant would be masked by the gate one site over and reported as
# caught while measuring nothing. The first version of this control did exactly
# that.
def _requests_crossing_mid_wave_pre(module):
    return len(run_node(_SIX, cap=3 * CALL_COST, parallel=1,
                        node=module.node_llm_classifier_evaluation)[1].requests)


plant("9e  *** a BYPASS at the wave's call site is CAUGHT [9b/4c]: the queued "
      "trial calls go out after the budget has been crossed ***",
      [("            _refusal = _spend_gate(spend.SPEND_SKIP_WAVE_KEY_PREFIX,\n"
        "                                   _call_counter, where=\"a Stage 5 wave\",\n"
        "                                   count=len(chunk_))\n"
        "            if _refusal is not None:\n"
        "                return (\"error\", _refusal)\n",
        "")],
      _requests_crossing_mid_wave_pre, 7)
check("9e-i CLEAN CONTROL for 9e: the unplanted module stops at three, so the "
      "plant is measured against a probe that can tell the difference",
      _requests_crossing_mid_wave_pre(_evaluation), 3)

# --- 9f: the warmup's gate is removed --------------------------------------
plant("9f  *** a BYPASS at the WARMUP is CAUGHT: the patient sends the one "
      "request that costs the most input tokens of any in the wave ***",
      [("            elif _spend_gate(spend.SPEND_SKIP_WARMUP_KEY_PREFIX,\n"
        "                             _call_counter,\n"
        "                             where=\"a Stage 5 cache warmup\") is not None:",
        "            elif False:")],
      _requests_under_cap, 1)

# --- 9g: the send loop's gate is removed (the GROUPED arm's only gate) ------
def _grouped_requests_under_cap(module):
    return len(run_node(_SIX, cap=1.0, seed_usd=1.0, per_trial=False,
                        node=module.node_llm_classifier_evaluation)[1].requests)


plant("9g  *** a BYPASS at the SEND LOOP is CAUGHT, which is the RETAINED "
      "GROUPED arm's only gate -- the one a per-trial-only fix would have left "
      "open ***",
      [("        _refusal = _spend_gate(spend.SPEND_SKIP_SEND_KEY_PREFIX, "
        "_call_counter,\n"
        "                               where=\"a Stage 5 send loop\", "
        "count=len(chunk))\n"
        "        if _refusal is not None:\n"
        "            raise _refusal\n",
        "")],
      _grouped_requests_under_cap, 1)

# --- 9h: the LEDGER stops being charged ------------------------------------
# A gate reading a ledger nothing charges never fires, which is the silent half
# of a bypass: every request passes because the total never moves.
def _requests_crossing_mid_wave(module):
    return len(run_node(_SIX, cap=3 * CALL_COST, parallel=1,
                        node=module.node_llm_classifier_evaluation)[1].requests)


plant("9h  *** a wave call that is ISSUED and never CHARGED is CAUGHT: the "
      "ledger stops moving, so the gate never fires and the whole patient goes "
      "out under a three-call budget ***",
      [("            _charge_spend(_response)\n            return (\"ok\", _response)",
        "            return (\"ok\", _response)")],
      _requests_crossing_mid_wave, 7)
check("9h-i CLEAN CONTROL for 9h: the unplanted module stops at three",
      _requests_crossing_mid_wave(_evaluation), 3)

# --- 9i: the exception stops being a shutdown ------------------------------
# `Stage5SpendStopped` subclasses `Stage5ShutdownRequested` precisely so the
# send loop's non-isolation branch covers it with no edit. Break the
# inheritance and the refusal is isolated to its trial: the patient COMPLETES
# with the un-issued trials marked not evaluable, `_on_done` checkpoints it, and
# a resume skips it forever.
def _completes_with_holes(module):
    result, _ = run_node(_SIX, cap=3 * CALL_COST, parallel=1,
                         node=module.node_llm_classifier_evaluation)
    return (bool(at(result, "error")), len(at(result, "evaluations") or []))


plant("9i  *** breaking the exception's inheritance is CAUGHT: the patient "
      "COMPLETES with holes instead of failing, and a completed patient is "
      "checkpointed and skipped forever ***",
      [("class Stage5SpendStopped(Stage5ShutdownRequested):",
        "class Stage5SpendStopped(RuntimeError):")],
      _completes_with_holes, (False, 6))
check("9i-i CLEAN CONTROL for 9i: the unplanted module FAILS the patient",
      _completes_with_holes(_evaluation), (True, 0))
check("9i-ii ...and the inheritance really is what does it",
      issubclass(_evaluation.Stage5SpendStopped,
                 _evaluation.Stage5ShutdownRequested), True)


# ===========================================================================
# SECTION 10 -- HYGIENE
# ===========================================================================

section("SECTION 10 -- nothing outside this file's scratch tree was touched")

check("10a every repository file this run reads is byte-identical to its "
      "pre-run state -- every plant went into an in-memory copy",
      {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
       for p in _BASELINE_HASHES}, _BASELINE_HASHES)
check("10a-i non-degeneracy: the baseline hashes are distinct, so 10a is not "
      "comparing one file with itself",
      len(set(_BASELINE_HASHES.values())), len(_BASELINE_HASHES))

check("10b every dependency override this file installed was cleared",
      deps.peek(deps.OPENAI_CLIENT), deps.UNSET)

check("10c the config constants this file writes are back where they started",
      (config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED,
       config.SPEND_CALL_CEILING_ENFORCED,
       config.MATCHING_PER_TRIAL_CALLS_ENABLED,
       config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS), _START_CONFIG)
check("10c-i non-degeneracy: the captured start values are the real ones, not "
      "a pair this file could have satisfied by writing nothing",
      (_START_CONFIG[0] == 300.00, _START_CONFIG[1] is True), (True, True))

check("10d NO MODEL WAS LOADED: torch and transformers never entered "
      "sys.modules, which is what says this file costs a second rather than a "
      "download",
      sorted(m for m in ("torch", "transformers") if m in sys.modules), [])

# THE PRODUCTION DATABASE WAS NEVER RESOLVED, LET ALONE OPENED.
check("10e paths._RESOLVED was seeded so nothing could reach the production "
      "tree, and the seeded path was never created",
      (_paths._RESOLVED["inferences_path"].startswith(_TMP),
       os.path.exists(_paths._RESOLVED["inferences_path"])), (True, False))

_paths._RESOLVED.clear()
_paths._RESOLVED.update(_SAVED_RESOLVED)
check("10e-i ...and it was restored", _paths._RESOLVED, _SAVED_RESOLVED)

spend.SPEND_LEDGER.reset()
spend.SPEND_STOP.reset()
spend.SPEND_GATE_SKIPS.clear()
spend.SPEND_CEILING_TRIPS.clear()
spend.SPEND_LEDGER_FAULTS.clear()
check("10f the process-global ledger and latch are left clean, so a runner "
      "importing this module afterwards does not inherit a spend it did not "
      "make",
      (spend.SPEND_LEDGER.total, spend.SPEND_STOP.requested), (0.0, False))

check("10g every plant was applied to an in-memory copy and the controls all "
      "ran (non-degeneracy: a section whose plants silently did not apply "
      "would report this as zero)",
      _CONTROLS_RUN[0] >= 5, True)

# THE CLOSING REPORT IS DRIVEN, because its one production caller is inside a
# main() that cannot be run without spending money.
_lines = []
spend.print_report(out=_lines.append)
check("10h the closing spend block ALWAYS prints, even for a run that spent "
      "nothing -- silence there would be indistinguishable from a ledger that "
      "was never wired up",
      (_lines[0] if _lines else _Absent("no lines"),
       any("all budgets" in ln for ln in _lines)), ("SPEND", True))

# THE LINE THIS CHECK NAMES USED TO READ "campaign total" AND THE RENAME IS THE
# FINDING RATHER THAN COSMETIC. That figure is the WHOLE ledger -- every budget
# -- and once budgets are plural it is a number no cap is compared against, so
# a label calling it "campaign" put it beside a campaign cap it can exceed
# without the campaign having spent a cent of it.
check("10h-0 ...and the whole-ledger figure SAYS no cap is compared against "
      "it, so it cannot be read as a budget",
      any("all budgets" in ln and "no cap is compared" in ln
          for ln in _lines), True)
check("10h-0-i *** every budget prints its own spent/cap/remaining group on "
      "every run, INCLUDING one this run never touched -- a budget printed "
      "only when it spent would make its silence read as coverage ***",
      sorted(b for b in spend.SPEND_BUDGETS
             if any(ln.startswith(f"  cap {b} ") for ln in _lines)),
      sorted(spend.SPEND_BUDGETS))


def _cap_lines(cap, enforced=True, budget=spend.SPEND_BUDGET_CAMPAIGN):
    saved = (config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED)
    out = []
    try:
        config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED = cap, enforced
        spend.print_report(out=out.append)
    finally:
        config.SPEND_CAP_USD, config.SPEND_CAP_ENFORCED = saved
    return [ln for ln in out if ln.startswith(f"  cap {budget} ")]


def _rater_cap_lines(cap, enforced=True):
    saved = (config.RATER_SPEND_CAP_USD, config.SPEND_CAP_ENFORCED)
    out = []
    try:
        config.RATER_SPEND_CAP_USD, config.SPEND_CAP_ENFORCED = cap, enforced
        spend.print_report(out=out.append)
    finally:
        config.RATER_SPEND_CAP_USD, config.SPEND_CAP_ENFORCED = saved
    return [ln for ln in out if ln.startswith("  cap rater ")]


# THREE STATES, NOT TWO. An unreadable cap is not an absent one, and the first
# version of report_lines() printed BOTH lines for it -- two claims about one
# value in one block. THE BUDGET SPLIT HAD TO KEEP THAT PROPERTY PER BUDGET,
# which is what the second half of this pair measures: the identical three
# states of the RATER cap, reached by writing the OTHER constant.
def _one(lines):
    """The single line, or a named absence. NEVER raises.

    A bare ``[0]`` raises ``IndexError`` on exactly the defect these checks
    exist to catch -- a report that stopped printing a budget's cap -- so the
    run would print one traceback where it owed a summary. MEASURED: the revert
    that makes `report_lines()` print one budget instead of every one ABORTED
    this file before this helper existed.
    """
    return lines[0] if lines else _Absent("no cap line")


check("10h-i a readable cap, an absent cap and an UNREADABLE cap each print "
      "exactly ONE campaign cap line, and the three lines are different",
      ([len(_cap_lines(300.0)), len(_cap_lines(None)), len(_cap_lines(-1.0))],
       # str() BECAUSE `_Absent` DEFINES `__eq__` AND IS THEREFORE
       # UNHASHABLE. A set of them raises TypeError -- on exactly the defect
       # `_one` was added to survive, which is the abort shape one level in.
       # MEASURED: the report-prints-one-budget revert aborted here.
       len({str(_one(_cap_lines(300.0))), str(_one(_cap_lines(None))),
            str(_one(_cap_lines(-1.0)))})),
      ([1, 1, 1], 3))
check("10h-ii ...and the same three states of config.RATER_SPEND_CAP_USD "
      "print exactly ONE rater cap line each, and differ",
      ([len(_rater_cap_lines(50.0)), len(_rater_cap_lines(None)),
        len(_rater_cap_lines(-1.0))],
       len({str(_one(_rater_cap_lines(50.0))),
            str(_one(_rater_cap_lines(None))),
            str(_one(_rater_cap_lines(-1.0)))})),
      ([1, 1, 1], 3))
check("10h-iii *** ...and writing ONE constant moves ONE budget's line: an "
      "unreadable campaign cap leaves the rater line readable, which is the "
      "whole content of 'per billed program' in the report ***",
      ("UNREADABLE" in str(_one(_cap_lines(-1.0))),
       "UNREADABLE" in str(_one(_rater_cap_lines(50.0, True))),
       "$50.00" in str(_one(_cap_lines(-1.0,
                                       budget=spend.SPEND_BUDGET_RATER)))),
      (True, False, True))
check("10h-iv ...and config.RATER_SPEND_CAP_USD was restored by every probe "
      "above",
      config.RATER_SPEND_CAP_USD, _START_RATER_CAP)
check("10h-ii ...and the measured-only mode says so on the cap line rather "
      "than looking like a working brake",
      ("MEASURED ONLY" in _cap_lines(300.0, enforced=False)[0],
       "MEASURED ONLY" in _cap_lines(300.0, enforced=True)[0]), (True, False))

shutil.rmtree(_TMP, ignore_errors=True)
check("10i the scratch directory every database went into is gone",
      os.path.exists(_TMP), False)


# ===========================================================================
print("\n" + "=" * 78)
print("SUMMARY")
print("=" * 78)
print(f"Passed: {_RESULTS['passed']}")
print(f"Failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFailures:")
    for _label, _expected, _actual in _FAILURES:
        print(f"  - {_label}\n          expected: {_expected!r}"
              f"\n          actual:   {_actual!r}")
print("=" * 78)

sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 31 2026

@author: ramyalsaffar
"""
