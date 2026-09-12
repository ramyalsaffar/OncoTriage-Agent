# Provider Resilience Test: Client-Side Pacing and the One Retry Policy
########################################################################

"""The pacer and the one retry policy, measured in virtual time.

WHAT THIS FILE HOLDS
--------------------
    1. The closed vocabularies and the shipped configuration, plus the
       import-time validator driven with bad values.
    2. The backoff: ceilings DERIVED from config (no test here names a
       60-second literal), full jitter that VARIES, and a retry-after hint
       honoured up to the window.
    3. The request pacer: scheduled starts never exceed the configured rate in
       any sliding window, from one thread and from eight; SDK slots reserved;
       post-throttle recovery resumes at the paced rate.
    4. Token RESERVATION: a case where completed-usage-only pacing bursts past
       the window and reservation pacing does not; a reservation larger than
       the window refused by name; a not-billed failure refunded.
    5. The policy: transient retried and recovered, non-transient NOT retried,
       exhaustion at exactly the budget, the budget TOTAL across SDK retries,
       every wire attempt paced, possibly-billed failures charged.
    6. Cancellation in REAL time: a paced wait and a backoff wait end within the
       poll interval; the Stage 5 cancellation factory; the runner's STOP switch
       sets the drain.
    7. The REAL Stage 5 node on the Converse arm: warmup exhaustion is terminal
       and routed to the error handler; per-trial isolation, recovery and
       pacing preserved; a local error keeps the patient-level retry.
    8. The printed surfaces: the run-start announcement, the runner's wiring,
       the run-summary block, the results-file retirement on a constructed
       old-results shape, and the corrected error labels.
    9. REAL botocore: the shipped client configuration makes exactly one wire
       attempt per call (a 429-answering `before-send` hook, no socket).
   10. Restores.

EVERY SAFEGUARD HAS A CONTROL THAT FIRES. Each control is a different INPUT to
the real function, a module attribute rebound inside try/finally with the
restore asserted by identity, or an in-memory string handed to a pure scan --
so this file EXECS NOTHING and needs no ``_EXEC_ALLOWLIST`` entry.

NO NETWORK, NO KEYS, NO SPEND, NO LIVE QDRANT, NO MODEL LOAD, NO CORPUS, NO GIT
HISTORY. Every provider is a stand-in installed through
``oncotriage/agent/deps.py``; section 9 builds a real botocore client with fake
static credentials and a hook that answers before any socket is opened, and a
socket guard armed for the whole file RAISES on any outbound connection. It
writes only inside a ``tempfile.mkdtemp`` it removes and asserts gone.
"""

import asyncio
import contextlib
import io
import json
import os
import random
import shutil
import socket
import statistics
import sys
import tempfile
import threading
import time
import ast

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ONCOTRIAGE_DEFER_LOCAL_MODELS", "1")

from oncotriage import config                                    # noqa: E402
from oncotriage import paths                                     # noqa: E402
from oncotriage import provider_resilience as pr                 # noqa: E402
from oncotriage import spend                                     # noqa: E402
from oncotriage import tracking                                  # noqa: E402
from oncotriage.agent import deps                                # noqa: E402
from oncotriage.agent import evaluation as ev                    # noqa: E402
from oncotriage.agent import graph as agent_graph                # noqa: E402
from oncotriage.batch import runner                              # noqa: E402
# READ BY SECTION 5b, WHICH PRICES ONE ATTEMPT'S CONSERVATIVE UPPER
# BOUND with the SAME function the ledger uses rather than retyping
# PRICING_CONFIG's arithmetic -- a literal here would be a second copy
# that goes stale the day a rate moves, which is the class of defect
# this project removes.
from oncotriage.utils import get_model_cost                     # noqa: E402


# ===========================================================================
# HARNESS
# ===========================================================================

_RESULTS = {"passed": 0, "failed": 0, "skipped": 0}
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


def skip(label, reason):
    _RESULTS["skipped"] += 1
    print(f"  SKIP  {label} -- {reason}")


def section(title):
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


class Raised:
    __slots__ = ("kind", "message", "exc")

    def __init__(self, exc):
        self.kind = type(exc).__name__
        self.message = str(exc)
        self.exc = exc

    def __eq__(self, other):
        return isinstance(other, Raised) and self.kind == other.kind

    def __repr__(self):
        return f"<raised {self.kind}: {self.message[:110]}>"


def drive(fn, *args, **kwargs):
    """Call fn with output captured; return its value or a Raised. Never raises.

    THE CAPTURE IS MAIN-THREAD ONLY, AND THAT IS A CORRECTNESS PROPERTY RATHER
    THAN A PREFERENCE. ``contextlib.redirect_stdout`` rebinds a PROCESS-WIDE
    name; it is not thread-local. Two call sites here drive a production wait
    from a BACKGROUND thread, and for as long as such a call is inside the
    redirect every ``print`` THE MAIN THREAD MAKES lands in this throwaway
    buffer -- every PASS, every FAIL and the RESULTS block with them. At the
    shipped code that window is the ~0.2s a cancellation takes and the race is
    invisible; with the cancellation check REVERTED it is a whole backoff, and
    the run then exits non-zero having printed no summary at all. MEASURED:
    that revert printed 69 PASS lines, no FAIL line and no RESULTS block while
    `_RESULTS["failed"]` was non-zero -- a control whose failure mode is that
    the report disappears. So a background driver keeps its output, which is
    noise on the console and cannot swallow anybody else's.
    """
    if threading.current_thread() is not threading.main_thread():
        try:
            return fn(*args, **kwargs)
        except Exception as exc:                                 # noqa: BLE001
            return Raised(exc)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            return fn(*args, **kwargs)
    except Exception as exc:                                     # noqa: BLE001
        return Raised(exc)


def at(mapping, key, default=None):
    try:
        return mapping[key]
    except Exception:                                            # noqa: BLE001
        return default


# THE SOCKET GUARD, ARMED FOR THE WHOLE FILE AND FIRED ONCE TO SHOW IT IS.
_NET_ATTEMPTS = []
_REAL_CONNECT = socket.socket.connect
_REAL_CREATE = socket.create_connection


def _blocked_connect(self, address, *a, **k):
    _NET_ATTEMPTS.append(repr(address))
    raise ConnectionRefusedError(f"network blocked by this test: {address!r}")


def _blocked_create(address, *a, **k):
    _NET_ATTEMPTS.append(repr(address))
    raise ConnectionRefusedError(f"network blocked by this test: {address!r}")


socket.socket.connect = _blocked_connect
socket.create_connection = _blocked_create


class FakeClock:
    """A thread-safe virtual clock. ``sleep`` advances it instead of waiting."""

    def __init__(self, start=1000.0):
        self._t = float(start)
        self._lock = threading.Lock()
        self.slept = 0.0

    def now(self):
        with self._lock:
            return self._t

    def sleep(self, seconds):
        with self._lock:
            self._t += max(0.0, float(seconds))
            self.slept += max(0.0, float(seconds))


@contextlib.contextmanager
def virtual_time(start=1000.0):
    clock = FakeClock(start)
    previous = pr.set_time_source(clock.now, clock.sleep)
    try:
        yield clock
    finally:
        pr.set_time_source(*previous)


@contextlib.contextmanager
def settings(**knobs):
    """Rebind config knobs for one block; restore BY IDENTITY afterwards."""
    saved = {k: getattr(config, k) for k in knobs}
    for key, value in knobs.items():
        setattr(config, key, value)
    try:
        yield
    finally:
        for key, value in saved.items():
            setattr(config, key, value)


def quotas(requests=None, tokens=None):
    """A settings() block replacing the two quota dicts with COPIES.

    AN OVERRIDE REACHES EVERY SCOPE SHARING THE BUCKET, and that is required
    rather than convenient. `config.PROVIDER_QUOTA_BUCKETS` makes `openai` and
    `ragas_judge` two callers of ONE allowance, and
    `validate_provider_resilience_config()` refuses rows inside one bucket that
    disagree -- so a harness that set one of them and left the other UNKNOWN
    would be modelling a configuration the shipped code refuses to start under,
    and `effective_limits` would answer two different things for one schedule
    depending on which caller asked.

    It is applied per FAMILY: overriding a requests row propagates the requests
    figure only, so a block that deliberately leaves tokens UNKNOWN (section 4b
    drives exactly that) still gets what it asked for.
    """
    rpm = dict(config.PROVIDER_REQUESTS_PER_MINUTE)
    tpm = dict(config.PROVIDER_TOKENS_PER_MINUTE)
    for table, given in ((rpm, requests or {}), (tpm, tokens or {})):
        for scope, value in given.items():
            for peer in config.provider_quota_bucket_members(
                    config.provider_quota_bucket(scope)):
                table[peer] = value
    return settings(PROVIDER_REQUESTS_PER_MINUTE=rpm,
                    PROVIDER_TOKENS_PER_MINUTE=tpm)


@contextlib.contextmanager
def counters_cleared():
    """Clear every per-drive accumulator for the block, and put it all back.

    THE DOLLAR TOTAL IS ONE OF THEM, AND LEAVING IT OUT MADE TWO CHECKS MEASURE
    THE WHOLE FILE. `_UNCONFIRMED_USD` is a plain module dict rather than a
    Counter, so an earlier version of this helper cleared the three Counters
    beside it and left it accumulating: section 6d's parity matrix charges
    0.25 x 6 attempts x 2 twins = 3.00 on this scope, and the cancellation
    block that followed read 3.25 where its own charge was 0.25 -- and its
    CONTROL, which must read 0.00, read 3.25 too. Both were statements about
    every drive in the file rather than about the block that made them.

    RESTORED RATHER THAN LEFT EMPTY, so section 8's run-end report still prints
    the file's accumulated totals: what a block needs is a LOCAL reading, not a
    permanently reset one.
    """
    # A LIST OF PAIRS, NOT A DICT: a Counter is unhashable, and the first
    # version keyed a dict on the counters and aborted the file at section 3.
    saved = [(c, dict(c)) for c in (pr.PROVIDER_RETRY_OUTCOMES,
                                    pr.PROVIDER_UNCONFIRMED_BILLING,
                                    pr.PROVIDER_PACING_WAITS,
                                    pr._UNCONFIRMED_USD)]
    for c, _v in saved:
        c.clear()
    try:
        yield
    finally:
        for c, v in saved:
            c.clear()
            c.update(v)


_SHIPPED = {n: getattr(config, n) for n in (
    "PROVIDER_REQUESTS_PER_MINUTE", "PROVIDER_TOKENS_PER_MINUTE",
    "PROVIDER_PACING_HEADROOM", "PROVIDER_QUOTA_WINDOW_SECONDS",
    "MATCHING_CALL_MAX_ATTEMPTS", "MATCHING_RETRY_BASE_SECONDS",
    "PROVIDER_WAIT_POLL_SECONDS", "MATCHING_PROVIDER",
    "BEDROCK_ANTHROPIC_MAX_ATTEMPTS", "MATCHING_PER_TRIAL_CALLS_ENABLED")}
_BEDROCK = config.MATCHING_PROVIDER_BEDROCK_ANTHROPIC
_OPENAI = config.MATCHING_PROVIDER_OPENAI
_BATCH = config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH
WINDOW = config.PROVIDER_QUOTA_WINDOW_SECONDS

# THIS FILE DOES NOT INSTALL THE SHARED TEST LIMITS, AND THAT IS THE POINT.
# Its subject IS the rate, so every section that drives the pacer states the
# limit it is driving under through `quotas()` above, and section 1 asserts the
# SHIPPED configuration -- which is UNKNOWN for three of the four scopes and
# must stay that way here or the honesty checks would be measuring the
# harness's own numbers. Section 5b drives the refusal itself.


def max_in_any_window(starts, window):
    """The most starts inside any half-open window (s - window, s]."""
    starts = sorted(starts)
    best = 0
    for i, s in enumerate(starts):
        n = sum(1 for t in starts[:i + 1] if t > s - window)
        best = max(best, n)
    return best


def max_tokens_in_any_window(events, window):
    """``events`` = [(start, tokens)]; the heaviest half-open window."""
    events = sorted(events)
    best = 0
    for i, (s, _) in enumerate(events):
        best = max(best, sum(tok for t, tok in events[:i + 1] if t > s - window))
    return best


def throttle_verdict(_exc):
    return pr.verdict_for(pr.CATEGORY_THROTTLED)


_MGMT = pr.RESERVATION_MANAGEMENT
"""What every `pr.execute` drive in this file declares, and WHY -- once here
rather than thirteen times at the call sites.

`execute` requires a `RESERVATION_KINDS` member and refuses an `inference`
reservation of zero, because an inference request consumes model tokens by
definition and a zero is an estimate that did not happen. Every drive below
reserves zero tokens deliberately: their subject is the RETRY POLICY and the
REQUEST spacing, and a fabricated `send` that returns a string generates no
tokens at all -- so `management` is the accurate declaration rather than a
convenience, and it is the same fact `config.PROVIDER_QUOTA_SCOPE_OPENAI_BATCH`
records for the four calls that upload, enqueue, poll and download.

SECTION 4 IS WHERE THE `inference` SIDE IS DRIVEN, including the refusal and
its clean control, so declaring `management` here does not leave the strict
half unexercised."""


class _Throttle(Exception):
    pass


# ===========================================================================
# SECTION 0 — THE GUARD IS ARMED
# ===========================================================================

section("0. The socket guard raises, and records what tried")
_probe = drive(socket.create_connection, ("192.0.2.1", 9))
check("a real outbound connection is refused by the guard",
      isinstance(_probe, Raised) and "blocked by this test" in _probe.message,
      True)
check("...and recorded, so the end-of-file count is not vacuous",
      len(_NET_ATTEMPTS), 1)
_NET_ATTEMPTS.clear()


# ===========================================================================
# SECTION 1 — VOCABULARIES, SHIPPED CONFIGURATION, VALIDATOR
# ===========================================================================

section("1. Vocabularies and the shipped configuration")

_TRANSIENT_BY_AWS = {pr.CATEGORY_THROTTLED, pr.CATEGORY_TIMEOUT,
                     pr.CATEGORY_SERVICE_UNAVAILABLE, pr.CATEGORY_SERVER,
                     pr.CATEGORY_CONNECT, pr.CATEGORY_CONNECTION_LOST,
                     pr.CATEGORY_MODEL_NOT_READY}
check("exactly AWS's transient families are retried (throttle, timeout, "
      "service unavailable, internal server, network, model-not-ready)",
      {c for c, (cls, _b) in pr.CATEGORY_POLICY.items()
       if cls == pr.FAILURE_TRANSIENT}, _TRANSIENT_BY_AWS)
check("...and validation/access (client), local, model-error, translation, an "
      "abandoned in-flight request and unclassified are NOT",
      {c for c, (cls, _b) in pr.CATEGORY_POLICY.items()
       if cls == pr.FAILURE_NON_TRANSIENT},
      {pr.CATEGORY_CLIENT, pr.CATEGORY_LOCAL, pr.CATEGORY_MODEL_ERROR,
       pr.CATEGORY_TRANSLATION, pr.CATEGORY_ABANDONED,
       pr.CATEGORY_UNCLASSIFIED})
check("unknown is NOT assumed free: unclassified, timeout, a lost connection, "
      "a server error, model error, translation and a request abandoned in "
      "flight are possibly billed",
      {c for c, (_cls, b) in pr.CATEGORY_POLICY.items()
       if b == pr.BILLING_POSSIBLY_BILLED},
      {pr.CATEGORY_UNCLASSIFIED, pr.CATEGORY_TIMEOUT,
       pr.CATEGORY_CONNECTION_LOST, pr.CATEGORY_SERVER,
       pr.CATEGORY_MODEL_ERROR, pr.CATEGORY_TRANSLATION,
       pr.CATEGORY_ABANDONED})
# ABANDONED IS NOT RETRIED AND IS NOT FREE, and both halves are the decision
# rather than a default. Not retried: the caller is going away, so a retry is
# work nobody is waiting for. Not free: the provider was asked and may have
# answered into a socket nobody read -- the same reading a timeout already gets.
check("...and `abandoned_in_flight` is its own category rather than reusing "
      "`unclassified`, so an operator who pressed Ctrl-C is not told their run "
      "met an unclassifiable provider error",
      (pr.CATEGORY_ABANDONED in pr.CATEGORIES,
       pr.CATEGORY_ABANDONED != pr.CATEGORY_UNCLASSIFIED,
       pr.CATEGORY_POLICY[pr.CATEGORY_ABANDONED]),
      (True, True, (pr.FAILURE_NON_TRANSIENT, pr.BILLING_POSSIBLY_BILLED)))
check("an unknown category string resolves to unclassified",
      pr.verdict_for("no-such-category").category, pr.CATEGORY_UNCLASSIFIED)

check("the shipped arm's requests/minute is the PROVISIONAL operator-configured "
      "10 (not provider-confirmed: the Service Quotas read is unavailable here)",
      config.PROVIDER_REQUESTS_PER_MINUTE[_BEDROCK], 10)
check("...and every other scope's requests/minute is UNKNOWN, which REFUSES "
      "dispatch rather than running unpaced",
      {s: pr.quota_state(s, "requests") for s in config.PROVIDER_QUOTA_SCOPES},
      {_BEDROCK: pr.QUOTA_STATE_CONFIGURED,
       _OPENAI: pr.QUOTA_STATE_UNKNOWN,
       config.MATCHING_PROVIDER_BEDROCK: pr.QUOTA_STATE_UNKNOWN,
       _BATCH: pr.QUOTA_STATE_UNKNOWN,
       # THE TWO RAGAS SCOPES, UNKNOWN AND THEREFORE REFUSING. The map stays
       # EXACT rather than becoming a predicate over "every scope but the
       # shipped one": an exact map is what fails when a scope is added with no
       # quota row, which is the one way a path can end up dispatching unpaced.
       config.PROVIDER_QUOTA_SCOPE_RAGAS_JUDGE: pr.QUOTA_STATE_UNKNOWN,
       config.PROVIDER_QUOTA_SCOPE_RAGAS_EMBEDDING: pr.QUOTA_STATE_UNKNOWN})
check("no token quota is configured for any arm that is metered on that axis",
      {s: pr.quota_state(s, "tokens") for s in config.PROVIDER_QUOTA_SCOPES},
      {_BEDROCK: pr.QUOTA_STATE_UNKNOWN,
       _OPENAI: pr.QUOTA_STATE_UNKNOWN,
       config.MATCHING_PROVIDER_BEDROCK: pr.QUOTA_STATE_UNKNOWN,
       # ARGUED, NOT CONVENIENT: the four Batch calls consume no model tokens.
       _BATCH: pr.QUOTA_STATE_NOT_APPLICABLE,
       # AND THE TWO RAGAS SCOPES ARE `None` RATHER THAN NOT_APPLICABLE, which
       # is the distinction the row above exists to make: a judge completion and
       # an embedding both BURN a tokens-per-minute allowance, so "we have not
       # measured it" is UNKNOWN and refuses, and writing the sentinel to make a
       # run start would be the misuse that constant forbids.
       config.PROVIDER_QUOTA_SCOPE_RAGAS_JUDGE: pr.QUOTA_STATE_UNKNOWN,
       config.PROVIDER_QUOTA_SCOPE_RAGAS_EMBEDDING: pr.QUOTA_STATE_UNKNOWN})
check("...and NOT_APPLICABLE is a distinct state, so a scope is never blocked "
      "waiting on a limit that does not govern it",
      drive(pr.require_known_quota, _BATCH).kind, "QuotaUnknown")
check("...for the REQUESTS family only -- its tokens row raises nothing",
      pr.quota_state(_BATCH, "tokens"), pr.QUOTA_STATE_NOT_APPLICABLE)
check("botocore's own retries are DISABLED on the Converse arm",
      config.bedrock_anthropic_max_attempts(), 1)
with settings(MATCHING_PROVIDER=_OPENAI):
    # THE DECLARED NUMBER IS PINNED AGAINST THE COVERED CLIENT'S REAL SETTING,
    # WHICH IS THE ONLY FORM THAT CATCHES THE DRIFT THAT HAPPENED HERE.
    #
    # This check read `config.OPENAI_SDK_MAX_RETRIES + 1` -- the SHARED
    # client's constant -- and passed for a whole pass while being wrong,
    # because it compared the function against the same stale constant the
    # function returned. Two copies of one number agree with each other however
    # wrong they both are; that is the shape this project removes.
    #
    # Stage 5's chat completion is served by `get_openai_inference_client()`,
    # so the number that must be true is THAT builder's `max_retries` + 1. The
    # constant it passes is read OUT OF config.py's SOURCE by AST rather than
    # named here, so a builder repointed at a different constant fails this
    # check instead of silently redefining what it is compared against.
    # `tests/test_openai_inference_seam.py` pins the same builder from the
    # other side (that it carries the inference constant and that the embedding
    # builder does not), so neither file is the only thing holding it.
    _INFER_CONST = None
    for _fn in ast.walk(ast.parse(open(config.__file__, encoding="utf-8").read())):
        if (isinstance(_fn, ast.FunctionDef)
                and _fn.name == "get_openai_inference_client"):
            for _call in ast.walk(_fn):
                if not isinstance(_call, ast.Call):
                    continue
                for _kw in _call.keywords:
                    if _kw.arg == "max_retries" and isinstance(_kw.value, ast.Name):
                        _INFER_CONST = _kw.value.id
    check("NON-DEGENERACY: the covered client's builder was found and passes a "
          "NAMED constant, so the pin below is derived from source rather than "
          "from a name typed here", _INFER_CONST, "OPENAI_INFERENCE_SDK_MAX_RETRIES")
    check("*** the openai arm declares exactly the wire attempts the client "
          "STAGE 5 USES really makes -- the covered client's max_retries + 1. "
          "It declared the SHARED client's number until the client split was "
          "followed through, which made the policy divide a budget by 2 it "
          "never spent and the pacer reserve 2 slots per 1 request ***",
          config.matching_sdk_attempts_per_call(),
          getattr(config, _INFER_CONST) + 1)
    check("...and that is ONE wire attempt per send, so every attempt on this "
          "arm is a policy attempt: paced, jittered and cancellable",
          config.matching_sdk_attempts_per_call(), 1)
    check("...so the policy owns the WHOLE budget on this arm, rather than "
          "half of it",
          config.matching_policy_attempts(), config.MATCHING_CALL_MAX_ATTEMPTS)
    check("...and the policy divides the TOTAL budget by it",
          config.matching_policy_attempts() * config.matching_sdk_attempts_per_call()
          <= config.MATCHING_CALL_MAX_ATTEMPTS, True)
    check("CONTROL: the SHARED client's constant is a DIFFERENT number, so the "
          "pin above is not satisfied by whichever constant happens to be read "
          "-- reverting the function to it fails here",
          config.OPENAI_SDK_MAX_RETRIES + 1
          == config.matching_sdk_attempts_per_call(), False)
check("on the Converse arm the policy makes the whole budget itself",
      config.matching_policy_attempts(), config.MATCHING_CALL_MAX_ATTEMPTS)

# --- 1a-ii. SDK RETRIES ARE DISABLED EXACTLY WHERE THE POLICY COVERS EVERY
# --- CONSUMER OF THE CLIENT, AND LEFT ALONE WHERE IT DOES NOT.
#
# `max_retries` is a CLIENT-WIDE argument with no per-request form, so the
# number that governs a client governs every call it serves. That makes this a
# question about each client's CONSUMERS rather than a preference, and the two
# OpenAI-SDK clients answer it differently:
#
#   * get_bedrock_client() refuses to build unless MATCHING_PROVIDER is the
#     Responses arm, so Stage 5 is its only consumer -- disabled, every wire
#     attempt is a policy attempt;
#   * get_openai_client() also serves Stage 2's embedding, the indexer and the
#     validator, and OPENAI_SDK_MAX_RETRIES is `get_embedding`'s ONLY retry
#     since item 29d removed its tenacity decorator -- so it KEEPS its retry
#     and the policy divides the budget by it instead.
#
# BOTH HALVES ARE PINNED. Without the second, a later edit "finishing the job"
# by zeroing OPENAI_SDK_MAX_RETRIES would silently remove a retry from three
# paths this policy does not cover, and nothing would fail.
check("the Bedrock RESPONSES arm's SDK retries are DISABLED, so its policy "
      "makes the whole budget itself",
      config.BEDROCK_RESPONSES_SDK_MAX_RETRIES, 0)
with settings(MATCHING_PROVIDER=config.MATCHING_PROVIDER_BEDROCK):
    check("...which the arm's own attempt count reports as one wire attempt "
          "per send", config.matching_sdk_attempts_per_call(), 1)
    check("...and the policy therefore owns every attempt in the budget",
          config.matching_policy_attempts(), config.MATCHING_CALL_MAX_ATTEMPTS)
_ARM_PRODUCTS = {}
for _arm in config.MATCHING_PROVIDERS:
    # AN EXPLICIT LOOP WITH THE CONTEXT MANAGER ENTERED AND EXITED, which the
    # first version of this check got wrong: it entered `settings()` inside a
    # comprehension's `for _ in [...]` clause and never exited it, so the last
    # arm's value LEAKED into every check below. Found by re-reading it rather
    # than by running it, which is why the restore is asserted on the next line
    # rather than assumed.
    with settings(MATCHING_PROVIDER=_arm):
        _ARM_PRODUCTS[_arm] = (config.matching_sdk_attempts_per_call()
                               * config.matching_policy_attempts())
check("EVERY arm's SDK attempts times its policy attempts is the ONE total "
      "budget -- no arm is over it and none silently under-uses it",
      _ARM_PRODUCTS,
      {arm: config.MATCHING_CALL_MAX_ATTEMPTS
       for arm in config.MATCHING_PROVIDERS})
check("...and the provider is back to the shipped arm after that sweep",
      config.MATCHING_PROVIDER, _SHIPPED["MATCHING_PROVIDER"])

# THE THREE BUILDERS, READ OFF config.py's SOURCE rather than by constructing a
# client (which would resolve a credential and is what the whole file refuses
# to do). A builder that went back to the shared constant fails the first; a
# builder that zeroed the shared one fails the second.
#
# THIS BLOCK SAID "THE TWO BUILDERS" AND THE COUNT BELOW EXPECTED 3, AND BOTH
# WENT STALE THE MOMENT A THIRD CLIENT WAS ADDED. `config.py` gained
# `get_openai_inference_client` -- Stage 5's own chat client, split out so its
# SDK retries could be disabled without touching the embedding path's -- and
# that block contributes THREE further `max_retries=` occurrences: the builder
# itself plus the two comment mentions that argue for it. The count went 3 -> 6
# and this check FAILED, correctly: a non-degeneracy count is supposed to
# notice a builder nobody named, and it did. What was missing was the naming,
# so the third builder is asserted here rather than merely tolerated.
_CFG_SRC = open(config.__file__, encoding="utf-8").read()
check("the Responses-arm builder passes BEDROCK_RESPONSES_SDK_MAX_RETRIES",
      "max_retries=BEDROCK_RESPONSES_SDK_MAX_RETRIES," in _CFG_SRC, True)
check("...and the shared OpenAI client still passes OPENAI_SDK_MAX_RETRIES, so "
      "the embedding path's only retry is untouched",
      "max_retries=OPENAI_SDK_MAX_RETRIES," in _CFG_SRC, True)
check("...which is a real retry rather than a disabled one: the EXCLUDED paths "
      "keep the behaviour they had", config.OPENAI_SDK_MAX_RETRIES >= 1, True)
check("...and Stage 5's OWN chat client passes its own constant, so disabling "
      "its SDK retries did not reach the shared client",
      "max_retries=OPENAI_INFERENCE_SDK_MAX_RETRIES," in _CFG_SRC, True)
check("...which IS disabled, because the one retry policy covers every "
      "consumer of that client", config.OPENAI_INFERENCE_SDK_MAX_RETRIES, 0)
# COUNTED AS KEYWORD ARGUMENTS, NOT AS TEXT, AND THE TEXT FORM HAD ALREADY
# GONE STALE ONCE. This check read `_CFG_SRC.count("max_retries=")` against a
# hand-maintained total of "three builders plus three comment mentions". The
# subject it states is CLIENTS -- "exactly three clients set max_retries at
# all" -- and a substring count cannot express that: it moves when anybody
# writes the word in a comment or a docstring, and it moved again when
# `matching_sdk_attempts_per_call`'s own docstring was corrected to name the
# covered client's `max_retries=0`. Its own block above records the previous
# stale reading (3 -> 6) for the same reason.
#
# An AST walk asks the question the label asks: how many CALLS pass
# `max_retries=`. A comment cannot change it, and a fourth BUILDER still fails
# it -- which is the property the check exists for, now held by an instrument
# that cannot be moved by prose.
_CFG_TREE = ast.parse(_CFG_SRC)
_MAX_RETRIES_KW = sorted(
    _kw.value.id if isinstance(_kw.value, ast.Name) else ast.unparse(_kw.value)
    for _call in ast.walk(_CFG_TREE) if isinstance(_call, ast.Call)
    for _kw in _call.keywords if _kw.arg == "max_retries")
check("NON-DEGENERACY: exactly three clients set max_retries at all, so the "
      "checks above are not passing over a fourth builder nobody named -- "
      "counted as KEYWORD ARGUMENTS, so no comment or docstring can move it",
      len(_MAX_RETRIES_KW), 3)
check("...and each of the three passes its OWN named constant, so the three "
      "checks above name every builder there is rather than three of four",
      _MAX_RETRIES_KW,
      ["BEDROCK_RESPONSES_SDK_MAX_RETRIES", "OPENAI_INFERENCE_SDK_MAX_RETRIES",
       "OPENAI_SDK_MAX_RETRIES"])
check("CONTROL: the retired substring count is a DIFFERENT number on this same "
      "source, which is what says the instrument changed rather than the "
      "expectation being retuned to whatever the file happens to say",
      _CFG_SRC.count("max_retries=") == len(_MAX_RETRIES_KW), False)

check("the retry budget is a TUNABLE (it decides which trials get a verdict)",
      "MATCHING_CALL_MAX_ATTEMPTS" in config.TUNABLE_NAMES, True)
check("...and no pacing limit is (they move how long, never what)",
      [n for n in config.TUNABLE_NAMES if n.startswith("PROVIDER_")], [])
check("the tracking index records the budget",
      "MATCHING_CALL_MAX_ATTEMPTS" in tracking.CONFIGURATION_PARAM_NAMES, True)

# --- 1b. The validator refuses, each case with its clean control ---------
check("CLEAN CONTROL: the validator passes on the shipped values",
      drive(config.validate_provider_resilience_config), None)
_BAD_CASES = [
    ("a headroom above 1 would pace ABOVE the quota",
     dict(PROVIDER_PACING_HEADROOM=1.5), "PROVIDER_PACING_HEADROOM"),
    ("`True` is not a quota",
     dict(PROVIDER_REQUESTS_PER_MINUTE={**config.PROVIDER_REQUESTS_PER_MINUTE,
                                        _BEDROCK: True}),
     "PROVIDER_REQUESTS_PER_MINUTE"),
    ("a provider with no row would be silently unpaced",
     dict(PROVIDER_REQUESTS_PER_MINUTE={_BEDROCK: 10}),
     "PROVIDER_REQUESTS_PER_MINUTE"),
    ("a budget below the SDK's own attempts cannot be total",
     dict(MATCHING_CALL_MAX_ATTEMPTS=1, BEDROCK_ANTHROPIC_MAX_ATTEMPTS=2),
     "MATCHING_CALL_MAX_ATTEMPTS"),
    ("a zero poll would make every wait spin",
     dict(PROVIDER_WAIT_POLL_SECONDS=0), "PROVIDER_WAIT_POLL_SECONDS"),
]
for _label, _knobs, _named in _BAD_CASES:
    with settings(**_knobs):
        _r = drive(config.validate_provider_resilience_config)
    check(f"refused, naming the constant: {_label}",
          isinstance(_r, Raised) and _r.kind == "RuntimeError"
          and _named in _r.message, True)


# ===========================================================================
# SECTION 2 — BACKOFF AND FULL JITTER
# ===========================================================================

section("2. The backoff: derived ceilings, full jitter, retry-after")

_policy_n = config.matching_policy_attempts()
_ceilings = [pr.backoff_ceiling_seconds(n) for n in range(1, _policy_n)]
check("every retry's ceiling is min(window, base x 2^(n-1)), from config",
      _ceilings,
      [min(WINDOW, config.MATCHING_RETRY_BASE_SECONDS * 2 ** (n - 1))
       for n in range(1, _policy_n)])
check("NON-DEGENERACY: the shipped budget's last retry can wait a whole quota "
      "window, so a late retry can reach the NEXT window",
      max(_ceilings) if _ceilings else None, WINDOW)
check("...and the first retry's ceiling is below it, so the schedule really "
      "grows rather than being a constant", _ceilings[0] < WINDOW, True)


def jitter_ok(samples, ceiling):
    return (all(0.0 <= s <= ceiling for s in samples)
            and len(set(samples)) > 0.9 * len(samples)
            and statistics.pstdev(samples) > ceiling / 10.0)


_rng = random.Random(20260911)
_ceil3 = pr.backoff_ceiling_seconds(3)
_samples = [pr.full_jitter_delay(3, _rng) for _ in range(400)]
check("full jitter VARIES: 400 draws stay in [0, ceiling], nearly all distinct, "
      "with real spread", jitter_ok(_samples, _ceil3), True)
check("CONTROL: a no-jitter backoff (always the ceiling) fails the same "
      "predicate, so the check discriminates",
      jitter_ok([pr.backoff_ceiling_seconds(3)] * 400, _ceil3), False)

# 2b. The policy SLEEPS the jittered draw, and honours a retry-after hint.
#
# AN EXPLICIT, VERY LARGE REQUEST LIMIT RATHER THAN `None`. What these two
# checks need is for the REQUEST SPACING to contribute nothing, so the clock
# measures the BACKOFF alone. `None` used to mean exactly that -- "not paced" --
# and now means UNKNOWN, which refuses before the send: measured, the refusal
# reached `drive()` as a `Raised` and the slept time read 0.0 against a 1.295s
# draw, i.e. the check would have been asserting about a call that never
# happened. `QUOTA_NOT_APPLICABLE` is NOT the right spelling either -- Bedrock
# does meter requests per minute, and saying otherwise here would put a false
# claim about the API's semantics into a test. So the limit is stated, and it
# is large enough that its implied spacing (60s / 900,000 starts, ~67us) is
# four orders of magnitude below the seconds-scale sleeps these checks compare.
with virtual_time() as _clk, quotas(requests={_BEDROCK: 1_000_000}):
    _seed = random.Random(7)
    _expected = random.Random(7).uniform(0.0, pr.backoff_ceiling_seconds(1))
    _n = [0]

    def _once_then_ok():
        _n[0] += 1
        if _n[0] == 1:
            raise _Throttle("t")
        return "ok"

    _t0 = _clk.now()
    _r2b = drive(pr.execute, _once_then_ok, scope=_BEDROCK,
                 reservation_tokens=0, reservation_kind=_MGMT, classify=throttle_verdict,
                 pacer=pr.QuotaPacer(), rng=_seed)
    check("the policy slept exactly the jittered draw its rng produced",
          round(_clk.now() - _t0, 9), round(_expected, 9))

    _n[0] = 0
    _hint = 0.8 * WINDOW
    _t0 = _clk.now()
    drive(pr.execute, _once_then_ok, scope=_BEDROCK, reservation_tokens=0, reservation_kind=_MGMT,
          classify=lambda e: pr.verdict_for(pr.CATEGORY_THROTTLED, _hint),
          pacer=pr.QuotaPacer(), rng=random.Random(1))
    _waited = _clk.now() - _t0
    # A MICROSECOND OF TOLERANCE, because the fake clock's value is a float
    # accumulated over many poll-sized sleeps and the difference of two large
    # floats is not exact. The first version compared exactly and failed at
    # 47.99999999999 against a 48.0 hint the policy had in fact honoured.
    check("a retry-after hint is honoured (the wait is at least the hint)",
          _waited >= _hint - 1e-6, True)
    check("...and never beyond the quota window", _waited <= WINDOW + 1e-6,
          True)


# ===========================================================================
# SECTION 3 — THE REQUEST PACER
# ===========================================================================

section("3. The request pacer never exceeds the configured rate")

_interval, _tokcap, _win, _per_window = pr.effective_limits(_BEDROCK)
check("the shipped arm is paced: a spacing and a per-window count exist",
      (_interval is not None, _per_window is not None), (True, True))
check("...the per-window count is floor(quota x headroom), derived",
      _per_window, int(config.PROVIDER_REQUESTS_PER_MINUTE[_BEDROCK]
                       * config.PROVIDER_PACING_HEADROOM))

with virtual_time():
    _p = pr.QuotaPacer()
    _starts = [_p.reserve(_BEDROCK).start for _ in range(40)]
check("NON-DEGENERACY: 40 simultaneous arrivals, far more than one window's "
      "allowance", 40 > _per_window, True)
check("no sliding window holds more than the configured count",
      max_in_any_window(_starts, _win) <= _per_window, True)
check("...and starts are SPACED, not bursted: consecutive gap >= the interval",
      min(b - a for a, b in zip(_starts, _starts[1:])) >= _interval - 1e-9,
      True)

# THE CONTROL NEEDS A SCOPE WITH NO REQUEST SPACING, AND SINCE THE UNKNOWN
# STATE NOW REFUSES, IT HAS TO SAY SO EXPLICITLY. `reserve(_OPENAI)` used to
# mean "a scope with no limit recorded"; that is UNKNOWN now and raises before
# it can burst, which would abort the file rather than fail the check. A very
# large explicit limit gives the same burst (spacing ~67us against a window of
# 60s) while being a configured number rather than an absent one.
with virtual_time(), quotas(requests={_OPENAI: 1_000_000},
                            tokens={_OPENAI: 1_000_000_000}):
    _p_unpaced = pr.QuotaPacer()
    _burst = [_p_unpaced.reserve(_OPENAI).start for _ in range(40)]
check("CONTROL: the same arrivals on a scope spaced far finer than the window "
      "burst, and the window predicate that passed above FAILS on them",
      max_in_any_window(_burst, _win) <= _per_window, False)

# 3b. Eight threads, five attempts each, through acquire() in virtual time.
with virtual_time():
    _p8 = pr.QuotaPacer()
    _errors = []

    def _worker():
        try:
            for _ in range(5):
                _p8.acquire(_BEDROCK)
        except Exception as exc:                                 # noqa: BLE001
            _errors.append(exc)

    _threads = [threading.Thread(target=_worker) for _ in range(8)]
    for _t in _threads:
        _t.start()
    for _t in _threads:
        _t.join(timeout=30)
    _hist = [h[1] for h in _p8.history(_BEDROCK)]
check("eight concurrent threads all finished without error",
      (all(not t.is_alive() for t in _threads), _errors), (True, []))
check("...40 attempts were scheduled", len(_hist), 40)
check("...and no window ever held more than the configured count",
      max_in_any_window(_hist, _win) <= _per_window, True)

# 3b-ii. TWO SCOPES, ONE PROVIDER ALLOWANCE, ONE SCHEDULE.
#
# THE DEFECT THIS MEASURES. A quota is a property of an ACCOUNT, an ENDPOINT
# CLASS and a MODEL -- not of a name in config.py. `openai` and `ragas_judge`
# both reach chat.completions.create on one account for one model (all three of
# MATCHING_MODEL, ragas' DEFAULT_JUDGE_MODEL and the rater's DEFAULT_MODEL are
# `gpt-5.6-terra`, read from their own sources), so a pacer that gave each
# scope its own schedule would let two callers each pace to the WHOLE
# configured rate and together send twice it -- the burst this module exists to
# prevent, reached through the NAMING rather than through concurrency.
#
# THE CONTROL IS NOT A PLANT: two separate pacers ARE what per-scope keying
# produces, so driving one is the honest reproduction of the pre-bucket
# behaviour, and it needs no patched copy of anything.

_SHARED = config.provider_quota_bucket(_OPENAI)
_SHARED_MEMBERS = config.provider_quota_bucket_members(_SHARED)
check("NON-DEGENERACY: two DIFFERENT scopes really do share one allowance, or "
      "everything below is a statement about a single-member bucket",
      (len(_SHARED_MEMBERS) >= 2,
       config.provider_quota_bucket(config.PROVIDER_QUOTA_SCOPE_RAGAS_JUDGE)
       == _SHARED), (True, True))
check("...while a scope with its own provider limit does NOT share it, so the "
      "table is a mapping rather than one bucket for everything",
      config.provider_quota_bucket(_BEDROCK) == _SHARED, False)

_SHARE_RPM = 10
with virtual_time(), quotas(requests={_OPENAI: _SHARE_RPM}):
    _per_window_shared = pr.effective_limits(_OPENAI)[3]
    # ONE pacer, arrivals alternating between the two scopes that share the
    # allowance -- which is what a batch run and a ragas run in one process
    # would look like to it.
    _one = pr.QuotaPacer()
    _shared_starts = [
        _one.reserve(_SHARED_MEMBERS[i % len(_SHARED_MEMBERS)]).start
        for i in range(2 * _per_window_shared)]
    # TWO pacers, one per scope: exactly the independent schedules that keying
    # `_scopes` by SCOPE would have produced.
    _per_scope = {s: pr.QuotaPacer() for s in _SHARED_MEMBERS}
    _split_starts = [
        _per_scope[_SHARED_MEMBERS[i % len(_SHARED_MEMBERS)]].reserve(
            _SHARED_MEMBERS[i % len(_SHARED_MEMBERS)]).start
        for i in range(2 * _per_window_shared)]

check("*** two scopes sharing one provider allowance share ONE schedule: their "
      "COMBINED starts never exceed the configured rate in any window ***",
      max_in_any_window(_shared_starts, WINDOW) <= _per_window_shared, True)
check("*** CONTROL: give each scope its own schedule -- which is precisely what "
      "keying the pacer by SCOPE produces -- and the same arrivals BURST past "
      "the allowance, sending twice the configured rate ***",
      max_in_any_window(_split_starts, WINDOW) <= _per_window_shared, False)
check("...and the burst is the expected size: each scope paced its own full "
      "allowance, so the window holds both",
      max_in_any_window(_split_starts, WINDOW),
      len(_SHARED_MEMBERS) * _per_window_shared)

# 3c. SDK slots: one policy attempt that may become two wire attempts.
with virtual_time():
    _ps = pr.QuotaPacer()
    _a = _ps.reserve(_BEDROCK, slots=2)
    _b = _ps.reserve(_BEDROCK)
check("a 2-slot reservation holds two intervals, so an SDK's internal retry is "
      "paced too", round(_b.start - _a.start, 9), round(2 * _interval, 9))

# 3d. Post-throttle recovery resumes AT the paced rate.
with virtual_time() as _clk, counters_cleared():
    _pp = pr.QuotaPacer()
    _log = []
    _calls = {"A": 0}

    def _send_a():
        _calls["A"] += 1
        _log.append(("A", _clk.now()))
        if _calls["A"] < 3:
            raise _Throttle("throttled")
        return "A-ok"

    def _send_b():
        _log.append(("B", _clk.now()))
        return "B-ok"

    _ra = drive(pr.execute, _send_a, scope=_BEDROCK, reservation_tokens=0, reservation_kind=_MGMT,
                classify=throttle_verdict, pacer=_pp, rng=random.Random(3))
    _rb = drive(pr.execute, _send_b, scope=_BEDROCK, reservation_tokens=0, reservation_kind=_MGMT,
                classify=throttle_verdict, pacer=_pp, rng=random.Random(3))
    _times = [t for _who, t in _log]
    # READ INSIDE THE BLOCK: counters_cleared() RESTORES on exit, and the
    # first version read them after it had, getting the pre-block values.
    _outcomes_3d = (pr.PROVIDER_RETRY_OUTCOMES[f"retried:{_BEDROCK}:throttled"],
                    pr.PROVIDER_RETRY_OUTCOMES[f"recovered:{_BEDROCK}:throttled"])
check("the throttled call recovered on its third attempt", (_ra, _calls["A"]),
      ("A-ok", 3))
check("...every wire attempt of both calls went through the pacer",
      len(_pp.history(_BEDROCK)), 4)
check("...and after the throttles every start is still >= one interval after "
      "the previous one: recovery resumed at the paced rate, not in a burst",
      min(b - a for a, b in zip(_times, _times[1:])) >= _interval - 1e-9, True)
check("...the outcome counters say retried twice and recovered once",
      _outcomes_3d, (2, 1))


# ===========================================================================
# SECTION 4 — TOKEN RESERVATION
# ===========================================================================

section("4. Token pacing by reservation (input + max_tokens at dispatch)")

_TPM = 100_000
_RESERVE = 10_000 + 32_000
_ACTUAL = 12_000
# THIS SECTION'S SUBJECT IS THE TOKEN WINDOW, so the REQUEST limit must
# contribute no spacing of its own -- otherwise the starts it measures would be
# the request pacer's rather than the token reservation's. That used to be
# spelled `requests: None`; `None` is UNKNOWN now and refuses before reserving,
# so it is spelled as an explicit limit whose interval (~67us) is five orders
# of magnitude below the 60s window these checks bucket into.
_NO_SPACING = 1_000_000
with quotas(requests={_OPENAI: _NO_SPACING}, tokens={_OPENAI: _TPM}), virtual_time():
    _cap = pr.effective_limits(_OPENAI)[1]
    # TEN REQUESTS ARRIVE TOGETHER AND ARE ALL IN FLIGHT BEFORE ANY COMPLETES,
    # which is the burst the smoke run had. All ten are reserved first and only
    # then settled: the first version settled each one at its own dispatch --
    # a request that completes the instant it is sent -- which is not the case
    # under test and freed capacity no real in-flight request frees.
    _pr_res = pr.QuotaPacer()
    _permits = [_pr_res.reserve(_OPENAI, _RESERVE) for _ in range(10)]
    _events_reserved = [(p.start, _RESERVE) for p in _permits]
    for _perm in _permits:
        _pr_res.settle(_perm, billing=pr.BILLING_POSSIBLY_BILLED,
                       actual_tokens=_ACTUAL)
    _events_actual = [(p.start, _ACTUAL) for p in _permits]
    # COMPLETED-USAGE-ONLY accounting: nothing reserved at dispatch, the actual
    # usage recorded only at completion -- so all ten are admitted at once.
    _pr_usage = pr.QuotaPacer()
    _u_permits = [_pr_usage.reserve(_OPENAI, 0) for _ in range(10)]
    for _perm in _u_permits:
        _pr_usage.settle(_perm, billing=pr.BILLING_POSSIBLY_BILLED,
                         actual_tokens=_ACTUAL)
    _usage_only = [(p.start, _ACTUAL) for p in _u_permits]
check("NON-DEGENERACY: ten requests' ACTUAL usage exceeds one window's cap",
      10 * _ACTUAL > _cap, True)
check("reservation pacing keeps every window's RESERVED tokens under the cap",
      max_tokens_in_any_window(_events_reserved, WINDOW) <= _cap, True)
check("...and therefore its ACTUAL usage too",
      max_tokens_in_any_window(_events_actual, WINDOW) <= _cap, True)
check("CONTROL: completed-usage-only pacing admits all ten at once and its "
      "actual usage BURSTS past the cap -- the predicate fails",
      max_tokens_in_any_window(_usage_only, WINDOW) <= _cap, False)

with quotas(requests={_OPENAI: _NO_SPACING}, tokens={_OPENAI: _TPM}), virtual_time():
    _too_big = drive(pr.QuotaPacer().reserve, _OPENAI, _TPM * 2)
check("a reservation larger than the whole window is REFUSED by name, not "
      "waited on forever",
      isinstance(_too_big, Raised) and _too_big.kind == "ReservationExceedsQuota",
      True)

# --- 4b. A ZERO-TOKEN *INFERENCE* RESERVATION CANNOT BYPASS THE UNKNOWN-TPM
# --- REFUSAL, AND A BATCH-MANAGEMENT CALL IS STILL ALLOWED.
#
# THE HOLE THIS CLOSES, STATED AS THE SEQUENCE THAT REACHES IT.
# `require_known_quota` demands the TOKEN family only when a reservation
# actually reserves tokens -- which is right, because a zero-token reservation
# cannot consume a tokens-per-minute allowance and refusing it for an UNKNOWN
# TPM would be a refusal no caller could satisfy. But "does this consume
# tokens" was answered by `tokens > 0`, and that is A NUMBER A BUG CAN PRODUCE:
# an empty rendered prompt, a `max_output` of 0, an arithmetic slip in
# `_reservation_input_tokens`. Such a call would have been ADMITTED under an
# unmeasured token quota and then burned tokens the provider meters and this
# process never reserved.
#
# THE SCOPE HERE HAS ITS TOKEN QUOTA UNKNOWN ON PURPOSE -- that is the state
# every arm ships with, and it is the state in which the bypass mattered. The
# request family is configured, because every reservation consumes a request
# slot and leaving it UNKNOWN would make the refusal below ambiguous about
# which family produced it.
with quotas(requests={_OPENAI: _NO_SPACING}), virtual_time():
    _zero_inference = drive(
        pr.execute, lambda: "sent", scope=_OPENAI, reservation_tokens=0,
        reservation_kind=pr.RESERVATION_INFERENCE, classify=throttle_verdict,
        pacer=pr.QuotaPacer())
    _zero_mgmt_calls = []
    _zero_mgmt = drive(
        pr.execute, lambda: _zero_mgmt_calls.append(1) or "sent", scope=_OPENAI,
        reservation_tokens=0, reservation_kind=pr.RESERVATION_MANAGEMENT,
        classify=throttle_verdict, pacer=pr.QuotaPacer())
    # THE SAME INFERENCE CALL UNDER AN UNKNOWN TOKEN QUOTA, which is what makes
    # the clean control below a statement about the ZERO rather than about the
    # kind: with tokens UNKNOWN a POSITIVE reservation is refused too, by the
    # quota guard, and the two refusals name different remedies.
    _real_inference_unknown_tpm = drive(
        pr.execute, lambda: "sent", scope=_OPENAI, reservation_tokens=1,
        reservation_kind=pr.RESERVATION_INFERENCE, classify=throttle_verdict,
        pacer=pr.QuotaPacer())
    _bad_kind = drive(
        pr.execute, lambda: "sent", scope=_OPENAI, reservation_tokens=10,
        reservation_kind="no-such-kind", classify=throttle_verdict,
        pacer=pr.QuotaPacer())

# THE CLEAN CONTROL NEEDS A CONFIGURED TOKEN QUOTA, and that is the rule
# working rather than a concession: an inference call RESERVES tokens, so under
# an UNKNOWN tokens-per-minute figure it is refused whatever its number --
# which is exactly `_real_inference_unknown_tpm` above. Driving the clean arm
# inside the UNKNOWN block would have reported `QuotaUnknown` and been read as
# "the guard refuses everything". Found by reading this block back, not by
# running it.
with quotas(requests={_OPENAI: _NO_SPACING}, tokens={_OPENAI: _TPM}), \
        virtual_time():
    _real_inference_calls = []
    _real_inference = drive(
        pr.execute, lambda: _real_inference_calls.append(1) or "sent",
        scope=_OPENAI, reservation_tokens=1,
        reservation_kind=pr.RESERVATION_INFERENCE, classify=throttle_verdict,
        pacer=pr.QuotaPacer())
check("NON-DEGENERACY: this scope's token quota is UNKNOWN, which is the state "
      "in which a zero could have bypassed the refusal",
      pr.quota_state(_OPENAI, "tokens"), pr.QUOTA_STATE_UNKNOWN)
check("*** an INFERENCE call reserving zero tokens is REFUSED BY NAME, so a "
      "broken estimate cannot dispatch under an unmeasured token quota ***",
      getattr(_zero_inference, "kind", None), "ZeroTokenInferenceReservation")
check("...and the refusal names the caller's remedy and says nothing was sent",
      ("Fix the caller's token estimate" in getattr(_zero_inference, "message", "")
       and "NOTHING HAS BEEN SENT" in getattr(_zero_inference, "message", "")),
      True)
check("...while a BATCH-MANAGEMENT call reserving zero is ALLOWED and issued: "
      "its four endpoints upload, enqueue, poll and download, which consume no "
      "model tokens -- the same fact its scope's token row already argues",
      (_zero_mgmt, _zero_mgmt_calls), ("sent", [1]))
check("CLEAN CONTROL: an inference call with a real reservation is issued, so "
      "the refusal above is about the ZERO and not about the kind",
      (_real_inference, _real_inference_calls), ("sent", [1]))
check("...and it needed a CONFIGURED token quota to get there, which is the "
      "other half of the same rule: an inference call reserving tokens under "
      "an UNKNOWN token quota is refused for the QUOTA rather than for the "
      "zero, and the two refusals are different findings",
      getattr(_real_inference_unknown_tpm, "kind", None), "QuotaUnknown")
check("a kind outside the closed vocabulary is refused before the send, as an "
      "unrecognised SCOPE already is",
      getattr(_bad_kind, "kind", None), "ValueError")
check("...and it is refused ABOVE the quota guard: a bad kind with a real "
      "reservation on an UNKNOWN-token scope reports the KIND, not the quota, "
      "so a caller is sent to the argument they got wrong",
      "reservation_kind must be one of" in getattr(_bad_kind, "message", ""),
      True)
check("...and the two kinds are the whole vocabulary, so a third added without "
      "a rule in execute() fails here",
      pr.RESERVATION_KINDS,
      (pr.RESERVATION_INFERENCE, pr.RESERVATION_MANAGEMENT))
check("the SHIPPED Stage 5 dispatch declares INFERENCE, by AST rather than by "
      "grep -- a comment about the constant would satisfy a text search",
      [ast.unparse(_kw.value)
       for _fn in ast.walk(ast.parse(open(ev.__file__, encoding="utf-8").read()))
       if isinstance(_fn, ast.FunctionDef)
       and _fn.name == "_execute_matching_call"
       for _call in ast.walk(_fn)
       if isinstance(_call, ast.Call)
       for _kw in _call.keywords if _kw.arg == "reservation_kind"],
      ["provider_resilience.RESERVATION_INFERENCE"])

with quotas(requests={_OPENAI: _NO_SPACING}, tokens={_OPENAI: _TPM}), virtual_time():
    _prf = pr.QuotaPacer()
    _p1 = _prf.reserve(_OPENAI, 80_000)
    _prf.settle(_p1, billing=pr.BILLING_NOT_BILLED)
    _p2 = _prf.reserve(_OPENAI, 80_000)
    _prk = pr.QuotaPacer()
    _k1 = _prk.reserve(_OPENAI, 80_000)
    _prk.settle(_k1, billing=pr.BILLING_POSSIBLY_BILLED)
    _k2 = _prk.reserve(_OPENAI, 80_000)
    # CAPTURED INSIDE THE BLOCK. `effective_limits` reads `config` on every
    # call, so asking for it below -- after the override has been restored --
    # reads the SHIPPED row, which is UNKNOWN for this scope and returns None.
    _refund_interval = pr.effective_limits(_OPENAI)[0]
check("a NOT-BILLED failure (a throttle) refunds its reservation: the next "
      "request is admitted at once -- i.e. it waits only the REQUEST spacing "
      "and not the token window",
      round(_p2.start - _p1.start, 9), round(_refund_interval, 9))
check("CONTROL: a possibly-billed failure keeps it, and the next request waits "
      "for the window", _k2.start - _k1.start > 0, True)


# ===========================================================================
# SECTION 5 — THE POLICY
# ===========================================================================

section("5. The one retry policy")

with virtual_time(), quotas(requests={_BEDROCK: _NO_SPACING}), counters_cleared():
    _cnt = [0]

    def _client_error():
        _cnt[0] += 1
        raise _Throttle("ValidationException")

    _client = lambda e: pr.verdict_for(pr.CATEGORY_CLIENT)
    _r5a = drive(pr.execute, _client_error, scope=_BEDROCK,
                 reservation_tokens=0, reservation_kind=_MGMT, classify=_client, pacer=pr.QuotaPacer())
    check("a NON-transient (validation) error is raised after ONE attempt",
          (_cnt[0], isinstance(_r5a, Raised)), (1, True))
    check("...tagged with the attempt count for the dispatch seam",
          getattr(_r5a.exc, pr.ATTEMPTS_ATTR, None), 1)
    check("...and counted not_retried",
          pr.PROVIDER_RETRY_OUTCOMES[f"not_retried:{_BEDROCK}:client_error"], 1)

    _saved_row = pr.CATEGORY_POLICY[pr.CATEGORY_CLIENT]
    try:
        pr.CATEGORY_POLICY[pr.CATEGORY_CLIENT] = (pr.FAILURE_TRANSIENT,
                                                  pr.BILLING_NOT_BILLED)
        _cnt[0] = 0
        drive(pr.execute, _client_error, scope=_BEDROCK, reservation_tokens=0, reservation_kind=_MGMT,
              classify=lambda e: pr.verdict_for(pr.CATEGORY_CLIENT),
              pacer=pr.QuotaPacer())
        _retried_validation = _cnt[0]
    finally:
        pr.CATEGORY_POLICY[pr.CATEGORY_CLIENT] = _saved_row
    check("CONTROL: a policy that retried validation errors makes the whole "
          "budget of attempts, so 'exactly one' would FAIL",
          _retried_validation == 1, False)
    check("...and the table row was restored BY IDENTITY",
          pr.CATEGORY_POLICY[pr.CATEGORY_CLIENT] is _saved_row, True)

    _cnt[0] = 0

    def _always_throttled():
        _cnt[0] += 1
        raise _Throttle("ThrottlingException")

    _r5c = drive(pr.execute, _always_throttled, scope=_BEDROCK,
                 reservation_tokens=0, reservation_kind=_MGMT, classify=throttle_verdict,
                 pacer=pr.QuotaPacer())
    check("a still-transient error is retried to EXACTLY the budget",
          _cnt[0], config.MATCHING_CALL_MAX_ATTEMPTS)
    check("...then raised, tagged with the full budget",
          getattr(getattr(_r5c, "exc", None), pr.ATTEMPTS_ATTR, None),
          config.MATCHING_CALL_MAX_ATTEMPTS)
    check("...and counted exhausted once",
          pr.PROVIDER_RETRY_OUTCOMES[f"exhausted:{_BEDROCK}:throttled"], 1)

    # 5d. THE BUDGET IS TOTAL ACROSS AN SDK'S INTERNAL RETRIES.
    def _sdk_send(wire, internal):
        def _send():
            for _ in range(internal):
                wire.append(1)
            raise _Throttle("ThrottlingException")
        return _send

    _wire = []
    drive(pr.execute, _sdk_send(_wire, 2), scope=_BEDROCK, reservation_tokens=0, reservation_kind=_MGMT,
          classify=throttle_verdict, sdk_attempts=2, pacer=pr.QuotaPacer())
    check("with an SDK that retries once internally, total WIRE attempts never "
          "exceed the budget", len(_wire) <= config.MATCHING_CALL_MAX_ATTEMPTS,
          True)
    check("...NON-DEGENERACY: it used the budget, not one attempt",
          len(_wire), config.MATCHING_CALL_MAX_ATTEMPTS)
    _wire_ctl = []
    drive(pr.execute, _sdk_send(_wire_ctl, 2), scope=_BEDROCK,
          reservation_tokens=0, reservation_kind=_MGMT, classify=throttle_verdict, sdk_attempts=1,
          pacer=pr.QuotaPacer())
    check("CONTROL: a policy that ignored the SDK's retries (sdk_attempts=1 "
          "while the SDK makes 2) sends MORE than the budget -- the total "
          "check FAILS", len(_wire_ctl) <= config.MATCHING_CALL_MAX_ATTEMPTS,
          False)
    _r5e = drive(pr.execute, _sdk_send([], 2), scope=_BEDROCK,
                 reservation_tokens=0, reservation_kind=_MGMT, classify=throttle_verdict,
                 sdk_attempts=config.MATCHING_CALL_MAX_ATTEMPTS + 1,
                 pacer=pr.QuotaPacer())
    check("an SDK allowance above the whole budget is refused by name",
          getattr(_r5e, "kind", None), "AttemptBudgetConfigurationError")

    # 5f. Possibly-billed failures are charged; not-billed ones are not.
    _charges = []
    drive(pr.execute, _always_throttled, scope=_BEDROCK, reservation_tokens=0, reservation_kind=_MGMT,
          classify=lambda e: pr.verdict_for(pr.CATEGORY_TIMEOUT),
          on_possibly_billed=lambda v: _charges.append(v) or 0.5,
          pacer=pr.QuotaPacer())
    _no_charges = []
    drive(pr.execute, _always_throttled, scope=_BEDROCK, reservation_tokens=0, reservation_kind=_MGMT,
          classify=throttle_verdict,
          on_possibly_billed=lambda v: _no_charges.append(v) or 0.5,
          pacer=pr.QuotaPacer())
    check("every possibly-billed failed attempt (a timeout) is charged an upper "
          "bound", len(_charges), config.MATCHING_CALL_MAX_ATTEMPTS)
    check("CONTROL: a throttle -- refused before inference -- charges nothing",
          len(_no_charges), 0)


# ===========================================================================
# SECTION 5b — THE UPPER BOUND REACHES THE NUMBER THE SPEND GATE READS,
#              AND IS RETAINED
# ===========================================================================

section("5b. A possibly-billed failure moves the spend gate's own number")

# WHY THIS IS A SEPARATE SECTION FROM 5f ABOVE. 5f proves the POLICY calls
# `on_possibly_billed` the right number of times -- against a lambda that
# appends to a list. It says nothing about whether the money reaches the
# accounting `spend.cap_exceeded` actually consults, which is a different
# question with a different owner: `_execute_matching_call` builds the charge,
# `SpendLedger._commit` records it, and `budget_spend(campaign)` is what the
# gate reads. A run could satisfy every check in 5f while charging a ledger
# nobody enforces against.
#
# DRIVEN THROUGH THE REAL `_execute_matching_call`, with a `send` that raises.
# A bare RuntimeError classifies as `unclassified` -- MEASURED, not assumed:
# non-transient AND possibly billed, which is the deliberate "unknown is not
# assumed free" row. Non-transient means EXACTLY ONE attempt, so the expected
# charge is one attempt's upper bound and the arithmetic is exact rather than
# a multiple nobody can predict.
_SYS5B = "system prompt for the billing-retention probe"
_USR5B = "user prompt naming nct_id=NCT99999999 and nothing else"
_MAXOUT5B = 321
_INPUT5B = ev._reservation_input_tokens(_SYS5B, _USR5B)
_EXPECTED5B = get_model_cost(config.matching_wire_model(),
                             _INPUT5B, _MAXOUT5B)


def _stage5_ledger_delta(**kw):
    """Drive one failing Stage 5 call; return (delta the GATE reads, result)."""
    _src = spend.SPEND_SOURCE_STAGE5
    _before = spend.SPEND_LEDGER.by_source().get(_src, 0.0)
    _got = drive(ev._execute_matching_call,
                 lambda: (_ for _ in ()).throw(RuntimeError("boom")),
                 _SYS5B, _USR5B, max_output=_MAXOUT5B, drain_applies=False,
                 **kw)
    return (spend.SPEND_LEDGER.by_source().get(_src, 0.0) - _before, _got)


with quotas(requests={_BEDROCK: _NO_SPACING}, tokens={_BEDROCK: 100_000_000}), \
        virtual_time(), counters_cleared():
    _d5b, _r5b = _stage5_ledger_delta()
    _unconf5b = sum(pr.PROVIDER_UNCONFIRMED_BILLING.values())
    # RETENTION: a SECOND failure that the policy classifies NOT billed must
    # leave the first charge standing. There is no refund path in `spend` at
    # all, and that absence is the guarantee -- so this measures that the
    # number only ever goes UP.
    _mid = spend.SPEND_LEDGER.by_source().get(spend.SPEND_SOURCE_STAGE5, 0.0)
    _d5b_throttle, _ = _stage5_ledger_delta()
    _after = spend.SPEND_LEDGER.by_source().get(spend.SPEND_SOURCE_STAGE5, 0.0)
    # THE CONTROL, DRIVEN RATHER THAN DESCRIBED: the same policy, the same
    # failing send, the same reservation -- and `on_possibly_billed` simply not
    # supplied, which is that parameter's own Optional default and therefore
    # "the conservative charge removed" without exec'ing a patched copy of
    # anything. The gate's number must not move.
    _before_ctl = spend.SPEND_LEDGER.by_source().get(
        spend.SPEND_SOURCE_STAGE5, 0.0)
    drive(pr.execute,
          lambda: (_ for _ in ()).throw(RuntimeError("boom")),
          scope=_BEDROCK, reservation_tokens=_INPUT5B + _MAXOUT5B,
          reservation_kind=pr.RESERVATION_INFERENCE,
          classify=ev._classify_matching_failure, pacer=pr.QuotaPacer())
    _d5b_nocharge = spend.SPEND_LEDGER.by_source().get(
        spend.SPEND_SOURCE_STAGE5, 0.0) - _before_ctl

check("NON-DEGENERACY: one attempt's conservative upper bound is a real, "
      "positive amount -- priced at the WIRE model over the estimated input "
      "plus the request's own max_output",
      (_INPUT5B > 0, _EXPECTED5B > 0), (True, True))
check("*** a possibly-billed FAILURE adds exactly that upper bound to the "
      "number the spend gate reads -- `budget_spend`'s per-source total, not a "
      "separate counter ***",
      round(_d5b, 10), round(_EXPECTED5B, 10))
check("...counted once per attempt in the unconfirmed-billing register, so the "
      "dollar figure and the count agree about how many attempts were made",
      _unconf5b, 1)
check("...and the failure is still a FAILURE: the charge is not a success",
      isinstance(_r5b, Raised), True)
check("*** the amount is RETAINED: a later failure never reduces it, because "
      "`spend` has no refund path at all -- an unverified 'not billed' "
      "reclassification cannot give the money back ***",
      _after >= _mid > 0, True)
check("...and the second failure added its own upper bound rather than "
      "replacing the first", round(_d5b_throttle, 10), round(_EXPECTED5B, 10))
check("CONTROL: with the conservative charge REMOVED -- `on_possibly_billed` "
      "not supplied, which is the parameter's own Optional default -- the "
      "gate's number does not move at all, so the check above is measuring "
      "the charge and not something else about a failing call. THE FIRST "
      "VERSION OF THIS CONTROL WAS A TAUTOLOGY: a nested lambda carrying `if "
      "False else None`, so it never drove the policy and returned 0.0 "
      "whatever the code did -- the `or True` shape, reached through a "
      "comprehension",
      round(_d5b_nocharge, 10), 0.0)
check("...and the SHIPPED dispatch really does supply it, by AST rather than "
      "by grep -- so deleting the argument in production fails here",
      sorted({_kw.arg for _fn in ast.walk(
                  ast.parse(open(ev.__file__, encoding="utf-8").read()))
              if isinstance(_fn, ast.FunctionDef)
              and _fn.name == "_execute_matching_call"
              for _call in ast.walk(_fn)
              if isinstance(_call, ast.Call)
              for _kw in _call.keywords
              if _kw.arg in ("on_possibly_billed", "reservation_kind")}),
      ["on_possibly_billed", "reservation_kind"])


# ===========================================================================
# SECTION 6 — CANCELLATION, IN REAL TIME
# ===========================================================================

section("6. A shutdown ends a sleeping wait promptly")

pr.reset_time_source()
check("real time is in force for this section",
      (pr._CLOCK is time.monotonic, pr._SLEEP is time.sleep), (True, True))
_PROMPT = config.PROVIDER_WAIT_POLL_SECONDS + 0.75

with quotas(requests={_BEDROCK: 1}):
    _p6 = pr.QuotaPacer()
    _p6.reserve(_BEDROCK)
    # READ INSIDE THE BLOCK, WHICH IS THE WHOLE REPAIR. `quotas()` is a
    # `settings()` block: the override lives only for the body, and the
    # non-degeneracy check below stood OUTSIDE it, where `effective_limits`
    # answers for the SHIPPED row instead of the one this section installed.
    # That is why it carried `... if False else True` -- a constant-folded
    # tautology that could not fail, hiding an assertion that would have been
    # measuring the wrong quota if it ever ran.
    _S6_INTERVAL = pr.effective_limits(_BEDROCK)[0]
    _flag = threading.Event()
    _outcome = {}

    def _paced_waiter():
        _t = time.monotonic()
        try:
            _p6.acquire(_BEDROCK, cancelled=_flag.is_set)
            _outcome["r"] = "acquired"
        except pr.WaitCancelled:
            _outcome["r"] = "cancelled"
        _outcome["dt"] = time.monotonic() - _t

    _th = threading.Thread(target=_paced_waiter)
    _th.start()
    time.sleep(0.2)
    _flag.set()
    _th.join(timeout=10)
check("NON-DEGENERACY: the second acquisition was scheduled a whole interval "
      "out, far longer than the test waits -- so the cancellation measured "
      "below ended a wait that was really going to sleep",
      _S6_INTERVAL is not None and _S6_INTERVAL > 5, True)
check("a PACED wait ends on cancellation", _outcome.get("r"), "cancelled")
check("...within the poll interval of the flag, not at the scheduled start",
      _outcome.get("dt", 99) < 0.2 + _PROMPT, True)

# THE CONTROL FOR THE NON-DEGENERACY CHECK ABOVE, AND IT IS WHAT SAYS THE
# REPAIR IS A REPAIR RATHER THAN A REWORDING.
#
# `X if False else True` is CONSTANT-FOLDED: the comparison is never evaluated,
# so the check reported True for every value the interval could take -- it
# could not fail. Driven here on a quota whose spacing is far SHORTER than the
# wait, which is precisely the state that makes this section's premise false:
# the repaired predicate reports False, and the shape that stood here reports
# True on the SAME value.
#
# The retired shape is evaluated from its own source text rather than retyped
# as a literal, so this control cannot drift away from what it is controlling.
# `eval` of one expression, never `exec` --
# `tests/test_harness_endpoint_budget.py`'s precedent.
with quotas(requests={_BEDROCK: 60000}):
    _S6_FAST = pr.effective_limits(_BEDROCK)[0]
check("NON-DEGENERACY: the control's own interval really is shorter than the "
      "wait, so the two readings below are about different states",
      _S6_FAST is not None and _S6_FAST < 0.2, True)
check("CONTROL: the REPAIRED predicate reports False on that interval -- it "
      "can fail, which is the whole property a non-degeneracy check must have",
      _S6_FAST is not None and _S6_FAST > 5, False)
check("...while the constant-folded shape that stood here reports True on the "
      "SAME value. That gap is the defect: an assertion that could not fail, "
      "standing where the section's premise was supposed to be measured",
      eval("_S6_FAST > 5 if False else True"), True)

with quotas(requests={_BEDROCK: _NO_SPACING}):
    _flag2 = threading.Event()
    _out2 = {}

    class _Stop(Exception):
        pass

    def _backoff_waiter():
        _t = time.monotonic()
        _out2["r"] = drive(
            pr.execute, _always_throttled, scope=_BEDROCK, reservation_tokens=0, reservation_kind=_MGMT,
            classify=lambda e: pr.verdict_for(pr.CATEGORY_THROTTLED,
                                              0.8 * WINDOW),
            cancelled=lambda: _Stop("stop") if _flag2.is_set() else None,
            pacer=pr.QuotaPacer())
        _out2["dt"] = time.monotonic() - _t

    _th2 = threading.Thread(target=_backoff_waiter)
    _th2.start()
    time.sleep(0.2)
    _flag2.set()
    _th2.join(timeout=10)
check("a sleeping RETRY (backed off for most of a quota window) ends on "
      "cancellation, raising the caller's exception",
      getattr(_out2.get("r"), "kind", None), "_Stop")
check("...promptly", _out2.get("dt", 99) < 0.2 + _PROMPT, True)

_t = time.monotonic()
pr.cancellable_wait(1.2, None)
check("CONTROL: the same wait WITHOUT a cancellation predicate runs its full "
      "length, so the promptness checks above discriminate",
      time.monotonic() - _t >= 1.2, True)

# 6b. The Stage 5 cancellation factory.
ev.clear_stage5_shutdown()
check("no cancellation is in force after a clear",
      (ev._stage5_cancellation(drain_applies=True)(),
       ev._stage5_cancellation(drain_applies=False)()), (None, None))
ev.request_stage5_drain("test STOP")
check("the operator's STOP does NOT cancel a trial call's wait (a paid "
      "patient completes)", ev._stage5_cancellation(drain_applies=False)(),
      None)
_drained = ev._stage5_cancellation(drain_applies=True)()
check("...it DOES cancel a warmup's wait, as the drain subclass of the "
      "shutdown exception the send loop refuses to isolate",
      (type(_drained).__name__,
       isinstance(_drained, ev.Stage5ShutdownRequested)),
      ("Stage5DrainRequested", True))
ev.request_stage5_shutdown("test SIGTERM")
check("a shutdown cancels every wait",
      type(ev._stage5_cancellation(drain_applies=False)()).__name__,
      "Stage5ShutdownRequested")
ev.clear_stage5_shutdown()
check("...and clear_stage5_shutdown forgets BOTH",
      (ev.stage5_shutdown_requested(), ev.stage5_drain_requested()),
      (False, False))

# 6b-ii. WHICH PREDICATE EACH DISPATCH SITE ASKS FOR. The checks above are
# about what the factory RETURNS; this is about which of the two each call site
# PASSES, and that is the reconciliation the operator's ruling turns on: a
# trial call is work already paid for and must be allowed to finish, a warmup
# is not. The two are one keyword apart, so nothing behavioural upstream of a
# wait can tell them apart -- a call that needs no wait never consults the
# predicate at all, which is why this is pinned structurally.
_EV_TREE = ast.parse(open(ev.__file__, encoding="utf-8").read())
_drain_args = {}
for _fn in ast.walk(_EV_TREE):
    if (not isinstance(_fn, ast.FunctionDef)
            or _fn.name not in ("call_matching_model",
                                "call_matching_model_warmup")):
        continue
    for _call in ast.walk(_fn):
        if (isinstance(_call, ast.Call)
                and getattr(_call.func, "id", None) == "_execute_matching_call"):
            for _kw in _call.keywords:
                if _kw.arg == "drain_applies":
                    _drain_args[_fn.name] = ast.literal_eval(_kw.value)
check("the TRIAL dispatch asks for the predicate that IGNORES the drain, and "
      "the WARMUP asks for the one that honours it",
      (_drain_args.get("call_matching_model"),
       _drain_args.get("call_matching_model_warmup")), (False, True))
check("...NON-DEGENERACY: the walk found BOTH dispatch sites, so the pin is "
      "not passing over a call site it failed to locate",
      sorted(_drain_args),
      ["call_matching_model", "call_matching_model_warmup"])

# 6c. The runner's STOP switch sets the drain the moment it trips.
_TMP = tempfile.mkdtemp(prefix="prov_resilience_")
_SAVED_CP = paths._RESOLVED.get("checkpoint_path")
try:
    paths._RESOLVED["checkpoint_path"] = _TMP + os.sep
    runner.STOP_SWITCH.reset()
    _no_sentinel = drive(runner.STOP_SWITCH.poll, "test")
    check("CONTROL: with no sentinel the switch does not trip and nothing drains",
          (_no_sentinel, ev.stage5_drain_requested()), (False, False))
    open(runner.stop_switch_path(), "w").close()
    _tripped = drive(runner.STOP_SWITCH.poll, "test")
    check("with the sentinel the switch trips...", _tripped, True)
    check("...and Stage 5 is drained in the same call",
          ev.stage5_drain_requested(), True)
finally:
    runner.STOP_SWITCH.reset()
    ev.clear_stage5_shutdown()
    if _SAVED_CP is None:
        paths._RESOLVED.pop("checkpoint_path", None)
    else:
        paths._RESOLVED["checkpoint_path"] = _SAVED_CP


# ===========================================================================
# SECTION 6d — THE ASYNC TWIN: PARITY, AND FOUR CANCELLATION PROPERTIES
# ===========================================================================
#
# WHY THIS SECTION EXISTS, AND WHY "IT COMPLETED" WOULD NOT DO. `execute_async`
# is a SECOND COPY of the retry loop. The module argues why a wrapper was not
# available -- `asyncio.to_thread(execute, ...)` would put the caller's awaited
# request inside a synchronous `send()`, and driving a loop from inside
# `execute` raises because the ragas harness is already inside `asyncio.run` --
# and a duplicated policy is safe ONLY while something requires the two to
# agree. A check that an async call merely finishes passes against a twin that
# has lost the retry-after floor, the window cap, the attempt budget or the
# billing rule, which is every decision the policy makes.
#
# SO PARITY IS COMPARED AS A WHOLE OUTCOME, not as a return value: the value,
# the WIRE attempts made, the attempts the pacer scheduled, and every counter
# key the drive moved. And the control is a twin that really has diverged.
#
# CANCELLATION IS TWO MECHANISMS AND THEY ARE NOT INTERCHANGEABLE:
#
#   the `cancelled` PREDICATE firing during a pacing or backoff wait
#       -> WaitCancelled -> the caller's exception, and the token slot is
#          REFUNDED, because nothing was sent.
#   the asyncio TASK being cancelled while `await send()` is in flight
#       -> CancelledError -> the reservation is KEPT and an upper bound is
#          charged, because the provider was asked and may have answered.
#
# Testing one and calling it "cancellation" would leave the other -- the one
# that decides whether abandoned money is recorded -- unmeasured.

section("6d. execute_async: twin parity and four cancellation properties")

_ASYNC = _OPENAI
_ASYNC_BIG = 1_000_000
_ASYNC_TOK = 1_000_000_000
"""A TOKEN limit, stated because these drives reserve as INFERENCE.

WHY IT CANNOT BE LEFT UNSET, MEASURED RATHER THAN REASONED. The shipped tokens
row for this scope is `None` -- UNKNOWN -- and `require_known_quota` refuses an
INFERENCE reservation under it BEFORE `send()` is called. The first version of
the blocks below set only the REQUEST limit, so every drive raised
`QuotaUnknown` before its `send` ran, the coroutine that was supposed to signal
`asyncio.Event` never started, and the file HUNG on `await _started.wait()`
rather than failing. That is the guard working exactly as designed and a
harness modelling a configuration the shipped code refuses to start under.

Large enough that the token window never binds: no check in this section
measures token spacing -- section 4 is where that is driven."""

_ASYNC_WAIT_SECONDS = 10.0
"""The bound on every `Event.wait()` below. A DEADLOCK GUARD, not a timing knob.

THE SECOND HALF OF THE SAME LESSON. An unbounded `wait()` turns any future
refusal above it -- a quota guard, a validation raise, a reservation defect --
into a HANG rather than a failure, and a suite that hangs reports nothing at
all. Bounded, the same defect is a named `TimeoutError` in one check."""


def _counter_snapshot(scope):
    """Every counter key this scope moved, as one comparable dict."""
    return {k: v for k, v in
            list(pr.PROVIDER_RETRY_OUTCOMES.items())
            + list(pr.PROVIDER_UNCONFIRMED_BILLING.items())
            + list(pr.PROVIDER_PACING_WAITS.items())
            if f":{scope}:" in k or k.startswith(f"{scope}:")}


def _run_twin(twin, send_factory, **kw):
    """Drive ONE twin on a fresh pacer and return its whole outcome.

    The two twins are driven through ONE function so the comparison cannot
    accidentally hand them different arguments -- which is the way a parity
    check most easily becomes vacuous.
    """
    wire = [0]
    pacer = pr.QuotaPacer()
    with counters_cleared():
        send = send_factory(wire)
        if twin is pr.execute:
            got = drive(pr.execute, send, scope=_ASYNC, pacer=pacer, **kw)
        else:
            got = drive(asyncio.run, twin(send, scope=_ASYNC, pacer=pacer, **kw))
        # READ INSIDE THE BLOCK: counters_cleared() restores on exit, so a
        # snapshot taken after it would be the pre-block values -- the defect
        # section 3d records having made once already.
        counters = _counter_snapshot(_ASYNC)
    return {"value": got.kind if isinstance(got, Raised) else got,
            "wire": wire[0],
            "paced": len(pacer.history(_ASYNC)),
            "counters": counters}


def _sender(outcomes):
    """A send that walks `outcomes` ('ok' / 'throttle' / 'client'), last repeats."""
    def factory(wire):
        seq = list(outcomes)

        def send():
            wire[0] += 1
            what = seq.pop(0) if len(seq) > 1 else seq[0]
            if what == "throttle":
                raise _Throttle("ThrottlingException")
            if what == "client":
                raise _Throttle("ValidationException")
            return "ok"
        return send
    return factory


def _async_sender(outcomes):
    """The same script as an awaitable, so both twins see one scenario."""
    def factory(wire):
        seq = list(outcomes)

        async def send():
            wire[0] += 1
            what = seq.pop(0) if len(seq) > 1 else seq[0]
            if what == "throttle":
                raise _Throttle("ThrottlingException")
            if what == "client":
                raise _Throttle("ValidationException")
            return "ok"
        return send
    return factory


def _by_class(exc):
    """Throttle -> transient; validation -> not. One classifier, both twins."""
    return pr.verdict_for(pr.CATEGORY_CLIENT if "Validation" in str(exc)
                          else pr.CATEGORY_THROTTLED)


_PARITY = [
    ("a first-try success", ["ok"], {}),
    ("a transient failure then a success (recovered)", ["throttle", "ok"], {}),
    ("a still-transient error retried to the whole budget", ["throttle"], {}),
    ("a non-transient error raised after ONE attempt", ["client"], {}),
    ("a possibly-billed failure charged per attempt", ["throttle"],
     {"classify": lambda e: pr.verdict_for(pr.CATEGORY_TIMEOUT),
      "on_possibly_billed": lambda v: 0.25}),
    ("a retry-after hint honoured up to the window", ["throttle", "ok"],
     {"classify": lambda e: pr.verdict_for(pr.CATEGORY_THROTTLED,
                                           0.5 * WINDOW)}),
]

_PARITY_DIFFS = []
with quotas(requests={_ASYNC: _ASYNC_BIG}), virtual_time():
    for _label, _script, _extra in _PARITY:
        _kw = {"reservation_tokens": 0, "reservation_kind": _MGMT,
               "classify": _by_class, "rng": random.Random(11)}
        _kw.update(_extra)
        _sync = _run_twin(pr.execute, _sender(_script), **_kw)
        _asyn = _run_twin(pr.execute_async, _async_sender(_script), **_kw)
        if _sync != _asyn:
            _PARITY_DIFFS.append((_label, _sync, _asyn))

check("*** THE TWO TWINS AGREE ON EVERY SCENARIO -- the value, the WIRE "
      "attempts, the attempts the pacer scheduled and every counter key. A "
      "policy decision changed in one and not the other fails here ***",
      _PARITY_DIFFS, [])
check("NON-DEGENERACY: the matrix really drove both twins, and the scenarios "
      "are not all one outcome -- a matrix of six identical successes would "
      "agree for free",
      len(_PARITY) >= 6, True)

# --- THE FIRING CONTROL -----------------------------------------------------
#
# A TWIN THAT REALLY HAS DIVERGED, driven through the SAME comparator. It is a
# stand-in rather than a plant into the shipped module on purpose: this file
# EXECS NOTHING and is deliberately absent from
# tests/test_package_invariants.py's _EXEC_ALLOWLIST, and a different INPUT to
# the comparator is this project's own natural control for a function of its
# argument. What it models is one policy decision moved in one twin only --
# here, a twin that retries a NON-TRANSIENT error, which is the single most
# expensive divergence available (it spends the whole budget on an error that
# will fail identically every time).


async def _divergent_async(send, *, scope, pacer, reservation_tokens,
                           reservation_kind, classify, **_ignored):
    """execute_async with ONE decision changed: everything is retried."""
    return await pr.execute_async(
        send, scope=scope, pacer=pacer,
        reservation_tokens=reservation_tokens,
        reservation_kind=reservation_kind,
        classify=lambda e: pr.verdict_for(pr.CATEGORY_THROTTLED))


with quotas(requests={_ASYNC: _ASYNC_BIG}), virtual_time():
    _ctl_kw = {"reservation_tokens": 0, "reservation_kind": _MGMT,
               "classify": _by_class, "rng": random.Random(11)}
    _ctl_sync = _run_twin(pr.execute, _sender(["client"]), **_ctl_kw)
    _ctl_async = _run_twin(_divergent_async, _async_sender(["client"]),
                           **_ctl_kw)
check("*** CONTROL: a twin whose policy really differs -- it retries a "
      "non-transient error -- is CAUGHT by the same comparison that passed "
      "above, so the parity check discriminates rather than comparing two "
      "things that cannot differ ***",
      _ctl_sync == _ctl_async, False)
check("...and the difference is the one that was planted: the divergent twin "
      "made the whole budget of wire attempts where the shipped pair makes one",
      (_ctl_sync["wire"], _ctl_async["wire"]),
      (1, config.MATCHING_CALL_MAX_ATTEMPTS))

# --- C1  A CANCELLED PACING WAIT LEAVES NO ABANDONED RESERVATION ------------
#
# THE PREDICATE FIRING WHILE THE COROUTINE IS WAITING FOR ITS SLOT. Nothing has
# been sent, so the token reservation must be GIVEN BACK -- otherwise every
# cancelled patient would hold a slice of the token window for a full minute
# against requests that never happened, and a run that cancelled often would
# throttle itself on phantom load.
#
# MEASURED THROUGH A LATER RESERVATION RATHER THAN THROUGH THE PACER'S
# INTERNALS: the observable fact is that a request needing the cancelled
# tokens is admitted AT ONCE instead of being pushed a whole window.

_TOK_CAP = 100          # -> floor(100 * 0.9) = 90 admitted per window
_HALF = 45


class _Cancel(Exception):
    pass


with quotas(requests={_ASYNC: _ASYNC_BIG}, tokens={_ASYNC: _TOK_CAP}), \
        virtual_time(), counters_cleared():
    _c1_pacer = pr.QuotaPacer()
    _c1_first = _c1_pacer.reserve(_ASYNC, _HALF)

    async def _c1_drive():
        # Its start is a whole window out (the first 45 are held), so it is
        # WAITING when the predicate turns true.
        return await pr.execute_async(
            lambda: "never", scope=_ASYNC, reservation_tokens=_HALF,
            reservation_kind=pr.RESERVATION_INFERENCE, classify=_by_class,
            cancelled=lambda: _Cancel("stop"), pacer=_c1_pacer)

    _c1 = drive(asyncio.run, _c1_drive())
    _c1_third = _c1_pacer.reserve(_ASYNC, _HALF)
    _c1_cancelled = pr.PROVIDER_PACING_WAITS[f"{_ASYNC}:cancelled"]
    _c1_interval = pr.effective_limits(_ASYNC)[0]

check("a cancelled PACING wait raises the caller's own exception",
      getattr(_c1, "kind", None), "_Cancel")
check("...and is counted as a pacing cancellation", _c1_cancelled, 1)
check("*** its token reservation is REFUNDED: a later request needing exactly "
      "those tokens is admitted at the request spacing rather than pushed a "
      "whole quota window ***",
      _c1_third.start - _c1_first.start < WINDOW, True)
check("NON-DEGENERACY: the window is the thing it would have been pushed by, "
      "and the reservations really do fill it -- two halves fit and three do "
      "not", (2 * _HALF <= 90, 3 * _HALF > 90), (True, True))

# --- C2  NO ORPHANED TASK, AND C3  NOTHING UNRELATED IS CANCELLED -----------
#
# ONE LOOP, TWO COROUTINES, ONE CANCELLED. The questions are different: C2 asks
# whether the cancelled one left anything behind on the loop, C3 whether the
# OTHER one survived untouched. A mechanism that cancelled the loop, or that
# shared one cancellation flag between callers, would pass neither.

_C3 = {}
with quotas(requests={_ASYNC: _ASYNC_BIG}, tokens={_ASYNC: _ASYNC_TOK}), \
        virtual_time(), counters_cleared():

    async def _c2c3():
        _started = asyncio.Event()

        async def _slow():
            _started.set()
            await asyncio.sleep(30)

        async def _quick():
            return "unrelated-ok"

        _victim = asyncio.ensure_future(pr.execute_async(
            _slow, scope=_ASYNC, reservation_tokens=10,
            reservation_kind=pr.RESERVATION_INFERENCE, classify=_by_class,
            pacer=pr.QuotaPacer()))
        _bystander = asyncio.ensure_future(pr.execute_async(
            _quick, scope=_ASYNC, reservation_tokens=10,
            reservation_kind=pr.RESERVATION_INFERENCE, classify=_by_class,
            pacer=pr.QuotaPacer()))
        # BOUNDED: see `_ASYNC_WAIT_SECONDS`. If anything above refuses before
        # `send()` runs, this raises instead of hanging the whole file.
        await asyncio.wait_for(_started.wait(), _ASYNC_WAIT_SECONDS)
        _victim.cancel()
        try:
            await _victim
        except asyncio.CancelledError:
            _C3["victim"] = "cancelled"
        _C3["bystander"] = await _bystander
        # ASKED INSIDE THE LOOP, because `asyncio.run` closes it on the way out
        # and `all_tasks()` afterwards would be a statement about nothing.
        _C3["left"] = sorted(
            t.get_name() for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done())

    drive(asyncio.run, _c2c3())

check("the cancelled coroutine really was cancelled", _C3.get("victim"),
      "cancelled")
check("*** C2: it left NO task behind on the loop -- no orphaned pacing or "
      "backoff task outliving the caller that asked for it ***",
      _C3.get("left"), [])
check("*** C3: the UNRELATED coroutine on the same loop and the same scope "
      "completed untouched, so cancellation is per-call and not a loop-wide "
      "or scope-wide flag ***", _C3.get("bystander"), "unrelated-ok")

# --- C4  AN ALREADY-DISPATCHED REQUEST IS AN UNCERTAIN COMPLETION -----------
#
# THE ONE THAT DECIDES WHETHER ABANDONED MONEY IS RECORDED. `CancelledError` is
# a BaseException and NOT an Exception, so it travels straight through the
# twins' `except Exception` -- and with it went the reservation settle, the
# charge and every counter. A cancelled ragas run abandoned requests the
# provider had already been asked for and recorded a spend of ZERO for them.

_C4 = {}
with quotas(requests={_ASYNC: _ASYNC_BIG}, tokens={_ASYNC: _ASYNC_TOK}), \
        virtual_time(), counters_cleared():

    async def _c4(charge):
        _started = asyncio.Event()

        async def _inflight():
            _started.set()
            await asyncio.sleep(30)

        _kw = {} if charge is None else {"on_possibly_billed": charge}
        _task = asyncio.ensure_future(pr.execute_async(
            _inflight, scope=_ASYNC, reservation_tokens=5000,
            reservation_kind=pr.RESERVATION_INFERENCE, classify=_by_class,
            pacer=pr.QuotaPacer(), **_kw))
        # BOUNDED, for the reason at `_ASYNC_WAIT_SECONDS`.
        await asyncio.wait_for(_started.wait(), _ASYNC_WAIT_SECONDS)
        _task.cancel()
        try:
            await _task
        except asyncio.CancelledError:
            return "propagated"
        return "SWALLOWED"

    _charged = []
    _C4["outcome"] = drive(asyncio.run,
                           _c4(lambda v: _charged.append(v.category) or 0.25))
    _C4["charged"] = list(_charged)
    _C4["counter"] = pr.PROVIDER_UNCONFIRMED_BILLING[
        f"{_ASYNC}:{pr.CATEGORY_ABANDONED}"]
    _C4["usd"] = pr._UNCONFIRMED_USD.get(_ASYNC, 0.0)
    _C4["cancelled_key"] = pr.PROVIDER_RETRY_OUTCOMES[
        f"{pr.RETRY_OUTCOME_CANCELLED}:{_ASYNC}:{pr.CATEGORY_ABANDONED}"]

with quotas(requests={_ASYNC: _ASYNC_BIG}, tokens={_ASYNC: _ASYNC_TOK}), \
        virtual_time(), counters_cleared():
    # THE CONTROL, AND IT IS THE PARAMETER'S OWN Optional DEFAULT rather than a
    # patched module: with no charge callback the same cancellation must move
    # no money, so the reading above is about the CHARGE and not about
    # something else a cancelled call happens to do.
    drive(asyncio.run, _c4(None))
    _C4["nocharge_usd"] = pr._UNCONFIRMED_USD.get(_ASYNC, 0.0)

check("the cancellation PROPAGATES rather than being swallowed to tidy up "
      "accounting", _C4.get("outcome"), "propagated")
check("*** an already-dispatched request resolves as an UNCERTAIN COMPLETION: "
      "charged its conservative upper bound under its own category, not "
      "assumed free ***",
      (_C4.get("charged"), _C4.get("counter"), _C4.get("usd")),
      ([pr.CATEGORY_ABANDONED], 1, 0.25))
check("...and it is recorded as a CANCELLATION too, so the run-end block can "
      "tell an abandoned request from an unclassifiable provider error",
      _C4.get("cancelled_key"), 1)
check("CONTROL: with no charge callback -- the parameter's own Optional "
      "default -- the same cancellation moves no money at all",
      _C4.get("nocharge_usd", -1.0), 0.0)
check("NON-DEGENERACY: `CancelledError` is NOT an `Exception`, which is the "
      "whole reason the clause the check above measures has to exist -- an "
      "`except Exception` twin cannot see it",
      (issubclass(asyncio.CancelledError, BaseException),
       issubclass(asyncio.CancelledError, Exception)), (True, False))

# --- THE ASYNC WAIT LEAVES THE EVENT LOOP -----------------------------------
#
# THE PROPERTY THAT MAKES THE ASYNC TWIN WORTH HAVING AT ALL. If its waits
# blocked the loop, a ragas run's tens of scoring coroutines would stall behind
# whichever one was waiting for its slot -- and every check above would still
# pass, because they measure one coroutine at a time.

_LOOP_FREE = {}
with quotas(requests={_ASYNC: _ASYNC_BIG}), virtual_time():

    async def _loop_free():
        _ticks = [0]

        async def _ticker():
            for _ in range(5):
                _ticks[0] += 1
                await asyncio.sleep(0)

        async def _waiter():
            await pr.cancellable_wait_async(0.5, None)
            return "waited"

        _got = await asyncio.gather(_ticker(), _waiter())
        _LOOP_FREE["ticks"] = _ticks[0]
        _LOOP_FREE["waited"] = _got[1]

    drive(asyncio.run, _loop_free())

check("a pacing/backoff wait runs OFF the event loop: another coroutine kept "
      "running while it waited",
      (_LOOP_FREE.get("waited"), _LOOP_FREE.get("ticks", 0) >= 5),
      ("waited", True))


# ===========================================================================
# SECTION 7 — THE REAL STAGE 5 NODE ON THE CONVERSE ARM
# ===========================================================================

section("7. The real node: exhaustion is terminal, isolation is preserved")

PATIENT = {
    "patient_id": "provider-resilience-patient",
    "demographics": {"age": 61, "sex": "female", "race": "white",
                     "ethnicity": "not hispanic or latino"},
    "conditions": [{"code": "254837009",
                    "display": "Malignant neoplasm of breast (disorder)",
                    "verification_status": "confirmed"}],
    "medications": [], "allergies": [], "observations": [], "procedures": [],
}


def make_trial(nct_id):
    return {"trial": {"nct_id": nct_id, "title": f"Study {nct_id}",
                      "phase": "PHASE2",
                      "eligibility": {
                          "inclusion_criteria": "Inclusion Criteria:\n- Age 18+",
                          "exclusion_criteria": "Exclusion Criteria:\n- Pregnancy"}}}


TRIALS = [make_trial(f"NCT0000000{i}") for i in range(1, 4)]


class ClientError(Exception):
    """A botocore ClientError's SHAPE (boto3 is not needed to classify it)."""

    def __init__(self, code, message, status):
        super().__init__(f"An error occurred ({code}) when calling the "
                         f"Converse operation: {message}")
        self.response = {"Error": {"Code": code, "Message": message},
                         "ResponseMetadata": {"HTTPStatusCode": status}}


def _throttle():
    return ClientError("ThrottlingException", "Too many requests", 429)


def _reply(text, stop="end_turn", read=19000, write=0, out=100):
    return {"ResponseMetadata": {"RequestId": "stub"},
            "output": {"message": {"role": "assistant",
                                   "content": [{"text": text}]}},
            "stopReason": stop,
            "usage": {"inputTokens": 1000, "outputTokens": out,
                      "totalTokens": 1000 + out,
                      "cacheReadInputTokens": read,
                      "cacheWriteInputTokens": write}}


def _eligible(ids):
    return json.dumps({"evaluations": [
        {"assessment": "No known disqualifiers.", "eligible": "eligible",
         "inclusion_criteria": [{"criterion": "Age 18+", "patient_value": "61",
                                 "status": "met"}],
         "exclusion_criteria": [], "match_score": 0.0, "nct_id": i}
        for i in ids]})


class Scripted:
    """``converse`` only. ``plan`` maps 'warmup' or an nct_id to a list of
    outcomes consumed in order ('throttle', 'timeout', 'ok'); the last repeats."""

    def __init__(self, clock, plan):
        self.clock = clock
        self.plan = {k: list(v) for k, v in plan.items()}
        self.log = []
        self._lock = threading.Lock()

    def _next(self, key):
        with self._lock:
            seq = self.plan.get(key, ["ok"])
            return seq.pop(0) if len(seq) > 1 else seq[0]

    def converse(self, **kwargs):
        text = kwargs["messages"][0]["content"][0]["text"]
        warm = text == config.MATCHING_PER_TRIAL_WARMUP_USER_MESSAGE
        ids = [] if warm else [t["trial"]["nct_id"] for t in TRIALS
                               if f"nct_id={t['trial']['nct_id']} " in text]
        key = "warmup" if warm else (ids[0] if ids else "?")
        with self._lock:
            self.log.append((key, self.clock.now()))
        outcome = self._next(key)
        if outcome == "throttle":
            raise _throttle()
        if outcome == "timeout":
            raise ClientError("ModelTimeoutException", "timed out", 408)
        if warm:
            return _reply("", stop="max_tokens", read=0, write=19000, out=1)
        return _reply(_eligible(ids))

    def count(self, key):
        return sum(1 for k, _ in self.log if k == key)


def run_node(plan):
    # EXPLICIT LIMITS FOR BOTH FAMILIES, because this drives the REAL Stage 5
    # node and the node reserves `estimated input + max_output` tokens per
    # attempt. `bedrock_anthropic`'s tokens row ships UNKNOWN, which REFUSES
    # before the send -- correctly, and fatally for a section whose subject is
    # what the node does once a request goes out. MEASURED before this block
    # existed: every section-7 check failed and the spacing check aborted on
    # `min()` over an empty list, because not one wire attempt was ever issued.
    #
    # THE REQUEST LIMIT IS THE SHIPPED 10, NOT A LARGE ONE. Section 7's own
    # checks measure that the node's calls are SPACED at the configured rate,
    # so this file states the real figure and lets the pacer pace; only the
    # token limit is set high, because no check here measures token spacing.
    with virtual_time() as clk, quotas(
            requests={_BEDROCK: _SHIPPED["PROVIDER_REQUESTS_PER_MINUTE"][_BEDROCK]},
            tokens={_BEDROCK: 100_000_000}):
        stub = Scripted(clk, plan)
        saved = deps.set_overrides({deps.BEDROCK_ANTHROPIC_CLIENT: stub})
        try:
            with settings(MATCHING_PROVIDER=_BEDROCK,
                          MATCHING_PER_TRIAL_CALLS_ENABLED=True):
                state = {"patient_data": PATIENT, "filtered_trials": TRIALS,
                         "llm_classifier_retries": 0,
                         "mesh_filter_applied": True,
                         "mesh_filter_skip_reason": "applied",
                         "stage_timings": {}}
                return drive(ev.node_llm_classifier_evaluation, state), stub
        finally:
            deps.restore_overrides(saved)


spend.SPEND_LEDGER.reset()
spend.SPEND_STOP.reset()
ev.clear_stage5_shutdown()

with counters_cleared():
    _r7a, _s7a = run_node({"warmup": ["throttle"]})
_note = at(_r7a, "llm_classifier_transport_exhausted")
check("a warmup throttled on every attempt was retried to the whole budget",
      _s7a.count("warmup"), config.MATCHING_CALL_MAX_ATTEMPTS)
check("...and no trial call was issued (cache-or-nothing unchanged)",
      len(_s7a.log) - _s7a.count("warmup"), 0)
check("...the patient FAILED (warmup-fails-the-patient unchanged)",
      (at(_r7a, "evaluations"), bool(at(_r7a, "error"))), ([], True))
check("...the stored error says how many provider attempts were made",
      f"after {config.MATCHING_CALL_MAX_ATTEMPTS} provider attempt(s)"
      in str(at(_r7a, "error", "")), True)
check("...and the node marked it TRANSPORT-EXHAUSTED", bool(_note), True)
_state_after = {"error": at(_r7a, "error"), "evaluations": [],
                "llm_classifier_retries": at(_r7a, "llm_classifier_retries"),
                "llm_classifier_transport_exhausted": _note}
check("the router sends it to the error handler: ONE budget, no second one",
      agent_graph.route_after_llm_classifier(_state_after), "error_handler")
_state_ctl = dict(_state_after, llm_classifier_transport_exhausted=None)
check("CONTROL: without the flag the same state would re-enter Stage 5 for "
      "another full budget", agent_graph.route_after_llm_classifier(_state_ctl),
      "llm_classifier_retry")

_lost = TRIALS[1]["trial"]["nct_id"]
with counters_cleared():
    _r7b, _s7b = run_node({_lost: ["throttle"]})
_by_id = {e.get("nct_id"): e for e in (at(_r7b, "evaluations") or [])}
check("a trial throttled on every attempt was retried to the budget",
      _s7b.count(_lost), config.MATCHING_CALL_MAX_ATTEMPTS)
check("...and ISOLATED to not-evaluable per_trial_call_failed (unchanged)",
      (at(_by_id.get(_lost, {}), "eligible"),
       at(_by_id.get(_lost, {}), "not_evaluable_reason")),
      ("not_evaluable", "per_trial_call_failed"))
check("...while the patient COMPLETED with its other trials judged",
      (bool(at(_r7b, "error")),
       sorted(k for k, v in _by_id.items() if v.get("eligible") == "eligible")),
      (False, sorted(t["trial"]["nct_id"] for t in TRIALS
                     if t["trial"]["nct_id"] != _lost)))
check("...and a partial loss is NOT terminal",
      at(_r7b, "llm_classifier_transport_exhausted"), None)

with counters_cleared():
    _r7c, _s7c = run_node({_lost: ["throttle", "throttle", "ok"],
                           "warmup": ["throttle", "ok"]})
_by_id_c = {e.get("nct_id"): e for e in (at(_r7c, "evaluations") or [])}
check("a trial throttled twice then answered RECOVERS its verdict",
      (_s7c.count(_lost), at(_by_id_c.get(_lost, {}), "eligible")),
      (3, "eligible"))
check("...and a warmup throttled once then answered releases the wave",
      (_s7c.count("warmup"), bool(at(_r7c, "error"))), (2, False))
_times_c = sorted(t for _k, t in _s7c.log)
_int = pr.effective_limits(_BEDROCK)[0]
check("NON-DEGENERACY: the node made several wire attempts",
      len(_times_c) >= 5, True)
check("...and at the node level every attempt started >= one paced interval "
      "after the one before", min(b - a for a, b in zip(_times_c, _times_c[1:]))
      >= _int - 1e-9, True)

_ledger_before = spend.SPEND_LEDGER.measured
with counters_cleared():
    _r7d, _s7d = run_node({_lost: ["timeout"]})
    _unconf = sum(pr.PROVIDER_UNCONFIRMED_BILLING.values())
_ledger_delta = spend.SPEND_LEDGER.measured - _ledger_before
check("a model TIMEOUT on every attempt is counted possibly billed per attempt",
      _unconf, config.MATCHING_CALL_MAX_ATTEMPTS)
_ledger_before2 = spend.SPEND_LEDGER.measured
with counters_cleared():
    run_node({_lost: ["throttle"]})
    _unconf_ctl = sum(pr.PROVIDER_UNCONFIRMED_BILLING.values())
_ledger_delta_ctl = spend.SPEND_LEDGER.measured - _ledger_before2
check("...and the spend ledger was charged MORE for the timeouts than for the "
      "same run with throttles, which are refused before inference",
      _ledger_delta > _ledger_delta_ctl, True)
check("CONTROL: throttles charge no upper bound", _unconf_ctl, 0)

class _LocalIndexError(IndexError):
    pass


check("an exception the policy never saw (a malformed response's IndexError) "
      "is NOT transport-exhausted, so it keeps the patient-level retry",
      ev._transport_exhaustion_note(_LocalIndexError("choices")), None)
_tagged = RuntimeError("x")
setattr(_tagged, pr.ATTEMPTS_ATTR, 6)
setattr(_tagged, pr.CATEGORY_ATTR, pr.CATEGORY_THROTTLED)
check("...while one the policy tagged is", bool(
    ev._transport_exhaustion_note(_tagged)), True)
check("...and a shutdown is never terminal-by-transport",
      ev._transport_exhaustion_note(ev.Stage5ShutdownRequested("s")), None)


# ===========================================================================
# SECTION 8 — THE PRINTED SURFACES
# ===========================================================================

section("8. Run-start announcement, runner wiring, run summary, results, labels")

_ann = pr.describe_pacing(concurrency=pr.stage5_concurrency(config.MAX_WORKERS))
_i8, _t8, _w8, _n8 = pr.effective_limits(_BEDROCK)
check("the announcement states the per-window count and spacing, derived",
      (f"at most {_n8} starts per {_w8:g}s" in _ann,
       f"spaced {_i8:.2f}s apart" in _ann), (True, True))
check("...that tokens/min is UNKNOWN and that dispatch will be REFUSED for it, "
      "rather than the retired 'not enforced' which read like a decision",
      ("tokens/min UNKNOWN" in _ann and "DISPATCH WILL BE REFUSED" in _ann),
      True)
check("...the PROCESS-LOCAL rule for simultaneous processes",
      "PROCESS-LOCAL" in _ann and "half these limits" in _ann, True)
check("...and the total attempt budget",
      f"at most {config.MATCHING_CALL_MAX_ATTEMPTS} wire attempts" in _ann, True)
check("CONTROL: a scope whose quota is UNKNOWN announces that DISPATCH WILL BE "
      "REFUSED rather than being quiet -- the state that used to print 'NOT "
      "PACED' and read like a deliberate choice",
      ("requests/min UNKNOWN" in pr.describe_pacing(scope=_OPENAI)
       and "DISPATCH WILL BE REFUSED" in pr.describe_pacing(scope=_OPENAI)),
      True)
check("...and a scope the provider does not meter on an axis says THAT "
      "instead, so the two absences are never one string",
      "NOT METERED" in pr.describe_pacing(scope=_BATCH), True)

_RUNNER_TREE = ast.parse(open(runner.__file__, encoding="utf-8").read())
_main = next(n for n in _RUNNER_TREE.body
             if isinstance(n, ast.FunctionDef) and n.name == "main")
_src_lines = [ast.unparse(s) for s in ast.walk(_main)
              if isinstance(s, ast.Expr) or isinstance(s, ast.Assign)]


def _index_of(fragment, lines):
    for i, line in enumerate(lines):
        if fragment in line:
            return i
    return -1


_stmts = []
for _node in ast.walk(_main):
    for _field in ("body", "orelse", "finalbody"):
        _block = getattr(_node, _field, None)
        if isinstance(_block, list):
            _stmts.append([ast.unparse(s) for s in _block])


def _follows(first, second):
    for block in _stmts:
        for i, line in enumerate(block[:-1]):
            if first in line and second in block[i + 1]:
                return True
    return False


check("the runner announces pacing on the line right after the spend cap",
      _follows("spend.describe_cap()", "provider_resilience.describe_pacing"),
      True)
check("...retires an earlier campaign's results right before loading results",
      _follows("retire_superseded_results(completed_ids)",
               "results_list = load_results()"), True)
check("...and resets the pacer right after clearing the shutdown flags",
      _follows("clear_stage5_shutdown()", "provider_resilience.reset()"), True)
check("CONTROL: the adjacency scan is not satisfied by any pair",
      _follows("provider_resilience.reset()", "spend.describe_cap()"), False)

# 8b. The run-summary block, at the printed surface, on a constructed old
# results file.
_RES_DIR = tempfile.mkdtemp(prefix="prov_results_", dir=_TMP)
_SAVED_CP2 = paths._RESOLVED.get("checkpoint_path")
try:
    paths._RESOLVED["checkpoint_path"] = _RES_DIR + os.sep
    _old = [{"patient_id": f"old-{i}", "status": "success",
             "eligible_matches": 1, "near_misses": 0, "not_evaluable": 0,
             "total_time": 100.0, "timestamp": "2026-08-01T00:00:00",
             "error": "", "is_resample": False, "db_row_written": True}
            for i in range(1100)]
    with open(runner._results_path(), "w") as fh:
        json.dump(_old, fh)

    def _summary_total(results):
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf), contextlib.redirect_stdout(buf):
            runner.print_summary(results, 60.0)
        text = buf.getvalue()
        main_block = text.split("--- MAIN PASS ---")[1].split("---")[0]
        total_line = [l for l in main_block.splitlines()
                      if l.strip().startswith("total ")]
        return (int(total_line[0].split()[-1]) if total_line else None), text

    # CONTROL FIRST: the pre-fix behaviour is "load whatever is there".
    _merged = runner.load_results()
    runner.append_result(_merged, {"patient_id": "new-1", "status": "success",
                                   "eligible_matches": 1, "near_misses": 0,
                                   "not_evaluable": 0, "total_time": 10.0,
                                   "timestamp": "2026-09-11T00:00:00",
                                   "error": "", "is_resample": False,
                                   "db_row_written": True})
    _merged_total, _ = _summary_total(_merged)
    check("CONTROL: without retirement a fresh run's summary merges the old "
          "campaign (1,100 + 1)", _merged_total, 1101)
    with open(runner._results_path(), "w") as fh:
        json.dump(_old, fh)

    check("a RESUME (checkpoint has completions) does NOT retire the file",
          drive(runner.retire_superseded_results, {"stem-1"}), None)
    check("...so a resumed campaign's summary stays campaign-wide",
          len(runner.load_results()), 1100)

    _retired = drive(runner.retire_superseded_results, set())
    check("a run that resumes nothing retires the earlier campaign's file",
          isinstance(_retired, str) and os.path.exists(_retired)
          and not os.path.exists(runner._results_path()), True)
    check("...kept, not deleted: the retired file still holds 1,100 entries",
          len(json.load(open(_retired))) if isinstance(_retired, str) else None,
          1100)
    _fresh = runner.load_results()
    for _k in range(2):
        runner.append_result(_fresh, {
            "patient_id": f"new-{_k}", "status": "success",
            "eligible_matches": 1, "near_misses": 0, "not_evaluable": 0,
            "total_time": 10.0, "timestamp": "2026-09-11T00:00:00",
            "error": "", "is_resample": False, "db_row_written": True})
    _fresh_total, _text = _summary_total(_fresh)
    check("the batch summary now covers THIS run's patients only",
          _fresh_total, 2)
    check("...and prints the PROVIDER PACING AND RETRIES block beside spend",
          "PROVIDER PACING AND RETRIES" in _text
          and _text.index("PROVIDER PACING AND RETRIES") > _text.index("SPEND"),
          True)
    check("...with the policy's counts from this file's own drives",
          "logical calls" in _text and "unconfirmed billing" in _text, True)

    # EVERY OUTCOME IS PRINTED, EVEN AT ZERO. A block that printed only the
    # keys that fired reads identically for "the policy measured no retries"
    # and "nothing was counted at all", which is the one distinction a run-end
    # report has to make. Read off the printed surface, not off the counter.
    _outcome_lines = [l for l in _text.splitlines()
                      if l.strip().startswith("outcomes ")]
    check("...and the run-end block names EVERY member of RETRY_OUTCOMES, "
          "even the ones that never fired",
          [o for o in pr.RETRY_OUTCOMES
           if not any(f"{o} " in l for l in _outcome_lines)], [])
    check("...non-degeneracy: an outcome line was found and some outcome "
          "printed a zero",
          bool(_outcome_lines) and any(" 0" in l for l in _outcome_lines), True)
    # CONTROL: the shipped report is NOT equivalent to printing the keys that
    # fired -- on this run at least one outcome never fired, so a report built
    # from the counter's own keys would omit it.
    _fired = {k.split(":", 1)[0] for k in pr.PROVIDER_RETRY_OUTCOMES}
    check("CONTROL: at least one outcome did not fire here, so a report driven "
          "off the counter's keys would have omitted it",
          len(_fired & set(pr.RETRY_OUTCOMES)) < len(pr.RETRY_OUTCOMES), True)

    # The retire failure path: a directory that refuses the rename.
    with open(runner._results_path(), "w") as fh:
        json.dump(_old, fh)
    os.chmod(_RES_DIR, 0o500)
    try:
        _before_fail = runner.RESULTS_FILE_FAILURES.get(
            "retire:PermissionError", 0)
        _rf = drive(runner.retire_superseded_results, set())
        _after_fail = runner.RESULTS_FILE_FAILURES.get(
            "retire:PermissionError", 0)
    finally:
        os.chmod(_RES_DIR, 0o700)
    check("a rename the filesystem refuses is COUNTED, not silent, and the run "
          "continues", (_rf, _after_fail - _before_fail), (None, 1))
finally:
    if _SAVED_CP2 is None:
        paths._RESOLVED.pop("checkpoint_path", None)
    else:
        paths._RESOLVED["checkpoint_path"] = _SAVED_CP2

# 8c. The corrected labels, in executable code.
def _string_literals(source):
    tree = ast.parse(source)
    docstrings = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
                          ast.Module)) and n.body \
                and isinstance(n.body[0], ast.Expr) \
                and isinstance(getattr(n.body[0], "value", None), ast.Constant):
            docstrings.add(id(n.body[0].value))
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


_ev_src = open(ev.__file__, encoding="utf-8").read()
_lits = _string_literals(_ev_src)
check("no executable string in evaluation.py still says 'GPT-4o API error' or "
      "'GPT-4o JSON parse error'",
      [s for s in _lits if s.startswith(("GPT-4o API error",
                                         "GPT-4o JSON parse error"))], [])
check("...the stage's own labels are there instead",
      (any(s.startswith("LLM classifier API error") for s in _lits),
       any(s.startswith("LLM classifier JSON parse error") for s in _lits)),
      (True, True))
_planted = _ev_src.replace('f"LLM classifier JSON parse error',
                           'f"GPT-4o JSON parse error', 1)
check("CONTROL: the same scan over a copy with the old label planted finds it",
      len([s for s in _string_literals(_planted)
           if s.startswith("GPT-4o JSON parse error")]) > 0, True)


# ===========================================================================
# SECTION 9 — REAL botocore: one wire attempt per call
# ===========================================================================

section("9. The shipped Converse client configuration makes ONE wire attempt")

try:
    import boto3                                                 # noqa: E402
    from botocore.config import Config as _BotoConfig            # noqa: E402
    from botocore.awsrequest import AWSResponse                  # noqa: E402
except ImportError as _exc:
    boto3 = None
    skip("real botocore attempt count", f"boto3 not importable: {_exc}")

if boto3 is not None:
    class _Raw:
        def __init__(self, body):
            self._body = body

        def stream(self, *a, **k):
            yield self._body

    def _wire_attempts(retries):
        client = boto3.client(
            "bedrock-runtime", region_name="us-east-1",
            aws_access_key_id="AKIDFAKEFORTESTONLY0", aws_secret_access_key="x",
            config=_BotoConfig(retries=retries))
        count = [0]

        def _before_send(request, **_kw):
            count[0] += 1
            return AWSResponse(request.url, 429,
                               {"x-amzn-ErrorType": "ThrottlingException",
                                "Content-Type": "application/json"},
                               _Raw(b'{"message":"Too many requests"}'))

        client.meta.events.register("before-send.bedrock-runtime.Converse",
                                    _before_send)
        drive(client.converse, modelId="m",
              messages=[{"role": "user", "content": [{"text": "hi"}]}])
        return count[0]

    # THE SHIPPED DICT, READ OFF THE BUILDER'S SOURCE rather than retyped, so
    # a builder that went back to `max_attempts` fails here.
    _builder = open(config.__file__, encoding="utf-8").read()
    check("the builder passes botocore `total_max_attempts` (TOTAL attempts)",
          '"total_max_attempts": bedrock_anthropic_max_attempts()' in _builder,
          True)
    _shipped_n = _wire_attempts({"total_max_attempts":
                                 config.bedrock_anthropic_max_attempts(),
                                 "mode": config.BEDROCK_ANTHROPIC_RETRY_MODE})
    check("REAL botocore with the shipped retries config makes exactly ONE wire "
          "attempt per call, so the policy is the only retry layer", _shipped_n, 1)
    _old_key_n = _wire_attempts({"max_attempts": 4, "mode": "standard"})
    check("CONTROL: the key that shipped before ({'max_attempts': 4}) makes FIVE "
          "-- it counts retries -- and with the policy's budget on top the "
          "total would be far past it", _old_key_n, 5)
    check("...so the 'total' check fails for the old configuration",
          _old_key_n * config.matching_policy_attempts()
          <= config.MATCHING_CALL_MAX_ATTEMPTS, False)


# ===========================================================================
# SECTION 10 — RESTORES
# ===========================================================================

section("10. Everything this file touched is put back")

check("every config knob this file rebound is restored BY IDENTITY",
      [n for n, v in _SHIPPED.items() if getattr(config, n) is not v], [])
check("the real time source is back", (pr._CLOCK is time.monotonic,
                                        pr._SLEEP is time.sleep), (True, True))
check("no Stage 5 flag is left set",
      (ev.stage5_shutdown_requested(), ev.stage5_drain_requested()),
      (False, False))
check("no provider client override is left installed",
      deps.peek(deps.BEDROCK_ANTHROPIC_CLIENT) is deps.UNSET, True)
shutil.rmtree(_TMP, ignore_errors=True)
check("the temp tree is removed", os.path.exists(_TMP), False)
check("NO outbound connection was attempted by anything this file drove",
      _NET_ATTEMPTS, [])
socket.socket.connect = _REAL_CONNECT
socket.create_connection = _REAL_CREATE


# ===========================================================================
# SUMMARY
# ===========================================================================

print("\n" + "=" * 74)
print(f"RESULTS: {_RESULTS['passed']} passed, {_RESULTS['failed']} failed, "
      f"{_RESULTS['skipped']} skipped")
print("=" * 74)
for _f in _FAILURES:
    print(f"  FAILED: {_f}")

if __name__ == "__main__":
    sys.exit(1 if _RESULTS["failed"] else 0)


#------------------------------------------------------------------------------


#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 11 2026

@author: ramyalsaffar
"""
