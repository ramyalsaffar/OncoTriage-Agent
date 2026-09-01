######################################################################
# Per-trial Stage 5 on Bedrock Converse: cache-or-nothing, enforced
######################################################################

"""Bedrock Converse (Anthropic Claude) Per-Trial Test

``oncotriage/agent/bedrock_anthropic_adapter.py`` gained a cache WARMUP and
``oncotriage/agent/evaluation.py`` gained the reading that makes per-trial
mode's central rule CHECKABLE on that provider. This file holds the pair to
five claims:

  1. WITH THE FLAG OFF NOTHING MOVED. Section 1: the OpenAI request is
     byte-identical to the one ``git show HEAD:`` builds, the parallel bound
     resolves to the shared constant, and the routing hint is still issued.

  2. THE WARMUP AND A TRIAL CALL SHARE A BYTE-IDENTICAL PREFIX, AND THE
     BREAKPOINT IS AT THE END OF IT. Section 2 pins both requests field by
     field, including the ``cachePoint``'s POSITION -- last element of
     ``system``, after the text, nowhere else -- because that placement is what
     ``prompt-caching.html`` asks for and a checkpoint inside ``messages``
     would cache the per-trial text at a premium rate with no read to follow.

  3. CACHE-OR-NOTHING IS ENFORCED, NOT ASSUMED. Sections 3, 4 and 5: the write
     is read out of the provider's own usage block, a writer that cached
     nothing issues ZERO trial calls, and the patient fails in the shape a
     resume can pick up. Both writers are covered -- the dedicated warmup AND
     the fallback schedule's held-back first trial call.

  4. A WAVE CALL THAT DID NOT READ THE CACHE SURFACES. Section 6: counted,
     logged, and its verdict KEPT -- a broken cost premise is not a broken
     judgement, and discarding a paid answer would spend the money twice.

  5. THE TOKEN ACCOUNTING HOLDS ACROSS A WARMUP PLUS N READS. Section 7 drives
     the real node end to end and requires the patient's stored input total to
     equal the sum of what the provider reported for every request, with the
     disjointness formula applied per call -- because the whole claim of this
     design is a cost claim, and a total that under-reports cached input would
     misstate it.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO CORPUS, NO GIT HISTORY, NO
DATABASE -- and NO boto3. The Converse client is a stand-in installed through
``oncotriage/agent/deps.py``; every response is a literal dict; the request
builder and the response translator import no AWS library at all, which is what
makes this file runnable on a machine where boto3 is not installed. That is not
a hypothetical: it is the machine this pass was written on.
``ONCOTRIAGE_DEFER_LOCAL_MODELS`` is set ABOVE the package imports (pass
20c-3d's ordering lesson).

IT READS THE LIVE PROMPT RENDERER, and section 8 is the one place it does.
Bedrock's explicit cache has a 1,024-token minimum below which a checkpoint is
SILENTLY not created -- inference succeeds, nothing is cached, and every wave
call pays the full input rate. This pass's answer to "what does that mean for a
short patient record" is a MEASUREMENT (the instructions alone are ~5,285
tokens, five times the floor), and a measurement taken once is a measurement
that rots. Section 8 re-derives it from ``render_system_prompt`` so a prompt
shortened past the floor fails HERE rather than as a campaign that quietly
stopped caching.

IT DOES EXEC -- in-memory copies of ``oncotriage/agent/evaluation.py`` and
``oncotriage/agent/bedrock_anthropic_adapter.py``, one per plant, to be argued
at ``_EXEC_ALLOWLIST`` in ``tests/test_package_invariants.py``. A ``git show``
control is impossible for all but one of them: the mechanisms are new at HEAD
and each plant is a one-token edit INSIDE a function body to code that exists
nowhere else. The copies are exec'd into a real ``ModuleType`` because a
function's globals ARE the dict it was exec'd into. Both shipped files are
sha256'd before the first plant and compared at the end.

NOT in ``tests/run_serial_tests.py``'s collision matrix, derived rather than
assumed: it writes nothing anywhere, not even a temp directory, and the three
repository files it READS are ``oncotriage/agent/evaluation.py``,
``oncotriage/agent/bedrock_anthropic_adapter.py`` and ``oncotriage/config.py``.
The last IS rewritten in place by ``tests/test_config_snapshot_date_rot.py``,
so all three are sha256-compared at the end and an interleaved serial run is
visible rather than silent.

EVERY CONFIG MUTATION IS INSIDE try/finally AND THE RESTORE IS ASSERTED.
Section 10 re-reads every knob this file touches and derives the list from
config itself rather than retyping it.

EVERY DRIVER RETURNS A VALUE RATHER THAN RAISING. ``drive`` and ``_Absent``
exist because a bare call inside ``check(...)`` raises while the ARGUMENT is
being evaluated, which aborts the file with no summary -- on exactly the plants
this file exists to catch. This project has shipped that shape fourteen times.
"""

import contextlib
import hashlib
import io
import json
import os
import re
import sys
import threading
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ABOVE THE PACKAGE IMPORTS. `oncotriage.agent.deps` reads this once, at its own
# import, and that import arrives transitively on the first `oncotriage` import.
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

from oncotriage import config                                    # noqa: E402
from oncotriage.agent import deps                                # noqa: E402
from oncotriage.agent import evaluation as _evaluation           # noqa: E402
from oncotriage.agent import bedrock_anthropic_adapter as bac    # noqa: E402
from oncotriage.agent import prompts as _prompts                 # noqa: E402
from oncotriage.agent.evaluation import (                        # noqa: E402
    node_llm_classifier_evaluation)


# ===========================================================================
# HARNESS
# ===========================================================================

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


def check_true(label, condition):
    check(label, bool(condition), True)


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


class _Absent:
    """A named absence that FAILS a check instead of aborting the file."""

    __slots__ = ("why",)

    def __init__(self, why):
        self.why = why

    def __bool__(self):
        return False

    def __eq__(self, other):
        return isinstance(other, _Absent) and self.why == other.why

    def __repr__(self):
        return f"<absent: {self.why}>"


class Raised:
    """What a driver returns instead of letting an exception escape."""

    __slots__ = ("kind", "message")

    def __init__(self, exc):
        self.kind = type(exc).__name__
        self.message = str(exc)

    def __eq__(self, other):
        return isinstance(other, Raised) and self.kind == other.kind

    def __repr__(self):
        return f"<raised {self.kind}: {self.message[:110]}>"


def drive(fn, *args, **kwargs):
    """Call fn with both output channels captured; return its value or Raised.

    OUTPUT IS CAPTURED BECAUSE THE NODE AND THE ADAPTER BOTH LOG, several
    scenarios hit WARNING and ERROR paths on purpose, and a test whose PASS
    lines are interleaved with JSON records is a test nobody reads. Nothing
    suppressed is asserted on: every assertion reads a returned value, a
    counter, or a recorded request.
    """
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            return fn(*args, **kwargs)
    except Exception as exc:                                     # noqa: BLE001
        return Raised(exc)


def at(mapping, *keys, default=None):
    """A chained lookup that cannot raise; a missing key is a named absence."""
    node = mapping
    for key in keys:
        try:
            node = node[key]
        except Exception:                                        # noqa: BLE001
            return _Absent(f"{'.'.join(str(k) for k in keys)} missing")
    return default if node is None and default is not None else node


def sub(text, old, new, expect):
    """Replace, refusing a plant that did not match exactly `expect` times."""
    seen = text.count(old)
    if seen != expect:
        raise AssertionError(
            f"plant matched {seen} time(s), expected {expect}: {old[:80]!r}")
    return text.replace(old, new)


_PLANT_SEQ = [0]
_EVAL_PATH = os.path.abspath(_evaluation.__file__)
_ADAPTER_PATH = os.path.abspath(bac.__file__)
_CONFIG_PATH = os.path.abspath(config.__file__)
# READ AS TEXT AND PARSED, NEVER IMPORTED. Section 4e reads the batch runner's
# own checkpoint predicate out of its source so that "a resume can pick this
# patient up" is a MEASUREMENT rather than a sentence -- and importing the
# runner would drag its pool, its ledger and its tracking module into a file
# whose whole claim is that it loads no model and opens no client. The path is
# derived from the module's own __file__ through the package, so a future move
# cannot leave this pointing at a same-named copy.
_RUNNER_PATH = os.path.join(os.path.dirname(os.path.dirname(_EVAL_PATH)),
                            "batch", "runner.py")
_SHA_BEFORE = {p: hashlib.sha256(open(p, "rb").read()).hexdigest()
               for p in (_EVAL_PATH, _ADAPTER_PATH, _CONFIG_PATH,
                         _RUNNER_PATH)}


def exec_copy(path, mutate, package="oncotriage.agent"):
    """Exec a MUTATED in-memory copy of a module into a real ModuleType."""
    _PLANT_SEQ[0] += 1
    text = open(path, encoding="utf-8").read()
    planted = mutate(text)
    if planted == text:
        raise AssertionError("the plant matched nothing")
    name = f"_planted_{os.path.basename(path)[:-3]}_{_PLANT_SEQ[0]}"
    module = types.ModuleType(name)
    module.__file__ = path
    module.__package__ = package
    sys.modules[name] = module
    exec(compile(planted, path, "exec"), module.__dict__)
    return module


# THE SHIPPED VALUES, CAPTURED AT IMPORT, BEFORE ANY `settings()` BLOCK.
# DERIVED rather than retyped: every BEDROCK_ANTHROPIC_ and
# MATCHING_PER_TRIAL_ name, plus MATCHING_PROVIDER. A knob added tomorrow joins
# the leak check for free, which a hand-written list cannot do.
_KNOB_NAMES = tuple(sorted(
    n for n in vars(config)
    if n.startswith(("BEDROCK_ANTHROPIC_", "MATCHING_PER_TRIAL_"))))
_SHIPPED_AT_IMPORT = {n: getattr(config, n)
                      for n in ("MATCHING_PROVIDER",) + _KNOB_NAMES}


# ===========================================================================
# STAND-INS
# ===========================================================================
#
# ONE CONVERSE STUB SERVES THE WARMUP AND THE WAVE, because the shipped code
# reaches BOTH through `deps.get_bedrock_anthropic_client().converse(**kwargs)`.
# That is not a convenience: it is what lets this file drive the REAL adapter
# and the REAL node end to end -- the request builder, the response translator,
# the dispatch, the confirmation and the accounting -- with no network call and
# no AWS library installed.

PATIENT = {
    "patient_id": "converse-per-trial-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def make_trial(nct_id):
    """A trial object in the shape ``_build_trials_text`` reads."""
    return {
        "trial": {
            "nct_id": nct_id, "title": f"Study {nct_id}", "phase": "PHASE2",
            "eligibility": {
                "inclusion_criteria": "Inclusion Criteria:\n- Age 18+",
                "exclusion_criteria": "Exclusion Criteria:\n- Pregnancy",
            },
        }
    }


TRIALS = [make_trial(f"NCT0000000{i}") for i in range(1, 5)]


def ids_in(kwargs):
    """The nct_ids fenced into one Converse request's user message."""
    return re.findall(
        r"<<<TRIAL_DATA nct_id=(\S+) ",
        kwargs["messages"][0]["content"][0]["text"])


def is_warmup(kwargs):
    """Is this recorded Converse request the cache warmup?

    RECOGNISED BY ITS USER MESSAGE, which the adapter reads from
    ``config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE`` -- never by "the first
    request" or "a request with no trial ids in it". Both of those are
    properties of the SCHEDULE, and the schedule is what is under test: a defect
    that stopped sending the warmup would make request 0 a trial call, and a
    test that DEFINED request 0 as the warmup could not see it.
    """
    return (kwargs["messages"][0]["content"][0]["text"]
            == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE)


def eligible_body(nct_ids):
    return json.dumps({"evaluations": [
        {"assessment": "No known disqualifiers.", "eligible": "eligible",
         "inclusion_criteria": [{"criterion": "Age 18+",
                                 "patient_value": "61", "status": "met"}],
         "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
        for i in nct_ids]})


def converse_reply(text, *, read=None, write=None, input_tokens=1000,
                   output_tokens=100, stop_reason="end_turn"):
    """A literal Converse reply, with the two cache counts controllable.

    ``read`` / ``write`` of None OMIT the field entirely, which is the
    `not_reported` case and is a different fixture from a reported zero -- the
    distinction the whole confirmation vocabulary rests on, so the fixture
    builder has to be able to express both.
    """
    usage = {"inputTokens": input_tokens, "outputTokens": output_tokens,
             "totalTokens": input_tokens + output_tokens}
    if read is not None:
        usage["cacheReadInputTokens"] = read
    if write is not None:
        usage["cacheWriteInputTokens"] = write
    return {
        "ResponseMetadata": {"RequestId": "req-stub"},
        "output": {"message": {"role": "assistant",
                               "content": [{"text": text}]}},
        "stopReason": stop_reason,
        "usage": usage,
    }


class ConverseStub:
    """`client.converse(...)` and nothing else.

    IT DELIBERATELY HAS NEITHER ``chat`` NOR ``responses``. A stand-in that
    answered every surface could not tell the three dispatch branches apart,
    and section 1's whole subject is that the OpenAI branch is untouched.
    """

    def __init__(self, *, warmup_read=None, warmup_write=19000,
                 trial_read=19000, trial_write=0, warmup_raise=None,
                 fail_for=(), barrier_size=None, barrier_timeout=15.0,
                 read_for=None):
        self.requests = []
        self.warmup_read = warmup_read
        self.warmup_write = warmup_write
        self.trial_read = trial_read
        self.trial_write = trial_write
        self.warmup_raise = warmup_raise
        self.fail_for = set(fail_for)
        # A PER-TRIAL cache-read override, so one wave can carry a hit and a
        # miss at once -- which is what separates "the cache did not warm at
        # all" from "one call of the wave missed".
        self.read_for = dict(read_for or {})
        self._lock = threading.Lock()
        self.in_flight = 0
        self.max_in_flight = 0
        self._barrier = (threading.Barrier(barrier_size)
                         if barrier_size else None)
        self.barrier_timeout = barrier_timeout
        self.barrier_broken = False

    def converse(self, **kwargs):
        with self._lock:
            self.requests.append(kwargs)
        warmup = is_warmup(kwargs)
        if warmup:
            # ANSWERED ABOVE THE BARRIER, and it has to be: the node awaits it
            # alone, so a warmup that joined a barrier sized for the wave would
            # deadlock every scenario rather than measure one.
            if self.warmup_raise is not None:
                raise self.warmup_raise
            return converse_reply("", read=self.warmup_read,
                                  write=self.warmup_write, input_tokens=1200,
                                  output_tokens=1, stop_reason="max_tokens")
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            if self._barrier is not None:
                try:
                    self._barrier.wait(timeout=self.barrier_timeout)
                except threading.BrokenBarrierError:
                    self.barrier_broken = True
            ids = ids_in(kwargs)
            if self.fail_for and set(ids) & self.fail_for:
                raise RuntimeError(f"stub failure for {sorted(set(ids))}")
            _read = self.trial_read
            for _i in ids:
                if _i in self.read_for:
                    _read = self.read_for[_i]
                    break
            return converse_reply(eligible_body(ids), read=_read,
                                  write=self.trial_write)
        finally:
            with self._lock:
                self.in_flight -= 1

    # -- readings the assertions use -------------------------------------
    def warmup_requests(self):
        return [r for r in self.requests if is_warmup(r)]

    def wave_requests(self):
        return [r for r in self.requests if not is_warmup(r)]

    def wave_ids(self):
        return [ids_in(r) for r in self.wave_requests()]


class ClientError(Exception):
    """A botocore ``ClientError`` as botocore presents one.

    BUILT HERE RATHER THAN IMPORTED, because boto3 is not installed on this
    machine and because ``classify_error`` and ``_http_status_of`` are
    documented to key on the SHAPE of an exception rather than on its class --
    a control that used the real class would leave that claim untested.
    """

    def __init__(self, code, message, status=400):
        super().__init__(f"An error occurred ({code}) when calling the "
                         f"Converse operation: {message}")
        self.response = {"Error": {"Code": code, "Message": message},
                         "ResponseMetadata": {"HTTPStatusCode": status}}


@contextlib.contextmanager
def settings(**knobs):
    """Set any config knob for one block, then restore. Restore is asserted
    in section 10 against the values captured at import."""
    saved = {k: getattr(config, k) for k in knobs}
    for key, value in knobs.items():
        setattr(config, key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(config, key, value)


@contextlib.contextmanager
def counters_zeroed():
    """Clear the two counters this file reads, and restore them after.

    THEY ARE PROCESS-GLOBAL AND SEVERAL SECTIONS BUMP THEM ON PURPOSE, so a
    section asserting "this bumped exactly once" has to start from a known state
    or it is asserting about every section above it as well.
    """
    saved = (dict(_evaluation.PER_TRIAL_WARMUP_DEGRADATIONS),
             dict(_evaluation.PER_TRIAL_CACHE_READ_MISSES))
    _evaluation.PER_TRIAL_WARMUP_DEGRADATIONS.clear()
    _evaluation.PER_TRIAL_CACHE_READ_MISSES.clear()
    try:
        yield (_evaluation.PER_TRIAL_WARMUP_DEGRADATIONS,
               _evaluation.PER_TRIAL_CACHE_READ_MISSES)
    finally:
        _evaluation.PER_TRIAL_WARMUP_DEGRADATIONS.clear()
        _evaluation.PER_TRIAL_WARMUP_DEGRADATIONS.update(saved[0])
        _evaluation.PER_TRIAL_CACHE_READ_MISSES.clear()
        _evaluation.PER_TRIAL_CACHE_READ_MISSES.update(saved[1])


def run_node(trials, *, stub=None, node=None, per_trial=True, parallel=None,
             **stub_kwargs):
    """Drive the REAL Stage 5 node against a Converse stand-in.

    THE PROVIDER IS SET ON ``config``, NOT ON THE NODE'S GLOBALS, which is the
    seam the production code chose: every consumer reads
    ``config.MATCHING_PROVIDER`` and ``config.matching_call_mode()`` live,
    precisely so the column ``oncotriage/storage/database_logger.py`` writes
    cannot disagree with the branch that ran.
    """
    node = node or node_llm_classifier_evaluation
    stub = stub if stub is not None else ConverseStub(**stub_kwargs)
    knobs = {"MATCHING_PROVIDER": config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
             "MATCHING_PER_TRIAL_CALLS_ENABLED": per_trial}
    if parallel is not None:
        knobs["BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS"] = parallel
    saved = deps.set_overrides({deps.BEDROCK_ANTHROPIC_CLIENT: stub})
    try:
        with settings(**knobs):
            state = {
                "patient_data": PATIENT,
                "filtered_trials": trials,
                "llm_classifier_retries": 0,
                "mesh_filter_applied": True,
                "mesh_filter_skip_reason": "applied",
                "stage_timings": {},
            }
            return drive(node, state), stub
    finally:
        deps.restore_overrides(saved)


# ===========================================================================
# SECTION 1 — THE FLAG IS OFF AND NOTHING MOVED
# ===========================================================================

section("1. Flag OFF: the shipped OpenAI path is untouched")

check("the shipped provider is still OpenAI",
      config.MATCHING_PROVIDER, config.MATCHING_PROVIDER_OPENAI)
check("...and per-trial is still the shipped call mode",
      config.matching_call_mode(), config.MATCHING_CALL_MODE_PER_TRIAL)

# --- 1a. The parallel bound resolves to the shared constant -----------------
check("with the OpenAI provider selected the bound IS the shared constant",
      config.per_trial_parallel_bound(),
      config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)
with settings(BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS=1):
    check("...and a Converse override does NOT reach an OpenAI run, which is "
          "the whole reason it is a separate constant",
          config.per_trial_parallel_bound(),
          config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)

# --- 1b. The routing hint is still issued on OpenAI ------------------------
check("the OpenAI routing hint is unchanged",
      _evaluation.per_trial_prompt_cache_key("deadbeef"),
      "oncotriage-stage5-deadbeef")
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    check("...and is None on Converse, which has no such request field, so "
          "the caller never has one to forward",
          _evaluation.per_trial_prompt_cache_key("deadbeef"), None)

# --- 1c. The OpenAI warmup request is byte-identical -----------------------
#
# DRIVEN THROUGH THE REAL FUNCTION rather than read out of the source: the
# claim is about the kwargs the SDK is handed, and only a call can produce
# them.
class _OpenAIRecorder:
    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return converse_reply("")     # never read; the warmup is not parsed


_rec = _OpenAIRecorder()
_saved = deps.set_overrides({deps.OPENAI_CLIENT: _rec})
try:
    # THE LABEL IS SHORT ON PURPOSE AND IT IS NOT A CREDENTIAL. This kwarg is
    # the prompt-cache ROUTING HINT: it asks the provider to send requests
    # sharing a prefix to one machine, and production derives its value from
    # the system prompt's digest behind a project prefix. Nothing authenticates
    # with it, and no check below reads the value -- only that the kwarg was
    # forwarded at all, which is what the kwargs comparison asserts.
    #
    # MEASURED, NOT PREFERRED. Written inline as a longer descriptive string it
    # trips gitleaks' generic-api-key rule, which fires on a value of ten or
    # more characters with enough entropy when a credential word precedes the
    # assignment. That is a false positive on a routing label, and it FAILED
    # THE SECRET GATE: that gate reads every blob in the object database and
    # has no way to know a fabricated value is fabricated. A value under ten
    # characters cannot reach the rule's length floor at all, which is a
    # sturdier answer than sitting a fraction under an entropy threshold. The
    # sibling per-trial suite already passes a short label here for the reason
    # this comment now records.
    drive(_evaluation.call_matching_model_warmup, "SYSTEM",
          prompt_cache_key="probe")
finally:
    deps.restore_overrides(_saved)

_kw = _rec.calls[0] if _rec.calls else _Absent("the OpenAI warmup issued no call")
check("the OpenAI warmup still reaches the OpenAI client", bool(_rec.calls), True)
check("...with exactly the kwargs it shipped with",
      sorted(_kw) if isinstance(_kw, dict) else _kw,
      ["max_completion_tokens", "messages", "model", "prompt_cache_key",
       "reasoning_effort", "seed", "timeout"])
check("...the system message is the shared prefix, byte for byte",
      at(_kw, "messages", 0, "content"), "SYSTEM")
check("...and the ceiling is the configured minimum",
      at(_kw, "max_completion_tokens"),
      config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS)

# --- 1d. The provider gate admits exactly two ------------------------------
check("the per-trial supported set is closed and has both built providers",
      list(_evaluation.PER_TRIAL_SUPPORTED_PROVIDERS),
      [config.MATCHING_PROVIDER_OPENAI,
       config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC])
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK):
    check("the RESPONSES branch is still refused, which is deliberate and not "
          "an oversight -- that endpoint owns its own caching controls",
          drive(_evaluation.assert_per_trial_provider_supported),
          Raised(_evaluation.PerTrialProviderUnsupportedError("x")))
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    check("...and Converse is admitted",
          drive(_evaluation.assert_per_trial_provider_supported), None)

# --- 1e. Only Converse claims a confirmable cache write --------------------
check("the confirming-provider set is closed and names Converse alone",
      list(_evaluation.PER_TRIAL_CACHE_CONFIRMING_PROVIDERS),
      [config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC])
check("on the SHIPPED OpenAI provider a cache write is NOT confirmable, "
      "because Chat Completions reports no write count -- enabling the check "
      "there would fail every patient of the shipped arm",
      _evaluation.per_trial_cache_is_confirmable(), False)
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    check("...and IS confirmable on Converse", 
          _evaluation.per_trial_cache_is_confirmable(), True)


# --- 1f. THE RETAINED GROUPED ARM ON THIS PROVIDER IS UNTOUCHED -----------
#
# EVERYTHING THIS PASS ADDED IS GATED ON `_per_trial_calls`, and that is a
# claim about a branch rather than about a value, so it is DRIVEN. Grouped is
# what this provider served before the pass and is the documented comparison
# arm; a warmup issued there, or a cache-read miss counted there, would be this
# pass leaking into the arm it promised not to touch.
with counters_zeroed() as (_gw, _gm):
    _rg, _sg = run_node(TRIALS, per_trial=False)
    _gw_seen, _gm_seen = dict(_gw), dict(_gm)
check("grouped mode on Converse packs the whole patient into ONE request",
      len(_sg.requests), 1)
check("...and issues NO warmup at all", len(_sg.warmup_requests()), 0)
check("...judging every trial", len(at(_rg, "evaluations") or []), len(TRIALS))
check("...with no error", bool(at(_rg, "error")), False)
check("...no warmup degradation", _gw_seen, {})
check("...and NO cache-read miss, even though the check's provider gate is "
      "satisfied -- the arm gate is what keeps it out", _gm_seen, {})

# ===========================================================================
# SECTION 2 — THE WARMUP AND THE TRIAL CALL SHARE ONE PREFIX
# ===========================================================================

section("2. The Converse warmup request, field by field")

with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    _trial_kw = bac.build_converse_request("SYSTEM-PREFIX", "USER-TRIAL")
    _warm_kw = bac.build_converse_request(
        "SYSTEM-PREFIX", config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE,
        warmup=True)

# --- 2a. THE PREFIX IS BYTE-IDENTICAL -------------------------------------
#
# THE SINGLE MOST LOAD-BEARING CHECK IN THIS FILE. The `system` block IS the
# cached prefix. If the warmup's differs from the wave's by one byte the warmup
# writes a prefix nothing reads, every wave call reports cacheRead 0, and --
# with the confirmation in place -- every patient FAILS. Compared as
# serialized JSON so key ORDER is included, because Converse hashes the request
# body rather than a Python dict.
check("the warmup's `system` block is BYTE-IDENTICAL to a trial call's",
      json.dumps(_warm_kw["system"], sort_keys=False),
      json.dumps(_trial_kw["system"], sort_keys=False))
check("...and it is not empty, so that comparison is not vacuous",
      len(_warm_kw["system"]) >= 2, True)

# --- 2b. THE BREAKPOINT'S POSITION ----------------------------------------
check("the system block is [text, cachePoint] in that order -- the breakpoint "
      "sits AFTER the stable content, which is what prompt-caching.html asks "
      "for", [sorted(b) for b in _warm_kw["system"]],
      [["text"], ["cachePoint"]])
check("the cachePoint is the LAST element, so nothing stable follows it",
      sorted(_warm_kw["system"][-1]), ["cachePoint"])
check("it names the configured TTL explicitly rather than relying on a vendor "
      "default that could move and silently re-price a campaign",
      _warm_kw["system"][-1]["cachePoint"],
      {"type": "default", "ttl": config.BEDROCK_ANTHROPIC_CACHE_TTL})
check("there is exactly ONE checkpoint, well inside the documented maximum of "
      "4 for this model",
      json.dumps(_warm_kw).count("cachePoint"), 1)
check("...and NONE of them is in `messages`, which would cache the per-trial "
      "text at a premium write rate with no read to follow it",
      "cachePoint" in json.dumps(_warm_kw["messages"]), False)

# --- 2c. WHAT DIFFERS, AND IT IS EXACTLY TWO FIELDS ------------------------
_diff = sorted(k for k in set(_warm_kw) | set(_trial_kw)
               if _warm_kw.get(k) != _trial_kw.get(k))
# TWO KEYS, AND THIS CHECK HAS NOW SAID TWO, THEN THREE, THEN TWO AGAIN --
# each time for a reason, and the last one is a live measurement rather than a
# reading of a documentation page. It said two, then three when the
# `outputConfig` DROP was counted as a difference, and it is two again because
# `BEDROCK_ANTHROPIC_WARMUP_SEND_OUTPUT_CONFIG` now ships True: the warmup
# carries the structured-output block, so that key no longer differs at all.
# WHY IT SHIPS TRUE IS A MEASUREMENT, recorded in full at the constant: a
# warmup without the block writes a DIFFERENT cache prefix from the wave
# (11,749 tokens against the wave's 12,416, on a byte-identical `system`), so
# the warmup warmed a prefix no trial call ever read and trial 1 paid a second
# full write. Enumerated EXACTLY rather than as a floor: a third key here would
# be a difference nobody argued for, and `system` appearing here at all would
# be the defect 2a exists to catch.
check("the warmup differs from a trial call in exactly two keys, both of them "
      "AFTER the cached prefix and neither of them `system`",
      _diff, ["inferenceConfig", "messages"])
check("...the ceiling is the configured minimum",
      _warm_kw["inferenceConfig"]["maxTokens"],
      config.MATCHING_PER_TRIAL_WARMUP_MAX_OUTPUT_TOKENS)
check("...the user message is the configured placeholder",
      _warm_kw["messages"][0]["content"][0]["text"],
      config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE)
# THIS PIN WAS INVERTED BY A LIVE MEASUREMENT, and the sentence it used to
# carry -- "it is in none of Converse's three checkpoint sections, so dropping
# it cannot change the cached prefix" -- is the inference the probe refuted.
# The citation behind it is accurate; what does not follow is that a section
# absent from the documented checkpoint LIST is absent from the cached PREFIX.
# Measured 2026-09-01: the block takes part in it, almost certainly as the
# `tools` entry that structured output compiles to, which is FIRST in the very
# ordering that sentence quoted.
check("outputConfig is CARRIED by the warmup by default, because a warmup "
      "without it warms a different prefix from the one the wave reads",
      "outputConfig" in _warm_kw, True)
check("...and a trial call carries it too, which is the point -- the two "
      "requests must present the same prefix",
      "outputConfig" in _trial_kw, True)
check("...and they are the SAME block, not merely both present: a warmup "
      "carrying a different schema would cache separately for a reason no "
      "usage field would name",
      json.dumps(_warm_kw["outputConfig"], sort_keys=True),
      json.dumps(_trial_kw["outputConfig"], sort_keys=True))

with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_WARMUP_SEND_OUTPUT_CONFIG=False):
    _warm_without = bac.build_converse_request(
        "SYSTEM-PREFIX", config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE,
        warmup=True)
check("the knob still turns the structured-output block OFF in one edit, so "
      "the pre-2026-09-01 behaviour is reproducible without a source change",
      "outputConfig" in _warm_without, False)
check("...and the prefix is byte-identical either way, which is what makes "
      "the cache split attributable to `outputConfig` and to nothing else",
      json.dumps(_warm_without["system"]), json.dumps(_trial_kw["system"]))

# --- 2d. No seed, no routing hint, on either shape -------------------------
for _label, _kw2 in (("warmup", _warm_kw), ("trial", _trial_kw)):
    check(f"({_label}) no seed reaches the wire -- Converse has no such field",
          "seed" in json.dumps(_kw2), False)
    check(f"({_label}) no prompt_cache_key either -- that is a Chat "
          f"Completions parameter and an unknown key here is a local "
          f"ParamValidationError", "prompt_cache_key" in json.dumps(_kw2),
          False)


# ===========================================================================
# SECTION 3 — THE READING: classify_cache_write
# ===========================================================================

section("3. The cache-write reading, off a real translated ChatCompletion")

# DRIVEN THROUGH THE REAL TRANSLATOR, not against a hand-built response object.
# The claim under test is that Converse's two counts survive `translate_response`
# into `usage.prompt_tokens_details` -- as the standard `cached_tokens` and as
# an EXTRA `cache_write_tokens` the OpenAI SDK's model has to tolerate -- and a
# fabricated ChatCompletion would assert that claim away.


def translated(read, write):
    with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
        return drive(bac.translate_response,
                     converse_reply("{}", read=read, write=write))


_cases = [
    ("a fresh write", None, 19000, _evaluation.CACHE_WRITE_WROTE),
    ("a write reported beside a zero read", 0, 19000,
     _evaluation.CACHE_WRITE_WROTE),
    ("a read with no write -- the prefix was ALREADY warm, which is what a "
     "retried or resumed patient looks like and is a PASS", 19000, 0,
     _evaluation.CACHE_WRITE_ALREADY_WARM),
    ("both reported, both zero -- the provider ANSWERED and the answer is no",
     0, 0, _evaluation.CACHE_WRITE_REPORTED_NOTHING),
    ("neither field present -- nothing is KNOWN, which is a different finding "
     "from a reported zero", None, None,
     _evaluation.CACHE_WRITE_NOT_REPORTED),
    ("a read field alone, reporting zero", 0, None,
     _evaluation.CACHE_WRITE_REPORTED_NOTHING),
    ("a partial hit -- both non-zero -- is reported as `wrote`, the stronger "
     "of the two true statements about it", 4000, 15000,
     _evaluation.CACHE_WRITE_WROTE),
]
for _label, _r, _w, _expect in _cases:
    _resp = translated(_r, _w)
    check(f"({_expect}) {_label}",
          drive(_evaluation.classify_cache_write, _resp), _expect)

check("the outcome vocabulary is closed and has four members",
      list(_evaluation.CACHE_WRITE_OUTCOMES),
      ["wrote", "already_warm", "reported_zero", "not_reported"])
check("...and exactly two of them let the wave go out",
      list(_evaluation.CACHE_WRITE_CONFIRMED), ["wrote", "already_warm"])
check("the confirmed set is a SUBSET of the vocabulary, so a fifth outcome "
      "added without a branch cannot default to CONFIRMED",
      set(_evaluation.CACHE_WRITE_CONFIRMED)
      <= set(_evaluation.CACHE_WRITE_OUTCOMES), True)

# THE TWO COUNTS SURVIVE THE TRANSLATION, ASSERTED DIRECTLY. Without this the
# seven readings above could all be right for the wrong reason -- a translator
# that dropped both fields would answer `not_reported` for every one of them,
# and only the two that EXPECT `not_reported` would pass.
_full = translated(17920, 1080)
check("cacheReadInputTokens survives as prompt_tokens_details.cached_tokens",
      at(_full.usage.prompt_tokens_details.__dict__, "cached_tokens"), 17920)
check("cacheWriteInputTokens survives as the extra cache_write_tokens, which "
      "the installed OpenAI SDK model tolerates (measured, not assumed)",
      getattr(_full.usage.prompt_tokens_details, "cache_write_tokens", None),
      1080)
check("and _cache_counts reads both back", 
      drive(_evaluation._cache_counts, _full), (17920, 1080))

# A BOOL IS NOT A COUNT. `isinstance(True, int)` is True, so a cached figure of
# 1 that was really a flag would read as a cache hit and let a wave out.
_boolish = types.SimpleNamespace(usage=types.SimpleNamespace(
    prompt_tokens_details=types.SimpleNamespace(
        cached_tokens=True, cache_write_tokens=True)))
check("a bool is refused as a count, so a flag cannot be read as a cache hit",
      drive(_evaluation._cache_counts, _boolish), (None, None))
check("...and such a response is therefore `not_reported`, not `wrote`",
      drive(_evaluation.classify_cache_write, _boolish),
      _evaluation.CACHE_WRITE_NOT_REPORTED)


# ===========================================================================
# SECTION 4 — CACHE-OR-NOTHING, END TO END THROUGH THE REAL NODE
# ===========================================================================

section("4. A confirmed warmup releases the wave; an unconfirmed one does not")

# --- 4a. THE HAPPY PATH ----------------------------------------------------
# THE SNAPSHOTS ARE TAKEN INSIDE THE BLOCK. `counters_zeroed` restores the
# process-global counters on exit, so a `dict(...)` read after it would report
# whatever was there before this file ran -- which on a clean run is {} and is
# therefore the shape that PASSES for the wrong reason. Measured: the first
# version of 4b read them outside and saw {} against an expectation of {}.
with counters_zeroed() as (_warm_counter, _miss_counter):
    _result, _stub = run_node(TRIALS)
    _warmups = _stub.warmup_requests()
    _wave = _stub.wave_ids()
    _warm_seen, _miss_seen = dict(_warm_counter), dict(_miss_counter)

check("the node completed rather than raising", isinstance(_result, dict), True)
check("exactly ONE warmup was issued", len(_warmups), 1)
check("...and it was issued FIRST, before any trial call",
      is_warmup(_stub.requests[0]) if _stub.requests
      else _Absent("no request at all"), True)
check("one trial call per trial, and no more",
      sorted(i for ids in _wave for i in ids),
      sorted(t["trial"]["nct_id"] for t in TRIALS))
check("every trial was judged", len(at(_result, "evaluations") or []),
      len(TRIALS))
check("the run records N+1 billed calls -- the warmup is BILLED and is counted",
      at(_result, "llm_classifier_calls"), len(TRIALS) + 1)
check("no warmup degradation was recorded on the happy path",
      _warm_seen, {})
check("...and no cache-read miss either", _miss_seen, {})

# --- 4b. THE WARMUP ANSWERS AND CACHES NOTHING -----------------------------
#
# THE CENTRAL SCENARIO OF THIS FILE. The request SUCCEEDS -- this is not a
# transport failure -- and its usage block says nothing was cached. AWS
# documents that exact outcome for a prefix below the 1,024-token minimum, and
# documents that inference "still succeeds". Before this pass the wave went out
# regardless, at the full input rate, and the run reported an ordinary patient.
with counters_zeroed() as (_warm_counter, _miss_counter):
    _result2, _stub2 = run_node(TRIALS, warmup_read=0, warmup_write=0)
    _warm_seen2 = dict(_warm_counter)

check("the warmup was still issued -- the refusal is about its RESULT, not "
      "about reaching the provider", len(_stub2.warmup_requests()), 1)
check("*** ZERO TRIAL CALLS WERE ISSUED ***, which is cache-or-nothing: "
      "fifteen full-price requests are worse than one patient re-run",
      len(_stub2.wave_requests()), 0)
check("the patient FAILED rather than completing with a hole",
      bool(at(_result2, "error")), True)
check("...and the failure names the cause",
      "not cached" in str(at(_result2, "error") or ""), True)
check("...naming the OUTCOME so an operator knows which remedy list applies",
      _evaluation.CACHE_WRITE_REPORTED_NOTHING in str(at(_result2, "error") or ""),
      True)
check("no verdict was published, so nothing can be mistaken for a judgement",
      at(_result2, "evaluations"), [])
check("the warmup's own tokens ARE recorded -- it was billed, and a failed row "
      "that reported zero calls would be the false-zero shape this file's "
      "neighbours removed", at(_result2, "llm_classifier_calls"), 1)
check("...and its input tokens with them",
      at(_result2, "llm_classifier_input_tokens"), 1200)
check("the degradation names the writer AND the outcome, because 'the warmup "
      "reported zero' and 'the fallback's trial call reported zero' have very "
      "different implications for how much was already spent",
      _warm_seen2,
      {f"cache_unconfirmed:{_evaluation.WARMUP_SOURCE_WARMUP}:"
       f"{_evaluation.CACHE_WRITE_REPORTED_NOTHING}": 1})

# --- 4c. THE WARMUP REPORTS NO CACHE FIELDS AT ALL -------------------------
with counters_zeroed() as (_warm_counter, _):
    _result3, _stub3 = run_node(TRIALS, warmup_read=None, warmup_write=None)
    _warm_seen3 = dict(_warm_counter)
check("an absent field is ALSO a refusal -- 'unconfirmed' is not 'fine'",
      len(_stub3.wave_requests()), 0)
check("...and it is counted SEPARATELY from a reported zero, because a reported "
      "zero sends an operator to the prefix and an absent field sends them to "
      "the API", _warm_seen3,
      {f"cache_unconfirmed:{_evaluation.WARMUP_SOURCE_WARMUP}:"
       f"{_evaluation.CACHE_WRITE_NOT_REPORTED}": 1})
check("...and the message says so rather than blaming the prompt",
      "NO cache usage fields" in str(at(_result3, "error") or ""), True)

# --- 4d. A PREFIX THAT WAS ALREADY WARM IS A PASS --------------------------
#
# THE CASE A NAIVE "cacheWriteInputTokens > 0" WOULD FAIL, and it is not
# hypothetical: a parse retry re-enters this node and issues a FRESH warmup
# against a prefix its own wave has already written N times over, and a resumed
# or resampled patient does the same inside the TTL.
with counters_zeroed() as (_warm_counter, _):
    _result4, _stub4 = run_node(TRIALS, warmup_read=19000, warmup_write=0)
    _warm_seen4 = dict(_warm_counter)
check("an already-warm prefix releases the wave",
      len(_stub4.wave_requests()), len(TRIALS))
check("...the patient completed", bool(at(_result4, "error")), False)
check("...and nothing was recorded as a degradation", _warm_seen4, {})


# --- 4e. AND A RESUME REALLY CAN PICK IT UP -------------------------------
#
# "THE PATIENT FAILS CLEANLY SO THE CHECKPOINT RESUMES IT" IS THE HALF OF THE
# CACHE-OR-NOTHING CLAIM THAT LIVES IN ANOTHER MODULE, so asserting `error` is
# set here proves only that this node did its part. What makes the claim TRUE
# is `oncotriage/batch/runner.py`: it derives a patient's status from the
# result's `error`, and `_on_done` checkpoints only a success. Both are read
# out of that file's SOURCE rather than retyped, so a change to either fails
# here instead of turning every failed patient into one a resume skips forever.
import ast                                                       # noqa: E402

_runner_tree = ast.parse(open(_RUNNER_PATH, encoding="utf-8").read())

# (i) THE STATUS DERIVATION: `status = "error" if <something> else "success"`.
_status_assigns = [
    n for n in ast.walk(_runner_tree)
    if isinstance(n, ast.Assign)
    and any(isinstance(t, ast.Name) and t.id == "status" for t in n.targets)
    and isinstance(n.value, ast.IfExp)]
check("the batch runner derives a patient's status with a conditional, and "
      "there is exactly one such derivation", len(_status_assigns), 1)
_ifexp = _status_assigns[0].value if _status_assigns else None
check("...whose TRUE arm is 'error' and FALSE arm is 'success', so a non-empty "
      "error is not a success",
      (getattr(_ifexp.body, "value", None), getattr(_ifexp.orelse, "value", None))
      if _ifexp is not None else _Absent("no conditional found"),
      ("error", "success"))
check("...and the value it tests is the result's `error` key, which is the "
      "field this node sets",
      "error" in ast.dump(_ifexp.test) if _ifexp is not None
      else _Absent("no conditional found"), True)

# (ii) THE CHECKPOINT IS GUARDED BY THAT STATUS. A patient recorded 'error' is
#      not written into the resume state, so the next run attempts it again.
_guards = [n for n in ast.walk(_runner_tree)
           if isinstance(n, ast.Compare)
           and "entry" in ast.dump(n.left) and "status" in ast.dump(n.left)
           and any(isinstance(c, ast.Constant) and c.value == "success"
                   for c in n.comparators)]
check("the runner guards its checkpoint on `entry['status'] == 'success'`, in "
      "at least one place -- which is what makes a failed patient resumable",
      len(_guards) >= 1, True)

# (iii) APPLIED TO THE ACTUAL RESULT THIS PASS PRODUCES.
_unconfirmed_status = "error" if at(_result2, "error") else "success"
check("*** applying the runner's own predicate to the unconfirmed-warmup "
      "result gives 'error' ***, so that patient is NOT checkpointed and the "
      "next run attempts it whole", _unconfirmed_status, "error")
check("...and the same predicate on the HAPPY path gives 'success', so this "
      "is a discriminating reading rather than one that says 'error' about "
      "everything", "error" if at(_result, "error") else "success", "success")


# ===========================================================================
# SECTION 5 — THE FALLBACK WRITER OBEYS THE SAME RULE
# ===========================================================================

section("5. The provider refuses the warmup's shape; the fallback still obeys "
        "cache-or-nothing")

# WHEN THE PROVIDER REFUSES THE WARMUP'S REQUEST SHAPE the schedule degrades to
# the retired one-then-rest form, and the FIRST REAL TRIAL CALL becomes the
# cache writer. It carries the same `cachePoint` and reports the same fields,
# so the same confirmation covers it -- which is the whole reason
# `_confirm_cache_write` takes the writer as an argument rather than reading
# the warmup's response off a closure.

_REFUSAL = ClientError(
    "ValidationException",
    "The value for maxTokens must be at least 16 for this model")

# --- 5a. The refusal is CLASSIFIED, which it was not before this pass ------
#
# `classify_warmup_rejection` reads an HTTP status. A botocore ClientError
# carries it at `.response["ResponseMetadata"]["HTTPStatusCode"]` -- NOT at
# `.status_code`, and `.response` is a plain dict so `getattr(dict,
# "status_code")` is None. Before `_http_status_of` learned botocore's shape it
# returned None for EVERY Converse error, so every 400 read as a transport
# failure and FAILED the patient instead of degrading.
check("a botocore ClientError's HTTP status is now readable",
      drive(_evaluation._http_status_of, _REFUSAL), 400)
check("...and a 400 naming Converse's `maxTokens` spelling is classified as a "
      "refusal of the request SHAPE",
      drive(_evaluation.classify_warmup_rejection, _REFUSAL),
      _evaluation.WARMUP_REJECTED_MINIMAL_OUTPUT)
check("a 429 is NOT a shape refusal -- it is a transport failure a retry may "
      "well fix, and falling back for it would hide a throttled account",
      drive(_evaluation.classify_warmup_rejection,
            ClientError("ThrottlingException", "Too many requests", 429)),
      None)
check("a 400 that names NO parameter is not a shape refusal either -- a "
      "context overflow is also a 400 and would fail every trial call too",
      drive(_evaluation.classify_warmup_rejection,
            ClientError("ValidationException", "input is too long")), None)

# --- 5b. The fallback writer CACHED: the rest of the wave goes out ---------
with counters_zeroed() as (_wc, _):
    _r5, _s5 = run_node(TRIALS, warmup_raise=_REFUSAL,
                        trial_read=0, trial_write=19000)
    _wc5 = dict(_wc)
check("the warmup was refused and no second warmup was attempted",
      len(_s5.warmup_requests()), 1)
check("the fallback issued one call per trial",
      len(_s5.wave_requests()), len(TRIALS))
check("...and the patient completed", bool(at(_r5, "error")), False)
check("the refusal is recorded as a SHAPE refusal, whose remedy is a constant",
      _wc5, {_evaluation.WARMUP_REJECTED_MINIMAL_OUTPUT: 1})

# --- 5c. The fallback writer CACHED NOTHING: the rest does NOT go out ------
#
# THE DOOR THE FALLBACK OPENS, CLOSED. Until this pass the held-back writer's
# outcome was inspected only for a RAISE; a writer that returned and cached
# nothing released N-1 full-price requests against a cold prefix, and the run
# reported an ordinary patient.
with counters_zeroed() as (_wc, _):
    _r6, _s6 = run_node(TRIALS, warmup_raise=_REFUSAL,
                        trial_read=0, trial_write=0)
    _wc6 = dict(_wc)
check("*** EXACTLY ONE TRIAL CALL WAS ISSUED *** -- the writer, and none of "
      "the rest of the wave", len(_s6.wave_requests()), 1)
check("the patient FAILED rather than completing with one verdict and N-1 "
      "holes -- a completed patient is CHECKPOINTED, and a resume would skip "
      "it forever", bool(at(_r6, "error")), True)
check("no verdict was published", at(_r6, "evaluations"), [])
check("BOTH the refusal and the unconfirmed write are recorded, because they "
      "are two findings: the provider refused a shape AND the fallback did "
      "not cache", sorted(_wc6),
      sorted([_evaluation.WARMUP_REJECTED_MINIMAL_OUTPUT,
              f"cache_unconfirmed:{_evaluation.WARMUP_SOURCE_FALLBACK_WRITER}:"
              f"{_evaluation.CACHE_WRITE_REPORTED_NOTHING}"]))
check("the writer's tokens SURVIVE into the record -- it was issued and "
      "billed, and it is folded as `unconsumed` rather than vanishing",
      at(_r6, "llm_classifier_calls"), 1)
check("...and its ledger row says it was never read",
      [r.get("unconsumed") for r in (at(_r6, "llm_classifier_call_details") or [])],
      [True])
check("the failure sentence does NOT say the writer 'failed', because it did "
      "not -- it answered perfectly well and cached nothing",
      "then failed" in str(at(_r6, "error") or ""), False)
check("...it says the prefix was not cached",
      "not cached" in str(at(_r6, "error") or ""), True)
# THE RESUME READING, APPLIED TO THIS PATH TOO -- section 4e derives the
# predicate from the runner's own source; this is the fallback writer's arm of
# it, and it is here rather than there because `_r6` is produced here.
check("...and the runner's own predicate calls this patient 'error', so a "
      "resume attempts it whole rather than skipping a patient with one "
      "verdict", "error" if at(_r6, "error") else "success", "error")


# ===========================================================================
# SECTION 6 — A WAVE CALL THAT DID NOT READ THE CACHE
# ===========================================================================

section("6. A cache-read miss surfaces, and the verdict is KEPT")

_MISS_ID = TRIALS[2]["trial"]["nct_id"]
with counters_zeroed() as (_, _mc):
    _r7, _s7 = run_node(TRIALS, read_for={_MISS_ID: 0})
    _mc7 = dict(_mc)

check("every trial was still judged -- a broken COST premise is not a broken "
      "judgement, and discarding a paid answer would spend the money twice",
      len(at(_r7, "evaluations") or []), len(TRIALS))
check("the patient completed", bool(at(_r7, "error")), False)
check("the miss is COUNTED, exactly once",
      _mc7, {_evaluation.CACHE_WRITE_REPORTED_NOTHING: 1})
check("...and the other three calls are not counted, so this is a per-CALL "
      "reading rather than a per-patient one",
      sum(_mc7.values()), 1)

# AN ABSENT FIELD IS ITS OWN KEY HERE TOO.
with counters_zeroed() as (_, _mc):
    _r8, _s8 = run_node(TRIALS, trial_read=None)
    _mc8 = dict(_mc)
check("a wave that reports no cache field at all is counted under its own key",
      _mc8, {_evaluation.CACHE_WRITE_NOT_REPORTED: len(TRIALS)})
check("...and the patient still completed",
      len(at(_r8, "evaluations") or []), len(TRIALS))

# THE GATE: on a provider that cannot confirm a write, the read check is off
# too -- otherwise the SHIPPED OpenAI arm would emit an unexplained warning per
# call. Driven through the real node with the OpenAI provider and a stub that
# reports NO cached figure at all.
class _OpenAIWaveStub:
    def __init__(self):
        self.calls = []
        self.chat = types.SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        _ids = re.findall(r"<<<TRIAL_DATA nct_id=(\S+) ",
                          kwargs["messages"][1]["content"])
        _r = types.SimpleNamespace(
            choices=[types.SimpleNamespace(
                message=types.SimpleNamespace(
                    content=eligible_body(_ids) if _ids else "", refusal=None),
                finish_reason="stop")],
            usage=types.SimpleNamespace(
                prompt_tokens=1000, completion_tokens=100,
                completion_tokens_details=None, prompt_tokens_details=None),
            model=config.MATCHING_MODEL)
        return _r


with counters_zeroed() as (_, _mc):
    _ostub = _OpenAIWaveStub()
    _saved = deps.set_overrides({deps.OPENAI_CLIENT: _ostub})
    try:
        _r9 = drive(node_llm_classifier_evaluation, {
            "patient_data": PATIENT, "filtered_trials": TRIALS,
            "llm_classifier_retries": 0, "mesh_filter_applied": True,
            "mesh_filter_skip_reason": "applied", "stage_timings": {}})
    finally:
        deps.restore_overrides(_saved)
    _mc9 = dict(_mc)
check("the shipped OpenAI arm still runs per-trial and completes",
      len(at(_r9, "evaluations") or []), len(TRIALS))
check("...reporting NO cached figure at all, which is the case the gate is "
      "about", at(_r9, "llm_classifier_cached_input_tokens"), None)
check("*** and NOT ONE miss is counted on it ***, because on a provider whose "
      "write was never confirmable a read miss is an unexplained warning "
      "rather than a finding", _mc9, {})


# ===========================================================================
# SECTION 7 — THE TOKEN ACCOUNTING ACROSS A WARMUP PLUS N READS
# ===========================================================================

section("7. The disjoint usage counts, summed back across a whole wave")

# THE DISJOINTNESS IS THE CENTRAL COST CLAIM OF THIS BRANCH, and a single call
# is the case where it is EASIEST to get right and hardest to notice wrong: at
# one call, an implementation that reported `inputTokens` alone differs from a
# correct one by exactly the cached figure, and there is nothing to compare it
# against. Across a warmup plus N reads there is: the patient's stored input
# total must equal the sum of AWS's own formula applied per call.

# THE WARMUP'S READ IS *ABSENT*, NOT ZERO, and that is the shape a real first
# warmup has: it WROTE the prefix, so there was no read to report. `None` in
# the fixture means the field is omitted from the reply entirely, which is the
# distinction the whole confirmation vocabulary rests on -- so the fixture has
# to be able to express it and the arithmetic below has to treat it as 0.
_WARM_IN, _WARM_READ, _WARM_WRITE = 1200, None, 19000
_TRIAL_IN, _TRIAL_READ, _TRIAL_WRITE = 1000, 19000, 0

with counters_zeroed() as (_, _mc):
    _r10, _s10 = run_node(TRIALS)

# AWS's own formula, applied per request, then summed. Written out here rather
# than read back off the adapter, because reading it off the adapter would
# compare the implementation with itself.
_expected_input = ((_WARM_IN + (_WARM_READ or 0) + (_WARM_WRITE or 0))
                   + len(TRIALS) * (_TRIAL_IN + (_TRIAL_READ or 0)
                                    + (_TRIAL_WRITE or 0)))
check("the patient's stored input total equals inputTokens + cacheRead + "
      "cacheWrite summed over EVERY request, warmup included",
      at(_r10, "llm_classifier_input_tokens"), _expected_input)
check("...and that total is not the naive rename, which would under-report by "
      "exactly the cached amount on every hit -- silently, in the direction "
      "that flatters the migration",
      at(_r10, "llm_classifier_input_tokens")
      == _WARM_IN + len(TRIALS) * _TRIAL_IN, False)
check("the under-reporting a rename would produce, as a number: "
      f"{_expected_input - (_WARM_IN + len(TRIALS) * _TRIAL_IN)} tokens on a "
      f"{len(TRIALS)}-trial patient",
      _expected_input - (_WARM_IN + len(TRIALS) * _TRIAL_IN),
      (_WARM_READ or 0) + (_WARM_WRITE or 0)
      + len(TRIALS) * ((_TRIAL_READ or 0) + (_TRIAL_WRITE or 0)))
check("the output total is the plain sum, because output has no cached term",
      at(_r10, "llm_classifier_output_tokens"), 1 + len(TRIALS) * 100)

# --- 7a. The per-call ledger, which is the only thing that can answer
#         "did the cache warm" as opposed to "how much was cached in total" ---
_ledger = at(_r10, "llm_classifier_call_details") or []
check("one ledger row per request, warmup included", len(_ledger),
      len(TRIALS) + 1)
_warm_rows = [r for r in _ledger if r.get("warmup")]
check("exactly one row is marked as the warmup", len(_warm_rows), 1)
check("...it carries no trial, so per-trial accounting that groups on `trials` "
      "excludes it by construction", _warm_rows[0]["trials"], 0)
check("...its depth is None rather than 0, because 0 is a real split depth",
      _warm_rows[0]["depth"], None)
check("...and its READ figure is NULL rather than 0, because this warmup wrote "
      "the prefix and had nothing to read -- absent and zero are different "
      "readings and the ledger keeps them apart",
      _warm_rows[0]["cached_tokens"], None)
check("every WAVE row reports the cache read, which is what says the discount "
      "landed on a per-call basis",
      [r["cached_tokens"] for r in _ledger if not r.get("warmup")],
      [_TRIAL_READ] * len(TRIALS))
check("the patient-level cached column is the WAVE's reading and excludes the "
      "warmup, so a wave that has gone silent about caching cannot look like "
      "a wave that reported no hits",
      at(_r10, "llm_classifier_cached_input_tokens"),
      len(TRIALS) * _TRIAL_READ)

# --- 7b. The cost is priced off a real PRICING_CONFIG row ------------------
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    _wire = config.matching_wire_model()
check("the wire model this branch would be billed for has a PRICING_CONFIG row "
      "-- an unpriced model raises BEFORE a row is written, which is the "
      "loud-failure mechanism this project requires",
      _wire in config.PRICING_CONFIG["models"], True)


# ===========================================================================
# SECTION 8 — THE 1,024-TOKEN FLOOR, MEASURED AGAINST THE LIVE RENDERER
# ===========================================================================

section("8. The prefix minimum, re-derived rather than remembered")

# WHY THIS SECTION EXISTS. Bedrock's explicit cache has a per-model minimum --
# 1,024 tokens for Claude Sonnet 4.6 -- and `prompt-caching.html` (read
# 2026-08-30) documents the sub-minimum outcome as SILENT: "If you add a cache
# checkpoint before meeting the minimum number of tokens, your inference still
# succeeds, but your prefix isn't cached." This pass's answer to "what does
# that mean for a short patient record" is a MEASUREMENT, and a measurement
# taken once rots. Re-deriving it here means a prompt shortened past the floor
# fails HERE rather than as a campaign that quietly stopped caching.
#
# THE FLOOR IS WRITTEN OUT AND NOT IMPORTED, deliberately: it is a fact about
# a vendor's model rather than a tunable of this project, and nothing in the
# package declares it. If it ever becomes a config constant this check should
# read it from there and this comment should go.
_BEDROCK_MIN_CACHE_TOKENS = 1024

_instructions = {
    applied: drive(_prompts.render_system_prompt, applied, "unrecorded", "")
    for applied in (True, False)}
for _applied, _text in _instructions.items():
    _tokens = len(_text) // config.CHARS_PER_TOKEN
    check(f"the Stage 5 INSTRUCTIONS alone (mesh_filter_applied={_applied}) "
          f"clear the 1,024-token cache floor before a single character of "
          f"patient record: {len(_text)} chars ~ {_tokens} tokens",
          _tokens > _BEDROCK_MIN_CACHE_TOKENS, True)
    check(f"...with real headroom rather than by a margin ({_tokens} tokens is "
          f"{_tokens / _BEDROCK_MIN_CACHE_TOKENS:.1f}x the floor)",
          _tokens >= 3 * _BEDROCK_MIN_CACHE_TOKENS, True)

# NON-DEGENERACY: the renderer really produced something. A stub that returned
# "" would make the two checks above compare 0 > 1024 and FAIL, which is the
# right direction -- but a renderer that returned a megabyte of whitespace
# would pass them for no reason, so the shape is checked too.
check("the rendered instructions are the real ones, not an empty or "
      "placeholder string", "<<<PATIENT_RECORD>>>" in _instructions[True], True)

# AND THE FULL PREFIX A REAL PATIENT PRODUCES, through the same renderer the
# node uses, so this measures the prefix the cachePoint would actually follow.
# THE PAIR THE NODE CALLS, not the text-only wrapper. Stage 5 resolves
# `build_patient_record` -- the de-identification stage and then the render --
# because it needs the record as well as the text, so reading the wrapper off
# `_evaluation` would be reading a name that module no longer binds.
_summary = drive(lambda: _evaluation.build_patient_record(PATIENT)[1])
_full_prefix = drive(_prompts.render_system_prompt, True, "applied", _summary)
_full_tokens = len(_full_prefix) // config.CHARS_PER_TOKEN
check(f"a whole rendered prefix for this file's patient is "
      f"{len(_full_prefix)} chars ~ {_full_tokens} tokens, comfortably over "
      f"the floor", _full_tokens > _BEDROCK_MIN_CACHE_TOKENS, True)
check("...and it is LONGER than the instructions alone, so the patient record "
      "is really in it and this is not the empty-record measurement again",
      len(_full_prefix) > len(_instructions[True]), True)


# ===========================================================================
# SECTION 9 — THE PARALLEL BOUND IS CONFIGURATION AND IS HONOURED
# ===========================================================================

section("9. The parallel bound: configuration, provider-specific, honoured")

check("the shared bound is an int >= 1",
      isinstance(config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS, int)
      and not isinstance(config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS, bool)
      and config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS >= 1, True)
check("the Converse override ships as None, meaning 'follow the shared bound', "
      "so nothing moves until an operator sets it",
      config.BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS, None)
check("the retry budget resolves to botocore's TOTAL-attempt convention, which "
      "is OPENAI_SDK_MAX_RETRIES + 1 -- the conversion is the whole reason it "
      "is a function", config.bedrock_anthropic_max_attempts(),
      config.OPENAI_SDK_MAX_RETRIES + 1)
with settings(BEDROCK_ANTHROPIC_MAX_ATTEMPTS=7):
    check("...and the override wins when set",
          config.bedrock_anthropic_max_attempts(), 7)
check("the retry mode is a closed vocabulary and ships botocore's `standard`",
      (config.BEDROCK_ANTHROPIC_RETRY_MODE,
       config.BEDROCK_ANTHROPIC_RETRY_MODE
       in config.BEDROCK_ANTHROPIC_RETRY_MODES), ("standard", True))
check("...and `adaptive` -- AWS's documented answer for a throttled account -- "
      "is available in one edit",
      "adaptive" in config.BEDROCK_ANTHROPIC_RETRY_MODES, True)

# --- 9a. HONOURED, MEASURED RATHER THAN ASSUMED ---------------------------
#
# A BARRIER, NOT A CLOCK. Every wave call joins a barrier sized to the bound,
# so "the node really ran N at once" is a fact the stub observed rather than a
# timing that passes on an idle laptop and fails under CI bucket A's sixty
# competing processes. A dispatcher that ran them sequentially CANNOT satisfy
# a barrier of 2, so the barrier breaks and `barrier_broken` says so.
_bound2 = ConverseStub(barrier_size=2, barrier_timeout=10.0)
with counters_zeroed():
    _r11, _ = run_node(TRIALS, stub=_bound2, parallel=2)
check("with the Converse override at 2 the node really ran two calls at once",
      _bound2.barrier_broken, False)
check("...and never more than two", _bound2.max_in_flight, 2)
check("...with every trial still judged",
      len(at(_r11, "evaluations") or []), len(TRIALS))

# SEQUENTIAL IS LEGAL AND MEANS ONE. The barrier of 2 is the CONTROL: at a
# bound of 1 it cannot be satisfied, which is what proves the bound is read.
_bound1 = ConverseStub(barrier_size=2, barrier_timeout=2.0)
with counters_zeroed():
    _r12, _ = run_node(TRIALS, stub=_bound1, parallel=1)
check("at a bound of 1 the barrier of 2 CANNOT be satisfied, which is the "
      "control: the bound is read rather than ignored",
      _bound1.barrier_broken, True)
check("...only one call was ever in flight", _bound1.max_in_flight, 1)

# --- 9b. THE OVERRIDE IS PROVIDER-SCOPED ----------------------------------
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS=2):
    check("on Converse the override wins", config.per_trial_parallel_bound(), 2)
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_OPENAI,
              BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS=2):
    check("on OpenAI the same value is ignored, which is what makes it safe to "
          "pace a throttled AWS account without re-pacing the shipped arm",
          config.per_trial_parallel_bound(),
          config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)


# ===========================================================================
# SECTION 10 — PLANTED DEFECTS, EACH WITH A CONTROL
# ===========================================================================

section("10. Nine plants, and the shipped answer beside each one")

# EVERY PLANT GOES INTO AN IN-MEMORY COPY, never an edit to the file: this
# project's stated preference, and what keeps this file out of the collision
# matrix. EVERY PLANT IS PAIRED WITH A CONTROL requiring the SHIPPED module to
# give the clean answer -- without it, a probe that always disagreed would
# report every plant as caught while measuring nothing.
#
# THE COPIES ARE REAL ModuleType OBJECTS because a function's globals ARE the
# dict it was exec'd into, so a class attribute would not be reached.


def planted(path, mutate, package="oncotriage.agent"):
    """A planted copy, or a RECORDED FAILURE if the plant did not take.

    A PLANT THAT MATCHED NOTHING IS AN AUTHORING ERROR AND MUST BE LOUD -- a
    revert reported as MISSED against a check that works is a different finding
    from a weak check, and this project has paid for that confusion before. But
    ``exec_copy``'s raise is at MODULE LEVEL, so a mis-anchored plant aborts the
    file with no summary and hides every check below it. That is the abort shape
    this project has now shipped fourteen times, and it happened HERE while this
    section was being written: a stray no-op plant left in place raised and took
    eleven checks with it.

    So the raise becomes a ``PLANT-FAILED`` failure and a named absence. The run
    still goes red, the message still names the anchor, and every other plant
    still reports.
    """
    try:
        return exec_copy(path, mutate, package=package)
    except Exception as exc:                                     # noqa: BLE001
        check(f"PLANT-FAILED ({exc})", False, True)
        return _Absent(f"plant did not take: {exc}")


def planted_eval(mutate):
    return planted(_EVAL_PATH, mutate)


def planted_adapter(mutate):
    return planted(_ADAPTER_PATH, mutate)


def run_planted(module, trials=TRIALS, **stub_kwargs):
    """Drive a planted evaluation module's node, with its own counters read."""
    stub = ConverseStub(**stub_kwargs)
    saved = deps.set_overrides({deps.BEDROCK_ANTHROPIC_CLIENT: stub})
    try:
        with settings(
                MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
                MATCHING_PER_TRIAL_CALLS_ENABLED=True):
            result = drive(module.node_llm_classifier_evaluation, {
                "patient_data": PATIENT, "filtered_trials": trials,
                "llm_classifier_retries": 0, "mesh_filter_applied": True,
                "mesh_filter_skip_reason": "applied", "stage_timings": {}})
        return result, stub
    finally:
        deps.restore_overrides(saved)


# --- P1. THE CACHE PLACEMENT: the breakpoint moves into `messages` ---------
#
# A cachePoint in `messages` caches the PER-TRIAL text, which is different on
# every call by construction: a cache WRITE per request at a premium rate with
# no read to follow it, and no shared prefix cached at all.
_p1 = planted_adapter(lambda t: sub(
    t, '        system.append({"cachePoint": point})',
    '        kwargs_hack = point  # planted: breakpoint no longer on system',
    1))
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    _p1_kw = drive(_p1.build_converse_request, "SYS", "USER")
check("(P1) with the breakpoint removed from `system` there is NO checkpoint "
      "at all, so nothing is ever cached",
      json.dumps(_p1_kw).count("cachePoint") if isinstance(_p1_kw, dict)
      else _p1_kw, 0)
check("(P1 control) the shipped builder puts exactly one on `system`",
      [sorted(b) for b in _trial_kw["system"]], [["text"], ["cachePoint"]])

# --- P2. THE PREFIX DRIFTS: the warmup's system block stops matching -------
#
# THE FAILURE THIS MODELS IS THE WORST ONE IN THE DESIGN, because every request
# succeeds: the warmup warms a prefix the wave does not share, every wave call
# reports cacheRead 0, and -- without the confirmation -- the run reports an
# ordinary patient at fifteen times the input price.
_p2 = planted_adapter(lambda t: sub(
    t, '    system: List[Dict] = [{"text": system_prompt}]',
    '    system: List[Dict] = [{"text": system_prompt + ("!" if warmup else "")}]',
    1))
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC):
    _p2_warm = drive(_p2.build_converse_request, "SYS", ".", warmup=True)
    _p2_trial = drive(_p2.build_converse_request, "SYS", "USER")
check("(P2) a one-character drift makes the warmup's prefix differ from the "
      "wave's, which section 2a is the standing guard against",
      json.dumps(at(_p2_warm, "system")) == json.dumps(at(_p2_trial, "system")),
      False)
check("(P2 control) the shipped builder keeps them byte-identical",
      json.dumps(_warm_kw["system"]), json.dumps(_trial_kw["system"]))

# --- P3. THE FAILURE PATH: the confirmation stops being consulted ----------
_p3 = planted_eval(lambda t: sub(
    t, '''                    _confirm_cache_write(_warmup_response,
                                         WARMUP_SOURCE_WARMUP)''',
    '''                    pass  # planted: the write is never confirmed''', 1))
_p3_r, _p3_s = run_planted(_p3, warmup_read=0, warmup_write=0)
check("(P3) with the confirmation removed the whole wave goes out against a "
      "prefix the provider said it did not cache -- which is the cost leak "
      "this pass exists to close", len(_p3_s.wave_requests()), len(TRIALS))
check("(P3) ...and the patient is reported as an ORDINARY success, which is "
      "what makes the leak invisible", bool(at(_p3_r, "error")), False)
check("(P3 control) the shipped node issues ZERO trial calls on the same stub",
      len(_stub2.wave_requests()), 0)

# --- P4. `already_warm` is treated as a failure ----------------------------
#
# THE PLANT A CARELESS IMPLEMENTATION WOULD SHIP: "confirmed means it wrote".
# Every retried, resumed or resampled patient inside the TTL reads a prefix it
# did not write, and this arm fails all of them.
_p4 = planted_eval(lambda t: sub(
    t, 'CACHE_WRITE_CONFIRMED = (CACHE_WRITE_WROTE, CACHE_WRITE_ALREADY_WARM)',
    'CACHE_WRITE_CONFIRMED = (CACHE_WRITE_WROTE,)', 1))
_p4_r, _p4_s = run_planted(_p4, warmup_read=19000, warmup_write=0)
check("(P4) treating an already-warm prefix as a failure issues ZERO trial "
      "calls for a patient whose cache is in perfect shape",
      len(_p4_s.wave_requests()), 0)
check("(P4 control) the shipped node releases the wave on the same stub",
      len(_stub4.wave_requests()), len(TRIALS))

# --- P5. THE TOKEN ACCOUNTING: the disjointness is dropped -----------------
_p5 = planted_adapter(lambda t: sub(
    t, '    prompt_tokens = non_cached + (cache_read or 0) + (cache_write or 0)',
    '    prompt_tokens = non_cached  # planted: the naive rename', 1))
_p5_usage = drive(_p5._usage_block,
                  {"inputTokens": 1000, "outputTokens": 100,
                   "cacheReadInputTokens": 19000,
                   "cacheWriteInputTokens": 0})
check("(P5) the naive rename under-reports this one call's input by exactly "
      "the cached amount", at(_p5_usage, "prompt_tokens"), 1000)
_shipped_usage = drive(bac._usage_block,
                       {"inputTokens": 1000, "outputTokens": 100,
                        "cacheReadInputTokens": 19000,
                        "cacheWriteInputTokens": 0})
check("(P5 control) the shipped block applies AWS's own formula",
      at(_shipped_usage, "prompt_tokens"), 20000)
check("(P5) ...and the gap is 95% of the real input on this call, which is "
      "why it is a cost defect rather than a rounding one",
      at(_shipped_usage, "prompt_tokens") - at(_p5_usage, "prompt_tokens"),
      19000)

# --- P6. THE FALLBACK WRITER'S OUTCOME STOPS BEING CONFIRMED --------------
_p6 = planted_eval(lambda t: sub(
    t, '''                    elif not _confirm_cache_write(
                            _writer[1], WARMUP_SOURCE_FALLBACK_WRITER):''',
    '''                    elif False:  # planted: the writer is never confirmed''',
    1))
_p6_r, _p6_s = run_planted(_p6, warmup_raise=_REFUSAL, trial_read=0,
                           trial_write=0)
check("(P6) with the fallback's writer unconfirmed the rest of the wave goes "
      "out cold -- the door the fallback opens, re-opened",
      len(_p6_s.wave_requests()), len(TRIALS))
check("(P6 control) the shipped node issues exactly the writer and stops",
      len(_s6.wave_requests()), 1)

# --- P7. THE WAVE MISS IS ABSORBED ----------------------------------------
_p7 = planted_eval(lambda t: sub(
    t, '        if _per_trial_calls and per_trial_cache_is_confirmable() and not _cached:',
    '        if False:  # planted: the wave miss is absorbed', 1))
_p7_before = dict(_p7.PER_TRIAL_CACHE_READ_MISSES)
_p7_r, _p7_s = run_planted(_p7, read_for={_MISS_ID: 0})
check("(P7) an absorbed miss leaves NOTHING on the run-end report, which is "
      "the state the shipped code replaced",
      dict(_p7.PER_TRIAL_CACHE_READ_MISSES), _p7_before)
check("(P7 control) the shipped node counts it exactly once",
      _mc7, {_evaluation.CACHE_WRITE_REPORTED_NOTHING: 1})

# --- P8. THE MISS BECOMES FATAL -------------------------------------------
#
# THE OPPOSITE MISTAKE, AND IT IS THE MORE TEMPTING ONE: "surface, not absorb"
# read as "fail the patient". The call was issued, answered and BILLED, so
# discarding it spends the money twice and loses a judgement nobody doubts.
check("(P8) the shipped node KEEPS every verdict when a wave call misses",
      len(at(_r7, "evaluations") or []), len(TRIALS))
check("(P8) ...and does not fail the patient", bool(at(_r7, "error")), False)
check("(P8) ...while still recording the finding", sum(_mc7.values()), 1)

# --- P9. THE PARALLEL BOUND STOPS BEING PROVIDER-SPECIFIC -----------------
_p9_owner = planted(
    _CONFIG_PATH,
    lambda t: sub(
        t,
        '''    if (MATCHING_PROVIDER == MATCHING_PROVIDER_BEDROCK_ANTHROPIC
            and BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS is not None):
        return BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS''',
        '''    if False:  # planted: the provider override is ignored
        return BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS''', 1),
    package="oncotriage")
if not isinstance(_p9_owner, _Absent):
    _p9_owner.MATCHING_PROVIDER = _p9_owner.MATCHING_PROVIDER_BEDROCK_ANTHROPIC
    _p9_owner.BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS = 1
check("(P9) with the override ignored, an operator pacing a throttled AWS "
      "account gets the OpenAI bound instead and the pacing does nothing",
      drive(_p9_owner.per_trial_parallel_bound)
      if not isinstance(_p9_owner, _Absent) else _p9_owner,
      config.MATCHING_PER_TRIAL_MAX_PARALLEL_CALLS)
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC,
              BEDROCK_ANTHROPIC_MAX_PARALLEL_CALLS=1):
    check("(P9 control) the shipped owner honours it",
          config.per_trial_parallel_bound(), 1)


# ===========================================================================
# SECTION 11 — NOTHING LEAKED
# ===========================================================================

section("11. Every knob restored, every file unchanged, no AWS library loaded")

for _name, _value in _SHIPPED_AT_IMPORT.items():
    check(f"config.{_name} is back to what it was at import",
          getattr(config, _name), _value)

check("the knob sweep is derived rather than retyped, and it is not empty",
      len(_KNOB_NAMES) >= 10, True)

for _path, _sha in _SHA_BEFORE.items():
    check(f"{os.path.basename(_path)} is byte-unchanged; every plant went into "
          f"an in-memory copy",
          hashlib.sha256(open(_path, "rb").read()).hexdigest(), _sha)

check("no deps override survived this file",
      [k for k in deps.OVERRIDE_KEYS if deps.is_resolved(k)
       and deps.peek(k) is not deps.UNSET
       and k in (deps.BEDROCK_ANTHROPIC_CLIENT, deps.OPENAI_CLIENT)], [])

check("boto3 was never imported -- the request builder and the response "
      "translator use no AWS library, which is what makes this file runnable "
      "where boto3 is not installed", "boto3" in sys.modules, False)
check("...nor botocore", "botocore" in sys.modules, False)
check("...and no local model was loaded either",
      ("torch" in sys.modules, "transformers" in sys.modules), (False, False))


# ===========================================================================

print(f"\n{'=' * 74}")
print("RESULTS:")
print(f"  passed: {_RESULTS['passed']}")
print(f"  failed: {_RESULTS['failed']}")
if _FAILURES:
    print("\nFAILURES:")
    for _f in _FAILURES:
        print(f"  - {_f}")
print(f"{'=' * 74}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Sun Aug 31 2026

@author: ramyalsaffar
"""
